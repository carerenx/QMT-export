# -*- coding: gbk -*-
"""V11 filters with the next-day 1.5 percent stop-loss removed."""

import argparse
import json
import os
import time as _time
from datetime import datetime

import Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Overnight_v10_miniqmt as v10
import Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Overnight_v11_miniqmt as v11


ACCOUNT = v11.ACCOUNT
STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'CSI500_MA5_MA20_Overnight_v12_state.json')


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
    if time_text >= v11.FORCE_SELL_TIME:
        return 'FORCE_1000'
    buy_price = float(state['buy_price'])
    if state['phase'] == 'BOUGHT':
        if price >= buy_price * (1.0 + v11.SELLBACK_RISE_PCT):
            state['phase'] = 'SPIKING'
            state['sell_peak_price'] = price
        return None
    if state['phase'] == 'SPIKING':
        state['sell_peak_price'] = max(
            float(state.get('sell_peak_price', price)), price)
        peak = state['sell_peak_price']
        if peak > 0 and (peak - price) / peak >= v11.SELL_PULLBACK_PCT:
            return 'SELLBACK'
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


class NoStopOvernightRunner(v11.OptimizedOvernightRunner):
    def __init__(self, dry_run=True):
        super(NoStopOvernightRunner, self).__init__(dry_run=dry_run)
        self.state = _load_state() if not dry_run else {
            'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}

    def _persist(self):
        if not self.dry_run:
            _save_state(self.state)

    def _process_exits(self, today, hms, ticks):
        changed = False
        for code, position in list(self.state['positions'].items()):
            price = float(ticks.get(code, {}).get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            event = advance_sell_watch(position, today, hms, price)
            if not event:
                continue
            sold = self._submit(code, -int(position['shares']), price)
            if sold >= int(position['shares']):
                pnl = (price - position['buy_price']) * position['shares']
                print('[EXIT-{}] {} {} @ {:.2f} gross={:.2f}'.format(
                    event, code, position.get('name', '-'), price, pnl))
                del self.state['positions'][code]
                changed = True
        if changed:
            self._persist()

    def run(self):
        if not self.conn.connect_data():
            return 1
        if not self.dry_run and not self.conn.connect_trade(ACCOUNT):
            self.conn.disconnect()
            return 1
        print('[START] CSI500 overnight v12 no-stop mode={}'.format(
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
                tracked.add(v11.INDEX_CODE)
                tracked.update(self.state['positions'])
                tracked.update(self.state['watches'])
                ticks = v10._full_ticks(self.conn.xtdata, sorted(tracked))
                self._process_exits(today, hms, ticks)
                if _time.time() - self.last_scan >= v11.SCAN_INTERVAL_SEC:
                    self._scan(today, hms, ticks)
                    self.last_scan = _time.time()
                self._process_entries(today, hms, ticks)
                _time.sleep(v11.LOOP_INTERVAL_SEC)
        except KeyboardInterrupt:
            print('[STOP] interrupted')
            return 0
        finally:
            self.conn.disconnect()


def main():
    parser = argparse.ArgumentParser(description='CSI500 overnight v12 no-stop')
    parser.add_argument(
        '--mode', default='signal', choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250903')
    parser.add_argument('--end', default='20260902')
    args = parser.parse_args()
    if args.mode == 'backtest':
        from Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v12 import run_backtest
        return run_backtest(args.start, args.end)
    if args.mode == 'live':
        print('LIVE trading: account {} / no 1.5% stop-loss'.format(ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            return 1
    return NoStopOvernightRunner(dry_run=args.mode != 'live').run()


if __name__ == '__main__':
    raise SystemExit(main())
