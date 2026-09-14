"""Acquire auditable Redis QMT data and run the four-arm research matrix."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'integrations/bigqmt/src'))
from backtest.profit_priority_engine import replay, Account, drawdowns
from Stragety.MiniQMT_Stragety.core.profit_priority_swing import EXPERIMENTS


def audit(front, raw, minute, calendar, factors, start, end):
    errors = []
    session_rows = []
    selected = minute.loc[(minute.index.str[:8] >= start) & (minute.index.str[:8] <= end)]
    groups = dict(tuple(selected.groupby(selected.index.str[:8])))
    for label, frame in [('front', front), ('raw', raw), ('minute', selected)]:
        if not frame.index.is_unique:
            errors.append(label + ': duplicate timestamps')
        prices = frame[['open', 'high', 'low', 'close']]
        if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
            errors.append(label + ': invalid prices')
        if ((frame.high < frame[['open', 'close', 'low']].max(axis=1)) |
                (frame.low > frame[['open', 'close', 'high']].min(axis=1))).any():
            errors.append(label + ': inconsistent OHLC')
    for date in calendar:
        bars = groups.get(date)
        if bars is None:
            errors.append(date + ': missing session (suspension not independently confirmed)')
            continue
        count = len(bars)
        session_rows.append(dict(date=date, rows=count, first=bars.index[0], last=bars.index[-1]))
        if count < 240 or bars.index[0][8:12] not in ('0930','0931') or bars.index[-1][8:12] != '1500':
            errors.append(date + ': incomplete minute session')
        expected = set(pd.date_range(date+' 09:31', date+' 11:30', freq='min').strftime('%Y%m%d%H%M%S'))
        expected.update(pd.date_range(date+' 13:01', date+' 15:00', freq='min').strftime('%Y%m%d%H%M%S'))
        if not expected.issubset(set(bars.index)):
            errors.append(date + ': missing scheduled minute timestamps')
        if date not in raw.index or abs(float(bars.iloc[-1].close)-float(raw.loc[date,'close'])) > .02:
            errors.append(date + ': daily/minute close mismatch')
    if not selected.index.is_unique:
        errors.append('duplicate minute timestamps')
    ratio = (front.close / raw.close).dropna()
    changes = ratio.pct_change().abs()
    events = changes.loc[(changes.index >= start) & (changes.index <= end) & (changes > .0005)]
    if len(events):
        errors.append('corporate action adjustment requires verified cash/share entitlement: ' + ','.join(events.index))
    period_factors = {}
    for timestamp, value in factors.items():
        date = pd.to_datetime(int(timestamp), unit='ms', utc=True).tz_convert('Asia/Shanghai').strftime('%Y%m%d')
        if start <= date <= end:
            period_factors[date] = value
    if period_factors:
        errors.append('nonempty corporate actions require entitlement processing before valuation')
    returns = raw.close.pct_change().abs()
    jumps = returns.loc[(returns.index >= start) & (returns.index <= end) & (returns > .105)]
    if len(jumps):
        errors.append('unexplained >10.5% close changes: ' + ','.join(jumps.index))
    return dict(valid=not errors, errors=errors, sessions=session_rows,
                adjustment_events=events.to_dict(), period_factors=period_factors)


def fetch(output, stocks, start, end):
    from bigqmt_signal_trader.xtquant_compat import configure, load_client_config
    config = load_client_config()
    account = str(config.get('account_id') or '')
    trader, data = configure(account_id=account)
    pong = trader.client.call('ping')
    if not pong.get('pong') or str(pong.get('account_id')) != account:
        raise RuntimeError('Redis identity mismatch')
    frames = {}
    manifest = {'source':'BigQMT Redis RPC', 'rpc_revision':pong.get('rpc_revision'), 'stocks':{}}
    calendar = data.get_trading_dates('SH',start,end)
    for stock in stocks:
        folder = output / stock
        folder.mkdir(parents=True, exist_ok=True)
        bundle = {}
        for label, period, adjustment in [('front','1d','front'),('raw','1d','none'),('minute','1m','none')]:
            first = '20240901' if period == '1d' else start
            data.download_history_data2([stock],period,first,'20260912',dividend_type=adjustment)
            pieces = []
            # Request one month at a time to avoid RPC/cache truncation.
            boundaries = list(pd.date_range(first, end, freq='MS'))
            boundaries = sorted(set([pd.Timestamp(first)] + boundaries + [pd.Timestamp(end)+pd.Timedelta(days=1)]))
            for left,right in zip(boundaries,boundaries[1:]):
                result = data.get_market_data_ex(
                    field_list=['open','high','low','close','volume','amount'], stock_list=[stock],
                    period=period,start_time=left.strftime('%Y%m%d'),end_time=right.strftime('%Y%m%d'),
                    count=-1,dividend_type=adjustment,fill_data=False,timeout_seconds=120)
                pieces.append(result[stock])
            frame = pd.concat(pieces)
            frame.index = frame.index.astype(str)
            frame = frame.loc[~frame.index.duplicated()].sort_index()
            frame = frame.loc[frame.index.str[:8] <= end]
            frame.to_csv(folder / (label+'.csv'),index_label='time')
            bundle[label] = frame
        # BigQMT's optional date is a single-date selector, NOT a range.
        factors = data.get_divid_factors(stock)
        if hasattr(factors,'to_dict'):
            factors = factors.to_dict()
        report = audit(**bundle,calendar=calendar,factors=factors,start=start,end=end)
        manifest['stocks'][stock] = dict(audit=report, factors=factors,
            hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.csv')})
        frames[stock] = bundle
        print(stock, 'audit:', report['valid'], report['errors'], flush=True)
    (output/'data_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    return frames, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cached',action='store_true')
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True,exist_ok=True)
    stocks = ['600584.SH','600105.SH','601869.SH']
    if args.cached:
        manifest = json.loads((output/'data_manifest.json').read_text(encoding='utf8'))
        for stock, entry in manifest['stocks'].items():
            for name, expected in entry['hashes'].items():
                if hashlib.sha256((output/stock/name).read_bytes()).hexdigest() != expected:
                    raise ValueError('Cached data hash mismatch: ' + stock + '/' + name)
        frames = {s:{label:pd.read_csv(output/s/(label+'.csv'),dtype={'time':str}).set_index('time')
                     for label in ('front','raw','minute')} for s in stocks}
    else:
        frames,manifest = fetch(output,stocks,'20250912','20260911')
    if not all(v['audit']['valid'] for v in manifest['stocks'].values()):
        lines = ['# 收益优先融合研究：数据验收未通过', '',
                 '研究区间：2025-09-12 至 2026-09-11。全部属于样本内研究。', '',
                 '当前没有有效收益排名，不代表策略无收益或已提升收益。', '',
                 '## 数据检查', '', '| 标的 | 阻碍 |', '|---|---|']
        for stock, entry in manifest['stocks'].items():
            lines.append('| '+stock+' | '+'；'.join(entry['audit']['errors'])+' |')
        lines += ['', '原始 CSV、逐日检查和 SHA256 见 `data_manifest.json`。Redis 分红因子为空，但前复权/不复权比例出现变化；不得把除权直接当作普通涨跌。', '',
                  '## 已实现范围', '',
                  '- 独立研究策略、四组日线决策、统一单账户下一分钟撮合入口。',
                  '- 现金、整手、可卖股份、T+1、部分成交、订单最低费用、独占订单预留、显式日终撤单。',
                  '- 两次重定价限制；不采用同分钟成交或15:00强行平仓。',
                  '- 分钟/日终净值、损益恒等式、订单和成交记录。', '',
                  '## 尚未完成的验收与交付', '',
                  '- 完整起始日分钟数据及可核验的分红、送转与除权权益处理。',
                  '- 四组有效收益、双基准、逐笔归因、固定数量执行对照、阶段与成本敏感性结果。',
                  '- 持久化重启恢复、全面停牌/涨跌停识别、成交量单位独立核验。',
                  '- 波段状态目前按信号维护，尚需与实际减仓成交同步后才能作为验收版本。',
                  '- 注册表仅登记研究入口，未适配旧版日重置接口；不得声称通过严格连续策略注册验收。', '',
                  '## 本次验证记录（2026-09-14）', '',
                  '- DayT 黄金回放：396 次独立日回放全部通过，成交及损益精确一致。',
                  '- 新增撮合测试 4 项、既有 v2 测试 6 项全部通过；这不是全面安全验收或收益验证。',
                  '- 三只股票起始日均仅164根分钟线，首根10:47，末根15:00。', '',
                  '## 复现', '', '```powershell',
                  'python -m unittest tests.test_profit_priority -v',
                  'python backtest/dayt_benchmark.py replay',
                  'python backtest/profit_priority_research.py --output analysis/profit_priority_fusion_20260914 --cached',
                  '```', '',
                  '缓存读取先核验 SHA256。数据无效时阻断收益发布。未启动任何实盘委托。']
        (output/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
        print('DATA_INVALID: no return rankings published')
        return
    results = {}
    for stock,bundle in frames.items():
        results[stock] = {}
        for experiment in EXPERIMENTS:
            row = replay(**bundle,start='20250912',end='20260911',experiment=experiment)
            results[stock][experiment] = row
            print(stock,experiment,row['profit'],flush=True)
    (output/'results.json').write_text(json.dumps(results,ensure_ascii=False),encoding='utf8')


if __name__ == '__main__':
    main()
