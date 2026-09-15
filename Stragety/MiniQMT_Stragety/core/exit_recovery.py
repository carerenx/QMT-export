"""Serializable per-cycle exit intent; no broker calls or inferred intrabar prices."""
import math


class ExitRecovery:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def observe(self, minute, price, sell, atr, original, tighten_minutes=30,
                tighten_mult=.60, trail_units=0.):
        s = self.state
        if not all(math.isfinite(x) and x > 0 for x in (price, sell, atr, original)):
            s.pop('minute', None)
            return
        if minute <= s.get('minute', -1):
            return
        previous = s.get('minute')
        s['minute'] = minute
        if previous is not None and minute - previous == 1:
            s['elapsed'] = s.get('elapsed', 0) + 1
        s['low'] = min(s.get('low', price), price)
        s['target'] = max(s.get('target', original), original)
        if s.get('elapsed', 0) >= tighten_minutes:
            s['target'] = max(s['target'], round(sell * (1 - atr * .15 * tighten_mult), 2))
        if trail_units > 0 and s['low'] <= sell * (1 - atr * trail_units):
            s['trail_armed'] = True

    def begin(self, price, atr, chase_units=.05):
        if not self.state.get('pending'):
            self.state.update(pending=True, first=price,
                cap=math.floor(price * (1 + atr * chase_units) * 100 + 1e-8) / 100,
                attempts=0)

    def permit(self, minute, ask, maximum=3):
        s = self.state
        if s.get('attempt_minute') == minute:
            return False
        if s.get('attempts', 0) >= maximum:
            s['alert'] = 'RETRY_EXHAUSTED'
            return False
        if not math.isfinite(ask) or ask <= 0 or ask > s['cap']:
            s['alert'] = 'CHASE_CAP'
            return False
        s.update(attempt_minute=minute, attempts=s.get('attempts', 0) + 1, alert='')
        return True
