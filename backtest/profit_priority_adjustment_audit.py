"""Audit additive or proportional adjustment within known action segments."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def adjustment_segments(front, raw, event_dates):
    paired = pd.concat([raw.close.rename('raw'), front.close.rename('front')], axis=1).dropna()
    boundaries = sorted(set(event_dates))
    labels = [sum(date >= event for event in boundaries) for date in paired.index]
    reports = []
    for label, frame in paired.groupby(labels):
        if len(frame) < 5 or frame.raw.nunique() < 3:
            reports.append(dict(first=frame.index[0], last=frame.index[-1], status='INSUFFICIENT_POINTS'))
            continue
        design = np.column_stack([frame.raw.to_numpy(), np.ones(len(frame))])
        slope, offset = np.linalg.lstsq(design, frame.front.to_numpy(), rcond=None)[0]
        residual = float(np.max(np.abs(frame.front.to_numpy() - design @ [slope, offset])))
        reports.append(dict(first=frame.index[0], last=frame.index[-1], slope=float(slope),
                            offset=float(offset), max_residual=residual,
                            status='CONSISTENT' if slope > 0 and residual <= .025 else 'UNEXPLAINED'))
    return reports


def main():
    output = ROOT/'analysis/profit_priority_cross_stock_20'
    progress = json.loads((output/'progress.json').read_text(encoding='utf8'))
    findings = []
    for row in progress:
        stock = row['stock']
        folder = output/stock
        manifest = json.loads((folder/'data_manifest.json').read_text(encoding='utf8'))['stocks'][stock]
        for name, expected in manifest['hashes'].items():
            if hashlib.sha256((folder/stock/name).read_bytes()).hexdigest() != expected:
                raise ValueError('hash mismatch: '+stock+'/'+name)
        events = manifest['audit']['period_factors']
        frames = {label:pd.read_csv(folder/stock/(label+'.csv'),dtype={'time':str}).set_index('time')
                  for label in ('front','raw')}
        frames = {key:frame.loc['20250915':'20260911'] for key, frame in frames.items()}
        segments = adjustment_segments(frames['front'],frames['raw'],events)
        market_errors = [e for e in row['errors'] if not e.startswith(('corporate action','nonempty corporate'))]
        findings.append(dict(stock=stock,name=row['name'],sector=row['sector'],
            volatility=row['volatility'],market_errors=market_errors,segments=segments,events=events))
    (output/'adjustment_audit.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2),encoding='utf8')
    ranked = sorted(findings,key=lambda row:row['volatility'])
    groups = {r['stock']:('低' if i<7 else '中' if i<14 else '高') for i,r in enumerate(ranked)}
    lines = ['# 20股数据复核与事前波动分组', '',
             '20只均已完成下载。旧版固定比例检查不适用于加减式前复权，其大量公司行动提示不能直接解释为真实事件。', '',
             '本轮在QMT已知除权日期之间拟合前复权价=a×原价+b，检查价格舍入容差内的一致性；这只是数据诊断，不是用拟合价格替代真实行情。', '',
             '| 股票 | 名称 | 事前年化波动 | 相对分组 | 行情问题数 | 已知事件数 | 复权段异常数 |', '|---|---|---:|---|---:|---:|---:|']
    for row in findings:
        unresolved = sum(s['status'] != 'CONSISTENT' for s in row['segments'])
        lines.append(f"| {row['stock']} | {row['name']} | {row['volatility']:.2%} | {groups[row['stock']]} | {len(row['market_errors'])} | {len(row['events'])} | {unresolved} |")
    lines += ['', '低中高仅指这20股内部事前波动排序，不是风险评级。参数未改变，失败标的未删除。', '',
              '真实公司行动到账与税费、双基准及批量收益运行尚未完成，不能宣称策略有效。个别短复权段会标记样本不足，不能自动当作通过。', '',
              '20股为人工选取的当前大型股票，存在选择与幸存者偏差；即使跨股通过也不构成独立时间样本外验证。']
    (output/'DATA_REVIEW.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print('stocks',len(findings),'market_problem_stocks',sum(bool(r['market_errors']) for r in findings))


if __name__ == '__main__':
    main()
