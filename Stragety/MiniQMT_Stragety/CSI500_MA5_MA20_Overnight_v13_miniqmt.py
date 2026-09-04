# -*- coding: gbk -*-
"""V12 overnight strategy with an ATR14 percentage filter."""

import argparse
import json
import math
import os
import time as _time
from datetime import datetime

import CSI500_MA5_MA20_Overnight_v10_miniqmt as v10
import CSI500_MA5_MA20_Overnight_v11_miniqmt as v11
import CSI500_MA5_MA20_Overnight_v12_miniqmt as v12


ACCOUNT = v11.ACCOUNT
ATR_PERIOD = 14
MIN_ATR_PERCENT = 7.0
STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'CSI500_MA5_MA20_Overnight_v13_state.json')


def calculate_atr_percent(rows, period=ATR_PERIOD):
    if len(rows) < period + 1:
        return None
    values = rows[-(period + 1):]
    true_ranges = []
    for index in range(1, len(values)):
        previous_close = float(values[index - 1][2])
        high = float(values[index][0])
        low = float(values[index][1])
        true_ranges.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)))
    last_close = float(values[-1][2])
    if last_close <= 0:
        return None
    return sum(true_ranges) / float(period) / last_close * 100.0


def _date_key(value):
    if hasattr(value, 'strftime'):
        return value.strftime('%Y%m%d')
    text = str(value).replace('-', '').replace('/', '')
    if len(text) >= 8 and text[:8].isdigit():
        year = int(text[:4])
        if 1990 <= year <= 2100:
            return text[:8]
    try:
        stamp = float(value)
        if stamp > 100000000000:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp).strftime('%Y%m%d')
    except (TypeError, ValueError, OSError, OverflowError):
        return ''


def _daily_ohlc_by_code(xtdata, codes, start, today):
    data = xtdata.get_local_data(
        field_list=['high', 'low', 'close'],
        stock_list=codes,
        period='1d',
        start_time=start,
        end_time=today,
        dividend_type='front',
        fill_data=True,
    ) or {}
    result = {}
    for code in codes:
        frame = data.get(code)
        rows = []
        if frame is None:
            result[code] = rows
            continue
        for index, row in frame.iterrows():
            date = _date_key(index)
            try:
                high = float(row['high'])
                low = float(row['low'])
                close = float(row['close'])
            except Exception:
                continue
            if (date and date < today and high > 0 and low > 0 and close > 0 and
                    math.isfinite(high) and math.isfinite(low) and math.isfinite(close)):
                rows.append((date, high, low, close))
        rows.sort(key=lambda item: item[0])
        result[code] = [(row[1], row[2], row[3]) for row in rows]
    return result


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


class AtrFilteredOvernightRunner(v12.NoStopOvernightRunner):
    def __init__(self, dry_run=True):
        super(AtrFilteredOvernightRunner, self).__init__(dry_run=dry_run)
        self.state = _load_state() if not dry_run else {
            'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}
        self.atr_by_code = {}

    def _persist(self):
        if not self.dry_run:
            _save_state(self.state)

    def _refresh_history(self, today):
        if self.history_date == today:
            return
        super(AtrFilteredOvernightRunner, self)._refresh_history(today)
        start = '{}0101'.format(int(today[:4]) - 1)
        rows_by_code = _daily_ohlc_by_code(
            self.conn.xtdata, self.codes, start, today)
        self.atr_by_code = {
            code: calculate_atr_percent(rows)
            for code, rows in rows_by_code.items()
        }

    def _scan(self, today, hms, ticks):
        index_price = float(ticks.get(v11.INDEX_CODE, {}).get('lastPrice', 0) or 0)
        market_ok = v11.is_market_allowed(self.index_closes, index_price)
        candidates = []
        realtime_matches = 0
        for code in self.codes:
            price = float(ticks.get(code, {}).get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            result = v11.calculate_ma_candidate(
                self.closes_by_code.get(code, []), price,
                below_ma5_percent=v11.MA5_BELOW_PERCENT)
            if result and result['matched']:
                realtime_matches += 1
                atr_percent = self.atr_by_code.get(code)
                if atr_percent is None or atr_percent < MIN_ATR_PERCENT:
                    continue
                if code in self.state['positions'] or code in self.state['watches']:
                    continue
                candidates.append((result['gap_pct'], code, result))

        self.entry_allowed = (
            market_ok and realtime_matches <= v11.MAX_REALTIME_MATCHES)
        if not self.entry_allowed:
            print('[FILTER] market_ok={} MA matches={}/{}'.format(
                market_ok, realtime_matches, v11.MAX_REALTIME_MATCHES))
            return
        names = v11.load_selected_names(self.conn.xtdata, candidates)
        for _, code, result in candidates:
            tick = ticks[code]
            price = result['price']
            open_price = float(tick.get('open', 0) or price)
            watch = v11.new_buy_watch(open_price, price, hms)
            watch.update({
                'screen_date': today,
                'screen_price': price,
                'name': names.get(code, '-'),
                'atr_percent': self.atr_by_code[code],
            })
            self.state['watches'][code] = watch
            print('[SELECTED] {} {} price={:.2f} ATR14={:.2f}%'.format(
                code, watch['name'], price, watch['atr_percent']))
        if candidates:
            self._persist()

    def run(self):
        if not self.conn.connect_data():
            return 1
        if not self.dry_run and not self.conn.connect_trade(ACCOUNT):
            self.conn.disconnect()
            return 1
        print('[START] CSI500 overnight v13 ATR>=7% mode={}'.format(
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
    parser = argparse.ArgumentParser(description='CSI500 overnight v13 ATR filter')
    parser.add_argument(
        '--mode', default='signal', choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250903')
    parser.add_argument('--end', default='20260902')
    args = parser.parse_args()
    if args.mode == 'backtest':
        from backtest_csi500_overnight_v13 import run_backtest
        return run_backtest(args.start, args.end)
    if args.mode == 'live':
        print('LIVE trading: account {} / ATR14 >= {:.1f}%'.format(
            ACCOUNT, MIN_ATR_PERCENT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            return 1
    return AtrFilteredOvernightRunner(dry_run=args.mode != 'live').run()


if __name__ == '__main__':
    raise SystemExit(main())
