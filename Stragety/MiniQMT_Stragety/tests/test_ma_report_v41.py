#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for the v41 intraday MA position report."""

import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime


STRATEGY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if STRATEGY_DIR not in sys.path:
    sys.path.insert(0, STRATEGY_DIR)


class BaseStrategyRunner:
    def _init_state(self):
        self.st = {}

    def _daily_init(self):
        self.base_last_close = self.ctx.get_full_tick(
            ['601869.SH'])['601869.SH']['lastClose']

    def _lock_all_trading(self, reason):
        self.st['daily_signal'] = None

    def _update_intraday_average(self, tick_data):
        pass


# Isolate the v41 unit from v40's MiniQMT/runtime dependencies.  The production
# module still imports and subclasses the real v40 strategy.
V40_STUB = types.ModuleType('DayTradeing_v40_stragety_miniqmt')
V40_STUB.StrategyRunner = BaseStrategyRunner
V40_STUB.STOCK_QMT = '601869.SH'
V40_STUB.STOCK_CODE = '601869'
V40_STUB.STOCK_NAME = 'YOFC'
V40_STUB.ACCOUNT = 'test'
V40_STUB.cfg = types.SimpleNamespace(HIST_DATA_LEN=80)
V40_STUB.datetime = datetime
V40_STUB._log = lambda message: None
sys.modules['DayTradeing_v40_stragety_miniqmt'] = V40_STUB

STRATEGY_PATH = os.path.join(
    STRATEGY_DIR, 'DayTradeing_v41_stragety_miniqmt.py')
SPEC = importlib.util.spec_from_file_location('daytrading_v41', STRATEGY_PATH)
V41 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V41)


class MaReportV41Test(unittest.TestCase):
    def test_confirmed_one_day_stale_tick_uses_verified_close_for_base_init(self):
        class Context:
            def get_full_tick(self, codes):
                return {'601869.SH': {'lastClose': 400.03}}

        class Connector:
            def load_daily_snapshot(self, *args, **kwargs):
                return {
                    'tick_last_close_one_day_stale': True,
                    'verified_last_close': 407.80,
                }

        runner = V41.StrategyRunner.__new__(V41.StrategyRunner)
        runner.st = {'trade_date': '', 'initialized': False}
        runner.ctx = Context()
        runner.conn = Connector()

        runner._daily_init()

        self.assertEqual(407.80, runner.base_last_close)
        self.assertEqual(
            400.03,
            runner.ctx.get_full_tick(['601869.SH'])['601869.SH']['lastClose'])

    def test_trade_lock_keeps_empty_signal_mapping_for_open_log(self):
        runner = V41.StrategyRunner.__new__(V41.StrategyRunner)
        runner.st = {}

        runner._lock_all_trading('bad data')

        self.assertEqual({}, runner.st['daily_signal'])

    def test_intraday_ma_includes_current_price(self):
        result = V41.calculate_ma_position(list(range(1, 20)), 21.0)

        self.assertEqual(18.2, result['ma5'])
        self.assertEqual(10.55, result['ma20'])
        self.assertEqual('ABOVE', result['ma5_position'])
        self.assertEqual('ABOVE', result['ma20_position'])

    def test_risk_when_price_is_below_97_percent_of_ma20(self):
        result = V41.calculate_ma_position([100.0] * 19, 50.0)

        self.assertTrue(result['risk'])
        self.assertEqual('BELOW', result['ma20_position'])
        self.assertLess(50.0, result['ma20_risk_price'])

    def test_report_is_limited_to_once_per_ten_minutes(self):
        runner = V41.StrategyRunner.__new__(V41.StrategyRunner)
        runner.st = {
            'ma_completed_closes': [100.0] * 19,
            'last_ma_report_time': 1000.0,
        }

        self.assertFalse(runner._maybe_report_ma(101.0, 1599.9))
        self.assertTrue(runner._maybe_report_ma(101.0, 1600.0))
        self.assertEqual(1600.0, runner.st['last_ma_report_time'])

    def test_insufficient_history_does_not_start_timer(self):
        runner = V41.StrategyRunner.__new__(V41.StrategyRunner)
        runner.st = {
            'ma_completed_closes': [100.0] * 18,
            'last_ma_report_time': 0.0,
        }

        self.assertFalse(runner._maybe_report_ma(101.0, 1600.0))
        self.assertEqual(0.0, runner.st['last_ma_report_time'])


if __name__ == '__main__':
    unittest.main()
