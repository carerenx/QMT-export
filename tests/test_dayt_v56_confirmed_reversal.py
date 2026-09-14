import importlib.util
from pathlib import Path
import unittest


PATH = (Path(__file__).resolve().parents[1] / "Stragety" /
        "MiniQMT_Stragety" / "DayT" /
        "DayT_v56_nomom_ConfirmedReversalRiskBudget.py")
SPEC = importlib.util.spec_from_file_location("dayt_v56", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ConfirmedReversalTests(unittest.TestCase):
    def test_rejects_single_bar_noise(self):
        self.assertFalse(MODULE.confirmed_short_reversal(
            100.0, 100.30, 100.05, 1))

    def test_rejects_peak_without_enough_extension(self):
        self.assertFalse(MODULE.confirmed_short_reversal(
            100.0, 100.10, 99.80, 2))

    def test_rejects_immaterial_pullback(self):
        self.assertFalse(MODULE.confirmed_short_reversal(
            100.0, 100.30, 100.15, 2))

    def test_accepts_extended_confirmed_reversal(self):
        self.assertTrue(MODULE.confirmed_short_reversal(
            100.0, 100.30, 100.05, 2))

    def test_core_position_limits_t_inventory(self):
        shares = MODULE.calculate_t_shares(
            40.0, 1000, 100, 40000.0, MODULE.T_POSITION_FRACTION)
        self.assertEqual(400, shares)


if __name__ == "__main__":
    unittest.main()
