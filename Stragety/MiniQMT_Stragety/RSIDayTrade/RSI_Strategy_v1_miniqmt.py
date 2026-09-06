# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — RSI 买卖策略 v1
================================================================================
 基于 RSI(14) 相对强弱指标的日内交易策略。

 RSI数值       状态       含义
 > 70          超买       上涨动能强劲，价格可能短期过热，存在回调风险 → 卖出信号
 30 - 70       中性       市场处于正常波动区间 → 持有/观望
 < 30          超卖       下跌动能充分释放，价格可能短期超跌，存在反弹机会 → 买入信号

 交易逻辑:
   - RSI 从下方上穿 30 (脱离超卖): BUY  — 超卖反弹确认
   - RSI 从上方下穿 70 (脱离超买): SELL — 超买回调确认
   - 极致信号: RSI < 20 强烈超卖 / RSI > 80 强烈超买

 特点:
   ★ 实时打印 RSI 详细计算过程 (周期14, 逐日涨跌, 平均涨幅/跌幅)
   ★ 盘前打印完整交易计划 (仓位/资金/RSI区域/操作建议)
   ★ 每分钟心跳输出 RSI 数值 + 当前区域 + 距阈值距离

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v1_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/RSI_Strategy_v1_miniqmt.py" --mode live

================================================================================
"""
import os
import sys
import time as _time
import argparse
import traceback as _traceback
from datetime import datetime, timedelta
from typing import Optional

# ── 项目路径 ──
_STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
if _STRATEGY_DIR not in sys.path:
    sys.path.insert(0, _STRATEGY_DIR)

from Stragety.MiniQMT_Stragety.DayT.infra.logger import FileLogger, set_logger, get_logger, _log
from Stragety.MiniQMT_Stragety.DayT.infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares,
    set_global_conn,
)


# ============================================================================
# RSI 策略参数
# ============================================================================

ACCOUNT          = '8890145315'
STOCK_CODE       = '601869'
STOCK_NAME       = '长飞光纤'
STOCK_QMT        = f'{STOCK_CODE}.SH'
STRATEGY_NAME    = 'RSI策略_v1'

TRADE_LOT_SIZE   = 100          # 1手=100股
MINIQMT_PATH     = 'C:/QMT/userdata_mini'
SESSION_ID       = 0

# ── RSI 参数 ──
RSI_PERIOD       = 14           # RSI 计算周期
RSI_OVERBOUGHT   = 70           # 超买阈值
RSI_OVERSOLD     = 30           # 超卖阈值
RSI_EXTREME_HIGH = 80           # 极度超买
RSI_EXTREME_LOW  = 20           # 极度超卖

# ── 数据 & 费率 ──
HIST_DATA_LEN    = 80           # 历史日线长度 (至少 RSI_PERIOD*3)
COMMISSION       = 0.00025      # 佣金
STAMP_TAX        = 0.001        # 印花税 (仅卖出)

# ── 交易参数 ──
MAX_POSITION_LOTS  = 5          # 最大持仓手数
MIN_POSITION_LOTS  = 1          # 最小交易手数
MAX_DAILY_TRADES   = 2          # 每日最大交易次数
POSITION_PCT_BUY   = 0.30       # 单次买入使用资金比例
POSITION_PCT_SELL  = 0.30       # 单次卖出使用持仓比例
STOP_LOSS_PCT      = 0.03       # 止损比例 3%

# ── 连接参数 ──
FORCE_CLOSE_TIME   = '14:57:00'
ENABLE_FORCE_CLOSE = True


# ============================================================================
# RSI 计算 (独立实现，不依赖 core/indicators.py)
# ============================================================================

def compute_rsi(closes, period=14):
    """
    计算 RSI (Relative Strength Index) — Wilder's smoothing 方法。

    Args:
        closes: list[float] 收盘价序列
        period: int 计算周期 (默认14)

    Returns:
        list[float]: 与输入等长的 RSI 序列 (前 period 个值为 50.0 占位)

    公式:
        RS  = avg_gain / avg_loss
        RSI = 100 - 100 / (1 + RS)
    """
    n = len(closes)
    if n < period + 1:
        return [50.0] * n

    rsi_vals = [50.0] * n
    gains = []
    losses = []

    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0.0)
        losses.append(abs(diff) if diff < 0 else 0.0)

    # 初始 SMA
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi_vals[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_vals[period] = 100.0 - 100.0 / (1.0 + rs)

    # Wilder's smoothing
    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_vals[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_vals[i + 1] = 100.0 - 100.0 / (1.0 + rs)

    return rsi_vals


def compute_rsi_detailed(closes, period=14):
    """
    计算 RSI 并返回详细计算过程。

    Returns:
        dict: {
            'rsi': float,              # 当前 RSI 值
            'rsi_prev': float,         # 前一日 RSI
            'rsi_series': list[float], # 完整 RSI 序列
            'avg_gain': float,         # 平均涨幅
            'avg_loss': float,         # 平均跌幅
            'rs': float,               # RS 比值
            'daily_changes': list,     # 最近 period 天的逐日涨跌
            'zone': str,               # 当前区域
            'zone_cn': str,            # 当前区域中文
        }
    """
    n = len(closes)
    rsi_series = compute_rsi(closes, period)
    curr_rsi = rsi_series[-1]
    prev_rsi = rsi_series[-2] if n >= 2 else 50.0

    # 逐日涨跌
    daily_changes = []
    for i in range(max(1, n - period - 1), n):
        chg = closes[i] - closes[i - 1]
        chg_pct = (chg / closes[i - 1]) * 100 if closes[i - 1] > 0 else 0
        daily_changes.append({
            'day': i,
            'close': closes[i],
            'prev_close': closes[i - 1],
            'change': round(chg, 2),
            'change_pct': round(chg_pct, 2),
        })

    # 最近 period 天的 gains/losses
    recent_gains = []
    recent_losses = []
    for i in range(n - period, n):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            recent_gains.append(diff)
            recent_losses.append(0.0)
        else:
            recent_gains.append(0.0)
            recent_losses.append(abs(diff))

    avg_g = sum(recent_gains) / period
    avg_l = sum(recent_losses) / period
    rs = avg_g / avg_l if avg_l > 0 else float('inf')

    # 区域判断
    if curr_rsi > RSI_EXTREME_HIGH:
        zone = 'extreme_overbought'
        zone_cn = '🔴🔴 极度超买'
    elif curr_rsi > RSI_OVERBOUGHT:
        zone = 'overbought'
        zone_cn = '🔴 超买'
    elif curr_rsi < RSI_EXTREME_LOW:
        zone = 'extreme_oversold'
        zone_cn = '🟢🟢 极度超卖'
    elif curr_rsi < RSI_OVERSOLD:
        zone = 'oversold'
        zone_cn = '🟢 超卖'
    else:
        zone = 'neutral'
        zone_cn = '⚪ 中性'

    return {
        'rsi': round(curr_rsi, 2),
        'rsi_prev': round(prev_rsi, 2),
        'rsi_series': rsi_series,
        'avg_gain': round(avg_g, 2),
        'avg_loss': round(avg_l, 2),
        'rs': round(rs, 2) if rs != float('inf') else '∞',
        'daily_changes': daily_changes[-period:] if len(daily_changes) >= period else daily_changes,
        'zone': zone,
        'zone_cn': zone_cn,
    }


def compute_sma(values, period):
    """简单移动平均"""
    n = len(values)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


# ============================================================================
# 时间工具
# ============================================================================

def now_hms():
    return _time.strftime('%H:%M:%S')


def is_market_open(now_str=None):
    if now_str is None:
        now_str = now_hms()
    return ('09:30:00' <= now_str <= '11:30:00') or ('13:00:00' <= now_str <= '15:00:00')


def time_to_open(now_str):
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
# RSI Strategy Runner
# ============================================================================

class RSIStrategyRunner:
    """MiniQMT RSI 策略运行器"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='RSI_v1')
            set_logger(logger)

        self.conn = MiniQMTConnector()
        set_global_conn(self.conn, dry_run)

        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = dry_run

        self._last_heartbeat = 0.0
        self._running = True

        # 交易统计
        self.total_trades = 0
        self.total_pnl = 0.0
        self.win_count = 0
        self.loss_count = 0

        # RSI 状态
        self._rsi_detail = None
        self._prev_zone = None

    # ── 状态初始化 ──

    def _init_state(self):
        self.st.update({
            'trade_date': '',
            'initialized': False,
            'base_shares': 0,
            'base_can_use': 0,
            'base_cost': 0.0,
            'avail_cash': 0.0,
            'pos_value': 0.0,
            'pos_pct': 0.0,
            'total_asset': 0.0,
            # RSI 状态
            'rsi_val': 50.0,
            'rsi_zone': 'neutral',
            'rsi_zone_cn': '⚪ 中性',
            'rsi_prev': 50.0,
            # 交易状态
            'trade_count': 0,
            'holding_signal': None,     # 'buy' | 'sell' | None
            'signal_price': 0.0,
            'signal_time': '',
            # 风控
            'stop_loss_hit': False,
            'day_pnl': 0.0,
        })

    def _reset_daily(self):
        saved = {
            'base_shares': self.st.get('base_shares', 0),
            'base_can_use': self.st.get('base_can_use', 0),
            'base_cost': self.st.get('base_cost', 0.0),
        }
        self._init_state()
        self.st.update(saved)

    # ── 持仓刷新 ──

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                break

    # ── 每日初始化 ──

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if self.st.get('trade_date', '') == today and self.st.get('initialized', False):
            self._refresh_position()
            return

        is_new_day = self.st.get('trade_date', '') and self.st['trade_date'] != today
        if is_new_day:
            _log(f'\n[新交易日] {self.st["trade_date"]} -> {today}')

        self._reset_daily()
        self.st['trade_date'] = today

        # 获取历史日线
        hist_close = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'close')
        hist_open  = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'open')
        hist_high  = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'high')
        hist_low   = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'low')

        if STOCK_QMT not in hist_close or len(hist_close[STOCK_QMT]) < RSI_PERIOD + 1:
            _log(f'[警告] 日线数据不足 (需要至少{RSI_PERIOD+1}天), 跳过今日')
            return

        closes = list(hist_close[STOCK_QMT])
        opens  = list(hist_open[STOCK_QMT])
        highs  = list(hist_high[STOCK_QMT])
        lows   = list(hist_low[STOCK_QMT])

        # 用 tick 修正今日开盘价
        tick = self.ctx.get_full_tick([STOCK_QMT])
        tick_data = tick.get(STOCK_QMT, {})
        today_open = tick_data.get('open', 0)
        if today_open > 0 and len(opens) > 0:
            opens[-1] = today_open

        # ── 计算 RSI ──
        self._rsi_detail = compute_rsi_detailed(closes, RSI_PERIOD)
        curr_rsi = self._rsi_detail['rsi']
        rsi_zone = self._rsi_detail['zone']
        rsi_zone_cn = self._rsi_detail['zone_cn']

        # 前一日收盘
        close_yday = closes[-2] if len(closes) >= 2 else closes[-1]
        close_today_est = closes[-1]

        # 计算 MA
        ma5  = compute_sma(closes, 5)[-1]
        ma20 = compute_sma(closes, 20)[-1]

        # ── 刷新持仓 ──
        self._refresh_position()
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('base_cost', 0) == 0.0:
            self.st['base_cost'] = close_today_est

        # ── 资金 ──
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0

        curr_price = tick_data.get('lastPrice', close_today_est)
        if curr_price <= 0:
            curr_price = close_today_est

        pos_value = base_shares * curr_price
        total_asset = pos_value + avail_cash
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0

        # ── 保存状态 ──
        self.st.update({
            'initialized': True,
            'base_shares': base_shares,
            'base_can_use': base_can_use,
            'avail_cash': avail_cash,
            'pos_value': pos_value,
            'pos_pct': pos_pct,
            'total_asset': total_asset,
            'rsi_val': curr_rsi,
            'rsi_zone': rsi_zone,
            'rsi_zone_cn': rsi_zone_cn,
            'rsi_prev': self._rsi_detail['rsi_prev'],
            'trade_count': 0,
            'day_pnl': 0.0,
        })

        self._prev_zone = rsi_zone

        # ── 打印详细 RSI 计算 & 交易计划 ──
        self._print_rsi_calculation(closes, opens, highs, lows)
        self._print_trading_plan(curr_price)

    # ═══════════════════════════════════════════════════════════════
    # RSI 详细计算输出
    # ═══════════════════════════════════════════════════════════════

    def _print_rsi_calculation(self, closes, opens, highs, lows):
        """打印 RSI(14) 详细计算过程"""
        d = self._rsi_detail

        _log('')
        _log('╔' + '═' * 62 + '╗')
        _log('║  📊 RSI(14) 详细计算 — {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', '')).ljust(51) + '║')
        _log('╠' + '═' * 62 + '╣')

        # 最近14天逐日涨跌
        _log('║  最近{:d}天逐日涨跌:'.format(RSI_PERIOD).ljust(55) + '║')
        _log('║  {:>4s}  {:>10s}  {:>10s}  {:>12s}  ║'.format('日期', '收盘价', '涨跌额', '涨跌幅%'))
        _log('║  ' + '─' * 52 + '  ║')

        changes = d['daily_changes']
        for i, ch in enumerate(changes):
            n_day = len(changes) - i
            arrow = '📈' if ch['change'] > 0 else ('📉' if ch['change'] < 0 else '➡')
            _log('║  T-{:d}  Y{:>10.2f}  {:>+10.2f}  {:>+11.2f}%  {} ║'.format(
                n_day - 1, ch['close'], ch['change'], ch['change_pct'], arrow))

        _log('╠' + '═' * 62 + '╣')
        _log('║  平均涨幅 (Avg Gain):  Y{:.2f}'.format(d['avg_gain']).ljust(55) + '║')
        _log('║  平均跌幅 (Avg Loss):  Y{:.2f}'.format(d['avg_loss']).ljust(55) + '║')
        _log('║  RS = AvgGain / AvgLoss = {:.2f}'.format(
            d['rs'] if isinstance(d['rs'], float) else 999.99).ljust(55) + '║')
        _log('║  RSI = 100 - 100/(1+RS) = **{:.2f}**'.format(d['rsi']).ljust(55) + '║')
        _log('╠' + '═' * 62 + '╣')

        # RSI 区域判断
        zone_bar = self._rsi_zone_bar(d['rsi'])
        _log('║  RSI 当前位置:'.ljust(55) + '║')
        _log('║  {} ║'.format(zone_bar))
        _log('║  {:>6.1f}  ← 当前 RSI({})  |  区域: {} ║'.format(
            d['rsi'], RSI_PERIOD, d['zone_cn']))

        # 距阈值距离
        dist_to_ob = RSI_OVERBOUGHT - d['rsi']
        dist_to_os = d['rsi'] - RSI_OVERSOLD
        _log('║  距超买线(70): {:+.1f}点   |  距超卖线(30): {:+.1f}点 ║'.format(dist_to_ob, dist_to_os))

        # 前日对比
        rsi_delta = d['rsi'] - d['rsi_prev']
        delta_arrow = '↑' if rsi_delta > 0 else ('↓' if rsi_delta < 0 else '→')
        _log('║  前日 RSI: {:.2f}  →  今日: {:.2f}  (Δ{:+.2f} {}    ) ║'.format(
            d['rsi_prev'], d['rsi'], rsi_delta, delta_arrow))

        # 交叉信号检测
        cross_signal = self._detect_cross_signal(d['rsi_prev'], d['rsi'])
        if cross_signal:
            _log('╠' + '═' * 62 + '╣')
            _log('║  ⚡ RSI 交叉信号: {} ║'.format(cross_signal))

        _log('╚' + '═' * 62 + '╝')
        _log('')

    def _rsi_zone_bar(self, rsi_val):
        """生成 RSI 可视化刻度条"""
        bar_len = 50
        pos = int(rsi_val / 100 * bar_len)
        pos = max(0, min(bar_len, pos))

        # 30 和 70 标记位置
        os_mark = int(30 / 100 * bar_len)
        ob_mark = int(70 / 100 * bar_len)

        chars = []
        for i in range(bar_len + 1):
            if i == pos:
                chars.append('▼')
            elif i == os_mark or i == ob_mark:
                chars.append('┃')
            elif i < 30 / 100 * bar_len:
                chars.append('░')  # 超卖区
            elif i < 70 / 100 * bar_len:
                chars.append('─')  # 中性区
            else:
                chars.append('▓')  # 超买区

        return '0 ' + ''.join(chars) + ' 100'

    def _detect_cross_signal(self, rsi_prev, rsi_curr):
        """检测 RSI 交叉信号"""
        signals = []

        # 上穿30: 脱离超卖 → 买入信号
        if rsi_prev < RSI_OVERSOLD and rsi_curr >= RSI_OVERSOLD:
            signals.append('🟢 上穿30 → 脱离超卖, **买入信号**')
        # 下穿70: 脱离超买 → 卖出信号
        if rsi_prev > RSI_OVERBOUGHT and rsi_curr <= RSI_OVERBOUGHT:
            signals.append('🔴 下穿70 → 脱离超买, **卖出信号**')
        # 上穿70: 进入超买
        if rsi_prev < RSI_OVERBOUGHT and rsi_curr >= RSI_OVERBOUGHT:
            signals.append('⚠ 上穿70 → 进入超买区, 注意回调风险')
        # 下穿30: 进入超卖
        if rsi_prev > RSI_OVERSOLD and rsi_curr <= RSI_OVERSOLD:
            signals.append('⚠ 下穿30 → 进入超卖区, 注意反弹机会')

        return ' | '.join(signals) if signals else ''

    # ═══════════════════════════════════════════════════════════════
    # 交易计划输出
    # ═══════════════════════════════════════════════════════════════

    def _print_trading_plan(self, curr_price):
        """打印当日交易计划"""
        d = self._rsi_detail
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        avail_cash = self.st.get('avail_cash', 0)
        pos_pct = self.st.get('pos_pct', 0)
        total_asset = self.st.get('total_asset', 0)
        rsi_val = d['rsi']
        rsi_zone = d['zone']

        _log('┌' + '─' * 62 + '┐')
        _log('│  🎯 RSI 交易计划 — {}  {}  RSI={:.1f}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', ''), rsi_val, d['zone_cn']).ljust(55) + '│')
        _log('├' + '─' * 62 + '┤')

        # 持仓信息
        _log('│  持仓: {:>5}股  Y{:>10,.0f}  ({:.0f}%)  总资产: Y{:>12,.0f}'.format(
            base_shares, base_shares * curr_price, pos_pct, total_asset).ljust(56) + '│')
        _log('│  可用资金: Y{:>12,.0f}    T+0可卖: {:>3}股 ({}手)'.format(
            avail_cash, base_can_use, base_can_use // TRADE_LOT_SIZE).ljust(56) + '│')
        _log('├' + '─' * 62 + '┤')

        # RSI 区域分析
        if rsi_zone in ('oversold', 'extreme_oversold'):
            _log('│  📈 **超卖区域** — 下跌动能充分释放, 存在反弹机会'.ljust(55) + '│')

            # 买入可行性
            buy_lots_cash = int(avail_cash * POSITION_PCT_BUY / (curr_price * TRADE_LOT_SIZE * 1.01))
            buy_lots = min(buy_lots_cash, MAX_POSITION_LOTS)
            if buy_lots >= MIN_POSITION_LOTS:
                buy_amount = buy_lots * TRADE_LOT_SIZE * curr_price
                _log('│  ├─ ✅ 买入可行: {}手 × Y{:.2f} ≈ Y{:,.0f}'.format(
                    buy_lots, curr_price, buy_amount).ljust(55) + '│')
                _log('│  ├─ 建议买入价: Y{:.2f} (当前价)'.format(curr_price).ljust(55) + '│')
                target_price = round(curr_price * 1.03, 2)
                _log('│  ├─ 目标卖出价: Y{:.2f} (+3% 止盈)'.format(target_price).ljust(55) + '│')
                stop_price = round(curr_price * (1 - STOP_LOSS_PCT), 2)
                _log('│  └─ 止损价: Y{:.2f} (-{:.0f}%)'.format(
                    stop_price, STOP_LOSS_PCT * 100).ljust(55) + '│')
            else:
                _log('│  ├─ ❌ 资金不足: 需Y{:,.0f}/手, 可用Y{:,.0f}'.format(
                    curr_price * TRADE_LOT_SIZE, avail_cash).ljust(55) + '│')

        elif rsi_zone in ('overbought', 'extreme_overbought'):
            _log('│  📉 **超买区域** — 上涨动能过强, 存在回调风险'.ljust(55) + '│')

            # 卖出可行性
            sell_lots = min(base_can_use // TRADE_LOT_SIZE, MAX_POSITION_LOTS)
            if sell_lots >= MIN_POSITION_LOTS:
                sell_lots_actual = min(sell_lots, int(base_shares * POSITION_PCT_SELL / TRADE_LOT_SIZE))
                sell_lots_actual = max(MIN_POSITION_LOTS, sell_lots_actual)
                _log('│  ├─ ✅ 卖出可行: {}手 × Y{:.2f} ≈ Y{:,.0f}'.format(
                    sell_lots_actual, curr_price,
                    sell_lots_actual * TRADE_LOT_SIZE * curr_price).ljust(55) + '│')
                _log('│  ├─ 建议卖出价: Y{:.2f} (当前价)'.format(curr_price).ljust(55) + '│')
                buyback_price = round(curr_price * 0.97, 2)
                _log('│  ├─ 目标买回价: Y{:.2f} (-3% 止盈)'.format(buyback_price).ljust(55) + '│')
                emergency_price = round(curr_price * 1.03, 2)
                _log('│  └─ 紧急买回价: Y{:.2f} (+3% 止损)'.format(emergency_price).ljust(55) + '│')
            else:
                _log('│  ├─ ❌ 无可用持仓: T+0可卖{}股'.format(base_can_use).ljust(55) + '│')

        else:
            _log('│  ⚪ **中性区域** — 市场处于正常波动区间, 建议观望'.ljust(55) + '│')
            _log('│  ├─ RSI方向: {} (Δ{:+.1f} vs 前日)'.format(
                '向上' if rsi_val > d['rsi_prev'] else '向下',
                rsi_val - d['rsi_prev']).ljust(55) + '│')
            if rsi_val > 55:
                _log('│  ├─ RSI偏强, 接近超买区 (距70还差{:.1f}点)'.format(
                    RSI_OVERBOUGHT - rsi_val).ljust(55) + '│')
            elif rsi_val < 45:
                _log('│  ├─ RSI偏弱, 接近超卖区 (距30还差{:.1f}点)'.format(
                    rsi_val - RSI_OVERSOLD).ljust(55) + '│')
            _log('│  └─ 建议: 等待RSI进入超买/超卖区域再操作'.ljust(55) + '│')

        _log('├' + '─' * 62 + '┤')
        _log('│  📋 策略参数: RSI周期={} | 超买线={} | 超卖线={} | 日最大{}笔'.format(
            RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD, MAX_DAILY_TRADES).ljust(55) + '│')

        if self.total_trades > 0:
            wr = self.win_count / self.total_trades * 100 if self.total_trades > 0 else 0
            _log('│  📈 历史: {}笔 | 胜率{:.0f}% | 累计PnL ~Y{:,.0f}'.format(
                self.total_trades, wr, self.total_pnl).ljust(55) + '│')

        _log('└' + '─' * 62 + '┘')
        _log('')

    # ═══════════════════════════════════════════════════════════════
    # 交易执行
    # ═══════════════════════════════════════════════════════════════

    def _snapshot_account(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        shares = 0; can_use = 0; cost = 0.0
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                shares = pos.m_nVolume
                can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                cost = pos.m_dOpenPrice
                break
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        cash = account[0].m_dAvailable if account else 0.0
        tick = self.ctx.get_full_tick([STOCK_QMT])
        price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        return {
            'shares': shares, 'can_use': can_use, 'cash': cash,
            'cost': cost, 'total_asset': shares * price + cash, 'price': price,
        }

    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_can_use = snap_after['can_use'] - snap_before['can_use']
        d_cash = snap_after['cash'] - snap_before['cash']

        if abs(trade_shares) == TRADE_LOT_SIZE:
            if d_shares == trade_shares:
                status = '✅ 已成交'
            elif d_shares == 0:
                status = '⏳ 待成交'
            else:
                status = f'⚠ 部分成交(Δ{d_shares:+d}股)'
        else:
            status = '📝 已下单'

        _log('  ┌─ [RSI交易校验] {} ─'.format(label))
        _log('  │  下单: {}股 @ Y{:.2f}  |  {}'.format(
            '{:+d}'.format(trade_shares), trade_price, status))
        _log('  ├─ 持仓: {:>5}股 → {:>5}股  (Δ{:+d}股)'.format(
            snap_before['shares'], snap_after['shares'], d_shares))
        _log('  ├─ 资金: Y{:>12,.2f} → Y{:>12,.2f}  (Δ{:+,.2f})'.format(
            snap_before['cash'], snap_after['cash'], d_cash))
        _log('  └' + '─' * 45)

        self.st['base_shares'] = snap_after['shares']
        self.st['base_can_use'] = snap_after['can_use']
        self.st['base_cost'] = snap_after['cost']

    def _execute_buy(self, price):
        """执行买入 (RSI超卖反弹)"""
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES:
            _log(f'[RSI买入] 今日已达{MAX_DAILY_TRADES}笔上限, 跳过')
            return

        avail_cash = self.st.get('avail_cash', 0)
        need = price * TRADE_LOT_SIZE * 1.01
        if avail_cash < need:
            _log(f'[RSI买入] 资金不足 (需Y{need:,.0f} > Y{avail_cash:,.0f})')
            return

        self.st['trade_count'] = tc + 1
        _log(f'[RSI买入 #{tc+1}/{MAX_DAILY_TRADES}] RSI超卖反弹信号 @ Y{price:.2f}')

        snap = self._snapshot_account()
        if self.dry_run:
            _log(f'  [模拟] 买入 {STOCK_QMT} Y{price:.2f} x {TRADE_LOT_SIZE}股')
            self._verify_trade(snap, 'RSI买入(模拟)', price, TRADE_LOT_SIZE)
            self.total_trades += 1
            return

        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
        _time.sleep(0.5)
        self._verify_trade(snap, 'RSI买入', price, TRADE_LOT_SIZE)
        self.total_trades += 1

    def _execute_sell(self, price):
        """执行卖出 (RSI超买回调)"""
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES:
            _log(f'[RSI卖出] 今日已达{MAX_DAILY_TRADES}笔上限, 跳过')
            return

        can_use = self.st.get('base_can_use', 0)
        if can_use < TRADE_LOT_SIZE:
            _log(f'[RSI卖出] 无可用持仓 (T+0可卖{can_use}股)')
            return

        self.st['trade_count'] = tc + 1
        _log(f'[RSI卖出 #{tc+1}/{MAX_DAILY_TRADES}] RSI超买回调信号 @ Y{price:.2f}')

        snap = self._snapshot_account()
        if self.dry_run:
            _log(f'  [模拟] 卖出 {STOCK_QMT} Y{price:.2f} x {TRADE_LOT_SIZE}股')
            self._verify_trade(snap, 'RSI卖出(模拟)', price, -TRADE_LOT_SIZE)
            self.total_trades += 1
            return

        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
        _time.sleep(0.5)
        self._verify_trade(snap, 'RSI卖出', price, -TRADE_LOT_SIZE)
        self.total_trades += 1

    # ═══════════════════════════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════════════════════════

    def run(self):
        set_global_conn(self.conn, self.dry_run)

        if not self.dry_run:
            if not self.conn.connect_data():
                _log('[错误] 无法连接行情服务, 退出')
                return
            if not self.conn.connect_trade():
                _log('[错误] 无法连接交易服务, 退出')
                self.conn.disconnect()
                return
        else:
            if not self.conn.connect_data():
                _log('[错误] 无法连接行情服务, 退出')
                return
            _log('[信号mode] 已连接行情, 不下单')

        self._init_state()
        _log(f'{STOCK_NAME} RSI策略 v1 MiniQMT版 启动')
        _log(f'  mode: {"信号监控(不下单)" if self.dry_run else "实盘交易"}')
        _log(f'  标的: {STOCK_NAME}({STOCK_QMT})')
        _log(f'  RSI周期: {RSI_PERIOD} | 超买线: {RSI_OVERBOUGHT} | 超卖线: {RSI_OVERSOLD}')
        _log(f'  ★ 日志: {get_logger().log_path if get_logger() else "(未初始化)"}')

        # 初始化
        try:
            self._daily_init()
        except Exception as e:
            _log(f'[异常] 初始化失败: {e}')
            _traceback.print_exc()

        _log('开始监控... (Ctrl+C 停止)')
        _log('')

        try:
            while self._running:
                now = now_hms()
                now_ts = _time.time()

                # ── 非交易时段 ──
                if not is_market_open(now):
                    today = datetime.now().strftime('%Y%m%d')

                    # 盘前 9:25-9:30 预初始化
                    if '09:25:00' <= now < '09:30:00':
                        if self.st.get('trade_date', '') != today:
                            _log(f'\n[盘前预初始化] {now} 集合竞价结束, 计算RSI信号...')
                            try:
                                self._daily_init()
                                self._last_heartbeat = now_ts
                            except Exception as e:
                                _log(f'[盘前异常] {e}')
                            _time.sleep(5)
                            continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                            _log(f'[盘前就绪] {now} — 距开盘 {time_to_open(now)}')
                        _time.sleep(5)
                        continue

                    # 新交易日初始化
                    if self.st.get('trade_date', '') != today:
                        _log(f'[盘前] 新交易日 {today}, 初始化...')
                        try:
                            self._daily_init()
                        except Exception as e:
                            _log(f'[异常] {e}')

                    # 非交易时段心跳
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        rsi = self.st.get('rsi_val', 50)
                        zone_cn = self.st.get('rsi_zone_cn', '⚪ 中性')
                        if now < '09:30:00':
                            _log(f'[等待开盘] {now} | RSI={rsi:.1f} {zone_cn} | 距开盘 {time_to_open(now)}')
                        elif now > '15:00:00':
                            _log(f'[已收盘] {now} | RSI={rsi:.1f} {zone_cn}')
                        elif '11:30:00' < now < '13:00:00':
                            _log(f'[午休] {now} | RSI={rsi:.1f} {zone_cn}')

                    _time.sleep(10)
                    continue

                # ═══════════════════════════════════════════════════
                # 交易时段
                # ═══════════════════════════════════════════════════

                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick:
                    _time.sleep(1)
                    continue

                price = tick[STOCK_QMT].get('lastPrice', 0)
                if price <= 0:
                    _time.sleep(1)
                    continue

                rsi_val = self.st.get('rsi_val', 50)
                rsi_zone = self.st.get('rsi_zone', 'neutral')

                # ── RSI 信号检测 ──
                trade_count = self.st.get('trade_count', 0)
                if trade_count < MAX_DAILY_TRADES:
                    # 超卖 → 买入信号
                    if rsi_zone in ('oversold', 'extreme_oversold'):
                        # 检查是否已经基于此信号买入
                        if self.st.get('holding_signal') != 'buy':
                            _log(f'[RSI信号] 🟢 超卖买入信号! RSI={rsi_val:.1f} < {RSI_OVERSOLD}')
                            _log(f'  当前价: Y{price:.2f} | 建议买入')
                            self.st['holding_signal'] = 'buy'
                            self.st['signal_price'] = price
                            self.st['signal_time'] = now
                            self._execute_buy(price)

                    # 超买 → 卖出信号
                    elif rsi_zone in ('overbought', 'extreme_overbought'):
                        if self.st.get('holding_signal') != 'sell':
                            _log(f'[RSI信号] 🔴 超买卖出信号! RSI={rsi_val:.1f} > {RSI_OVERBOUGHT}')
                            _log(f'  当前价: Y{price:.2f} | 建议卖出')
                            self.st['holding_signal'] = 'sell'
                            self.st['signal_price'] = price
                            self.st['signal_time'] = now
                            self._execute_sell(price)

                    # 脱离超卖/超买 → 重置信号
                    elif rsi_zone == 'neutral':
                        if self.st.get('holding_signal') is not None:
                            prev_sig = self.st['holding_signal']
                            _log(f'[RSI信号] ⚪ 回到中性区, 重置{prev_sig}信号')
                            self.st['holding_signal'] = None
                            self.st['signal_price'] = 0.0

                # ── 心跳日志 (每分钟) ──
                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    self._heartbeat(price)

                _time.sleep(2)

        except KeyboardInterrupt:
            _log('\n用户中断')
        except Exception as e:
            _log(f'[异常] {e}')
            _traceback.print_exc()
        finally:
            self.conn.disconnect()
            _log(f'{STOCK_NAME} RSI策略 v1 已停止 | 累计 {self.total_trades}笔 | PnL ~Y{self.total_pnl:,.0f}')
            logger = get_logger()
            if logger is not None:
                _log('★ 日志已保存至: ' + logger.log_path)
                logger.close()

    def _heartbeat(self, price):
        """每分钟心跳 — RSI 状态 + 距阈值距离"""
        rsi = self.st.get('rsi_val', 50)
        zone_cn = self.st.get('rsi_zone_cn', '⚪ 中性')
        zone = self.st.get('rsi_zone', 'neutral')
        trade_count = self.st.get('trade_count', 0)

        # 距阈值
        dist_ob = RSI_OVERBOUGHT - rsi
        dist_os = rsi - RSI_OVERSOLD

        parts = [
            f'RSI={rsi:.1f}',
            f'{zone_cn}',
        ]

        if zone == 'overbought' or zone == 'extreme_overbought':
            parts.append(f'距超买线{dist_ob:+.1f}')
        elif zone == 'oversold' or zone == 'extreme_oversold':
            parts.append(f'距超卖线{dist_os:+.1f}')
        else:
            parts.append(f'距超买{dist_ob:+.1f}/距超卖{dist_os:+.1f}')

        parts.append(f'Y{price:.2f}')
        parts.append(f'交易{trade_count}/{MAX_DAILY_TRADES}')

        sig = self.st.get('holding_signal')
        if sig:
            sig_price = self.st.get('signal_price', 0)
            if sig == 'buy':
                pnl_pct = (price - sig_price) / sig_price * 100 if sig_price > 0 else 0
                parts.append(f'持仓浮盈{pnl_pct:+.2f}%')
            elif sig == 'sell':
                pnl_pct = (sig_price - price) / sig_price * 100 if sig_price > 0 else 0
                parts.append(f'卖后浮盈{pnl_pct:+.2f}%')

        _log('[RSI心跳] {}'.format(' | '.join(parts)))


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT RSI 买卖策略 v1 — 基于 RSI(14) 指标',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v1_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v1_miniqmt.py" --mode live

RSI 策略说明:
  RSI > 70  超买 → 卖出信号 (价格短期过热, 存在回调风险)
  RSI 30-70 中性 → 持有/观望
  RSI < 30  超卖 → 买入信号 (价格短期超跌, 存在反弹机会)

前置条件:
  1. 启动 MiniQMT: QMT -> 右上角"极简mode"
  2. MiniQMT 中已登录资金账号 8890145315
  3. QMT 已下载 601869 历史数据
        """
    )
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live'])
    args = parser.parse_args()

    logger = FileLogger(STOCK_CODE, version='RSI_v1')
    set_logger(logger)
    print(f'★ 日志文件: {logger.log_path}')

    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  ⚠ 即将启动实盘自动交易!')
        print(f'  策略: RSI 买卖策略 v1')
        print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
        print(f'  账号: {ACCOUNT}')
        print(f'  RSI周期: {RSI_PERIOD} | 超买线: {RSI_OVERBOUGHT} | 超卖线: {RSI_OVERSOLD}')
        print('  请确认:')
        print('  1. MiniQMT 已启动 (极简mode)')
        print('  2. 资金账号已登录')
        print('  3. 了解RSI策略风险')
        print('!' * 55)
        confirm = input('\n确认启动? (输入 yes 继续): ')
        if confirm.strip().lower() != 'yes':
            print('已取消')
            logger.close()
            return

    runner = RSIStrategyRunner(dry_run=dry_run)
    runner.run()


if __name__ == '__main__':
    main()
