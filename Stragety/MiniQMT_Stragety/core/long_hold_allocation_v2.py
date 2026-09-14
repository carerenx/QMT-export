# -*- coding: utf-8 -*-
"""Low-churn, bull-participation allocation policy for one A-share."""
from __future__ import division

from Stragety.MiniQMT_Stragety.core.long_hold_allocation import calculate_indicators
from Stragety.MiniQMT_Stragety.core.long_hold_allocation import classify_regime


REGIME_WEIGHTS = {
    "STRONG_BULL": 1.00,
    "BULL": 0.85,
    "SIDEWAYS": 0.60,
    "BEAR": 0.30,
}


def risk_state(drawdown):
    if drawdown >= 0.25:
        return "HALT_BUYS"
    if drawdown >= 0.20:
        return "DEFENSIVE_30"
    if drawdown >= 0.15:
        return "REDUCE_ONE_TIER"
    return "NORMAL"


def confirmed_regime(candidate, active, candidate_streak, confirmation_sessions=3):
    if active is None:
        return candidate
    if candidate == active:
        return active
    if candidate == "BEAR":
        return candidate
    if candidate_streak >= confirmation_sessions:
        return candidate
    return active


def decide_allocation(highs, lows, closes, current_weight, equity_drawdown,
                      signal_date, execution_date, active_regime=None,
                      candidate_streak=1):
    indicators = calculate_indicators(highs, lows, closes)
    candidate, unused_weight = classify_regime(indicators)
    regime = confirmed_regime(candidate, active_regime, candidate_streak)
    target = REGIME_WEIGHTS[regime]
    reasons = ["REGIME_{}".format(regime)]
    if candidate != regime:
        reasons.append("REGIME_CONFIRMATION_PENDING")
    if (regime in ("STRONG_BULL", "BULL") and
            indicators["close"] < indicators["ma60"] and
            indicators["signed_efficiency20"] < 0):
        target = min(target, 0.60)
        reasons.append("CONFIRMED_WEAKNESS_CAP_60")
    if (indicators["close"] < indicators["ma120"] and
            indicators["ma60_slope"] < 0):
        target = 0.30
        reasons.append("LONG_TREND_BROKEN_30")
    state = risk_state(equity_drawdown)
    if state == "REDUCE_ONE_TIER":
        target = min(target, 0.75)
        reasons.append("DRAWDOWN_15_CAP_75")
    elif state == "DEFENSIVE_30":
        target = min(target, 0.45)
        reasons.append("DRAWDOWN_20_CAP_45")
    elif state == "HALT_BUYS":
        target = 0.30
        reasons.append("DRAWDOWN_25_HALT_BUYS")
    return {
        "candidate_regime": candidate,
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
                 max_rebalance_fraction=0.25, min_weight_gap=0.10,
                 sessions_since_rebalance=None, rebalance_cooldown=5,
                 allow_buy=True):
    if price <= 0 or lot_size <= 0:
        raise ValueError("price and lot_size must be positive")
    equity = float(cash) + int(current_shares) * float(price)
    current_weight = current_shares * price / equity if equity > 0 else 0.0
    gap = float(target_weight) - current_weight
    risk_reduction = gap < 0 and target_weight <= 0.45
    if abs(gap) < min_weight_gap:
        return 0
    if (sessions_since_rebalance is not None and
            sessions_since_rebalance < rebalance_cooldown and
            not risk_reduction):
        return 0
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
