import unittest

import pandas as pd

from Stragety.MiniQMT_Stragety.core.satellite_reverse_t import admission
from Stragety.MiniQMT_Stragety.core.satellite_reverse_t import satellite_shares
from Stragety.MiniQMT_Stragety.core.satellite_reverse_t import simulate_day


class SatelliteBudgetTests(unittest.TestCase):
    def test_regime_budget_and_risk_block(self):
        self.assertEqual(0, satellite_shares(2000, "STRONG_BULL", "NORMAL"))
        self.assertEqual(200, satellite_shares(2000, "BULL", "NORMAL"))
        self.assertEqual(400, satellite_shares(2000, "SIDEWAYS", "NORMAL"))
        self.assertEqual(0, satellite_shares(2000, "BULL", "REDUCE_ONE_TIER"))

    def test_positive_three_day_momentum_blocks_entry(self):
        allowed, reason = admission([10, 10, 10, 10.1, 10.2, 10.3], "BULL", "NORMAL")
        self.assertFalse(allowed)
        self.assertEqual("POSITIVE_3D_MOMENTUM", reason)


class SatelliteCycleTests(unittest.TestCase):
    def test_confirmed_reversal_closes_same_day(self):
        index = [f"2026010209{40 + offset:02d}00" for offset in range(8)]
        closes = [10.30, 10.34, 10.36, 10.32, 10.20, 10.10, 10.05, 10.00]
        frame = pd.DataFrame({
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [10000] * len(closes),
            "amount": [100000] * len(closes),
        }, index=index)
        result = simulate_day(
            frame, [10.4, 10.3, 10.2, 10.1, 10.0, 10.0], 1.0,
            2000, "SIDEWAYS", "NORMAL")
        self.assertEqual(2, len(result["trades"]))
        self.assertEqual(0, sum(row["shares"] for row in result["trades"]))


if __name__ == "__main__":
    unittest.main()
