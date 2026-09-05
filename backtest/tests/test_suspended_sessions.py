"""Regression tests for matching on suspended or missing sessions."""
import unittest

from backtest.engine import BacktestEngine


class _Data:
    def get_value(self, code, field, bar):
        if field == 'tradable':
            return False
        if field == 'close':
            return 10.0
        return None


class SuspendedSessionTest(unittest.TestCase):
    def setUp(self):
        self.engine = BacktestEngine(_Data())
        self.engine._current_bar = 0
        self.engine._current_bar_date = '2026-01-02'
        self.engine.cash = 10_000.0

    def test_buy_is_not_filled_when_session_is_not_tradable(self):
        self.engine._execute_buy(
            {'code': '000001.SZ', 'shares': 100, 'price': 10.0}, None)

        self.assertEqual(10_000.0, self.engine.cash)
        self.assertEqual([], self.engine.trades)

    def test_sell_is_not_filled_when_session_is_not_tradable(self):
        self.engine.positions['000001.SZ'] = {
            'shares': 100, 'entry_price': 10.0,
        }
        self.engine._execute_sell(
            {'code': '000001.SZ', 'shares': 100, 'price': 10.0}, None)

        self.assertEqual(100, self.engine.positions['000001.SZ']['shares'])
        self.assertEqual([], self.engine.trades)
