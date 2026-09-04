# -*- coding: gbk -*-
"""V12 execution with fixed completed MAs and ATR-normalized selection."""

import argparse
import json
import math
import os
import time as _time
from datetime import datetime, timedelta

import CSI500_MA5_MA20_Overnight_v10_miniqmt as v10
import CSI500_MA5_MA20_Overnight_v11_miniqmt as v11
import CSI500_MA5_MA20_Overnight_v12_miniqmt as v12


ACCOUNT = v12.ACCOUNT
ATR_PERIOD = 14
MA20_SLOPE_LOOKBACK = 5
MIN_PULLBACK_ATR = 0.5
MIN_ATR_PERCENT = 7.0
FIXED_MA5_BELOW_PERCENT = 3.0
STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'CSI500_MA5_MA20_Overnight_v15_state.json')


def calculate_atr(rows, period=ATR_PERIOD):
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
    return sum(true_ranges) / float(period)


def calculate_optimized_candidate(completed_rows, current_price,
                                  variant='optimized'):
    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0 or len(completed_rows) < 25:
        return None
    try:
        rows = [(float(row[0]), float(row[1]), float(row[2]))
                for row in completed_rows]
    except (TypeError, ValueError, IndexError):
        return None
    if not all(
            math.isfinite(value) and value > 0
            for row in rows for value in row):
        return None

    closes = [row[2] for row in rows]
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    ma20_previous = sum(
        closes[-(20 + MA20_SLOPE_LOOKBACK):-MA20_SLOPE_LOOKBACK]) / 20.0
    atr14 = calculate_atr(rows)
    if atr14 is None or atr14 <= 0:
        return None
    pullback_atr = (ma5 - price) / atr14
    atr_percent = atr14 / closes[-1] * 100.0
    gap_pct = (price / ma5 - 1.0) * 100.0
    ma20_rising = ma20 > ma20_previous
    above_ma20 = price > ma20

    if variant == 'fixed':
        matched = above_ma20 and gap_pct < -FIXED_MA5_BELOW_PERCENT
    elif variant == 'fixed_slope':
        matched = (
            above_ma20 and ma20_rising and
            gap_pct < -FIXED_MA5_BELOW_PERCENT)
    elif variant == 'atr_normalized':
        matched = (
            above_ma20 and ma20_rising and
            pullback_atr >= MIN_PULLBACK_ATR)
    elif variant == 'optimized':
        matched = (
            above_ma20 and ma20_rising and
            gap_pct < -FIXED_MA5_BELOW_PERCENT and
            atr_percent >= MIN_ATR_PERCENT)
    else:
        raise ValueError('unknown selection variant: {}'.format(variant))
    return {
        'price': price,
        'ma5': ma5,
        'ma20': ma20,
        'ma20_previous': ma20_previous,
        'ma20_rising': ma20_rising,
        'atr14': atr14,
        'atr_percent': atr_percent,
        'pullback_atr': pullback_atr,
        'gap_pct': gap_pct,
        'matched': bool(matched),
    }


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
        rows = []
        frame = data.get(code)
        if frame is not None:
            for index, row in frame.iterrows():
                date = _date_key(index)
                try:
                    high = float(row['high'])
                    low = float(row['low'])
                    close = float(row['close'])
                except (TypeError, ValueError, KeyError):
                    continue
                if (date and date < today and high > 0 and low > 0 and close > 0 and
                        math.isfinite(high) and math.isfinite(low) and
                        math.isfinite(close)):
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


class OptimizedSelectionRunner(v12.NoStopOvernightRunner):
    def __init__(self, dry_run=True):
        super(OptimizedSelectionRunner, self).__init__(dry_run=dry_run)
        self.state = _load_state() if not dry_run else {
            'watches': {}, 'positions': {}, 'entry_date': '', 'entry_count': 0}
        self.ohlc_by_code = {}

    def _persist(self):
        if not self.dry_run:
            _save_state(self.state)

    def _refresh_history(self, today):
        if self.history_date == today:
            return
        super(OptimizedSelectionRunner, self)._refresh_history(today)
        start = (datetime.strptime(today, '%Y%m%d') - timedelta(days=180)).strftime(
            '%Y%m%d')
        self.ohlc_by_code = _daily_ohlc_by_code(
            self.conn.xtdata, self.codes, start, today)

    def _scan(self, today, hms, ticks):
        index_price = float(ticks.get(v11.INDEX_CODE, {}).get('lastPrice', 0) or 0)
        market_ok = v11.is_market_allowed(self.index_closes, index_price)
        candidates = []
        baseline_matches = 0
        for code in self.codes:
            price = float(ticks.get(code, {}).get('lastPrice', 0) or 0)
            if price <= 0:
                continue
            baseline = v11.calculate_ma_candidate(
                self.closes_by_code.get(code, []), price,
                below_ma5_percent=v11.MA5_BELOW_PERCENT)
            if baseline and baseline['matched']:
                baseline_matches += 1
            result = calculate_optimized_candidate(
                self.ohlc_by_code.get(code, []), price)
            if not result or not result['matched']:
                continue
            if code in self.state['positions'] or code in self.state['watches']:
                continue
            candidates.append((-result['pullback_atr'], code, result))

        self.entry_allowed = (
            market_ok and baseline_matches <= v11.MAX_REALTIME_MATCHES)
        if not self.entry_allowed:
            print('[FILTER] market_ok={} baseline_matches={}/{}'.format(
                market_ok, baseline_matches, v11.MAX_REALTIME_MATCHES))
            return
        candidates.sort()
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
                'atr14': result['atr14'],
                'atr_percent': result['atr_percent'],
                'pullback_atr': result['pullback_atr'],
            })
            self.state['watches'][code] = watch
            print(
                '[SELECTED] {} {} price={:.2f} MA5={:.2f} MA20={:.2f} '
                'ATR14={:.2f} ({:.2f}%) pullback={:.2f}ATR'.format(
                    code, watch['name'], price, result['ma5'], result['ma20'],
                    result['atr14'], result['atr_percent'],
                    result['pullback_atr']))
        if candidates:
            self._persist()

    def run(self):
        if not self.conn.connect_data():
            return 1
        if not self.dry_run and not self.conn.connect_trade(ACCOUNT):
            self.conn.disconnect()
            return 1
        print('[START] CSI500 overnight v15 optimized-selection mode={}'.format(
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
    parser = argparse.ArgumentParser(
        description='CSI500 overnight v15 optimized selection')
    parser.add_argument(
        '--mode', default='signal', choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250903')
    parser.add_argument('--end', default='20260902')
    args = parser.parse_args()
    if args.mode == 'backtest':
        from backtest_csi500_overnight_v15 import run_backtest
        return run_backtest(args.start, args.end)
    if args.mode == 'live':
        print(
            'LIVE trading: account {} / completed MA + rising MA20 + '
            'fixed pullback >= {:.1f}% + rising MA20 + ATR >= {:.1f}%'.format(
                ACCOUNT, FIXED_MA5_BELOW_PERCENT, MIN_ATR_PERCENT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            return 1
    return OptimizedSelectionRunner(dry_run=args.mode != 'live').run()


if __name__ == '__main__':
    raise SystemExit(main())
