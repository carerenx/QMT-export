# -*- coding: gbk -*-
"""Risk-filtered CSI 500 overnight strategy for MiniQMT."""

import argparse
import json
import os
import time as _time
from datetime import datetime, timedelta

import Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Overnight_v10_miniqmt as v10
from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v7_miniqmt import calculate_ma_candidate
from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v8_miniqmt import load_selected_names


ACCOUNT = '8890145315'
INDEX_CODE = '000905.SH'
MA5_BELOW_PERCENT = 3.0

BUY_TRIGGER_PCT = 0.030
BUY_TRIGGER_TRAIL = 0.020
BUY_BOUNCE_PCT = 0.003
STABLE_SECONDS = 180
SELLBACK_RISE_PCT = v10.SELLBACK_RISE_PCT
SELL_PULLBACK_PCT = v10.SELL_PULLBACK_PCT
STOP_LOSS_PCT = v10.STOP_LOSS_PCT
FORCE_SELL_TIME = v10.FORCE_SELL_TIME

MAX_REALTIME_MATCHES = 20
MAX_POSITION_PCT = 0.03
MAX_CONCURRENT_POSITIONS = 8
MAX_DAILY_ENTRIES = 10
INDEX_MAX_INTRADAY_DROP = 0.015

SCAN_INTERVAL_SEC = 60
LOOP_INTERVAL_SEC = 1
STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'CSI500_MA5_MA20_Overnight_v11_state.json')


def _seconds(time_text):
    parsed = datetime.strptime(str(time_text)[:8], '%H:%M:%S')
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def is_market_allowed(completed_closes, current_price):
    values = [float(value) for value in completed_closes]
    if len(values) < 25 or current_price <= 0:
        return False
    last_close = values[-1]
    ma20_now = (sum(values[-19:]) + float(current_price)) / 20.0
    ma20_completed = sum(values[-20:]) / 20.0
    ma20_five_days_ago = sum(values[-25:-5]) / 20.0
    intraday_return = float(current_price) / last_close - 1.0
    return (float(current_price) > ma20_now and
            ma20_completed > ma20_five_days_ago and
            intraday_return >= -INDEX_MAX_INTRADAY_DROP)


def is_screen_match(completed_closes, current_price):
    result = calculate_ma_candidate(
        completed_closes, current_price,
        below_ma5_percent=MA5_BELOW_PERCENT)
    return bool(result and result['matched'])


def new_buy_watch(open_price, current_price, time_text):
    return {
        'phase': 'WAITING',
        'buy_trigger_floor': float(open_price) * (1.0 - BUY_TRIGGER_PCT),
        'max_trail': float(current_price) * (1.0 - BUY_TRIGGER_TRAIL),
        'dip_price': 0.0,
        'stable_since': str(time_text),
    }


def advance_buy_watch(state, price, time_text):
    price = float(price)
    if state['phase'] == 'WAITING':
        state['max_trail'] = max(
            float(state.get('max_trail', 0.0)),
            price * (1.0 - BUY_TRIGGER_TRAIL))
        trigger = max(state['buy_trigger_floor'], state['max_trail'])
        if price <= trigger:
            state['phase'] = 'DIPPING'
            state['dip_price'] = price
            state['stable_since'] = str(time_text)
        return None

    if state['phase'] == 'DIPPING':
        if price < float(state.get('dip_price', price)):
            state['dip_price'] = price
            state['stable_since'] = str(time_text)
            return None
        stable = _seconds(time_text) - _seconds(state['stable_since'])
        dip = float(state['dip_price'])
        if stable >= STABLE_SECONDS and price >= dip * (1.0 + BUY_BOUNCE_PCT):
            state['phase'] = 'BUY_READY'
            return 'BUY'
    return None


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}
    try:
        with open(STATE_FILE, 'r') as file:
            state = json.load(file)
        state.setdefault('watches', {})
        state.setdefault('positions', {})
        state.setdefault('entry_date', '')
        state.setdefault('entry_count', 0)
        return state
    except Exception as error:
        print('[STATE] load failed: {}'.format(error))
        return {'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}


def _save_state(state):
    temp_path = STATE_FILE + '.tmp'
    with open(temp_path, 'w') as file:
        json.dump(state, file, ensure_ascii=True, indent=2, sort_keys=True)
    os.replace(temp_path, STATE_FILE)


class OptimizedOvernightRunner(v10.OvernightRunner):
    def __init__(self, dry_run=True):
        from Stragety.MiniQMT_Stragety.DayT.infra.connector import MiniQMTConnector
        self.conn = MiniQMTConnector()
        self.dry_run = dry_run
        self.state = _load_state() if not dry_run else {
            'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}
        self.codes = []
        self.closes_by_code = {}
        self.index_closes = []
        self.history_date = ''
        self.last_scan = 0.0
        self.entry_allowed = False

    def _persist(self):
        if not self.dry_run:
            _save_state(self.state)

    def _refresh_history(self, today):
        if self.history_date == today:
            return
        v10.OvernightRunner._refresh_history(self, today)
        start = (datetime.strptime(today, '%Y%m%d') - timedelta(days=370)).strftime('%Y%m%d')
        history = v10._read_history(self.conn.xtdata, [INDEX_CODE], start, today)
        closes = v10._valid_completed_closes_by_code(history, [INDEX_CODE], today)
        if len(closes.get(INDEX_CODE, [])) < 25:
            self.conn.xtdata.download_history_data(
                INDEX_CODE, '1d', start_time=start, end_time=today)
            history = v10._read_history(self.conn.xtdata, [INDEX_CODE], start, today)
            closes = v10._valid_completed_closes_by_code(history, [INDEX_CODE], today)
        self.index_closes = closes.get(INDEX_CODE, [])

    def _scan(self, today, hms, ticks):
        index_price = float(ticks.get(INDEX_CODE, {}).get('lastPrice', 0) or 0)
        market_ok = is_market_allowed(self.index_closes, index_price)
        candidates = []
        realtime_matches = 0
        for code in self.codes:
            price = float(ticks.get(code, {}).get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            result = calculate_ma_candidate(
                self.closes_by_code.get(code, []), price,
                below_ma5_percent=MA5_BELOW_PERCENT)
            if result and result['matched']:
                realtime_matches += 1
                if code in self.state['positions'] or code in self.state['watches']:
                    continue
                candidates.append((result['gap_pct'], code, result))

        self.entry_allowed = market_ok and realtime_matches <= MAX_REALTIME_MATCHES
        if not self.entry_allowed:
            print('[FILTER] market_ok={} realtime_matches={}/{}'.format(
                market_ok, realtime_matches, MAX_REALTIME_MATCHES))
            return

        names = load_selected_names(self.conn.xtdata, candidates)
        for _, code, result in candidates:
            tick = ticks[code]
            price = result['price']
            open_price = float(tick.get('open', 0) or price)
            watch = new_buy_watch(open_price, price, hms)
            watch.update({
                'screen_date': today,
                'screen_price': price,
                'name': names.get(code, '-'),
            })
            self.state['watches'][code] = watch
            print('[SELECTED] {} {} price={:.2f} MA5={:.2f} MA20={:.2f}'.format(
                code, watch['name'], price, result['ma5'], result['ma20']))
        if candidates:
            self._persist()

    def _portfolio_value(self):
        if self.dry_run:
            return 1000000.0
        account = self.conn.query_account()
        return float(getattr(account, 'total_asset', 0.0) or 0.0)

    def _process_entries(self, today, hms, ticks):
        if self.state.get('entry_date') != today:
            self.state['entry_date'] = today
            self.state['entry_count'] = 0
        changed = False
        for code, watch in list(self.state['watches'].items()):
            if watch.get('screen_date') != today:
                del self.state['watches'][code]
                changed = True
                continue
            if not self.entry_allowed:
                continue
            if len(self.state['positions']) >= MAX_CONCURRENT_POSITIONS:
                break
            if int(self.state.get('entry_count', 0)) >= MAX_DAILY_ENTRIES:
                break
            price = float(ticks.get(code, {}).get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            if advance_buy_watch(watch, price, hms) != 'BUY':
                continue
            portfolio_value = self._portfolio_value()
            if portfolio_value <= 0 or price * 100 > portfolio_value * MAX_POSITION_PCT:
                print('[RISK] {} skipped: one lot exceeds position cap'.format(code))
                del self.state['watches'][code]
                changed = True
                continue
            bought = self._submit(code, 100, price)
            if bought > 0:
                position = v10.new_sell_watch(today, price)
                position.update({
                    'shares': bought,
                    'name': watch.get('name', '-'),
                    'buy_time': hms,
                })
                self.state['positions'][code] = position
                self.state['entry_count'] = int(self.state.get('entry_count', 0)) + 1
                del self.state['watches'][code]
                print('[ENTRY] {} {} @ {:.2f} x {}'.format(
                    code, position['name'], price, bought))
                changed = True
        if changed:
            self._persist()

    def run(self):
        if not self.conn.connect_data():
            return 1
        if not self.dry_run and not self.conn.connect_trade(ACCOUNT):
            self.conn.disconnect()
            return 1
        print('[START] CSI500 overnight v11 mode={}'.format(
            'signal' if self.dry_run else 'live'))
        try:
            while True:
                now = datetime.now()
                today = now.strftime('%Y%m%d')
                hms = now.strftime('%H:%M:%S')
                if not v10._market_open(hms):
                    _time.sleep(5)
                    continue
                self._refresh_history(today)
                tracked = set(self.codes)
                tracked.add(INDEX_CODE)
                tracked.update(self.state['positions'])
                tracked.update(self.state['watches'])
                ticks = v10._full_ticks(self.conn.xtdata, sorted(tracked))
                self._process_exits(today, hms, ticks)
                if _time.time() - self.last_scan >= SCAN_INTERVAL_SEC:
                    self._scan(today, hms, ticks)
                    self.last_scan = _time.time()
                self._process_entries(today, hms, ticks)
                _time.sleep(LOOP_INTERVAL_SEC)
        except KeyboardInterrupt:
            print('[STOP] interrupted')
            return 0
        finally:
            self.conn.disconnect()


def main():
    parser = argparse.ArgumentParser(description='CSI500 overnight v11')
    parser.add_argument(
        '--mode', default='signal', choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250903')
    parser.add_argument('--end', default='20260902')
    args = parser.parse_args()
    if args.mode == 'backtest':
        from Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v11 import run_backtest
        return run_backtest(args.start, args.end)
    if args.mode == 'live':
        print('LIVE trading: account {} / optimized risk controls'.format(ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            return 1
    return OptimizedOvernightRunner(dry_run=args.mode != 'live').run()


if __name__ == '__main__':
    raise SystemExit(main())
