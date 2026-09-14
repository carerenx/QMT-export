import unittest

from Stragety.MiniQMT_Stragety.core.long_hold_allocation_v3 import next_risk_mode


def indicators(close=100, previous=99, ma20=95, ma60=90, efficiency=0.3):
    return {
        "close": close,
        "previous_close": previous,
        "ma20": ma20,
        "ma60": ma60,
        "signed_efficiency20": efficiency,
    }


class RiskRecoveryTests(unittest.TestCase):
    def test_fifteen_percent_drawdown_enters_first_defensive_tier(self):
        self.assertEqual(
            "DEFENSIVE_75", next_risk_mode(0.16, "NORMAL", 0, indicators()))

    def test_fifteen_percent_drawdown_defends_when_trend_is_weak(self):
        weak = indicators(close=90, previous=92, ma20=95, efficiency=-0.2)
        self.assertEqual(
            "DEFENSIVE_75", next_risk_mode(0.16, "NORMAL", 0, weak))

    def test_severe_weakness_enters_halt(self):
        weak = indicators(close=80, previous=82, ma20=95, ma60=90, efficiency=-0.3)
        self.assertEqual("HALT_30", next_risk_mode(0.26, "NORMAL", 0, weak))

    def test_recovery_advances_after_five_confirmed_sessions(self):
        self.assertEqual(
            "HALT_30", next_risk_mode(0.26, "HALT_30", 3, indicators()))
        self.assertEqual(
            "RECOVERY_45", next_risk_mode(0.26, "HALT_30", 4, indicators()))


if __name__ == "__main__":
    unittest.main()
