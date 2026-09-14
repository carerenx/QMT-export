"""Frozen 20-stock diagnostic, including non-reinvested dividend scenarios.

Not a substitute for verified corporate-action settlement and tax accounting.
"""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.profit_priority_engine import replay, drawdowns


def dividend_claim(events, fills, initial_shares=1000):
    claims = []
    for date, factors in events.items():
        if any(float(x) != 0 for x in factors[1:5]):
            raise ValueError('noncash action not supported')
        # Proxy entitlement: holdings before ex-date, NOT verified record date.
        shares = initial_shares + sum(f['shares'] for f in fills if f['time'][:8] < date)
        claims.append(dict(date=date, shares=shares, gross=shares*float(factors[0])))
    return claims


def benchmark(minute, full=False):
    first = float(minute.iloc[0].open)
    cash = 100000.
    shares = 1000
    initial = cash+shares*first
    fee = 0.
    # Market-open benchmark purchase; same slip and fee, assumes enough opening liquidity.
    if full:
        price = round(first+.01,2)
        extra = int((cash-5)/(price*1.0005)/100)*100
        fee = max(5.,extra*price*.0005) if extra else 0.
        cash -= extra*price+fee
        shares += extra
    curve = [(str(t),cash+shares*float(r.close)) for t,r in minute.iterrows()]
    return dict(initial=initial,final=curve[-1][1],profit=curve[-1][1]-initial,
                shares=shares,cash=cash,fees=fee,minute_drawdown=drawdowns([(minute.index[0],initial)]+curve),
                fills=[],average_weight=sum(shares*float(p)/(cash+shares*float(p)) for p in minute.close)/len(minute))


def main():
    folder = ROOT/'analysis/profit_priority_cross_stock_20'
    findings = json.loads((folder/'adjustment_audit.json').read_text(encoding='utf8'))
    result = {}
    for item in findings:
        stock = item['stock']
        destination = folder/stock/'diagnostic'
        destination.mkdir(exist_ok=True)
        try:
            if item['market_errors']:
                raise ValueError(str(item['market_errors']))
            manifest = json.loads((folder/stock/'data_manifest.json').read_text(encoding='utf8'))['stocks'][stock]
            bundle = {}
            for label in ('front','raw','minute'):
                path = folder/stock/stock/(label+'.csv')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['hashes'][path.name]:
                    raise ValueError('hash mismatch')
                frame = pd.read_csv(path,dtype={'time':str}).set_index('time')
                if label=='minute':
                    frame = frame.loc[(frame.index.str[:8]>='20250915') & (frame.index.str[:8]<='20260911')]
                bundle[label]=frame
            rows = {}
            for strategy in ('v2_control','profit_core','cash_hold','full_hold'):
                if strategy.endswith('hold'):
                    row = benchmark(bundle['minute'],strategy=='full_hold')
                    initial_shares = row['shares']
                else:
                    row = replay(**bundle,start='20250915',end='20260911',experiment=strategy)
                    initial_shares = 1000
                claims = dividend_claim(item['events'],row['fills'],initial_shares)
                gross = sum(c['gross'] for c in claims)
                row['dividend_proxy_claims']=claims
                row['terminal_scenarios']={str(f):row['final']+gross*f for f in (0.,.8,1.)}
                (destination/(strategy+'.json')).write_text(json.dumps(row,ensure_ascii=False),encoding='utf8')
                rows[strategy]={key:value for key,value in row.items() if key not in ('minute_equity','eod','fills','orders','plans')}
            result[stock]=dict(name=item['name'],sector=item['sector'],volatility=item['volatility'],rows=rows)
            print(stock,'DONE',flush=True)
        except Exception as exc:
            result[stock]=dict(name=item['name'],error=str(exc))
            print(stock,'FAILED',str(exc),flush=True)
        (folder/'comparison_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    valid = [r for r in result.values() if 'rows' in r]
    lines=['# 20股冻结参数比较：诊断结果', '',
           '区间2025-09-15至2026-09-11。未修改名单及策略参数。初始10万元现金+1000股，只做多、无融资。', '',
           '**不是严格有效性验收通过。** 本实验用除息日前持仓代理登记日权益，分红不参与再投资，期末按0%、80%、100%计入。80%不是实际税率。策略的仓位与回撤决策仍使用未含分红的账户。', '',
           '单边费用0.05%、每单最低5元、滑点0.01元。全资金基准期初按开盘价加滑点买入现金可承受整手，未校验开盘成交量，属于理想持有参考。分钟回撤不含期末分红补计。', '',
           '| 股票 | v2收益率 | 候选收益率 | 现金持有 | 全资金持有 | 候选-v2 | 候选-全资金 | 候选分钟回撤 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for stock,item in result.items():
        if 'error' in item:
            lines.append('| '+stock+' | 失败：'+item['error']+' | | | | | | |')
            continue
        rows=item['rows']
        returns={k:v['terminal_scenarios']['0.8']/v['initial']-1 for k,v in rows.items()}
        lines.append(f"| {stock} {item['name']} | {returns['v2_control']:.2%} | {returns['profit_core']:.2%} | {returns['cash_hold']:.2%} | {returns['full_hold']:.2%} | {returns['profit_core']-returns['v2_control']:.2%} | {returns['profit_core']-returns['full_hold']:.2%} | {rows['profit_core']['minute_drawdown']['maximum']:.2%} |")
    lines+=['','## 分红情景汇总','','| 计入比例 | 有结果股票 | 候选胜v2 | 候选胜全资金 | 候选等权收益 | v2等权收益 | 全资金等权收益 |','|---|---:|---:|---:|---:|---:|---:|']
    for fraction in ('0.0','0.8','1.0'):
        values=[{k:v['terminal_scenarios'][fraction]/v['initial']-1 for k,v in r['rows'].items()} for r in valid]
        n=len(values)
        if n:
            wins=sum(v['profit_core']>v['v2_control'] for v in values)
            holdwins=sum(v['profit_core']>v['full_hold'] for v in values)
            means={k:sum(v[k] for v in values)/n for k in ('profit_core','v2_control','full_hold')}
            lines.append(f"| {fraction} | {n} | {wins}/{n} | {holdwins}/{n} | {means['profit_core']:.2%} | {means['v2_control']:.2%} | {means['full_hold']:.2%} |")
    lines+=['','## 结论边界','','只能依据本表评价本诊断模型下的跨股表现，不能声称真实账户收益已验证。仍欠缺真实权益到账和持有期税费、完整成本压力、涨跌停与成交量核验以及状态恢复验收。', '',
            '名单为人工选择的当前大型股票，存在幸存者与选择偏差；同一历史区间已参与研究，不是独立时间样本外。固定1000股会导致贵州茅台等高价股初始资产明显更大，因此以等权收益率和胜率优先，不用合计金额掩盖失败股票。', '',
            '各股票diagnostic目录保存逐笔成交、订单与净值JSON；comparison_summary.json保存可复核汇总。没有启动实盘。']
    (folder/'COMPARISON.md').write_text('\n'.join(lines)+'\n',encoding='utf8')


if __name__=='__main__':
    main()
