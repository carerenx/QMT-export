"""Pure, causal minute-strength reference. No broker or filesystem access."""
from dataclasses import dataclass
import math

import numpy as np
from .indicators_np import atr as daily_atr


@dataclass(frozen=True)
class StrengthConfig:
    history_days: int = 80
    quantile: float = .35
    lookback: int = 10
    smooth: int = 3
    warmup: int = 15
    open_units: float = .25
    average_units: float = .10
    momentum_units: float = .10
    rebound_units: float = .25
    rebound_min: float = .008
    rebound_max: float = .02
    average_buffer: float = .05
    strong: float = .8
    max_gap: float = 90.

    def __post_init__(self):
        if (any(not isinstance(v, int) or v <= 0 for v in
                (self.history_days, self.lookback, self.smooth, self.warmup)) or
                self.history_days < 30 or self.warmup < self.lookback + self.smooth):
            raise ValueError('invalid history/lookback/smoothing/warmup windows')
        if not 0 <= self.quantile <= 1 or not 0 < self.strong <= 1:
            raise ValueError('invalid quantile/strong threshold')
        if not all(math.isfinite(v) and v > 0 for v in
                   (self.open_units, self.average_units, self.momentum_units,
                    self.rebound_units, self.rebound_min, self.rebound_max, self.max_gap)):
            raise ValueError('strength scales and time gap must be finite and positive')
        if self.rebound_min > self.rebound_max or not math.isfinite(self.average_buffer) or self.average_buffer < 0:
            raise ValueError('invalid rebound range/average buffer')


def lower_excursion_units(opens, highs, lows, closes, config=StrengthConfig()):
    """Caller supplies ONLY completed daily bars. Same normalization as v50."""
    n = min(config.history_days, len(opens), len(highs), len(lows), len(closes))
    if n < 30:
        raise ValueError('at least 30 complete daily bars required')
    o, h, l, c = [np.asarray(v[-n:], dtype=float) for v in (opens, highs, lows, closes)]
    if not all(np.isfinite(v).all() for v in (o, h, l, c)) or min(o.min(), c.min()) <= 0:
        raise ValueError('invalid completed daily history')
    span = max(2, int(n ** .5))
    values = daily_atr(h, l, c, span)
    units = [max(0., h[i + 1] / o[i + 1] - 1) / (values[i] / c[i])
             for i in range(span, n - 1) if values[i] > 0 and math.isfinite(values[i])]
    if not units:
        raise ValueError('no valid historical excursions')
    return float(np.quantile(units, config.quantile))


def reference_price(base, original, atr, lower, strength, average, low,
                    config=StrengthConfig()):
    upper = max(0., (original / base - 1) / atr)
    lower = min(upper, max(0., lower))
    strength = min(1., max(0., strength))
    units = lower + strength * (upper - lower)
    rebound = min(config.rebound_max, max(config.rebound_min, atr * config.rebound_units))
    prices = dict(linear=base * (1 + atr * units),
                  average=average * (1 + atr * config.average_buffer),
                  rebound=low * (1 + rebound))
    limit = max(prices, key=prices.get)
    result = min(original, math.ceil(prices[limit] * 100 - 1e-9) / 100)
    return dict(candidate=result, upper_units=upper, lower_units=lower,
                effective_units=units, limit='original' if result == original else limit,
                **prices)


class IntradayStrength:
    def __init__(self, config=StrengthConfig()):
        self.config = config
        self.reset()

    def reset(self):
        self.identity = None
        self.last_time = None
        self.bucket = None
        self.minutes = []
        self.scores = []
        self.count = 0
        self.effective = None
        self.result = {}

    def invalidate(self, original, phase):
        self.reset()
        self.effective = original
        self.result = dict(effective=original, phase=phase, minutes=0,
                           strength=0., components=[0., 0., 0.], touched=False,
                           cancel=False, new_minute=False, reason=phase)
        return dict(self.result)

    def update(self, now, price, opening, average, atr, base, original, lower,
               frozen=False, blocked=False):
        values = (now, price, opening, average, atr, base, original)
        if (not all(math.isfinite(float(v)) and v > 0 for v in values)
                or not math.isfinite(float(lower)) or lower < 0):
            return self.invalidate(original, 'INVALID')
        if blocked:
            return self.invalidate(original, 'EXISTING_LEG')
        identity = (opening, atr, base, original, lower)
        if self.identity != identity:
            self.invalidate(original, 'WARMUP')
            self.identity = identity
        if self.last_time is not None and (now <= self.last_time or now - self.last_time > self.config.max_gap):
            self.invalidate(original, 'GAP')
            self.identity = identity
        self.last_time = now
        minute = int(now // 60)
        new_minute = False
        if self.bucket and minute != self.bucket['minute']:
            bucket = self.bucket
            if minute != bucket['minute'] + 1:
                self.invalidate(original, 'GAP')
                self.identity, self.last_time = identity, now
            elif bucket['full']:
                self.minutes.append(bucket)
                self.count += 1
                self.minutes = self.minutes[-max(self.config.warmup, self.config.lookback + 1):]
                new_minute = True
                if len(self.minutes) > self.config.lookback:
                    previous = self.minutes[-self.config.lookback - 1]['price']
                    raw = [(bucket['price'] / opening - 1) / (atr * self.config.open_units),
                           (bucket['price'] / bucket['average'] - 1) / (atr * self.config.average_units),
                           (bucket['price'] / previous - 1) / (atr * self.config.momentum_units)]
                    components = [min(1., max(0., v)) for v in raw]
                    self.scores.append(max(components))
                    self.scores = self.scores[-self.config.smooth:]
                    self.result.update(components=components, strength=sum(self.scores) / len(self.scores))
            else:
                self.invalidate(original, 'INCOMPLETE_MINUTE')
                self.identity, self.last_time = identity, now
            self.bucket = None
        if self.bucket is None:
            # A startup mid-minute is not a full observed minute.
            self.bucket = dict(minute=minute, full=now - minute * 60 <= 5,
                               price=price, average=average, low=price)
        else:
            self.bucket.update(price=price, average=average, low=min(price, self.bucket['low']))

        result = dict(self.result, minutes=self.count, new_minute=new_minute,
                      effective=self.effective, touched=False, cancel=False)
        if self.count < self.config.warmup or len(self.scores) < self.config.smooth:
            result.update(phase='WARMUP', effective=original)
        else:
            strength = result['strength']
            if frozen:
                if strength >= self.config.strong and self.effective < original:
                    self.effective = original
                    result.update(phase='STRONG', cancel=True)
                else:
                    result['phase'] = 'FROZEN'
            elif price >= self.effective:
                result.update(phase='TOUCHED', touched=True)
            elif strength >= self.config.strong:
                self.effective = original
                result['phase'] = 'STRONG'
            elif new_minute:
                last = self.minutes[-1]
                detail = reference_price(base, original, atr, lower, strength,
                                         last['average'], min(v['low'] for v in self.minutes[-self.config.lookback:]),
                                         self.config)
                result.update(detail)
                if detail['candidate'] <= price:
                    result['phase'] = 'BELOW_MARKET_HOLD'
                else:
                    self.effective = detail['candidate']
                    result['phase'] = 'ADAPTIVE'
            else:
                result['phase'] = 'ADAPTIVE'
            result['effective'] = self.effective
            result['reason'] = result['phase']
        self.result = result
        return dict(result)
