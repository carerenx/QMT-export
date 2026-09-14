# -*- coding: utf-8 -*-
"""Trend allocation with conditional defence and staged risk recovery."""
from __future__ import division

from Stragety.MiniQMT_Stragety.core.long_hold_allocation import calculate_indicators
from Stragety.MiniQMT_Stragety.core.long_hold_allocation import classify_regime
from Stragety.MiniQMT_Stragety.core.long_hold_allocation_v2 import REGIME_WEIGHTS
from Stragety.MiniQMT_Stragety.core.long_hold_allocation_v2 import target_order


RISK_CAPS = {
    "NORMAL": 1.00,
    "DEFENSIVE_75": 0.75,
    "DEFENSIVE_45": 0.45,
    "HALT_30": 0.30,
    "RECOVERY_45": 0.45,
    "RECOVERY_60": 0.60,
    "RECOVERY_75": 0.75,
}


def confirmed_regime(candidate, active, candidate_streak, indicators,
                     confirmation_sessions=3):
    if active is None or candidate == active:
        return candidate
    if candidate == "BEAR":
        return candidate
    weakness = (
        indicators["close"] < indicators["ma20"] and
        indicators["signed_efficiency20"] < 0)
    if active == "STRONG_BULL" and candidate == "BULL" and not weakness:
        return active
    if candidate_streak >= confirmation_sessions:
        return candidate
    return active


def next_risk_mode(drawdown, previous_mode, mode_age, indicators):
    weakness = (
        indicators["close"] < indicators["ma20"] and
        indicators["signed_efficiency20"] < 0)
    severe_weakness = (
        indicators["close"] < indicators["ma60"] and
        indicators["signed_efficiency20"] < 0)
    recovery = (
        indicators["close"] > indicators["ma20"] and
        indicators["ma20"] > indicators["ma60"] and
        indicators["close"] >= indicators["previous_close"] and
        indicators["signed_efficiency20"] > 0.20)
    if drawdown >= 0.25 and severe_weakness:
        return "HALT_30"
    if previous_mode != "NORMAL" and recovery and mode_age >= 4:
        upgrades = {
            "HALT_30": "RECOVERY_45",
            "DEFENSIVE_45": "RECOVERY_60",
            "DEFENSIVE_75": "RECOVERY_75",
            "RECOVERY_45": "RECOVERY_60",
            "RECOVERY_60": "RECOVERY_75",
            "RECOVERY_75": "NORMAL",
        }
        return upgrades.get(previous_mode, previous_mode)
    if previous_mode != "NORMAL" and recovery:
        return previous_mode
    if drawdown >= 0.20 and weakness:
        return "DEFENSIVE_45"
    if drawdown >= 0.15:
        return "DEFENSIVE_75"
    if previous_mode == "NORMAL":
        return "NORMAL"
    if drawdown < 0.10:
        return "NORMAL"
    return previous_mode


def decide_allocation(highs, lows, closes, current_weight, equity_drawdown,
                      signal_date, execution_date, active_regime=None,
                      candidate_streak=1, previous_risk_mode="NORMAL",
                      risk_mode_age=0):
    indicators = calculate_indicators(highs, lows, closes)
    candidate = classify_regime(indicators)[0]
    regime = confirmed_regime(
        candidate, active_regime, candidate_streak, indicators)
    risk_mode = next_risk_mode(
        equity_drawdown, previous_risk_mode, risk_mode_age, indicators)
    target = min(REGIME_WEIGHTS[regime], RISK_CAPS[risk_mode])
    reasons = ["REGIME_{}".format(regime), "RISK_{}".format(risk_mode)]
    if candidate != regime:
        reasons.append("REGIME_CONFIRMATION_PENDING")
    if risk_mode.startswith("RECOVERY"):
        reasons.append("TREND_STAGED_RECOVERY")
    if (indicators["close"] < indicators["ma120"] and
            indicators["ma60_slope"] < 0):
        target = 0.30
        reasons.append("LONG_TREND_BROKEN_30")
    return {
        "candidate_regime": candidate,
        "regime": regime,
        "risk_mode": risk_mode,
        "current_weight": round(float(current_weight), 6),
        "target_weight": round(max(0.30, min(1.00, target)), 6),
        "reason_codes": reasons,
        "risk_state": "HALT_BUYS" if risk_mode == "HALT_30" else risk_mode,
        "signal_date": str(signal_date),
        "execution_date": str(execution_date),
        "indicators": indicators,
    }
