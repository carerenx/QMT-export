import unittest
from Stragety.MiniQMT_Stragety.core.exit_recovery import ExitRecovery


class ExitTests(unittest.TestCase):
    def test_tightening_latches_without_price_gate(self):
        c = ExitRecovery()
        for m in range(31):
            c.observe(m, 319., 321.86, .1053062643, 316.78)
        self.assertEqual(c.state['target'], 318.81)
        restored = ExitRecovery(c.state)
        restored.observe(500, 317., 321.86, .1053062643, 316.78)
        self.assertEqual(restored.state['elapsed'], 30)
        self.assertEqual(restored.state['target'], 318.81)

    def test_intent_cap_budget_and_restart(self):
        c = ExitRecovery()
        c.begin(382.6, .071144)
        self.assertTrue(c.permit(1, 383.))
        self.assertFalse(c.permit(1, 383.))
        c = ExitRecovery(c.state)
        c.begin(500., .1)
        self.assertLess(c.state['cap'], 385.)
        self.assertFalse(c.permit(2, 400.))
        self.assertTrue(c.permit(2, 383.))
        self.assertTrue(c.permit(3, 383.))
        self.assertFalse(c.permit(4, 383.))

    def test_trail_optional_and_no_intrabar_low(self):
        c = ExitRecovery()
        c.observe(1, 319.01, 321.86, .1053, 316.78, trail_units=.05)
        self.assertTrue(c.state['trail_armed'])
        c.observe(1, 310., 321.86, .1053, 316.78)
        self.assertEqual(c.state['low'], 319.01)
