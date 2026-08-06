# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 迷你反T v11 策略回测 — 基于 xtdata 本地数据
================================================================================

 使用 QMT 本地 .DAT 数据 (通过 xtdata.get_local_data()) 进行精确回测。

 数据源:
   - 日线: xtdata.get_local_data('1d') -> handlebar 信号计算
   - 1分钟线: xtdata.get_local_data('1m') -> ontimer 状态机模拟

 运行前提:
   1. MiniQMT 已启动 (极简模式, 端口 58610)
   2. QMT 已下载 601869 的历史日线和1分钟线数据

 用法:
   python backtest/backtest_v10_xtdata.py

   可选参数:
   python backtest/backtest_v10_xtdata.py --start 20240101 --end 20260806
   python backtest/backtest_v10_xtdata.py --no-download  (使用已有缓存)
================================================================================
"""
import sys
# 强制 UTF-8 输出, 避免 Windows GBK 编码问题
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import os
import time
import argparse
import traceback
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ============================================================================
# 策略参数 (与 QMT_迷你反T_v10 完全一致)
# ============================================================================

STOCK_CODE = '601869'
STOCK_QMT  = '601869.SH'
TRADE_LOT_SIZE = 100

ATR_PERIOD = 14

SELL_TRIGGER_BASE_BEAR      = 0.40
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55
SELL_TRIGGER_BASE_WEAK_BULL = 0.70

DYNAMIC_MULT_MIN = 0.20
DYNAMIC_MULT_MAX = 1.50

DAILY_RANGE_CAP_ENABLED = True
DAILY_RANGE_CAP_MULT    = 0.80

PULLBACK_PCT = 0.0010
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT = 0.0010
BUYBACK_TIGHTEN_MULT = 0.60

VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI     = 70
STRONG_BULL_STREAK  = 5

EMERGENCY_BUYBACK_PCT = 0.03
STOP_LOSS_MIN_PCT     = 0.015
STOP_LOSS_ATR_MULT    = 0.25

FORCE_CLOSE_TIME = '14:57:00'
HIST_DATA_LEN    = 80
COMMISSION       = 0.00025
STAMP_TAX        = 0.001

# 状态机常量
STATE_IDLE    = 'IDLE'
STATE_SPIKING = 'SPIKING'
STATE_SOLD    = 'SOLD'
STATE_DIPPING = 'DIPPING'
STATE_DONE    = 'DONE'
STATE_FORCED  = 'FORCED'


# ============================================================================
# 技术指标 (与策略文件完全一致)
# ============================================================================

def _sma(values, period):
    n = len(values)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _atr(highs, lows, closes, period=14):
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


def _rsi(closes, period=14):
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    rsi = [50.0] * n
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    return rsi


def _up_streak(closes):
    n = len(closes)
    streak = [0] * n
    for i in range(1, n):
        streak[i] = streak[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return streak


def _daily_range_ma(highs, lows, opens, period=10):
    n = len(opens)
    ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0:
            ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)


# ============================================================================
# 动态乘数模型 (与策略文件完全一致)
# ============================================================================

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    if trend == 'bear':
        base = SELL_TRIGGER_BASE_BEAR
    elif trend == 'weak_bull':
        base = SELL_TRIGGER_BASE_WEAK_BULL
    else:
        base = SELL_TRIGGER_BASE_SIDEWAYS

    deviations = {}
    total_deviation = 0.0

    # 因子1: 趋势
    if trend == 'bear':
        d = -0.25 if up_streak == 0 else -0.15
    elif trend == 'strong_bull':
        d = +999
    elif trend == 'weak_bull':
        if up_streak >= 3:       d = +0.20
        elif up_streak >= 1:     d = +0.12
        else:                    d = +0.05
    else:
        d = 0.00
    deviations['趋势'] = d
    total_deviation += d

    # 因子2: 波动率
    if atr_pct > 0.08:        atr_d = -0.30
    elif atr_pct > 0.07:      atr_d = -0.22
    elif atr_pct > 0.06:      atr_d = -0.15
    elif atr_pct > 0.05:      atr_d = -0.08
    elif atr_pct > 0.03:      atr_d = +0.05
    elif atr_pct > 0.02:      atr_d = +0.15
    else:                     atr_d = +0.25

    if atr_ratio > 1.50:        atrd_d = -0.25
    elif atr_ratio > 1.25:      atrd_d = -0.18
    elif atr_ratio > 1.10:      atrd_d = -0.10
    elif atr_ratio > 0.90:      atrd_d = 0.00
    elif atr_ratio > 0.70:      atrd_d = +0.12
    elif atr_ratio > 0.50:      atrd_d = +0.20
    else:                       atrd_d = +0.25

    vol_d = atr_d * 0.55 + atrd_d * 0.45
    vol_d = max(-0.35, min(0.30, vol_d))
    deviations['波动率'] = round(vol_d, 2)
    total_deviation += vol_d

    # 因子3: 成交量
    if vol_ratio > 2.00:        d = -0.25
    elif vol_ratio > 1.50:      d = -0.18
    elif vol_ratio > 1.20:      d = -0.08
    elif vol_ratio > 0.80:      d = 0.00
    elif vol_ratio > 0.60:      d = +0.12
    elif vol_ratio > 0.40:      d = +0.20
    else:                       d = +0.25
    deviations['成交量'] = d
    total_deviation += d

    # 因子4: RSI
    if rsi_val > 80:          d = -0.25
    elif rsi_val > 70:        d = -0.18
    elif rsi_val > 60:        d = -0.08
    elif rsi_val > 55:        d = -0.03
    elif rsi_val > 45:        d = 0.00
    elif rsi_val > 40:        d = +0.03
    elif rsi_val > 30:        d = +0.10
    elif rsi_val > 20:        d = +0.20
    else:                     d = +0.25
    deviations['RSI'] = d
    total_deviation += d

    final = base + total_deviation
    final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final))
    return round(final, 2), deviations, base


# ============================================================================
# 信号计算 (与策略文件完全一致)
# ============================================================================

def compute_signal(opens, highs, lows, closes, volumes):
    """计算当日反T信号"""
    n = len(closes)
    if n < 60:
        return None

    co = opens[-1]
    cc = closes[-1]
    cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]
    curr_rsi = _rsi(closes)[-1]
    up_streak = _up_streak(closes)[-1]

    price_above_ma = (cc > ma20) and (ma5 > ma20)
    price_below_ma = (cc < ma20) and (ma5 < ma20)

    if price_above_ma and curr_rsi > STRONG_BULL_RSI and up_streak >= STRONG_BULL_STREAK:
        trend = 'strong_bull'
    elif price_above_ma:
        trend = 'weak_bull'
    elif price_below_ma:
        trend = 'bear'
    else:
        trend = 'sideways'

    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    sell_mult, factor_details, base_used = calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak
    )

    daily_range_ma10 = _daily_range_ma(highs, lows, opens, 10)[-1]
    max_trigger_by_range = co * (1.0 + daily_range_ma10 * DAILY_RANGE_CAP_MULT)
    range_capped = False

    sell_trigger_raw = co + curr_atr * sell_mult

    if DAILY_RANGE_CAP_ENABLED and sell_trigger_raw > max_trigger_by_range:
        sell_trigger = round(max_trigger_by_range, 2)
        range_capped = True
    else:
        sell_trigger = round(sell_trigger_raw, 2)

    do_short = True
    reason = ''

    if trend == 'strong_bull':
        do_short = False
        reason = '强牛禁反T'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short = False
        reason = f'缩量(量比{curr_vr:.2f})'
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short = False
        reason = f'RSI超买({curr_rsi:.0f})'

    return {
        'do_short': do_short, 'blocked_reason': reason, 'trend': trend,
        'sell_trigger': sell_trigger, 'sell_trigger_raw': round(sell_trigger_raw, 2),
        'range_capped': range_capped, 'open_price': co, 'close_yday': cc,
        'atr': curr_atr, 'atr_pct': curr_atr_pct, 'rsi': curr_rsi,
        'vol_ratio': curr_vr, 'sell_mult': sell_mult, 'sell_mult_base': base_used,
        'factor_details': factor_details, 'atr_ratio': atr_ratio,
        'up_streak': up_streak, 'buyback_mult': BUYBACK_TRIGGER_MULT,
    }


# ============================================================================
# 数据管理
# ============================================================================

class XTDataManager:
    """通过 xtdata 获取 QMT 本地数据 (支持 data_dir 指定完整QMT数据目录)"""

    def __init__(self, stock_code='601869.SH', data_dir=None):
        self.stock = stock_code
        self._daily_df = None
        self._minute_cache = {}   # {date_str: DataFrame}
        # 数据目录: 默认使用 MiniQMT 目录, 可指定 C:/QMT/datadir 获取更长历史
        self.data_dir = data_dir

    def load_daily(self, start='20200101', end='20260806'):
        """加载日线数据 (支持 data_dir 指定完整QMT数据目录)"""
        from xtquant import xtdata
        data_dir_hint = f' (data_dir={self.data_dir})' if self.data_dir else ''
        print(f'[数据] 加载日线 {start}~{end}{data_dir_hint} ...')
        kwargs = dict(
            field_list=['open', 'high', 'low', 'close', 'volume', 'amount'],
            stock_list=[self.stock],
            period='1d',
            start_time=start,
            end_time=end,
            dividend_type='front',
        )
        if self.data_dir:
            kwargs['data_dir'] = self.data_dir
        data = xtdata.get_local_data(**kwargs)
        if self.stock in data:
            self._daily_df = data[self.stock]
            print(f'[数据] 日线: {len(self._daily_df)} 条, {self._daily_df.index[0]} ~ {self._daily_df.index[-1]}')
        else:
            raise RuntimeError(f'无法获取 {self.stock} 日线数据')

    def get_daily_slice(self, end_idx, n_bars=80):
        """获取 end_idx 位置之前 n_bars 根日线"""
        start_idx = max(0, end_idx + 1 - n_bars)
        return self._daily_df.iloc[start_idx:end_idx + 1]

    def load_minutes_for_day(self, date_str):
        """加载某天的1分钟线 (带缓存)"""
        if date_str in self._minute_cache:
            return self._minute_cache[date_str]

        from xtquant import xtdata

        kwargs = dict(
            field_list=['open', 'high', 'low', 'close', 'volume'],
            stock_list=[self.stock],
            period='1m',
            start_time=date_str,
            end_time=date_str,
        )
        if self.data_dir:
            kwargs['data_dir'] = self.data_dir
        data = xtdata.get_local_data(**kwargs)
        if self.stock in data and len(data[self.stock]) > 0:
            df = data[self.stock]
            self._minute_cache[date_str] = df
            return df
        return None

    def get_trading_days(self):
        """返回所有交易日日期列表 (str, 'YYYYMMDD' 格式)"""
        return sorted(self._daily_df.index.tolist())

    def get_daily_bar(self, date):
        """获取某天的日线数据"""
        if date in self._daily_df.index:
            return self._daily_df.loc[date]
        return None


# ============================================================================
# 回测引擎
# ============================================================================

class BacktestResult:
    """单笔交易结果"""
    def __init__(self, date, sell_price, buyback_price, reason, gross_profit,
                 net_profit, trend, atr_pct, sell_mult, sell_trigger):
        self.date = date
        self.sell_price = sell_price
        self.buyback_price = buyback_price
        self.reason = reason
        self.gross_profit = gross_profit
        self.net_profit = net_profit
        self.trend = trend
        self.atr_pct = atr_pct
        self.sell_mult = sell_mult
        self.sell_trigger = sell_trigger

    def to_dict(self):
        return {
            'date': self.date, 'sell_price': self.sell_price,
            'buyback_price': self.buyback_price, 'reason': self.reason,
            'gross_profit': round(self.gross_profit, 2),
            'net_profit': round(self.net_profit, 2),
            'trend': self.trend, 'atr_pct': round(self.atr_pct, 4),
            'sell_mult': self.sell_mult, 'sell_trigger': self.sell_trigger,
        }


class BacktestEngine:
    """迷你反T策略回测引擎"""

    def __init__(self, data_mgr: XTDataManager):
        self.data = data_mgr
        self.results: list[BacktestResult] = []
        self.no_trade_days = []      # 未触发交易的日期及原因
        self.daily_pnl = []           # 每日PnL序列

    def run(self, start_date=None, end_date=None, verbose=True):
        """
        运行回测

        对每个交易日:
          1. 计算当日信号 (基于前80根日线)
          2. 如果 do_short=True, 加载1分钟线模拟日内状态机
          3. 记录交易结果
        """
        trading_days = self.data.get_trading_days()

        # 过滤日期范围
        if start_date:
            start_dt = pd.Timestamp(start_date)
            trading_days = [d for d in trading_days if pd.Timestamp(d) >= start_dt]
        if end_date:
            end_dt = pd.Timestamp(end_date)
            trading_days = [d for d in trading_days if pd.Timestamp(d) <= end_dt]

        print(f'[回测] {len(trading_days)} 个交易日, {trading_days[0]} ~ {trading_days[-1]}')

        total = len(trading_days)
        last_print_pct = 0

        for day_idx, day in enumerate(trading_days):
            # 进度
            pct = int((day_idx + 1) / total * 100)
            if pct >= last_print_pct + 10:
                day_display = f'{day[:4]}-{day[4:6]}-{day[6:8]}' if len(day) == 8 else str(day)
                print(f'[回测] 进度 {pct}% ({day_idx + 1}/{total}) {day_display}'
                      f' | 已成交 {len(self.results)} 笔')
                last_print_pct = pct

            day_str = day  # 已经是 'YYYYMMDD' 格式

            # ── 1. 计算当日信号 ──
            # day_idx是过滤后列表的索引, 需要找到在DataFrame中的位置
            df_idx = self.data._daily_df.index.get_loc(day_str)
            daily_slice = self.data.get_daily_slice(df_idx, HIST_DATA_LEN)
            if len(daily_slice) < 60:
                continue

            opens   = daily_slice['open'].values.tolist()
            highs   = daily_slice['high'].values.tolist()
            lows    = daily_slice['low'].values.tolist()
            closes  = daily_slice['close'].values.tolist()
            volumes = daily_slice['volume'].values.tolist()

            signal = compute_signal(opens, highs, lows, closes, volumes)
            if signal is None:
                continue

            if not signal['do_short']:
                self.no_trade_days.append({
                    'date': day, 'reason': signal['blocked_reason']
                })
                self.daily_pnl.append({'date': day, 'pnl': 0, 'reason': signal['blocked_reason']})
                continue

            # ── 2. 加载日内1分钟线 ──
            minute_bars = self.data.load_minutes_for_day(day_str)
            if minute_bars is None or len(minute_bars) == 0:
                # 尝试下载
                if verbose:
                    print(f'  [下载] {day_str} 1分钟线...')
                try:
                    from xtquant import xtdata
                    xtdata.download_history_data('601869.SH', '1m', day_str, day_str)
                    time.sleep(0.3)
                    minute_bars = self.data.load_minutes_for_day(day_str)
                except Exception:
                    pass

            if minute_bars is None or len(minute_bars) == 0:
                self.no_trade_days.append({
                    'date': day, 'reason': '无1分钟线数据'
                })
                self.daily_pnl.append({'date': day, 'pnl': 0, 'reason': '无分钟数据'})
                continue

            # ── 3. 日内状态机模拟 ──
            result = self._simulate_day(signal, minute_bars, day, verbose)
            if result:
                self.results.append(result)
                self.daily_pnl.append({
                    'date': day, 'pnl': result.net_profit,
                    'reason': result.reason
                })
            else:
                self.daily_pnl.append({
                    'date': day, 'pnl': 0, 'reason': '未触发'
                })

        print(f'[回测] 完成! {len(self.results)} 笔交易, {len(self.no_trade_days)} 天未交易')

    def _simulate_day(self, signal, minute_bars, date, verbose):
        """
        用1分钟线模拟日内状态机

        对每根1分钟K线, 按 OHLC 顺序模拟价格路径:
          Open -> High -> Low -> Close
        检查各状态转换条件.
        """
        sell_trigger = signal['sell_trigger']
        atr_pct = signal['atr_pct']
        open_price = signal['open_price']

        # 状态变量
        fstate = STATE_IDLE
        peak_price = 0.0
        dip_price = 0.0
        sell_fill_price = 0.0
        buyback_target = 0.0
        sell_elapsed = 0
        stop_loss_hit = False

        # 日线约束: 如果当日最高价都不到触发线, 直接跳过
        day_high = minute_bars['high'].max()
        if day_high < sell_trigger:
            return None

        for idx, (bar_time, bar) in enumerate(minute_bars.iterrows()):
            o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']

            # 跳过无效bar
            if h <= 0 or l <= 0:
                continue

            # ─ ─ ─ 模拟: Open -> High -> Low -> Close ─ ─ ─

            if fstate == STATE_IDLE:
                # 价格从开盘走到最高
                if h >= sell_trigger:
                    fstate = STATE_SPIKING
                    peak_price = h
                    continue

            if fstate == STATE_SPIKING:
                # 更新最高价 (价格从open->high)
                if h > peak_price:
                    peak_price = h

                # 从最高点回落 (high->low)
                if peak_price > 0:
                    pullback = (peak_price - l) / peak_price
                    if pullback >= PULLBACK_PCT:
                        # 卖出确认
                        sell_fill_price = round(peak_price * (1.0 - PULLBACK_PCT), 2)
                        fstate = STATE_SOLD
                        buyback_pct = atr_pct * BUYBACK_TRIGGER_MULT
                        buyback_target = round(sell_fill_price * (1.0 - buyback_pct), 2)
                        sell_elapsed = 0
                        continue

                # 假突破: low跌回触发线以下
                if l < sell_trigger:
                    fstate = STATE_IDLE
                    peak_price = 0.0
                    continue

            if fstate == STATE_SOLD:
                sell_elapsed += 1

                # 紧急买回: high >= 卖价 * 1.03
                emergency_line = sell_fill_price * (1.0 + EMERGENCY_BUYBACK_PCT)
                if h >= emergency_line:
                    buyback_price = round(emergency_line, 2)
                    return self._record_trade(
                        date, sell_fill_price, buyback_price, '紧急',
                        signal, minute_bars
                    )

                # 动态收紧
                tightened_bt = buyback_target
                if sell_elapsed > 30 and c > sell_fill_price * 0.995:
                    tightened_bt = sell_fill_price * (
                        1.0 - atr_pct * BUYBACK_TRIGGER_MULT * BUYBACK_TIGHTEN_MULT
                    )
                    tightened_bt = round(max(tightened_bt, buyback_target), 2)

                # 跌到买回触发线
                if l <= tightened_bt:
                    fstate = STATE_DIPPING
                    dip_price = l
                    continue

            if fstate == STATE_DIPPING:
                # 更新最低价
                if l < dip_price:
                    dip_price = l

                # 回升确认 (从low->close)
                if dip_price > 0:
                    bounce = (c - dip_price) / dip_price
                    if bounce >= BOUNCE_PCT:
                        buyback_price = round(dip_price * (1.0 + BOUNCE_PCT), 2)
                        return self._record_trade(
                            date, sell_fill_price, buyback_price, '正常',
                            signal, minute_bars
                        )

                # 假跌破: high涨回买回线之上
                if h > buyback_target:
                    fstate = STATE_SOLD
                    dip_price = 0.0
                    continue

            # ★ v11: ATR自适应止损检查
            #   stop_pct = max(1.5%, ATR% * 0.25)
            #   高ATR时放宽止损避免噪声触发, 低ATR时保持1.5%底线
            if fstate in (STATE_SOLD, STATE_DIPPING) and not stop_loss_hit:
                stop_pct = max(STOP_LOSS_MIN_PCT, atr_pct * STOP_LOSS_ATR_MULT)
                loss_limit = TRADE_LOT_SIZE * open_price * stop_pct
                estimated_pnl = (sell_fill_price - c) * TRADE_LOT_SIZE
                if estimated_pnl < -loss_limit:
                    stop_loss_hit = True
                    buyback_price = c
                    return self._record_trade(
                        date, sell_fill_price, buyback_price, '止损',
                        signal, minute_bars
                    )

        # ── 日内未完成交易 ──
        if fstate in (STATE_SOLD, STATE_DIPPING):
            # 尾盘强制买回(使用最后一根bar的close)
            last_close = minute_bars['close'].iloc[-1]
            return self._record_trade(
                date, sell_fill_price, last_close, '尾盘',
                signal, minute_bars
            )

        # SPIKING状态未触发卖出 -> 未成交
        if fstate == STATE_SPIKING:
            return None

        return None

    def _record_trade(self, date, sell_price, buyback_price, reason, signal, minute_bars):
        """计算交易盈亏并返回BacktestResult"""
        shares = TRADE_LOT_SIZE

        # 卖出手续费 + 印花税
        sell_amount = sell_price * shares
        sell_commission = sell_amount * COMMISSION
        sell_stamp = sell_amount * STAMP_TAX
        sell_net = sell_amount - sell_commission - sell_stamp

        # 买入手续费
        buy_amount = buyback_price * shares
        buy_commission = buy_amount * COMMISSION
        buy_net = buy_amount + buy_commission

        # 价差毛利
        gross_profit = (sell_price - buyback_price) * shares
        # 净利
        net_profit = sell_net - buy_net

        return BacktestResult(
            date=date if len(date) == 8 else str(date),
            sell_price=sell_price,
            buyback_price=buyback_price,
            reason=reason,
            gross_profit=gross_profit,
            net_profit=net_profit,
            trend=signal['trend'],
            atr_pct=signal['atr_pct'],
            sell_mult=signal['sell_mult'],
            sell_trigger=signal['sell_trigger'],
        )

    def print_report(self):
        """打印回测报告"""
        trades = self.results
        if not trades:
            print('\n[报告] 无交易记录')
            return

        # 基本统计
        n_trades = len(trades)
        gross_profits = [t.gross_profit for t in trades]
        net_profits = [t.net_profit for t in trades]
        wins = [p for p in net_profits if p > 0]
        losses = [p for p in net_profits if p <= 0]

        total_gross = sum(gross_profits)
        total_net = sum(net_profits)
        avg_net = total_net / n_trades if n_trades > 0 else 0
        win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        # 最大回撤 (基于累计净利)
        cumulative = np.cumsum(net_profits)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        max_dd = np.max(drawdowns) if len(drawdowns) > 0 else 0

        # 按原因统计
        by_reason = defaultdict(lambda: {'count': 0, 'net_profit': 0.0, 'gross_profit': 0.0})
        for t in trades:
            by_reason[t.reason]['count'] += 1
            by_reason[t.reason]['net_profit'] += t.net_profit
            by_reason[t.reason]['gross_profit'] += t.gross_profit

        # 按趋势统计
        by_trend = defaultdict(lambda: {'count': 0, 'net_profit': 0.0, 'gross_profit': 0.0})
        for t in trades:
            by_trend[t.trend]['count'] += 1
            by_trend[t.trend]['net_profit'] += t.net_profit
            by_trend[t.trend]['gross_profit'] += t.gross_profit

        # 年度统计
        by_year = defaultdict(lambda: {'count': 0, 'net_profit': 0.0, 'gross_profit': 0.0})
        for t in trades:
            year = t.date[:4]
            by_year[year]['count'] += 1
            by_year[year]['net_profit'] += t.net_profit
            by_year[year]['gross_profit'] += t.gross_profit

        # ── 打印 (使用全角￥避免GBK编码问题) ──
        RMB = '￥'  # 全角人民币符号, GBK兼容
        print()
        print('=' * 70)
        print('  QMT 迷你反T v11 策略回测报告')
        print('=' * 70)
        print(f'  标的: {STOCK_CODE} | 手数: {TRADE_LOT_SIZE}股/笔')
        print(f'  回测区间: {trades[0].date} ~ {trades[-1].date}')
        print(f'  交易天数: {len(self.daily_pnl)} | 成交: {n_trades} 笔')
        print(f'  未交易天数: {len(self.no_trade_days)}')
        print()
        print(f'  {"─" * 50}')
        print(f'  {"核心指标":^50}')
        print(f'  {"─" * 50}')
        print(f'  总毛利:     ¥{total_gross:>12,.2f}')
        print(f'  总净利:     ¥{total_net:>12,.2f}')
        print(f'  平均净利:   ¥{avg_net:>12,.2f} /笔')
        print(f'  胜率:       {win_rate:>11.1f}% ({len(wins)}/{n_trades})')
        print(f'  平均盈利:   ¥{avg_win:>12,.2f} /笔')
        print(f'  平均亏损:   ¥{avg_loss:>12,.2f} /笔')
        if wins and losses:
            profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else float('inf')
            print(f'  盈亏比:     {profit_factor:>12.2f}')
        print(f'  最大回撤:   ¥{max_dd:>12,.2f}')
        print()
        print(f'  {"─" * 50}')
        print(f'  {"按交易类型":^50}')
        print(f'  {"─" * 50}')
        print(f'  {"类型":<10} {"笔数":<8} {"毛利":<14} {"净利":<14}')
        for reason, stats in sorted(by_reason.items()):
            print(f'  {reason:<10} {stats["count"]:<8} ¥{stats["gross_profit"]:>12,.2f} ¥{stats["net_profit"]:>12,.2f}')

        print()
        print(f'  {"─" * 50}')
        print(f'  {"按市场趋势":^50}')
        print(f'  {"─" * 50}')
        print(f'  {"趋势":<12} {"笔数":<8} {"毛利":<14} {"净利":<14}')
        for trend, stats in sorted(by_trend.items()):
            print(f'  {trend:<12} {stats["count"]:<8} ¥{stats["gross_profit"]:>12,.2f} ¥{stats["net_profit"]:>12,.2f}')

        print()
        print(f'  {"─" * 50}')
        print(f'  {"按年度":^50}')
        print(f'  {"─" * 50}')
        print(f'  {"年份":<8} {"笔数":<8} {"毛利":<14} {"净利":<14}')
        for year, stats in sorted(by_year.items()):
            print(f'  {year:<8} {stats["count"]:<8} ¥{stats["gross_profit"]:>12,.2f} ¥{stats["net_profit"]:>12,.2f}')

        # 熔断统计
        blocked = defaultdict(int)
        for d in self.no_trade_days:
            blocked[d['reason']] += 1
        if blocked:
            print()
            print(f'  {"─" * 50}')
            print(f'  {"熔断原因统计":^50}')
            print(f'  {"─" * 50}')
            for reason, count in sorted(blocked.items(), key=lambda x: -x[1]):
                print(f'  {reason:<30} {count:>4} 天')

        print()
        print('=' * 70)

    def save_csv(self, output_dir=None):
        """保存交易记录CSV"""
        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(__file__), 'output')
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # 交易记录
        trades_file = os.path.join(output_dir, f'v11_backtest_trades_{timestamp}.csv')
        trades_df = pd.DataFrame([t.to_dict() for t in self.results])
        trades_df.to_csv(trades_file, index=False, encoding='utf-8-sig')
        print(f'[保存] 交易记录: {trades_file}')

        # 每日PnL
        pnl_file = os.path.join(output_dir, f'v11_backtest_daily_{timestamp}.csv')
        pnl_df = pd.DataFrame(self.daily_pnl)
        pnl_df.to_csv(pnl_file, index=False, encoding='utf-8-sig')
        print(f'[保存] 每日PnL: {pnl_file}')


# ============================================================================
# 主入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='QMT 迷你反T v11 策略回测 (xtdata 本地数据)',
    )
    parser.add_argument('--start', default='20240101',
                        help='回测开始日期 YYYYMMDD (默认: 20240101)')
    parser.add_argument('--end', default='20260806',
                        help='回测结束日期 YYYYMMDD (默认: 20260806)')
    parser.add_argument('--download', action='store_true', default=True,
                        help='自动下载缺失的1分钟线 (默认: True)')
    parser.add_argument('--no-download', action='store_false', dest='download',
                        help='不下载数据, 仅使用缓存')
    parser.add_argument('--data-dir', default='C:/QMT/datadir',
                        help='QMT数据目录 (默认: C:/QMT/datadir, 可获取更长历史)')
    parser.add_argument('--verbose', action='store_true',
                        help='打印每笔交易详情')
    parser.add_argument('--save', action='store_true', default=True,
                        help='保存CSV结果 (默认: True)')
    args = parser.parse_args()

    print('=' * 70)
    print('  QMT 迷你反T v11 策略回测')
    print(f'  数据源: xtdata.get_local_data() -> {args.data_dir}')
    print(f'  回测区间: {args.start} ~ {args.end}')
    print('=' * 70)
    print()

    # ── 1. 加载数据 ──
    t0 = time.time()
    data_mgr = XTDataManager('601869.SH', data_dir=args.data_dir)
    data_mgr.load_daily(start=args.start, end=args.end)

    # ── 2. 预下载1分钟线 (如果需要) ──
    if args.download:
        trading_days = data_mgr.get_trading_days()
        # 过滤日期范围
        start_dt = pd.Timestamp(args.start)
        end_dt = pd.Timestamp(args.end)
        trading_days = [d for d in trading_days if start_dt <= pd.Timestamp(d) <= end_dt]

        # 先尝试直接读取, 缺的才下载
        missing_days = []
        print(f'[数据] 检查1分钟线覆盖 ({len(trading_days)} 天)...')
        for i, day in enumerate(trading_days):
            df = data_mgr.load_minutes_for_day(day)
            if df is None or len(df) == 0:
                missing_days.append(day)

        if missing_days:
            print(f'[数据] 缺失 {len(missing_days)} 天, 开始下载...')
            from xtquant import xtdata

            # 按月批量下载
            months = sorted(set(d[:6] for d in missing_days))
            for mi, month in enumerate(months):
                month_days = [d for d in missing_days if d.startswith(month)]
                first_day = month_days[0]
                last_day = month_days[-1]
                print(f'  [{mi+1}/{len(months)}] 下载 {month} ({first_day}~{last_day}) ...')
                try:
                    xtdata.download_history_data('601869.SH', '1m', first_day, last_day)
                    time.sleep(0.5)
                except Exception as e:
                    print(f'    下载失败: {e}')
                # 重新加载到缓存
                for d in month_days:
                    data_mgr.load_minutes_for_day(d)
        else:
            print('[数据] 1分钟线全覆盖')

    # ── 3. 运行回测 ──
    engine = BacktestEngine(data_mgr)
    engine.run(start_date=args.start, end_date=args.end, verbose=args.verbose)

    elapsed = time.time() - t0
    print(f'\n[耗时] {elapsed:.1f} 秒')

    # ── 4. 打印报告 ──
    engine.print_report()

    # ── 5. 保存结果 ──
    if args.save:
        engine.save_csv()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'\n回测异常: {e}')
        traceback.print_exc()
        sys.exit(1)
