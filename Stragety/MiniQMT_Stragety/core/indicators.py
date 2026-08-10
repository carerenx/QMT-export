# -*- coding: utf-8 -*-
"""
core/indicators.py — 技术指标计算
==================================
纯函数, 输入 numpy 数组或 list, 返回 list。
与 QMT 内置策略中的计算逻辑完全一致。
"""


def sma(values, period):
    """简单移动平均"""
    n = len(values)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def atr(highs, lows, closes, period=14):
    """平均真实波幅 (Average True Range)"""
    n = len(closes)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
    result = [0.0] * n
    for i in range(period, n):
        result[i] = sum(tr[i - period + 1 : i + 1]) / period
    return result


def rsi(closes, period=14):
    """相对强弱指标 (RSI)"""
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    rsi_vals = [50.0] * n
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_vals[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    return rsi_vals


def up_streak(closes):
    """连续上涨天数"""
    n = len(closes)
    streak = [0] * n
    for i in range(1, n):
        streak[i] = streak[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return streak


def daily_range_ma(highs, lows, opens, period=10):
    """日内振幅移动平均"""
    n = len(opens)
    ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0:
            ranges[i] = (highs[i] - lows[i]) / opens[i]
    return sma(ranges, period)
