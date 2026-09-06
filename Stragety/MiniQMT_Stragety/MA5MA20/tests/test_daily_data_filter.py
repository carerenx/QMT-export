#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Daily cache completeness tests."""

import os
import sys
import unittest

import pandas as pd


MINIQMT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STRATEGY_DIR = os.path.join(MINIQMT_ROOT, 'DayT')
for import_root in (MINIQMT_ROOT, STRATEGY_DIR):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from Stragety.MiniQMT_Stragety.DayT.infra.connector import exclude_incomplete_daily_bar


class DailyDataFilterTest(unittest.TestCase):
    def test_current_day_bar_is_removed(self):
        frame = pd.DataFrame(
            {'volume': [100.0, 5.0]},
            index=['20260821', '20260824'],
        )

        filtered, removed = exclude_incomplete_daily_bar(frame, today='20260824')

        self.assertTrue(removed)
        self.assertEqual(['20260821'], filtered.index.tolist())
        self.assertEqual([100.0], filtered['volume'].tolist())

    def test_last_complete_day_is_preserved(self):
        frame = pd.DataFrame(
            {'volume': [100.0]},
            index=['20260821'],
        )

        filtered, removed = exclude_incomplete_daily_bar(frame, today='20260824')

        self.assertFalse(removed)
        self.assertEqual(['20260821'], filtered.index.tolist())


if __name__ == '__main__':
    unittest.main()
