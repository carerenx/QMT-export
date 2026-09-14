# -*- coding: utf-8 -*-
"""Pure long-only allocation logic for one configurable A-share."""
from __future__ import division

import math


WEIGHT_TIERS = (0.30, 0.45, 0.60, 0.75, 1.00)


def _mean(values):
    return sum(values) / float(len(values)) if values else 0.0


def _true_ranges(highs, lows, closes):
    ranges = []
    for index in range(1, len(closes)):
        ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    return ranges


def _tier_index(weight):
    return min(range(len(WEIGHT_TIERS)), key=lambda index: abs(WEIGHT_TIERS[index] - weight))


def move_tier(weight, steps):
    index = max(0, min(len(WEIGHT_TIERS) - 1, _tier_index(weight) + int(steps)))
    return WEIGHT_TIERS[index]


def risk_state(drawdown):
    if drawdown >= 0.30:
        return "HALT_BUYS"
    if drawdown >= 0.25:
        return "DEFENSIVE_30"
    if drawdown >= 0.20:
        return "REDUCE_ONE_TIER"
    return "NORMAL"


def calculate_indicators(highs, lows, closes):
    if len(closes) < 140 or len(highs) != len(closes) or len(lows) != len(closes):
        raise ValueError("at least 140 aligned completed daily bars are required")
    values = [float(value) for value in closes]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("completed closes must be finite and positive")
    ma20 = _mean(values[-20:])
    ma60 = _mean(values[-60:])
    ma120 = _mean(values[-120:])
    ma60_previous = _mean(values[-80:-20])
    changes = [abs(values[index] - values[index - 1]) for index in range(len(values) - 20, len(values))]
    path = sum(changes)
    signed_efficiency = (values[-1] - values[-21]) / path if path > 0 else 0.0
    atr20 = _mean(_true_ranges(highs, lows, values)[-20:])
    prior_high20 = max(values[-21:-1])
    drawdown20_atr = (prior_high20 - values[-1]) / atr20 if atr20 > 0 else 0.0
    return {
        "close": values[-1],
        "previous_close": values[-2],
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ma60_slope": ma60 / ma60_previous - 1.0 if ma60_previous > 0 else 0.0,
        "signed_efficiency20": signed_efficiency,
        "atr20": atr20,
        "atr_pct": atr20 / values[-1],
        "prior_high20": prior_high20,
        "drawdown20_atr": drawdown20_atr,
        "ma20_distance_atr": (values[-1] - ma20) / atr20 if atr20 > 0 else 0.0,
    }


def classify_regime(indicators):
    close = indicators["close"]
    ma20 = indicators["ma20"]
    ma60 = indicators["ma60"]
    ma120 = indicators["ma120"]
    slope = indicators["ma60_slope"]
    efficiency = indicators["signed_efficiency20"]
    if close > ma120 and ma20 > ma60 and slope > 0 and efficiency >= 0.35:
        return "STRONG_BULL", 0.75
    if (close > ma120 and slope > 0) or (close > ma60 and ma20 > ma60):
        return "BULL", 0.60
    if close < ma120 and slope < 0:
        return "BEAR", 0.30
    return "SIDEWAYS", 0.45


def decide_allocation(highs, lows, closes, current_weight, equity_drawdown,
                      signal_date, execution_date, sessions_since_buy=None):
    indicators = calculate_indicators(highs, lows, closes)
    regime, target = classify_regime(indicators)
    reasons = ["REGIME_{}".format(regime)]
    long_term_intact = indicators["close"] > indicators["ma120"]
    can_add = sessions_since_buy is None or sessions_since_buy >= 3
    if (regime in ("STRONG_BULL", "BULL") and
            indicators["close"] > indicators["prior_high20"] and
            indicators["signed_efficiency20"] >= 0.35 and can_add):
        target = move_tier(target, 1)
        reasons.append("BREAKOUT_20D")
    elif (long_term_intact and indicators["drawdown20_atr"] >= 2.5 and
          indicators["close"] >= indicators["previous_close"] and can_add):
        target = move_tier(target, 2)
        reasons.append("PULLBACK_2_5_ATR_STABILIZED")
    elif (long_term_intact and indicators["drawdown20_atr"] >= 1.5 and
          indicators["close"] >= indicators["previous_close"] and can_add):
        target = move_tier(target, 1)
        reasons.append("PULLBACK_1_5_ATR_STABILIZED")
    if indicators["ma20_distance_atr"] >= 2.0 and indicators["close"] < indicators["previous_close"]:
        target = move_tier(target, -1)
        reasons.append("OVERHEATED_REVERSAL")
    if indicators["close"] < indicators["ma20"] and indicators["signed_efficiency20"] < 0:
        target = move_tier(target, -1)
        reasons.append("BELOW_MA20_NEGATIVE_EFFICIENCY")
    if indicators["close"] < indicators["ma60"]:
        target = min(target, 0.45)
        reasons.append("BELOW_MA60_CAP_45")
    if indicators["close"] < indicators["ma120"] and indicators["ma60_slope"] < 0:
        target = 0.30
        reasons.append("BEAR_FLOOR_30")
    state = risk_state(equity_drawdown)
    if state == "REDUCE_ONE_TIER":
        target = move_tier(target, -1)
        reasons.append("DRAWDOWN_20_REDUCE")
    elif state in ("DEFENSIVE_30", "HALT_BUYS"):
        target = 0.30
        reasons.append("DRAWDOWN_{}_FLOOR".format(25 if state == "DEFENSIVE_30" else 30))
    if not can_add and target > current_weight:
        target = current_weight
        reasons.append("BUY_COOLDOWN")
    return {
        "regime": regime,
        "current_weight": round(float(current_weight), 6),
        "target_weight": round(max(0.30, min(1.00, target)), 6),
        "reason_codes": reasons,
        "risk_state": state,
        "signal_date": str(signal_date),
        "execution_date": str(execution_date),
        "indicators": indicators,
    }


def target_order(current_shares, cash, price, target_weight, lot_size=100,
                 max_rebalance_fraction=0.25, allow_buy=True):
    if price <= 0 or lot_size <= 0:
        raise ValueError("price and lot_size must be positive")
    equity = float(cash) + int(current_shares) * float(price)
    desired = int(equity * target_weight / price / lot_size) * lot_size
    raw_delta = desired - int(current_shares)
    max_delta = int(equity * max_rebalance_fraction / price / lot_size) * lot_size
    if max_delta < lot_size:
        return 0
    delta = max(-max_delta, min(max_delta, raw_delta))
    if delta > 0:
        if not allow_buy:
            return 0
        affordable = int(float(cash) / price / lot_size) * lot_size
        delta = min(delta, affordable)
    else:
        delta = max(delta, -int(current_shares))
    return int(delta / lot_size) * lot_size
