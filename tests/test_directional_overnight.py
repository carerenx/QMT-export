import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Stragety/MiniQMT_Stragety'))
from core.directional_overnight import DirectionalAdmission, Cycle, exposure_limit, validate_cycles


class DirectionalTests(unittest.TestCase):
    def test_warmup_signed_strength_and_gap(self):
        gate=DirectionalAdmission()
        for n in range(15):
            allowed=gate.on_minute(100000+n*60,100+n,99+n,.05)
            self.assertEqual(allowed,n==14)
        self.assertFalse(gate.on_minute(102000,120,119,.05))
        self.assertEqual(len(gate.rows),1)
        for n in range(20): gate.on_minute(200000+n*60,100-n,101-n,.05)
        self.assertLess(gate.score,0)
        self.assertFalse(gate.allowed)
        gate.on_minute(0,0,0,0)
        self.assertEqual(gate.rows,[])

    def test_cycle_fills_reserve_and_identity(self):
        cycle=Cycle('1',0,'SHORT','20260910','10:00',.05)
        cycle.fill(1,100,70,True)
        cycle.fill(2,100,72,True)
        self.assertEqual(cycle.average,71)
        self.assertAlmostEqual(cycle.reserve(75),15757.875)
        self.assertFalse(cycle.fill(1,100,70,True))
        cycle.fill(3,100,69,False,5)
        self.assertEqual(cycle.quantity,100)
        self.assertEqual(cycle.realized_gross,200)
        self.assertFalse(cycle.fee_known)
        for day in ('20260910','20260911','20260911','20260914'): cycle.advance(day)
        self.assertEqual(len(cycle.trading_days),3)
        self.assertTrue(validate_cycles([cycle],400,300))
        with self.assertRaises(ValueError): validate_cycles([cycle],400,400)

    def test_caps_do_not_inflate_on_small_inventory(self):
        self.assertEqual(exposure_limit(900),400)
        self.assertEqual(exposure_limit(200),100)
        self.assertEqual(exposure_limit(100),100)
        self.assertEqual(exposure_limit(50),50)
