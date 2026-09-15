"""Research-only entry. No live trading functions are provided."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from Stragety.MiniQMT_Stragety.core.long_hold_ablation import AblationPolicy

# Default remains the control until experiments and safety acceptance complete.
FULL_BULL = False
REMOVE_DRAWDOWN = False
REMOVE_WEAKNESS = False


def build_policy():
    return AblationPolicy(FULL_BULL, REMOVE_DRAWDOWN, REMOVE_WEAKNESS)


if __name__ == '__main__':
    print('Research only: python backtest/long_hold_ablation.py')
