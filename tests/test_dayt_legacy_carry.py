import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from collections import deque
from contextlib import ExitStack
from unittest.mock import patch
from backtest.dayt_legacy_carry import LegacyCarry


class CarryTests(unittest.TestCase):
    def setUp(self):
        self.state=dict(trade_date='20260911',short_legs=[(70.,100)],long_legs=[],mom_leg_shares=0,
                        fstate='SPIKING',buyback_target=69.,daily_signal={'atr_pct':.05},
                        trade_count_short=3,mom_trade_count=2,price_history=deque([1]),mom_price_history=deque([2]))
        self.runner=SimpleNamespace(st=self.state,_daily_init=Mock(),_refresh_position=Mock(),_new_leg_block_reason=lambda:'')
        self.broker=SimpleNamespace(book=SimpleNamespace(legs={'SHORT':[(70.,100)]}),cash=10000,position=100)
        module=SimpleNamespace(STATE_SPIKING='SPIKING',STATE_SOLD='SOLD',STATE_BT_DIPPING='BT_DIPPING',STATE_BT_BOUGHT='BT_BOUGHT')
        self.carry=LegacyCarry(self.runner,self.broker,module,'v39')

    def test_roll_preserves_money_legs_targets_and_atr(self):
        self.carry.before_event('20260914')
        self.assertTrue(self.carry.active)
        self.assertEqual(self.state['fstate'],'SOLD')
        self.assertEqual(self.state['buyback_target'],69)
        self.assertEqual(self.state['daily_signal'],{'atr_pct':.05})
        self.assertEqual(self.state['short_legs'],[(70,100)])
        self.assertEqual(self.broker.cash,10000)
        self.assertEqual(self.state['trade_count_short'],0)
        self.assertTrue(self.state['do_short'])
        self.carry.initialize()
        self.runner._daily_init.assert_not_called()

    def test_flat_reinitializes_once(self):
        self.carry.before_event('20260914')
        self.broker.book.legs={}
        self.state['short_legs']=[]
        self.carry.before_event('20260914')
        self.assertFalse(self.carry.active)
        self.runner._daily_init.assert_called_once()

    def test_disagreement_still_blocks(self):
        self.state['short_legs']=[]
        with self.assertRaisesRegex(RuntimeError,'quantities disagree'):
            self.carry.before_event('20260914')

    def test_failed_mom_force_close_retains_confirmed_leg(self):
        self.state.update(mom_state='MOM_SOLD',mom_sell_price=70,mom_leg_shares=100)
        self.broker.book.legs={'MOM SHORT':[(70,100)]}
        self.broker.current_day='20260911'
        self.runner._mom_force_close=lambda price:self.state.update(mom_state='MOM_IDLE',mom_sell_price=0,mom_leg_shares=0)
        self.runner._wait_for_fill=Mock(return_value=('TIMEOUT',0))
        with ExitStack() as stack:
            self.carry.install(stack,patch)
            self.runner._mom_force_close(72)
            self.assertEqual(self.state['mom_leg_shares'],100)
            self.assertEqual(self.state['mom_sell_price'],70)
            self.assertEqual(self.state['mom_state'],'MOM_SOLD')


if __name__=='__main__': unittest.main()
