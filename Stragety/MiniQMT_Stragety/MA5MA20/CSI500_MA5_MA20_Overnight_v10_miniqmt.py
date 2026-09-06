# -*- coding: gbk -*-
"""CSI 500 MA screen + v40 FWD-T entry + next-day exit for MiniQMT."""

import argparse
import json
import math
import os
import time as _time
from datetime import datetime, timedelta

from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v1_miniqmt import TICK_CHUNK_SIZE
from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v4_miniqmt import discover_universe
from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v5_miniqmt import (
    _download_progress,
    _read_history,
)
from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v6_miniqmt import (
    _missing_history_codes,
    _valid_completed_closes_by_code,
)
from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v7_miniqmt import calculate_ma_candidate
from Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Screener_v8_miniqmt import load_selected_names


ACCOUNT = '8890145315'
TRADE_LOT_SIZE = 100
MA5_BELOW_PERCENT = 3.0

# DayTradeing_v40 main FWD-T parameters.
BUY_TRIGGER_PCT = 0.030
BUY_TRIGGER_TRAIL = 0.020
BUY_BOUNCE_PCT = 0.0010
SELLBACK_RISE_PCT = 0.012
SELL_PULLBACK_PCT = 0.0010
STOP_LOSS_PCT = 0.015
FORCE_SELL_TIME = '10:00:00'

SCAN_INTERVAL_SEC = 60
LOOP_INTERVAL_SEC = 1
FILL_TIMEOUT_SEC = 8
STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'CSI500_MA5_MA20_Overnight_v10_state.json')


def is_screen_match(completed_closes, current_price):
    result = calculate_ma_candidate(
        completed_closes,
        current_price,
        below_ma5_percent=MA5_BELOW_PERCENT,
    )
    return bool(result and result['matched'])


def new_buy_watch(open_price, current_price):
    floor = float(open_price) * (1.0 - BUY_TRIGGER_PCT)
    trail = float(current_price) * (1.0 - BUY_TRIGGER_TRAIL)
    return {
        'phase': 'WAITING',
        'buy_trigger_floor': floor,
        'max_trail': trail,
        'dip_price': 0.0,
    }


def advance_buy_watch(state, price):
    price = float(price)
    if state['phase'] == 'WAITING':
        trail = price * (1.0 - BUY_TRIGGER_TRAIL)
        state['max_trail'] = max(float(state.get('max_trail', 0.0)), trail)
        trigger = max(state['buy_trigger_floor'], state['max_trail'])
        if price <= trigger:
            state['phase'] = 'DIPPING'
            state['dip_price'] = price
        return None

    if state['phase'] == 'DIPPING':
        state['dip_price'] = min(float(state.get('dip_price', price)), price)
        dip = state['dip_price']
        if dip > 0 and (price - dip) / dip >= BUY_BOUNCE_PCT:
            state['phase'] = 'BUY_READY'
            return 'BUY'
    return None


def new_sell_watch(buy_date, buy_price):
    return {
        'phase': 'BOUGHT',
        'buy_date': str(buy_date),
        'buy_price': float(buy_price),
        'sell_peak_price': 0.0,
    }


def advance_sell_watch(state, trade_date, time_text, price):
    if str(trade_date) <= str(state['buy_date']):
        return None
    price = float(price)
    if time_text >= FORCE_SELL_TIME:
        return 'FORCE_1000'

    buy_price = float(state['buy_price'])
    if price <= buy_price * (1.0 - STOP_LOSS_PCT):
        return 'STOP_LOSS'

    if state['phase'] == 'BOUGHT':
        if price >= buy_price * (1.0 + SELLBACK_RISE_PCT):
            state['phase'] = 'SPIKING'
            state['sell_peak_price'] = price
        return None

    if state['phase'] == 'SPIKING':
        state['sell_peak_price'] = max(
            float(state.get('sell_peak_price', price)), price)
        peak = state['sell_peak_price']
        if peak > 0 and (peak - price) / peak >= SELL_PULLBACK_PCT:
            return 'SELLBACK'
    return None


def _market_open(hms):
    return ('09:30:00' <= hms <= '11:30:00' or
            '13:00:00' <= hms <= '15:00:00')


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {'watches': {}, 'positions': {}}
    try:
        with open(STATE_FILE, 'r') as file:
            data = json.load(file)
        data.setdefault('watches', {})
        data.setdefault('positions', {})
        return data
    except Exception as error:
        print('[STATE] load failed: {}'.format(error))
        return {'watches': {}, 'positions': {}}


def _save_state(state):
    temp_path = STATE_FILE + '.tmp'
    with open(temp_path, 'w') as file:
        json.dump(state, file, ensure_ascii=True, indent=2, sort_keys=True)
    os.replace(temp_path, STATE_FILE)


def _position_map(conn):
    result = {}
    for position in conn.query_positions():
        code = getattr(position, 'stock_code', '')
        if not code:
            continue
        result[code] = {
            'volume': int(getattr(position, 'volume', 0) or 0),
            'can_use': int(getattr(position, 'can_use_volume', 0) or 0),
        }
    return result


def _available_cash(conn):
    account = conn.query_account()
    return float(getattr(account, 'cash', 0.0) or 0.0) if account else 0.0


def _full_ticks(xtdata, codes):
    ticks = {}
    for start in range(0, len(codes), TICK_CHUNK_SIZE):
        chunk = codes[start:start + TICK_CHUNK_SIZE]
        ticks.update(xtdata.get_full_tick(chunk) or {})
    return ticks


class OvernightRunner(object):
    def __init__(self, dry_run=True):
        from Stragety.MiniQMT_Stragety.DayT.infra.connector import MiniQMTConnector
        self.conn = MiniQMTConnector()
        self.dry_run = dry_run
        self.state = _load_state() if not dry_run else {
            'watches': {}, 'positions': {}}
        self.codes = []
        self.closes_by_code = {}
        self.history_date = ''
        self.last_scan = 0.0

    def _persist(self):
        if not self.dry_run:
            _save_state(self.state)

    def _refresh_history(self, today):
        if self.history_date == today:
            return
        if not self.codes:
            self.codes, source = discover_universe(self.conn.xtdata)
            print('[UNIVERSE] {} stocks ({})'.format(len(self.codes), source))
        start = (datetime.strptime(today, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')
        history = _read_history(self.conn.xtdata, self.codes, start, today)
        closes = _valid_completed_closes_by_code(history, self.codes, today)
        missing = _missing_history_codes(closes)
        if missing:
            print('[HISTORY] downloading {} missing stocks'.format(len(missing)))
            self.conn.xtdata.download_history_data2(
                missing, '1d', start_time=start, end_time=today,
                callback=_download_progress)
            history = _read_history(self.conn.xtdata, self.codes, start, today)
            closes = _valid_completed_closes_by_code(history, self.codes, today)
        self.closes_by_code = closes
        self.history_date = today

    def _submit(self, code, shares, price):
        if self.dry_run:
            print('[SIGNAL] {} {} x {} @ {:.2f}'.format(
                'BUY' if shares > 0 else 'SELL', code, abs(shares), price))
            return abs(shares)

        positions = _position_map(self.conn)
        before = positions.get(code, {}).get('volume', 0)
        if shares > 0 and _available_cash(self.conn) < price * shares * 1.001:
            print('[ORDER] BUY {} skipped: insufficient cash'.format(code))
            return 0
        if shares < 0:
            sellable = positions.get(code, {}).get('can_use', 0)
            shares = -min(abs(shares), sellable)
            if shares == 0:
                print('[ORDER] SELL {} skipped: no sellable shares'.format(code))
                return 0

        order_id = self.conn.order_stock(code, shares, 'COMPETE', price)
        if order_id is None:
            return 0
        expected = abs(shares)
        deadline = _time.time() + FILL_TIMEOUT_SEC
        filled = 0
        while _time.time() < deadline:
            _time.sleep(0.5)
            after = _position_map(self.conn).get(code, {}).get('volume', 0)
            delta = after - before
            filled = max(0, delta) if shares > 0 else max(0, -delta)
            if filled >= expected:
                return filled
        self.conn.cancel_order(order_id)
        return filled

    def _process_exits(self, today, hms, ticks):
        changed = False
        for code, position in list(self.state['positions'].items()):
            tick = ticks.get(code, {})
            price = float(tick.get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            event = advance_sell_watch(position, today, hms, price)
            if event:
                sold = self._submit(code, -int(position['shares']), price)
                if sold >= int(position['shares']):
                    pnl = (price - position['buy_price']) * position['shares']
                    print('[EXIT-{}] {} {} @ {:.2f} gross={:.2f}'.format(
                        event, code, position.get('name', '-'), price, pnl))
                    del self.state['positions'][code]
                    changed = True
        if changed:
            self._persist()

    def _scan(self, today, ticks):
        candidates = []
        for code in self.codes:
            if code in self.state['positions'] or code in self.state['watches']:
                continue
            tick = ticks.get(code, {})
            price = float(tick.get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            result = calculate_ma_candidate(
                self.closes_by_code.get(code, []), price,
                below_ma5_percent=MA5_BELOW_PERCENT)
            if result and result['matched']:
                candidates.append((result['gap_pct'], code, result))
        names = load_selected_names(self.conn.xtdata, candidates)
        for _, code, result in candidates:
            tick = ticks[code]
            price = result['price']
            open_price = float(tick.get('open', 0) or price)
            watch = new_buy_watch(open_price, price)
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

    def _process_entries(self, today, ticks):
        changed = False
        for code, watch in list(self.state['watches'].items()):
            if watch.get('screen_date') != today:
                del self.state['watches'][code]
                changed = True
                continue
            price = float(ticks.get(code, {}).get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            if advance_buy_watch(watch, price) == 'BUY':
                bought = self._submit(code, TRADE_LOT_SIZE, price)
                if bought > 0:
                    position = new_sell_watch(today, price)
                    position.update({
                        'shares': bought,
                        'name': watch.get('name', '-'),
                        'buy_time': datetime.now().strftime('%H:%M:%S'),
                    })
                    self.state['positions'][code] = position
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
        print('[START] CSI500 overnight v10 mode={}'.format(
            'signal' if self.dry_run else 'live'))
        try:
            while True:
                now = datetime.now()
                today = now.strftime('%Y%m%d')
                hms = now.strftime('%H:%M:%S')
                if not _market_open(hms):
                    _time.sleep(5)
                    continue
                self._refresh_history(today)
                tracked = set(self.codes)
                tracked.update(self.state['positions'])
                tracked.update(self.state['watches'])
                ticks = _full_ticks(self.conn.xtdata, sorted(tracked))
                self._process_exits(today, hms, ticks)
                if _time.time() - self.last_scan >= SCAN_INTERVAL_SEC:
                    self._scan(today, ticks)
                    self.last_scan = _time.time()
                self._process_entries(today, ticks)
                _time.sleep(LOOP_INTERVAL_SEC)
        except KeyboardInterrupt:
            print('[STOP] interrupted')
            return 0
        finally:
            self.conn.disconnect()


def main():
    parser = argparse.ArgumentParser(description='CSI500 overnight v10')
    parser.add_argument(
        '--mode', default='signal', choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250903')
    parser.add_argument('--end', default='20260902')
    args = parser.parse_args()
    if args.mode == 'backtest':
        from Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v10 import run_backtest
        return run_backtest(args.start, args.end)
    if args.mode == 'live':
        print('LIVE trading: account {} / one lot per selected stock'.format(ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            return 1
    return OvernightRunner(dry_run=args.mode != 'live').run()


if __name__ == '__main__':
    raise SystemExit(main())

