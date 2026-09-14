"""Single-owner, next-bar execution engine; no live order methods."""
from dataclasses import asdict, dataclass
from datetime import datetime
import math

from Stragety.MiniQMT_Stragety.core.profit_priority_swing import Policy
from Stragety.MiniQMT_Stragety.core.long_hold_allocation_v2 import target_order
from backtest.profit_priority_corporate import DividendLedger


@dataclass
class Order:
    quantity: int
    time: str
    limit: float
    reason: str
    filled: int = 0
    value: float = 0.
    fee: float = 0.
    status: str = 'OPEN'


class Account:
    def __init__(self, price, rate=.0005, slip=1):
        self.cash = 100000.
        self.shares = 1000
        self.sellable = 1000
        self.cost = 1000 * price
        self.realized = 0.
        self.rate = rate
        self.slip = slip * .01
        self.orders = []
        self.fills = []
        self.initial = self.cash + self.cost

    def submit(self, quantity, time, reference, reason):
        if not quantity:
            return
        if any(o.status == 'OPEN' for o in self.orders):
            raise ValueError('one active order owns account reservations')
        offset = self.slip + .01
        limit = round(reference + (offset if quantity > 0 else -offset), 2)
        if quantity > 0:
            affordable = int(max(0., self.cash - 5) / (limit * (1 + self.rate)) / 100) * 100
            quantity = min(quantity, affordable)
        else:
            quantity = -min(-quantity, self.sellable // 100 * 100)
        if quantity:
            self.orders.append(Order(quantity, time, limit, reason))

    def cancel(self):
        for order in self.orders:
            if order.status == 'OPEN':
                order.status = 'PARTIAL_CANCELLED' if order.filled else 'CANCELLED'

    def process(self, time, row, previous_close):
        values = (row.open, row.high, row.low, row.close, row.volume, previous_close)
        if not all(math.isfinite(v) for v in values):
            raise ValueError('nonfinite market data')
        if min(row.open, row.high, row.low, row.close, previous_close) <= 0 or row.volume < 0:
            raise ValueError('invalid market data')
        capacity = int(max(0, row.volume) * 100 * .01 / 100) * 100
        # Bar timestamps denote completed minutes. Volume is known only at close;
        # using 1% of completed volume is an explicit participation approximation.
        for order in self.orders:
            if order.status != 'OPEN' or time <= order.time:
                continue
            if row.volume <= 0 or row.high == row.low and abs(row.open / previous_close - 1) >= .095:
                continue
            price = round(row.open + (self.slip if order.quantity > 0 else -self.slip), 2)
            if price > row.high or price < row.low:
                continue  # Never manufacture a price outside the observed bar.
            if price <= 0 or (order.quantity > 0 and price > order.limit) or (order.quantity < 0 and price < order.limit):
                continue
            quantity = min(abs(order.quantity) - order.filled, capacity)
            if order.quantity < 0:
                quantity = min(quantity, self.sellable // 100 * 100)
            while quantity:
                value = quantity * price
                fee = max(5., (order.value + value) * self.rate) - order.fee
                if order.quantity < 0 or value + fee <= self.cash + 1e-7:
                    break
                quantity -= 100
            if not quantity:
                continue
            signed = quantity if order.quantity > 0 else -quantity
            if signed > 0:
                self.cost += value + fee
            else:
                basis = self.cost * quantity / self.shares
                self.cost -= basis
                self.realized += value - fee - basis
                self.sellable -= quantity
            self.cash -= signed * price + fee
            self.shares += signed
            order.filled += quantity
            order.value += value
            order.fee += fee
            capacity -= quantity
            if order.filled == abs(order.quantity):
                order.status = 'FILLED'
            assert self.cash >= -1e-7 and 0 <= self.sellable <= self.shares
            self.fills.append(dict(time=time, submitted=order.time, shares=signed, price=price,
                                   fee=fee, cash=self.cash, position=self.shares, reason=order.reason,
                                   realized=self.realized))


def drawdowns(curve):
    peak = curve[0][1]
    peak_time = curve[0][0]
    worst = 0.
    start = peak_time
    trough = peak_time
    recovery = None
    longest = 0
    for time, equity in curve:
        if equity >= peak:
            if recovery is None and worst and equity >= recovery_peak:
                recovery = time
            peak = equity
            peak_time = time
        dd = 1 - equity / peak
        if dd > worst:
            worst = dd
            start = peak_time
            trough = time
            recovery = None
            recovery_peak = peak
        days = (datetime.strptime(time[:8], '%Y%m%d') - datetime.strptime(peak_time[:8], '%Y%m%d')).days
        longest = max(longest, days)
    return dict(maximum=worst, peak=start, trough=trough, recovery=recovery,
                longest_underwater_calendar_days=longest,
                recovery_days=None if recovery is None else (
                    datetime.strptime(recovery[:8], '%Y%m%d') - datetime.strptime(trough[:8], '%Y%m%d')).days)


def replay(front, raw, minute, start, end, experiment, rate=.0005, slip=1,
           confirmation=3, atr_threshold=2., fixed_plans=None, cash_dividends=(), policy_override=None,
           weakness_cooldown_bypass=False):
    days = [(d, b) for d, b in minute.groupby(minute.index.str[:8]) if start <= d <= end]
    if not days or days[0][0] != start or days[0][1].index[0][8:12] not in ('0930', '0931'):
        raise ValueError('complete opening session required; cannot silently shift initial valuation')
    price = float(days[0][1].iloc[0].open)
    account = Account(price, rate, slip)
    dividends = DividendLedger(cash_dividends)
    policy = policy_override if policy_override is not None else Policy(experiment, confirmation, atr_threshold)
    curve = [(days[0][0] + '093000', account.initial)]
    eod = []
    plans = []
    pending = None
    history = front.loc[front.index < start]
    if len(history) < 140:
        raise ValueError('insufficient completed daily warmup')
    pending = policy.decide(history, account.shares * price / account.initial, 0.,
                            str(history.index[-1]), start)
    last_fill_day = -100
    peak = account.initial
    weights = []
    split = experiment in ('split_execution', 'swing_fusion')
    for day_index, (date, bars) in enumerate(days):
        account.cash += dividends.open_day(date)
        account.sellable = account.shares
        history = front.loc[front.index < date]
        previous = float(raw.loc[raw.index < date].iloc[-1].close)
        remaining = 0
        reference = None
        extreme = None
        triggered = False
        started = False
        reprices = 0
        for row in bars.itertuples():
            stamp = str(row.Index)
            clock = stamp[8:]
            count = len(account.fills)
            account.process(stamp, row, previous)
            if len(account.fills) > count:
                last_fill_day = day_index
            equity = account.cash + account.shares * row.close + dividends.receivable
            curve.append((stamp, equity))
            if clock < '094000' or clock >= '145500':
                continue
            if pending is not None and not started:
                started = True
                quantity = target_order(account.shares, account.cash, row.close, pending['target_weight'],
                                        sessions_since_rebalance=(999 if weakness_cooldown_bypass and
                                            'CONFIRMED_WEAKNESS_CAP_60' in pending['reason_codes'] and
                                            pending['target_weight'] < account.shares*row.close/equity
                                            else day_index-last_fill_day),
                                        allow_buy=pending['risk_state'] != 'HALT_BUYS')
                if fixed_plans is not None:
                    quantity = fixed_plans.get(date, 0)
                plans.append(dict(date=date, quantity=quantity, **pending))
                reference = float(row.close)
                extreme = reference
                reason = '/'.join(pending['reason_codes'])
                first = quantity
                if split and not pending['risk_order']:
                    first = int(quantity / 200) * 100
                    remaining = quantity - first
                account.submit(first, stamp, reference, reason)
            elif started:
                # Reprice by cancel/replacing unfilled remainder at 5-minute intervals.
                if int(clock[2:4]) % 5 == 0 and reprices < 2:
                    for order in list(account.orders):
                        if order.status == 'OPEN':
                            remainder = (abs(order.quantity)-order.filled) * (1 if order.quantity > 0 else -1)
                            account.cancel()
                            account.submit(remainder, stamp, row.close, order.reason)
                            reprices += 1
                if remaining and not triggered:
                    extreme = min(extreme, row.close) if remaining > 0 else max(extreme, row.close)
                    # A completed-minute move of .25% followed by .10% reversal.
                    signal = (remaining > 0 and extreme <= reference*.9975 and row.close >= extreme*1.001)
                    signal = signal or (remaining < 0 and extreme >= reference*1.0025 and row.close <= extreme*.999)
                    if clock >= '144500':
                        for order in account.orders:
                            if order.status == 'OPEN':
                                remaining += (abs(order.quantity)-order.filled) * (1 if order.quantity > 0 else -1)
                        account.cancel()
                    if (signal or clock >= '144500') and not any(o.status == 'OPEN' for o in account.orders):
                        account.submit(remaining, stamp, row.close, reason + '/SECOND_SLICE')
                        triggered = True
        account.cancel()  # DAY expiry is explicit; no fabricated closing fill.
        close = float(bars.iloc[-1].close)
        dividends.record_close(date, account.shares)
        equity = account.cash + account.shares * close + dividends.receivable
        peak = max(peak, equity)
        weight = account.shares * close / equity
        weights.append(weight)
        eod.append((date, equity))
        completed = front.loc[front.index <= date]
        if len(completed) >= 140 and day_index+1 < len(days):
            pending = policy.decide(completed, weight, 1-equity/peak, date, days[day_index+1][0])
    final = eod[-1][1]
    unrealized = account.shares * close - account.cost
    assert abs(final-account.initial-account.realized-unrealized-dividends.income) < .001
    assert abs(account.cash - (100000+dividends.cash_paid-sum(f['shares']*f['price']+f['fee'] for f in account.fills))) < .001
    return dict(experiment=experiment, initial=account.initial, final=final, profit=final-account.initial,
                realized=account.realized, unrealized=unrealized, cash=account.cash, shares=account.shares,
                dividend_income=dividends.income, dividend_receivable=dividends.receivable,
                dividend_cash_paid=dividends.cash_paid, dividend_entries=dividends.entries,
                fees=sum(o.fee for o in account.orders), average_weight=sum(weights)/len(weights),
                minute_drawdown=drawdowns(curve), daily_drawdown=drawdowns([(d+'150000',e) for d,e in eod]),
                orders=[asdict(o) for o in account.orders], fills=account.fills, plans=plans,
                eod=eod, minute_equity=curve, cancelled_attempt_quantity=sum(abs(o.quantity)-o.filled for o in account.orders
                 if o.status in ('CANCELLED','PARTIAL_CANCELLED')), swing_open=policy.swing,
                accounting_valid=True)
