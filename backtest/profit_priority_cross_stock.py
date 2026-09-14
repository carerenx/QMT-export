"""Frozen cross-sector universe and resumable Redis QMT data acceptance."""
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.profit_priority_research import fetch

UNIVERSE = [
    ('601398.SH', '工商银行', '银行'),
    ('600036.SH', '招商银行', '银行'),
    ('601318.SH', '中国平安', '保险'),
    ('600030.SH', '中信证券', '证券'),
    ('600900.SH', '长江电力', '水电'),
    ('601088.SH', '中国神华', '煤炭'),
    ('601857.SH', '中国石油', '油气'),
    ('601899.SH', '紫金矿业', '有色金属'),
    ('600519.SH', '贵州茅台', '白酒'),
    ('600887.SH', '伊利股份', '乳制品'),
    ('000333.SZ', '美的集团', '家电'),
    ('600276.SH', '恒瑞医药', '医药'),
    ('000651.SZ', '格力电器', '家电'),
    ('600031.SH', '三一重工', '工程机械'),
    ('002594.SZ', '比亚迪', '汽车'),
    ('600406.SH', '国电南瑞', '电力设备'),
    ('601012.SH', '隆基绿能', '光伏'),
    ('002475.SZ', '立讯精密', '电子'),
    ('002415.SZ', '海康威视', '安防'),
    ('002230.SZ', '科大讯飞', '软件与人工智能'),
]


def main():
    output = ROOT / 'analysis/profit_priority_cross_stock_20'
    output.mkdir(parents=True, exist_ok=True)
    specification = dict(start='20250915', end='20260911', initial_cash=100000,
                         initial_shares=1000, experiments=['v2_control','profit_core'],
                         universe=UNIVERSE, selection='sector coverage, not outcome selected',
                         validation='cross-stock only, not independent temporal out-of-sample')
    spec = output/'universe.json'
    if spec.exists() and json.loads(spec.read_text(encoding='utf8')) != json.loads(json.dumps(specification)):
        raise ValueError('frozen universe differs')
    spec.write_text(json.dumps(specification,ensure_ascii=False,indent=2),encoding='utf8')
    rows = []
    for stock, name, sector in UNIVERSE:
        folder = output/stock
        try:
            path = folder/'data_manifest.json'
            if path.exists():
                manifest = json.loads(path.read_text(encoding='utf8'))
            else:
                _, manifest = fetch(folder,[stock],'20250915','20260911')
            entry = manifest['stocks'][stock]
            daily = pd.read_csv(folder/stock/'front.csv',dtype={'time':str}).set_index('time')
            history = daily.loc[daily.index < '20250915'].tail(121)
            volatility = history.close.pct_change().dropna().std() * 252**.5
            rows.append(dict(stock=stock,name=name,sector=sector,volatility=volatility,
                             valid=entry['audit']['valid'],errors=entry['audit']['errors']))
        except Exception as exc:
            rows.append(dict(stock=stock,name=name,sector=sector,valid=False,errors=[str(exc)]))
        (output/'progress.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
        print('PROGRESS',len(rows),'/20',stock,flush=True)
    lines = ['# 20只股票跨标的检验：数据验收', '',
             '名单在下载与收益计算前冻结。行情源Redis QMT，区间2025-09-15至2026-09-11；原三只研究标的不参与此组。', '',
             '行业为研究标签。事前波动率采用起点前最多120个日收益的标准差×√252，不用区间收益挑选股票。', '',
             '| 股票 | 名称 | 行业 | 事前年化波动率 | 验收问题 |', '|---|---|---|---:|---|']
    for row in rows:
        vol = f"{row['volatility']:.2%}" if 'volatility' in row else '未知'
        lines.append(f"| {row['stock']} | {row['name']} | {row['sector']} | {vol} | {'；'.join(row['errors']) or '通过'} |")
    lines += ['', '当前仅完成数据阶段，不是有效性验证结论。公司行动、涨跌停和成交量等验收未通过者不发布有效收益，但保留其失败记录。', '',
              '计划对比冻结v2与收益优先长期层，以及现金+1000股持有和全资金持有两基准。固定1000股导致初始资产不同，必须同时报告各股收益率、等权平均与超额胜率，不能只看合计金额。', '',
              '样本按当前可识别的大型股票人工选取，存在选择及幸存者偏差；不能声称代表全A股。无实盘委托。']
    (output/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf8')


if __name__ == '__main__':
    main()
