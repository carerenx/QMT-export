import unittest
import pandas as pd
from Stragety.MiniQMT_Stragety.core.long_hold_ablation import AblationPolicy
from Stragety.MiniQMT_Stragety.core.profit_priority_swing import Policy


class AblationTests(unittest.TestCase):
    def test_default_matches_v2_decisions(self):
        values = [100+i*.2 for i in range(170)]+[134-i*.8 for i in range(60)]
        frame = pd.DataFrame({'close':values,'high':[v+1 for v in values],'low':[v-1 for v in values]})
        for dd in (0,.15,.2,.25,.4):
            base = Policy('v2_control')
            new = AblationPolicy()
            for length in range(140,len(frame)+1):
                left = base.decide(frame.iloc[:length],.7,dd,'20250915','20250916')
                right = new.decide(frame.iloc[:length],.7,dd,'20250915','20250916')
                for key in ('target_weight','regime','risk_state','reason_codes'):
                    self.assertEqual(left[key],right[key])


if __name__=='__main__':
    unittest.main()
