# -*- coding: utf-8 -*-
"""Independent HS main-board A-shares MA5/MA20 screener v11 through the BigQMT Redis bridge.

The process is signal-only.  It does not import a versioned strategy and never
calls an order, cancellation, download, or other trading write method.

Universe widened to HS main-board A shares, excluding ST and unknown names.
Name checks run only on technical candidates to reduce RPC traffic.
Evidence on CSI500 only (not a backtest of this expanded universe): v13 ATR-only backtest 20250903-20260902; in-sample, not a guarantee.
Run this file with external Python while the configured BigQMT bridge is running.
Edit MA5_BELOW_PERCENT and MIN_ATR_PERCENT below; both use percentage points.
"""
import argparse
import math
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[2]
BRIDGE_SRC = PROJECT_ROOT / "integrations" / "bigqmt" / "src"
if str(BRIDGE_SRC) not in sys.path:
    sys.path.insert(0, str(BRIDGE_SRC))

from bigqmt_signal_trader.xtquant_compat import configure, load_client_config  # noqa: E402


SECTOR_NAME = "沪深A股"
SHORT_MA_DAYS = 5
LONG_MA_DAYS = 20
MA5_BELOW_PERCENT = 3.0  # 3.0 means 3% below MA5
ATR_DAYS = 14
MIN_ATR_PERCENT = 7.0  # ATR14 / previous completed close * 100
TICK_CHUNK_SIZE = 100
HISTORY_BATCH_SIZE = 100
HISTORY_BAR_COUNT = 40
HISTORY_RPC_TIMEOUT_SECONDS = 20
NAME_COLUMN_WIDTH = 12
RESULT_HEADER = (
    "code         name              price        MA5    vs_MA5"
    "       MA20   vs_MA20     ATR14%"
)


def _log(message):
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), message), flush=True)


def _positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _date_key(value):
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    text = str(value).replace("-", "").replace("/", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""


def _display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1
               for char in str(text))


def _pad_display(text, width):
    value = str(text)
    return value + " " * max(0, width - _display_width(value))


def calculate_ma_candidate(completed_rows, current_price,
                           below_ma5_percent=MA5_BELOW_PERCENT,
                           min_atr_percent=MIN_ATR_PERCENT):
    """Dynamic MA5/MA20 plus completed-bar ATR filter (v13 backtest definition)."""
    price = _positive(current_price)
    if price is None:
        return None
    if len(completed_rows) < max(LONG_MA_DAYS - 1, ATR_DAYS + 1):
        return None
    rows = []
    for row in completed_rows[-max(LONG_MA_DAYS - 1, ATR_DAYS + 1):]:
        high, low, close = (_positive(row.get(field)) for field in ("high", "low", "close"))
        if any(value is None for value in (high, low, close)):
            return None
        if not low <= close <= high:
            return None
        rows.append((high, low, close))
    closes = [row[2] for row in rows]
    true_ranges = [
        max(rows[i][0] - rows[i][1],
            abs(rows[i][0] - rows[i-1][2]), abs(rows[i][1] - rows[i-1][2]))
        for i in range(1, len(rows))
    ]
    atr14 = sum(true_ranges[-ATR_DAYS:]) / ATR_DAYS
    atr_percent = atr14 / closes[-1] * 100.0
    ma5 = (sum(closes[-(SHORT_MA_DAYS - 1):]) + price) / SHORT_MA_DAYS
    ma20 = (sum(closes[-(LONG_MA_DAYS - 1):]) + price) / LONG_MA_DAYS
    gap_pct = (price / ma5 - 1.0) * 100.0
    below_ma5 = price < ma5 * (1.0 - below_ma5_percent / 100.0)
    above_ma20 = price > ma20
    return {
        "price": price,
        "ma5": ma5,
        "ma20": ma20,
        "gap_pct": gap_pct,
        "below_ma5": below_ma5,
        "above_ma20": above_ma20,
        "atr14": atr14,
        "atr_percent": atr_percent,
        "atr_pass": atr_percent >= min_atr_percent,
        "matched": below_ma5 and above_ma20 and atr_percent >= min_atr_percent,
    }


def format_result_line(code, name, result):
    vs_ma20_pct = (result["price"] / result["ma20"] - 1.0) * 100.0
    return "%-12s %s %10.2f %10.2f %9.2f%% %10.2f %+9.2f%% %9.2f%%" % (
        code,
        _pad_display(name, NAME_COLUMN_WIDTH),
        result["price"],
        result["ma5"],
        result["gap_pct"],
        result["ma20"],
        vs_ma20_pct,
        result["atr_percent"],
    )


def _print_result_section(title, selected, names):
    _log("=" * 103)
    _log(title)
    _log(RESULT_HEADER)
    for _, code, result in selected:
        _log(format_result_line(code, names[code], result))


def is_main_board(code):
    """Shanghai/Shenzhen main-board A shares only; no B shares, ETFs or Beijing."""
    code = str(code).upper()
    number, separator, exchange = code.partition(".")
    if not separator or len(number) != 6 or not number.isdigit():
        return False
    return ((exchange == "SH" and number.startswith(("600", "601", "603", "605")))
            or (exchange == "SZ" and number.startswith(("000", "001", "002", "003"))))


def allowed_name(name):
    """Missing names cannot establish non-ST status, so exclude them."""
    normalized = str(name or "").strip().upper().replace(" ", "")
    return bool(normalized and normalized != "-" and "ST" not in normalized)


def _completed_rows(frame, today):
    """Keep OHLC aligned, exclude today's bar, reject invalid bars in calculation."""
    if frame is None:
        return []
    rows = {}
    for index, row in frame.iterrows():
        date = _date_key(index)
        if date and date < today:
            rows[date] = {field: row.get(field) for field in ("high", "low", "close")}
    return [rows[date] for date in sorted(rows)]


class BigQmtRedisApi(object):
    """Strictly read-only BigQMT Redis RPC adapter for this screener."""
    def __init__(self):
        config = load_client_config()
        self.account_id = str(config.get("account_id") or "")
        if not self.account_id:
            raise RuntimeError("BigQMT account_id is missing from private client configuration")
        self.trader, self.xtdata = configure(
            account_id=self.account_id, timeout_seconds=HISTORY_RPC_TIMEOUT_SECONDS)

    def verify(self):
        pong = self.trader.client.call("ping")
        if (not isinstance(pong, dict) or not pong.get("pong") or
                str(pong.get("account_id")) != self.account_id):
            raise RuntimeError("BigQMT bridge ping did not confirm configured account")

    def universe(self):
        codes = self.xtdata.get_stock_list_in_sector(SECTOR_NAME) or []
        if not codes:
            raise RuntimeError("HS A-share sector is empty; update sector data in BigQMT")
        main_board = sorted({str(code).upper() for code in codes if is_main_board(code)})
        _log("[UNIVERSE] all A shares: {} | main-board: {}".format(len(codes), len(main_board)))
        return main_board, "live HS A-share sector; ST checked before final output"

    def daily_ohlc(self, codes, start_date, today):
        history = {}
        total = len(codes)
        for offset in range(0, total, HISTORY_BATCH_SIZE):
            batch = codes[offset:offset + HISTORY_BATCH_SIZE]
            data = self.xtdata.get_market_data_ex(
                field_list=["high", "low", "close"], stock_list=batch, period="1d",
                start_time=start_date, end_time=today, count=HISTORY_BAR_COUNT,
                dividend_type="front", fill_data=True, chunk_size=0,
                timeout_seconds=HISTORY_RPC_TIMEOUT_SECONDS,
            ) or {}
            history.update(data)
            _log("[HISTORY] {}/{} stocks loaded".format(
                min(offset + len(batch), total), total))
        return history

    def ticks(self, codes):
        ticks = {}
        total = len(codes)
        for offset in range(0, total, TICK_CHUNK_SIZE):
            batch = codes[offset:offset + TICK_CHUNK_SIZE]
            ticks.update(self.xtdata.get_full_tick(batch) or {})
            _log("[TICKS] {}/{} stocks requested".format(
                min(offset + len(batch), total), total))
        return ticks

    def selected_names(self, selected):
        names = {}
        for index, (_, code, _) in enumerate(selected, 1):
            try:
                detail = self.xtdata.get_instrument_detail(code) or {}
                names[code] = str(detail.get("InstrumentName") or "-").strip() or "-"
            except Exception:
                names[code] = "-"
            if index == 1 or index % 25 == 0 or index == len(selected):
                _log("[NAMES/ST] {}/{} candidates checked".format(index, len(selected)))
        return names


def run_screen(api=None, today=None):
    api = api or BigQmtRedisApi()
    today_key = today or datetime.now().strftime("%Y%m%d")

    _log("[STEP 1/4] discovering HS main-board A-shares constituents...")
    codes, source = api.universe()
    if not codes:
        _log("[ERROR] unable to obtain HS main-board A-shares constituents")
        return {"universe_count": 0, "history_valid_count": 0,
                "quote_valid_count": 0, "below_ma5": [], "above_ma20": [],
                "selected": [], "skipped": 0}
    _log("[UNIVERSE] HS main-board A-shares: {} stocks ({})".format(len(codes), source))

    start_date = (datetime.strptime(today_key, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
    _log("[STEP 2/4] reading completed daily OHLC through Redis bridge...")
    history = api.daily_ohlc(codes, start_date, today_key)
    rows_by_code = {code: _completed_rows(history.get(code), today_key) for code in codes}
    history_valid_count = sum(calculate_ma_candidate(rows, 1.0) is not None
                              for rows in rows_by_code.values())
    _log("[HISTORY] valid completed history: {}/{} stocks".format(
        history_valid_count, len(codes)))

    _log("[STEP 3/4] reading live prices through Redis bridge...")
    ticks = api.ticks(codes)
    quote_valid_count = sum(_positive((ticks.get(code) or {}).get("lastPrice")) is not None
                            for code in codes)

    _log("[STEP 4/4] calculating v11 MA5/MA20 conditions...")
    below_ma5, above_ma20, selected, skipped = [], [], [], 0
    for code in codes:
        result = calculate_ma_candidate(
            rows_by_code[code], (ticks.get(code) or {}).get("lastPrice", 0),
            below_ma5_percent=MA5_BELOW_PERCENT)
        if result is None:
            skipped += 1
        else:
            item = (result["gap_pct"], code, result)
            if result["below_ma5"]:
                below_ma5.append(item)
            if result["above_ma20"]:
                above_ma20.append(item)
            if result["matched"]:
                selected.append(item)
    below_ma5.sort(key=lambda item: item[0])
    above_ma20.sort(key=lambda item: item[0])
    selected.sort(key=lambda item: item[0])
    names = api.selected_names(selected)
    st_excluded = sum(not allowed_name(names.get(item[1])) for item in selected)
    selected = [item for item in selected if allowed_name(names.get(item[1]))]
    _log("[ST] excluded ST or unknown name: {}".format(st_excluded))
    _print_result_section(
        "HS main-board A-shares: price < MA5 by {:.2f}% AND price > MA20 AND ATR14 >= {:.2f}% AND non-ST".format(
            MA5_BELOW_PERCENT, MIN_ATR_PERCENT), selected, names)
    _log("base MA matched: {} | ATR rejected: {}".format(
        sum(item[2]["above_ma20"] for item in below_ma5),
        sum(item[2]["above_ma20"] and not item[2]["atr_pass"] for item in below_ma5)))
    _log("combined matched: {} | valid history: {}/{} | valid quotes: {}/{} | skipped: {}".format(
        len(selected), history_valid_count, len(codes), quote_valid_count, len(codes), skipped))
    _log("=" * 115)
    return {"universe_count": len(codes), "history_valid_count": history_valid_count,
            "quote_valid_count": quote_valid_count, "below_ma5": below_ma5,
            "above_ma20": above_ma20, "selected": selected, "skipped": skipped}


def main(argv=None):
    parser = argparse.ArgumentParser(description="HS main-board A-shares MA5/MA20 screener v11 through BigQMT Redis")
    parser.add_argument("--mode", default="signal", choices=["signal"])
    parser.parse_args(argv)
    try:
        _log("[CONNECT] Checking configured BigQMT bridge...")
        api = BigQmtRedisApi()
        api.verify()
        _log("[START] HS main-board A-shares MA5/MA20 screener v11; Redis bridge; signal-only")
        result = run_screen(api)
        return 0 if result["universe_count"] > result["skipped"] else 1
    except Exception as error:
        _log("[ERROR] BigQMT Redis bridge unavailable: {}".format(error))
        _log("[ACTION] Start the configured Big QMT Redis bridge, then run run_bigqmt.py --probe.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
