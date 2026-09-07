# -*- coding: utf-8 -*-
"""Independent CSI 500 MA5/MA20 screener v9 through the BigQMT Redis bridge.

The process is signal-only.  It does not import a versioned strategy and never
calls an order, cancellation, download, or other trading write method.
"""
import argparse
import csv
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


INDEX_CODE = "000905.SH"
SHORT_MA_DAYS = 5
LONG_MA_DAYS = 20
MA5_BELOW_PERCENT = 3.0
TICK_CHUNK_SIZE = 100
HISTORY_BATCH_SIZE = 100
HISTORY_BAR_COUNT = 40
HISTORY_RPC_TIMEOUT_SECONDS = 20
NAME_COLUMN_WIDTH = 12
UNIVERSE_FILE = HERE / "csi500_constituents_20260831.csv"
RESULT_HEADER = (
    "code         name              price        MA5    vs_MA5"
    "       MA20   vs_MA20"
)


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


def calculate_ma_candidate(completed_closes, current_price,
                           below_ma5_percent=MA5_BELOW_PERCENT):
    """Implement v9: price is below MA5 by a threshold but remains above MA20."""
    price = _positive(current_price)
    if price is None:
        return None
    closes = [number for number in (_positive(value) for value in completed_closes)
              if number is not None]
    if len(closes) < LONG_MA_DAYS - 1:
        return None
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
        "matched": below_ma5 and above_ma20,
    }


def format_result_line(code, name, result):
    vs_ma20_pct = (result["price"] / result["ma20"] - 1.0) * 100.0
    return "%-12s %s %10.2f %10.2f %9.2f%% %10.2f %+9.2f%%" % (
        code,
        _pad_display(name, NAME_COLUMN_WIDTH),
        result["price"],
        result["ma5"],
        result["gap_pct"],
        result["ma20"],
        vs_ma20_pct,
    )


def _print_result_section(title, selected, names):
    print("=" * 103)
    print(title)
    print(RESULT_HEADER)
    for _, code, result in selected:
        print(format_result_line(code, names[code], result))


def _bundled_universe():
    try:
        with UNIVERSE_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
            codes = ["{}.{}".format(
                row["code"].strip(),
                "SH" if row["code"].strip().startswith("6") else "SZ")
                for row in csv.DictReader(handle)]
    except (OSError, KeyError):
        return []
    return sorted(set(codes)) if len(set(codes)) == 500 else []


def _completed_closes(frame, today):
    """Return completed, valid daily closes; today's forming daily bar is excluded."""
    if frame is None:
        return []
    rows = []
    for index, row in frame.iterrows():
        date, close = _date_key(index), _positive(row.get("close"))
        if date and date < today and close is not None:
            rows.append((date, close))
    return [close for _, close in sorted(rows)]


class BigQmtRedisApi(object):
    """Strictly read-only BigQMT Redis RPC adapter for this screener."""
    def __init__(self):
        config = load_client_config()
        self.account_id = str(config.get("account_id") or "")
        if not self.account_id:
            raise RuntimeError("BigQMT account_id is missing from private client configuration")
        self.trader, self.xtdata = configure(account_id=self.account_id)

    def verify(self):
        pong = self.trader.client.call("ping")
        if (not isinstance(pong, dict) or not pong.get("pong") or
                str(pong.get("account_id")) != self.account_id):
            raise RuntimeError("BigQMT bridge ping did not confirm configured account")

    def universe(self):
        try:
            weights = self.xtdata.get_index_weight(INDEX_CODE) or {}
            codes = weights.keys() if isinstance(weights, dict) else weights
            if codes:
                return sorted(set(codes)), "bridge index weights"
        except Exception:
            pass
        codes = _bundled_universe()
        return codes, "bundled snapshot 2026-08-31" if codes else "unavailable"

    def daily_close(self, codes, start_date, today):
        history = {}
        total = len(codes)
        for offset in range(0, total, HISTORY_BATCH_SIZE):
            batch = codes[offset:offset + HISTORY_BATCH_SIZE]
            data = self.xtdata.get_market_data_ex(
                field_list=["close"], stock_list=batch, period="1d",
                start_time=start_date, end_time=today, count=HISTORY_BAR_COUNT,
                dividend_type="front", fill_data=True, chunk_size=0,
                timeout_seconds=HISTORY_RPC_TIMEOUT_SECONDS,
            ) or {}
            history.update(data)
            print("[HISTORY] {}/{} stocks loaded".format(
                min(offset + len(batch), total), total))
        return history

    def ticks(self, codes):
        ticks = {}
        total = len(codes)
        for offset in range(0, total, TICK_CHUNK_SIZE):
            batch = codes[offset:offset + TICK_CHUNK_SIZE]
            ticks.update(self.xtdata.get_full_tick(batch) or {})
            print("[TICKS] {}/{} stocks requested".format(
                min(offset + len(batch), total), total))
        return ticks

    def selected_names(self, selected):
        names = {}
        for _, code, _ in selected:
            try:
                detail = self.xtdata.get_instrument_detail(code) or {}
                names[code] = str(detail.get("InstrumentName") or "-").strip() or "-"
            except Exception:
                names[code] = "-"
        return names


def run_screen(api=None, today=None):
    api = api or BigQmtRedisApi()
    today_key = today or datetime.now().strftime("%Y%m%d")

    print("[STEP 1/4] discovering CSI 500 constituents...")
    codes, source = api.universe()
    if not codes:
        print("[ERROR] unable to obtain CSI 500 constituents")
        return {"universe_count": 0, "history_valid_count": 0,
                "quote_valid_count": 0, "below_ma5": [], "above_ma20": [],
                "selected": [], "skipped": 0}
    print("[UNIVERSE] CSI 500: {} stocks ({})".format(len(codes), source))

    start_date = (datetime.strptime(today_key, "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
    print("[STEP 2/4] reading completed daily closes through Redis bridge...")
    history = api.daily_close(codes, start_date, today_key)
    closes_by_code = {code: _completed_closes(history.get(code), today_key) for code in codes}
    history_valid_count = sum(len(closes) >= LONG_MA_DAYS - 1
                              for closes in closes_by_code.values())
    print("[HISTORY] valid completed history: {}/{} stocks".format(
        history_valid_count, len(codes)))

    print("[STEP 3/4] reading live prices through Redis bridge...")
    ticks = api.ticks(codes)
    quote_valid_count = sum(_positive((ticks.get(code) or {}).get("lastPrice")) is not None
                            for code in codes)

    print("[STEP 4/4] calculating v9 MA5/MA20 conditions...")
    below_ma5, above_ma20, selected, skipped = [], [], [], 0
    for code in codes:
        result = calculate_ma_candidate(
            closes_by_code[code], (ticks.get(code) or {}).get("lastPrice", 0),
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
    names = api.selected_names({item[1]: item for item in below_ma5 + above_ma20}.values())

    _print_result_section(
        "CSI 500: current price < MA5 by {:.2f}%".format(MA5_BELOW_PERCENT),
        below_ma5, names)
    print("matched below MA5: {}".format(len(below_ma5)))
    _print_result_section("CSI 500: current price > MA20", above_ma20, names)
    print("matched above MA20: {}".format(len(above_ma20)))
    print("combined matched: {} | valid history: {}/{} | valid quotes: {}/{} | skipped: {}".format(
        len(selected), history_valid_count, len(codes), quote_valid_count, len(codes), skipped))
    print("=" * 103)
    return {"universe_count": len(codes), "history_valid_count": history_valid_count,
            "quote_valid_count": quote_valid_count, "below_ma5": below_ma5,
            "above_ma20": above_ma20, "selected": selected, "skipped": skipped}


def main(argv=None):
    parser = argparse.ArgumentParser(description="CSI 500 MA5/MA20 screener v9 through BigQMT Redis")
    parser.add_argument("--mode", default="signal", choices=["signal"])
    parser.parse_args(argv)
    try:
        api = BigQmtRedisApi()
        api.verify()
        print("[START] CSI 500 MA5/MA20 screener v9; Redis bridge; signal-only")
        return 0 if run_screen(api)["universe_count"] else 1
    except Exception as error:
        print("[ERROR] BigQMT Redis bridge unavailable: {}".format(error))
        print("[ACTION] Start the configured Big QMT Redis bridge, then run run_bigqmt.py --probe.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
