#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression tests for v37 decreasing MOM cooldown durations."""

import importlib.util
import os
import sys
import unittest


MINIQMT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STRATEGY_DIR = os.path.join(MINIQMT_ROOT, 'DayT')
for import_root in (MINIQMT_ROOT, STRATEGY_DIR):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

STRATEGY_PATH = os.path.join(
    STRATEGY_DIR, 'DayTradeing_v37_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v37', STRATEGY_PATH)
V37 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V37)


class MomDecreasingCooldownV37Test(unittest.TestCase):
    def _runner(self):
        runner = V37.StrategyRunner.__new__(V37.StrategyRunner)
        runner.st = {
            'mom_state': 'MOM_IDLE',
            'mom_cooldown_until': 0.0,
            'mom_cooldown_trigger': 0.0,
            'mom_cooldown_cycles': 0,
            'mom_cooldown_duration': 0.0,
            'mom_sell_price': 100.0,
            'mom_buy_price': 100.0,
            'mom_leg_shares': V37.MOM_LOT_SIZE,
        }
        runner.orders = []
        runner.buybacks = []
        runner.total_t_days = 0
        runner.total_pnl = 0.0

        def submit(shares, price, label, style='COMPETE'):
            runner.orders.append((shares, price, label, style))
            return 'FILLED', shares

        def buyback(shares, price, label):
            runner.buybacks.append((shares, price, label))
            runner._last_buyback_price = price
            return 'FILLED', shares

        runner._submit_order = submit
        runner._submit_buyback_order = buyback
        return runner

    def test_cooldown_sequence_decreases_by_two_seconds(self):
        runner = self._runner()
        durations = []
        now = 100.0

        for _ in range(7):
            runner._mom_start_close_cooldown(
                V37.MOM_STATE_SELLBACK_COOLING,
                101.8, 102.0, now, 'sellback')
            durations.append(runner.st['mom_cooldown_duration'])
            self.assertEqual(
                now + runner.st['mom_cooldown_duration'],
                runner.st['mom_cooldown_until'])
            now = runner.st['mom_cooldown_until']

        self.assertEqual([12.0, 10.0, 8.0, 6.0, 4.0, 2.0, 2.0], durations)

    def test_sellback_extension_uses_next_ten_second_window(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V37.MOM_STATE_SELLBACK_COOLING,
            'mom_cooldown_until': 112.0,
            'mom_cooldown_trigger': 101.8,
            'mom_cooldown_cycles': 1,
            'mom_cooldown_duration': 12.0,
        })

        runner._mom_handle_sellback_cooling(102.0, now_ts=112.0)

        self.assertEqual(2, runner.st['mom_cooldown_cycles'])
        self.assertEqual(10.0, runner.st['mom_cooldown_duration'])
        self.assertEqual(122.0, runner.st['mom_cooldown_until'])
        self.assertEqual([], runner.orders)

    def test_buyback_extension_uses_next_ten_second_window(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V37.MOM_STATE_BUYBACK_COOLING,
            'mom_cooldown_until': 212.0,
            'mom_cooldown_trigger': 98.5,
            'mom_cooldown_cycles': 1,
            'mom_cooldown_duration': 12.0,
        })

        runner._mom_handle_buyback_cooling(98.0, now_ts=212.0)

        self.assertEqual(2, runner.st['mom_cooldown_cycles'])
        self.assertEqual(10.0, runner.st['mom_cooldown_duration'])
        self.assertEqual(222.0, runner.st['mom_cooldown_until'])
        self.assertEqual([], runner.buybacks)

    def test_success_clears_cooldown_sequence(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V37.MOM_STATE_SELLBACK_COOLING,
            'mom_cooldown_until': 112.0,
            'mom_cooldown_trigger': 101.8,
            'mom_cooldown_cycles': 1,
            'mom_cooldown_duration': 12.0,
        })

        runner._mom_handle_sellback_cooling(101.0, now_ts=112.0)

        self.assertEqual('MOM_IDLE', runner.st['mom_state'])
        self.assertEqual(0, runner.st['mom_cooldown_cycles'])
        self.assertEqual(0.0, runner.st['mom_cooldown_duration'])
        self.assertEqual(0.0, runner.st['mom_cooldown_until'])


if __name__ == '__main__':
    unittest.main()
