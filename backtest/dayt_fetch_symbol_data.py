"""Fetch isolated DayT OHLCV inputs for one symbol through the BigQMT bridge."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/bigqmt/src'))

from bigqmt_signal_trader.xtquant_compat import configure


FIELDS = ['open', 'high', 'low', 'close', 'volume', 'amount']


def _fetch_frame(data, symbol, period, count, start_time, end_time):
    if period != '1m' or not start_time or not end_time:
        payload = data.get_market_data_ex(
            FIELDS, [symbol], period=period, count=count,
            start_time=start_time, end_time=end_time,
            dividend_type='none', fill_data=False, timeout_seconds=120)
        return payload[symbol]
    frames = []
    cursor = datetime.strptime(start_time[:8], '%Y%m%d')
    boundary = datetime.strptime(end_time[:8], '%Y%m%d')
    while cursor <= boundary:
        chunk_end = min(cursor + timedelta(days=89), boundary)
        payload = data.get_market_data_ex(
            FIELDS, [symbol], period=period, count=-1,
            start_time=cursor.strftime('%Y%m%d'),
            end_time=(chunk_end + timedelta(days=1)).strftime('%Y%m%d'),
            dividend_type='none', fill_data=False, timeout_seconds=120)
        frame = payload[symbol]
        if not frame.empty:
            frames.append(frame)
        cursor = chunk_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=FIELDS)
    combined = pd.concat(frames).loc[lambda frame: ~frame.index.duplicated(keep='last')]
    return combined.loc[combined.index.astype(str).str[:8] <= end_time[:8]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--minute-count', default=24000, type=int)
    parser.add_argument('--daily-count', default=400, type=int)
    parser.add_argument('--start-time', default='')
    parser.add_argument('--end-time', default='')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trader = None
    trader, data = configure(account_id='8890145315', timeout_seconds=20)
    counts = {'1m': args.minute_count, '1d': args.daily_count}
    for period, count in counts.items():
        if args.start_time and args.end_time:
            data.download_history_data2(
                [args.symbol], period, args.start_time, args.end_time,
                dividend_type='none', download_timeout_seconds=180.0,
                data_wait_seconds=60.0)
        frame = _fetch_frame(
            data, args.symbol, period, count, args.start_time,
            args.end_time).sort_index()
        if frame.empty:
            raise RuntimeError('empty {} data for {}'.format(period, args.symbol))
        if not frame.index.is_unique:
            raise RuntimeError('duplicate {} timestamps for {}'.format(
                period, args.symbol))
        frame.to_csv(args.output_dir / (period + '.csv'), index_label='time')
        print('{} {} rows {}..{}'.format(
            period, len(frame), frame.index[0], frame.index[-1]))


if __name__ == '__main__':
    main()
