"""Fetch normalized Sina OHLCV inputs for symbol-specific DayT research."""
import argparse
from pathlib import Path

import akshare as ak
import pandas as pd


FIELDS = ['open', 'high', 'low', 'close', 'volume', 'amount']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', required=True, help='Sina symbol, e.g. sh600584')
    parser.add_argument('--start', required=True, help='YYYYMMDD')
    parser.add_argument('--end', required=True, help='YYYYMMDD')
    parser.add_argument('--period', default='5', choices=['1', '5', '15', '30', '60'])
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()

    minute = ak.stock_zh_a_minute(
        symbol=args.symbol, period=args.period, adjust='')
    minute['day'] = pd.to_datetime(minute['day'])
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end) + pd.Timedelta(days=1)
    minute = minute[(minute['day'] >= start) & (minute['day'] < end)]
    minute = minute.set_index('day')[FIELDS].sort_index()

    daily_start = (pd.Timestamp(args.start) - pd.Timedelta(days=500)).strftime('%Y%m%d')
    daily = ak.stock_zh_a_daily(
        symbol=args.symbol, start_date=daily_start,
        end_date=args.end, adjust='')
    daily['date'] = pd.to_datetime(daily['date'])
    daily = daily.set_index('date')[FIELDS].sort_index()

    if minute.empty or daily.empty:
        raise RuntimeError('empty Sina data for {}'.format(args.symbol))
    if not minute.index.is_unique or not daily.index.is_unique:
        raise RuntimeError('duplicate timestamps for {}'.format(args.symbol))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    minute.index = minute.index.strftime('%Y%m%d%H%M%S')
    daily.index = daily.index.strftime('%Y%m%d')
    minute.to_csv(args.output_dir / '1m.csv', index_label='time')
    daily.to_csv(args.output_dir / '1d.csv', index_label='time')
    print('{}m {} rows {}..{}'.format(
        args.period, len(minute), minute.index[0], minute.index[-1]))
    print('1d {} rows {}..{}'.format(
        len(daily), daily.index[0], daily.index[-1]))


if __name__ == '__main__':
    main()
