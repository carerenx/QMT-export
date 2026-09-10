import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import itertools
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'dayt48', ROOT / 'Stragety/MiniQMT_Stragety/DayT/DayT_v48_ExecutionHealth.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.log = patch.object(s, '_log').start()
        self.addCleanup(patch.stopall)
        self.p = s.PortfolioRunner(False)
        self.r = s.StrategyRunner(self.p, '600584.SH')
        self.r._init_state()

    def test_actual_today_fill_prices_reach_state_targets_and_gross(self):
        orders = {
            1: SimpleNamespace(order_id=1, stock_code='600584.SH', traded_volume=100,
                               traded_price=69.98, order_status=56),
            2: SimpleNamespace(order_id=2, stock_code='600584.SH', traded_volume=100,
                               traded_price=69.37, order_status=56)}
        self.p.conn.trader = SimpleNamespace(query_stock_order=lambda account, oid: orders[oid])
        self.r.st.update(daily_signal={'atr_pct': .0637}, peak_price=70.10,
                         base_can_use=500, base_shares=500)
        with patch.object(s, 'order_shares', side_effect=[1, 2]), \
                patch.object(self.r, '_new_t_shares', return_value=100), \
                patch.object(self.r, '_clamp_sell_shares', return_value=100), \
                patch.object(self.r, '_clamp_buy_shares', return_value=100), \
                patch.object(self.r, '_snapshot_account', return_value={}), \
                patch.object(self.r, '_refresh_position'), \
                patch.object(self.r, '_buyback_limit_price', return_value=69.37), \
                patch.object(self.r, '_recalculate_next_t_triggers'), \
                patch.object(self.r, '_maybe_resume_trading'):
            self.r._handle_spiking(70.01)
            self.assertEqual(self.r.st['sell_fill_price'], 69.98)
            self.assertEqual(self.r.st['buyback_target'], round(69.98 * (1 - .0637 * .15), 2))
            self.r.st['dip_price'] = 69.20
            self.r._handle_dipping(69.37)
        self.assertAlmostEqual(self.r.total_pnl, 61.)
        self.assertEqual(self.r.total_t_days, 1)
        self.assertNotIn('', self.p.own_order_ids)

    def test_partial_fifo_and_duplicate_order(self):
        book = s.ExecutionBook()
        book.record(1, 'REV-T sell', -100, 70)
        book.record(2, 'REV-T sell', -100, 72)
        first = book.record(3, 'REV-T buyback(NORMAL)', 150, 69)
        second = book.record(4, 'REV-T force buyback', 50, 68)
        self.assertEqual(first, (250, False, 250))
        self.assertEqual(second, (200, True, 450))
        with self.assertRaises(ValueError):
            book.record(4, 'REV-T force buyback', 50, 68)

    def test_unresolved_order_is_cancelled_once_and_stops_account_orders(self):
        self.p.conn.trader = SimpleNamespace(query_stock_order=lambda *args: None)
        self.r._submitted_order_id = 123
        with patch.object(s._time, 'monotonic', side_effect=itertools.count().__next__), \
                patch.object(s._time, 'sleep'), \
                patch.object(self.p.conn, 'cancel_order') as cancel:
            with self.assertRaises(RuntimeError):
                self.r._wait_for_fill({}, -100, 'REV-T sell', 70., -100, timeout_sec=0)
            cancel.assert_called_once_with(123)
        self.assertTrue(self.p.order_uncertain)
        self.assertTrue(self.r._new_leg_block_reason())

    def test_terminal_partial_order_records_only_executed_quantity(self):
        order = SimpleNamespace(order_id=123, stock_code='600584.SH', traded_volume=100,
                                traded_price=69.98, order_status=53)
        self.p.conn.trader = SimpleNamespace(query_stock_order=lambda *args: order)
        self.r._submitted_order_id = 123
        with patch.object(self.r, '_refresh_position'):
            self.assertEqual(self.r._wait_for_fill({}, -200, 'REV-T sell', 70., -200),
                             ('PARTIAL', -100))
        self.assertEqual(self.r.execution_book.legs['SHORT'], [(69.98, 100)])

    def test_capacity_refresh_disables_old_short_permission(self):
        self.r.st.update(do_short=True, do_long=True, base_can_use=0,
                         daily_signal={'short_signal_allowed': True})
        with patch.object(self.r, '_refresh_position'), \
                patch.object(self.r, '_cur_price', return_value=442.), \
                patch.object(self.p.conn, 'query_account', return_value=SimpleNamespace(cash=10000.)):
            self.r._refresh_execution_capacity(force=True)
        self.assertFalse(self.r.st['do_short'])
        self.assertFalse(self.r.st['do_long'])
        self.assertIn('0 sh', self.r.st['daily_signal']['short_reason'])

    def test_quantile_atr_is_the_same_as_signal_atr(self):
        closes = [100. + (i % 7) for i in range(80)]
        signal = dict(atr_pct=.9, do_short=True)
        result = s.apply_quantile_trend_regime(
            signal, closes, [x + 3 for x in closes], [x - 2 for x in closes],
            closes, [1000.] * 80, 100.)
        self.assertEqual(result['atr_pct'], result['quantile_trend']['atr_pct'])
        self.assertAlmostEqual(result['sell_trigger'],
                               100 * (1 + result['atr_pct'] * result['trigger_units']), delta=.02)

    def test_watchdog_can_report_blocked_call_without_return(self):
        clock = [0.]
        messages = []
        watchdog = s.RuntimeWatchdog(messages.append, 15., lambda: clock[0])
        with watchdog.scope('RPC query_stock_asset'):
            clock[0] = 16.
            self.assertTrue(watchdog.check())
        self.assertIn('WATCHDOG-STALL', messages[0])
        self.assertIn('CALL-SLOW', messages[1])

    def test_query_timeout_instrumentation_does_not_retry_orders(self):
        calls = []
        client = SimpleNamespace(call=lambda *args, **kw: calls.append((args, kw)))
        watchdog = s.RuntimeWatchdog(lambda _: None)
        s.instrument_rpc(client, watchdog, 5.)
        client.call('query_stock_positions')
        client.call('order_stock')
        self.assertEqual(calls[0][1]['timeout_seconds'], 5.)
        self.assertIsNone(calls[1][1]['timeout_seconds'])
        self.assertEqual(len(calls), 2)

    def test_external_trade_is_logged_but_not_booked_as_t(self):
        trades = []
        self.p.own_order_ids.update(('1', '2'))
        self.p.conn.trader = SimpleNamespace(query_stock_trades=lambda account: trades)
        self.p._audit_account_trades()
        trades.append(SimpleNamespace(order_id='manual', stock_code='600584.SH',
                                       traded_id='t1', traded_volume=400, traded_price=69.21))
        self.p._audit_account_trades()
        self.p._audit_account_trades()
        messages = [x.args[0] for x in self.log.call_args_list]
        self.assertEqual(sum('ACCOUNT-UNATTRIBUTED' in x for x in messages), 1)
        self.assertEqual(self.r.total_pnl, 0.)


if __name__ == '__main__':
    unittest.main()
