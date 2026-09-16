import importlib.util
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PATH = (Path(__file__).resolve().parents[1] / "Stragety" /
        "MiniQMT_Stragety" / "DayT" /
        "DayT_v56_nomom_ConfirmedReversalRiskBudget.py")
SPEC = importlib.util.spec_from_file_location("dayt_v56", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ConfirmedReversalTests(unittest.TestCase):
    def _short_cycle_runner(self, trading_days):
        portfolio = MODULE.PortfolioRunner(False)
        runner = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)
        cycle = MODULE.Cycle(
            'risk', 0, 'SHORT', '20260916',
            '2026-09-16T10:12:12', 0.0435884456812737)
        cycle.fill(635106068, 400, 67.69, True, None)
        cycle.advance_verified(trading_days)
        runner.cycle = cycle
        return runner

    def test_rejects_single_bar_noise(self):
        self.assertFalse(MODULE.confirmed_short_reversal(
            100.0, 100.30, 100.05, 1))

    def test_rejects_peak_without_enough_extension(self):
        self.assertFalse(MODULE.confirmed_short_reversal(
            100.0, 100.10, 99.80, 2))

    def test_rejects_immaterial_pullback(self):
        self.assertFalse(MODULE.confirmed_short_reversal(
            100.0, 100.30, 100.15, 2))

    def test_accepts_extended_confirmed_reversal(self):
        self.assertTrue(MODULE.confirmed_short_reversal(
            100.0, 100.30, 100.05, 2))

    def test_core_position_limits_t_inventory(self):
        shares = MODULE.calculate_t_shares(
            40.0, 1000, 100, 40000.0, MODULE.T_POSITION_FRACTION)
        self.assertEqual(400, shares)

    def test_live_mode_requires_confirmation_before_starting_runner(self):
        with patch.object(sys, "argv", [str(PATH), "--mode", "live"]):
            with patch("builtins.input", return_value="no"):
                with patch.object(MODULE, "FileLogger"):
                    with patch.object(MODULE, "PortfolioRunner") as runner:
                        MODULE.main()
        runner.assert_not_called()

    def test_live_mode_starts_non_dry_runner_after_confirmation(self):
        with patch.object(sys, "argv", [str(PATH), "--mode", "live"]):
            with patch("builtins.input", return_value="yes"):
                with patch.object(MODULE, "FileLogger"):
                    with patch.object(MODULE, "PortfolioRunner") as runner:
                        MODULE.main()
        runner.assert_called_once_with(dry_run=False)
        runner.return_value.run.assert_called_once_with()

    def test_reused_order_id_stops_before_query_or_cancel(self):
        portfolio = MODULE.PortfolioRunner(False)
        runner = MODULE.StrategyRunner(portfolio, '600584.SH', lane=1)
        runner._init_state()
        portfolio.own_order_ids.add('635106068')
        portfolio.conn.trader = SimpleNamespace(query_stock_order=Mock())
        portfolio.conn.cancel_order = Mock()

        with patch.object(MODULE, 'order_shares', return_value=635106068), \
                patch.object(runner, '_new_t_shares', return_value=100), \
                patch.object(runner, '_clamp_sell_shares', return_value=100), \
                patch.object(runner, '_snapshot_account', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'duplicate broker order id'):
                MODULE.ExecutionRunner._submit_order(
                    runner, -100, 67.68, 'REV-T sell')

        self.assertTrue(portfolio.order_uncertain)
        portfolio.conn.trader.query_stock_order.assert_not_called()
        portfolio.conn.cancel_order.assert_not_called()

    def test_adverse_price_move_no_longer_forces_buyback(self):
        runner = self._short_cycle_runner(['20260916'])

        reason = runner._short_cycle_risk_reason(68.38)

        self.assertEqual('', reason)

    def test_max_holding_exit_is_retained(self):
        runner = self._short_cycle_runner(
            ['20260916', '20260917', '20260918'])

        reason = runner._short_cycle_risk_reason(68.38)

        self.assertEqual('MAX_HOLDING_DAYS', reason)

    def test_session_time_no_longer_forces_buyback(self):
        source = inspect.getsource(MODULE.StrategyRunner._short_cycle_risk_reason)
        self.assertNotIn('SESSION_END', source)
        self.assertFalse(hasattr(MODULE, 'SHORT_SESSION_EXIT_TIME'))

    def test_non_holding_period_forced_buybacks_are_removed(self):
        sold_source = inspect.getsource(MODULE.ExecutionRunner._handle_sold)
        run_source = inspect.getsource(MODULE.ExecutionRunner.run)

        self.assertNotIn('EMERG', sold_source)
        self.assertNotIn('REBOUND99', sold_source)
        self.assertNotIn('_force_buyback', run_source)
        self.assertNotIn('[STOP-LOSS] REV-T', run_source)
        self.assertFalse(hasattr(MODULE.ExecutionRunner, '_force_buyback'))

    def test_mom_trading_subsystem_is_removed(self):
        source = PATH.read_text(encoding='utf-8')

        for token in (
                'MOM_ENABLED', 'MOM_STATE_', 'def _mom_',
                "'MOM short'", "'MOM long'"):
            self.assertNotIn(token, source)

    def test_flat_legacy_mom_fields_are_discarded_on_restore(self):
        portfolio = MODULE.PortfolioRunner(False)
        original = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)
        original._init_state()
        record = original.checkpoint_record()
        record['state'] = dict(record['state'], mom_state='MOM_IDLE',
                               mom_leg_shares=0, mom_trade_count=2)
        restored = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)

        restored.restore_record(record)

        self.assertFalse(any(key.startswith('mom_') for key in restored.st))

    def test_open_legacy_mom_state_is_blocked_on_restore(self):
        portfolio = MODULE.PortfolioRunner(False)
        original = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)
        original._init_state()
        record = original.checkpoint_record()
        record['state'] = dict(record['state'], mom_state='MOM_SOLD',
                               mom_leg_shares=100)
        restored = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)

        with self.assertRaisesRegex(RuntimeError, 'manual reconciliation'):
            restored.restore_record(record)

    def test_fwd_trailing_trigger_tracks_tick_high_and_never_falls(self):
        portfolio = MODULE.PortfolioRunner(False)
        runner = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)
        runner._init_state()
        runner.st.update(
            initialized=True,
            do_long=False,
            fstate=MODULE.STATE_IDLE,
            daily_signal={
                'buy_trigger_floor': 97.0,
                'buy_trigger': 98.0,
            })

        runner._update_fwd_buy_trigger(105.0)
        runner._update_fwd_buy_trigger(100.0)

        signal = runner.st['daily_signal']
        self.assertEqual(98.0, signal['buy_trigger_trail'])
        self.assertEqual(102.9, signal['buy_trigger_max_trail'])
        self.assertEqual(102.9, signal['buy_trigger'])
        self.assertEqual(104.13, signal['sellback_target_hint'])

    def test_fwd_max_trail_survives_checkpoint_restore(self):
        portfolio = MODULE.PortfolioRunner(False)
        original = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)
        original._init_state()
        original.st.update(
            initialized=True,
            fstate=MODULE.STATE_IDLE,
            daily_signal={
                'buy_trigger_floor': 97.0,
                'buy_trigger': 98.0,
            })
        original._update_fwd_buy_trigger(105.0)
        record = original.checkpoint_record()
        restored = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)

        restored.restore_record(record)

        self.assertEqual(102.9, restored.st['bt_max_trail'])
        self.assertEqual(
            102.9, restored.st['daily_signal']['buy_trigger_max_trail'])

    def test_live_loop_updates_fwd_trail_before_idle_dispatch(self):
        source = inspect.getsource(MODULE.ExecutionRunner.run)

        update_at = source.index('self._update_fwd_buy_trigger(price)')
        idle_at = source.index('self._handle_idle(price)')

        self.assertLess(update_at, idle_at)
        self.assertNotIn("self.st['bt_max_trail'] = _buy_trail", source)

    def test_routine_operational_logs_are_file_only(self):
        heartbeat = inspect.getsource(MODULE.ExecutionRunner._heartbeat)
        save = inspect.getsource(MODULE.ExecutionPortfolio.save_checkpoint)
        monitor = inspect.getsource(MODULE.ExecutionRunner._monitor_connections)

        self.assertNotIn('self._log(', heartbeat)
        self.assertIn('self._file_log(', heartbeat)
        self.assertIn("_log_file_only('[STATE-SAVED]", save)
        self.assertIn("tag == 'QUOTE-HEALTH'", monitor)
        self.assertIn("('FRESH', 'NOT_APPLICABLE')", monitor)


if __name__ == "__main__":
    unittest.main()
