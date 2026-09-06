#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Daily volume-ratio regression tests."""

import os
import sys
import unittest


STRATEGY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if STRATEGY_DIR not in sys.path:
    sys.path.insert(0, STRATEGY_DIR)

from core.signals import compute_signal


def _price_series(size=60):
    closes = [100.0 + index * 0.1 for index in range(size)]
    opens = [price - 0.2 for price in closes]
    highs = [price + 1.0 for price in closes]
    lows = [price - 1.0 for price in closes]
    return opens, highs, lows, closes


class SignalVolumeRatioTest(unittest.TestCase):
    def test_current_open_is_used_without_mutating_complete_history(self):
        opens, highs, lows, closes = _price_series()
        original_opens = list(opens)
        volumes = [100.0] * 60

        signal = compute_signal(
            opens, highs, lows, closes, volumes, today_open=393.0)

        self.assertEqual(393.0, signal['open_price'])
        self.assertEqual(original_opens, opens)

    def test_ratio_uses_previous_twenty_complete_days(self):
        opens, highs, lows, closes = _price_series()
        volumes = [100.0] * 59 + [200.0]

        signal = compute_signal(opens, highs, lows, closes, volumes)

        self.assertTrue(signal['volume_valid'])
        self.assertAlmostEqual(2.0, signal['vol_ratio'])
        self.assertAlmostEqual(200.0, signal['volume_current'])
        self.assertAlmostEqual(100.0, signal['volume_avg20'])
        self.assertEqual(20, signal['volume_baseline_count'])

    def test_zero_volume_history_is_invalid_not_neutral_ratio(self):
        opens, highs, lows, closes = _price_series()
        volumes = [0.0] * 60

        signal = compute_signal(opens, highs, lows, closes, volumes)

        self.assertFalse(signal['volume_valid'])
        self.assertIsNone(signal['vol_ratio'])
        self.assertEqual(0.0, signal['volume_avg20'])
        self.assertEqual(20, signal['volume_baseline_count'])
        self.assertEqual(0.0, signal['factor_details']['volume'])
        self.assertNotIn('low volume', signal['blocked_reason'])

    def test_short_volume_series_is_invalid_without_crashing(self):
        opens, highs, lows, closes = _price_series()
        volumes = [100.0]

        signal = compute_signal(opens, highs, lows, closes, volumes)

        self.assertFalse(signal['volume_valid'])
        self.assertIsNone(signal['vol_ratio'])
        self.assertEqual(0.0, signal['volume_current'])
        self.assertEqual(0, signal['volume_baseline_count'])


if __name__ == '__main__':
    unittest.main()
