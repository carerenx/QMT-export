# -*- coding: gbk -*-
"""MiniQMT CSI 500 MA screener v4 with an offline universe fallback."""

import csv
from datetime import datetime, timedelta
from pathlib import Path

from CSI500_MA5_MA20_Screener_v1_miniqmt import (
    SECTOR_NAME,
    TICK_CHUNK_SIZE,
    calculate_ma_candidate,
    extract_completed_closes,
)


INDEX_CODE = '000905.SH'
BUNDLED_SNAPSHOT_DATE = '2026-08-31'
BUNDLED_UNIVERSE_FILE = Path(__file__).resolve().with_name(
    'csi500_constituents_20260831.csv')


def _cached_sector_codes(xtdata):
    try:
        return sorted(set(xtdata.get_stock_list_in_sector(SECTOR_NAME) or []))
    except Exception as error:
        print('[UNIVERSE] cached sector lookup failed: {}'.format(error))
        return []


def _cached_index_weight_codes(xtdata):
    try:
        weights = xtdata.get_index_weight(INDEX_CODE) or {}
        return sorted(set(weights.keys()))
    except Exception as error:
        print('[UNIVERSE] cached index-weight lookup failed: {}'.format(error))
        return []


def _to_qmt_code(code):
    if len(code) != 6 or not code.isdigit():
        raise ValueError('invalid stock code: {}'.format(code))
    market = 'SH' if code.startswith('6') else 'SZ'
    return '{}.{}'.format(code, market)


def load_bundled_constituents(path=None):
    snapshot_path = Path(path) if path else BUNDLED_UNIVERSE_FILE
    try:
        with snapshot_path.open('r', encoding='utf-8-sig', newline='') as file:
            codes = [_to_qmt_code(row['code'].strip()) for row in csv.DictReader(file)]
    except (OSError, KeyError, ValueError) as error:
        print('[UNIVERSE] bundled snapshot unavailable: {}'.format(error))
        return []

    codes = sorted(set(codes))
    if len(codes) != 500:
        print('[UNIVERSE] bundled snapshot invalid: expected 500, got {}'.format(
            len(codes)))
        return []
    return codes


def discover_universe(xtdata):
    """Find CSI 500 codes without any blocking download call."""
    codes = _cached_sector_codes(xtdata)
    if codes:
        return codes, 'cached sector'

    print('[UNIVERSE] cached sector empty; trying 000905.SH index weights...')
    codes = _cached_index_weight_codes(xtdata)
    if codes:
        return codes, 'cached index weights'

    print('[UNIVERSE] local sources empty; loading bundled snapshot...')
    codes = load_bundled_constituents()
    if codes:
        return codes, 'bundled snapshot {}'.format(BUNDLED_SNAPSHOT_DATE)
    return [], 'unavailable'


def run_screen(xtdata, today=None):
    today_key = today or datetime.now().strftime('%Y%m%d')

    print('[STEP 1/3] discovering CSI 500 constituents...')
    codes, source = discover_universe(xtdata)
    if not codes:
        print('[ERROR] unable to obtain CSI 500 constituents')
        print('[ACTION] restore {}'.format(BUNDLED_UNIVERSE_FILE.name))
        return {'universe_count': 0, 'selected': [], 'skipped': 0}
    print('[UNIVERSE] CSI 500: {} stocks ({})'.format(len(codes), source))

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
        print('[START] CSI 500 MA5/MA20 screener v4 (offline universe, read-only)')
        result = run_screen(connector.xtdata)
        return 0 if result['universe_count'] else 1
    finally:
        connector.disconnect()


if __name__ == '__main__':
    raise SystemExit(main())

