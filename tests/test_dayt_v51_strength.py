import sys
from pathlib import Path
import unittest
import importlib.util
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Stragety/MiniQMT_Stragety'))
from core.intraday_strength import IntradayStrength, reference_price, StrengthConfig

spec = importlib.util.spec_from_file_location('dayt51', ROOT / 'Stragety/MiniQMT_Stragety/DayT/DayT_v51_IntradayStrength.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
import test_dayt_v50_rebound as previous


class ExecutionRegression(previous.ExecutionRegression):
    def setUp(self):
        patch.object(previous, 's', s).start()
        super().setUp()


class RecoveryRegression(previous.RecoveryRegression):
    def setUp(self):
        patch.object(previous, 's', s).start()
        super().setUp()


class ExitRegression(previous.ExitDispatchRegression):
    def setUp(self):
        patch.object(previous, 's', s).start()
        super().setUp()


class StrengthTests(unittest.TestCase):
    @patch.object(s, 'QUANTILE_UNITS_SCALE', .8)
    def test_quantile_scale_changes_real_trigger_without_compounding(self):
        signal = dict(quantile_trend_active=True, open_price=437.32, atr_pct=.0654,
                      quantile_trend=dict(open_price=437.32, trigger_units=.698,
                                          trigger_pct=.0654*.698))
        s.scale_quantile_signal(signal)
        self.assertAlmostEqual(signal['trigger_units'], .5584)
        self.assertEqual(signal['sell_trigger'], 453.29)
        s.scale_quantile_signal(signal)
        self.assertAlmostEqual(signal['trigger_units'], .5584)
        with patch.object(s, 'QUANTILE_UNITS_SCALE', 1.):
            s.scale_quantile_signal(signal)
        self.assertAlmostEqual(signal['trigger_units'], .698)

    def test_quantile_scale_does_not_change_reentry_target(self):
        signal = dict(quantile_trend_active=True, trigger_base='CLOSE_FILL_ATR', sell_trigger=69.42)
        s.scale_quantile_signal(signal)
        self.assertEqual(signal['sell_trigger'], 69.42)

    @patch.object(s, 'REENTRY_UP_UNITS_SCALE', .8)
    def test_reentry_scale_is_independent_idempotent_and_keeps_buy_target(self):
        signal = dict(trigger_base='CLOSE_FILL_ATR', buy_trigger=67.08,
                      reentry=dict(base=68.21, atr_pct=.04885670852586228,
                                   up_units=.3613687253323494, buy_trigger=67.08))
        for _ in range(2):
            s.scale_reentry_signal(signal)
            self.assertEqual(signal['sell_trigger'], 69.18)
            self.assertAlmostEqual(signal['reentry']['up_units'], .3613687253323494*.8)
            self.assertEqual(signal['buy_trigger'], 67.08)
            self.assertEqual(signal['reentry']['buy_trigger'], 67.08)
        with patch.object(s, 'REENTRY_UP_UNITS_SCALE', 1.):
            s.scale_reentry_signal(signal)
        self.assertEqual(signal['sell_trigger'], 69.42)

    def test_reentry_scale_skips_first_round_and_rejects_invalid_parameter(self):
        signal = dict(sell_trigger=457.27)
        s.scale_reentry_signal(signal)
        self.assertEqual(signal['sell_trigger'], 457.27)
        for value in (0, -1, float('nan'), float('inf')):
            with patch.object(s, 'REENTRY_UP_UNITS_SCALE', value), self.assertRaises(ValueError):
                s.scale_reentry_signal(dict(trigger_base='CLOSE_FILL_ATR'))

    def warm(self, engine):
        for second in range(0, 901, 10):
            result = engine.update(32400 + second, 100., 100., 100., .1, 100., 110., .2)
        return result

    def test_flat_market_above_open_is_not_automatically_strong(self):
        engine = IntradayStrength()
        for second in range(0, 901, 10):
            result = engine.update(32400 + second, 437.5, 437.32, 437.6,
                                   .06539737, 437.32, 457.27, .313578)
            if second < 900:
                self.assertEqual(result['effective'], 457.27)
        self.assertEqual(result['minutes'], 15)
        self.assertLess(result['strength'], .03)
        self.assertGreater(result['effective'], 446.)
        self.assertLess(result['effective'], 447.)

    def test_interpolation_is_monotonic_and_uses_cycle_base(self):
        results = [reference_price(100, 110, .1, .2, strength, 90, 90)['candidate']
                   for strength in (0, .5, 1)]
        self.assertEqual(results, [102., 106., 110.])
        self.assertEqual(reference_price(200, 220, .1, .2, .5, 180, 180)['candidate'], 212.)

    def test_touch_preserves_old_reference_before_minute_update(self):
        engine = IntradayStrength()
        self.assertEqual(self.warm(engine)['effective'], 102.)
        result = engine.update(33310, 102.1, 100., 100., .1, 100., 110., .2)
        self.assertTrue(result['touched'])
        self.assertEqual(result['effective'], 102.)

    def test_touch_wins_even_when_new_minute_strength_turns_strong(self):
        engine = IntradayStrength()
        self.warm(engine)
        engine.scores = [1., 1.]
        engine.bucket.update(price=112., average=100.)
        result = engine.update(33360, 112., 100., 100., .1, 100., 110., .2)
        self.assertEqual(result['phase'], 'TOUCHED')
        self.assertEqual(result['effective'], 102.)

    def test_lowering_below_market_is_not_a_touch(self):
        engine = IntradayStrength()
        self.warm(engine)
        # A prior high reference is held until a real future crossing.
        engine.effective = 110.
        with patch('core.intraday_strength.reference_price', return_value={'candidate': 99.}):
            result = engine.update(33360, 100., 100., 100., .1, 100., 110., .2)
        self.assertEqual(result['phase'], 'BELOW_MARKET_HOLD')
        self.assertFalse(result['touched'])
        self.assertEqual(result['effective'], 110.)

    def test_gap_and_bad_data_restart_full_observation(self):
        engine = IntradayStrength()
        self.warm(engine)
        result = engine.update(34200, 100., 100., 100., .1, 100., 110., .2)
        self.assertEqual(result['effective'], 110.)
        self.assertEqual(result['minutes'], 0)
        result = engine.update(34210, 100., 100., 0., .1, 100., 110., .2)
        self.assertEqual(result['phase'], 'INVALID')

    def test_strong_frozen_reference_requests_cancel(self):
        engine = IntradayStrength()
        self.warm(engine)
        cancels = 0
        for t in range(33310, 33541, 10):
            result = engine.update(t, 112., 100., 100., .1, 100., 110., .2, frozen=True)
            cancels += int(result['cancel'])
        self.assertEqual(cancels, 1)
        self.assertEqual(result['effective'], 110.)

    def test_incomplete_middle_minute_breaks_continuity(self):
        engine = IntradayStrength()
        self.warm(engine)
        engine.update(33310, 100., 100., 100., .1, 100., 110., .2)
        # Next bucket first seen 20 seconds after its boundary, then completes.
        engine.update(33380, 100., 100., 100., .1, 100., 110., .2)
        result = engine.update(33420, 100., 100., 100., .1, 100., 110., .2)
        self.assertEqual(result['minutes'], 0)
        self.assertEqual(result['effective'], 110.)

    def test_original_price_arm_stays_frozen_and_is_not_cancelled(self):
        engine = IntradayStrength()
        self.warm(engine)
        engine.effective = 110.
        result = engine.update(33360, 100., 100., 100., .1, 100., 110., .2, frozen=True)
        self.assertEqual(result['effective'], 110.)
        self.assertFalse(result['cancel'])

    def test_invalid_configuration_fails_before_observation(self):
        for settings in ({'open_units': 0}, {'warmup': 3}, {'quantile': 2}, {'rebound_min': .03}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                StrengthConfig(**settings)

    def test_starting_midminute_does_not_count_partial_bucket(self):
        engine = IntradayStrength()
        for t in range(32430, 33331, 10):
            result = engine.update(t, 100., 100., 100., .1, 100., 110., .2)
        self.assertEqual(result['minutes'], 14)
        self.assertEqual(result['effective'], 110.)


class WiringTests(unittest.TestCase):
    def setUp(self):
        patch.object(s, '_log').start()
        self.addCleanup(patch.stopall)
        self.p = s.PortfolioRunner(False)
        self.r = s.StrategyRunner(self.p, '601869.SH')
        self.r._init_state()
        self.r.st.update(daily_signal=dict(open_price=100., atr_pct=.1, sell_trigger=110.),
                         do_short=True, do_long=False, base_shares=100, base_can_use=100,
                         intraday_avg_valid=True, intraday_avg_price=100.)
        self.r._strength_key = ('', 100., 110., .1, 0)
        self.r._strength_lower = .2

    def warm(self):
        for second in range(0, 901, 10):
            self.r._update_strength_reference(100., 32400 + second, {'open': 100.})

    @patch.object(s, 'REENTRY_UP_UNITS_SCALE', .8)
    def test_restore_scales_old_reentry_without_changing_exit_or_counts(self):
        self.r.st.update(fstate=s.STATE_SOLD, buyback_target=68.91, trade_count_short=1)
        self.r.st['daily_signal'].update(trigger_base='CLOSE_FILL_ATR', buy_trigger=67.08,
            reentry=dict(base=68.21, atr_pct=.04885670852586228,
                         up_units=.3613687253323494, buy_trigger=67.08))
        for _ in range(2):
            record = self.r.checkpoint_record()
            self.r = s.StrategyRunner(self.p, '601869.SH')
            self.r.restore_record(record)
            self.assertEqual(self.r.st['daily_signal']['sell_trigger'], 69.18)
            self.assertEqual(self.r.st['buyback_target'], 68.91)
            self.assertEqual(self.r.st['trade_count_short'], 1)

    @patch.object(s, 'REENTRY_UP_UNITS_SCALE', .8)
    def test_new_cycle_and_preview_use_scaled_close_fill_formula(self):
        from types import SimpleNamespace
        result = dict(base=68.21, atr_pct=.04885670852586228,
                      up_units=.3613687253323494, down_units=.33857,
                      sell_trigger=69.42, buy_trigger=67.08, quantile=.5, sample_count=70)
        self.r.st.update(reentry_pending=dict(order_id=123, shares=200, completed_by='REV'),
                         reentry_history=s.pd.DataFrame({k: [1.] for k in ('open', 'high', 'low', 'close')}))
        order = SimpleNamespace(order_id=123, order_sysid='', stock_code='601869.SH',
                                traded_price=68.21, traded_volume=200)
        trader = SimpleNamespace(query_stock_order=lambda account, order_id: order)
        with patch.object(self.r.conn, 'trader', trader), \
                patch.object(s, 'calculate_atr_reentry', return_value=result):
            self.assertTrue(self.r._retry_atr_reentry())
        sig = self.r.st['daily_signal']
        self.assertEqual(sig['sell_trigger'], 69.18)
        self.assertEqual(sig['buy_trigger'], 67.08)
        self.assertEqual(self.r._rev_sell_trigger(), 69.18)
        sig.update(quantile_trend_active=True, open_price=68.64)
        with patch.object(self.r, '_log') as log, \
                patch.object(self.r.ctx, 'get_full_tick', return_value={}):
            self.r._print_daily_brief(sig)
        preview = next(call.args[0] for call in log.call_args_list if '[REV-T]' in call.args[0])
        self.assertIn('base-trigger Y69.18 = close-fill Y68.2100', preview)
        self.assertIn('reentry_units 0.361369*scale 0.800', preview)
        self.assertNotIn('quantile_units', preview)
        self.assertFalse(any('[QUANTILE-FORMULA]' in call.args[0] for call in log.call_args_list))

    def test_dynamic_reference_is_not_equated_to_base_formula(self):
        sig = self.r.st['daily_signal']
        sig.update(quantile_trend_active=True, trigger_units=1.,
                   quantile_trend=dict(trigger_units=1., unscaled_trigger_units=1.25, units_scale=.8))
        self.r.st['rebound_effective'] = 105.
        with patch.object(s.cfg, 'is_market_open', return_value=True), \
                patch.object(self.r.ctx, 'get_full_tick', return_value={}), patch.object(self.r, '_log') as log:
            self.r._print_daily_brief(sig)
            self.r._heartbeat(100.)
        lines = [call.args[0] for call in log.call_args_list]
        preview = next(line for line in lines if '[REV-T]' in line)
        self.assertIn('base-trigger Y110.00 = open Y100.00', preview)
        self.assertIn('raw 1.250000*scale 0.800', preview)
        for line in (preview, next(line for line in lines if '[HB]' in line)):
            self.assertIn('execution Y105.00', line)
            self.assertIn('shadow-candidate NOT_READY', line)
        self.assertIn('= Y105.00*(1-', preview)

    def test_candidate_labels_require_completed_observation_and_trading_session(self):
        with patch.object(s.cfg, 'is_market_open', return_value=True):
            self.assertEqual(self.r._candidate_log(), 'NOT_READY')
            self.warm()
            self.assertEqual(self.r._candidate_log(), 'Y102.00')
            self.assertEqual(self.r._rev_sell_trigger(), 110.)
        with patch.object(s.cfg, 'is_market_open', return_value=False):
            self.assertEqual(self.r._candidate_log(), 'NON_TRADING')

    def test_quote_alerts_are_throttled_isolated_and_log_only(self):
        import copy
        from types import SimpleNamespace
        now = 1700000100.
        quote = dict(lastPrice=100., time=1700000000)
        trader = SimpleNamespace(data_connected=True, trade_connected=True, query_account=lambda: object())
        before = copy.deepcopy(self.r.st)
        other = s.StrategyRunner(self.p, '600584.SH')
        other._init_state()
        with patch.object(self.r, 'conn', trader), patch.object(other, 'conn', trader), \
                patch.object(self.r.ctx, 'get_full_tick', return_value={'601869.SH': quote}) as query, \
                patch.object(s.cfg, 'is_market_open', return_value=True), patch.object(self.r, '_log') as log:
            self.r._monitor_connections(now)
            self.r._monitor_connections(now + 30)
            self.assertEqual(query.call_count, 2)
            health = lambda: [c.args[0] for c in log.call_args_list if '[QUOTE-' in c.args[0]]
            self.assertEqual(len(health()), 1)
            self.assertIn('STALE', health()[0])
            self.assertIn('connection=OK', health()[0])
            self.r._monitor_connections(now + 300)
            self.assertEqual(len(health()), 2)
            quote['time'] = now + 330
            self.r._monitor_connections(now + 330)
            self.assertIn('[QUOTE-RECOVERED] FRESH', health()[-1])
            quote.pop('time')
            self.r._monitor_connections(now + 360)
            self.assertIn('UNKNOWN', health()[-1])
            self.assertIn('action=LOG_ONLY', health()[-1])
            self.assertIsNone(other._quote_health_status)
            with patch.object(s.cfg, 'is_market_open', return_value=False):
                self.r._monitor_connections(now + 361)
            self.assertIn('NOT_APPLICABLE', health()[-1])
            self.r._monitor_connections(now + 362)
            self.assertEqual(self.r._quote_health_status, 'UNKNOWN')
        self.assertEqual(before, self.r.st)

    def test_default_shadow_does_not_change_execution_or_count(self):
        self.assertEqual(s.INTRADAY_REFERENCE_MODE, 'shadow')
        self.warm()
        self.assertEqual(self.r._strength_result['effective'], 102.)
        self.assertEqual(self.r._rev_sell_trigger(), 110.)
        with patch.object(self.r, '_submit_order') as order:
            self.r._update_strength_reference(102.1, 33310, {'open':100.})
            self.r._handle_idle(102.1)
            order.assert_not_called()
        self.assertEqual(self.r.st['fstate'], s.STATE_IDLE)
        self.assertEqual(self.r.st.get('trade_count_short', 0), 0)
        self.assertTrue(self.r._shadow_strength_armed)

    def test_active_touch_freezes_then_strong_cancels_once(self):
        with patch.object(s, 'INTRADAY_REFERENCE_MODE', 'active'):
            self.warm()
            self.r._update_strength_reference(102.1, 33310, {'open':100.})
            self.r._handle_idle(102.1)
            self.assertEqual(self.r.st['fstate'], s.STATE_SPIKING)
            self.assertEqual(self.r.st['trade_count_short'], 1)
            for t in range(33320, 33551, 10):
                self.r._update_strength_reference(112., t, {'open':100.})
            self.assertEqual(self.r.st['fstate'], s.STATE_IDLE)
            self.assertEqual(self.r.st['trade_count_short'], 0)
            self.assertEqual(self.r._rev_sell_trigger(), 110.)

    def test_existing_leg_falls_back_without_changing_exit(self):
        with patch.object(s, 'INTRADAY_REFERENCE_MODE', 'active'):
            self.warm()
            self.r.st.update(fstate=s.STATE_SOLD, short_legs=[(103.,100)], buyback_target=101.)
            self.r._update_strength_reference(102., 33310, {'open':100.})
            self.assertEqual(self.r._rev_sell_trigger(), 110.)
            self.assertEqual(self.r.st['buyback_target'], 101.)

    def test_real_loop_clears_observation_on_missing_tick_and_lunch(self):
        from contextlib import ExitStack
        for market_open in (True, False):
            with self.subTest(market_open=market_open), ExitStack() as stack:
                self.warm()
                self.r._restored = True
                for method in ('_daily_init', '_print_daily_brief', '_monitor_connections',
                               '_retry_atr_reentry', '_refresh_execution_capacity'):
                    stack.enter_context(patch.object(self.r, method))
                stack.enter_context(patch.object(s.cfg, 'is_market_open', return_value=market_open))
                stack.enter_context(patch.object(s.cfg, 'now_hms', return_value='11:31:00'))
                stack.enter_context(patch.object(self.r.ctx, 'get_full_tick', return_value={}))
                task = self.r.run()
                try:
                    next(task)
                    self.assertEqual(self.r.strength_engine.count, 0)
                    self.assertEqual(self.r._strength_result['phase'],
                                     'MISSING_TICK' if market_open else 'NON_TRADING')
                finally:
                    task.close()

    def test_restart_keeps_ledger_but_cancels_unfilled_lower_arm_in_active(self):
        self.r.st.update(fstate=s.STATE_SPIKING, strength_armed=True, rebound_armed=True,
                         trade_count_short=2, peak_price=103.)
        record = self.r.checkpoint_record()
        with patch.object(s, 'INTRADAY_REFERENCE_MODE', 'active'):
            restored = s.StrategyRunner(self.p, '601869.SH')
            restored.restore_record(record)
        self.assertEqual(restored.st['fstate'], s.STATE_IDLE)
        self.assertEqual(restored.st['trade_count_short'], 1)
        self.assertEqual(restored.strength_engine.count, 0)


if __name__ == '__main__':
    unittest.main()
