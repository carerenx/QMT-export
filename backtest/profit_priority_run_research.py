"""Provisional four-arm experiment with explicit dividend haircut assumptions."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.profit_priority_engine import replay
from backtest.profit_priority_corporate import CashDividend
from Stragety.MiniQMT_Stragety.core.profit_priority_swing import EXPERIMENTS

EVENTS = {
 '600584.SH': [('20250925','20250926',.03), ('20260622','20260623',.10)],
 '600105.SH': [('20251013','20251014',.035), ('20260616','20260617',.015)],
 '601869.SH': [('20260820','20260821',.295)]}


def main():
    folder = ROOT / 'analysis/profit_priority_20250915_20260911'
    manifest = json.loads((folder/'data_manifest.json').read_text(encoding='utf8'))
    results = {}
    lines = ['# 四组融合策略：初步研究结果', '',
             '区间2025-09-15至2026-09-11，三只标的同参数。样本内研究，尚未完成策略安全验收，不可用于实盘。', '',
             '本轮现金分红统一按公告含税额的80%在派息日入账。这是保守研究折减，不是实际持有期税费计算；初始股份持有期未知。', '',
             '费用单边0.05%且每单最低5元，滑点1分；分钟成交量按手计，参与率1%（仍需独立核验单位）。波段状态尚按信号而非实际成交同步，因此第四组只作诊断。', '',
             '| 股票 | 实验 | 期末资产 | 净利润 | 分钟最大回撤 | 平均仓位 |', '|---|---|---:|---:|---:|---:|']
    for stock, info in manifest['stocks'].items():
        if not info['audit']['market_data_valid']:
            raise ValueError('market audit failed')
        bundle = {}
        for label in ('front','raw','minute'):
            path = folder/stock/(label+'.csv')
            if hashlib.sha256(path.read_bytes()).hexdigest() != info['hashes'][path.name]:
                raise ValueError('hash mismatch')
            bundle[label] = pd.read_csv(path,dtype={'time':str}).set_index('time')
        dividends = [CashDividend(stock+ex,record,ex,ex,gross*.8,
            'CORPORATE_ACTION_REVIEW.md; 20% research haircut, NOT actual tax')
            for record,ex,gross in EVENTS[stock]]
        results[stock] = {}
        for experiment in EXPERIMENTS:
            row = replay(**bundle,start='20250915',end='20260911',experiment=experiment,cash_dividends=dividends)
            results[stock][experiment] = row
            (folder/stock/(experiment+'.json')).write_text(json.dumps(row,ensure_ascii=False),encoding='utf8')
            lines.append(f"| {stock} | {experiment} | {row['final']:.2f} | {row['profit']:.2f} | {row['minute_drawdown']['maximum']:.2%} | {row['average_weight']:.2%} |")
            print(stock,experiment,round(row['final'],2),flush=True)
    lines += ['', '## 三股合计（初步诊断，不是最终选择）', '', '| 实验 | 合计期末资产 | 合计利润 |', '|---|---:|---:|']
    for experiment in EXPERIMENTS:
        total = sum(v[experiment]['final'] for v in results.values())
        profit = sum(v[experiment]['profit'] for v in results.values())
        lines.append(f'| {experiment} | {total:.2f} | {profit:.2f} |')
    lines += ['', '各股票目录JSON包含逐笔订单、成交、费用、分红、日终与分钟净值。', '',
              '未完成：双持有基准、完整公告核验、真实税费、成本/参数敏感性、固定数量执行归因、波段成交同步与恢复验收。不得据本表宣称计划达标。']
    (folder/'PRELIMINARY_RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf8')


if __name__ == '__main__':
    main()
