# -*- coding: utf-8 -*-
"""Simple, data-driven trend-quantile REV-T threshold."""
import math

import numpy as np

from .indicators_np import atr


# 使用最近多少个已完成日线。更大更稳定，较小更灵敏。
HISTORY_DAYS = 80
# 特征窗口 = 样本数的此指数次方；0.5 即平方根窗口。
LOOKBACK_EXPONENT = 0.5

MIN_HISTORY = 30
MIN_SPAN = 3
MIN_ATR_PERIOD = 2


def _quantile(values, rank):
    return float(np.quantile(np.asarray(values, dtype=float), rank))


def compute_quantile_trend_regime(opens, highs, lows, closes, volumes,
                                  today_open=None):
    """Return a transparent REV-T threshold from trend rank and excursions.

    The current ATR-normalized trend is ranked against its own history. That
    rank directly selects a quantile of historical next-day open-to-high moves:
    a stronger trend demands a higher sell trigger, while a weaker trend seeks
    a lower, easier-to-reach trigger. No regression and no bull hard block are
    involved.
    """
    n = min(HISTORY_DAYS, len(opens), len(highs), len(lows), len(closes),
            len(volumes))
    if n < MIN_HISTORY:
        return None
    opens, highs, lows, closes = (
        np.asarray(values[-n:], dtype=float)
        for values in (opens, highs, lows, closes))
    span = max(MIN_SPAN, int(n ** LOOKBACK_EXPONENT))
    atr_period = max(MIN_ATR_PERIOD, int(n ** LOOKBACK_EXPONENT))
    atr_values = np.asarray(atr(highs, lows, closes, atr_period), dtype=float)

    trend_scores, next_day_units = [], []
    for index in range(span, n - 1):
        atr_value = atr_values[index]
        if atr_value <= 0 or closes[index] <= 0 or opens[index + 1] <= 0:
            continue
        trend = ((closes[index] - np.mean(closes[index - span:index + 1])) /
                 atr_value)
        atr_pct = atr_value / closes[index]
        excursion = (highs[index + 1] / opens[index + 1] - 1.0) / atr_pct
        trend_scores.append(float(trend))
        next_day_units.append(float(excursion))
    if len(trend_scores) < MIN_HISTORY - span - 1:
        return None

    current_atr = atr_values[-1]
    if current_atr <= 0 or closes[-1] <= 0:
        return None
    trend_score = float((closes[-1] - np.mean(closes[-span:])) / current_atr)
    trend_rank = float(np.mean(np.asarray(trend_scores) <= trend_score))
    tail_rank = 1.0 / max(2, int(len(next_day_units) ** LOOKBACK_EXPONENT))
    trigger_quantile = tail_rank + trend_rank * (1.0 - 2.0 * tail_rank)
    trigger_units = max(0.0, _quantile(next_day_units, trigger_quantile))
    atr_pct = current_atr / closes[-1]
    open_price = (float(today_open) if today_open and float(today_open) > 0
                  else float(opens[-1]))
    trigger_pct = max(0.0, atr_pct * trigger_units)
    style = ('bear' if trend_rank <= tail_rank else
             'bull' if trend_rank >= 1.0 - tail_rank else 'sideways')

    return {
        'style': style,
        'trend_score': round(trend_score, 4),
        'trend_rank': round(trend_rank, 4),
        'trigger_quantile': round(trigger_quantile, 4),
        'trigger_units': round(trigger_units, 4),
        'trigger_pct': trigger_pct,
        'atr_pct': atr_pct,
        'sell_trigger': round(open_price * (1.0 + trigger_pct), 2),
        'open_price': open_price,
        'sample_count': len(next_day_units),
        'lookback_span': span,
        'tail_rank': round(tail_rank, 4),
    }
