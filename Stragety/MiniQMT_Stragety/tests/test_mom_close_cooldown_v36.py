#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression tests for v36 non-blocking MOM close cooldown."""

import importlib.util
import os
import sys
import unittest


STRATEGY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if STRATEGY_DIR not in sys.path:
    sys.path.insert(0, STRATEGY_DIR)

STRATEGY_PATH = os.path.join(
    STRATEGY_DIR, 'DayTradeing_v36_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v36', STRATEGY_PATH)
V36 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V36)


class MomCloseCooldownV36Test(unittest.TestCase):
    def _runner(self):
        runner = V36.StrategyRunner.__new__(V36.StrategyRunner)
        runner.total_t_days = 0
        runner.total_pnl = 0.0
        runner.orders = []
        runner.buybacks = []
        runner.st = {
            'mom_state': 'MOM_IDLE',
            'mom_sell_price': 100.0,
            'mom_buy_price': 100.0,
            'mom_leg_shares': V36.MOM_LOT_SIZE,
            'mom_dip': 98.4,
            'mom_peak': 102.0,
            'mom_pullback_pct': 0.0035,
            'mom_cooldown_until': 0.0,
            'mom_cooldown_trigger': 0.0,
            'mom_cooldown_cycles': 0,
        }

        def submit(shares, price, label, style='COMPETE'):
            runner.orders.append((shares, price, label, style))
            return 'FILLED', shares

        def buyback(shares, price, label):
            runner.buybacks.append((shares, price, label))
            runner._last_buyback_price = price
            return 'FILLED', shares

        runner._submit_order = submit
        runner._submit_buyback_order = buyback
        runner._mom_pullback_threshold = lambda reference: 0.0035
        return runner

    def test_sellback_readiness_starts_12_second_cooldown(self):
        runner = self._runner()
        runner.st['mom_state'] = 'MOM_BT_SPIKING'

        runner._mom_handle_bt_spiking(101.6, now_ts=100.0)

        self.assertEqual(V36.MOM_STATE_SELLBACK_COOLING,
                         runner.st['mom_state'])
        self.assertEqual(112.0, runner.st['mom_cooldown_until'])
        self.assertEqual(101.8, runner.st['mom_cooldown_trigger'])
        self.assertEqual([], runner.orders)

    def test_sellback_does_not_order_before_cooldown_expires(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V36.MOM_STATE_SELLBACK_COOLING,
            'mom_cooldown_until': 112.0,
            'mom_cooldown_trigger': 101.8,
            'mom_cooldown_cycles': 1,
        })

        runner._mom_handle_sellback_cooling(101.0, now_ts=111.9)

        self.assertEqual([], runner.orders)
        self.assertEqual(112.0, runner.st['mom_cooldown_until'])

    def test_sellback_above_trigger_extends_cooldown(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V36.MOM_STATE_SELLBACK_COOLING,
            'mom_cooldown_until': 112.0,
            'mom_cooldown_trigger': 101.8,
            'mom_cooldown_cycles': 1,
        })

        runner._mom_handle_sellback_cooling(102.0, now_ts=112.0)

        self.assertEqual([], runner.orders)
        self.assertEqual(124.0, runner.st['mom_cooldown_until'])
        self.assertEqual(2, runner.st['mom_cooldown_cycles'])

    def test_sellback_in_profit_band_sells_after_cooldown(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V36.MOM_STATE_SELLBACK_COOLING,
            'mom_cooldown_until': 112.0,
            'mom_cooldown_trigger': 101.8,
            'mom_cooldown_cycles': 1,
        })

        runner._mom_handle_sellback_cooling(101.0, now_ts=112.0)

        self.assertEqual(
            [(-V36.MOM_LOT_SIZE, 101.0, 'MOM sellback', 'COMPETE')],
            runner.orders)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])
        self.assertEqual(0.0, runner.st['mom_cooldown_until'])

    def test_buyback_readiness_starts_12_second_cooldown(self):
        runner = self._runner()
        runner.st['mom_state'] = 'MOM_DIPPING'

        runner._mom_handle_dipping(98.55, now_ts=200.0)

        self.assertEqual(V36.MOM_STATE_BUYBACK_COOLING,
                         runner.st['mom_state'])
        self.assertEqual(212.0, runner.st['mom_cooldown_until'])
        self.assertEqual(98.5, runner.st['mom_cooldown_trigger'])
        self.assertEqual([], runner.buybacks)

    def test_buyback_below_trigger_extends_cooldown(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V36.MOM_STATE_BUYBACK_COOLING,
            'mom_cooldown_until': 212.0,
            'mom_cooldown_trigger': 98.5,
            'mom_cooldown_cycles': 1,
        })

        runner._mom_handle_buyback_cooling(98.0, now_ts=212.0)

        self.assertEqual([], runner.buybacks)
        self.assertEqual(224.0, runner.st['mom_cooldown_until'])
        self.assertEqual(2, runner.st['mom_cooldown_cycles'])

    def test_buyback_in_profit_band_buys_after_cooldown(self):
        runner = self._runner()
        runner.st.update({
            'mom_state': V36.MOM_STATE_BUYBACK_COOLING,
            'mom_cooldown_until': 212.0,
            'mom_cooldown_trigger': 98.5,
            'mom_cooldown_cycles': 1,
        })

        runner._mom_handle_buyback_cooling(99.0, now_ts=212.0)

        self.assertEqual(
            [(V36.MOM_LOT_SIZE, 99.0, 'MOM buyback')],
            runner.buybacks)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])
        self.assertEqual(0.0, runner.st['mom_cooldown_until'])

    def test_force_close_buys_back_during_buyback_cooldown(self):
        runner = self._runner()
        runner.st['mom_state'] = V36.MOM_STATE_BUYBACK_COOLING

        runner._mom_force_close(99.2)

        self.assertEqual(
            [(V36.MOM_LOT_SIZE, 99.2, 'MOM force buyback')],
            runner.buybacks)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])

    def test_force_close_sells_during_sellback_cooldown(self):
        runner = self._runner()
        runner.st['mom_state'] = V36.MOM_STATE_SELLBACK_COOLING

        runner._mom_force_close(100.8)

        self.assertEqual(
            [(-V36.MOM_LOT_SIZE, 100.8, 'MOM force sell', 'COMPETE')],
            runner.orders)
        self.assertEqual('MOM_IDLE', runner.st['mom_state'])


if __name__ == '__main__':
    unittest.main()
