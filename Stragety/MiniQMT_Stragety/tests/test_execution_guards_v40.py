#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Execution guards and next-cycle intraday-average triggers for v40."""

import importlib.util
import os
import sys
import unittest


STRATEGY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if STRATEGY_DIR not in sys.path:
    sys.path.insert(0, STRATEGY_DIR)

STRATEGY_PATH = os.path.join(
    STRATEGY_DIR, 'DayTradeing_v40_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v40', STRATEGY_PATH)
V40 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V40)


class TickContext:
    def __init__(self, tick_data):
        self.tick_data = tick_data

    def get_full_tick(self, stock_codes):
        return {V40.STOCK_QMT: dict(self.tick_data)}


class ExecutionGuardsV40Test(unittest.TestCase):
    def test_zero_sellable_shares_disable_both_daily_directions(self):
        capacity = V40.calculate_execution_capacity(
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
        active, release_since, event = V40.limit_up_guard_transition(
            active=False, release_since=0.0,
            price=426.82, last_close=388.02, now_ts=100.0)
        self.assertTrue(active)
        self.assertEqual(0.0, release_since)
        self.assertEqual('LOCK', event)

        active, release_since, event = V40.limit_up_guard_transition(
            active=active, release_since=release_since,
            price=419.06, last_close=388.02, now_ts=200.0)
        self.assertTrue(active)
        self.assertEqual(200.0, release_since)
        self.assertEqual('RELEASE_PENDING', event)

        active, release_since, event = V40.limit_up_guard_transition(
            active=active, release_since=release_since,
            price=419.06, last_close=388.02, now_ts=320.0)
        self.assertFalse(active)
        self.assertEqual(0.0, release_since)
        self.assertEqual('UNLOCK', event)

    def test_mom_long_does_not_arm_without_sellable_base_shares(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
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
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {
            'fstate': V40.STATE_IDLE,
            'daily_signal': {'sell_trigger': 400.72, 'buy_trigger': 418.28},
            'do_short': True,
            'do_long': True,
            'base_shares': 100,
            'base_can_use': 100,
            'limit_up_guard': True,
            'locked': False,
        }

        runner._handle_idle(426.82)

        self.assertEqual(V40.STATE_IDLE, runner.st['fstate'])

    def test_open_long_leg_reserves_sellable_shares_from_mom(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
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
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {'base_can_use': 100, 'base_shares': 100}
        original = V40.get_trade_detail_data
        V40.get_trade_detail_data = lambda *args: []
        try:
            actual = runner._clamp_sell_shares(100)
        finally:
            V40.get_trade_detail_data = original

        self.assertEqual(0, actual)
        self.assertEqual(0, runner.st['base_can_use'])

    def test_mom_rechecks_pairing_capacity_before_actual_buy(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
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
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {
            'fstate': V40.STATE_BT_DIPPING,
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
        self.assertEqual(V40.STATE_IDLE, runner.st['fstate'])
        self.assertEqual(0, runner.st['trade_count_long'])

    def test_guard_rechecked_before_main_rev_sell(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {
            'fstate': V40.STATE_SPIKING,
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
        self.assertEqual(V40.STATE_IDLE, runner.st['fstate'])

    def test_guard_rechecked_before_main_fwd_buy(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {
            'fstate': V40.STATE_BT_DIPPING,
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
        self.assertEqual(V40.STATE_IDLE, runner.st['fstate'])

    def test_guard_rechecked_before_mom_rev_sell(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
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
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
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

    def test_intraday_average_uses_tick_amount_and_raw_volume(self):
        average = V40.calculate_intraday_average(
            amount=40_250_000.0, pvolume=100_000, last_price=405.0)

        self.assertEqual(402.5, average)

    def test_next_cycle_triggers_are_based_on_intraday_average(self):
        triggers = V40.calculate_next_t_triggers(
            daily_average=400.0, atr_pct=0.05, sell_mult=0.40,
            daily_range_ma10=0.10)

        self.assertEqual(404.8, triggers['sell_trigger'])
        self.assertEqual(388.0, triggers['buy_trigger'])
        self.assertFalse(triggers['range_capped'])

    def test_completed_t_recalculates_signal_for_next_cycle(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {
            'intraday_avg_price': 400.0,
            'intraday_avg_valid': True,
            'daily_signal': {
                'sell_trigger': 410.0,
                'buy_trigger': 390.0,
                'atr_pct': 0.05,
                'sell_mult': 0.40,
                'daily_range_ma10': 0.10,
            },
            'next_t_cycle': 0,
        }
        runner.ctx = TickContext({
            'amount': 40_000_000.0, 'pvolume': 100_000,
            'lastPrice': 405.0})

        self.assertTrue(runner._recalculate_next_t_triggers('REV-T'))

        signal = runner.st['daily_signal']
        self.assertEqual(404.8, signal['sell_trigger'])
        self.assertEqual(388.0, signal['buy_trigger'])
        self.assertEqual('INTRADAY_AVG', signal['trigger_base'])
        self.assertEqual(1, runner.st['next_t_cycle'])

    def test_invalid_intraday_average_keeps_existing_triggers(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {
            'intraday_avg_price': 400.0,
            'intraday_avg_valid': True,
            'daily_signal': {
                'sell_trigger': 410.0, 'buy_trigger': 390.0,
                'atr_pct': 0.05, 'sell_mult': 0.40,
                'daily_range_ma10': 0.10,
            },
            'next_t_cycle': 0,
        }
        runner.ctx = TickContext({
            'amount': 0, 'pvolume': 0, 'lastPrice': 405.0})
        self.assertFalse(runner._recalculate_next_t_triggers('REV-T'))
        self.assertEqual(410.0, runner.st['daily_signal']['sell_trigger'])
        self.assertEqual(390.0, runner.st['daily_signal']['buy_trigger'])
        self.assertEqual(0, runner.st['next_t_cycle'])

    def test_vwap_based_buy_trigger_does_not_drift_on_heartbeat(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.total_t_days = 1
        runner.total_pnl = 100.0
        runner.st = {
            'fstate': V40.STATE_IDLE,
            'daily_signal': {
                'sell_trigger': 404.8, 'buy_trigger': 388.0,
                'buy_trigger_floor': 388.0,
                'trigger_base': 'INTRADAY_AVG',
            },
            'do_short': True, 'do_long': True,
            'locked': False, 'limit_up_guard': False,
            'bt_max_trail': 388.0,
        }

        runner._heartbeat(420.0)

        self.assertEqual(388.0, runner.st['daily_signal']['buy_trigger'])

    def test_mom_close_cancels_unfilled_main_monitor_from_old_trigger(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.ctx = TickContext({
            'amount': 40_000_000.0, 'pvolume': 100_000,
            'lastPrice': 405.0})
        runner.st = {
            'fstate': V40.STATE_SPIKING, 'peak_price': 410.0,
            'trade_count_short': 1, 'short_legs': [],
            'intraday_avg_price': 399.0, 'intraday_avg_valid': True,
            'daily_signal': {
                'sell_trigger': 410.0, 'buy_trigger': 390.0,
                'atr_pct': 0.05, 'sell_mult': 0.40,
                'daily_range_ma10': 0.10,
            },
            'next_t_cycle': 0,
        }

        runner._recalculate_next_t_triggers('MOM REV-T')

        self.assertEqual(V40.STATE_IDLE, runner.st['fstate'])
        self.assertEqual(0, runner.st['trade_count_short'])
        self.assertEqual(0.0, runner.st['peak_price'])

    def test_mom_close_cancels_old_monitor_even_when_vwap_is_invalid(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.ctx = TickContext({
            'amount': 0, 'pvolume': 0, 'lastPrice': 405.0})
        runner.st = {
            'fstate': V40.STATE_SPIKING, 'peak_price': 410.0,
            'trade_count_short': 1, 'short_legs': [],
            'intraday_avg_price': 400.0, 'intraday_avg_valid': True,
            'daily_signal': {
                'sell_trigger': 410.0, 'buy_trigger': 390.0,
                'atr_pct': 0.05, 'sell_mult': 0.40,
                'daily_range_ma10': 0.10,
            },
            'next_t_cycle': 0,
        }

        self.assertFalse(runner._recalculate_next_t_triggers('MOM REV-T'))

        self.assertEqual(V40.STATE_IDLE, runner.st['fstate'])
        self.assertEqual(0, runner.st['trade_count_short'])
        self.assertEqual(410.0, runner.st['daily_signal']['sell_trigger'])

    def test_main_force_closes_recalculate_exactly_once_after_full_fill(self):
        rev = V40.StrategyRunner.__new__(V40.StrategyRunner)
        rev.st = {
            'short_legs': [(410.0, 100)], 'sell_fill_price': 410.0,
            'fstate': V40.STATE_SOLD,
        }
        rev._cur_price = lambda: 400.0
        rev._submit_buyback_order = lambda *args: ('FILLED', 100)
        rev.recalcs = []
        rev._recalculate_next_t_triggers = lambda source: rev.recalcs.append(source)
        rev._force_buyback()

        fwd = V40.StrategyRunner.__new__(V40.StrategyRunner)
        fwd.st = {
            'long_legs': [(390.0, 100)], 'bt_buy_fill_price': 390.0,
            'fstate': V40.STATE_BT_BOUGHT,
        }
        fwd._cur_price = lambda: 400.0
        fwd._submit_order = lambda *args: ('FILLED', -100)
        fwd.recalcs = []
        fwd._recalculate_next_t_triggers = lambda source: fwd.recalcs.append(source)
        fwd._do_bt_force_sell()

        self.assertEqual(['REV-T force'], rev.recalcs)
        self.assertEqual(['FWD-T force'], fwd.recalcs)

    def test_mom_force_close_recalculates_only_after_full_fill(self):
        runner = V40.StrategyRunner.__new__(V40.StrategyRunner)
        runner.st = {
            'mom_state': 'MOM_SOLD', 'mom_leg_shares': 100,
            'mom_sell_price': 410.0,
            'mom_cooldown_until': 0.0, 'mom_cooldown_trigger': 0.0,
            'mom_cooldown_cycles': 0, 'mom_cooldown_duration': 0.0,
        }
        runner._submit_buyback_order = lambda *args: ('FILLED', 100)
        runner.recalcs = []
        runner._recalculate_next_t_triggers = lambda source: runner.recalcs.append(source)

        runner._mom_force_close(400.0)

        self.assertEqual(['MOM REV-T force'], runner.recalcs)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])

    def test_mom_partial_cooling_closes_keep_remaining_legs(self):
        short = V40.StrategyRunner.__new__(V40.StrategyRunner)
        short.total_t_days = 0; short.total_pnl = 0.0
        short.st = {
            'mom_state': V40.MOM_STATE_BUYBACK_COOLING,
            'mom_sell_price': 100.0, 'mom_leg_shares': 100,
            'mom_cooldown_until': 0.0, 'mom_cooldown_trigger': 98.5,
            'mom_cooldown_cycles': 1, 'mom_cooldown_duration': 12.0,
        }
        short._last_buyback_price = 99.0
        short._submit_buyback_order = lambda *args: ('PARTIAL', 40)
        short.recalcs = []
        short._recalculate_next_t_triggers = lambda source: short.recalcs.append(source)
        short._mom_handle_buyback_cooling(99.0, now_ts=100.0)

        long = V40.StrategyRunner.__new__(V40.StrategyRunner)
        long.total_t_days = 0; long.total_pnl = 0.0
        long.st = {
            'mom_state': V40.MOM_STATE_SELLBACK_COOLING,
            'mom_buy_price': 100.0, 'mom_leg_shares': 100,
            'mom_cooldown_until': 0.0, 'mom_cooldown_trigger': 101.8,
            'mom_cooldown_cycles': 1, 'mom_cooldown_duration': 12.0,
        }
        long._submit_order = lambda *args: ('PARTIAL', -40)
        long.recalcs = []
        long._recalculate_next_t_triggers = lambda source: long.recalcs.append(source)
        long._mom_handle_sellback_cooling(101.0, now_ts=100.0)

        self.assertEqual('MOM_SOLD', short.st['mom_state'])
        self.assertEqual(60, short.st['mom_leg_shares'])
        self.assertEqual([], short.recalcs)
        self.assertEqual('MOM_BT_BOUGHT', long.st['mom_state'])
        self.assertEqual(60, long.st['mom_leg_shares'])
        self.assertEqual([], long.recalcs)


if __name__ == '__main__':
    unittest.main()
