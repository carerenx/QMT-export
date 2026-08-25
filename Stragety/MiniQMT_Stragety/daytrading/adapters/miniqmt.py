"""MiniQMT translation at the execution Seam."""
from ..domain import OrderSnapshot, PortfolioSnapshot


class MiniQmtExecutionAdapter:
    def __init__(self, connector, context, account, stock_code,
                 portfolio_provider, order_function):
        self.connector = connector
        self.context = context
        self.account = account
        self.stock_code = stock_code
        self.portfolio_provider = portfolio_provider
        self.order_function = order_function

    def portfolio_snapshot(self):
        value = self.portfolio_provider()
        return PortfolioSnapshot(
            shares=int(value.get('shares', 0)),
            sellable=int(value.get('can_use', 0)),
            cash=float(value.get('cash', 0.0)),
            cost=float(value.get('cost', 0.0)),
            last_price=float(value.get('price', 0.0)),
            valid=bool(value.get('valid', False)),
        )

    def submit(self, intent, signed_shares):
        return self.order_function(
            self.stock_code, signed_shares, 'COMPETE', intent.reference_price,
            self.context, self.account)

    def order_snapshot(self, order_id):
        value = self.connector.get_order_snapshot(order_id)
        if value is None:
            return None
        return OrderSnapshot(
            order_id=int(value.get('order_id', order_id)),
            requested_shares=int(value.get('order_volume', 0) or 0),
            filled_shares=int(value.get('traded_volume', 0) or 0),
            average_price=float(value.get('traded_price', 0.0) or 0.0),
            terminal=bool(value.get('terminal', False)),
            rejected=bool(value.get('rejected', False)),
        )

    def cancel(self, order_id):
        return bool(self.connector.cancel_order(order_id))

    def set_order_pending(self, pending):
        self.connector.order_pending = bool(pending)
