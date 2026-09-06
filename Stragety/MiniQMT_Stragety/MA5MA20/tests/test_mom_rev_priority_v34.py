#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression tests for v34 bounded MOM/REV-T priority and adaptive pullback."""

import importlib.util
import os
import sys
import unittest


MINIQMT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STRATEGY_DIR = os.path.join(MINIQMT_ROOT, 'DayT')
for import_root in (MINIQMT_ROOT, STRATEGY_DIR):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

STRATEGY_PATH = os.path.join(STRATEGY_DIR, 'DayTradeing_v34_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v34', STRATEGY_PATH)
V34 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V34)


class MomRevPriorityV34Test(unittest.TestCase):
    def setUp(self):
        self.original_priority = V34.MOM_REV_PRIORITY_ENABLED
        self.original_adaptive = V34.MOM_ADAPTIVE_PULLBACK_ENABLED

    def tearDown(self):
        V34.MOM_REV_PRIORITY_ENABLED = self.original_priority
        V34.MOM_ADAPTIVE_PULLBACK_ENABLED = self.original_adaptive

    def _runner(self, pullback_pct=0.0035):
        runner = V34.StrategyRunner.__new__(V34.StrategyRunner)
        runner.st = {
            'do_short': True,
            'daily_signal': {'sell_trigger': 364.82},
            'fstate': V34.STATE_IDLE,
            'mom_state': 'MOM_SPIKING',
            'mom_peak': 362.03,
            'mom_pullback_pct': pullback_pct,
            'mom_rev_yield_trigger': 0.0,
            'mom_leg_shares': 0,
            'mom_trade_count': 0,
            'mom_atr_history': V34.deque(),
        }
        runner.orders = []

        def submit(shares, price, label):
            runner.orders.append((shares, price, label))
            return 'FILLED', shares

        runner._submit_order = submit
        return runner

    def test_adaptive_pullback_filters_incident_micro_pullback(self):
        V34.MOM_ADAPTIVE_PULLBACK_ENABLED = True
        V34.MOM_REV_PRIORITY_ENABLED = False
        runner = self._runner(pullback_pct=0.0035)

        runner._mom_handle_spiking(361.12)  # 362.03 peak -> 0.25% pullback

        self.assertEqual([], runner.orders)
        self.assertEqual('MOM_SPIKING', runner.st['mom_state'])

    def test_adaptive_pullback_uses_recent_minute_range_atr(self):
        V34.MOM_ADAPTIVE_PULLBACK_ENABLED = True
        runner = self._runner(pullback_pct=0.0)
        runner.st['mom_atr_history'] = V34.deque([
            (0.0, 100.0), (1.0, 100.6),
            (60.0, 100.0), (61.0, 101.0),
        ])

        # Median minute range=(0.6+1.0)/2=0.8; 50% / reference 100 = 0.40%.
        self.assertAlmostEqual(0.004, runner._mom_pullback_threshold(100.0))

    def test_adaptive_switch_off_restores_legacy_point_one_percent(self):
        V34.MOM_ADAPTIVE_PULLBACK_ENABLED = False
        V34.MOM_REV_PRIORITY_ENABLED = False
        runner = self._runner(pullback_pct=0.0035)

        runner._mom_handle_spiking(361.12)

        self.assertEqual(1, len(runner.orders))
        self.assertEqual('MOM_SOLD', runner.st['mom_state'])

    def test_rev_priority_yields_instead_of_selling(self):
        V34.MOM_ADAPTIVE_PULLBACK_ENABLED = False
        V34.MOM_REV_PRIORITY_ENABLED = True
        runner = self._runner()

        runner._mom_handle_spiking(361.12)

        self.assertEqual([], runner.orders)
        self.assertEqual(V34.MOM_STATE_REV_YIELD, runner.st['mom_state'])
        self.assertAlmostEqual(364.82, runner.st['mom_rev_yield_trigger'])

    def test_priority_switch_off_allows_mom_sell_in_priority_zone(self):
        V34.MOM_ADAPTIVE_PULLBACK_ENABLED = False
        V34.MOM_REV_PRIORITY_ENABLED = False
        runner = self._runner()

        runner._mom_handle_spiking(361.12)

        self.assertEqual(1, len(runner.orders))
        self.assertEqual('MOM_SOLD', runner.st['mom_state'])

    def test_incident_sequence_yields_as_soon_as_it_enters_priority_zone(self):
        V34.MOM_ADAPTIVE_PULLBACK_ENABLED = True
        V34.MOM_REV_PRIORITY_ENABLED = True
        runner = self._runner(pullback_pct=0.0035)

        runner._mom_handle_spiking(361.12)

        self.assertEqual([], runner.orders)
        self.assertEqual(V34.MOM_STATE_REV_YIELD, runner.st['mom_state'])

    def test_rev_keeps_priority_after_arming_while_price_is_below_trigger(self):
        V34.MOM_REV_PRIORITY_ENABLED = True
        runner = self._runner()
        runner.st['fstate'] = V34.STATE_SPIKING

        runner._mom_handle_spiking(359.00)

        self.assertEqual([], runner.orders)
        self.assertEqual(V34.MOM_STATE_REV_YIELD, runner.st['mom_state'])

    def test_mom_does_not_yield_at_or_above_rev_sell_trigger(self):
        V34.MOM_REV_PRIORITY_ENABLED = True
        for main_state in (V34.STATE_SPIKING, V34.STATE_SOLD, V34.STATE_DIPPING):
            with self.subTest(main_state=main_state):
                runner = self._runner()
                runner.st['fstate'] = main_state
                self.assertFalse(runner._mom_should_yield_to_rev(364.82))
                self.assertFalse(runner._mom_should_yield_to_rev(365.00))

    def test_yield_state_releases_only_after_price_exits_zone(self):
        runner = self._runner()
        runner.st['mom_state'] = V34.MOM_STATE_REV_YIELD
        runner.st['mom_rev_yield_trigger'] = 364.82

        runner._mom_handle_rev_yield(362.00)
        self.assertEqual(V34.MOM_STATE_REV_YIELD, runner.st['mom_state'])

        runner._mom_handle_rev_yield(360.00)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])

    def test_yield_state_releases_at_rev_sell_trigger(self):
        for main_state in (V34.STATE_SPIKING, V34.STATE_SOLD, V34.STATE_DIPPING):
            with self.subTest(main_state=main_state):
                runner = self._runner()
                runner.st['mom_state'] = V34.MOM_STATE_REV_YIELD
                runner.st['mom_rev_yield_trigger'] = 364.82
                runner.st['fstate'] = main_state

                runner._mom_handle_rev_yield(364.82)

                self.assertEqual('MOM_IDLE', runner.st['mom_state'])
                self.assertEqual(0.0, runner.st['mom_rev_yield_trigger'])


if __name__ == '__main__':
    unittest.main()
