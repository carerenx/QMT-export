#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression tests for v31 FIX-priced MOM/REV-T buybacks."""

import importlib.util
import os
import sys
import unittest


STRATEGY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if STRATEGY_DIR not in sys.path:
    sys.path.insert(0, STRATEGY_DIR)

STRATEGY_PATH = os.path.join(STRATEGY_DIR, 'DayTradeing_v31_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v31', STRATEGY_PATH)
V31 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V31)


class _FakeContext:
    def __init__(self, ask_prices):
        self.ask_prices = ask_prices

    def get_full_tick(self, codes):
        return {V31.STOCK_QMT: {'askPrice': self.ask_prices}}


class BuybackFixPricingTest(unittest.TestCase):
    def _runner(self, ask_prices):
        runner = V31.StrategyRunner.__new__(V31.StrategyRunner)
        runner.ctx = _FakeContext(ask_prices)
        runner._clamp_buy_shares = lambda planned, price: planned
        runner._clamp_sell_shares = lambda planned: planned
        runner._snapshot_account = lambda: {'shares': 200, 'cash': 39001.0}
        runner._wait_for_fill = lambda *args, **kwargs: ('FILLED', args[1])
        return runner

    def test_limit_uses_ask1_without_upper_limit_overrun(self):
        runner = self._runner([368.00, 368.10])

        self.assertAlmostEqual(368.00, runner._buyback_limit_price(367.50))

    def test_limit_falls_back_to_trigger_price_when_ask_is_missing(self):
        runner = self._runner([])

        self.assertAlmostEqual(367.50, runner._buyback_limit_price(367.50))

    def test_buyback_submits_explicit_fix_price(self):
        runner = self._runner([368.00])
        calls = []
        original = V31.order_shares
        V31.order_shares = lambda *args: calls.append(args)
        try:
            status, delta = runner._submit_buyback_order(100, 367.50, 'MOM buyback')
        finally:
            V31.order_shares = original

        self.assertEqual(('FILLED', 100), (status, delta))
        self.assertEqual('FIX', calls[0][2])
        self.assertAlmostEqual(368.00, calls[0][3])
        self.assertAlmostEqual(368.00, runner._last_buyback_price)

    def test_incident_cash_can_cover_fix_limit(self):
        runner = self._runner([368.00])
        runner._available_cash = lambda: 39001.0

        self.assertEqual(100, V31.StrategyRunner._clamp_buy_shares(runner, 100, 368.00))

    def test_non_buyback_order_keeps_compete_style(self):
        runner = self._runner([368.00])
        calls = []
        original = V31.order_shares
        V31.order_shares = lambda *args: calls.append(args)
        try:
            runner._submit_order(-100, 368.00, 'REV-T sell')
        finally:
            V31.order_shares = original

        self.assertEqual('COMPETE', calls[0][2])


if __name__ == '__main__':
    unittest.main()
