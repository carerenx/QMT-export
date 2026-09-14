"""One-cycle intraday reverse-T overlay with a protected core position."""
from __future__ import annotations


T_FRACTIONS = {
    "STRONG_BULL": 0.00,
    "BULL": 0.10,
    "SIDEWAYS": 0.20,
    "BEAR": 0.00,
}


def satellite_shares(position, regime, risk_state, lot_size=100):
    if risk_state != "NORMAL" or position <= 0:
        return 0
    fraction = T_FRACTIONS.get(regime, 0.0)
    return int(position * fraction / lot_size) * lot_size


def admission(completed_closes, regime, risk_state):
    if satellite_shares(1000, regime, risk_state) <= 0:
        return False, "REGIME_OR_RISK_BLOCK"
    if len(completed_closes) < 6:
        return False, "INSUFFICIENT_HISTORY"
    closes = [float(value) for value in completed_closes]
    return_3d = closes[-1] / closes[-4] - 1.0
    return_5d = closes[-1] / closes[-6] - 1.0
    if return_3d > 0.0:
        return False, "POSITIVE_3D_MOMENTUM"
    if return_5d > 0.03:
        return False, "EXCESSIVE_5D_MOMENTUM"
    return True, "ALLOWED"


def simulate_day(bars, completed_closes, atr20, position, regime, risk_state,
                 fee_rate=0.0005, minimum_fee=5.0, tick_size=0.01):
    quantity = satellite_shares(position, regime, risk_state)
    allowed, reason = admission(completed_closes, regime, risk_state)
    if not allowed or quantity <= 0 or atr20 <= 0 or bars.empty:
        return {"trades": [], "gross": 0.0, "fees": 0.0, "reason": reason}
    prior_close = float(completed_closes[-1])
    trigger = prior_close + 0.25 * float(atr20)
    peak = None
    trigger_index = None
    sell = None
    for index, row in enumerate(bars.itertuples()):
        stamp = str(row.Index)
        if stamp[8:14] < "094000" or stamp[8:14] >= "142000":
            continue
        price = float(row.close)
        if trigger_index is None and price >= trigger:
            trigger_index = index
            peak = price
            continue
        if trigger_index is None:
            continue
        peak = max(peak, price)
        extension_ok = peak >= trigger * 1.0015
        pullback_ok = price <= peak * 0.998
        bars_ok = index - trigger_index >= 2
        if extension_ok and pullback_ok and bars_ok:
            fill = max(tick_size, price - tick_size)
            fee = max(minimum_fee, quantity * fill * fee_rate)
            sell = {"time": stamp, "shares": -quantity, "price": fill, "fee": fee,
                    "label": "SATELLITE_REV_SELL"}
            sell_index = index
            break
    if sell is None:
        return {"trades": [], "gross": 0.0, "fees": 0.0, "reason": "NO_CONFIRMED_REVERSAL"}
    target_drop = max(0.10 * float(atr20), sell["price"] * 0.01)
    buy = None
    tuples = list(bars.itertuples())
    for row in tuples[sell_index + 1:]:
        stamp = str(row.Index)
        price = float(row.close)
        if price <= sell["price"] - target_drop:
            fill = price + tick_size
            fee = max(minimum_fee, quantity * fill * fee_rate)
            buy = {"time": stamp, "shares": quantity, "price": fill, "fee": fee,
                   "label": "SATELLITE_REV_BUYBACK"}
            break
    if buy is None:
        row = tuples[-1]
        fill = float(row.close) + tick_size
        fee = max(minimum_fee, quantity * fill * fee_rate)
        buy = {"time": str(row.Index), "shares": quantity, "price": fill, "fee": fee,
               "label": "SATELLITE_REV_SESSION_EXIT"}
    gross = quantity * (sell["price"] - buy["price"])
    fees = sell["fee"] + buy["fee"]
    return {"trades": [sell, buy], "gross": gross, "fees": fees,
            "reason": "COMPLETE"}
