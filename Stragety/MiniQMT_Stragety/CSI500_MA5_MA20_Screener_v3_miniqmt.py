# -*- coding: gbk -*-
"""MiniQMT CSI 500 MA screener v3 with bounded universe discovery."""

import subprocess
import sys
from datetime import datetime, timedelta

from CSI500_MA5_MA20_Screener_v1_miniqmt import (
    SECTOR_NAME,
    TICK_CHUNK_SIZE,
    calculate_ma_candidate,
    extract_completed_closes,
)


INDEX_CODE = '000905.SH'
SECTOR_REFRESH_TIMEOUT_SEC = 20
SECTOR_REFRESH_CHILD_ARG = '--refresh-sector-child'


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


def discover_universe(xtdata, refresh_sector=None):
    """Find CSI 500 codes without allowing an unbounded native API call."""
    codes = _cached_sector_codes(xtdata)
    if codes:
        return codes, 'cached sector'

    print('[UNIVERSE] cached sector empty; trying 000905.SH index weights...')
    codes = _cached_index_weight_codes(xtdata)
    if codes:
        return codes, 'cached index weights'

    refresh = refresh_sector or _refresh_sector_with_timeout
    print('[UNIVERSE] local sources empty; refreshing sector data (timeout {}s)...'.format(
        SECTOR_REFRESH_TIMEOUT_SEC))
    if refresh():
        codes = _cached_sector_codes(xtdata)
        if codes:
            return codes, 'refreshed sector cache'
    return [], 'unavailable'


def _refresh_sector_with_timeout():
    command = [sys.executable, __file__, SECTOR_REFRESH_CHILD_ARG]
    try:
        completed = subprocess.run(
            command,
            timeout=SECTOR_REFRESH_TIMEOUT_SEC,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print('[UNIVERSE] sector refresh timed out; child process terminated')
        return False
    if completed.returncode != 0:
        print('[UNIVERSE] sector refresh failed with code {}'.format(
            completed.returncode))
        return False
    print('[UNIVERSE] sector refresh completed')
    return True


def _run_sector_refresh_child():
    from xtquant import xtdata

    try:
        xtdata.connect()
        xtdata.download_sector_data()
        return 0
    except Exception:
        return 1
    finally:
        try:
            xtdata.disconnect()
        except Exception:
            pass


def run_screen(xtdata, today=None, refresh_sector=None):
    today_key = today or datetime.now().strftime('%Y%m%d')

    print('[STEP 1/3] discovering CSI 500 constituents...')
    codes, source = discover_universe(xtdata, refresh_sector=refresh_sector)
    if not codes:
        print('[ERROR] unable to obtain CSI 500 constituents')
        print('[ACTION] update sector/index data in QMT, then run again')
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
    if sys.argv[1:] == [SECTOR_REFRESH_CHILD_ARG]:
        return _run_sector_refresh_child()

    from infra.connector import MiniQMTConnector

    connector = MiniQMTConnector()
    if not connector.connect_data():
        return 1
    try:
        print('[START] CSI 500 MA5/MA20 screener v3 (read-only)')
        result = run_screen(connector.xtdata)
        return 0 if result['universe_count'] else 1
    finally:
        connector.disconnect()


if __name__ == '__main__':
    raise SystemExit(main())
