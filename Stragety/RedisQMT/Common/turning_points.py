"""Reusable price turning-point confirmation for intraday T strategies."""


class TurningPointTracker(object):
    """Track a peak pullback or dip rebound and emit one trading action."""

    def __init__(self, direction, threshold, action):
        if direction not in ("peak", "dip"):
            raise ValueError("direction must be 'peak' or 'dip'")
        if float(threshold) <= 0:
            raise ValueError("threshold must be positive")
        self.direction = direction
        self.threshold = float(threshold)
        self.action = str(action)
        self.extreme_price = 0.0
        self.armed = False
        self.triggered = False

    def arm(self, reference_price):
        price = float(reference_price)
        if price <= 0:
            raise ValueError("reference_price must be positive")
        self.extreme_price = price
        self.armed = True
        self.triggered = False

    def update(self, price):
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        if not self.armed or self.triggered or price <= 0:
            return None
        if self.direction == "peak":
            self.extreme_price = max(self.extreme_price, price)
            move = (self.extreme_price - price) / self.extreme_price
        else:
            self.extreme_price = min(self.extreme_price, price)
            move = (price - self.extreme_price) / self.extreme_price
        if move + 1e-12 < self.threshold:
            return None
        self.triggered = True
        return self.action

