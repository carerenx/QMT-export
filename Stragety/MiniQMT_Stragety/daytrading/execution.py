"""Resource checks and broker-order lifecycle orchestration."""
import time

from .domain import ExecutionResult, OrderEffect, OrderStatus


class ResourcePolicy:
    """Calculates the executable quantity from one validated account snapshot."""

    def __init__(self, min_lot=100, fee_buffer=0.001):
        self.min_lot = int(min_lot)
        self.fee_buffer = float(fee_buffer)

    def allowed_shares(self, intent, portfolio, reserved_sellable=0):
        if not portfolio.valid:
            return 0
        planned = int(intent.shares)
        reserved = max(0, int(reserved_sellable))
        if intent.effect.is_sell:
            available = int(portfolio.sellable)
            if intent.effect == OrderEffect.OPEN_SHORT:
                available = max(0, available - reserved)
            allowed = min(planned, available)
        else:
            price = float(intent.reference_price or portfolio.last_price)
            affordable = int(portfolio.cash / (price * (1.0 + self.fee_buffer))) if price > 0 else 0
            allowed = min(planned, affordable)
            if intent.effect == OrderEffect.OPEN_LONG:
                allowed = min(allowed, max(0, int(portfolio.sellable) - reserved))
        return allowed if allowed >= self.min_lot else 0


class ExecutionCoordinator:
    """Deep Module: validate, submit, inspect, cancel, and reconcile one order."""

    def __init__(self, adapter, resource_policy=None, timeout_sec=8.0,
                 cancel_timeout_sec=2.0, poll_interval_sec=0.25,
                 clock=None, sleeper=None):
        self.adapter = adapter
        self.resource_policy = resource_policy or ResourcePolicy()
        self.timeout_sec = float(timeout_sec)
        self.cancel_timeout_sec = float(cancel_timeout_sec)
        self.poll_interval_sec = float(poll_interval_sec)
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep

    def execute(self, intent, reserved_sellable=0, dry_run=False):
        portfolio = self.adapter.portfolio_snapshot()
        allowed = self.resource_policy.allowed_shares(
            intent, portfolio, reserved_sellable=reserved_sellable)
        if allowed <= 0:
            return ExecutionResult(OrderStatus.SKIPPED, 0)

        direction = -1 if intent.effect.is_sell else 1
        if dry_run:
            return ExecutionResult(
                OrderStatus.FILLED, direction * allowed,
                average_fill_price=float(intent.reference_price))

        order_id = self.adapter.submit(intent, direction * allowed)
        if not isinstance(order_id, int) or order_id <= 0:
            return ExecutionResult(OrderStatus.UNFILLED, 0, order_id=order_id)

        deadline = self.clock() + max(0.0, self.timeout_sec)
        last = None
        while True:
            last = self.adapter.order_snapshot(order_id)
            result = self._terminal_result(last, order_id, allowed, direction)
            if result is not None:
                return result
            if self.clock() >= deadline:
                break
            self.sleeper(self.poll_interval_sec)

        cancel_sent = self.adapter.cancel(order_id)
        cancel_deadline = self.clock() + max(0.0, self.cancel_timeout_sec)
        while self.clock() < cancel_deadline:
            last = self.adapter.order_snapshot(order_id) or last
            if last is not None and last.terminal:
                break
            self.sleeper(min(0.1, self.poll_interval_sec))

        terminal = bool(last is not None and last.terminal)
        self.adapter.set_order_pending(not terminal)
        if last is None:
            return ExecutionResult(
                OrderStatus.UNFILLED, 0, order_id, False,
                'cannot read final state for order {}'.format(order_id))
        filled = min(allowed, max(0, int(last.filled_shares)))
        status = self._status(filled, allowed)
        reason = ''
        if not cancel_sent or not terminal:
            reason = 'cancellation terminal state unconfirmed for order {}'.format(order_id)
        return ExecutionResult(
            status, direction * filled, order_id, terminal, reason,
            float(last.average_price or 0.0))

    @staticmethod
    def _status(filled, expected):
        if filled >= expected:
            return OrderStatus.FILLED
        if filled > 0:
            return OrderStatus.PARTIAL
        return OrderStatus.UNFILLED

    def _terminal_result(self, snapshot, order_id, expected, direction):
        if snapshot is None:
            return None
        filled = min(expected, max(0, int(snapshot.filled_shares)))
        if filled >= expected or snapshot.terminal:
            self.adapter.set_order_pending(False)
            return ExecutionResult(
                self._status(filled, expected), direction * filled,
                order_id, True, '', float(snapshot.average_price or 0.0))
        return None
