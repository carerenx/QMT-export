import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_dayt_v49_recovery as previous

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dayt50', ROOT / 'Stragety/MiniQMT_Stragety/DayT/DayT_v50_IntradayRebound.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class RecoveryRegression(previous.RecoveryTests):
    def setUp(self):
        patch.object(previous, 's', s).start()
        super().setUp()

    def test_save_log_throttling_does_not_skip_forced_writes(self):
        with patch.object(s._time, 'monotonic', side_effect=[1000., 1030., 1299., 1300.]), \
                patch.object(s, 'write_checkpoint') as write, \
                patch.object(s, '_log') as log:
            for _ in range(4):
                self.p.save_checkpoint(force=True)
        self.assertEqual(write.call_count, 4)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertEqual(sum('[STATE-SAVED]' in message for message in messages), 2)


class ExecutionRegression(previous.V49ExecutionHealthTests):
    def setUp(self):
        patch.object(previous, 's', s).start()
        super().setUp()


class ExitDispatchRegression(previous.ExitDispatchTests):
    def setUp(self):
        patch.object(previous, 's', s).start()
        self.addCleanup(patch.stopall)


class ReboundTests(unittest.TestCase):
    def setUp(self):
        patch.object(s, '_log').start()
        self.addCleanup(patch.stopall)
        self.p = s.PortfolioRunner(False)
        self.r = s.StrategyRunner(self.p, '601869.SH')
        self.r._init_state()
        self.r.st.update(daily_signal={'sell_trigger': 457.27, 'atr_pct': .0654},
                         intraday_avg_valid=True, intraday_avg_price=440.,
                         do_short=True, do_long=False, base_can_use=100, base_shares=100)

    def warmup(self):
        for t in range(1000, 1211, 30):
            self.r._update_rebound_reference(436., t, {'open': 445.})

    def test_weak_reference_is_lower_but_not_current_price(self):
        self.warmup()
        value = self.r._rev_sell_trigger()
        self.assertGreater(value, 436.)
        self.assertLess(value, 457.27)
        self.assertEqual(value, 443.13)
        self.assertEqual(self.r.st['daily_signal']['sell_trigger'], 457.27)

    def test_touch_arms_only_and_strong_recovery_cancels(self):
        self.warmup()
        with patch.object(self.r, '_submit_order') as submit:
            self.r._handle_idle(443.2)
            self.assertEqual(self.r.st['fstate'], s.STATE_SPIKING)
            self.assertEqual(self.r.st['trade_count_short'], 1)
            submit.assert_not_called()
            self.r._update_rebound_reference(445., 1240, {'open': 445.})
            self.assertEqual(self.r.st['fstate'], s.STATE_IDLE)
            self.assertEqual(self.r.st['trade_count_short'], 0)
            self.assertEqual(self.r._rev_sell_trigger(), 457.27)

    def test_missing_average_and_quote_gap_restore_original(self):
        self.warmup()
        self.r._update_rebound_reference(436., 1400, {'open': 445.})
        self.assertEqual(self.r._rev_sell_trigger(), 457.27)
        self.warmup()
        self.r.st['intraday_avg_valid'] = False
        self.r._update_rebound_reference(436., 1240, {'open': 445.})
        self.assertEqual(self.r._rev_sell_trigger(), 457.27)

    def test_strong_market_never_lowers_reference(self):
        for t in range(1000, 1301, 30):
            self.r._update_rebound_reference(446., t, {'open': 445.})
        self.assertEqual(self.r._rev_sell_trigger(), 457.27)

    def test_new_cycle_discards_previous_reference(self):
        self.warmup()
        self.r.st['next_t_cycle'] = 1
        self.r.st['daily_signal'].update(sell_trigger=450., trigger_base='CLOSE_FILL_ATR')
        self.r._update_rebound_reference(436., 1240, {'open': 445.})
        self.assertEqual(self.r._rev_sell_trigger(), 450.)

    def test_existing_exit_target_is_unchanged(self):
        self.warmup()
        self.r.st.update(fstate=s.STATE_SOLD, short_legs=[(444., 100)], buyback_target=439.)
        self.r._update_rebound_reference(436., 1240, {'open': 445.})
        self.assertEqual(self.r.st['buyback_target'], 439.)
        self.assertEqual(self.r._rev_sell_trigger(), 457.27)

    def test_heartbeat_displays_effective_reference(self):
        self.warmup()
        with patch.object(self.r, '_log') as log:
            self.r._heartbeat(436.)
        self.assertIn('443.13', log.call_args.args[0])

    def test_restart_rewarms_instead_of_reusing_old_low(self):
        self.warmup()
        record = self.r.checkpoint_record()
        restored = s.StrategyRunner(self.p, '601869.SH')
        restored.restore_record(record)
        self.assertEqual(restored._rev_sell_trigger(), 457.27)
        self.assertEqual(restored.st['rebound_memory'], {})

    def test_second_cycle_full_weakness_targets_about_69(self):
        self.r.st['daily_signal'].update(sell_trigger=69.42, atr_pct=.0488567,
            trigger_base='CLOSE_FILL_ATR', reentry={'base':68.21, 'up_units':.3614})
        self.r.st['intraday_avg_price'] = 68.8
        for t in range(1000, 1211, 30):
            self.r._update_rebound_reference(67.8, t, {'open':69.1})
        self.assertEqual(self.r._rev_sell_trigger(), 69.00)
        self.assertAlmostEqual(self.r.st['rebound_memory']['effective_units'], .3614*.65)
        self.assertEqual(self.r.st['daily_signal']['sell_trigger'], 69.42)

    def test_second_cycle_partial_weakness_is_linear(self):
        self.r.st['daily_signal'].update(sell_trigger=69.42, atr_pct=.0488567,
            trigger_base='CLOSE_FILL_ATR', reentry={'base':68.21, 'up_units':.3614})
        self.r.st['intraday_avg_price'] = 68.8
        price = 68.8 / (1 + .0488567*.25*.5)
        for t in range(1000, 1211, 30):
            self.r._update_rebound_reference(price, t, {'open':69.1})
        self.assertAlmostEqual(self.r.st['rebound_memory']['weakness'], .5)
        self.assertAlmostEqual(self.r.st['rebound_memory']['effective_units'], .3614*.825)
        self.assertGreater(self.r._rev_sell_trigger(), 69.00)
        self.assertLess(self.r._rev_sell_trigger(), 69.42)

    def test_second_cycle_average_guard_can_keep_price_above_69(self):
        self.r.st['daily_signal'].update(sell_trigger=69.42, atr_pct=.0488567,
            trigger_base='CLOSE_FILL_ATR', reentry={'base':68.21, 'up_units':.3614})
        self.r.st['intraday_avg_price'] = 69.1
        for t in range(1000, 1211, 30):
            self.r._update_rebound_reference(67.8, t, {'open':69.2})
        self.assertGreater(self.r._rev_sell_trigger(), 69.00)
        self.r._update_rebound_reference(69.3, 1240, {'open':69.2})
        self.assertEqual(self.r._rev_sell_trigger(), 69.42)


if __name__ == '__main__':
    unittest.main()
