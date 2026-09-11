import unittest
from backtest.dayt_exchange import Exchange


class ExchangeTests(unittest.TestCase):
    def test_next_event_limit_and_no_future_price(self):
        e=Exchange(); e.close(100)
        oid=e.submit(100,99)
        self.assertEqual(e.orders[oid].traded_volume,0)
        self.assertEqual(e.price,100)
        e.opening('20260910','0932',101,100000)
        self.assertEqual(e.orders[oid].reason,'LIMIT_NOT_MET')
        e.opening('20260910','0933',98,100000)
        self.assertEqual(e.orders[oid].traded_price,98)

    def test_partial_fee_per_order_and_volume_shared(self):
        e=Exchange(rate=.0005); e.close(10)
        a=e.submit(300); b=e.submit(100)
        e.opening('20260910','0932',10,10000)
        self.assertEqual(e.orders[a].traded_volume,100)
        self.assertEqual(e.orders[b].traded_volume,0)
        self.assertEqual(e.cash,98995)
        e.opening('20260910','0933',10,10000)
        e.opening('20260910','0934',10,10000)
        self.assertEqual(e.orders[a].fee,5)
        self.assertEqual(e.cash,96995)
        self.assertEqual(e.orders[a].order_status,56)

    def test_t1_zero_volume_limit_lock_and_cancel(self):
        e=Exchange(shares=0); e.close(10)
        e.submit(100)
        e.opening('20260910','0932',10,10000)
        oid=e.submit(-100)
        e.opening('20260910','0933',10,10000)
        self.assertEqual(e.orders[oid].reason,'T1_SELLABLE')
        e.opening('20260911','0931',10,0)
        self.assertEqual(e.orders[oid].traded_volume,0)
        e.opening('20260911','0932',10,10000,one_sided_limit=True)
        self.assertEqual(e.orders[oid].traded_volume,0)
        e.opening('20260911','0933',10,10000)
        self.assertEqual(e.orders[oid].traded_volume,100)
        pending=e.submit(100); e.cancel(pending)
        e.opening('20260911','0934',10,10000)
        self.assertEqual(e.orders[pending].order_status,54)

    def test_fee_can_make_order_unaffordable(self):
        e=Exchange(cash=1000,shares=0); e.close(10); oid=e.submit(100)
        e.opening('20260910','0932',10,10000)
        self.assertEqual(e.orders[oid].reason,'CASH')
        self.assertEqual(e.cash,1000)


if __name__=='__main__': unittest.main()
