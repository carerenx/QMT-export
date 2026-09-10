import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'dayt_v46', ROOT / 'Stragety/MiniQMT_Stragety/DayT/DayT_v46_ATRReentry.py')
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)


def history():
    return pd.DataFrame({'open': [100.] * 80, 'high': [104.] * 80,
                         'low': [98.] * 80, 'close': [100.] * 80})


class ReentryTests(unittest.TestCase):
    def setUp(self):
        self.log = patch.object(s, '_log').start()
        self.addCleanup(patch.stopall)
        self.p = s.PortfolioRunner(dry_run=False)
        self.r = s.StrategyRunner(self.p, '600584.SH')
        self.r._init_state()
        self.r.st['daily_signal'] = dict(sell_trigger=70.66, buy_trigger=67.69,
                                         atr_pct=.06, open_price=70.)
        self.r.st['reentry_history'] = history()
        self.r._last_executed_order = dict(order_id=123, shares=100)
        self.order = SimpleNamespace(order_id=123, stock_code='600584.SH',
                                     traded_price=69., traded_volume=100)
        self.p.conn.trader = SimpleNamespace(
            query_stock_order=lambda account, oid: self.order)

    def test_directional_units_and_price_anchor(self):
        result = s.calculate_atr_reentry(69., *[
            history()[f].tolist() for f in ('open', 'high', 'low', 'close')])
        self.assertAlmostEqual(result['atr_pct'], .06)
        self.assertAlmostEqual(result['up_units'], 2 / 3)
        self.assertAlmostEqual(result['down_units'], 1 / 3)
        self.assertEqual(result['sell_trigger'], 71.76)
        self.assertEqual(result['buy_trigger'], 67.62)

    def test_confirmed_execution_reanchors_without_changing_active_exit(self):
        self.r.st['buyback_target'] = 65.
        self.r.st['bt_sellback_target'] = 75.
        self.assertTrue(self.r._recalculate_next_t_triggers('REV-T'))
        self.assertEqual(self.r.st['daily_signal']['trigger_base_price'], 69.)
        self.assertEqual(self.r.st['daily_signal']['sell_trigger'], 71.76)
        self.assertEqual(self.r.st['buyback_target'], 65.)
        self.assertEqual(self.r.st['bt_sellback_target'], 75.)
        self.assertIsNone(self.r.st['reentry_pending'])

    def test_missing_execution_blocks_new_entries_then_recovers(self):
        self.order.traded_price = 0
        self.assertFalse(self.r._recalculate_next_t_triggers('FWD-T'))
        self.assertTrue(self.r._new_leg_block_reason())
        self.assertEqual(self.r.st['daily_signal']['sell_trigger'], 70.66)
        self.order.traded_price = 69.
        self.r.st['reentry_pending']['retry_at'] = 0
        self.assertTrue(self.r._retry_atr_reentry())
        self.assertFalse(self.r._new_leg_block_reason())

    def test_wrong_symbol_or_order_cannot_anchor(self):
        self.order.stock_code = '601869.SH'
        self.assertFalse(self.r._recalculate_next_t_triggers('REV-T'))
        self.order.stock_code = '600584.SH'
        self.order.order_id = 456
        self.r.st['reentry_pending']['retry_at'] = 0
        self.assertFalse(self.r._retry_atr_reentry())

    def test_partial_buyback_does_not_reanchor(self):
        self.r.st['short_legs'] = [(70., 200)]
        with patch.object(self.r, '_submit_buyback_order', return_value=('PARTIAL', 100)), \
                patch.object(self.r, '_recalculate_next_t_triggers') as recalc:
            self.r._do_buyback(69.)
            recalc.assert_not_called()
            self.assertEqual(self.r.st['daily_signal']['sell_trigger'], 70.66)

    def test_invalid_history_does_not_produce_threshold(self):
        self.assertIsNone(s.calculate_atr_reentry(69., [1.] * 5, [2.] * 5,
                                                  [.5] * 5, [1.] * 5))

    def test_heartbeat_cannot_overwrite_frozen_reentry(self):
        self.r._recalculate_next_t_triggers('FWD-T')
        self.r.st['do_long'] = True
        with patch.object(self.r, '_maybe_resume_trading'):
            self.r._heartbeat(100.)
        self.assertEqual(self.r.st['daily_signal']['buy_trigger'], 67.62)


if __name__ == '__main__':
    unittest.main()
