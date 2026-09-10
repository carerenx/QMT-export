import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from collections import deque
import test_dayt_v48_health as previous_health
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
name = os.environ.get('DAYT_REGRESSION_SOURCE', 'DayT_v49_StateRecovery.py')
spec = importlib.util.spec_from_file_location('dayt49', ROOT / 'Stragety/MiniQMT_Stragety/DayT' / name)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class V49ExecutionHealthTests(previous_health.HealthTests):
    """Exercise every previous fill/accounting regression against the new strategy."""
    def setUp(self):
        patch.object(previous_health, 's', s).start()
        super().setUp()


class ExitDispatchTests(unittest.TestCase):
    def test_existing_exits_run_without_entry_capacity(self):
        for state, handler in ((s.STATE_SOLD, '_handle_sold'),
                               (s.STATE_DIPPING, '_handle_dipping'),
                               (s.STATE_BT_BOUGHT, '_handle_bt_bought'),
                               (s.STATE_BT_SPIKING, '_handle_bt_spiking')):
            for account_failed in (False, True):
                with self.subTest(state=state, account_failed=account_failed):
                    self.check_dispatch(state, handler, account_failed)

    def check_dispatch(self, state, handler, account_failed):
        p = s.PortfolioRunner(False)
        r = s.StrategyRunner(p, '601869.SH')
        p.runners[r.stock_qmt] = r
        def initialize():
            r.st.update(fstate=state, short_legs=[(449., 100)],
                        sell_fill_price=449., buyback_target=444.5,
                        base_can_use=0, base_shares=0,
                        daily_signal={'short_signal_allowed': True, 'do_short': True,
                                      'open_price': 430., 'atr_pct': .065},
                        do_short=True, do_long=False, _market_open_logged=True,
                        trade_count_short=s.cfg.MAX_DAILY_TRADES,
                        trade_count_long=s.cfg.MAX_DAILY_TRADES)
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(s, '_log'))
            stack.enter_context(patch.object(s.cfg, 'now_hms', return_value='10:00:00'))
            stack.enter_context(patch.object(s.cfg, 'is_market_open', return_value=True))
            stack.enter_context(patch.object(s, 'MOM_ENABLED', False))
            stack.enter_context(patch.object(r, '_daily_init', side_effect=initialize))
            for method in ('_print_daily_brief', '_monitor_connections', '_retry_atr_reentry',
                           '_refresh_position', '_update_intraday_average', '_update_limit_up_guard',
                           '_heartbeat'):
                stack.enter_context(patch.object(r, method))
            stack.enter_context(patch.object(r, '_cur_price', return_value=442.))
            stack.enter_context(patch.object(p.conn, 'query_account',
                                return_value=None if account_failed else SimpleNamespace(cash=45000.)))
            stack.enter_context(patch.object(r.ctx, 'get_full_tick',
                                return_value={r.stock_qmt: {'lastPrice': 442.}}))
            called = stack.enter_context(patch.object(r, handler))
            task = r.run()
            try:
                next(task)
                self.assertFalse(r.st['do_short'])
                self.assertFalse(r.st['do_long'])
                called.assert_called_once_with(442.)
            finally:
                task.close()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        patch.object(s, 'STATE_FILE', str(self.path)).start()
        patch.object(s, '_log').start()
        self.addCleanup(patch.stopall)
        self.p = s.PortfolioRunner(False)
        self.positions = []
        self.orders = []
        self.trader = SimpleNamespace(query_stock_positions=lambda _: self.positions,
                                      query_stock_orders=lambda _: self.orders)
        self.p.conn.trader = self.trader
        self.p.restore_checkpoint()
        self.r = s.StrategyRunner(self.p, '601869.SH')
        self.r._init_state()
        self.r.st.update(initialized=True, trade_date=s.datetime.now().strftime('%Y%m%d'),
                         fstate=s.STATE_SOLD, short_legs=[(449., 100)],
                         trade_count_short=3, daily_signal={'atr_pct': .065, 'open_price': 430.},
                         reentry_history=s.pd.DataFrame({'open': [430.], 'high': [450.]}))
        self.r.execution_book.record(1, 'REV-T sell', -100, 449.)
        self.p.runners[self.r.stock_qmt] = self.r
        self.p.own_order_ids.add('1')

    def restored_portfolio(self):
        result = s.PortfolioRunner(False)
        result.conn.trader = self.trader
        result.restore_checkpoint()
        return result

    def test_zero_holding_leg_counts_book_and_dataframe_restore(self):
        self.p.save_checkpoint(force=True)
        restored = self.restored_portfolio()
        r = restored.runners['601869.SH']
        self.assertEqual(r.st['trade_count_short'], 3)
        self.assertEqual(r.st['fstate'], s.STATE_SOLD)
        self.assertIsInstance(r.st['price_history'], deque)
        self.assertAlmostEqual(restored.reserved_cash(''), 45349.)
        self.assertEqual(r.st['reentry_history']['high'].iloc[0], 450.)
        gross, completed, _ = r.execution_book.record(2, 'REV-T buyback', 100, 440.)
        self.assertEqual(gross, 900.)
        self.assertTrue(completed)
        self.assertIn('601869.SH', restored.tasks)

    def test_mixed_date_flat_record_is_reinitialized_without_resetting_today(self):
        self.p.save_checkpoint(force=True)
        saved = s.read_checkpoint(self.path, s.ACCOUNT)
        import copy
        old = copy.deepcopy(saved['runners']['601869.SH'])
        old['state'].update(trade_date='20200101', fstate=s.STATE_IDLE, short_legs=[])
        old['legs'] = {}
        saved['runners']['600584.SH'] = old
        s.write_checkpoint(self.path, saved)
        p = self.restored_portfolio()
        self.assertFalse(p.runners['600584.SH'].st['initialized'])
        self.assertEqual(p.runners['601869.SH'].st['trade_count_short'], 3)

    def test_mixed_date_open_record_blocks_with_symbol_and_dates(self):
        self.p.save_checkpoint(force=True)
        saved = s.read_checkpoint(self.path, s.ACCOUNT)
        saved['runners']['601869.SH']['state']['trade_date'] = '20200101'
        s.write_checkpoint(self.path, saved)
        with self.assertRaisesRegex(RuntimeError, '601869.SH.*20200101.*overnight'):
            self.restored_portfolio()

    def test_save_rejects_mixed_dates_without_overwriting(self):
        self.p.save_checkpoint(force=True)
        before = self.path.read_bytes()
        self.r.st['trade_date'] = '20200101'
        with self.assertRaisesRegex(RuntimeError, '601869.SH.*20200101'):
            self.p.save_checkpoint(force=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_running_portfolio_rolls_all_symbols_before_save(self):
        self.r.st.update(short_legs=[], fstate=s.STATE_IDLE, trade_date='20200101')
        self.r.execution_book = s.ExecutionBook()
        second = s.StrategyRunner(self.p, '600584.SH')
        second._init_state()
        second.st.update(initialized=True, trade_date='20200101')
        self.p.runners[second.stock_qmt] = second
        visited = []
        def initialize(runner):
            visited.append(runner.stock_qmt)
            runner.st.update(initialized=True, trade_date=s.datetime.now().strftime('%Y%m%d'))
        with patch.object(s.StrategyRunner, '_daily_init', initialize):
            self.p._prepare_trading_day()
        self.p.save_checkpoint(force=True)
        saved = s.read_checkpoint(self.path, s.ACCOUNT)
        self.assertEqual(set(visited), {'601869.SH', '600584.SH'})
        self.assertTrue(all(r['state']['trade_date'] == saved['date']
                            for r in saved['runners'].values()))

    def test_rollover_failure_preserves_checkpoint(self):
        self.p.save_checkpoint(force=True)
        before = self.path.read_bytes()
        self.r.st.update(short_legs=[], fstate=s.STATE_IDLE, trade_date='20200101')
        self.r.execution_book = s.ExecutionBook()
        with patch.object(self.r, '_daily_init', side_effect=RuntimeError('data unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'data unavailable'):
                self.p._prepare_trading_day()
        with self.assertRaisesRegex(RuntimeError, 'cannot checkpoint'):
            self.p.save_checkpoint(force=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_rollover_checks_open_legs_before_resetting_any_symbol(self):
        self.r.st['trade_date'] = '20200101'
        with patch.object(self.r, '_init_state') as reset:
            with self.assertRaisesRegex(RuntimeError, '601869.SH.*overnight'):
                self.p._prepare_trading_day()
            reset.assert_not_called()

    def test_inflight_blocks_restart_and_preserves_file(self):
        self.p.before_submit('601869.SH', 'REV-T buyback', 100, 440.)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.restored_portfolio()
        self.assertEqual(self.path.read_bytes(), before)

    def test_complete_transition_clears_marker(self):
        self.p.before_submit('601869.SH', 'REV-T buyback', 100, 440.)
        self.p.save_checkpoint(settled=True)
        self.assertIsNone(s.read_checkpoint(self.path, s.ACCOUNT)['inflight'])
        self.restored_portfolio()

    def test_uncertain_transition_never_clears_marker(self):
        self.p.before_submit('601869.SH', 'REV-T buyback', 100, 440.)
        self.p.order_uncertain = True
        self.p.save_checkpoint(settled=True)
        self.assertIsNotNone(s.read_checkpoint(self.path, s.ACCOUNT)['inflight'])
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            self.restored_portfolio()

    def test_restored_runner_does_not_reset_counts_on_first_tick(self):
        self.p.save_checkpoint(force=True)
        r = self.restored_portfolio().runners['601869.SH']
        from contextlib import ExitStack
        with ExitStack() as stack:
            for method in ('_daily_init', '_print_daily_brief', '_monitor_connections',
                           '_retry_atr_reentry', '_refresh_execution_capacity'):
                stack.enter_context(patch.object(r, method))
            stack.enter_context(patch.object(s.cfg, 'now_hms', return_value='17:00:00'))
            stack.enter_context(patch.object(s.cfg, 'is_market_open', return_value=False))
            task = r.run()
            try:
                next(task)
                self.assertEqual(r.st['trade_count_short'], 3)
                self.assertEqual(r.st['fstate'], s.STATE_SOLD)
                self.assertEqual(r.st['short_legs'][0][1], 100)
            finally:
                task.close()

    def test_broker_change_blocks_restart(self):
        self.p.save_checkpoint(force=True)
        self.positions.append(SimpleNamespace(stock_code='601869.SH', volume=100, can_use_volume=0))
        with self.assertRaisesRegex(RuntimeError, 'positions/orders changed'):
            self.restored_portfolio()

    def test_pending_broker_order_blocks_even_first_start(self):
        self.orders.append(SimpleNamespace(order_status=50))
        with self.assertRaisesRegex(RuntimeError, 'unfinished orders'):
            self.restored_portfolio()

    def test_query_failure_blocks_start(self):
        self.positions = None
        with self.assertRaisesRegex(RuntimeError, 'query unavailable'):
            self.restored_portfolio()

    def test_corrupt_or_wrong_account_is_not_silently_reset(self):
        s.write_checkpoint(self.path, {'schema': 1, 'account': 'wrong'})
        with self.assertRaises(ValueError):
            self.restored_portfolio()

    def test_overnight_open_leg_is_not_reset(self):
        self.p.save_checkpoint(force=True)
        saved = s.read_checkpoint(self.path, s.ACCOUNT)
        saved['date'] = '20200101'
        s.write_checkpoint(self.path, saved)
        with self.assertRaisesRegex(RuntimeError, 'overnight'):
            self.restored_portfolio()

    def test_failed_save_prevents_broker_submission(self):
        with patch.object(s, 'write_checkpoint', side_effect=OSError('disk full')), \
                patch.object(s, 'order_shares') as submit, \
                patch.object(self.r, '_snapshot_account', return_value={}), \
                patch.object(self.r, '_clamp_buy_shares', return_value=100):
            with self.assertRaises(OSError):
                self.r._submit_order(100, 440., 'REV-T buyback')
            submit.assert_not_called()

    def test_signal_mode_does_not_touch_live_checkpoint(self):
        self.p.save_checkpoint(force=True)
        before = self.path.read_bytes()
        p = s.PortfolioRunner(True)
        p.restore_checkpoint()
        p.save_checkpoint(force=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_scheduler_initializes_all_symbols_before_first_checkpoint(self):
        p = s.PortfolioRunner(False)
        p.conn.trader = self.trader
        p.conn._account_obj = SimpleNamespace(account_id=str(s.ACCOUNT))
        self.positions.extend([
            SimpleNamespace(stock_code=code, volume=100, can_use_volume=100)
            for code in ('601869.SH', '600584.SH')])
        def initialize(runner):
            runner.st.update(initialized=True, trade_date=s.datetime.now().strftime('%Y%m%d'))
        def tick(runner):
            yield .5
        with patch.object(p.conn, 'connect_data', return_value=True), \
                patch.object(p.conn, 'connect_trade', return_value=True), \
                patch.object(p.conn, 'disconnect'), \
                patch.object(s.StrategyRunner, '_daily_init', initialize), \
                patch.object(s.StrategyRunner, 'run', tick), \
                patch.object(s._time, 'sleep', side_effect=KeyboardInterrupt):
            p.run()
        saved = s.read_checkpoint(self.path, s.ACCOUNT)
        self.assertEqual(set(saved['runners']), {'601869.SH', '600584.SH'})
        self.assertFalse(p.order_uncertain)


if __name__ == '__main__':
    unittest.main()
