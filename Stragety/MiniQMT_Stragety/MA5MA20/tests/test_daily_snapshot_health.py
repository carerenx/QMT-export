#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Behavior tests for the MiniQMT daily-data health gate."""

import os
import sys
import unittest

import pandas as pd


MINIQMT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DAYT_DIR = os.path.join(MINIQMT_ROOT, 'DayT')
for import_root in (MINIQMT_ROOT, DAYT_DIR):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from Stragety.MiniQMT_Stragety.DayT.infra.connector import MiniQMTConnector


FIELDS = ['open', 'high', 'low', 'close', 'volume', 'amount']


def _frame(rows):
    return pd.DataFrame(rows, columns=['date'] + FIELDS).set_index('date')


class _FakeXtData:
    def __init__(self, adjusted, raw, trading_dates):
        self.adjusted = adjusted
        self.raw = raw
        self.trading_dates = trading_dates
        self.local_calls = []
        self.download_calls = []

    def download_history_data(self, stock_code, period, start_time='', end_time='',
                              incrementally=None, dividend_type='none'):
        self.download_calls.append(
            (stock_code, period, start_time, end_time, dividend_type))

    def get_local_data(self, **kwargs):
        self.local_calls.append(kwargs)
        frame = self.adjusted if kwargs['dividend_type'] == 'front' else self.raw
        return {'601869.SH': frame.copy()}

    def get_trading_dates(self, market, start_time='', end_time='', count=-1):
        return list(self.trading_dates)


class DailySnapshotHealthTest(unittest.TestCase):
    def _connector(self, adjusted, raw, trading_dates):
        connector = MiniQMTConnector()
        connector.xtdata = _FakeXtData(adjusted, raw, trading_dates)
        return connector

    def test_stale_august_5_history_is_rejected_on_august_27(self):
        stale = _frame([
            ['20260803', 270.81, 275.57, 259.00, 262.30, 89009, 1],
            ['20260804', 267.00, 286.88, 264.03, 284.00, 141474, 1],
            ['20260805', 270.00, 312.40, 269.00, 312.40, 157140, 1],
        ])
        connector = self._connector(
            stale, stale, ['20260826', '20260827'])

        snapshot = connector.load_daily_snapshot(
            80, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertIsNone(snapshot)

    def test_daily_snapshot_requests_exact_history_window_for_both_adjustments(self):
        frame = _frame([
            ['20260826', 393.00, 414.00, 386.50, 388.02, 300, 1],
        ])
        connector = self._connector(frame, frame, ['20260826', '20260827'])

        connector.load_daily_snapshot(
            80, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertEqual(
            [('601869.SH', '1d', '', '', 'front'),
             ('601869.SH', '1d', '', '', 'none')],
            connector.xtdata.download_calls)
        self.assertEqual([80, 80], [
            call['count'] for call in connector.xtdata.local_calls])

    def test_current_partial_bar_is_removed_and_previous_day_is_accepted(self):
        adjusted = _frame([
            ['20260824', 370.92, 379.60, 358.58, 379.19, 100, 1],
            ['20260825', 360.00, 411.11, 345.01, 394.50, 200, 1],
            ['20260826', 393.00, 414.00, 386.50, 388.02, 300, 1],
            ['20260827', 393.00, 393.00, 393.00, 393.00, 5, 1],
        ])
        raw = adjusted.copy()
        connector = self._connector(
            adjusted, raw, ['20260826', '20260827'])

        snapshot = connector.load_daily_snapshot(
            80, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertIsNotNone(snapshot)
        self.assertEqual('20260826', snapshot['last_complete_date'])
        self.assertEqual(
            ['20260824', '20260825', '20260826'],
            snapshot['adjusted'].index.tolist())
        self.assertEqual(388.02, snapshot['raw'].iloc[-1]['close'])

    def test_raw_close_must_match_tick_last_close(self):
        adjusted = _frame([
            ['20260826', 393.00, 414.00, 386.50, 388.02, 300, 1],
        ])
        stale_raw = _frame([
            ['20260826', 270.00, 312.40, 269.00, 312.40, 300, 1],
        ])
        connector = self._connector(
            adjusted, stale_raw, ['20260826', '20260827'])

        snapshot = connector.load_daily_snapshot(
            80, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertIsNone(snapshot)

    def test_same_day_tick_last_close_matches_the_previous_trading_day(self):
        frame = _frame([
            ['20260902', 392.59, 413.68, 389.00, 407.80, 200, 1],
            ['20260903', 412.00, 413.00, 385.00, 393.46, 300, 1],
            ['20260904', 399.66, 400.71, 374.50, 378.80, 400, 1],
        ])
        connector = self._connector(
            frame, frame,
            ['20260902', '20260903', '20260904'])

        snapshot = connector.load_daily_snapshot(
            80, today='20260906', tick_last_close=393.46,
            tick_time='20260904 15:29:34',
            retries=1, retry_delay=0)

        self.assertIsNotNone(snapshot)
        self.assertEqual('20260904', snapshot['last_complete_date'])
        self.assertEqual(378.80, snapshot['verified_last_close'])
        self.assertFalse(snapshot['tick_last_close_one_day_stale'])

    def test_non_finite_daily_values_are_rejected(self):
        invalid = _frame([
            ['20260826', 393.00, float('inf'), 386.50,
             float('nan'), 300, 1],
        ])
        connector = self._connector(
            invalid, invalid, ['20260826', '20260827'])

        snapshot = connector.load_daily_snapshot(
            80, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertIsNone(snapshot)

    def test_invalid_values_outside_requested_window_do_not_lock_trading(self):
        frame = _frame([
            ['20260825', 0.0, 0.0, 0.0, 0.0, 0, 0],
            ['20260826', 393.00, 414.00, 386.50, 388.02, 300, 1],
        ])
        connector = self._connector(
            frame, frame, ['20260826', '20260827'])

        snapshot = connector.load_daily_snapshot(
            1, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertIsNotNone(snapshot)
        self.assertEqual(['20260826'], snapshot['adjusted'].index.tolist())

    def test_active_data_directory_is_used_without_hardcoded_data_dir(self):
        frame = _frame([
            ['20260826', 393.00, 414.00, 386.50, 388.02, 300, 1],
        ])
        connector = self._connector(
            frame, frame, ['20260826', '20260827'])

        snapshot = connector.load_daily_snapshot(
            80, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertIsNotNone(snapshot)
        self.assertEqual(2, len(connector.xtdata.local_calls))
        self.assertTrue(all(
            'data_dir' not in call for call in connector.xtdata.local_calls))

    def test_integer_yyyymmdd_indexes_are_normalized_as_trade_dates(self):
        frame = _frame([
            [20260826, 393.00, 414.00, 386.50, 388.02, 300, 1],
        ])
        connector = self._connector(
            frame, frame, [20260826, 20260827])

        snapshot = connector.load_daily_snapshot(
            80, today='20260827', tick_last_close=388.02,
            retries=1, retry_delay=0)

        self.assertIsNotNone(snapshot)
        self.assertEqual('20260826', snapshot['last_complete_date'])

    def test_legacy_history_reader_also_uses_active_data_directory(self):
        frame = _frame([
            ['20260826', 393.00, 414.00, 386.50, 388.02, 300, 1],
        ])
        connector = self._connector(
            frame, frame, ['20260826', '20260827'])

        result = connector.get_history_data(80, '1d', 'open')

        self.assertEqual({'601869.SH': [393.0]}, result)
        self.assertTrue(all(
            'data_dir' not in call for call in connector.xtdata.local_calls))


if __name__ == '__main__':
    unittest.main()
