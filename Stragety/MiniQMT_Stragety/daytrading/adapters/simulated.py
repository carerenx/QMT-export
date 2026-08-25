"""Deterministic adapter used by unit tests and event-replay backtests."""
from collections import defaultdict

from ..domain import OrderSnapshot


class SimulatedExecutionAdapter:
    def __init__(self, portfolio, snapshots=None):
        self.portfolio = portfolio
        self.snapshots = defaultdict(list)
        self.cancelled = []
        self.submitted = []
        self.order_pending = False
        self._next_order_id = 1
        if snapshots:
            for order_id, values in snapshots.items():
                self.snapshots[int(order_id)] = list(values)

    def portfolio_snapshot(self):
        return self.portfolio

    def submit(self, intent, signed_shares):
        order_id = self._next_order_id
        self._next_order_id += 1
        self.submitted.append((order_id, intent, signed_shares))
        self.order_pending = True
        if not self.snapshots[order_id]:
            self.snapshots[order_id].append(OrderSnapshot(
                order_id, abs(signed_shares), abs(signed_shares),
                intent.reference_price, True))
        return order_id

    def order_snapshot(self, order_id):
        values = self.snapshots[int(order_id)]
        if not values:
            return None
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return True

    def set_order_pending(self, pending):
        self.order_pending = bool(pending)
