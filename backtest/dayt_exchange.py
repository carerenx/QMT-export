"""Deterministic event exchange. Prices become visible only at processed events.

Research fee assumptions, not broker tariffs. Input volume is in shares.
No intrabar high/low order is inferred; only OPEN events can fill orders.
"""
from dataclasses import dataclass
import math


@dataclass
class Order:
    order_id: int
    signed_quantity: int
    limit: float | None
    submitted_event: int
    label: str = ''
    traded_volume: int = 0
    traded_value: float = 0.
    fee: float = 0.
    order_status: int = 50
    reason: str = ''

    @property
    def order_volume(self): return abs(self.signed_quantity)
    @property
    def traded_price(self): return self.traded_value/self.traded_volume if self.traded_volume else 0.


class Exchange:
    def __init__(self, cash=100000., shares=200, rate=.0005, minimum=5., participation=.01, lot=100):
        self.cash, self.shares, self.sellable = cash, shares, shares
        self.rate, self.minimum, self.participation, self.lot = rate, minimum, participation, lot
        self.orders, self.fills, self.rejections = {}, [], []
        self.event, self.day, self.price = 0, None, None

    def submit(self, quantity, limit=None, label=''):
        if not isinstance(quantity,int) or not quantity or abs(quantity)%self.lot:
            raise ValueError('order must be a nonzero whole lot')
        if limit is not None and (not math.isfinite(limit) or limit<=0):
            raise ValueError('invalid limit')
        oid=len(self.orders)+1
        self.orders[oid]=Order(oid,quantity,limit,self.event,label)
        return oid

    def cancel(self, oid):
        order=self.orders[oid]
        if order.order_status in (50,55):
            order.order_status=53 if order.traded_volume else 54

    def close(self, price):
        self.event+=1
        self.price=price

    def opening(self, day, time, price, volume_shares, tradable=True, one_sided_limit=False):
        """One event; capacity is shared by all orders, in submission order."""
        self.event+=1
        if self.day!=day:
            self.sellable=self.shares
            self.day=day
        self.price=price
        capacity=int(max(0,volume_shares)*self.participation/self.lot)*self.lot
        valid=tradable and not one_sided_limit and math.isfinite(price) and price>0
        for order in self.orders.values():
            if order.order_status not in (50,55) or order.submitted_event>=self.event: continue
            buying=order.signed_quantity>0
            reason=''
            if not valid: reason='NON_TRADABLE'
            elif capacity<self.lot: reason='VOLUME_CAP'
            elif order.limit is not None and ((buying and price>order.limit) or (not buying and price<order.limit)):
                reason='LIMIT_NOT_MET'
            if reason:
                order.reason=reason
                self.rejections.append(dict(time=time,order=order.order_id,reason=reason))
                continue
            quantity=min(capacity,order.order_volume-order.traded_volume)
            if not buying: quantity=min(quantity,self.sellable//self.lot*self.lot)
            while quantity>0:
                value=quantity*price
                fee=max(self.minimum,(order.traded_value+value)*self.rate)-order.fee
                if not buying or value+fee<=self.cash+1e-8: break
                quantity-=self.lot
            if not quantity:
                order.reason='CASH' if buying else 'T1_SELLABLE'
                self.rejections.append(dict(time=time,order=order.order_id,reason=order.reason))
                continue
            signed=quantity if buying else -quantity
            self.cash-=signed*price+fee
            self.shares+=signed
            if not buying: self.sellable-=quantity
            order.traded_volume+=quantity
            order.traded_value+=value
            order.fee+=fee
            order.order_status=56 if order.traded_volume==order.order_volume else 55
            order.reason=''
            capacity-=quantity
            self.fills.append(dict(time=time,event=self.event,order=order.order_id,label=order.label,
                                   shares=signed,price=price,fee=fee,cash=self.cash,position=self.shares))

    @property
    def equity(self): return self.cash+self.shares*self.price if self.price is not None else None
