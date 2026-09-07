# -*- coding: utf-8 -*-
"""Read-only CSI500 MA5/MA20 overnight signal strategy through BigQMT Redis RPC.

The Big QMT bridge is the only component that talks to QMT.  This external
process never invokes order or cancel RPCs: its positions are deliberately
virtual and last only for the current process.
"""
import argparse
import csv
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[2]
BRIDGE_SRC = PROJECT_ROOT / "integrations" / "bigqmt" / "src"
if str(BRIDGE_SRC) not in sys.path:
    sys.path.insert(0, str(BRIDGE_SRC))

from bigqmt_signal_trader.xtquant_compat import (  # noqa: E402
    StockAccount,
    configure,
    load_client_config,
)


INDEX_CODE = "000905.SH"
TICK_CHUNK_SIZE = 100
# 100 codes / 40 completed daily bars was verified against the configured bridge.
# Keep this bounded to prevent a single RPC from requesting the entire CSI 500.
HISTORY_BATCH_SIZE = 100
HISTORY_BAR_COUNT = 40
HISTORY_RPC_TIMEOUT_SECONDS = 20
TRADE_LOT_SIZE = 100
ATR_PERIOD = 14
FIXED_MA5_BELOW_PERCENT = 3.0
MIN_ATR_PERCENT = 7.0
MIN_PULLBACK_ATR = 0.5
BUY_TRIGGER_PCT = 0.030
BUY_TRIGGER_TRAIL = 0.020
BUY_BOUNCE_PCT = 0.003
STABLE_SECONDS = 180
SELLBACK_RISE_PCT = 0.012
SELL_PULLBACK_PCT = 0.001
FORCE_SELL_TIME = "10:00:00"
MAX_REALTIME_MATCHES = 20
MAX_CONCURRENT_POSITIONS = 8
MAX_DAILY_ENTRIES = 10
INDEX_MAX_INTRADAY_DROP = 0.015
SCAN_INTERVAL_SEC = 60
LOOP_INTERVAL_SEC = 1
UNIVERSE_FILE = HERE / "csi500_constituents_20260831.csv"


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


def calculate_atr(rows, period=ATR_PERIOD):
    if len(rows) < period + 1:
        return None
    values, ranges = rows[-(period + 1):], []
    for index in range(1, len(values)):
        prior_close = values[index - 1][2]
        high, low = values[index][0], values[index][1]
        ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    return sum(ranges) / float(period)


def calculate_candidate(completed_rows, current_price):
    """v15 selection rule evaluated only from completed daily bars."""
    price = _positive(current_price)
    if price is None or len(completed_rows) < 25:
        return None
    try:
        rows = [(float(high), float(low), float(close)) for high, low, close in completed_rows]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) and item > 0 for row in rows for item in row):
        return None
    closes = [row[2] for row in rows]
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    ma20_previous = sum(closes[-25:-5]) / 20.0
    atr14 = calculate_atr(rows)
    if atr14 is None or atr14 <= 0:
        return None
    gap_pct = (price / ma5 - 1.0) * 100.0
    atr_percent = atr14 / closes[-1] * 100.0
    pullback_atr = (ma5 - price) / atr14
    return {
        "price": price, "ma5": ma5, "ma20": ma20, "atr14": atr14,
        "gap_pct": gap_pct, "atr_percent": atr_percent,
        "pullback_atr": pullback_atr,
        "matched": (price > ma20 and ma20 > ma20_previous and
                    gap_pct < -FIXED_MA5_BELOW_PERCENT and
                    atr_percent >= MIN_ATR_PERCENT),
    }


def is_market_allowed(completed_closes, current_price):
    closes = [_positive(value) for value in completed_closes]
    price = _positive(current_price)
    if price is None or len(closes) < 25 or any(value is None for value in closes):
        return False
    return (price > (sum(closes[-19:]) + price) / 20.0 and
            sum(closes[-20:]) > sum(closes[-25:-5]) and
            price / closes[-1] - 1.0 >= -INDEX_MAX_INTRADAY_DROP)


def _seconds(value):
    parsed = datetime.strptime(str(value)[:8], "%H:%M:%S")
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def new_buy_watch(open_price, current_price, time_text):
    return {"phase": "WAITING", "buy_trigger_floor": float(open_price) * (1 - BUY_TRIGGER_PCT),
            "max_trail": float(current_price) * (1 - BUY_TRIGGER_TRAIL),
            "dip_price": 0.0, "stable_since": str(time_text)}


def advance_buy_watch(watch, price, time_text):
    price = float(price)
    if watch["phase"] == "WAITING":
        watch["max_trail"] = max(float(watch["max_trail"]), price * (1 - BUY_TRIGGER_TRAIL))
        if price <= max(float(watch["buy_trigger_floor"]), float(watch["max_trail"])):
            watch.update(phase="DIPPING", dip_price=price, stable_since=str(time_text))
        return None
    if watch["phase"] == "DIPPING":
        if price < float(watch["dip_price"]):
            watch.update(dip_price=price, stable_since=str(time_text))
        elif (_seconds(time_text) - _seconds(watch["stable_since"]) >= STABLE_SECONDS and
              price >= float(watch["dip_price"]) * (1 + BUY_BOUNCE_PCT)):
            watch["phase"] = "BUY_READY"
            return "BUY"
    return None


def new_sell_watch(buy_date, buy_price, shares):
    return {"phase": "BOUGHT", "buy_date": str(buy_date), "buy_price": float(buy_price),
            "shares": int(shares), "sell_peak_price": 0.0}


def advance_sell_watch(position, trade_date, time_text, price):
    if str(trade_date) <= str(position["buy_date"]):
        return None
    price = float(price)
    if time_text >= FORCE_SELL_TIME:
        return "FORCE_1000"
    if position["phase"] == "BOUGHT":
        if price >= float(position["buy_price"]) * (1 + SELLBACK_RISE_PCT):
            position.update(phase="SPIKING", sell_peak_price=price)
    elif position["phase"] == "SPIKING":
        position["sell_peak_price"] = max(float(position["sell_peak_price"]), price)
        if ((position["sell_peak_price"] - price) / position["sell_peak_price"] >=
                SELL_PULLBACK_PCT):
            return "SELLBACK"
    return None


def _bundled_universe():
    try:
        with UNIVERSE_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
            return sorted(set("{}.{}".format(
                row["code"].strip(), "SH" if row["code"].strip().startswith("6") else "SZ")
                for row in csv.DictReader(handle)))
    except (OSError, KeyError):
        return []


class BigQmtRedisApi(object):
    """Read-only façade over the explicit BigQMT Redis compatibility API."""
    def __init__(self):
        config = load_client_config()
        self.account_id = str(config.get("account_id") or "")
        if not self.account_id:
            raise RuntimeError("BigQMT account_id is missing from private client configuration")
        self.trader, self.xtdata = configure(account_id=self.account_id)
        self.account = StockAccount(self.account_id, "STOCK")

    def verify(self):
        pong = self.trader.client.call("ping")
        if not isinstance(pong, dict) or not pong.get("pong") or str(pong.get("account_id")) != self.account_id:
            raise RuntimeError("BigQMT bridge ping did not confirm configured account")
        # Both calls are deliberately read-only and confirm account mapping.
        self.trader.query_stock_asset(self.account)
        self.trader.query_stock_positions(self.account)

    def universe(self):
        try:
            codes = self.xtdata.get_index_weight(INDEX_CODE) or {}
            codes = codes.keys() if isinstance(codes, dict) else codes
            if codes:
                return sorted(set(codes))
        except Exception:
            pass
        return _bundled_universe()

    def daily_ohlc(self, codes, start, today):
        result = {}
        total = len(codes)
        for offset in range(0, total, HISTORY_BATCH_SIZE):
            batch = codes[offset:offset + HISTORY_BATCH_SIZE]
            data = self.xtdata.get_market_data_ex(
                field_list=["high", "low", "close"], stock_list=batch,
                period="1d", start_time=start, end_time=today,
                count=HISTORY_BAR_COUNT, dividend_type="front", fill_data=True,
                chunk_size=0, timeout_seconds=HISTORY_RPC_TIMEOUT_SECONDS,
            ) or {}
            result.update(data)
            print("[HISTORY] {}/{} stocks loaded".format(
                min(offset + len(batch), total), total))
        return result

    def ticks(self, codes):
        result = {}
        for offset in range(0, len(codes), TICK_CHUNK_SIZE):
            result.update(self.xtdata.get_full_tick(codes[offset:offset + TICK_CHUNK_SIZE]) or {})
        return result


def _completed_ohlc(frame, today):
    rows = []
    if frame is None:
        return rows
    for index, row in frame.iterrows():
        date = _date_key(index)
        high, low, close = _positive(row.get("high")), _positive(row.get("low")), _positive(row.get("close"))
        if date and date < today and high and low and close:
            rows.append((date, high, low, close))
    return [(high, low, close) for _, high, low, close in sorted(rows)]


class SignalRunner(object):
    def __init__(self, api=None):
        self.api = api or BigQmtRedisApi()
        self.codes, self.ohlc, self.index_closes = [], {}, []
        self.history_date, self.last_scan = "", 0.0
        self.watches, self.positions = {}, {}
        self.entry_date, self.entry_count, self.entry_allowed = "", 0, False

    def refresh_history(self, today):
        if self.history_date == today:
            return
        if not self.codes:
            self.codes = self.api.universe()
            if not self.codes:
                raise RuntimeError("CSI500 universe unavailable from bridge and bundled snapshot")
        start = (datetime.strptime(today, "%Y%m%d") - timedelta(days=370)).strftime("%Y%m%d")
        raw = self.api.daily_ohlc(sorted(set(self.codes + [INDEX_CODE])), start, today)
        self.ohlc = {code: _completed_ohlc(raw.get(code), today) for code in self.codes + [INDEX_CODE]}
        self.index_closes = [row[2] for row in self.ohlc.get(INDEX_CODE, [])]
        self.history_date = today

    def scan(self, today, hms, ticks):
        index_price = _positive((ticks.get(INDEX_CODE) or {}).get("lastPrice"))
        matches, candidates = 0, []
        for code in self.codes:
            price = _positive((ticks.get(code) or {}).get("lastPrice"))
            result = calculate_candidate(self.ohlc.get(code, []), price) if price else None
            if result and result["matched"]:
                matches += 1
                if code not in self.positions and code not in self.watches:
                    candidates.append((-result["pullback_atr"], code, result))
        self.entry_allowed = bool(index_price) and is_market_allowed(self.index_closes, index_price) and matches <= MAX_REALTIME_MATCHES
        for _, code, result in sorted(candidates):
            print("[SELECTED] {} price={:.2f} MA5={:.2f} MA20={:.2f} "
                  "ATR14={:.2f}% pullback={:.2f}ATR".format(
                      code, result["price"], result["ma5"], result["ma20"],
                      result["atr_percent"], result["pullback_atr"]))
        print("[SCREEN] candidates={} MA-matches={} entry_allowed={}".format(
            len(candidates), matches, self.entry_allowed))
        if not self.entry_allowed:
            print("[FILTER] virtual entry blocked; market filter or breadth limit failed")
            return candidates
        for _, code, result in sorted(candidates):
            tick, price = ticks[code], result["price"]
            watch = new_buy_watch(_positive(tick.get("open")) or price, price, hms)
            watch.update(screen_date=today, atr14=result["atr14"], atr_percent=result["atr_percent"])
            self.watches[code] = watch
        return candidates

    def scan_once(self, now=None):
        """Print the current candidates even outside trading hours; never trades."""
        now = now or datetime.now()
        today, hms = now.strftime("%Y%m%d"), now.strftime("%H:%M:%S")
        self.refresh_history(today)
        ticks = self.api.ticks(sorted(set(self.codes + [INDEX_CODE])))
        return self.scan(today, hms, ticks)

    def process_entries(self, today, hms, ticks):
        if self.entry_date != today:
            self.entry_date, self.entry_count = today, 0
        for code, watch in list(self.watches.items()):
            if watch.get("screen_date") != today:
                del self.watches[code]
                continue
            if (not self.entry_allowed or len(self.positions) >= MAX_CONCURRENT_POSITIONS or
                    self.entry_count >= MAX_DAILY_ENTRIES):
                break
            price = _positive((ticks.get(code) or {}).get("lastPrice"))
            if price and advance_buy_watch(watch, price, hms) == "BUY":
                self.positions[code] = new_sell_watch(today, price, TRADE_LOT_SIZE)
                del self.watches[code]
                self.entry_count += 1
                print("[VIRTUAL-ENTRY] {} @ {:.2f} x {} (no order sent)".format(code, price, TRADE_LOT_SIZE))

    def process_exits(self, today, hms, ticks):
        for code, position in list(self.positions.items()):
            price = _positive((ticks.get(code) or {}).get("lastPrice"))
            event = advance_sell_watch(position, today, hms, price) if price else None
            if event:
                del self.positions[code]
                print("[VIRTUAL-EXIT-{}] {} @ {:.2f} (no order sent)".format(event, code, price))

    def run(self):
        self.api.verify()
        print("[START] BigQMT Redis v15 signal-only; orders are disabled")
        try:
            self.scan_once()
            while True:
                now = datetime.now()
                today, hms = now.strftime("%Y%m%d"), now.strftime("%H:%M:%S")
                if not ("09:30:00" <= hms <= "11:30:00" or "13:00:00" <= hms <= "15:00:00"):
                    time.sleep(5)
                    continue
                self.refresh_history(today)
                tracked = sorted(set(self.codes + [INDEX_CODE] + list(self.watches) + list(self.positions)))
                ticks = self.api.ticks(tracked)
                self.process_exits(today, hms, ticks)
                if time.time() - self.last_scan >= SCAN_INTERVAL_SEC:
                    self.scan(today, hms, ticks)
                    self.last_scan = time.time()
                self.process_entries(today, hms, ticks)
                time.sleep(LOOP_INTERVAL_SEC)
        except KeyboardInterrupt:
            return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="CSI500 v15 signal-only strategy over BigQMT Redis RPC")
    parser.add_argument("--mode", default="signal", choices=["signal"])
    args = parser.parse_args(argv)
    del args
    try:
        return SignalRunner().run()
    except Exception as error:
        print("[ERROR] BigQMT Redis bridge unavailable: {}".format(error))
        print("[ACTION] Start the configured Big QMT Redis bridge, then run run_bigqmt.py --probe.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
