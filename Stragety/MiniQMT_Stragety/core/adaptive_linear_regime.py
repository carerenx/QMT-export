# -*- coding: utf-8 -*-
"""Data-driven linear regime and REV-T trigger calculation."""
import math

import numpy as np

from .indicators_np import atr

FEATURE_COUNT = 5
# These are data-validity and numerical-stability requirements, not market
# thresholds. All timing, coefficients and trigger limits are learned from the
# supplied history.
MIN_HISTORY_OBSERVATIONS = FEATURE_COUNT * (FEATURE_COUNT + 1)
MIN_TRAINING_OBSERVATIONS = FEATURE_COUNT * 2 + 2
MIN_ROLLING_SPAN = 3
MIN_ATR_PERIOD = 2
NUMERICAL_EPSILON = 1e-12


def _quantile(values, q):
    return float(np.quantile(np.asarray(values, dtype=float), q))


def _features(opens, highs, lows, closes, volumes):
    """Return ATR-normalized, scale-free daily features."""
    n = len(closes)
    span = max(MIN_ROLLING_SPAN, int(math.sqrt(n)))
    atr_period = max(MIN_ATR_PERIOD, int(math.sqrt(n)))
    atr_values = np.asarray(atr(highs, lows, closes, atr_period), dtype=float)
    close = np.asarray(closes, dtype=float)
    volume = np.asarray(volumes, dtype=float)
    rows = []
    for i in range(span, n):
        local_atr = atr_values[i]
        if local_atr <= 0 or close[i - 1] <= 0:
            rows.append(None)
            continue
        history = close[i - span:i + 1]
        atr_mean = float(np.mean(atr_values[i - span:i + 1]))
        volume_mean = float(np.mean(volume[i - span:i + 1]))
        rows.append(np.asarray([
            (close[i] - close[i - span]) / local_atr,
            (close[i] - close[i - 1]) / local_atr,
            (close[i] - float(np.max(history))) / local_atr,
            local_atr / atr_mean - 1.0 if atr_mean > 0 else 0.0,
            volume[i] / volume_mean - 1.0 if volume_mean > 0 else 0.0,
        ], dtype=float))
    return span, atr_values, rows


def _fit_linear(features, target):
    """Fit a ridge-stabilized linear model; ridge strength is data-derived."""
    x = np.asarray(features, dtype=float)
    y = np.asarray(target, dtype=float)
    x_mean = x.mean(axis=0)
    x_std = x.std(axis=0)
    x_std[x_std < NUMERICAL_EPSILON] = 1.0
    z = (x - x_mean) / x_std
    y_mean = float(y.mean())
    xtx = z.T @ z
    ridge = float(np.trace(xtx) / max(1, len(z) * z.shape[1]))
    beta = np.linalg.solve(xtx + np.eye(z.shape[1]) * ridge, z.T @ (y - y_mean))
    return x_mean, x_std, y_mean, beta


def _predict(feature, model):
    mean, std, intercept, beta = model
    return float(intercept + np.dot((feature - mean) / std, beta))


def compute_adaptive_linear_regime(opens, highs, lows, closes, volumes,
                                   today_open=None):
    """Calculate a continuous style score and an adaptive linear REV-T trigger.

    Coefficients are fitted from the supplied daily history.  The market style
    boundaries and trigger guardrails are empirical quantiles of that same
    history, not hand-tuned constants.
    """
    n = min(len(opens), len(highs), len(lows), len(closes), len(volumes))
    if n < MIN_HISTORY_OBSERVATIONS:
        return None
    opens, highs, lows, closes, volumes = (
        list(map(float, values[-n:])) for values in (opens, highs, lows, closes, volumes)
    )
    span, atr_values, rows = _features(opens, highs, lows, closes, volumes)
    horizon = max(1, span // 3)
    style_x, future_return = [], []
    trigger_x, next_day_excursion = [], []
    for i in range(span, n - horizon):
        feature = rows[i - span]
        atr_pct = atr_values[i] / closes[i] if closes[i] > 0 else 0.0
        if feature is None or atr_pct <= 0:
            continue
        style_x.append(feature)
        future_return.append((closes[i + horizon] / closes[i] - 1.0) / atr_pct)
        # At the close of day i, predict day i+1's open-to-high excursion.
        # This prevents using same-day high/volume/close information to fit a
        # threshold that is meant to be available at the next open.
        if i + 1 < n and opens[i + 1] > 0:
            trigger_x.append(feature)
            next_day_excursion.append(
                (highs[i + 1] / opens[i + 1] - 1.0) / atr_pct)
    if (len(style_x) < MIN_TRAINING_OBSERVATIONS or
            len(trigger_x) < MIN_TRAINING_OBSERVATIONS):
        return None

    style_model = _fit_linear(style_x, future_return)
    trigger_model = _fit_linear(trigger_x, next_day_excursion)
    current_feature = rows[-1]
    if current_feature is None:
        return None
    style_score = _predict(current_feature, style_model)
    residuals = [target - _predict(feature, style_model)
                 for feature, target in zip(style_x, future_return)]
    neutral_band = float(np.std(residuals) / math.sqrt(len(residuals)))
    bear_boundary = -neutral_band
    bull_boundary = neutral_band
    style = 'bear' if style_score < bear_boundary else (
        'bull' if style_score > bull_boundary else 'sideways')

    predicted_units = _predict(current_feature, trigger_model)
    observed_units = np.asarray(next_day_excursion, dtype=float)
    tail_rank = 1.0 / max(2, int(math.sqrt(len(observed_units))))
    trigger_units = min(
        _quantile(observed_units, 1.0 - tail_rank),
        max(_quantile(observed_units, tail_rank), predicted_units))
    open_price = float(today_open) if today_open and float(today_open) > 0 else opens[-1]
    atr_pct = atr_values[-1] / closes[-1] if closes[-1] > 0 else 0.0
    trigger_pct = max(0.0, atr_pct * trigger_units)
    sell_trigger = round(open_price * (1.0 + trigger_pct), 2)

    names = ('trend', 'momentum', 'drawdown', 'volatility', 'volume')
    return {
        'style': style,
        'style_score': round(style_score, 4),
        'bear_boundary': round(bear_boundary, 4),
        'bull_boundary': round(bull_boundary, 4),
        'trigger_units': round(trigger_units, 4),
        'trigger_pct': trigger_pct,
        'sell_trigger': sell_trigger,
        'open_price': open_price,
        'feature_values': dict(zip(names, [round(float(v), 4) for v in current_feature])),
        'style_weights': dict(zip(names, [round(float(v), 4) for v in style_model[3]])),
        'trigger_weights': dict(zip(names, [round(float(v), 4) for v in trigger_model[3]])),
        'sample_count': len(style_x),
        'trigger_sample_count': len(trigger_x),
        'lookback_span': span,
        'horizon': horizon,
    }
