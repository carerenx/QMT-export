#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression tests for v35 sell-price-99% rebound buyback."""

import importlib.util
import os
import sys
import unittest


STRATEGY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if STRATEGY_DIR not in sys.path:
    sys.path.insert(0, STRATEGY_DIR)

STRATEGY_PATH = os.path.join(
    STRATEGY_DIR, 'DayTradeing_v35_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v35', STRATEGY_PATH)
V35 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V35)


class _TickContext:
    def __init__(self, ask1):
        self.ask1 = ask1

    def get_full_tick(self, stock_codes):
        return {V35.STOCK_QMT: {'askPrice': [self.ask1]}}


class RevReboundBuybackV35Test(unittest.TestCase):
    def _runner(self, buyback_target=98.0):
        runner = V35.StrategyRunner.__new__(V35.StrategyRunner)
        runner.st = {
            'sell_fill_price': 100.0,
            'buyback_target': buyback_target,
            'sell_elapsed_bars': 0,
            'daily_signal': {'atr_pct': 0.08},
            'ladder_sell_target': 0.0,
            'rebound_99_armed': False,
            'fstate': V35.STATE_SOLD,
        }
        runner.buybacks = []

        def do_buyback(price, reason=''):
            runner.buybacks.append((price, reason))
            return V35.TRADE_LOT_SIZE

        runner._do_buyback = do_buyback
        return runner

    def test_first_touch_of_99_percent_only_arms_condition(self):
        runner = self._runner()

        runner._handle_sold(99.0)

        self.assertTrue(runner.st['rebound_99_armed'])
        self.assertEqual([], runner.buybacks)
        self.assertEqual(V35.STATE_SOLD, runner.st['fstate'])

    def test_rebound_to_99_percent_buys_back(self):
        runner = self._runner()
        runner._handle_sold(99.0)
        runner._handle_sold(98.8)

        runner._handle_sold(99.0)

        self.assertEqual([(99.0, 'REBOUND99')], runner.buybacks)

    def test_original_buyback_target_keeps_priority(self):
        runner = self._runner()
        runner._handle_sold(99.0)

        runner._handle_sold(98.0)

        self.assertEqual([], runner.buybacks)
        self.assertEqual(V35.STATE_DIPPING, runner.st['fstate'])
        self.assertEqual(98.0, runner.st['dip_price'])

    def test_target_above_99_percent_does_not_arm_new_mechanism(self):
        runner = self._runner(buyback_target=99.2)

        runner._handle_sold(99.0)

        self.assertFalse(runner.st['rebound_99_armed'])
        self.assertEqual(V35.STATE_DIPPING, runner.st['fstate'])

    def test_rebound_buyback_price_uses_ask1_fix_limit(self):
        runner = V35.StrategyRunner.__new__(V35.StrategyRunner)
        runner.ctx = _TickContext(99.06)
        calls = []

        def submit(shares, price, label, style='COMPETE'):
            calls.append((shares, price, label, style))
            return 'FILLED', shares

        runner._submit_order = submit

        result = runner._submit_buyback_order(
            V35.TRADE_LOT_SIZE, 99.0, 'REV-T buyback(REBOUND99)')

        self.assertEqual(('FILLED', V35.TRADE_LOT_SIZE), result)
        self.assertEqual(
            [(V35.TRADE_LOT_SIZE, 99.06,
              'REV-T buyback(REBOUND99)', 'FIX')],
            calls)


if __name__ == '__main__':
    unittest.main()
