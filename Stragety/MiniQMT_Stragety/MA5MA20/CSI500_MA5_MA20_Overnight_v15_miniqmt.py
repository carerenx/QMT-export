# -*- coding: gbk -*-
"""Independent CSI500 MA5/MA20 overnight strategy for MiniQMT.

No versioned strategy module is imported.  Selection, execution state machines,
history validation and risk guards are contained in this deployable file.
"""
import argparse
import csv
import json
import math
import os
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

ACCOUNT = '8890145315'
INDEX_CODE = '000905.SH'
TRADE_LOT_SIZE, TICK_CHUNK_SIZE = 100, 100
ATR_PERIOD, MA20_SLOPE_LOOKBACK = 14, 5
FIXED_MA5_BELOW_PERCENT, MIN_ATR_PERCENT, MIN_PULLBACK_ATR = 3.0, 7.0, 0.5
BUY_TRIGGER_PCT, BUY_TRIGGER_TRAIL, BUY_BOUNCE_PCT, STABLE_SECONDS = .030, .020, .003, 180
SELLBACK_RISE_PCT, SELL_PULLBACK_PCT, FORCE_SELL_TIME = .012, .001, '10:00:00'
MAX_REALTIME_MATCHES, MAX_POSITION_PCT = 20, .03
MAX_CONCURRENT_POSITIONS, MAX_DAILY_ENTRIES = 8, 10
INDEX_MAX_INTRADAY_DROP, SCAN_INTERVAL_SEC, LOOP_INTERVAL_SEC, FILL_TIMEOUT_SEC = .015, 60, 1, 8
HERE = Path(__file__).resolve().parent
# Support both ``python path/to/strategy.py`` and package execution. The
# shared connector has a legacy absolute ``core`` import, so both its package
# root and the repository root must be available before it is imported.
PROJECT_ROOT = HERE.parents[2]
SHARED_PACKAGE_ROOT = HERE.parents[0]
for _path in (str(PROJECT_ROOT), str(SHARED_PACKAGE_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
STATE_FILE = str(HERE / 'CSI500_MA5_MA20_Overnight_v15_state.json')
UNIVERSE_FILE = HERE / 'csi500_constituents_20260831.csv'


def _positive(value):
    try: value = float(value)
    except (TypeError, ValueError): return None
    return value if math.isfinite(value) and value > 0 else None


def _date_key(value):
    if hasattr(value, 'strftime'): return value.strftime('%Y%m%d')
    text = str(value).replace('-', '').replace('/', '')
    if len(text) >= 8 and text[:8].isdigit(): return text[:8]
    try:
        stamp = float(value) / (1000.0 if float(value) > 1e11 else 1.0)
        return datetime.fromtimestamp(stamp).strftime('%Y%m%d')
    except (TypeError, ValueError, OSError, OverflowError): return ''


def calculate_atr(rows, period=ATR_PERIOD):
    if len(rows) < period + 1: return None
    values, ranges = rows[-(period + 1):], []
    for i in range(1, len(values)):
        prior, high, low = float(values[i - 1][2]), float(values[i][0]), float(values[i][1])
        ranges.append(max(high - low, abs(high - prior), abs(low - prior)))
    return sum(ranges) / float(period)


def calculate_optimized_candidate(completed_rows, current_price, variant='optimized'):
    """Calculate using *only completed* daily OHLC rows: (high, low, close)."""
    price = _positive(current_price)
    if price is None or len(completed_rows) < 25: return None
    try: rows = [(float(a), float(b), float(c)) for a, b, c in completed_rows]
    except (TypeError, ValueError): return None
    if not all(math.isfinite(x) and x > 0 for row in rows for x in row): return None
    closes = [row[2] for row in rows]
    ma5, ma20, ma20_previous = sum(closes[-5:]) / 5.0, sum(closes[-20:]) / 20.0, sum(closes[-25:-5]) / 20.0
    atr14 = calculate_atr(rows)
    if atr14 is None or atr14 <= 0: return None
    gap_pct, pullback_atr = (price / ma5 - 1.0) * 100.0, (ma5 - price) / atr14
    atr_percent, ma20_rising, above_ma20 = atr14 / closes[-1] * 100.0, ma20 > ma20_previous, price > ma20
    if variant == 'fixed': matched = above_ma20 and gap_pct < -FIXED_MA5_BELOW_PERCENT
    elif variant == 'fixed_slope': matched = above_ma20 and ma20_rising and gap_pct < -FIXED_MA5_BELOW_PERCENT
    elif variant == 'atr_normalized': matched = above_ma20 and ma20_rising and pullback_atr >= MIN_PULLBACK_ATR
    elif variant == 'optimized': matched = above_ma20 and ma20_rising and gap_pct < -FIXED_MA5_BELOW_PERCENT and atr_percent >= MIN_ATR_PERCENT
    else: raise ValueError('unknown selection variant: {}'.format(variant))
    return {'price': price, 'ma5': ma5, 'ma20': ma20, 'ma20_previous': ma20_previous,
            'ma20_rising': ma20_rising, 'atr14': atr14, 'atr_percent': atr_percent,
            'pullback_atr': pullback_atr, 'gap_pct': gap_pct, 'matched': bool(matched)}


def is_market_allowed(completed_closes, current_price):
    values, price = [_positive(x) for x in completed_closes], _positive(current_price)
    if len(values) < 25 or price is None or any(x is None for x in values): return False
    return (price > (sum(values[-19:]) + price) / 20.0 and sum(values[-20:]) > sum(values[-25:-5]) and
            price / values[-1] - 1.0 >= -INDEX_MAX_INTRADAY_DROP)


def _seconds(text):
    item = datetime.strptime(str(text)[:8], '%H:%M:%S'); return item.hour * 3600 + item.minute * 60 + item.second


def new_buy_watch(open_price, current_price, time_text):
    return {'phase': 'WAITING', 'buy_trigger_floor': float(open_price) * (1 - BUY_TRIGGER_PCT),
            'max_trail': float(current_price) * (1 - BUY_TRIGGER_TRAIL), 'dip_price': 0.0, 'stable_since': str(time_text)}


def advance_buy_watch(state, price, time_text):
    price = float(price)
    if state['phase'] == 'WAITING':
        state['max_trail'] = max(float(state.get('max_trail', 0)), price * (1 - BUY_TRIGGER_TRAIL))
        if price <= max(state['buy_trigger_floor'], state['max_trail']):
            state.update(phase='DIPPING', dip_price=price, stable_since=str(time_text))
        return None
    if state['phase'] == 'DIPPING':
        if price < float(state['dip_price']): state.update(dip_price=price, stable_since=str(time_text)); return None
        if _seconds(time_text) - _seconds(state['stable_since']) >= STABLE_SECONDS and price >= float(state['dip_price']) * (1 + BUY_BOUNCE_PCT):
            state['phase'] = 'BUY_READY'; return 'BUY'
    return None


def new_sell_watch(buy_date, buy_price):
    return {'phase': 'BOUGHT', 'buy_date': str(buy_date), 'buy_price': float(buy_price), 'sell_peak_price': 0.0}


def advance_sell_watch(state, trade_date, time_text, price):
    if str(trade_date) <= str(state['buy_date']): return None
    price = float(price)
    if time_text >= FORCE_SELL_TIME: return 'FORCE_1000'
    if state['phase'] == 'BOUGHT':
        if price >= float(state['buy_price']) * (1 + SELLBACK_RISE_PCT): state.update(phase='SPIKING', sell_peak_price=price)
    elif state['phase'] == 'SPIKING':
        state['sell_peak_price'] = max(float(state['sell_peak_price']), price)
        if (state['sell_peak_price'] - price) / state['sell_peak_price'] >= SELL_PULLBACK_PCT: return 'SELLBACK'
    return None


def _load_state():
    empty = {'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}
    try:
        with open(STATE_FILE, 'r') as file: state = json.load(file)
        for key, value in empty.items(): state.setdefault(key, value)
        return state
    except (OSError, ValueError, TypeError): return empty


def _save_state(state):
    temporary = STATE_FILE + '.tmp'
    with open(temporary, 'w') as file: json.dump(state, file, ensure_ascii=True, indent=2, sort_keys=True)
    os.replace(temporary, STATE_FILE)


def _market_open(hms): return '09:30:00' <= hms <= '11:30:00' or '13:00:00' <= hms <= '15:00:00'


def _universe(xtdata):
    for source in ('sector', 'weight', 'file'):
        try:
            if source == 'sector': codes = xtdata.get_stock_list_in_sector('中证500') or []
            elif source == 'weight': codes = (xtdata.get_index_weight(INDEX_CODE) or {}).keys()
            else:
                with UNIVERSE_FILE.open(encoding='utf-8-sig', newline='') as file:
                    codes = ['{}.{}'.format(r['code'].strip(), 'SH' if r['code'].strip().startswith('6') else 'SZ') for r in csv.DictReader(file)]
            if codes: return sorted(set(codes))
        except (OSError, KeyError, AttributeError, TypeError): pass
    return []


def _daily_ohlc(xtdata, codes, start, today):
    data = xtdata.get_local_data(field_list=['high', 'low', 'close'], stock_list=codes, period='1d', start_time=start, end_time=today, dividend_type='front', fill_data=True) or {}
    result = {}
    for code in codes:
        rows, frame = [], data.get(code)
        if frame is not None:
            for index, row in frame.iterrows():
                date, high, low, close = _date_key(index), _positive(row.get('high')), _positive(row.get('low')), _positive(row.get('close'))
                if date < today and high and low and close: rows.append((date, high, low, close))
        result[code] = [(high, low, close) for _, high, low, close in sorted(rows)]
    return result


def _ticks(xtdata, codes):
    output = {}
    for offset in range(0, len(codes), TICK_CHUNK_SIZE): output.update(xtdata.get_full_tick(codes[offset:offset + TICK_CHUNK_SIZE]) or {})
    return output


def _position_map(conn):
    return {getattr(p, 'stock_code', ''): {'volume': int(getattr(p, 'volume', 0) or 0), 'can_use': int(getattr(p, 'can_use_volume', 0) or 0)} for p in conn.query_positions() if getattr(p, 'stock_code', '')}


class OptimizedSelectionRunner(object):
    def __init__(self, dry_run=True):
        from Stragety.MiniQMT_Stragety.DayT.infra.connector import MiniQMTConnector
        self.conn, self.dry_run = MiniQMTConnector(), dry_run
        self.state = _load_state() if not dry_run else {'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}
        self.codes, self.ohlc_by_code, self.index_closes, self.history_date, self.last_scan, self.entry_allowed = [], {}, [], '', 0.0, False

    def _persist(self):
        if not self.dry_run: _save_state(self.state)

    def _refresh_history(self, today):
        if self.history_date == today: return
        if not self.codes: self.codes = _universe(self.conn.xtdata); print('[UNIVERSE] {} stocks'.format(len(self.codes)))
        start = (datetime.strptime(today, '%Y%m%d') - timedelta(days=370)).strftime('%Y%m%d')
        self.ohlc_by_code = _daily_ohlc(self.conn.xtdata, self.codes + [INDEX_CODE], start, today)
        self.index_closes = [row[2] for row in self.ohlc_by_code.get(INDEX_CODE, [])]; self.history_date = today

    def _submit(self, code, shares, price):
        if self.dry_run: print('[SIGNAL] {} {} x {} @ {:.2f}'.format('BUY' if shares > 0 else 'SELL', code, abs(shares), price)); return abs(shares)
        positions, before = _position_map(self.conn), _position_map(self.conn).get(code, {}).get('volume', 0)
        account = self.conn.query_account()
        if shares > 0 and float(getattr(account, 'cash', 0) or 0) < price * shares * 1.001: return 0
        if shares < 0: shares = -min(abs(shares), positions.get(code, {}).get('can_use', 0))
        if not shares: return 0
        order_id = self.conn.order_stock(code, shares, 'COMPETE', price)
        if order_id is None: return 0
        filled, deadline = 0, _time.time() + FILL_TIMEOUT_SEC
        while _time.time() < deadline:
            _time.sleep(.5); after = _position_map(self.conn).get(code, {}).get('volume', 0)
            filled = max(0, after - before) if shares > 0 else max(0, before - after)
            if filled >= abs(shares): return filled
        self.conn.cancel_order(order_id); return filled

    def _process_exits(self, today, hms, ticks):
        changed = False
        for code, position in list(self.state['positions'].items()):
            price = _positive((ticks.get(code) or {}).get('lastPrice')); event = advance_sell_watch(position, today, hms, price) if price else None
            if event and self._submit(code, -int(position['shares']), price) >= int(position['shares']): del self.state['positions'][code]; changed = True
        if changed: self._persist()

    def _scan(self, today, hms, ticks):
        index_price = _positive((ticks.get(INDEX_CODE) or {}).get('lastPrice')); candidates, matches = [], 0
        for code in self.codes:
            price = _positive((ticks.get(code) or {}).get('lastPrice')); result = calculate_optimized_candidate(self.ohlc_by_code.get(code, []), price) if price else None
            if result and result['matched']:
                matches += 1
                if code not in self.state['positions'] and code not in self.state['watches']: candidates.append((-result['pullback_atr'], code, result))
        self.entry_allowed = bool(index_price) and is_market_allowed(self.index_closes, index_price) and matches <= MAX_REALTIME_MATCHES
        if not self.entry_allowed: print('[FILTER] matches={}/{}'.format(matches, MAX_REALTIME_MATCHES)); return
        for _, code, result in sorted(candidates):
            price, tick = result['price'], ticks[code]; watch = new_buy_watch(_positive(tick.get('open')) or price, price, hms)
            watch.update(screen_date=today, name='-', atr14=result['atr14'], atr_percent=result['atr_percent']); self.state['watches'][code] = watch
            print('[SELECTED] {} price={:.2f} MA5={:.2f} MA20={:.2f}'.format(code, price, result['ma5'], result['ma20']))
        if candidates: self._persist()

    def _process_entries(self, today, hms, ticks):
        if self.state['entry_date'] != today: self.state.update(entry_date=today, entry_count=0)
        changed = False
        for code, watch in list(self.state['watches'].items()):
            if watch.get('screen_date') != today: del self.state['watches'][code]; changed = True; continue
            if not self.entry_allowed or len(self.state['positions']) >= MAX_CONCURRENT_POSITIONS or self.state['entry_count'] >= MAX_DAILY_ENTRIES: break
            price = _positive((ticks.get(code) or {}).get('lastPrice'))
            if not price or advance_buy_watch(watch, price, hms) != 'BUY': continue
            asset = self.conn.query_account() if not self.dry_run else None; value = float(getattr(asset, 'total_asset', 1000000) or 0) if asset else 1000000.0
            if price * TRADE_LOT_SIZE > value * MAX_POSITION_PCT: del self.state['watches'][code]; changed = True; continue
            bought = self._submit(code, TRADE_LOT_SIZE, price)
            if bought:
                position = new_sell_watch(today, price); position.update(shares=bought, name=watch.get('name', '-'), buy_time=hms)
                self.state['positions'][code] = position; del self.state['watches'][code]; self.state['entry_count'] += 1; changed = True
        if changed: self._persist()

    def run(self):
        try:
            if not self.conn.connect_data(): return 1
        except ModuleNotFoundError as error:
            if error.name != 'xtquant':
                raise
            print('[ERROR] xtquant SDK is unavailable in {}'.format(sys.executable))
            print('[ACTION] Run this strategy with the Python bundled with MiniQMT, then start MiniQMT.')
            print('[ACTION] Do not install xtquant into Python 3.14; use the SDK version supplied by your MiniQMT installation.')
            return 2
        if not self.dry_run and not self.conn.connect_trade(ACCOUNT): self.conn.disconnect(); return 1
        try:
            while True:
                now = datetime.now(); today, hms = now.strftime('%Y%m%d'), now.strftime('%H:%M:%S')
                if not _market_open(hms): _time.sleep(5); continue
                self._refresh_history(today); tracked = sorted(set(self.codes + [INDEX_CODE] + list(self.state['positions']) + list(self.state['watches']))); ticks = _ticks(self.conn.xtdata, tracked)
                self._process_exits(today, hms, ticks)
                if _time.time() - self.last_scan >= SCAN_INTERVAL_SEC: self._scan(today, hms, ticks); self.last_scan = _time.time()
                self._process_entries(today, hms, ticks); _time.sleep(LOOP_INTERVAL_SEC)
        except KeyboardInterrupt: return 0
        finally: self.conn.disconnect()


def main():
    parser = argparse.ArgumentParser(description='Independent CSI500 MA5/MA20 overnight v15')
    parser.add_argument('--mode', default='signal', choices=['signal', 'live', 'backtest']); parser.add_argument('--start', default='20250903'); parser.add_argument('--end', default='20260902'); args = parser.parse_args()
    if args.mode == 'backtest':
        from Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v15 import run_backtest
        return run_backtest(args.start, args.end)
    if args.mode == 'live' and input('Type yes to continue: ').strip().lower() != 'yes': return 1
    return OptimizedSelectionRunner(dry_run=args.mode != 'live').run()


if __name__ == '__main__': raise SystemExit(main())
