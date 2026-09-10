import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'dayt_v47', ROOT / 'Stragety/MiniQMT_Stragety/DayT/DayT_v47_AdaptiveTSize.py')
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)


class SizingTests(unittest.TestCase):
    def test_prices_and_position_constraints(self):
        cases = [(65., 1000, 300), (65., 400, 200), (65., 200, 100),
                 (400., 200, 100), (65., 100, 100), (65., 99, 0)]
        for price, base, expected in cases:
            with self.subTest(price=price, base=base):
                self.assertEqual(s.calculate_t_shares(price, base, 100, 20000, .5), expected)

    def test_cash_and_lot_rounding(self):
        self.assertEqual(s.calculate_t_shares(65., 1000, 100, 20000, .5, 14000), 200)
        self.assertEqual(s.calculate_t_shares(65., 1000, 100, 20000, .5, 6000), 0)
        self.assertEqual(s.calculate_t_shares(65., 1000, 200, 20000, .5), 200)
        self.assertEqual(s.calculate_t_shares(float('nan'), 1000, 100, 20000, .5), 0)

    def setUp(self):
        patch.object(s, '_log').start()
        self.addCleanup(patch.stopall)
        self.p = s.PortfolioRunner(dry_run=False)
        self.r = s.StrategyRunner(self.p, '600584.SH')
        self.r._init_state()
        self.r.st['base_can_use'] = 1000
        self.r.st['base_shares'] = 1000
        self.p.runners[self.r.stock_qmt] = self.r

    def test_reserved_backing_is_removed_before_sizing(self):
        self.r.st['long_legs'] = [(65., 800)]
        with patch.object(self.r, '_refresh_position'), \
                patch.object(self.r, '_available_cash', return_value=100000):
            self.assertEqual(self.r._new_t_shares(65., 'SELL'), 100)

    def test_new_buy_preserves_own_buyback_reserve(self):
        self.r.st['short_legs'] = [(65., 300)]
        with patch.object(self.r, '_refresh_position'), \
                patch.object(self.r, '_available_cash', return_value=20000):
            self.assertEqual(self.r._new_t_shares(65., 'BUY'), 0)

    def test_opening_order_expands_but_closing_order_keeps_actual_quantity(self):
        with patch.object(self.r, '_new_t_shares', return_value=300) as sizing, \
                patch.object(self.r, '_clamp_sell_shares', side_effect=lambda n: n), \
                patch.object(self.r, '_clamp_buy_shares', side_effect=lambda n, p: n), \
                patch.object(self.r, '_snapshot_account', return_value={}), \
                patch.object(self.r, '_wait_for_fill', side_effect=lambda a, n, *rest: ('FILLED', n)), \
                patch.object(s, 'order_shares', return_value=123) as submit:
            self.assertEqual(self.r._submit_order(-100, 65., 'REV-T sell'), ('FILLED', -300))
            self.assertEqual(submit.call_args.args[1], -300)
            self.assertEqual(self.r._submit_order(200, 64., 'REV-T buyback(NORMAL)'), ('FILLED', 200))
            self.assertEqual(submit.call_args.args[1], 200)
            self.assertEqual(sizing.call_count, 1)

    def test_signal_mode_reports_size_without_order(self):
        self.r.dry_run = True
        with patch.object(self.r, '_new_t_shares', return_value=300), \
                patch.object(self.r, '_clamp_sell_shares', return_value=300), \
                patch.object(s, 'order_shares') as submit:
            self.assertEqual(self.r._submit_order(-100, 65., 'MOM short'), ('SKIP', 0))
            submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
