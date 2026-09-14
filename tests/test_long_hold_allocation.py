from datetime import datetime, timedelta
import unittest

import pandas as pd

from Stragety.MiniQMT_Stragety.core.long_hold_allocation import (
    calculate_indicators,
    classify_regime,
    decide_allocation,
    risk_state,
    target_order,
)
from backtest.long_hold_rotation import run_backtest


def series(start=100.0, step=0.5, count=160):
    closes = [start + step * index for index in range(count)]
    highs = [value + 1.0 for value in closes]
    lows = [value - 1.0 for value in closes]
    return highs, lows, closes


class AllocationSignalTests(unittest.TestCase):
    def test_requires_warmup(self):
        with self.assertRaises(ValueError):
            calculate_indicators([2.0] * 139, [1.0] * 139, [1.5] * 139)

    def test_strong_bull_classification(self):
        indicators = calculate_indicators(*series())
        self.assertEqual(("STRONG_BULL", 0.75), classify_regime(indicators))

    def test_bear_classification_and_floor(self):
        highs, lows, closes = series(start=200.0, step=-0.5)
        decision = decide_allocation(highs, lows, closes, 0.8, 0.0, "20260101", "20260102")
        self.assertEqual("BEAR", decision["regime"])
        self.assertEqual(0.30, decision["target_weight"])

    def test_drawdown_states_have_hard_boundaries(self):
        self.assertEqual("NORMAL", risk_state(0.1999))
        self.assertEqual("REDUCE_ONE_TIER", risk_state(0.20))
        self.assertEqual("DEFENSIVE_30", risk_state(0.25))
        self.assertEqual("HALT_BUYS", risk_state(0.30))

    def test_hard_drawdown_blocks_increase(self):
        highs, lows, closes = series()
        decision = decide_allocation(highs, lows, closes, 0.30, 0.30, "20260101", "20260102")
        self.assertEqual(0.30, decision["target_weight"])
        self.assertEqual("HALT_BUYS", decision["risk_state"])

    def test_buy_cooldown_preserves_current_weight(self):
        highs, lows, closes = series()
        decision = decide_allocation(highs, lows, closes, 0.45, 0.0, "20260101", "20260102", 1)
        self.assertEqual(0.45, decision["target_weight"])
        self.assertIn("BUY_COOLDOWN", decision["reason_codes"])


class TargetOrderTests(unittest.TestCase):
    def test_rebalance_is_limited_to_quarter_equity(self):
        self.assertEqual(700, target_order(1000, 100000.0, 50.0, 1.0))

    def test_order_is_rounded_to_board_lot(self):
        self.assertEqual(600, target_order(1000, 100000.0, 63.0, 0.75))

    def test_buy_is_clamped_by_cash(self):
        self.assertEqual(100, target_order(1000, 7000.0, 50.0, 1.0))

    def test_hard_stop_blocks_buys_but_allows_sells(self):
        self.assertEqual(0, target_order(1000, 100000.0, 50.0, 1.0, allow_buy=False))
        self.assertLess(target_order(1000, 0.0, 50.0, 0.30, allow_buy=False), 0)


class BacktestContractTests(unittest.TestCase):
    def test_signal_executes_next_session_at_or_after_0940(self):
        origin = datetime(2025, 1, 1)
        daily_dates = [(origin + timedelta(days=index)).strftime("%Y%m%d") for index in range(180)]
        closes = [50.0 + index * 0.1 for index in range(180)]
        daily = pd.DataFrame({
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [10000.0] * 180,
            "amount": [500000.0] * 180,
        }, index=daily_dates)
        minute_dates = daily_dates[140:150]
        index = []
        rows = []
        for date in minute_dates:
            for hms in ("093900", "094000", "150000"):
                index.append(date + hms)
                rows.append({"open": 64.0, "high": 64.1, "low": 63.9,
                             "close": 64.0, "volume": 100.0, "amount": 6400.0})
        minute = pd.DataFrame(rows, index=index)
        result = run_backtest("600000.SH", daily, minute, minute_dates[0], minute_dates[-1])
        for trade in result["trades"]:
            self.assertGreaterEqual(trade["execution_time"][8:14], "094000")
            self.assertGreater(trade["execution_date"], trade["signal_date"])
        self.assertGreaterEqual(result["final_shares"], 0)
        self.assertGreaterEqual(result["fees"], 0.0)
        self.assertIn(result["acceptance"], ("PASS", "FAIL"))

    def test_incomplete_requested_history_fails_acceptance(self):
        origin = datetime(2025, 1, 1)
        daily_dates = [(origin + timedelta(days=index)).strftime("%Y%m%d") for index in range(180)]
        closes = [50.0 + index * 0.1 for index in range(180)]
        daily = pd.DataFrame({
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [10000.0] * 180,
            "amount": [500000.0] * 180,
        }, index=daily_dates)
        rows = []
        index = []
        for date in daily_dates[140:150]:
            index.append(date + "094000")
            rows.append({"open": 64.0, "high": 64.1, "low": 63.9,
                         "close": 64.0, "volume": 100.0, "amount": 6400.0})
        minute = pd.DataFrame(rows, index=index)
        result = run_backtest("600000.SH", daily, minute, "20230101", "20260101")
        self.assertEqual("FAIL", result["acceptance"])
        self.assertIn("REQUESTED_PERIOD_MINUTE_COVERAGE_INCOMPLETE", result["acceptance_failures"])


if __name__ == "__main__":
    unittest.main()
