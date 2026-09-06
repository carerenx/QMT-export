# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — QMT day trading v13 + position management strategy
================================================================================
 VS Code connect xtquant MiniQMT run v13 strategy。

 [arch]
   VS Code (script)
     ├─ xtdata     → QMT local.DAT history data + realtime data
     ├─ xttrader   → order + holding/counter check
     └─ v13stragety → signal + state machinese (保持与QMT内置版完全一致)

 [pre condition]
   1. MiniQMT running (QMT → 右上角"极简mode")
   2. MiniQMT logged in with account 8890145315
   3. QMT has downloaded 601869 daily chart data
   4. pip install xtquant numpy pandas

 [run mode]

   mode1 — signla mode(only moniter):
     python "Stragety/MiniQMT_Stragety/DayTradeing_v13_stragety_miniqmt.py" --mode signal

   mode2 — real mode (auto order):
     python "Stragety/MiniQMT_Stragety/DayTradeing_v13_stragety_miniqmt.py" --mode live

   mode3 — backtest mode (用QMT本地数据回测, 不需要MiniQMT):
     python "Stragety/MiniQMT_Stragety/DayTradeing_v13_stragety_miniqmt.py" --mode backtest

 [diff with QMT internal]
   QMT internal:  策略在QMT客户端内运行, 使用 ContextInfo/handlebar 注入函数
   MiniQMT: 策略在VS Code运行, 使用 xtdata/xttrader 直接调用
   Strategy:  same

================================================================================
"""
import os
import sys
import time as _time
import argparse
import traceback as _traceback
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

# ============================================================================
# ★ Strategy Config Parameters
# ============================================================================

ACCOUNT = '8890145315'

STOCK_CODE = '601869'
STOCK_NAME = '长飞光纤'
STOCK_QMT  = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE = 100
MIN_LOT        = 100

ATR_PERIOD = 14

SELL_TRIGGER_BASE_BEAR      = 0.40
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55
SELL_TRIGGER_BASE_WEAK_BULL = 0.65

DYNAMIC_MULT_MIN = 0.20
DYNAMIC_MULT_MAX = 1.50

DAILY_RANGE_CAP_ENABLED = True
DAILY_RANGE_CAP_MULT    = 0.80

PULLBACK_PCT = 0.0010
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT   = 0.0010
BUYBACK_TIGHTEN_MULT = 0.60

VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI     = 70
STRONG_BULL_STREAK  = 5

EMERGENCY_BUYBACK_PCT = 0.03
STOP_LOSS_PCT         = 0.015

FORCE_CLOSE_TIME    = '14:57:00'
ENABLE_FORCE_CLOSE  = False            # ★ 尾盘强平开关: True=14:57强平 False=不管
HIST_DATA_LEN       = 80
COMMISSION       = 0.00025
STAMP_TAX        = 0.001

# ============================================================================
# [buy-first T parameters] — buy first than sell 
# ============================================================================
# 正T买入触发: 二选一(取较高者)
#   (1) open price× (1 - BUY_TRIGGER_PCT)    — 底线, 基于开盘
#   (2) current price × (1 - BUY_TRIGGER_TRAIL)  — ★ 动态跟随, 价格涨了触发线也上移
#   效果: 开盘330时触发线=320, 涨到350时触发线=343, 只需回调2%即可触发
BUY_TRIGGER_PCT   = 0.030             # 买入触发底线: 开盘-3%
BUY_TRIGGER_TRAIL = 0.020             # ★ Dynamic follow: current price -2% (价格涨触发线跟涨)
SELLBACK_RISE_PCT = 0.012             # if the price rises 1.2% after buy, then sell
# 正T确认参数: 复用 BOUNCE_PCT(探底回升确认) 和 PULLBACK_PCT(冲高回落确认)

# ============================================================================
# [lock holding parameters] — if the stock is strong, keep holding
# ============================================================================
# if satisfied, lock holding, do not sell until the condition disappears:
#   (1) current price > open price × (1 + LOCK_PRICE_RATIO)    — current price more
#   (2) 近N分钟价格持续上行 (斜率 > LOCK_MOMENTUM)  — 动量向上
#   (3) 现价接近日内最高价 (回撤 < LOCK_DRAWDOWN)   — 无见顶迹象
LOCK_PRICE_RATIO  = 0.015             # 价格超开盘1.5% → 强势
LOCK_MOMENTUM_PCT = 0.005             # 近5分钟涨幅>0.5% → 上行动量
LOCK_DRAWDOWN_PCT = 0.005             # 距日内最高<0.5% → 无回调迹象
LOCK_LOOKBACK_SEC = 300               # 动量计算回看秒数 (5分钟)
LOCK_COOLDOWN_SEC = 120               # 解锁冷却: 条件消失后等2分钟再解锁

# ============================================================================
# [position management parameters] — max/min position, daily trade count
# ============================================================================
MAX_POSITION_LOTS  = 5                  # Max holding
MIN_POSITION_LOTS  = 1                  # Min holding
MAX_DAILY_TRADES   = 3                  # Max daily trading count(signl direction)

# MiniQMT 连接参数
MINIQMT_PATH = 'C:/QMT/userdata_mini'
SESSION_ID   = 0

# 状态机常量
STATE_IDLE    = 'IDLE'
STATE_SPIKING = 'SPIKING'
STATE_SOLD    = 'SOLD'
STATE_DIPPING = 'DIPPING'
STATE_DONE    = 'DONE'
STATE_FORCED  = 'FORCED'

# 正T状态 (新增)
STATE_BT_DIPPING  = 'BT_DIPPING'   # 正T: 跌到位, 跟踪最低点等回升确认买入
STATE_BT_BOUGHT   = 'BT_BOUGHT'    # 正T: 已买入, 等待涨到卖出触发线
STATE_BT_SPIKING  = 'BT_SPIKING'   # 正T: 涨到位, 跟踪最高点等回落确认卖出


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
        reason = '强牛禁反T(连涨>=5+RSI>70)'
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
        'bounce_pct': BOUNCE_PCT,
    }


# ============================================================================
# 工具函数
# ============================================================================

def _now():
    return _time.strftime('%H:%M:%S')


def _ts():
    return _time.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    ts = _ts()
    if args:
        print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now_str=None):
    """判断是否在A股连续竞价时段"""
    if now_str is None:
        now_str = _now()
    return ('09:30:00' <= now_str <= '11:30:00') or ('13:00:00' <= now_str <= '15:00:00')


def _time_to_open(now_str):
    """计算距离开盘的剩余时间, 返回可读字符串"""
    h, m, s = int(now_str[:2]), int(now_str[3:5]), int(now_str[6:8])
    now_secs = h * 3600 + m * 60 + s

    if now_str < '09:30:00':
        target = 9 * 3600 + 30 * 60
    elif '11:30:00' < now_str < '13:00:00':
        target = 13 * 3600
    else:
        return '--'

    remain = target - now_secs
    if remain > 3600:
        return f'{remain // 3600}时{(remain % 3600) // 60}分'
    elif remain > 60:
        return f'{remain // 60}分{remain % 60}秒'
    else:
        return f'{remain}秒'


# ============================================================================
# MiniQMT 连接层
# ============================================================================

class MiniQMTConnector:
    """封装 xtdata + xttrader 连接"""

    def __init__(self):
        self.xtdata = None
        self.xttrader = None
        self.trader = None
        self.callback = None
        self._data_connected = False
        self._trade_connected = False
        self._account_obj = None
        self._daily_data_cache = None   # 缓存的日线DataFrame

        # 交易回调聚合
        self.last_order_status = None
        self.last_trade = None
        self.order_pending = False

    def connect_data(self):
        """连接行情 (xtdata)"""
        from xtquant import xtdata as _xtdata
        self.xtdata = _xtdata
        try:
            _xtdata.connect()
            self._data_connected = True
            print('[连接] MiniQMT 行情服务已连接')
            return True
        except Exception as e:
            print(f'[连接] 行情服务连接失败: {e}')
            print('[提示] 请先启动 MiniQMT (QMT → 右上角"极简mode")')
            return False

    def connect_trade(self, account_id=ACCOUNT, path=MINIQMT_PATH, session=SESSION_ID):
        """连接交易 (xttrader)"""
        from xtquant import xttrader as _xttrader, xtconstant

        class _Callback(_xttrader.XtQuantTraderCallback):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent

            def on_connected(self):
                print('[交易] MiniQMT 交易服务已连接')

            def on_disconnected(self, reason):
                print(f'[交易] 连接断开: {reason}')

            def on_stock_order(self, order):
                sm = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
                status = getattr(order, 'm_nOrderStatus', -1)
                if status in sm:
                    d = '买' if getattr(order, 'm_nDirection', 0) == 1 else '卖'
                    p = getattr(order, 'm_dLimitPrice', 0)
                    vt = getattr(order, 'm_nVolumeTraded', 0)
                    vo = getattr(order, 'm_nVolumeTotalOriginal', 0)
                    _log(f'[委托] {d} Y{p:.2f} {vt}/{vo}股 -> {sm[status]}')
                    if status in (55, 56):  # 废单/已撤 -> 允许重试
                        self.parent.order_pending = False
                self.parent.last_order_status = status

            def on_stock_trade(self, trade):
                d = '买' if getattr(trade, 'm_nDirection', 0) == 1 else '卖'
                p = getattr(trade, 'm_dPrice', 0)
                v = getattr(trade, 'm_nVolume', 0)
                code = getattr(trade, 'm_strInstrumentID', '')
                _log(f'[成交] {d} {code} Y{p:.2f} x {v}股 = Y{p*v:,.0f}')
                self.parent.last_trade = trade
                self.parent.order_pending = False  # 成交了, 清除pending

            def on_stock_position(self, position):
                pass  # 我们主动查询, 不依赖回调

            def on_stock_asset(self, asset):
                pass

            def on_order_error(self, order_error):
                msg = getattr(order_error, 'error_msg', str(order_error))
                _log(f'[下单错误] {msg}')
                self.parent.order_pending = False

            def on_cancel_error(self, cancel_error):
                _log(f'[撤单错误] {cancel_error}')

            def on_account_status(self, account_status):
                _log(f'[账号状态] {account_status}')

        self.callback = _Callback(self)
        self.xttrader = _xttrader
        self.trader = _xttrader.XtQuantTrader(path, session, self.callback)

        try:
            self.trader.start()
            ret = self.trader.connect()
            if ret != 0:
                print(f'[交易] 连接失败, 返回码: {ret}')
                return False

            accounts = self.trader.query_account_infos()
            if not accounts:
                print('[交易] 未找到资金账号 — 请在MiniQMT中登录账号')
                return False

            for acc in accounts:
                if acc.account_id == account_id:
                    self._account_obj = acc
                    break
            if self._account_obj is None:
                self._account_obj = accounts[0]
                print(f'[交易] 使用账号: {self._account_obj.account_id}')

            self.trader.subscribe(self._account_obj)
            _time.sleep(0.5)
            self._trade_connected = True
            print(f'[交易] 已订阅账号 {self._account_obj.account_id}')
            return True
        except Exception as e:
            print(f'[交易] 连接异常: {e}')
            _traceback.print_exc()
            return False

    def disconnect(self):
        """断开所有连接"""
        if self._trade_connected and self._account_obj:
            try:
                self.trader.unsubscribe(self._account_obj)
                self.trader.stop()
            except Exception:
                pass
            self._trade_connected = False
        if self._data_connected:
            try:
                self.xtdata.disconnect()
            except Exception:
                pass
            self._data_connected = False

    # ── 数据查询 ──

    def get_history_data(self, length, period, field):
        """
        对应 QMT: ContextInfo.get_history_data(N, '1d', field)
        返回: {stock_code: [values_list]}
        """
        code = STOCK_QMT
        if self._daily_data_cache is None:
            # 首次加载: 从 xtdata 获取全部日线
            end = datetime.now().strftime('%Y%m%d')
            start = (datetime.now() - timedelta(days=365 * 6)).strftime('%Y%m%d')
            data = self.xtdata.get_local_data(
                field_list=['open', 'high', 'low', 'close', 'volume', 'amount'],
                stock_list=[code],
                period='1d',
                start_time=start,
                end_time=end,
                dividend_type='front',
                data_dir='C:/QMT/datadir',
            )
            if code in data and len(data[code]) > 0:
                self._daily_data_cache = data[code]
            else:
                return {}

        df = self._daily_data_cache
        field_map = {
            'close': 'close', 'open': 'open', 'high': 'high',
            'low': 'low', 'volume': 'volume', 'amount': 'amount',
        }
        col = field_map.get(field, field)
        if col in df.columns:
            vals = df[col].values.tolist()
            # 截取最后 length 个值
            if len(vals) > length:
                vals = vals[-length:]
            return {code: vals}
        return {}

    def get_full_tick(self, codes):
        """
        对应 QMT: ContextInfo.get_full_tick([code])
        返回: {code: {lastPrice, open, high, low, bid1-5, ask1-5, ...}}
        """
        try:
            tick = self.xtdata.get_full_tick(codes)
            return tick if tick else {}
        except Exception:
            return {}

    # ── 交易查询 ──

    def query_positions(self):
        """查询持仓 → 模拟 get_trade_detail_data(account, 'STOCK', 'POSITION')"""
        if not self._trade_connected:
            return []
        try:
            positions = self.trader.query_stock_positions(self._account_obj)
            return positions or []
        except Exception:
            return []

    def query_account(self):
        """查询账户 → 模拟 get_trade_detail_data(account, 'STOCK', 'ACCOUNT')"""
        if not self._trade_connected:
            return None
        try:
            return self.trader.query_stock_asset(self._account_obj)
        except Exception:
            return None

    # ── 下单 ──

    def order_stock(self, stock_code, shares, style, price=None):
        """
        对应 QMT: order_shares(stockcode, shares, style, price, ContextInfo, accId)

        style: 'COMPETE' → LATEST_PRICE (最新价/对手价)
               'FIX'     → FIX_PRICE
               'MARKET'  → LATEST_PRICE (市价)
        """
        from xtquant import xtconstant

        if not self._trade_connected:
            _log(f'[下单跳过-未连接交易]')
            return

        if shares > 0:
            order_type = xtconstant.STOCK_BUY
            dir_name = '买入'
        else:
            order_type = xtconstant.STOCK_SELL
            shares = abs(shares)
            dir_name = '卖出'

        # 下单: 用实际触发价(信号价)作为委托价, 便于日志追踪;
        #       COMPETE/LATEST_PRICE 实际成交以对手价为准
        order_price = price if price and price > 0 else 0
        price_type = xtconstant.FIX_PRICE

        _log('  >>> 下单{}: Y{:.2f} x {}股 {}'.format(dir_name, order_price, shares, stock_code))

        try:
            ret = self.trader.order_stock(
                self._account_obj,
                stock_code,
                order_type,
                shares,
                price_type,
                order_price,
                'mini反T_v12',
                '迷你反T_{}'.format(dir_name),
            )
            self.order_pending = True
            return ret
        except Exception as e:
            _log('[下单异常] {}'.format(e))
            self.order_pending = False
            return None


# ============================================================================
# QMT ContextInfo 模拟
# ============================================================================

class MockContextInfo:
    """
    模拟 QMT 的 ContextInfo 对象。
    提供 v10 策略需要的接口: get_history_data, get_full_tick, is_last_bar, run_time 等
    """

    def __init__(self, connector: MiniQMTConnector):
        self.conn = connector
        self.st = {}           # 策略状态字典 (对应 ContextInfo.st)
        self.accID = ACCOUNT
        self._barpos = 0

    @property
    def barpos(self):
        return self._barpos

    @barpos.setter
    def barpos(self, value):
        self._barpos = value

    def set_universe(self, stock_list):
        pass  # MiniQMT 不需要设置股票池

    def set_account(self, acc_id):
        self.accID = acc_id

    def get_history_data(self, length, period, field,
                         dividend_type=0, skip_paused=True):
        """QMT: ContextInfo.get_history_data(N, '1d', field)"""
        if period != '1d':
            _log(f'[警告] 仅支持1d周期, 收到: {period}')
            return {}
        return self.conn.get_history_data(length, period, field)

    def get_full_tick(self, codes):
        """QMT: ContextInfo.get_full_tick([code])"""
        return self.conn.get_full_tick(codes)

    def is_last_bar(self):
        """MiniQMT 实盘: 永远是"最新"bar"""
        return True

    def run_time(self, func_name, interval, start_time='', market=''):
        """
        QMT: ContextInfo.run_time("ontimer", "1nSecond", ...)
        MiniQMT: 不实际操作, 由主循环处理
        """
        pass

    def get_stock_name(self, code):
        return STOCK_NAME

    def get_open_date(self, code):
        return '20140701'  # 长飞光纤上市日期


# ============================================================================
# QMT 全局函数模拟
# ============================================================================

# 这些函数在 QMT 中是直接注入到模块命名空间的, 不是 ContextInfo 的方法

class MockPosition:
    """模拟 QMT 的持仓对象"""
    def __init__(self, xt_pos=None):
        if xt_pos is not None:
            self.m_strInstrumentID = xt_pos.stock_code.split('.')[0] if hasattr(xt_pos, 'stock_code') else ''
            self.m_nVolume = getattr(xt_pos, 'volume', 0)
            self.m_dOpenPrice = getattr(xt_pos, 'open_price', 0.0)
            self.m_nCanUseVolume = getattr(xt_pos, 'can_use_volume', getattr(xt_pos, 'volume', 0))
        else:
            self.m_strInstrumentID = ''
            self.m_nVolume = 0
            self.m_dOpenPrice = 0.0
            self.m_nCanUseVolume = 0


class MockAccount:
    """模拟 QMT 的账户对象"""
    def __init__(self, asset=None):
        if asset is not None:
            self.m_dAvailable = getattr(asset, 'cash', 0.0)
            self.m_dBalance = getattr(asset, 'total_asset', 0.0)
        else:
            self.m_dAvailable = 0.0
            self.m_dBalance = 0.0


# ── 全局引用 (在 run() 中初始化) ──
_global_conn: Optional[MiniQMTConnector] = None
_global_dry_run = False


def get_trade_detail_data(account_id, account_type, data_type):
    """
    模拟 QMT: get_trade_detail_data(account, 'STOCK', 'POSITION'/'ACCOUNT')
    """
    conn = _global_conn
    if conn is None:
        return []

    if data_type.upper() == 'POSITION':
        xt_positions = conn.query_positions()
        result = []
        for xp in xt_positions:
            result.append(MockPosition(xp))
        # 如果策略有虚拟卖出但还没买回, xt_positions 中可能已减仓,
        # 但策略需要的 base_shares 应该从 st 中读取, 这里返回的是实际持仓
        return result

    elif data_type.upper() == 'ACCOUNT':
        asset = conn.query_account()
        if asset is not None:
            return [MockAccount(asset)]
        return [MockAccount()]  # fallback

    return []


def order_shares(stockcode, shares, style='LATEST', price=None,
                 ContextInfo=None, accId=None):
    """
    模拟 QMT: order_shares(code, shares, style, price, ContextInfo, accId)

    参数处理兼容 QMT 的多种调用方式:
      order_shares(code, shares, 'COMPETE', ContextInfo, accId)
      order_shares(code, shares, 'FIX', price, ContextInfo, accId)
    """
    conn = _global_conn
    if conn is None:
        _log('[order_shares] 未连接, 跳过')
        return

    # 解析参数: QMT中第4个参数可能是price或ContextInfo
    _ctx = ContextInfo
    _price = price
    if price is not None and not isinstance(price, (int, float)):
        # price 参数实际传的是 ContextInfo
        _ctx = price
        _price = None

    if _global_dry_run:
        direction = '买入' if shares > 0 else '卖出'
        px = _price if _price else '(对手价)'
        _log(f'[模拟] {direction} {stockcode} {px} x {abs(shares)}股')
        return 0

    return conn.order_stock(stockcode, shares, style, _price)


# ============================================================================
# Strategy main cycle
# ============================================================================

class StrategyRunner:
    """
    MiniQMT Strategy — in VS Code run state machine。

    main cycle duty:
      1. 每个交易日开盘时: 获取日线数据 → 计算当日信号 → 重置状态机
      2. 盘中每秒: 获取实时价格 → 驱动状态机 (IDLE→SPIKING→SOLD→DIPPING→DONE)
      3. 尾盘 14:57: 未完成的交易强制买回
    """

    def __init__(self, dry_run=False):
        global _global_conn, _global_dry_run

        self.conn = MiniQMTConnector()
        _global_conn = self.conn
        _global_dry_run = dry_run

        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st          # 策略状态 (shortcut)
        self.dry_run = dry_run

        self._last_heartbeat = 0.0
        self._last_trade_date = ''     # 用于跨日检测
        self._signal_printed = False   # 当日信号是否已打印
        self._running = True

        # 交易统计
        self.total_t_days = 0
        self.total_pnl = 0.0
        self.day_pnl = 0.0

    # ── 初始化 ──

    def _init_state(self):
        """初始化策略状态字典 (对应 v10 init() 中的 ContextInfo.st)"""
        self.st.update({
            'daily_signal': None,
            'base_shares': 0,
            'base_can_use': 0,
            'base_cost': 0.0,
            'entry_price': 0.0,
            'fstate': STATE_IDLE,
            'peak_price': 0.0,
            'dip_price': 0.0,
            'sell_fill_price': 0.0,
            'buyback_target': 0.0,
            'buyback_target_pct': 0.0,
            'day_pnl': 0.0,
            'stop_loss_hit': False,
            'total_t_days': self.total_t_days,
            'total_pnl': self.total_pnl,
            'entry_price': 0.0,
            'trade_date': '',
            '_guard_date': '',
            'initialized': False,
            'init_attempts': 0,
            'last_init_time': 0.0,
            'startup_printed': False,
            '_startup_guard': '',
            'state_enter_time': '',
            'sell_elapsed_bars': 0,
            'last_heartbeat': 0.0,
            'last_fstate': '',
            '_last_logged_transition': '',
            'ontimer_errors': 0,
            'callback_errors': 0,
            
            'order_pending': False,
            'order_side': '',
            'order_signal_price': 0.0,
            'order_sent_at': 0.0,
            'order_retries': 0,
            'order_retry_logged': False,

            # ★ v12: 锁仓状态
            'locked': False,                 # 是否已锁仓
            'lock_reason': '',               # 锁仓原因
            'lock_since': '',                # 锁仓开始时间
            'lock_cooldown_until': 0.0,      # 解锁冷却到何时
            'price_history': [],             # [(timestamp, price), ...] 近5分钟价格
        })

    # ── 每日初始化 ──

    def _daily_init(self):
        """每日信号计算 + 状态机重置"""
        today = datetime.now().strftime('%Y%m%d')

        # 跨日检测
        if self.st.get('trade_date', '') == today and self.st.get('initialized', False):
            # 同日已初始化, 只刷新持仓
            self._refresh_position()
            return

        is_new_day = self.st.get('trade_date', '') and self.st['trade_date'] != today
        if is_new_day:
            _log(f'\n[新交易日] {self.st["trade_date"]} -> {today}')

        # ★ 保存同日状态 (跨日才重置)
        saved_trail = self.st.get('bt_max_trail', 0) if not is_new_day else 0
        saved_history = self.st.get('price_history', []) if not is_new_day else []

        self._reset_daily()
        self.st['trade_date'] = today
        self.st['_guard_date'] = today

        # ★ 同日恢复: 保留上午积累的触发线和价格历史
        if not is_new_day and saved_trail > 0:
            self.st['bt_max_trail'] = saved_trail
            self.st['price_history'] = saved_history
            _log('[恢复] 正T触发线最高记录: Y{:.2f}'.format(saved_trail))

        # ── 获取日线数据 ──
        hist_close  = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'close')
        hist_open   = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'open')
        hist_high   = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'high')
        hist_low    = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'low')
        hist_volume = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'volume')

        if STOCK_QMT not in hist_close or len(hist_close[STOCK_QMT]) < 60:
            _log('[警告] 日线数据不足, 跳过今日')
            return

        # ── 读取持仓 ──
        self._refresh_position()

        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)

        if self.st.get('entry_price', 0) == 0.0:
            self.st['entry_price'] = self.st.get('base_cost', 0.0)

        # ── ★ 修正开盘价: xtdata 日线最后一条是昨日完整bar,
        #        opens[-1] 是昨日开盘而非今日开盘, 需用 tick 的 open 替换 ──
        tick_now = self.ctx.get_full_tick([STOCK_QMT])
        today_open = tick_now.get(STOCK_QMT, {}).get('open', 0)
        opens_list = list(hist_open[STOCK_QMT])
        if today_open > 0 and len(opens_list) > 0:
            opens_list[-1] = today_open
            _log('[数据] 今日开盘: Y{:.2f} (来自tick)'.format(today_open))
        else:
            _log('[警告] 无法获取今日开盘价, 使用昨日数据(可能不准)')

        # ── 计算当日信号 ──
        signal = compute_signal(
            opens_list, hist_high[STOCK_QMT],
            hist_low[STOCK_QMT], hist_close[STOCK_QMT], hist_volume[STOCK_QMT]
        )
        if signal is None:
            return

        # ═══════════════════════════════════════════════════════════
        # ★ v13: 仓位评估 + 方向决策 + 动态手数
        # ═══════════════════════════════════════════════════════════
        open_price = signal['open_price']
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0

        # 实时价格
        tick_now = self.ctx.get_full_tick([STOCK_QMT])
        curr_price_now = tick_now.get(STOCK_QMT, {}).get('lastPrice', open_price)
        if curr_price_now <= 0:
            curr_price_now = open_price

        lot_cost = curr_price_now * TRADE_LOT_SIZE * 1.005
        pos_value = base_shares * curr_price_now
        total_asset = pos_value + avail_cash
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0

        # ── 动态手数计算 ──
        short_lots = min(base_can_use // TRADE_LOT_SIZE, MAX_DAILY_TRADES)
        long_lots_cash = int(avail_cash / (curr_price_now * TRADE_LOT_SIZE * 1.01))
        long_lots_sell = base_can_use // TRADE_LOT_SIZE  # T+1: 卖需可用持仓
        long_lots = min(long_lots_cash, long_lots_sell, MAX_DAILY_TRADES)

        # ── 反T可行性 ──
        do_short = signal['do_short'] and (short_lots >= MIN_POSITION_LOTS)
        short_reason = ''
        if not signal['do_short']:
            short_reason = signal.get('blocked_reason', '信号禁止')
        elif short_lots < MIN_POSITION_LOTS:
            short_reason = '可用{}股<{}手'.format(base_can_use, MIN_POSITION_LOTS)

        # ── 正T可行性 ──
        do_long = long_lots >= MIN_POSITION_LOTS
        long_reason = ''
        if not do_long:
            reasons = []
            if long_lots_cash < MIN_POSITION_LOTS:
                reasons.append('资金不足(需Y{:,.0f}>Y{:,.0f})'.format(
                    curr_price_now * TRADE_LOT_SIZE * 1.01, avail_cash))
            if long_lots_sell < MIN_POSITION_LOTS:
                reasons.append('T+1:无可卖持仓(可用{}股)'.format(base_can_use))
            long_reason = '; '.join(reasons) if reasons else '未知'

        # ── 正T目标价 ──
        buy_trigger_floor = round(open_price * (1.0 - BUY_TRIGGER_PCT), 2)
        buy_trigger_trail = round(curr_price_now * (1.0 - BUY_TRIGGER_TRAIL), 2)
        buy_trigger = max(buy_trigger_floor, buy_trigger_trail)
        sellback_target_hint = round(buy_trigger * (1.0 + SELLBACK_RISE_PCT), 2)

        # ── 仓位建议 ──
        if pos_pct > 80:
            pos_advice = '仓位过重(>{:.0f}%), 不建议加仓'.format(pos_pct)
        elif trend == 'strong_bull':
            pos_advice = '强牛持有, 不做反T'
        elif trend == 'bear':
            pos_advice = '熊市积极反T({}手可用)'.format(short_lots)
        else:
            pos_advice = '可反T{}手 / 可正T{}手'.format(short_lots, long_lots)

        # ── 写入signal+st ──
        signal['do_short'] = do_short
        signal['short_reason'] = short_reason
        signal['buy_trigger'] = buy_trigger
        signal['buy_trigger_floor'] = buy_trigger_floor
        signal['buy_trigger_trail'] = buy_trigger_trail
        signal['sellback_target_hint'] = sellback_target_hint

        self.st['daily_signal'] = signal
        self.st['do_short'] = do_short
        self.st['do_long'] = do_long
        self.st['long_reason'] = long_reason
        self.st['short_lots'] = short_lots
        self.st['long_lots'] = long_lots
        self.st['pos_value'] = pos_value
        self.st['pos_pct'] = pos_pct
        self.st['avail_cash'] = avail_cash
        self.st['pos_advice'] = pos_advice
        self.st['trade_count_short'] = 0   # 今日反T已成交次数
        self.st['trade_count_long'] = 0    # 今日正T已成交次数

        # ── 重置状态机 ──
        self.st['fstate']             = STATE_IDLE
        self.st['peak_price']         = 0.0
        self.st['dip_price']          = 0.0
        self.st['sell_fill_price']    = 0.0
        self.st['buyback_target']     = 0.0
        self.st['buyback_target_pct'] = 0.0
        self.st['day_pnl']            = 0.0
        self.st['stop_loss_hit']      = False
        self.st['state_enter_time']   = _now()
        self.st['sell_elapsed_bars']  = 0
        self.st['initialized']        = True
        # 正T状态变量
        self.st['bt_dip_price']       = 0.0
        self.st['bt_buy_trigger']     = 0.0
        self.st['bt_buy_fill_price']  = 0.0
        self.st['bt_sellback_target'] = 0.0
        self.st['bt_max_trail']       = 0.0   # 动态触发线历史最高值
        # 锁仓
        self.st['locked']             = False
        self.st['lock_reason']        = ''
        self.st['lock_since']         = ''
        self.st['bt_sell_peak_price'] = 0.0

        # ── 打印信号 ──
        self._print_signal(signal)

    def _refresh_position(self):
        """刷新当前持仓和可用资金"""
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                break

    def _reset_daily(self):
        """重置日内状态 (保留累计统计和底仓)"""
        guard = self.st.get('_guard_date', '')
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        base_cost = self.st.get('base_cost', 0.0)
        entry_price = self.st.get('entry_price', 0.0)

        self._init_state()

        self.st['base_shares'] = base_shares
        self.st['base_can_use'] = base_can_use
        self.st['base_cost'] = base_cost
        self.st['entry_price'] = entry_price
        self.st['_guard_date'] = guard
        self.st['total_t_days'] = self.total_t_days
        self.st['total_pnl'] = self.total_pnl
        # 正T变量
        self.st['bt_dip_price'] = 0.0
        self.st['bt_buy_trigger'] = 0.0
        self.st['bt_buy_fill_price'] = 0.0
        self.st['bt_sellback_target'] = 0.0
        self.st['bt_max_trail'] = 0.0
        self.st['locked'] = False
        self.st['lock_reason'] = ''
        self.st['lock_since'] = ''
        self.st['bt_sell_peak_price'] = 0.0

    def _print_signal(self, signal):
        """打印当日信号和账户状态"""
        if self.st.get('_startup_guard', '') == self.st.get('trade_date', ''):
            return
        self.st['_startup_guard'] = self.st.get('trade_date', '')

        self._signal_printed = True
        base_shares = self.st['base_shares']
        base_cost = self.st['base_cost']

        tick = self.ctx.get_full_tick([STOCK_QMT])
        curr_price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if curr_price <= 0:
            curr_price = signal['close_yday']

        pos_value = base_shares * curr_price
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0
        total_val = pos_value + avail_cash

        do_short = self.st.get('do_short', False)
        do_long = self.st.get('do_long', False)

        # 预提取所有信号值
        sell_trig = signal.get('sell_trigger', 0)
        buy_trig = signal.get('buy_trigger', 0)
        atr_val = signal.get('atr', 0)
        atr_pct_val = signal.get('atr_pct', 0)
        sell_mult = signal.get('sell_mult', 0)
        open_p = signal.get('open_price', 0)
        trend = signal.get('trend', '?')
        rsi_val = signal.get('rsi', 0)
        vol_r = signal.get('vol_ratio', 0)
        sell_hint = signal.get('sellback_target_hint', 0)
        range_capped = signal.get('range_capped', False)
        factor_details = signal.get('factor_details', {})
        trade_date = self.st.get('trade_date', '')

        # 监控计划
        plan_parts = []
        if do_short:
            plan_parts.append('卖出(反T): 触发Y{:.2f}'.format(sell_trig))
        else:
            plan_parts.append('卖出(反T): 不可用')
        if do_long:
            plan_parts.append('买入(正T): 触发Y{:.2f}'.format(buy_trig))
        else:
            plan_parts.append('买入(正T): 不可用')

        monitor_mode = ' + '.join([p for p in plan_parts if '不可用' not in p])
        if not monitor_mode:
            monitor_mode = '今日无可用交易方向'

        _log('')
        _log('╔' + '═' * 55 + '╗')
        _log('║  {}  迷你反T v10  |  {}  |  {}  ║'.format(STOCK_NAME, trade_date, trend))
        _log('╠' + '═' * 55 + '╣')
        _log('║  开盘: Y{:.2f}  |  ATR: {:.1f}%  |  RSI: {:.0f}  |  量比: {:.2f}    ║'.format(
            open_p, atr_pct_val * 100, rsi_val, vol_r))
        _log('║  持仓: {}股 Y{:,.0f}({:.0f}%)  |  可用: Y{:,.0f}  |  总资产: Y{:,.0f}  ║'.format(
            base_shares, pos_value, self.st.get('pos_pct', 0), avail_cash, total_val))
        _log('║  T+0可用: {}股(反T{}手)  T+1锁定: {}股  |  {}  ║'.format(
            base_can_use, self.st.get('short_lots', 0),
            base_shares - base_can_use, self.st.get('pos_advice', '')))
        _log('╠' + '═' * 55 + '╣')
        _log('║  ★ 监控: {:<44s} ║'.format(monitor_mode))
        _log('╚' + '═' * 55 + '╝')

        # 反T详情
        if do_short:
            _log('')
            _log('  ┌─ [反T — 先卖出后买回] ─')
            _log('  │  ★ 卖出触发线:   Y{:.2f}'.format(sell_trig))
            _log('  │     (开盘{:.2f} + ATR{:.2f} x 乘数{:.2f})'.format(open_p, atr_val, sell_mult))
            if range_capped:
                _log('  │     [振幅约束] 原始Y{:.2f} -> 上限Y{:.2f}'.format(
                    signal.get('sell_trigger_raw', 0), sell_trig))
            _log('  │  卖出确认: 回落 >= {:.2f}% -> 执行卖出'.format(PULLBACK_PCT * 100))
            _log('  │  买回触发:  卖价 x (1 - ATR% x {:.2f})'.format(BUYBACK_TRIGGER_MULT))
            _log('  │  买回确认:  回升 >= {:.2f}% -> 执行买回'.format(BOUNCE_PCT * 100))
            _log('  │  紧急买回: +{:.0f}% | 止损: {:.1f}% | 尾盘: {}'.format(
                EMERGENCY_BUYBACK_PCT * 100, STOP_LOSS_PCT * 100, FORCE_CLOSE_TIME))
            for name, dev in factor_details.items():
                if dev != 0:
                    _log('  │  [{}因子 {:+.2f}]'.format(name, dev))
            _log('  └' + '─' * 45)
        else:
            reason = signal.get('short_reason', signal.get('blocked_reason', '信号禁止'))
            _log('  反T(卖出): x 不可用 — {}'.format(reason))

        # 正T详情
        if do_long:
            _log('')
            _log('  ┌─ [正T — 先买入后卖出] ─')
            _log('  │  ★ 买入触发线:   Y{:.2f} (动态跟随盘面)'.format(buy_trig))
            _log('  │     底线: Y{:.2f} (开盘{:.2f} - {:.1f}%)'.format(
                signal.get('buy_trigger_floor', 0), open_p, BUY_TRIGGER_PCT * 100))
            _log('  │     跟随: Y{:.2f} (当前价 - {:.1f}%)  ← 实时更新'.format(
                signal.get('buy_trigger_trail', 0), BUY_TRIGGER_TRAIL * 100))
            _log('  │     取较高者 -> 价格涨了触发线跟着涨')
            _log('  │  买入确认: 回升 >= {:.2f}% -> 执行买入'.format(BOUNCE_PCT * 100))
            _log('  │  ★ 卖出触发线:   Y{:.2f} (买价 + {:.1f}%)'.format(sell_hint, SELLBACK_RISE_PCT * 100))
            _log('  │  卖出确认: 回落 >= {:.2f}% -> 执行卖出'.format(PULLBACK_PCT * 100))
            _log('  │  1手需 Y{:,.0f}  |  可用 Y{:,.0f}'.format(curr_price * TRADE_LOT_SIZE, avail_cash))
            _log('  └' + '─' * 45)
        else:
            reason = self.st.get('long_reason', '未知')
            _log('  正T(买入): x 不可用 — {} | 1手需 Y{:,.0f}'.format(
                reason, curr_price * TRADE_LOT_SIZE))

        if self.total_t_days > 0:
            _log('')
            _log('  ── 累计: {}笔  |  毛利~Y{:,.0f}  ──'.format(self.total_t_days, self.total_pnl))
        _log('')

    def _handle_idle(self, price):
        st = self.st
        signal = st.get('daily_signal', {})

        # ── 反T (先卖后买): 价格 >= 卖出触发线 ──
        if st.get('do_short', False):
            trigger = signal.get('sell_trigger', 999999)
            if price >= trigger:
                can_use = st.get('base_can_use', st['base_shares'])
                if can_use < TRADE_LOT_SIZE:
                    _log(f'[反T跳过] 可用{can_use}股 < 1手')
                    return

                # ★ v13: 单日交易次数限制
                tc = st.get('trade_count_short', 0)
                if tc >= MAX_DAILY_TRADES:
                    _log('[反T跳过] 已达单日上限{}次'.format(MAX_DAILY_TRADES))
                    return

                # ★ v12: 锁仓检查
                if st.get('locked', False):
                    _log('[反T锁定] 盘面强势({}), 跳过'.format(st.get('lock_reason', '')))
                    return

                st['trade_count_short'] = tc + 1
                st['fstate'] = STATE_SPIKING
                st['peak_price'] = price
                st['state_enter_time'] = _now()
                _log('[反T冲高#{}/{}] Y{:.2f} >= Y{:.2f}'.format(tc + 1, MAX_DAILY_TRADES, price, trigger))
                return

        # ── 正T (先买后卖): 价格 <= 买入触发线 ──
        if st.get('do_long', False):
            buy_trigger = signal.get('buy_trigger', 0)
            if price <= buy_trigger:
                # ★ v13: 单日交易次数限制
                tc = st.get('trade_count_long', 0)
                if tc >= MAX_DAILY_TRADES:
                    _log('[正T跳过] 已达单日上限{}次'.format(MAX_DAILY_TRADES))
                    return

                st['trade_count_long'] = tc + 1
                st['fstate'] = STATE_BT_DIPPING
                st['bt_dip_price'] = price
                st['bt_buy_trigger'] = buy_trigger
                st['state_enter_time'] = _now()
                under_pct = (buy_trigger - price) / buy_trigger * 100
                _log('[正T探底#{}/{}] Y{:.2f} <= Y{:.2f}(-{:.2f}%)'.format(
                    tc + 1, MAX_DAILY_TRADES, price, buy_trigger, under_pct))
                return

    def _handle_spiking(self, price):
        st = self.st
        trigger = st['daily_signal']['sell_trigger']

        if price > st['peak_price']:
            st['peak_price'] = price

        peak = st['peak_price']
        pullback = (peak - price) / peak if peak > 0 else 0

        if pullback >= PULLBACK_PCT:
            _log(f'[卖出] 最高Y{peak:.2f} 回落{pullback*100:.2f}% -> Y{price:.2f} 确认卖出')

            atr_pct = st['daily_signal']['atr_pct']
            buyback_pct = atr_pct * BUYBACK_TRIGGER_MULT
            buyback_target = round(price * (1.0 - buyback_pct), 2)

            st['sell_fill_price'] = price
            st['buyback_target'] = buyback_target
            st['buyback_target_pct'] = buyback_pct * 100
            st['sell_elapsed_bars'] = 0
            st['fstate'] = STATE_SOLD
            st['state_enter_time'] = _now()

            _log(f'  买回触发线: Y{buyback_target:.2f} (卖价-{buyback_pct*100:.2f}%)')
            _log(f'  紧急买回线: Y{price*(1+EMERGENCY_BUYBACK_PCT):.2f} (卖价+{EMERGENCY_BUYBACK_PCT*100:.1f}%)')

            # ★ 下单卖出
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)

    def _handle_sold(self, price):
        st = self.st
        sp = st['sell_fill_price']
        bt = st['buyback_target']

        # 紧急买回
        emergency_line = sp * (1.0 + EMERGENCY_BUYBACK_PCT)
        if price >= emergency_line:
            rise_pct = (price - sp) / sp * 100
            _log(f'[紧急买回] 卖Y{sp:.2f} -> 现Y{price:.2f}(+{rise_pct:.2f}%)')
            self._do_buyback(price, '紧急')
            return

        # 动态收紧
        tightened_bt = bt
        if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
            tightened_bt = sp * (
                1.0 - st['daily_signal']['atr_pct']
                * BUYBACK_TRIGGER_MULT
                * BUYBACK_TIGHTEN_MULT
            )
            tightened_bt = round(max(tightened_bt, bt), 2)

        # 进入DIPPING
        if price <= tightened_bt:
            drop_pct = (sp - price) / sp * 100
            st['fstate'] = STATE_DIPPING
            st['dip_price'] = price
            st['state_enter_time'] = _now()
            tag = '(收紧)' if tightened_bt > bt else ''
            _log(f'[买回触发{tag}] Y{price:.2f}(-{drop_pct:.2f}%)')

    def _handle_dipping(self, price):
        st = self.st
        bt = st['buyback_target']

        if price < st['dip_price']:
            st['dip_price'] = price

        dip = st['dip_price']
        if dip <= 0:
            st['dip_price'] = price
            dip = price

        bounce = (price - dip) / dip

        if bounce >= BOUNCE_PCT:
            sell_p = st['sell_fill_price']
            gross = (sell_p - price) * TRADE_LOT_SIZE
            _log(f'[买回] 最低Y{dip:.2f} 回升{bounce*100:.2f}% -> Y{price:.2f}')
            _log(f'  卖Y{sell_p:.2f} -> 买Y{price:.2f} | 毛利~Y{gross:.0f}')
            self._do_buyback(price, '正常')
            self.total_t_days += 1
            self.total_pnl += gross

    def _do_buyback(self, price, reason=''):
        """下单买回"""
        need = price * TRADE_LOT_SIZE * 1.001
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail = account[0].m_dAvailable if account else 0.0

        if avail < need:
            _log(f'[买回失败-{reason}] 资金不足 (需Y{need:,.0f} > Y{avail:,.0f})')
            return

        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
        st = self.st
        st['fstate'] = STATE_DONE
        _log(f'  >>> 下单买回({reason}): Y{price:.2f} x {TRADE_LOT_SIZE}股')

    def _force_buyback(self):
        """尾盘/止损强制买回"""
        _log(f'[强制买回] 对手价 x {TRADE_LOT_SIZE}股')
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
        self.st['fstate'] = STATE_FORCED

    # ── 正T状态处理 (新增) ──

    def _handle_bt_dipping(self, price):
        """正T DIPPING: 跟踪最低点, 等回升确认买入"""
        st = self.st

        if price < st.get('bt_dip_price', price):
            st['bt_dip_price'] = price

        dip = st.get('bt_dip_price', price)
        if dip <= 0:
            st['bt_dip_price'] = price
            dip = price

        bounce = (price - dip) / dip

        # 回升确认 -> 买入
        if bounce >= BOUNCE_PCT:
            _log(f'[正T买入] 最低Y{dip:.2f} 回升{bounce*100:.2f}% -> Y{price:.2f}')

            # 检查资金
            need = price * TRADE_LOT_SIZE * 1.001
            account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
            avail = account[0].m_dAvailable if account else 0.0
            if avail < need:
                _log(f'[正T买入失败] 资金不足 (需Y{need:,.0f} > Y{avail:,.0f})')
                st['fstate'] = STATE_IDLE
                return

            order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            st['fstate'] = STATE_BT_BOUGHT
            st['bt_buy_fill_price'] = price
            st['bt_sellback_target'] = round(price * (1.0 + SELLBACK_RISE_PCT), 2)
            _log(f'  买价Y{price:.2f} | 卖回触发线: Y{st["bt_sellback_target"]:.2f}(+{SELLBACK_RISE_PCT*100:.1f}%)')

    def _handle_bt_bought(self, price):
        """正T BOUGHT: 已买入, 等涨到卖回触发线 + 止损保护"""
        st = self.st
        target = st.get('bt_sellback_target', 999999)
        buy_price = st.get('bt_buy_fill_price', 0)

        # 止损: 跌超买价1.5% → 强制卖出
        if buy_price > 0 and price <= buy_price * (1.0 - STOP_LOSS_PCT):
            loss_pct = (price - buy_price) / buy_price * 100
            _log('[正T止损] 买Y{:.2f} 现Y{:.2f}({:.1f}%) 触发止损'.format(buy_price, price, loss_pct))
            self._do_bt_force_sell()
            return

        # 涨到卖出触发线 → 进入冲高监控
        if price >= target:
            rise = (price - buy_price) / buy_price * 100
            st['fstate'] = STATE_BT_SPIKING
            st['bt_sell_peak_price'] = price
            _log('[正T卖回监控] 涨{:.2f}% -> Y{:.2f} >= Y{:.2f} | 等回落{:.2f}%'.format(
                rise, price, target, PULLBACK_PCT * 100))

    def _handle_bt_spiking(self, price):
        """正T SPIKING: 涨到位, 等回落确认卖出"""
        st = self.st

        if price > st.get('bt_sell_peak_price', price):
            st['bt_sell_peak_price'] = price

        peak = st.get('bt_sell_peak_price', price)
        pullback = (peak - price) / peak if peak > 0 else 0

        # 回落确认 -> 卖出
        if pullback >= PULLBACK_PCT:
            bp = st['bt_buy_fill_price']
            gross = (price - bp) * TRADE_LOT_SIZE
            _log(f'[正T卖出] 峰值Y{peak:.2f} 回落{pullback*100:.2f}% -> Y{price:.2f} | 毛利~Y{gross:.0f}')

            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            st['fstate'] = STATE_DONE
            self.total_t_days += 1
            self.total_pnl += gross

        # 假突破 -> 回到BOUGHT (需跌破卖出触发线0.1%才算)
    def _do_bt_force_sell(self):
        """正T尾盘/止损强制卖出"""
        st = self.st
        _log(f'[正T强制卖出] 对手价 x {TRADE_LOT_SIZE}股')
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
        st['fstate'] = STATE_FORCED

    # ── ★ v12: 锁仓强度评估 ──

    def _assess_strength(self, price, now_ts):
        """
        评估盘面实时强度, 决定是否锁仓。

        三个条件同时满足 → 锁仓 (禁止卖出):
          (1) 价格远超开盘 (现价 > 开盘 × 1.015)
          (2) 近5分钟持续上攻 (涨幅 > 0.3%)
          (3) 距日内最高价很近 (回撤 < 0.5%, 无见顶迹象)

        锁仓有冷却期: 条件消失后需等 LOCK_COOLDOWN_SEC 才解锁,
        避免频繁切换。
        """
        st = self.st
        sig = st.get('daily_signal', {})
        open_price = sig.get('open_price', 0)
        if open_price <= 0:
            return

        # 更新价格历史 (保留最近 LOCK_LOOKBACK_SEC 的数据)
        st['price_history'].append((now_ts, price))
        cutoff = now_ts - LOCK_LOOKBACK_SEC
        st['price_history'] = [(t, p) for t, p in st['price_history'] if t >= cutoff]

        history = st['price_history']
        if len(history) < 10:
            return  # 数据不够, 不判断

        prices = [p for _, p in history]
        price_5min_ago = prices[0]
        price_now = prices[-1]

        # ── 条件1: 价格远超开盘 ──
        cond1 = price_now > open_price * (1.0 + LOCK_PRICE_RATIO)

        # ── 条件2: 近5分钟持续上行 ──
        momentum = (price_now - price_5min_ago) / price_5min_ago if price_5min_ago > 0 else 0
        cond2 = momentum > LOCK_MOMENTUM_PCT

        # ── 条件3: 距日内最高很近 (无回调迹象) ──
        day_high = max(prices)
        drawdown = (day_high - price_now) / day_high if day_high > 0 else 1
        cond3 = drawdown < LOCK_DRAWDOWN_PCT

        should_lock = cond1 and cond2 and cond3
        cooldown_ok = now_ts >= st.get('lock_cooldown_until', 0)

        if should_lock and not st.get('locked'):
            st['locked'] = True
            st['lock_since'] = _now()
            st['lock_reason'] = '开盘+{:.1f}% 动量+{:.2f}% 回撤{:.2f}%'.format(
                (price_now / open_price - 1) * 100, momentum * 100, drawdown * 100)
            _log('[锁仓] {} | 盘面强势, 禁止卖出'.format(st['lock_reason']))

        elif not should_lock and st.get('locked') and cooldown_ok:
            st['locked'] = False
            st['lock_reason'] = ''
            st['lock_since'] = ''
            _log('[解锁] 强势条件消失, 恢复卖出监控')

        elif not should_lock and st.get('locked') and not cooldown_ok:
            # 条件消失但冷却中 — 保持锁仓
            pass

        if not should_lock and not st.get('locked'):
            st['lock_cooldown_until'] = 0.0  # 不需要冷却

        if should_lock:
            st['lock_cooldown_until'] = 0.0  # 锁仓中不需要冷却
        elif st.get('locked'):
            # 刚解锁或条件消失, 设置冷却
            if st['lock_cooldown_until'] == 0.0:
                st['lock_cooldown_until'] = now_ts + LOCK_COOLDOWN_SEC

    # ── 主循环 ──

    def run(self):
        """启动策略主循环"""
        global _global_conn
        _global_conn = self.conn

        # 连接 MiniQMT
        if not self.dry_run:
            if not self.conn.connect_data():
                print('[错误] 无法连接行情服务, 退出')
                return
            if not self.conn.connect_trade():
                print('[错误] 无法连接交易服务, 退出')
                self.conn.disconnect()
                return
        else:
            if not self.conn.connect_data():
                print('[错误] 无法连接行情服务, 退出')
                return
            _log('[信号mode] 已连接行情, 不下单')

        # 初始化状态
        self._init_state()
        _log(f'{STOCK_NAME} 迷你反T v10 MiniQMT版 启动')
        _log(f'  mode: {"信号监控(不下单)" if self.dry_run else "实盘交易"}')
        _log(f'  标的: {STOCK_NAME}({STOCK_QMT})')
        _log(f'  触发BASE: bear={SELL_TRIGGER_BASE_BEAR} sideways={SELL_TRIGGER_BASE_SIDEWAYS} weak_bull={SELL_TRIGGER_BASE_WEAK_BULL}')

        # ── 首次每日初始化 ──
        try:
            self._daily_init()
        except Exception as e:
            _log(f'[异常] 初始化失败: {e}')
            _traceback.print_exc()

        _log('开始监控... (Ctrl+C 停止)')
        _log('')

        try:
            while self._running:
                now = _now()
                now_ts = _time.time()

                # ── 非交易时段 → 休眠 + 等待心跳 ──
                if not _is_market_open(now):
                    # 跨日检测: 收盘后重置, 为下个交易日准备
                    today = datetime.now().strftime('%Y%m%d')
                    if self.st.get('trade_date', '') != today:
                        _log(f'[盘前] 新交易日 {today}, 初始化...')
                        try:
                            self._daily_init()
                        except Exception as e:
                            _log(f'[异常] 初始化失败: {e}')
                            _traceback.print_exc()

                    # 每5分钟输出一次等待心跳
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        if now < '09:30:00':
                            _log(f'[等待开盘] {now} — 距开盘 {_time_to_open(now)}')
                        elif now > '15:00:00':
                            _log(f'[已收盘] {now} — 策略待命')
                        elif '11:30:00' < now < '13:00:00':
                            _log(f'[午休] {now} — 等待下午开盘 13:00')
                    _time.sleep(10)
                    continue

                # ── 终态检查 ──
                fstate = self.st.get('fstate', STATE_IDLE)
                if fstate in (STATE_DONE, STATE_FORCED):
                    _time.sleep(5)
                    continue

                # ── 获取实时价格 ──
                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick:
                    _time.sleep(1)
                    continue

                price = tick[STOCK_QMT].get('lastPrice', 0)
                if price <= 0:
                    _time.sleep(1)
                    continue

                # ── 前置检查: 至少有一个方向可用 ──
                signal = self.st.get('daily_signal')
                do_short = self.st.get('do_short', False)
                do_long = self.st.get('do_long', False)
                if signal is None or (not do_short and not do_long):
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        reasons = []
                        if signal and not do_short:
                            reasons.append('反T:' + (signal.get('short_reason') or signal.get('blocked_reason', '禁止')))
                        if not do_long:
                            reasons.append('正T:' + self.st.get('long_reason', '禁止'))
                        _log('[待命] Y{:.2f} | {}'.format(price, '  |  '.join(reasons)))
                    _time.sleep(5)
                    continue

                # ★ v12: 实时评估盘面强度 (锁仓判断)
                if fstate == STATE_IDLE:
                    self._assess_strength(price, now_ts)

                # ── 状态路由 ──
                if fstate == STATE_IDLE:
                    self._handle_idle(price)
                elif fstate == STATE_SPIKING:
                    self._handle_spiking(price)
                elif fstate == STATE_SOLD:
                    self._handle_sold(price)
                elif fstate == STATE_DIPPING:
                    self._handle_dipping(price)
                elif fstate == STATE_BT_DIPPING:
                    self._handle_bt_dipping(price)
                elif fstate == STATE_BT_BOUGHT:
                    self._handle_bt_bought(price)
                elif fstate == STATE_BT_SPIKING:
                    self._handle_bt_spiking(price)

                # ── 卖后计时 (反T) ──
                if self.st['fstate'] in (STATE_SOLD, STATE_DIPPING):
                    self.st['sell_elapsed_bars'] += 1

                # ── 尾盘强制平仓 (开关控制) ──
                if ENABLE_FORCE_CLOSE and now >= FORCE_CLOSE_TIME:
                    f = self.st['fstate']
                    if f in (STATE_SOLD, STATE_DIPPING):
                        _log(f'[尾盘] {now} 强制买回(反T)')
                        self._force_buyback()
                    elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
                        _log(f'[尾盘] {now} 强制卖出(正T)')
                        self._do_bt_force_sell()
                    elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE):
                        self.st['fstate'] = STATE_DONE

                # ── 反T止损检查 ──
                if fstate == STATE_SOLD and not self.st.get('stop_loss_hit', False):
                    loss_limit = self.st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
                    if self.st.get('day_pnl', 0) < -loss_limit:
                        _log(f'[止损-反T] 亏损超限')
                        self.st['stop_loss_hit'] = True
                        self._force_buyback()

                # ── 心跳日志 ──
                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    fs = self.st['fstate']
                    sig = self.st.get('daily_signal', {})

                    if fs == STATE_IDLE:
                        # ★ 正T动态触发线: 只跟涨不跟跌 (trailing-up only)
                        #   记录今日最高trail值, 价格回落时触发线停住不动
                        if self.st.get('do_long'):
                            bt_floor = sig.get('buy_trigger_floor', 0)
                            bt_trail = round(price * (1.0 - BUY_TRIGGER_TRAIL), 2)
                            max_trail = self.st.get('bt_max_trail', 0)
                            max_trail = max(max_trail, bt_trail)
                            self.st['bt_max_trail'] = max_trail
                            bt_dynamic = max(bt_floor, max_trail)
                            sig['buy_trigger'] = bt_dynamic
                            sig['buy_trigger_trail'] = bt_trail

                        # 显示两个方向的等待状态
                        parts = []
                        if self.st.get('do_short'):
                            st_trig = sig.get('sell_trigger', 0)
                            dist = st_trig - price
                            parts.append('反T: 需涨{:.2f}至Y{:.2f}触发卖出'.format(dist, st_trig))
                        else:
                            reason = sig.get('short_reason', sig.get('blocked_reason', '禁止'))
                            parts.append('反T: 禁止({})'.format(reason))

                        if self.st.get('do_long'):
                            bt_dyn = sig.get('buy_trigger', 0)
                            dist = price - bt_dyn
                            parts.append('正T: 需跌{:.2f}至Y{:.2f}触发买入'.format(dist, bt_dyn))
                        else:
                            reason = self.st.get('long_reason', '资金不足')
                            parts.append('正T: 禁止({})'.format(reason))

                        # ★ v12: 锁仓状态
                        if self.st.get('locked'):
                            parts.append('锁仓: {}'.format(self.st.get('lock_reason', '')))
                        _log('[心跳] {} | Y{:.2f} | {}'.format(fs, price, '  |  '.join(parts)))

                    elif fs == STATE_SPIKING:
                        peak = self.st.get('peak_price', 0)
                        pullback = (peak - price) / peak * 100 if peak > 0 else 0
                        _log('[心跳] {} | Y{:.2f} | peak=Y{:.2f} 回落{:.2f}%(需>={:.2f}%)'.format(
                            fs, price, peak, pullback, PULLBACK_PCT * 100))

                    elif fs in (STATE_SOLD, STATE_DIPPING):
                        sp = self.st.get('sell_fill_price', 0)
                        bt = self.st.get('buyback_target', 0)
                        if sp > 0:
                            chg = (price - sp) / sp * 100
                            _log('[心跳] {} | Y{:.2f} | 卖Y{:.2f} 现{}{:.2f}% | 买回线Y{:.2f}'.format(
                                fs, price, sp, '+' if chg >= 0 else '', chg, bt))

                    elif fs == STATE_BT_DIPPING:
                        dip = self.st.get('bt_dip_price', price)
                        bounce = (price - dip) / dip * 100 if dip > 0 else 0
                        _log('[心跳] {} | Y{:.2f} | dip=Y{:.2f} 回升{:.2f}%(需>={:.2f}%)'.format(
                            fs, price, dip, bounce, BOUNCE_PCT * 100))

                    elif fs == STATE_BT_BOUGHT:
                        bp = self.st.get('bt_buy_fill_price', 0)
                        target = self.st.get('bt_sellback_target', 0)
                        if bp > 0:
                            chg = (price - bp) / bp * 100
                            to_target = target - price
                            stop_price = bp * (1.0 - STOP_LOSS_PCT)
                            _log('[心跳] {} | Y{:.2f} | 买Y{:.2f} 现{}{:.2f}% | 卖出线Y{:.2f}(需涨{:.2f}) | 止损线Y{:.2f} | 尾盘{}强平'.format(
                                fs, price, bp, '+' if chg >= 0 else '', chg,
                                target, to_target, stop_price, FORCE_CLOSE_TIME))

                    elif fs == STATE_BT_SPIKING:
                        peak = self.st.get('bt_sell_peak_price', price)
                        pullback = (peak - price) / peak * 100 if peak > 0 else 0
                        _log('[心跳] {} | Y{:.2f} | peak=Y{:.2f} 回落{:.2f}%(需>={:.2f}%)'.format(
                            fs, price, peak, pullback, PULLBACK_PCT * 100))

                    elif fs in (STATE_DONE, STATE_FORCED):
                        _log('[心跳] {} | 今日交易已完成, 等待下一交易日'.format(fs))

                    else:
                        _log('[心跳] {} | Y{:.2f}'.format(fs, price))

                _time.sleep(1)

        except KeyboardInterrupt:
            _log('\n用户中断')
        except Exception as e:
            _log(f'[异常] {e}')
            _traceback.print_exc()
        finally:
            self.conn.disconnect()
            fstate = self.st.get('fstate', '')
            if fstate in (STATE_SOLD, STATE_DIPPING):
                _log('[警告] 策略停止时有未买回头寸! 请手动检查!')
            _log(f'{STOCK_NAME} v10 MiniQMT版 已停止 | 累计 {self.total_t_days}天 | 毛利~Y{self.total_pnl:,.0f}')


# ============================================================================
# 回测mode (内嵌, 与 backtest_v10_xtdata.py 等价)
# ============================================================================

def run_backtest_mode(start='20250801', end='20260806'):
    """使用 xtdata 本地数据进行回测 (不需要 MiniQMT)"""
    print(f'\n{"="*55}')
    print(f'  回测mode — QMT 迷你反T v10')
    print(f'  数据源: xtdata.get_local_data() -> QMT本地.DAT')
    print(f'  区间: {start} ~ {end}')
    print(f'{"="*55}\n')

    # 直接用回测模块
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from backtest.backtest_v10_xtdata import XTDataManager, BacktestEngine

    data_mgr = XTDataManager('601869.SH', data_dir='C:/QMT/datadir')
    data_mgr.load_daily(start=start, end=end)

    engine = BacktestEngine(data_mgr)
    engine.run(start_date=start, end_date=end)
    engine.print_report()
    engine.save_csv()


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT 迷你反T v10 策略 — VS Code 运行版',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python "MyPy-Q/QMT_迷你反T_v10_miniqmt_实盘.py" --mode signal    (信号监控, 推荐首次)
  python "MyPy-Q/QMT_迷你反T_v10_miniqmt_实盘.py" --mode live      (实盘自动交易)
  python "MyPy-Q/QMT_迷你反T_v10_miniqmt_实盘.py" --mode backtest  (回测)

前置条件:
  1. 启动 MiniQMT: QMT -> 右上角"极简mode"
  2. MiniQMT 中已登录资金账号 8890145315
  3. QMT 已下载 601869 历史数据
        """
    )
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live', 'backtest'],
                        help='运行mode (默认: signal)')
    parser.add_argument('--start', default='20250801',
                        help='回测开始日期 YYYYMMDD')
    parser.add_argument('--end', default='20260806',
                        help='回测结束日期 YYYYMMDD')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_backtest_mode(args.start, args.end)
        return

    dry_run = (args.mode == 'signal')

    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  ⚠ 即将启动实盘自动交易!')
        print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
        print(f'  账号: {ACCOUNT}')
        print('  请确认:')
        print('  1. MiniQMT 已启动 (极简mode)')
        print('  2. 资金账号已登录')
        print('  3. 当前持有底仓')
        print('!' * 55)
        confirm = input('\n确认启动? (输入 yes 继续): ')
        if confirm.strip().lower() != 'yes':
            print('已取消')
            return

    runner = StrategyRunner(dry_run=dry_run)
    runner.run()


if __name__ == '__main__':
    main()
