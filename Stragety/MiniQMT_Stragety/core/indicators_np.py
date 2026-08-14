# -*- coding: utf-8 -*-
"""
core/indicators_np.py — 向量化技术指标
=======================================
与原版 core/indicators.py 的差异:

  atr()    — TR 向量化 + RMA (Wilder's smoothing), 业界标准
  atr_sma()— 向量化 SMA, 与原版行为完全一致
  rsi()    — Cutler's RSI (np.convolve 纯向量化), 无 Python 循环

算法对比:
  atr  原版(SMA):  atr[i] = mean(tr[i-period+1 : i+1])
  atr  新版(RMA):  atr[i] = (atr[i-1]*(period-1) + tr[i]) / period

  rsi  原版(Wilder): avg_gain = (avg_gain*13 + gain) / 14  (RMA递推)
  rsi  新版(Cutler): avg_gain = SMA(gain, period)           (纯向量化)

用法:
  from core.indicators_np import atr, atr_sma, rsi

接口: 所有函数 list in, list out, 与原版完全兼容
"""

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# ATR — 核心优化
# ═══════════════════════════════════════════════════════════════════════════════

def atr(highs, lows, closes, period=14):
    """
    平均真实波幅 — Wilder's RMA 平滑 (业界标准)

    算法:
      1. TR[i] = max(H-L, |H-C[i-1]|, |L-C[i-1]|)   ← 向量化
      2. atr[period] = mean(tr[1:period+1])           ← 首值 SMA
      3. atr[i] = (atr[i-1]*(period-1) + tr[i])/period ← 递推

    Args:
        highs, lows, closes: list[float], 日线序列 (最近一条在末尾)
        period: ATR 周期, 默认 14

    Returns:
        list[float], 长度与输入一致, 前 period 位为 0.0

    Examples:
        假设 601869 某段日线:
        >>> h = [340, 345, 350, 355, 348]
        >>> l = [330, 335, 338, 340, 335]
        >>> c = [335, 342, 345, 348, 340]
        >>> atr(h, l, c, period=3)
        # 前3位=0, 后续为RMA递推值
    """
    h = np.asarray(highs, dtype=np.float64)
    l = np.asarray(lows, dtype=np.float64)
    c = np.asarray(closes, dtype=np.float64)
    n = len(c)

    if n < period + 1:
        return [0.0] * n

    # ── 1. 向量化 True Range ──
    # TR[i] = max(H[i]-L[i], |H[i]-C[i-1]|, |L[i]-C[i-1]|)  for i >= 1
    tr = np.zeros(n)
    tr[1:] = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1]))
    )

    # ── 2. 首值: SMA of tr[1:period+1] ──
    result = np.zeros(n)
    result[period] = np.mean(tr[1:period + 1])

    # ── 3. RMA 递推 ──
    # atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    #        = atr[i-1] * coef + tr[i] * inv_period
    coef = (period - 1) / period
    inv = 1.0 / period
    for i in range(period + 1, n):
        result[i] = result[i - 1] * coef + tr[i] * inv

    return result.tolist()


def atr_sma(highs, lows, closes, period=14):
    """
    ATR — 简单移动平均版 (与原版 indicators.atr 行为一致)

    用于需要保持与原策略信号完全一致的场景。
    """
    h = np.asarray(highs, dtype=np.float64)
    l = np.asarray(lows, dtype=np.float64)
    c = np.asarray(closes, dtype=np.float64)
    n = len(c)

    if n < period + 1:
        return [0.0] * n

    # 向量化 TR (同上)
    tr = np.zeros(n)
    tr[1:] = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1]))
    )

    # SMA: np.convolve 向量化
    result = np.zeros(n)
    kernel = np.ones(period) / period
    result[period:] = np.convolve(tr[1:], kernel, mode='valid')

    return result.tolist()


# ═══════════════════════════════════════════════════════════════════════════════
# RSI — Cutler's SMA 纯向量化
# ═══════════════════════════════════════════════════════════════════════════════

def rsi(closes, period=14):
    """
    相对强弱指标 — Cutler's RSI (np.convolve 纯向量化, 无 Python 循环)

    算法:
      1. diff[i] = closes[i] - closes[i-1]                     ← 向量化差分
      2. gain[i] = max(diff[i], 0),  loss[i] = max(-diff[i], 0)
      3. avg_gain = SMA(gain, period),  avg_loss = SMA(loss, period)  ← np.convolve
      4. RSI = 100 - 100 / (1 + avg_gain / avg_loss)

    与原版 Wilder's RSI 的区别:
      原版: avg_gain 用 RMA 递推 (指数平滑), 近值权重高
      新版: avg_gain 用 SMA 卷积 (等权滑动窗口), 无递归依赖, 可完全向量化

      两者数值接近但不等价。Cutler's RSI 是 TradingView 等平台的默认实现。

    Args:
        closes: list[float], 收盘价序列 (最近一条在末尾)
        period: RSI 周期, 默认 14

    Returns:
        list[float], 长度与输入一致, 前 period 位为 50.0

    Examples:
        >>> rsi([100, 102, 101, 103, 105, 104, 106], period=3)
        [50.0, 50.0, 50.0, ..., ...]
    """
    c = np.asarray(closes, dtype=np.float64)
    n = len(c)

    if n < period + 1:
        return [50.0] * n

    # ── 1. 向量化涨跌 (长度 n-1, 无 prepend, 每个窗口恰好 period 个真实值) ──
    diff = np.diff(c)
    gain = np.maximum(diff, 0.0)
    loss = np.maximum(-diff, 0.0)

    # ── 2. np.convolve 滑动窗口求和 (长度 n-period) ──
    kernel = np.ones(period, dtype=np.float64)
    sum_gain = np.convolve(gain, kernel, mode='valid')
    sum_loss = np.convolve(loss, kernel, mode='valid')

    avg_gain = sum_gain / period
    avg_loss = sum_loss / period

    # ── 3. 向量化 RSI ──
    with np.errstate(divide='ignore', invalid='ignore'):
        rs = avg_gain / avg_loss
        rsi_valid = 100.0 - 100.0 / (1.0 + rs)

    # avg_loss=0 & avg_gain>0 → rs=inf → RSI=100 (公式自动处理, 结果正确)
    # avg_loss=0 & avg_gain=0 → rs=nan → RSI→nan, 需手动修复为 50
    rsi_valid[(avg_loss == 0) & (avg_gain == 0)] = 50.0

    # ── 4. 填充结果 ──
    result = np.full(n, 50.0)
    result[period:] = rsi_valid
    return result.tolist()


# ═══════════════════════════════════════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import time

    # 模拟 2500 条日线 (约 10 年)
    np.random.seed(42)
    n = 2500
    base = 300.0 + np.cumsum(np.random.randn(n) * 5)
    h = base + np.abs(np.random.randn(n) * 8)
    l = base - np.abs(np.random.randn(n) * 8)
    c = base + np.random.randn(n) * 3
    h_list, l_list, c_list = h.tolist(), l.tolist(), c.tolist()

    from indicators import atr as atr_old, rsi as rsi_old

    print(f'Data: {n} rows (~10 years daily)')
    print(f'{"="*55}')

    # ── ATR ──
    t0 = time.perf_counter(); old_a = atr_old(h_list, l_list, c_list, 14)
    t_atr_old = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter(); rma = atr(h_list, l_list, c_list, 14)
    t_atr_rma = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter(); sma = atr_sma(h_list, l_list, c_list, 14)
    t_atr_sma = (time.perf_counter() - t0) * 1000

    print(f'[ATR]')
    print(f'  old SMA (pure Python): {t_atr_old:.3f}ms')
    print(f'  new SMA (np.convolve): {t_atr_sma:.3f}ms')
    print(f'  new RMA (Wilder):      {t_atr_rma:.3f}ms')
    print(f'  SMA consistency:       {np.allclose(old_a, sma, atol=1e-6)}')
    print(f'  RMA vs SMA last:       RMA={rma[-1]:.4f}  SMA={old_a[-1]:.4f}  '
          f'delta={abs(rma[-1]-old_a[-1]):.4f}')

    # ── RSI ──
    t0 = time.perf_counter(); old_r = rsi_old(c_list, 14)
    t_rsi_old = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter(); new_r = rsi(c_list, 14)
    t_rsi_new = (time.perf_counter() - t0) * 1000

    # 计算相关性 (Wilder vs Cutler 数值不等价, 但走势应高度相关)
    valid = slice(14, None)
    corr = np.corrcoef(old_r[valid], new_r[valid])[0, 1]
    mae = np.mean(np.abs(np.array(old_r[valid]) - np.array(new_r[valid])))

    print(f'\n[RSI]')
    print(f'  old Wilder (pure Python): {t_rsi_old:.3f}ms')
    print(f'  new Cutler (np.convolve): {t_rsi_new:.3f}ms  '
          f'({t_rsi_old/max(t_rsi_new,0.001):.1f}x)')
    print(f'  correlation:              {corr:.4f}')
    print(f'  MAE (valid range):        {mae:.4f}')
    print(f'  last value:               old={old_r[-1]:.2f}  new={new_r[-1]:.2f}')

    # ── 汇总 ──
    total_old = t_atr_old + t_rsi_old
    total_new = t_atr_rma + t_rsi_new
    print(f'\n[Total] old={total_old:.2f}ms  new={total_new:.2f}ms  '
          f'({total_old/max(total_new,0.001):.1f}x)')
