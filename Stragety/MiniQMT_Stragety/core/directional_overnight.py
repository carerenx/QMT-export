"""Pure admission and cycle risk accounting for the v52 research strategy."""
from dataclasses import dataclass, field, asdict
import math


class DirectionalAdmission:
    def __init__(self, threshold=.2, warmup=15, lookback=5, smooth=3, confirmation=3):
        self.threshold, self.warmup = threshold, warmup
        self.lookback, self.smooth, self.confirmation = lookback, smooth, confirmation
        self.reset()

    def reset(self):
        self.rows, self.scores = [], []
        self.passed = 0
        self.score = None

    def on_minute(self, timestamp, price, average, atr):
        """Accept a completed minute only. Any continuity break restarts warmup."""
        if not all(math.isfinite(v) and v > 0 for v in (timestamp,price,average,atr)):
            self.reset(); return False
        if self.rows and timestamp-self.rows[-1][0] != 60:
            self.reset()
        self.rows.append((timestamp,price,average))
        if len(self.rows)>self.lookback:
            previous=self.rows[-self.lookback-1]
            clip=lambda value: max(-1.,min(1.,value/(atr*.10)))
            instantaneous=sum((clip(price/average-1),clip(price/previous[1]-1),clip(average/previous[2]-1)))/3
            self.scores.append(instantaneous)
            self.scores=self.scores[-self.smooth:]
            self.score=sum(self.scores)/len(self.scores)
            self.passed=self.passed+1 if len(self.scores)==self.smooth and self.score>=self.threshold else 0
        self.rows=self.rows[-max(self.warmup,self.lookback+1):]
        return self.allowed

    @property
    def allowed(self):
        return len(self.rows)>=self.warmup and self.passed>=self.confirmation


@dataclass
class Cycle:
    cycle_id: str
    lane: int
    direction: str
    opened_day: str
    opened_time: str
    atr: float
    quantity: int = 0
    opening_quantity: int = 0
    opening_value: float = 0.
    realized_gross: float = 0.
    fees: float = 0.
    fee_known: bool = True
    orders: dict = field(default_factory=dict)
    trading_days: list = field(default_factory=list)
    label: str = ''
    targets: dict = field(default_factory=dict)

    @property
    def average(self): return self.opening_value/self.opening_quantity if self.opening_quantity else 0.

    def fill(self, order_id, quantity, price, opening, fee=None):
        record=(int(quantity),float(price),bool(opening),fee)
        key=str(order_id)
        if key in self.orders:
            if tuple(self.orders[key])!=record: raise ValueError('conflicting cycle order')
            return False
        if quantity<=0 or price<=0 or not math.isfinite(price): raise ValueError('invalid cycle execution')
        if opening:
            self.opening_quantity+=quantity
            self.opening_value+=quantity*price
            self.quantity+=quantity
        else:
            if quantity>self.quantity: raise ValueError('cycle close exceeds remaining quantity')
            self.realized_gross+=quantity*((self.average-price) if self.direction=='SHORT' else (price-self.average))
            self.quantity-=quantity
        if fee is None: self.fee_known=False
        else: self.fees+=fee
        self.orders[key]=record
        return True

    def reserve(self, price, fee_rate=.0005, minimum_fee=5.):
        if self.direction!='SHORT' or not self.quantity: return 0.
        value=self.quantity*max(self.average,price)*(1+self.atr)
        return value+max(minimum_fee,value*fee_rate)

    def advance(self, trading_day):
        if trading_day not in self.trading_days: self.trading_days.append(trading_day)
        return len(self.trading_days)

    def advance_verified(self, trading_days):
        """Caller supplies verified exchange sessions, never a weekday approximation."""
        for day in sorted(set(trading_days)):
            if day >= self.opened_day: self.advance(day)
        return len(self.trading_days)

    def record(self): return asdict(self)


def exposure_limit(base_shares, lot=100, fraction=.5):
    if base_shares<0 or lot<=0: raise ValueError('invalid base/lot')
    return min(base_shares,max(lot,int(base_shares*fraction/lot)*lot))


def validate_cycles(cycles, base_shares, actual_shares):
    active=[c for c in cycles if c.quantity]
    expected=base_shares+sum(c.quantity*(1 if c.direction=='LONG' else -1) for c in active)
    if expected!=actual_shares: raise ValueError('cycle/account inventory mismatch')
    ids=[oid for c in cycles for oid in c.orders]
    if len(ids)!=len(set(ids)): raise ValueError('order belongs to multiple cycles')
    return True
