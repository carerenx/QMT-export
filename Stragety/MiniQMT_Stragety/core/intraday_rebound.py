"""Causal intraday entry reference. Does not submit or alter exit targets."""
import math


def rebound_reference(state, now, price, opening, average, atr, original,
                      window=600, confirm=180, rebound_units=.25,
                      rebound_min=.008, rebound_max=.02,
                      average_units=.05, weak_units=.08, strong_units=.15,
                      reentry_base=0, reentry_units=0, reentry_discount=.35,
                      weakness_full_units=.25):
    values = (now, price, opening, average, atr, original)
    if not all(math.isfinite(float(v)) and v > 0 for v in values):
        state.clear()
        return original, 'INVALID'
    history = state.setdefault('history', [])
    if history and (now <= history[-1][0] or now - history[-1][0] > 90):
        state.clear()
        history = state.setdefault('history', [])
    history.append((now, price))
    history[:] = [(t, p) for t, p in history if t >= now - window]
    strong = price >= opening or price >= average * (1 + atr * strong_units)
    if strong:
        state.pop('reference', None)
        state.pop('weak_since', None)
        return original, 'STRONG'
    weak = (price <= min(opening, average) * (1 - atr * weak_units)
            and price <= history[0][1])
    if weak:
        state.setdefault('weak_since', now)
    else:
        state.pop('weak_since', None)
    ready = (now - history[0][0] >= confirm and
             now - state.get('weak_since', now) >= confirm)
    if ready and now - state.get('updated', 0) >= 60:
        # Do not move the reference upwards to chase an approaching rebound.
        if price < state.get('reference', original):
            low = min(p for _, p in history)
            rebound = min(rebound_max, max(rebound_min, atr * rebound_units))
            candidate = min(original, math.ceil(max(
                low * (1 + rebound), average * (1 + atr * average_units)) * 100 - 1e-9) / 100)
            detail = {}
            if reentry_base > 0 and reentry_units > 0:
                # Continuous weakness: gap below both opening and average,
                # normalized by an ATR distance. No hardcoded target price.
                weakness = min(1., max(0., (min(opening, average) / price - 1) /
                                      max(atr * weakness_full_units, 1e-9)))
                units = reentry_units * (1 - min(1., max(0., reentry_discount)) * weakness)
                linear_price = reentry_base * (1 + atr * units)
                candidate = min(original, math.ceil(max(
                    linear_price, average * (1 + atr * average_units)) * 100 - 1e-9) / 100)
                detail = dict(weakness=weakness, effective_units=units,
                              reentry_base=reentry_base, reentry_units=reentry_units,
                              reentry_discount=reentry_discount)
            if candidate <= state.get('reference', original):
                state.update(detail)
            state['reference'] = min(state.get('reference', original), candidate)
            state.update(updated=now, low=low, average=average, rebound=rebound)
    return state.get('reference', original), 'REBOUND' if 'reference' in state else 'WARMUP'
