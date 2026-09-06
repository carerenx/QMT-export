#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Execution-capacity and limit-up guard behavior for v39."""

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
    STRATEGY_DIR, 'DayTradeing_v39_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v39', STRATEGY_PATH)
V39 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V39)


class ExecutionGuardsV39Test(unittest.TestCase):
    def test_zero_sellable_shares_disable_both_daily_directions(self):
        capacity = V39.calculate_execution_capacity(
            base_can_use=0, available_cash=75491.0,
            price=426.82, max_daily_trades=5)

        self.assertEqual(0, capacity['short_lots'])
        self.assertEqual(0, capacity['long_lots'])
        self.assertFalse(capacity['can_short'])
        self.assertFalse(capacity['can_long'])
        self.assertEqual('sellable 0 sh < 100 sh', capacity['short_reason'])
        self.assertEqual(
            'T+1: sellable base shares 0 sh < 100 sh',
            capacity['long_reason'])

    def test_limit_up_guard_locks_immediately_and_releases_after_hold(self):
        active, release_since, event = V39.limit_up_guard_transition(
            active=False, release_since=0.0,
            price=426.82, last_close=388.02, now_ts=100.0)
        self.assertTrue(active)
        self.assertEqual(0.0, release_since)
        self.assertEqual('LOCK', event)

        active, release_since, event = V39.limit_up_guard_transition(
            active=active, release_since=release_since,
            price=419.06, last_close=388.02, now_ts=200.0)
        self.assertTrue(active)
        self.assertEqual(200.0, release_since)
        self.assertEqual('RELEASE_PENDING', event)

        active, release_since, event = V39.limit_up_guard_transition(
            active=active, release_since=release_since,
            price=419.06, last_close=388.02, now_ts=320.0)
        self.assertFalse(active)
        self.assertEqual(0.0, release_since)
        self.assertEqual('UNLOCK', event)

    def test_mom_long_does_not_arm_without_sellable_base_shares(self):
        original_now_hms = V39.cfg.now_hms
        V39.cfg.now_hms = lambda: '10:00:00'
        self.addCleanup(setattr, V39.cfg, 'now_hms', original_now_hms)
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'mom_state': 'MOM_IDLE',
            'mom_trade_count': 0,
            'mom_trigger_pct': 0.01,
            'base_can_use': 0,
            'limit_up_guard': False,
            'locked': False,
            'mom_last_block_reason': '',
        }
        runner._mom_detect = lambda price: 'DOWN'
        runner._refresh_position = lambda: None
        runner._available_cash = lambda: 75491.0

        runner._mom_handle_idle(400.0)

        self.assertEqual('MOM_IDLE', runner.st['mom_state'])
        self.assertEqual(0.0, runner.st.get('mom_dip', 0.0))
        self.assertIn('sellable base shares',
                      runner.st['mom_last_block_reason'])

    def test_limit_up_guard_prevents_main_state_from_arming(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'fstate': V39.STATE_IDLE,
            'daily_signal': {'sell_trigger': 400.72, 'buy_trigger': 418.28},
            'do_short': True,
            'do_long': True,
            'base_shares': 100,
            'base_can_use': 100,
            'limit_up_guard': True,
            'locked': False,
        }

        runner._handle_idle(426.82)

        self.assertEqual(V39.STATE_IDLE, runner.st['fstate'])

    def test_open_long_leg_reserves_sellable_shares_from_mom(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'base_can_use': 100,
            'long_legs': [(390.0, 100)],
            'mom_state': 'MOM_IDLE',
            'mom_leg_shares': 0,
        }
        runner._refresh_position = lambda: None
        runner._available_cash = lambda: 100000.0

        capacity = runner._paired_long_capacity(400.0)

        self.assertFalse(capacity['can_long'])
        self.assertEqual(100, capacity['reserved_long_shares'])
        self.assertEqual(0, capacity['pairing_shares'])

    def test_sell_order_refreshes_even_when_cached_sellable_is_positive(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {'base_can_use': 100, 'base_shares': 100}
        original = V39.get_trade_detail_data
        V39.get_trade_detail_data = lambda *args: []
        try:
            actual = runner._clamp_sell_shares(100)
        finally:
            V39.get_trade_detail_data = original

        self.assertEqual(0, actual)
        self.assertEqual(0, runner.st['base_can_use'])

    def test_mom_rechecks_pairing_capacity_before_actual_buy(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'mom_state': 'MOM_BT_DIPPING',
            'mom_dip': 100.0,
            'mom_last_block_reason': '',
        }
        runner.orders = []
        runner._paired_long_capacity = lambda price: {
            'can_long': False,
            'long_reason': 'T+1 pairing consumed by another leg',
        }
        runner._submit_order = lambda *args, **kwargs: runner.orders.append(args)

        runner._mom_handle_bt_dipping(100.2)

        self.assertEqual([], runner.orders)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])

    def test_main_fwd_rechecks_pairing_capacity_before_actual_buy(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'fstate': V39.STATE_BT_DIPPING,
            'bt_dip_price': 100.0,
            'trade_count_long': 1,
            'long_legs': [],
        }
        runner.orders = []
        runner._paired_long_capacity = lambda price: {
            'can_long': False,
            'long_reason': 'T+1 pairing consumed by MOM',
        }
        runner._submit_order = lambda *args, **kwargs: runner.orders.append(args)

        runner._handle_bt_dipping(100.2)

        self.assertEqual([], runner.orders)
        self.assertEqual(V39.STATE_IDLE, runner.st['fstate'])
        self.assertEqual(0, runner.st['trade_count_long'])

    def test_guard_rechecked_before_main_rev_sell(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'fstate': V39.STATE_SPIKING,
            'peak_price': 426.82,
            'daily_signal': {'atr_pct': 0.078},
            'trade_count_short': 1,
            'limit_up_guard': True,
            'locked': False,
        }
        runner.orders = []
        runner._submit_order = lambda *args, **kwargs: runner.orders.append(args)

        runner._handle_spiking(425.0)

        self.assertEqual([], runner.orders)
        self.assertEqual(V39.STATE_IDLE, runner.st['fstate'])

    def test_guard_rechecked_before_main_fwd_buy(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'fstate': V39.STATE_BT_DIPPING,
            'bt_dip_price': 100.0,
            'trade_count_long': 1,
            'long_legs': [],
            'limit_up_guard': True,
            'locked': False,
        }
        runner.orders = []
        runner._paired_long_capacity = lambda price: {'can_long': True}
        runner._submit_order = lambda *args, **kwargs: runner.orders.append(args)

        runner._handle_bt_dipping(100.2)

        self.assertEqual([], runner.orders)
        self.assertEqual(V39.STATE_IDLE, runner.st['fstate'])

    def test_guard_rechecked_before_mom_rev_sell(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'mom_state': 'MOM_SPIKING',
            'mom_peak': 100.5,
            'mom_pullback_pct': 0.0035,
            'do_short': False,
            'limit_up_guard': True,
            'locked': False,
        }
        runner.orders = []
        runner._submit_order = lambda *args, **kwargs: runner.orders.append(args)

        runner._mom_handle_spiking(100.0)

        self.assertEqual([], runner.orders)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])

    def test_guard_rechecked_before_mom_fwd_buy(self):
        runner = V39.StrategyRunner.__new__(V39.StrategyRunner)
        runner.st = {
            'mom_state': 'MOM_BT_DIPPING',
            'mom_dip': 100.0,
            'mom_last_block_reason': '',
            'limit_up_guard': True,
            'locked': False,
        }
        runner.orders = []
        runner._paired_long_capacity = lambda price: {'can_long': True}
        runner._submit_order = lambda *args, **kwargs: runner.orders.append(args)

        runner._mom_handle_bt_dipping(100.2)

        self.assertEqual([], runner.orders)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])


if __name__ == '__main__':
    unittest.main()
