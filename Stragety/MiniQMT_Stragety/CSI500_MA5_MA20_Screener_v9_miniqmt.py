# -*- coding: gbk -*-
"""MiniQMT CSI 500 MA screener v9 with MA distance columns."""

from datetime import datetime, timedelta

from CSI500_MA5_MA20_Screener_v1_miniqmt import TICK_CHUNK_SIZE
from CSI500_MA5_MA20_Screener_v4_miniqmt import discover_universe
from CSI500_MA5_MA20_Screener_v5_miniqmt import (
    _download_progress,
    _read_history,
    _valid_live_price,
)
from CSI500_MA5_MA20_Screener_v6_miniqmt import (
    _missing_history_codes,
    _valid_completed_closes_by_code,
)
from CSI500_MA5_MA20_Screener_v7_miniqmt import calculate_ma_candidate
from CSI500_MA5_MA20_Screener_v8_miniqmt import (
    NAME_COLUMN_WIDTH,
    _pad_display,
    load_selected_names,
)


# Selection parameter: 3.0 means the price must be at least 3% below MA5.
MA5_BELOW_PERCENT = 3.0
RESULT_HEADER = (
    'code         name              price        MA5    vs_MA5'
    '       MA20   vs_MA20'
)


def format_result_line(code, name, result):
    vs_ma20_pct = (result['price'] / result['ma20'] - 1.0) * 100.0
    return '%-12s %s %10.2f %10.2f %9.2f%% %10.2f %+9.2f%%' % (
        code,
        _pad_display(name, NAME_COLUMN_WIDTH),
        result['price'],
        result['ma5'],
        result['gap_pct'],
        result['ma20'],
        vs_ma20_pct,
    )


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
    print('[STEP 2/4] checking valid cached daily closes...')
    history = _read_history(xtdata, codes, start_date, today_key)
    closes_by_code = _valid_completed_closes_by_code(history, codes, today_key)
    missing = _missing_history_codes(closes_by_code)
    print('[HISTORY] valid cache: {}/{} stocks'.format(
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
        closes_by_code = _valid_completed_closes_by_code(
            history, codes, today_key)
        missing = _missing_history_codes(closes_by_code)
        print('[HISTORY] valid after download: {}/{} stocks'.format(
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
            below_ma5_percent=MA5_BELOW_PERCENT,
        )
        if result is None:
            skipped += 1
            continue
        if result['matched']:
            selected.append((result['gap_pct'], code, result))

    selected.sort(key=lambda item: item[0])
    names = load_selected_names(xtdata, selected)

    print('=' * 103)
    print('CSI 500: current price < MA5 by {:.2f}% and current price > MA20'.format(
        MA5_BELOW_PERCENT))
    print(RESULT_HEADER)
    for _, code, result in selected:
        print(format_result_line(code, names[code], result))
    print('matched: {} | valid history: {}/{} | valid quotes: {}/{} | skipped: {}'.format(
        len(selected), history_valid_count, len(codes),
        quote_valid_count, len(codes), skipped))
    print('=' * 103)
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
        print('[START] CSI 500 MA5/MA20 screener v9 (MA distance columns)')
        result = run_screen(connector.xtdata)
        return 0 if result['universe_count'] else 1
    finally:
        connector.disconnect()


if __name__ == '__main__':
    raise SystemExit(main())

