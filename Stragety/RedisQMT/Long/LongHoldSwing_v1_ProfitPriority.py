"""Independent research entry. Real-money execution is deliberately unavailable."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from Stragety.MiniQMT_Stragety.core.profit_priority_swing import Policy, EXPERIMENTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',choices=EXPERIMENTS,default='swing_fusion')
    parser.add_argument('--mode',choices=['research','live'],default='research')
    args = parser.parse_args()
    if args.mode == 'live':
        raise RuntimeError('Live execution unavailable: use the research runner')
    print('Policy:', Policy(args.experiment))
    print('Run: python backtest/profit_priority_research.py --output analysis/profit_priority_fusion_20260914')


if __name__ == '__main__':
    main()
