"""Order-scoped, gross FIFO accounting. Fees are deliberately not estimated."""
import math


class ExecutionBook:
    def __init__(self):
        self.orders = set()
        self.legs = {}
        self.cycle_gross = {}

    def record(self, order_id, label, signed_shares, price):
        key = str(order_id)
        if key in self.orders:
            raise ValueError('order already accounted: ' + key)
        if not math.isfinite(price) or price <= 0 or not signed_shares:
            raise ValueError('invalid execution')
        risk = label in ('RISK-OFF sell', 'RISK-RESTORE buy')
        opening = label in (
            'REV-T sell', 'FWD-T buy', 'MOM short', 'MOM long',
            'RISK-OFF sell')
        short = (label == 'REV-T sell' or 'buyback' in label or
                 label == 'MOM short' or risk)
        group = ('RISK' if risk else
                 ('MOM ' if label.startswith('MOM') else '') +
                 ('SHORT' if short else 'LONG'))
        legs = self.legs.setdefault(group, [])
        quantity = abs(signed_shares)
        if not opening and quantity > sum(n for _, n in legs):
            raise ValueError('closing shares exceed recorded legs: ' + group)
        if opening:
            legs.append((price, quantity))
            gross = 0.0
        else:
            gross = 0.0
            remaining = quantity
            while remaining:
                entry, shares = legs[0]
                used = min(shares, remaining)
                gross += ((entry - price) if short else (price - entry)) * used
                remaining -= used
                if used == shares:
                    legs.pop(0)
                else:
                    legs[0] = (entry, shares - used)
        self.orders.add(key)
        self.cycle_gross[group] = self.cycle_gross.get(group, 0.0) + gross
        completed = not opening and not legs
        cycle = self.cycle_gross[group]
        if completed:
            self.cycle_gross[group] = 0.0
        return gross, completed, cycle
