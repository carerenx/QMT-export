"""Explicit, source-verified cash dividend ledger; never infer from price gaps."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CashDividend:
    event_id: str
    record_date: str
    ex_date: str
    payment_date: str
    net_per_share: float
    source: str

    def __post_init__(self):
        if not self.event_id or not self.source:
            raise ValueError('event id and verified source required')
        if not self.record_date < self.ex_date <= self.payment_date:
            raise ValueError('invalid dividend dates')
        if not math.isfinite(self.net_per_share) or self.net_per_share < 0:
            raise ValueError('explicit nonnegative net dividend required')


class DividendLedger:
    def __init__(self, events=()):
        self.events = tuple(events)
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError('duplicate dividend id')
        self.entitlements = {}
        self.accrued = set()
        self.paid = set()
        self.receivable = 0.
        self.income = 0.
        self.cash_paid = 0.
        self.entries = []

    def record_close(self, date, shares):
        for event in self.events:
            if event.record_date == date and event.event_id not in self.entitlements:
                self.entitlements[event.event_id] = shares * event.net_per_share

    def open_day(self, date):
        cash = 0.
        for event in self.events:
            key = event.event_id
            if event.ex_date <= date and key not in self.accrued:
                if key not in self.entitlements:
                    raise ValueError('missing record-date holdings: ' + key)
                amount = self.entitlements[key]
                self.receivable += amount
                self.income += amount
                self.accrued.add(key)
                self.entries.append(dict(date=date, event_id=key, kind='ACCRUE', amount=amount))
            if event.payment_date <= date and key not in self.paid:
                amount = self.entitlements[key]
                self.receivable -= amount
                self.cash_paid += amount
                cash += amount
                self.paid.add(key)
                self.entries.append(dict(date=date, event_id=key, kind='PAY', amount=amount))
        return cash
