"""Frozen 8-arm factorial plus isolated weakness-cooldown experiment."""
import hashlib
import itertools
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sys
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backtest.profit_priority_engine import replay
from backtest.profit_priority_cross_compare import dividend_claim
from Stragety.MiniQMT_Stragety.core.long_hold_ablation import AblationPolicy


def run_stock(item):
    stock=item['stock']
    source=ROOT/'analysis/profit_priority_cross_stock_20'/stock
    output=ROOT/'analysis/long_hold_ablation_20'/stock
    output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((source/'data_manifest.json').read_text(encoding='utf8'))['stocks'][stock]
    bundle={}
    for label in ('front','raw','minute'):
        path=source/stock/(label+'.csv')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['hashes'][path.name]:
            raise ValueError('hash mismatch')
        bundle[label]=pd.read_csv(path,dtype={'time':str}).set_index('time')
    baseline=json.loads((source/'diagnostic/v2_control.json').read_text(encoding='utf8'))
    rows={}
    variants=[(''.join(map(str,bits)),bits,False) for bits in itertools.product((0,1),repeat=3)]
    variants.append(('000_cooldown',(0,0,0),True))
    for name,bits,bypass in variants:
        row=replay(**bundle,start='20250915',end='20260911',experiment='ablation',
                   policy_override=AblationPolicy(*map(bool,bits)),weakness_cooldown_bypass=bypass)
        if name=='000':
            if abs(row['final']-baseline['final'])>.001 or row['fills']!=baseline['fills']:
                raise ValueError('v2 equivalence failed '+stock)
        claims=dividend_claim(item['events'],row['fills'])
        gross=sum(v['gross'] for v in claims)
        row['terminal_scenarios']={str(f):row['final']+gross*f for f in (0.,.8,1.)}
        rows[name]=dict(initial=row['initial'],final=row['terminal_scenarios']['0.8'],
                        returns=row['terminal_scenarios']['0.8']/row['initial']-1,
                        dd=row['minute_drawdown']['maximum'],fees=row['fees'],
                        weight=row['average_weight'])
        (output/(name+'.json')).write_text(json.dumps(row,ensure_ascii=False),encoding='utf8')
    return stock,rows


def main():
    folder=ROOT/'analysis/long_hold_ablation_20'
    folder.mkdir(parents=True,exist_ok=True)
    items=json.loads((ROOT/'analysis/profit_priority_cross_stock_20/adjustment_audit.json').read_text(encoding='utf8'))
    results={}
    with ProcessPoolExecutor(max_workers=4) as pool:
        for stock,rows in pool.map(run_stock,items):
            results[stock]=rows
            (folder/'summary.json').write_text(json.dumps(results,indent=2),encoding='utf8')
            print(stock,'9 variants complete',flush=True)
    lines=['# 三因素消融与冷却对照：20股', '',
           '000=v2；三个二进制位分别为普通多头100%、取消回撤控制、取消MA60弱势限仓。1表示开启改动。000_cooldown仅豁免弱势减仓冷却。', '',
           '名单、日期、交易费用与此前诊断一致。80%分红代理额仅在期末补计、不再投资，不是实际税费。所有000组逐笔成交及期末资产须与既有v2精确一致，否则停止。', '',
           '| 组合 | 等权收益 | 胜v2股数 | 最差股收益 | 平均分钟回撤 |', '|---|---:|---:|---:|---:|']
    for name in next(iter(results.values())):
        rows=[r[name] for r in results.values()]
        avg=sum(r['returns'] for r in rows)/len(rows)
        wins=sum(r[name]['final']>r['000']['final']+.001 for r in results.values())
        lines.append(f"| {name} | {avg:.2%} | {wins}/20 | {min(r['returns'] for r in rows):.2%} | {sum(r['dd'] for r in rows)/20:.2%} |")
    lines+=['','完整逐笔订单、成交、费用、分钟净值保存于各股JSON。summary.json保留所有股票所有组合，没有按股票挑选参数。', '',
            '本轮完成180个基础诊断运行，不是完整实盘验收：成本/参数压力、真实权益及税费、恢复安全仍待完成。20股已参与研究，本轮属样本内优化。未替换原策略或启动实盘。']
    (folder/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf8')


if __name__=='__main__':
    main()
