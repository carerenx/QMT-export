"""Pure daily signal calculations used by RedisQMT DayT strategies."""
from . import config


def _sma(values, period):
    if len(values) < period:
        return 0.0
    return sum(values[-period:]) / float(period)


def _atr(highs, lows, closes, period=config.ATR_PERIOD):
    if len(closes) < period + 1:
        return 0.0
    ranges = []
    for index in range(1, len(closes)):
        ranges.append(max(highs[index] - lows[index],
                          abs(highs[index] - closes[index - 1]),
                          abs(lows[index] - closes[index - 1])))
    value = sum(ranges[:period]) / float(period)
    for item in ranges[period:]:
        value = (value * (period - 1) + item) / float(period)
    return value


def _rsi(closes, period=config.ATR_PERIOD):
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for before, after in zip(closes[-(period + 1):-1], closes[-period:]):
        change = after - before
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / float(period)
    average_loss = sum(losses) / float(period)
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _up_streak(closes):
    result = 0
    for before, after in reversed(list(zip(closes, closes[1:]))):
        if after <= before:
            break
        result += 1
    return result


def _dynamic_sell_multiplier(trend, atr_pct, atr_ratio, volume_ratio,
                             rsi_value, streak):
    if trend == "bear":
        base = config.SELL_TRIGGER_BASE_BEAR
    elif trend == "weak_bull":
        base = config.SELL_TRIGGER_BASE_WEAK_BULL
    else:
        base = config.SELL_TRIGGER_BASE_SIDEWAYS
    if trend == "bear":
        trend_factor = -0.25 if streak == 0 else -0.15
    elif trend == "strong_bull":
        trend_factor = 999.0
    elif trend == "weak_bull":
        trend_factor = 0.20 if streak >= 3 else 0.12 if streak >= 1 else 0.05
    else:
        trend_factor = 0.0
    atr_absolute = (-0.30 if atr_pct > 0.08 else -0.22 if atr_pct > 0.07
                    else -0.15 if atr_pct > 0.06 else -0.08 if atr_pct > 0.05
                    else 0.05 if atr_pct > 0.03 else 0.15 if atr_pct > 0.02
                    else 0.25)
    atr_relative = (-0.25 if atr_ratio > 1.50 else -0.18 if atr_ratio > 1.25
                    else -0.10 if atr_ratio > 1.10 else 0.0 if atr_ratio > 0.90
                    else 0.12 if atr_ratio > 0.70 else 0.20 if atr_ratio > 0.50
                    else 0.25)
    volatility_factor = max(-0.35, min(0.30,
                            atr_absolute * 0.55 + atr_relative * 0.45))
    if volume_ratio is None:
        volume_factor = 0.0
    else:
        volume_factor = (-0.25 if volume_ratio > 2 else -0.18 if volume_ratio > 1.5
                         else -0.08 if volume_ratio > 1.2 else 0.0 if volume_ratio > 0.8
                         else 0.12 if volume_ratio > 0.6 else 0.20 if volume_ratio > 0.4
                         else 0.25)
    rsi_factor = (-0.25 if rsi_value > 80 else -0.18 if rsi_value > 70
                  else -0.08 if rsi_value > 60 else -0.03 if rsi_value > 55
                  else 0.0 if rsi_value > 45 else 0.03 if rsi_value > 40
                  else 0.10 if rsi_value > 30 else 0.20 if rsi_value > 20
                  else 0.25)
    value = base + trend_factor + volatility_factor + volume_factor + rsi_factor
    return round(max(config.DYNAMIC_MULT_MIN,
                     min(config.DYNAMIC_MULT_MAX, value)), 2)


def compute_signal(opens, highs, lows, closes, volumes, today_open=None):
    """Return v39-compatible non-MOM daily signal data."""
    values = [opens, highs, lows, closes, volumes]
    if len(closes) < 60 or any(len(item) != len(closes) for item in values):
        return None
    opens = [float(item) for item in opens]
    highs = [float(item) for item in highs]
    lows = [float(item) for item in lows]
    closes = [float(item) for item in closes]
    volumes = [float(item) for item in volumes]
    current_open = float(today_open or opens[-1])
    current_close = closes[-1]
    current_atr = _atr(highs, lows, closes) or current_close * 0.03
    atr_pct = current_atr / current_close
    recent_atrs = []
    for end in range(max(config.ATR_PERIOD + 1, len(closes) - 19), len(closes) + 1):
        recent_atrs.append(_atr(highs[:end], lows[:end], closes[:end]))
    atr_average = sum(recent_atrs) / len(recent_atrs) if recent_atrs else current_atr
    atr_ratio = current_atr / atr_average if atr_average > 0 else 1.0
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    rsi_value = _rsi(closes)
    streak = _up_streak(closes)
    above = current_close > ma20 and ma5 > ma20
    below = current_close < ma20 and ma5 < ma20
    if above and rsi_value > config.STRONG_BULL_RSI and streak >= config.STRONG_BULL_STREAK:
        trend = "strong_bull"
    elif above:
        trend = "weak_bull"
    elif below:
        trend = "bear"
    else:
        trend = "sideways"
    volume_average = sum(volumes[-21:-1]) / 20.0
    volume_ratio = volumes[-1] / volume_average if volumes[-1] > 0 and volume_average > 0 else None
    multiplier = _dynamic_sell_multiplier(
        trend, atr_pct, atr_ratio, volume_ratio, rsi_value, streak)
    raw_trigger = current_open * (1.0 + atr_pct * multiplier * config.SELL_TRIGGER_SCALE)
    ranges = [(high - low) / opening if opening > 0 else 0.0
              for opening, high, low in zip(opens[-10:], highs[-10:], lows[-10:])]
    range_cap = current_open * (1.0 + sum(ranges) / 10.0 * config.DAILY_RANGE_CAP_MULT)
    sell_trigger = round(min(raw_trigger, range_cap), 2)
    do_short = True
    reason = ""
    if trend == "strong_bull":
        do_short = False
        reason = "strong bull blocks reverse T"
    elif volume_ratio is not None and volume_ratio < config.VOLUME_FILTER_RATIO:
        do_short = False
        reason = "low volume"
    elif rsi_value > config.RSI_OVERBOUGHT:
        do_short = False
        reason = "RSI overbought"
    buy_trigger = round(current_open * (1.0 - config.BUY_TRIGGER_PCT), 2)
    return {"do_short": do_short, "do_long": True,
            "blocked_reason": reason, "trend": trend,
            "sell_trigger": sell_trigger, "sell_trigger_raw": round(raw_trigger, 2),
            "buy_trigger": buy_trigger, "atr": current_atr,
            "atr_pct": atr_pct, "atr_ratio": atr_ratio, "rsi": rsi_value,
            "vol_ratio": volume_ratio, "up_streak": streak,
            "open_price": current_open, "close_yday": current_close,
            "range_capped": raw_trigger > range_cap}
