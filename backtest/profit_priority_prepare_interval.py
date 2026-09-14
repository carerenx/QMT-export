"""Create an independently hashed interval from the frozen Redis QMT bundle."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.profit_priority_research import audit


def main():
    source = ROOT / 'analysis/profit_priority_redis_refresh_20260914'
    output = ROOT / 'analysis/profit_priority_20250915_20260911'
    output.mkdir(parents=True, exist_ok=True)
    original = json.loads((source / 'data_manifest.json').read_text(encoding='utf8'))
    manifest = dict(start='20250915', end='20260911', source=str(source),
                    in_sample=True, stocks={})
    lines = ['# 2025-09-15至2026-09-11融合研究', '',
             '用户批准的新区间；原区间与失败记录未覆盖。仅属样本内研究。', '',
             '| 标的 | 分钟根数 | 交易日 | 行情完整性 |', '|---|---:|---:|---|']
    for stock, info in original['stocks'].items():
        folder = output / stock
        folder.mkdir(exist_ok=True)
        bundle = {}
        for label in ('front', 'raw', 'minute'):
            path = source / stock / (label + '.csv')
            if hashlib.sha256(path.read_bytes()).hexdigest() != info['hashes'][path.name]:
                raise ValueError('source hash mismatch: ' + str(path))
            frame = pd.read_csv(path, dtype={'time': str}).set_index('time')
            if label == 'minute':
                frame = frame.loc[(frame.index.str[:8] >= '20250915') & (frame.index.str[:8] <= '20260911')]
            frame.to_csv(folder / path.name, index_label='time')
            bundle[label] = frame
        calendar = [r['date'] for r in info['audit']['sessions'] if '20250915' <= r['date'] <= '20260911']
        report = audit(**bundle, calendar=calendar, factors=info['factors'], start='20250915', end='20260911')
        market_errors = [e for e in report['errors'] if not e.startswith(('corporate action', 'nonempty corporate'))]
        report['market_data_valid'] = not market_errors
        manifest['stocks'][stock] = dict(audit=report, factors=info['factors'],
            hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.csv')})
        lines.append(f"| {stock} | {len(bundle['minute'])} | {len(calendar)} | {'通过' if not market_errors else market_errors} |")
        print(stock, 'market valid:', not market_errors, 'corporate events:', report['period_factors'], flush=True)
    lines += ['', '分钟行情检查通过不代表收益验收通过。真实分红登记、到账及税费仍需核验接入；未绕过公司行动保护，当前不发布收益排名。', '',
              '数据来源为此前通过Redis QMT取得并核验哈希的缓存；日线保留初始化历史，分钟线裁剪为批准区间。', '',
              '复现：`python backtest/profit_priority_prepare_interval.py`。逐日检查、事件及哈希见 `data_manifest.json`。']
    (output / 'data_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    (output / 'README.md').write_text('\n'.join(lines)+'\n', encoding='utf8')


if __name__ == '__main__':
    main()
