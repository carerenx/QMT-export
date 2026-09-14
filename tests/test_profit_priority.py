import unittest
from types import SimpleNamespace

from backtest.profit_priority_engine import Account, drawdowns
from backtest.profit_priority_corporate import CashDividend, DividendLedger


def bar(price=10., volume=100000., locked=False):
    return SimpleNamespace(open=price,close=price,high=price if locked else price+.1,
                           low=price if locked else price-.1,volume=volume)


class ExecutionTests(unittest.TestCase):
    def test_invalid_prices_fail_closed(self):
        account = Account(10)
        account.submit(100, '1', 10, 'TEST')
        with self.assertRaises(ValueError):
            account.process('2', bar(price=float('nan')), 10)
        self.assertFalse(account.fills)

    def test_slippage_cannot_leave_bar_range(self):
        account = Account(10)
        account.submit(100, '1', 10, 'TEST')
        account.process('2', bar(locked=True), 10)
        self.assertFalse(account.fills)

    def test_next_event_and_t1(self):
        account = Account(10)
        account.submit(100,'20250915094000',10,'BUY')
        account.process('20250915094000',bar(),10)
        self.assertFalse(account.fills)
        account.process('20250915094100',bar(),10)
        self.assertEqual(1100,account.shares)
        self.assertEqual(1000,account.sellable)
        account.submit(-1100,'20250915094200',10,'SELL')
        account.process('20250915094300',bar(),10)
        self.assertEqual(100,account.shares)

    def test_partial_fee_reservation_and_cash(self):
        account = Account(10)
        account.submit(300,'1',10,'TEST')
        with self.assertRaises(ValueError):
            account.submit(100,'1',10,'DUPLICATE')
        for time in ('2','3','4'):
            account.process(time,bar(volume=100),10)
        self.assertEqual(300,account.orders[0].filled)
        self.assertEqual(5,account.orders[0].fee)
        self.assertGreater(account.cash,0)

    def test_locked_and_expiry(self):
        account = Account(10)
        account.submit(100,'1',11,'TEST')
        account.process('2',bar(price=11,locked=True),10)
        self.assertFalse(account.fills)
        account.cancel()
        self.assertEqual('CANCELLED',account.orders[0].status)

    def test_drawdown_recovery(self):
        result = drawdowns([('20250101',100),('20250102',80),('20250103',105)])
        self.assertAlmostEqual(.2,result['maximum'])
        self.assertEqual('20250103',result['recovery'])


class DividendTests(unittest.TestCase):
    def event(self):
        return CashDividend('fixture', '20250915', '20250916', '20250918', .1, 'synthetic test only')

    def test_receivable_is_not_spendable_cash(self):
        ledger = DividendLedger([self.event()])
        ledger.record_close('20250915', 1000)
        self.assertEqual(0, ledger.open_day('20250916'))
        self.assertEqual(100, ledger.receivable)
        self.assertEqual(100, ledger.income)
        self.assertEqual(100, ledger.open_day('20250918'))
        self.assertEqual(0, ledger.receivable)
        self.assertEqual(100, ledger.cash_paid)

    def test_repeated_dates_do_not_duplicate_cash(self):
        ledger = DividendLedger([self.event()])
        ledger.record_close('20250915', 1000)
        ledger.record_close('20250915', 2000)
        self.assertEqual(100, ledger.open_day('20250918'))
        self.assertEqual(0, ledger.open_day('20250918'))
        self.assertEqual(2, len(ledger.entries))

    def test_missing_entitlement_fails_closed(self):
        ledger = DividendLedger([self.event()])
        with self.assertRaises(ValueError):
            ledger.open_day('20250916')

    def test_duplicate_events_rejected(self):
        with self.assertRaises(ValueError):
            DividendLedger([self.event(), self.event()])


if __name__ == '__main__':
    unittest.main()
