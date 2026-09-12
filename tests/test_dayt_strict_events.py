import unittest
import pandas as pd
from backtest.dayt_strict import StrictBroker


class StrictEventsTests(unittest.TestCase):
    def test_future_open_not_feedback_before_event(self):
        daily=pd.DataFrame(dict(open=[10.],high=[11.],low=[9.],close=[10.],volume=[1000.],amount=[1000000.]),index=['20260909'])
        bars=pd.DataFrame(dict(open=[10.,99.],high=[11.,999.],low=[9.,88.],close=[11.,100.],
                               volume=[1000.,1000.],amount=[1000000.,9900000.]),
                          index=['20260910093100','20260910093200'])
        broker=StrictBroker(daily,bars,.0005)
        self.assertEqual(broker.advance(),'PREOPEN')
        self.assertEqual(broker.get_full_tick([broker.code])[broker.code]['open'],0)
        self.assertEqual(broker.advance(),'OPEN')
        self.assertEqual(broker.advance(),'CLOSE')
        broker.label='FWD-T buy'
        oid=broker.order(broker.code,100,'COMPETE',11.)
        self.assertEqual(broker.query_order(oid).traded_volume,0)
        self.assertEqual(broker.get_full_tick([broker.code])[broker.code]['lastPrice'],11.)
        broker.sleep(.5)
        self.assertEqual(broker.query_order(oid).traded_price,99.)
        tick=broker.get_full_tick([broker.code])[broker.code]
        self.assertEqual(tick['lastPrice'],99.)
        self.assertNotEqual(tick['high'],999.)
        self.assertEqual(broker.sellable,200)
        self.assertEqual(broker.load_daily_snapshot(80)['last_complete_date'],'20260909')


if __name__=='__main__': unittest.main()
