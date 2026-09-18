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
    def test_three_session_trend_guard_is_absent(self):
        self.assertFalse(hasattr(MODULE, 'SHORT_TREND_LOOKBACK'))
        self.assertFalse(hasattr(MODULE, 'SHORT_TREND_GUARD_ENABLED'))
        self.assertFalse(hasattr(MODULE, 'SHORT_TREND_RETURN_MAX'))
        self.assertFalse(hasattr(MODULE, 'short_trend_guard'))

    def _portfolio_with_lanes(self, baseline=500):
        portfolio = MODULE.PortfolioRunner(False)
        lane0 = MODULE.StrategyRunner(portfolio, '600584.SH', lane=0)
        lane1 = MODULE.StrategyRunner(portfolio, '600584.SH', lane=1)
        portfolio.runners = {'600584.SH': lane0, '600584.SH#1': lane1}
        for runner in (lane0, lane1):
            runner._init_state()
            runner.baseline_shares = baseline
            runner.st.update(
                initialized=True,
                trade_date=MODULE.datetime.now().strftime('%Y%m%d'),
                daily_signal={'atr_pct': 0.04})
        return portfolio, lane0, lane1

    def _open_short_cycle(self, runner, quantity=100, price=68.0):
        cycle = MODULE.Cycle(
            'manual-sync-cycle-{}'.format(runner.lane), runner.lane,
            'SHORT', '20260917', '2026-09-17T09:30:00', 0.04)
        cycle.label = 'REV-T sell'
        cycle.fill('strategy-open-{}'.format(runner.lane), quantity,
                   price, True, None)
        runner.cycle = cycle
        runner.execution_book.record(
            'strategy-open-{}'.format(runner.lane), 'REV-T sell',
            -quantity, price)
        runner.st['short_legs'] = [(price, quantity)]
        runner.st['fstate'] = MODULE.STATE_SOLD
        return cycle

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

    def test_high_five_day_return_does_not_block_spike_arm(self):
        portfolio = MODULE.PortfolioRunner(False)
        runner = MODULE.StrategyRunner(portfolio, '601869.SH', lane=0)
        runner._init_state()
        runner.st.update(
            do_short=True,
            base_shares=100,
            base_can_use=100,
            trade_count_short=0,
            ma_completed_closes=[100.0, 100.0, 100.0, 100.0, 100.0, 104.29],
            daily_signal={'sell_trigger': 103.0})

        with patch.object(MODULE.cfg, 'now_hms', return_value='10:00:00'):
            runner._handle_idle(104.0)

        self.assertEqual(MODULE.STATE_SPIKING, runner.st['fstate'])
        self.assertEqual(1, runner.st['trade_count_short'])

    def test_unique_manual_buy_fully_closes_matching_short_cycle(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        self._open_short_cycle(lane1)
        order = SimpleNamespace(
            order_id='manual-buy-1', stock_code='600584.SH',
            order_status=56, order_type=23, traded_volume=100,
            traded_price=67.5, order_time=1789609200)

        changed = portfolio._reconcile_external_activity(
            {'600584.SH': 500}, [order])

        self.assertTrue(changed)
        self.assertIsNone(lane1.cycle)
        self.assertEqual([], lane1.st['short_legs'])
        self.assertEqual(MODULE.STATE_IDLE, lane1.st['fstate'])
        self.assertEqual(50.0, lane1.total_pnl)
        self.assertIn('manual-buy-1', portfolio.external_synced_order_ids)
        self.assertEqual('', lane0.paused_reason)
        self.assertEqual('', lane1.paused_reason)

    def test_ambiguous_manual_buy_keeps_both_lanes_paused(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        self._open_short_cycle(lane0)
        self._open_short_cycle(lane1)
        order = SimpleNamespace(
            order_id='manual-buy-ambiguous', stock_code='600584.SH',
            order_status=56, order_type=23, traded_volume=100,
            traded_price=67.5, order_time=1789609200)

        changed = portfolio._reconcile_external_activity(
            {'600584.SH': 400}, [order])

        self.assertFalse(changed)
        self.assertIsNotNone(lane0.cycle)
        self.assertIsNotNone(lane1.cycle)
        self.assertIn('ambiguous', lane0.paused_reason)
        self.assertIn('ambiguous', lane1.paused_reason)

    def test_manual_trade_with_no_cycle_updates_baseline(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        order = SimpleNamespace(
            order_id='manual-buy-baseline', stock_code='600584.SH',
            order_status=56, order_type=23, traded_volume=100,
            traded_price=67.5, order_time=1789609200)

        changed = portfolio._reconcile_external_activity(
            {'600584.SH': 600}, [order])

        self.assertTrue(changed)
        self.assertEqual(600, lane0.baseline_shares)
        self.assertEqual(600, lane1.baseline_shares)
        self.assertIn('manual-buy-baseline',
                      portfolio.external_synced_order_ids)

    def test_flat_manual_round_trip_is_classified_without_changing_baseline(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        sell = SimpleNamespace(
            order_id='manual-t-sell', stock_code='600584.SH',
            order_status=56, order_type=24, traded_volume=100,
            traded_price=68.0, order_time=1789609200)
        buy = SimpleNamespace(
            order_id='manual-t-buy', stock_code='600584.SH',
            order_status=56, order_type=23, traded_volume=100,
            traded_price=67.5, order_time=1789609260)

        with patch.object(MODULE, '_log') as log:
            changed = portfolio._reconcile_external_activity(
                {'600584.SH': 500}, [sell, buy])

        self.assertTrue(changed)
        self.assertEqual(500, lane0.baseline_shares)
        self.assertEqual(500, lane1.baseline_shares)
        self.assertEqual({'manual-t-sell', 'manual-t-buy'},
                         portfolio.external_synced_order_ids)
        self.assertIn('[MANUAL-T]', log.call_args.args[0])

    def test_multiple_net_flat_manual_orders_are_not_classified_as_manual_t(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        orders = [
            SimpleNamespace(order_id='sell-200', stock_code='600584.SH',
                            order_status=56, order_type=24, traded_volume=200,
                            traded_price=68.0, order_time=1789609200),
            SimpleNamespace(order_id='buy-100-a', stock_code='600584.SH',
                            order_status=56, order_type=23, traded_volume=100,
                            traded_price=67.5, order_time=1789609260),
            SimpleNamespace(order_id='buy-100-b', stock_code='600584.SH',
                            order_status=56, order_type=23, traded_volume=100,
                            traded_price=67.4, order_time=1789609320),
        ]

        changed = portfolio._reconcile_external_activity(
            {'600584.SH': 500}, orders)

        self.assertFalse(changed)
        self.assertEqual(set(), portfolio.external_synced_order_ids)
        self.assertIn('unique T pair', lane0.paused_reason)
        self.assertIn('unique T pair', lane1.paused_reason)

    def test_new_inventory_mismatch_waits_before_pausing(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes(baseline=500)

        with patch.object(MODULE._time, 'monotonic', side_effect=[100.0, 101.0]):
            first = portfolio._reconcile_external_activity(
                {'600584.SH': 600}, [])
            second = portfolio._reconcile_external_activity(
                {'600584.SH': 600}, [])

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual('', lane0.paused_reason)
        self.assertEqual('', lane1.paused_reason)

    def test_inventory_mismatch_pauses_after_reconciliation_grace(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes(baseline=500)

        with patch.object(MODULE._time, 'monotonic', side_effect=[100.0, 109.0]):
            portfolio._reconcile_external_activity({'600584.SH': 600}, [])
            portfolio._reconcile_external_activity({'600584.SH': 600}, [])

        self.assertIn('manual order', lane0.paused_reason)
        self.assertIn('manual order', lane1.paused_reason)

    def test_unfinished_external_order_pauses_only_its_symbol(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        other0 = MODULE.StrategyRunner(portfolio, '601869.SH', lane=0)
        other1 = MODULE.StrategyRunner(portfolio, '601869.SH', lane=1)
        portfolio.runners.update({'601869.SH': other0, '601869.SH#1': other1})
        for runner in (other0, other1):
            runner._init_state()
            runner.baseline_shares = 100
            runner.st.update(initialized=True,
                             trade_date=MODULE.datetime.now().strftime('%Y%m%d'))
        order = SimpleNamespace(
            order_id='manual-pending', stock_code='600584.SH',
            order_status=50, order_type=24, traded_volume=0,
            traded_price=0.0, order_time=1789609200)

        portfolio._reconcile_external_activity(
            {'600584.SH': 500, '601869.SH': 100}, [order])

        self.assertIn('unfinished', lane0.paused_reason)
        self.assertIn('unfinished', lane1.paused_reason)
        self.assertEqual('', other0.paused_reason)
        self.assertEqual('', other1.paused_reason)

    def test_untracked_unfinished_order_conservatively_pauses_all_symbols(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        order = SimpleNamespace(
            order_id='unknown-pending', stock_code='000001.SZ',
            order_status=50, order_type=23, traded_volume=0,
            traded_price=0.0, order_time=1789609200)

        portfolio._reconcile_external_activity({'600584.SH': 500}, [order])

        self.assertIn('outside tracked symbols', lane0.paused_reason)
        self.assertIn('outside tracked symbols', lane1.paused_reason)

    def test_unfinished_strategy_order_remains_globally_blocking(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        portfolio.own_order_ids.add('strategy-pending')
        order = SimpleNamespace(
            order_id='strategy-pending', stock_code='600584.SH',
            order_status=50, order_type=24, traded_volume=0,
            traded_price=0.0, order_time=1789609200)

        with self.assertRaisesRegex(RuntimeError, 'strategy order unfinished'):
            portfolio._reconcile_external_activity({'600584.SH': 500}, [order])

    def test_active_cycle_waits_for_delayed_completed_manual_order(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()
        self._open_short_cycle(lane1)
        order = SimpleNamespace(
            order_id='manual-delayed-buy', stock_code='600584.SH',
            order_status=56, order_type=23, traded_volume=100,
            traded_price=67.5, order_time=1789609200)

        with patch.object(MODULE._time, 'monotonic', return_value=100.0):
            first = portfolio._reconcile_external_activity(
                {'600584.SH': 500}, [])
        second = portfolio._reconcile_external_activity(
            {'600584.SH': 500}, [order])

        self.assertFalse(first)
        self.assertTrue(second)
        self.assertEqual('', lane0.paused_reason)
        self.assertEqual('', lane1.paused_reason)
        self.assertIsNone(lane1.cycle)

    def test_broker_snapshot_records_unfinished_order_without_global_error(self):
        portfolio = MODULE.PortfolioRunner(False)
        position = SimpleNamespace(
            stock_code='600584.SH', volume=500, can_use_volume=500)
        order = SimpleNamespace(
            order_id='manual-pending', stock_code='600584.SH',
            order_status=50, traded_volume=0, traded_price=0.0)
        portfolio.conn._account_obj = object()
        portfolio.conn.trader = SimpleNamespace(
            query_stock_positions=Mock(return_value=[position]),
            query_stock_orders=Mock(return_value=[order]))

        snapshot = portfolio._broker_snapshot()

        self.assertEqual(50, snapshot['orders'][0][2])

    def test_identical_lane_block_log_is_emitted_once(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()

        with patch.object(MODULE, '_log') as log, \
                patch.object(MODULE._time, 'monotonic', side_effect=[100.0, 101.0]):
            lane0._log('[REV-T] BLOCKED test reason | lane 0 plan')
            lane1._log('[REV-T] BLOCKED test reason | lane 1 plan')

        log.assert_called_once()
        self.assertNotIn('[L0]', log.call_args.args[0])

    def test_lane_one_quote_health_file_log_is_suppressed(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes()

        with patch.object(MODULE, '_log_file_only') as file_log:
            lane0._file_log('[QUOTE-HEALTH] FRESH')
            lane1._file_log('[QUOTE-HEALTH] FRESH')

        file_log.assert_called_once()

    def test_new_day_flat_ledger_refreshes_baseline_without_old_order(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes(baseline=500)
        for runner in (lane0, lane1):
            runner.st['trade_date'] = '20260917'
            runner.st['fstate'] = MODULE.STATE_SPIKING

        with patch.object(MODULE, 'datetime') as clock:
            clock.now.return_value.strftime.return_value = '20260918'
            changed = portfolio._reconcile_external_activity(
                {'600584.SH': 1000}, [])

        self.assertTrue(changed)
        self.assertEqual(1000, lane0.baseline_shares)
        self.assertEqual(1000, lane1.baseline_shares)
        self.assertEqual('', lane0.paused_reason)
        self.assertEqual('', lane1.paused_reason)
        MODULE.validate_cycles([], lane0.baseline_shares, 1000)

    def test_same_day_flat_inventory_change_without_order_still_pauses(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes(baseline=500)

        with patch.object(MODULE._time, 'monotonic', side_effect=[100.0, 109.0]):
            portfolio._reconcile_external_activity({'600584.SH': 1000}, [])
            changed = portfolio._reconcile_external_activity(
                {'600584.SH': 1000}, [])

        self.assertFalse(changed)
        self.assertIn('manual order', lane0.paused_reason)
        self.assertIn('manual order', lane1.paused_reason)

    def test_source_fingerprint_contains_path_mtime_and_sha256(self):
        fingerprint = MODULE.source_fingerprint(PATH)

        self.assertEqual(str(PATH.resolve()), fingerprint['path'])
        self.assertTrue(fingerprint['mtime'])
        self.assertEqual(64, len(fingerprint['sha256']))

    def test_new_day_open_cycle_inventory_mismatch_still_pauses(self):
        portfolio, lane0, lane1 = self._portfolio_with_lanes(baseline=500)
        self._open_short_cycle(lane0)
        for runner in (lane0, lane1):
            runner.st['trade_date'] = '20260917'

        with patch.object(MODULE, 'datetime') as clock:
            clock.now.return_value.strftime.return_value = '20260918'
            changed = portfolio._reconcile_external_activity(
                {'600584.SH': 500}, [])

        self.assertFalse(changed)
        self.assertIn('inventory mismatch', lane0.paused_reason)
        self.assertIn('inventory mismatch', lane1.paused_reason)

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

    def test_log_prefix_uses_plain_symbol_and_compact_lane(self):
        portfolio = MODULE.PortfolioRunner(False)
        runner = MODULE.StrategyRunner(portfolio, '601869.SH', lane=0)

        with patch.object(MODULE, '_log') as terminal, \
                patch.object(MODULE, '_log_file_only') as file_only:
            runner._log('visible')
            runner._file_log('quiet')

        terminal.assert_called_once_with('[601869][L0] visible')
        file_only.assert_called_once_with('[601869][L0] quiet')

    def test_lane_one_restore_logs_saved_lane_prefix(self):
        portfolio = MODULE.PortfolioRunner(False)
        original = MODULE.StrategyRunner(portfolio, '601869.SH', lane=1)
        original._init_state()
        record = original.checkpoint_record()
        restored = MODULE.StrategyRunner(portfolio, '601869.SH', lane=0)

        with patch.object(MODULE, '_log') as terminal:
            restored.restore_record(record)

        self.assertEqual(1, restored.lane)
        terminal.assert_any_call(
            '[601869][L1] [STATE-RESTORED] ledger state=IDLE '
            'REV-count=0 FWD-count=0')


if __name__ == "__main__":
    unittest.main()
