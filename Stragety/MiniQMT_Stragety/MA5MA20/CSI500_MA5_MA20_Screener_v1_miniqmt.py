# -*- coding: gbk -*-
"""Read-only CSI 500 MA5/MA20 screener for MiniQMT.

The moving-average convention follows DayTradeing_v41_stragety_miniqmt.py:
the current live price is combined with the previous 4 or 19 completed daily
closes.  This script only prints matches and never connects to trading.
"""

import math
from datetime import datetime


SECTOR_NAME = '\u4e2d\u8bc1500'
SHORT_MA_DAYS = 5
LONG_MA_DAYS = 20
BELOW_MA5_RATE = 0.04
HISTORY_COUNT = 25
TICK_CHUNK_SIZE = 200


def calculate_ma_candidate(completed_closes, current_price):
    """Return MA values and match status, or None when data is insufficient."""
    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None

    closes = []
    for value in completed_closes:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            closes.append(number)

    if len(closes) < LONG_MA_DAYS - 1:
        return None

    ma5 = (sum(closes[-(SHORT_MA_DAYS - 1):]) + price) / SHORT_MA_DAYS
    ma20 = (sum(closes[-(LONG_MA_DAYS - 1):]) + price) / LONG_MA_DAYS
    gap_pct = (price / ma5 - 1.0) * 100.0
    return {
        'price': price,
        'ma5': ma5,
        'ma20': ma20,
        'gap_pct': gap_pct,
        'matched': (
            price < ma5 * (1.0 - BELOW_MA5_RATE)
            and price > ma20
        ),
    }


def _normalize_date(value):
    if hasattr(value, 'strftime'):
        return value.strftime('%Y%m%d')
    if isinstance(value, (int, float)):
        timestamp = float(value)
        compact = str(int(timestamp)) if timestamp.is_integer() else ''
        if len(compact) in (8, 14) and 1900 <= int(compact[:4]) <= 2999:
            return compact[:8]
        if timestamp > 100000000000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp).strftime('%Y%m%d')
        except (OSError, OverflowError, ValueError):
            return ''
    text = str(value).replace('-', '').replace('/', '')
    return text[:8] if len(text) >= 8 else ''


def extract_completed_closes(frame, today):
    """Extract ordered closes and remove today's still-forming daily bar."""
    if frame is None or len(frame) == 0 or 'close' not in frame.columns:
        return []

    closes = frame['close'].tolist()
    if 'time' in frame.columns:
        time_values = frame['time'].tolist()
    else:
        time_values = list(frame.index)

    dated_closes = []
    for time_value, close in zip(time_values, closes):
        trade_date = _normalize_date(time_value)
        if trade_date and trade_date != today:
            dated_closes.append((trade_date, close))
    dated_closes.sort(key=lambda item: item[0])
    return [close for _, close in dated_closes]


def _download_progress(data):
    finished = int(data.get('finished', 0) or 0)
    total = int(data.get('total', 0) or 0)
    if finished == total or finished % 50 == 0:
        print('[HISTORY] downloaded {}/{}'.format(finished, total))


def _load_ticks(xtdata, codes):
    ticks = {}
    for start in range(0, len(codes), TICK_CHUNK_SIZE):
        chunk = codes[start:start + TICK_CHUNK_SIZE]
        ticks.update(xtdata.get_full_tick(chunk) or {})
    return ticks


def main():
    from Stragety.MiniQMT_Stragety.DayT.infra.connector import MiniQMTConnector

    connector = MiniQMTConnector()
    if not connector.connect_data():
        return 1

    try:
        xtdata = connector.xtdata
        print('[START] CSI 500 MA5/MA20 screener (read-only)')

        xtdata.download_sector_data()
        codes = xtdata.get_stock_list_in_sector(SECTOR_NAME) or []
        if not codes:
            print('[ERROR] CSI 500 sector is empty')
            return 1
        print('[UNIVERSE] CSI 500: {} stocks'.format(len(codes)))

        xtdata.download_history_data2(
            codes, '1d', start_time='', end_time='',
            callback=_download_progress
        )
        history = xtdata.get_market_data_ex(
            field_list=['time', 'close'],
            stock_list=codes,
            period='1d',
            start_time='',
            end_time='',
            count=HISTORY_COUNT,
            dividend_type='front',
            fill_data=True,
        ) or {}
        ticks = _load_ticks(xtdata, codes)
        today = datetime.now().strftime('%Y%m%d')

        selected = []
        skipped = 0
        for code in codes:
            frame = history.get(code)
            tick = ticks.get(code, {})
            result = calculate_ma_candidate(
                extract_completed_closes(frame, today),
                tick.get('lastPrice', 0),
            )
            if result is None:
                skipped += 1
                continue
            if result['matched']:
                selected.append((result['gap_pct'], code, result))

        selected.sort(key=lambda item: item[0])
        print('=' * 72)
        print('CSI 500: current price < MA5 * 96% and current price > MA20')
        print('code          price       MA5        MA20       vs_MA5')
        for _, code, result in selected:
            print('%-12s %10.2f %10.2f %10.2f %9.2f%%' % (
                code, result['price'], result['ma5'], result['ma20'],
                result['gap_pct']))
        print('matched: {} | skipped(no valid quote/history): {}'.format(
            len(selected), skipped))
        print('=' * 72)
        return 0
    finally:
        connector.disconnect()


if __name__ == '__main__':
    raise SystemExit(main())
