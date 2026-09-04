# -*- coding: gbk -*-
"""MiniQMT CSI 500 MA screener v2: local-cache, non-blocking startup.

v1 called synchronous sector and history download APIs before screening.  On
some MiniQMT installations those native calls can wait indefinitely.  v2 only
reads the sector list and daily bars already present in MiniQMT's local cache.
It never connects to trading and never submits orders.
"""

from datetime import datetime, timedelta

from CSI500_MA5_MA20_Screener_v1_miniqmt import (
    HISTORY_COUNT,
    SECTOR_NAME,
    TICK_CHUNK_SIZE,
    calculate_ma_candidate,
    extract_completed_closes,
)


def run_screen(xtdata, today=None):
    """Run one read-only screen using MiniQMT's existing local data."""
    today_key = today or datetime.now().strftime('%Y%m%d')

    print('[STEP 1/3] reading cached CSI 500 constituents...')
    codes = xtdata.get_stock_list_in_sector(SECTOR_NAME) or []
    if not codes:
        print('[ERROR] cached CSI 500 sector is empty')
        print('[ACTION] update sector data in the QMT client, then run again')
        return {'universe_count': 0, 'selected': [], 'skipped': 0}
    print('[UNIVERSE] CSI 500: {} stocks'.format(len(codes)))

    start_date = (
        datetime.strptime(today_key, '%Y%m%d') - timedelta(days=90)
    ).strftime('%Y%m%d')
    print('[STEP 2/3] reading cached daily closes...')
    history = xtdata.get_local_data(
        field_list=['close'],
        stock_list=codes,
        period='1d',
        start_time=start_date,
        end_time=today_key,
        dividend_type='front',
        fill_data=True,
    ) or {}
    print('[HISTORY] local data returned for {}/{} stocks'.format(
        len(history), len(codes)))

    print('[STEP 3/3] reading live prices and calculating...')
    ticks = {}
    for start in range(0, len(codes), TICK_CHUNK_SIZE):
        chunk = codes[start:start + TICK_CHUNK_SIZE]
        ticks.update(xtdata.get_full_tick(chunk) or {})
        print('[TICKS] {}/{} stocks requested'.format(
            min(start + len(chunk), len(codes)), len(codes)))
    selected = []
    skipped = 0
    for code in codes:
        result = calculate_ma_candidate(
            extract_completed_closes(history.get(code), today_key),
            ticks.get(code, {}).get('lastPrice', 0),
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
    return {
        'universe_count': len(codes),
        'selected': selected,
        'skipped': skipped,
    }


def main():
    from infra.connector import MiniQMTConnector

    connector = MiniQMTConnector()
    if not connector.connect_data():
        return 1

    try:
        print('[START] CSI 500 MA5/MA20 screener v2 (local cache, read-only)')
        result = run_screen(connector.xtdata)
        return 0 if result['universe_count'] else 1
    finally:
        connector.disconnect()


if __name__ == '__main__':
    raise SystemExit(main())
