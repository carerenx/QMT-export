# -*- coding: gbk -*-
"""MiniQMT CSI 500 MA screener v5 with missing-history download."""

import math
from datetime import datetime, timedelta

from CSI500_MA5_MA20_Screener_v1_miniqmt import (
    LONG_MA_DAYS,
    TICK_CHUNK_SIZE,
    calculate_ma_candidate,
    extract_completed_closes,
)
from CSI500_MA5_MA20_Screener_v4_miniqmt import discover_universe


def _read_history(xtdata, codes, start_date, today_key):
    return xtdata.get_local_data(
        field_list=['close'],
        stock_list=codes,
        period='1d',
        start_time=start_date,
        end_time=today_key,
        dividend_type='front',
        fill_data=True,
    ) or {}


def _completed_closes_by_code(history, codes, today_key):
    return {
        code: extract_completed_closes(history.get(code), today_key)
        for code in codes
    }


def _missing_history_codes(closes_by_code):
    required = LONG_MA_DAYS - 1
    return sorted(
        code for code, closes in closes_by_code.items()
        if len(closes) < required
    )


def _download_progress(data):
    finished = int(data.get('finished', 0) or 0)
    total = int(data.get('total', 0) or 0)
    if finished == 1 or finished == total or finished % 25 == 0:
        print('[DOWNLOAD] daily history {}/{}'.format(finished, total))


def _valid_live_price(tick):
    try:
        price = float((tick or {}).get('lastPrice', 0))
    except (TypeError, ValueError):
        return False
    return math.isfinite(price) and price > 0


def run_screen(xtdata, today=None):
    today_key = today or datetime.now().strftime('%Y%m%d')

    print('[STEP 1/4] discovering CSI 500 constituents...')
    codes, source = discover_universe(xtdata)
    if not codes:
        print('[ERROR] unable to obtain CSI 500 constituents')
        return {
            'universe_count': 0,
            'history_valid_count': 0,
            'quote_valid_count': 0,
            'selected': [],
            'skipped': 0,
        }
    print('[UNIVERSE] CSI 500: {} stocks ({})'.format(len(codes), source))

    start_date = (
        datetime.strptime(today_key, '%Y%m%d') - timedelta(days=90)
    ).strftime('%Y%m%d')
    print('[STEP 2/4] checking cached daily closes...')
    history = _read_history(xtdata, codes, start_date, today_key)
    closes_by_code = _completed_closes_by_code(history, codes, today_key)
    missing = _missing_history_codes(closes_by_code)
    print('[HISTORY] usable cache: {}/{} stocks'.format(
        len(codes) - len(missing), len(codes)))

    if missing:
        print('[STEP 3/4] downloading daily history for {} missing stocks...'.format(
            len(missing)))
        try:
            xtdata.download_history_data2(
                missing,
                '1d',
                start_time=start_date,
                end_time=today_key,
                callback=_download_progress,
            )
        except Exception as error:
            print('[DOWNLOAD] failed: {}'.format(error))
        history = _read_history(xtdata, codes, start_date, today_key)
        closes_by_code = _completed_closes_by_code(history, codes, today_key)
        missing = _missing_history_codes(closes_by_code)
        print('[HISTORY] usable after download: {}/{} stocks'.format(
            len(codes) - len(missing), len(codes)))
    else:
        print('[STEP 3/4] daily history is already complete; download skipped')

    print('[STEP 4/4] reading live prices and calculating...')
    ticks = {}
    for start in range(0, len(codes), TICK_CHUNK_SIZE):
        chunk = codes[start:start + TICK_CHUNK_SIZE]
        ticks.update(xtdata.get_full_tick(chunk) or {})
        print('[TICKS] {}/{} stocks requested'.format(
            min(start + len(chunk), len(codes)), len(codes)))

    history_valid_count = len(codes) - len(missing)
    quote_valid_count = sum(_valid_live_price(ticks.get(code)) for code in codes)
    selected = []
    skipped = 0
    for code in codes:
        result = calculate_ma_candidate(
            closes_by_code[code],
            (ticks.get(code) or {}).get('lastPrice', 0),
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
    print('matched: {} | usable history: {}/{} | valid quotes: {}/{} | skipped: {}'.format(
        len(selected), history_valid_count, len(codes),
        quote_valid_count, len(codes), skipped))
    print('=' * 72)
    return {
        'universe_count': len(codes),
        'history_valid_count': history_valid_count,
        'quote_valid_count': quote_valid_count,
        'selected': selected,
        'skipped': skipped,
    }


def main():
    from infra.connector import MiniQMTConnector

    connector = MiniQMTConnector()
    if not connector.connect_data():
        return 1
    try:
        print('[START] CSI 500 MA5/MA20 screener v5 (history auto-fill, read-only)')
        result = run_screen(connector.xtdata)
        return 0 if result['universe_count'] else 1
    finally:
        connector.disconnect()


if __name__ == '__main__':
    raise SystemExit(main())

