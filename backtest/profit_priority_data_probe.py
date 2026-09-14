"""Read-only RPC diagnostics. Preserve the original frozen research bundle."""
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/bigqmt/src'))
from bigqmt_signal_trader.xtquant_compat import configure, load_client_config


def main():
    config = load_client_config()
    trader, data = configure(account_id=str(config.get('account_id') or ''))
    records = {}
    for stock in ('600584.SH', '600105.SH', '601869.SH'):
        # BigQMT ContextInfo accepts one date, not an inclusive date range.
        factors = data.get_divid_factors(stock)
        events = {}
        for timestamp, value in factors.items():
            date = pd.to_datetime(int(timestamp), unit='ms', utc=True).tz_convert('Asia/Shanghai').strftime('%Y%m%d')
            if '20250912' <= date <= '20260911':
                events[date] = value
        download = data.download_history_data2([stock], '1m', '20250911000000',
                                               '20250913000000', incrementally=False)
        result = data.get_market_data_ex(stock_list=[stock],
            field_list=['open', 'high', 'low', 'close', 'volume', 'amount'], period='1m',
            start_time='20250912000000', end_time='20250912235959', count=-1,
            dividend_type='none', fill_data=False, timeout_seconds=60)
        frame = result[stock]
        records[stock] = dict(factors_in_period=events, download=download,
                             first=str(frame.index[0]) if len(frame) else None,
                             last=str(frame.index[-1]) if len(frame) else None, rows=len(frame))
        print(stock, records[stock], flush=True)
    destination = ROOT / 'analysis/profit_priority_fusion_20260914/data_probe.json'
    destination.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
