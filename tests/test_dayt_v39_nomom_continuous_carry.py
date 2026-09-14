import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Stragety/MiniQMT_Stragety/DayT/DayTradeing_v39_nomom_ContinuousCarry.py'
sys.path.insert(0, str(ROOT / 'Stragety/MiniQMT_Stragety'))


def load_strategy():
    spec = importlib.util.spec_from_file_location('v39_continuous_carry', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContinuousCarryTests(unittest.TestCase):
    def test_carry_snapshot_preserves_a_single_short_leg_and_blocks_new_entries(self):
        strategy = load_strategy()
        runner = strategy.StrategyRunner.__new__(strategy.StrategyRunner)
        runner.st = {
            'short_legs': [(70.0, 100)],
            'long_legs': [],
            'mom_leg_shares': 0,
            'fstate': strategy.STATE_SOLD,
            'sell_fill_price': 70.0,
            'buyback_target': 69.0,
            'buyback_target_pct': 0.01,
            'dip_price': 68.5,
            'ladder_sell_target': 71.0,
            'ladder_sold_count': 1,
        }

        carry = runner._capture_overnight_carry()

        self.assertEqual(carry['kind'], 'short')
        self.assertEqual(carry['short_legs'], [(70.0, 100)])
        runner.st['overnight_carry_active'] = True
        self.assertEqual(runner._new_leg_block_reason(), 'overnight carry awaiting reconciliation')


if __name__ == '__main__':
    unittest.main()
