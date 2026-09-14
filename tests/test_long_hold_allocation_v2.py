import unittest

from Stragety.MiniQMT_Stragety.core.long_hold_allocation_v2 import confirmed_regime
from Stragety.MiniQMT_Stragety.core.long_hold_allocation_v2 import risk_state
from Stragety.MiniQMT_Stragety.core.long_hold_allocation_v2 import target_order


class RegimeConfirmationTests(unittest.TestCase):
    def test_risk_on_change_requires_three_sessions(self):
        self.assertEqual("SIDEWAYS", confirmed_regime("BULL", "SIDEWAYS", 2))
        self.assertEqual("BULL", confirmed_regime("BULL", "SIDEWAYS", 3))

    def test_bear_transition_is_immediate(self):
        self.assertEqual("BEAR", confirmed_regime("BEAR", "STRONG_BULL", 1))

    def test_risk_thresholds_leave_gap_buffer(self):
        self.assertEqual("NORMAL", risk_state(0.1499))
        self.assertEqual("REDUCE_ONE_TIER", risk_state(0.15))
        self.assertEqual("DEFENSIVE_30", risk_state(0.20))
        self.assertEqual("HALT_BUYS", risk_state(0.25))


class LowChurnOrderTests(unittest.TestCase):
    def test_small_weight_gap_does_not_trade(self):
        self.assertEqual(0, target_order(1000, 100000, 100, 0.55))

    def test_cooldown_blocks_non_risk_rebalance(self):
        self.assertEqual(
            0,
            target_order(
                1000, 100000, 100, 0.85,
                sessions_since_rebalance=2),
        )

    def test_risk_reduction_bypasses_cooldown(self):
        self.assertLess(
            target_order(
                1000, 10000, 100, 0.30,
                sessions_since_rebalance=1),
            0,
        )


if __name__ == "__main__":
    unittest.main()
