"""Pure trading-domain values. This module has no QMT dependency."""
from dataclasses import dataclass
from enum import Enum


class OrderEffect(Enum):
    OPEN_LONG = 'OPEN_LONG'
    CLOSE_LONG = 'CLOSE_LONG'
    OPEN_SHORT = 'OPEN_SHORT'
    CLOSE_SHORT = 'CLOSE_SHORT'

    @property
    def is_opening(self):
        return self in (self.OPEN_LONG, self.OPEN_SHORT)

    @property
    def is_sell(self):
        return self in (self.CLOSE_LONG, self.OPEN_SHORT)


class OrderStatus(Enum):
    SKIPPED = 'SKIP'
    FILLED = 'FILLED'
    PARTIAL = 'PARTIAL'
    UNFILLED = 'TIMEOUT'


@dataclass(frozen=True)
class OrderIntent:
    effect: OrderEffect
    shares: int
    reference_price: float
    label: str

    def __post_init__(self):
        if self.shares <= 0:
            raise ValueError('shares must be positive')


@dataclass(frozen=True)
class PortfolioSnapshot:
    shares: int
    sellable: int
    cash: float
    cost: float = 0.0
    last_price: float = 0.0
    valid: bool = True


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: int
    requested_shares: int
    filled_shares: int
    average_price: float
    terminal: bool
    rejected: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    status: OrderStatus
    signed_filled_shares: int
    order_id: object = None
    terminal_confirmed: bool = True
    reconciliation_reason: str = ''
    average_fill_price: float = 0.0


@dataclass(frozen=True)
class TradeLeg:
    entry_price: float
    shares: int

    def __post_init__(self):
        if self.entry_price <= 0:
            raise ValueError('entry_price must be positive')
        if self.shares <= 0:
            raise ValueError('shares must be positive')


class InventoryLedger:
    """Typed view of intraday legs that reserve T+1 sellable inventory."""

    def __init__(self, short_legs=(), long_legs=(), momentum_long_shares=0):
        self.short_legs = tuple(self._normalize(short_legs))
        self.long_legs = tuple(self._normalize(long_legs))
        self.momentum_long_shares = max(0, int(momentum_long_shares))

    @classmethod
    def from_runtime_state(cls, state):
        momentum_shares = 0
        if state.get('mom_state') in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING'):
            momentum_shares = state.get('mom_leg_shares', 0)
        return cls(
            short_legs=state.get('short_legs', ()),
            long_legs=state.get('long_legs', ()),
            momentum_long_shares=momentum_shares,
        )

    @staticmethod
    def _normalize(legs):
        for value in legs or ():
            if isinstance(value, TradeLeg):
                yield value
            else:
                price, shares = value
                yield TradeLeg(float(price), int(shares))

    @property
    def reserved_sellable_shares(self):
        return (sum(leg.shares for leg in self.long_legs) +
                self.momentum_long_shares)

    @property
    def open_short_shares(self):
        return sum(leg.shares for leg in self.short_legs)

    @property
    def open_long_shares(self):
        return (sum(leg.shares for leg in self.long_legs) +
                self.momentum_long_shares)

    @staticmethod
    def close_fifo(legs, shares):
        return remaining_legs(
            [(leg.entry_price, leg.shares) if isinstance(leg, TradeLeg) else leg
             for leg in legs], shares)


def remaining_legs(legs, closed_shares):
    """Consume FIFO trade legs and return normalized remaining legs."""
    remaining_to_close = max(0, int(closed_shares))
    remaining = []
    for price, shares in legs:
        shares = max(0, int(shares))
        used = min(shares, remaining_to_close)
        remaining_to_close -= used
        if shares > used:
            remaining.append((float(price), shares - used))
    return remaining
