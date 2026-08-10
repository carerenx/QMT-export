# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — RSI 买卖策略 v2 (下跌反弹买入 + 上涨回落卖出)
================================================================================
 基于 RSI(14) 相对强弱指标的日内交易策略。

 RSI数值       状态       含义
 > 70          超买       上涨动能强劲，价格可能短期过热，存在回调风险 → 卖出信号
 30 - 70       中性       市场处于正常波动区间 → 持有/观望
 < 30          超卖       下跌动能充分释放，价格可能短期超跌，存在反弹机会 → 买入信号

 ★ v2 新增: 价格行为确认机制 (下跌反弹买入 + 上涨回落卖出)
   RSI 判断方向，价格行为确认时机 — 不再盲目在RSI进入区域时立即下单:

   ┌─ 买入路径 (下跌→反弹):
   │   RSI < 30 (超卖)                    → 激活买入监控 (MONITOR_DIP)
   │   → 价格持续下跌, 跟踪最低点(dip)      → 等待反弹
   │   → 价格从最低点回升 ≥ BOUNCE_PCT     → ✅ 确认反弹, 执行买入
   │   → 价格跌破止损线                    → ❌ 止损放弃
   │
   └─ 卖出路径 (上涨→回落):
       RSI > 70 (超买)                    → 激活卖出监控 (MONITOR_SPIKE)
       → 价格持续上涨, 跟踪最高点(peak)     → 等待回落
       → 价格从最高点回落 ≥ PULLBACK_PCT   → ✅ 确认回落, 执行卖出
       → 价格继续冲高                      → 更新peak, 继续等待

 特点:
   ★ RSI 详细计算过程 + 可视化刻度条
   ★ 盘前完整交易计划 (含触发价格 + 反弹/回落确认线)
   ★ 状态机实时监控 (当前阶段 + peak/dip跟踪 + 距触发线距离)
   ★ 每分钟心跳输出完整状态

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v2_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/RSI_Strategy_v2_miniqmt.py" --mode live

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

from infra.logger import FileLogger, set_logger, get_logger, _log
from infra.connector import (
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
STRATEGY_NAME    = 'RSI策略_v2'

TRADE_LOT_SIZE   = 100          # 1手=100股
MINIQMT_PATH     = 'C:/QMT/userdata_mini'
SESSION_ID       = 0

# ── RSI 参数 ──
RSI_PERIOD       = 14           # RSI 计算周期
RSI_OVERBOUGHT   = 70           # 超买阈值
RSI_OVERSOLD     = 30           # 超卖阈值
RSI_EXTREME_HIGH = 80           # 极度超买
RSI_EXTREME_LOW  = 20           # 极度超卖

# ── ★ v2 新增: 价格行为确认参数 ──
BOUNCE_PCT        = 0.0010      # 反弹确认阈值 (0.10%): 从最低点反弹超过此比例确认买入
PULLBACK_PCT      = 0.0010      # 回落确认阈值 (0.10%): 从最高点回落超过此比例确认卖出
DIP_TRIGGER_PCT   = 0.015       # 下跌触发跌幅 (1.5%): 从开盘价跌超此比例激活dip跟踪
SPIKE_TRIGGER_PCT = 0.015       # 上涨触发涨幅 (1.5%): 从开盘价涨超此比例激活spike跟踪
EMERGENCY_BUYBACK_PCT = 0.03    # 紧急买回: 卖后涨超3%强制买回
STOP_LOSS_PCT      = 0.03       # 止损比例 3%

# ── 数据 & 费率 ──
HIST_DATA_LEN    = 80
COMMISSION       = 0.00025
STAMP_TAX        = 0.001

# ── 交易参数 ──
MAX_POSITION_LOTS  = 5
MIN_POSITION_LOTS  = 1
MAX_DAILY_TRADES   = 2
POSITION_PCT_BUY   = 0.30
POSITION_PCT_SELL  = 0.30

# ── 状态机常量 ──
STATE_IDLE         = 'IDLE'
STATE_MONITOR_DIP  = 'MONITOR_DIP'     # 超卖 → 监控下跌反弹
STATE_MONITOR_SPIKE = 'MONITOR_SPIKE'  # 超买 → 监控上涨回落
STATE_DONE         = 'DONE'
STATE_FORCED       = 'FORCED'

# ── 尾盘 ──
FORCE_CLOSE_TIME   = '14:57:00'
ENABLE_FORCE_CLOSE = True


# ============================================================================
# RSI 计算 (独立实现)
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

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi_vals[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_vals[period] = 100.0 - 100.0 / (1.0 + rs)

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
    """计算 RSI 并返回详细计算过程。"""
    n = len(closes)
    rsi_series = compute_rsi(closes, period)
    curr_rsi = rsi_series[-1]
    prev_rsi = rsi_series[-2] if n >= 2 else 50.0

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
# RSI Strategy Runner v2
# ============================================================================

class RSIStrategyRunner:
    """
    MiniQMT RSI 策略运行器 v2

    ★ v2 新增: 价格行为确认状态机
      - 超卖 + 下跌反弹 → 买入 (不再直接下单)
      - 超买 + 上涨回落 → 卖出 (不再直接下单)
    """

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='RSI_v2')
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
        self._daily_open = 0.0

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
            'daily_open': 0.0,
            # RSI
            'rsi_val': 50.0,
            'rsi_zone': 'neutral',
            'rsi_zone_cn': '⚪ 中性',
            'rsi_prev': 50.0,
            # ★ v2 状态机
            'fstate': STATE_IDLE,
            'peak_price': 0.0,       # 跟踪最高点 (卖出用)
            'dip_price': 0.0,        # 跟踪最低点 (买入用)
            'trigger_price': 0.0,    # 触发价格
            'fill_price': 0.0,       # 成交价格
            'state_entered_at': '',  # 进入状态时间
            'state_bars': 0,         # 状态持续tick数
            # 交易
            'trade_count': 0,
            'day_pnl': 0.0,
            'stop_loss_hit': False,
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

        # 用 tick 修正今日数据
        tick = self.ctx.get_full_tick([STOCK_QMT])
        tick_data = tick.get(STOCK_QMT, {})
        today_open = tick_data.get('open', 0)
        if today_open > 0 and len(opens) > 0:
            opens[-1] = today_open
        self._daily_open = today_open if today_open > 0 else opens[-1]

        # ── 计算 RSI ──
        self._rsi_detail = compute_rsi_detailed(closes, RSI_PERIOD)
        curr_rsi = self._rsi_detail['rsi']
        rsi_zone = self._rsi_detail['zone']
        rsi_zone_cn = self._rsi_detail['zone_cn']

        close_yday = closes[-2] if len(closes) >= 2 else closes[-1]
        close_today_est = closes[-1]

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
            'daily_open': self._daily_open,
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
            'fstate': STATE_IDLE,
            'trade_count': 0,
            'day_pnl': 0.0,
        })

        self._prev_zone = rsi_zone

        # ── 打印 ──
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

        zone_bar = self._rsi_zone_bar(d['rsi'])
        _log('║  RSI 当前位置:'.ljust(55) + '║')
        _log('║  {} ║'.format(zone_bar))
        _log('║  {:>6.1f}  ← 当前 RSI({})  |  区域: {} ║'.format(
            d['rsi'], RSI_PERIOD, d['zone_cn']))

        dist_to_ob = RSI_OVERBOUGHT - d['rsi']
        dist_to_os = d['rsi'] - RSI_OVERSOLD
        _log('║  距超买线(70): {:+.1f}点   |  距超卖线(30): {:+.1f}点 ║'.format(dist_to_ob, dist_to_os))

        rsi_delta = d['rsi'] - d['rsi_prev']
        delta_arrow = '↑' if rsi_delta > 0 else ('↓' if rsi_delta < 0 else '→')
        _log('║  前日 RSI: {:.2f}  →  今日: {:.2f}  (Δ{:+.2f} {}    ) ║'.format(
            d['rsi_prev'], d['rsi'], rsi_delta, delta_arrow))

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
        os_mark = int(30 / 100 * bar_len)
        ob_mark = int(70 / 100 * bar_len)
        chars = []
        for i in range(bar_len + 1):
            if i == pos:
                chars.append('▼')
            elif i == os_mark or i == ob_mark:
                chars.append('┃')
            elif i < os_mark:
                chars.append('░')
            elif i < ob_mark:
                chars.append('─')
            else:
                chars.append('▓')
        return '0 ' + ''.join(chars) + ' 100'

    def _detect_cross_signal(self, rsi_prev, rsi_curr):
        """检测 RSI 交叉信号"""
        signals = []
        if rsi_prev < RSI_OVERSOLD and rsi_curr >= RSI_OVERSOLD:
            signals.append('🟢 上穿30 → 脱离超卖, **买入信号**')
        if rsi_prev > RSI_OVERBOUGHT and rsi_curr <= RSI_OVERBOUGHT:
            signals.append('🔴 下穿70 → 脱离超买, **卖出信号**')
        if rsi_prev < RSI_OVERBOUGHT and rsi_curr >= RSI_OVERBOUGHT:
            signals.append('⚠ 上穿70 → 进入超买区, 注意回调风险')
        if rsi_prev > RSI_OVERSOLD and rsi_curr <= RSI_OVERSOLD:
            signals.append('⚠ 下穿30 → 进入超卖区, 注意反弹机会')
        return ' | '.join(signals) if signals else ''

    # ═══════════════════════════════════════════════════════════════
    # 交易计划输出 (★ v2: 含价格触发线)
    # ═══════════════════════════════════════════════════════════════

    def _print_trading_plan(self, curr_price):
        """打印当日交易计划 (v2: 含反弹/回落确认线)"""
        d = self._rsi_detail
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        avail_cash = self.st.get('avail_cash', 0)
        pos_pct = self.st.get('pos_pct', 0)
        total_asset = self.st.get('total_asset', 0)
        rsi_val = d['rsi']
        rsi_zone = d['zone']

        _log('┌' + '─' * 62 + '┐')
        _log('│  🎯 RSI v2 交易计划 — {}  {}  RSI={:.1f}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', ''), rsi_val, d['zone_cn']).ljust(55) + '│')
        _log('├' + '─' * 62 + '┤')

        _log('│  开盘: Y{:.2f}  当前: Y{:.2f}  ({:+.2f}%)'.format(
            self._daily_open, curr_price,
            (curr_price / self._daily_open - 1) * 100 if self._daily_open > 0 else 0).ljust(56) + '│')
        _log('│  持仓: {:>5}股  Y{:>10,.0f}  ({:.0f}%)  总资产: Y{:>12,.0f}'.format(
            base_shares, base_shares * curr_price, pos_pct, total_asset).ljust(56) + '│')
        _log('│  可用资金: Y{:>12,.0f}    T+0可卖: {:>3}股 ({}手)'.format(
            avail_cash, base_can_use, base_can_use // TRADE_LOT_SIZE).ljust(56) + '│')
        _log('├' + '─' * 62 + '┤')

        # ── ★ v2: 价格行为确认机制说明 ──
        _log('│  🔄 价格行为确认机制 (v2):'.ljust(55) + '│')

        if rsi_zone in ('oversold', 'extreme_oversold'):
            _log('│  📈 **超卖区域** → 激活下跌反弹买入监控'.ljust(55) + '│')
            dip_line = round(self._daily_open * (1.0 - DIP_TRIGGER_PCT), 2)
            _log('│  ├─ 步骤1: 价格跌破 Y{:.2f} (-{:.1f}%)'.format(
                dip_line, DIP_TRIGGER_PCT * 100).ljust(55) + '│')
            _log('│  │         → 开始跟踪最低点 (dip_price)'.ljust(55) + '│')
            _log('│  ├─ 步骤2: 价格从最低点回升 ≥ {:.2f}%'.format(
                BOUNCE_PCT * 100).ljust(55) + '│')
            _log('│  │         → ✅ 确认反弹, 执行买入'.ljust(55) + '│')
            _log('│  └─ 📝 示例: Y{:.2f}跌到Y{:.2f}, 反弹回Y{:.2f} → 买入'.format(
                self._daily_open,
                round(self._daily_open * 0.97, 2),
                round(self._daily_open * 0.97 * (1 + BOUNCE_PCT), 2)).ljust(55) + '│')

            # 买入可行性
            buy_lots_cash = int(avail_cash * POSITION_PCT_BUY / (curr_price * TRADE_LOT_SIZE * 1.01))
            buy_lots = min(buy_lots_cash, MAX_POSITION_LOTS)
            if buy_lots >= MIN_POSITION_LOTS:
                _log('│     ✅ 买入可行: {}手 × Y{:.2f} ≈ Y{:,.0f}'.format(
                    buy_lots, curr_price, buy_lots * TRADE_LOT_SIZE * curr_price).ljust(55) + '│')
            else:
                _log('│     ❌ 资金不足: 需Y{:,.0f}/手'.format(
                    curr_price * TRADE_LOT_SIZE).ljust(55) + '│')

        elif rsi_zone in ('overbought', 'extreme_overbought'):
            _log('│  📉 **超买区域** → 激活上涨回落卖出监控'.ljust(55) + '│')
            spike_line = round(self._daily_open * (1.0 + SPIKE_TRIGGER_PCT), 2)
            _log('│  ├─ 步骤1: 价格涨破 Y{:.2f} (+{:.1f}%)'.format(
                spike_line, SPIKE_TRIGGER_PCT * 100).ljust(55) + '│')
            _log('│  │         → 开始跟踪最高点 (peak_price)'.ljust(55) + '│')
            _log('│  ├─ 步骤2: 价格从最高点回落 ≥ {:.2f}%'.format(
                PULLBACK_PCT * 100).ljust(55) + '│')
            _log('│  │         → ✅ 确认回落, 执行卖出'.ljust(55) + '│')
            _log('│  ├─ 紧急买回: 卖价 +{:.1f}% 时强制买回止损'.format(
                EMERGENCY_BUYBACK_PCT * 100).ljust(55) + '│')
            _log('│  └─ 📝 示例: Y{:.2f}涨到Y{:.2f}, 回落到Y{:.2f} → 卖出'.format(
                self._daily_open,
                round(self._daily_open * 1.03, 2),
                round(self._daily_open * 1.03 * (1 - PULLBACK_PCT), 2)).ljust(55) + '│')

            sell_lots = min(base_can_use // TRADE_LOT_SIZE, MAX_POSITION_LOTS)
            if sell_lots >= MIN_POSITION_LOTS:
                _log('│     ✅ 卖出可行: {}手'.format(sell_lots).ljust(55) + '│')
            else:
                _log('│     ❌ 无可用持仓: T+0可卖{}股'.format(base_can_use).ljust(55) + '│')

        else:
            _log('│  ⚪ **中性区域** — 等待RSI进入超买/超卖区域'.ljust(55) + '│')
            _log('│  ├─ RSI方向: {} (Δ{:+.1f} vs 前日)'.format(
                '向上' if rsi_val > d['rsi_prev'] else '向下',
                rsi_val - d['rsi_prev']).ljust(55) + '│')
            if rsi_val > 55:
                _log('│  ├─ RSI偏强, 接近超买区 (距70还差{:.1f}点)'.format(
                    RSI_OVERBOUGHT - rsi_val).ljust(55) + '│')
            elif rsi_val < 45:
                _log('│  ├─ RSI偏弱, 接近超卖区 (距30还差{:.1f}点)'.format(
                    rsi_val - RSI_OVERSOLD).ljust(55) + '│')
            _log('│  └─ 建议: 等待RSI进入超买/超卖区域再激活监控'.ljust(55) + '│')

        _log('├' + '─' * 62 + '┤')
        _log('│  📋 参数: RSI({}) | 超买{} | 超卖{} | 反弹{:.2f}% | 回落{:.2f}% | 日{}笔'.format(
            RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
            BOUNCE_PCT * 100, PULLBACK_PCT * 100, MAX_DAILY_TRADES).ljust(55) + '│')

        if self.total_trades > 0:
            wr = self.win_count / self.total_trades * 100 if self.total_trades > 0 else 0
            _log('│  📈 历史: {}笔 | 胜率{:.0f}% | 累计PnL ~Y{:,.0f}'.format(
                self.total_trades, wr, self.total_pnl).ljust(55) + '│')

        _log('└' + '─' * 62 + '┘')
        _log('')

    # ═══════════════════════════════════════════════════════════════
    # ★ v2: 价格行为确认状态机
    # ═══════════════════════════════════════════════════════════════

    def _handle_idle(self, price):
        """
        IDLE 状态: 等待 RSI 区域触发监控。

        - RSI超卖 (oversold) + 价格低于开盘 → 激活 MONITOR_DIP
        - RSI超买 (overbought) + 价格高于开盘 → 激活 MONITOR_SPIKE
        - 中性 → 保持 IDLE
        """
        st = self.st
        rsi_zone = st.get('rsi_zone', 'neutral')
        trade_count = st.get('trade_count', 0)

        if trade_count >= MAX_DAILY_TRADES:
            st['fstate'] = STATE_DONE
            _log('[状态] 今日交易已达上限{}笔 → DONE'.format(MAX_DAILY_TRADES))
            return

        if rsi_zone in ('oversold', 'extreme_oversold'):
            # 激活买入监控: 开始跟踪最低点
            st['fstate'] = STATE_MONITOR_DIP
            st['dip_price'] = price
            st['peak_price'] = 0.0
            st['state_entered_at'] = now_hms()
            st['state_bars'] = 0
            _log('')
            _log('┌' + '─' * 55 + '┐')
            _log('│  🟢 [激活] 下跌反弹买入监控 (RSI={:.1f} < {})'.format(
                st['rsi_val'], RSI_OVERSOLD).ljust(48) + '│')
            _log('│  当前价: Y{:.2f}  |  开盘: Y{:.2f}'.format(
                price, self._daily_open).ljust(48) + '│')
            _log('│  跟踪最低点 → 反弹{:.2f}%确认 → 执行买入'.format(
                BOUNCE_PCT * 100).ljust(48) + '│')
            _log('└' + '─' * 55 + '┘')

        elif rsi_zone in ('overbought', 'extreme_overbought'):
            # 激活卖出监控: 开始跟踪最高点
            st['fstate'] = STATE_MONITOR_SPIKE
            st['peak_price'] = price
            st['dip_price'] = 0.0
            st['state_entered_at'] = now_hms()
            st['state_bars'] = 0
            _log('')
            _log('┌' + '─' * 55 + '┐')
            _log('│  🔴 [激活] 上涨回落卖出监控 (RSI={:.1f} > {})'.format(
                st['rsi_val'], RSI_OVERBOUGHT).ljust(48) + '│')
            _log('│  当前价: Y{:.2f}  |  开盘: Y{:.2f}'.format(
                price, self._daily_open).ljust(48) + '│')
            _log('│  跟踪最高点 → 回落{:.2f}%确认 → 执行卖出'.format(
                PULLBACK_PCT * 100).ljust(48) + '│')
            _log('└' + '─' * 55 + '┘')

    def _handle_monitor_dip(self, price):
        """
        MONITOR_DIP 状态: 超卖区 — 跟踪下跌 + 等待反弹确认。

        逻辑:
          1. 持续更新最低点 (dip_price)
          2. 当 price 从 dip 回升 ≥ BOUNCE_PCT → 确认反弹, 执行买入
          3. 如果 RSI 回升到中性 → 取消监控, 回到 IDLE
        """
        st = self.st
        st['state_bars'] += 1

        # 更新最低点
        if price < st['dip_price']:
            old_dip = st['dip_price']
            st['dip_price'] = price
            if old_dip > 0:
                _log(f'  [DIP] 新低 Y{price:.2f} (前低 Y{old_dip:.2f}, 跌幅加深 {(old_dip - price) / old_dip * 100:.2f}%)')

        dip = st['dip_price']
        if dip <= 0:
            return

        bounce = (price - dip) / dip if dip > 0 else 0
        drop_from_open = (self._daily_open - dip) / self._daily_open if self._daily_open > 0 else 0

        # ── 止损检查: 如果跌破开盘价 STOP_LOSS_PCT, 放弃买入 ──
        if drop_from_open > STOP_LOSS_PCT and st['state_bars'] > 60:
            _log(f'[DIP止损] 跌幅已达 {drop_from_open*100:.2f}% > {STOP_LOSS_PCT*100:.1f}%, 放弃买入')
            st['fstate'] = STATE_DONE
            return

        # ── 反弹确认 → 买入 ──
        if bounce >= BOUNCE_PCT:
            _log('')
            _log('╔' + '═' * 55 + '╗')
            _log('║  ✅ [买入确认] 下跌反弹信号触发!'.ljust(48) + '║')
            _log('╠' + '═' * 55 + '╣')
            _log('║  最低点:  Y{:.2f}'.format(dip).ljust(48) + '║')
            _log('║  当前价:  Y{:.2f}'.format(price).ljust(48) + '║')
            _log('║  反弹幅度: {:.3f}%  (≥ {:.2f}% 确认线)'.format(
                bounce * 100, BOUNCE_PCT * 100).ljust(48) + '║')
            _log('║  跌幅(开): {:.2f}%'.format(
                drop_from_open * 100).ljust(48) + '║')
            _log('╚' + '═' * 55 + '╝')
            self._execute_buy(price, dip, bounce)

        # ── RSI 回到中性 → 取消监控 ──
        rsi_zone = st.get('rsi_zone', 'neutral')
        if rsi_zone == 'neutral' and st['state_bars'] > 10:
            _log(f'[DIP取消] RSI回到中性区, 取消买入监控 (最低Y{dip:.2f}, 当前Y{price:.2f})')
            st['fstate'] = STATE_IDLE
            st['dip_price'] = 0.0

    def _handle_monitor_spike(self, price):
        """
        MONITOR_SPIKE 状态: 超买区 — 跟踪上涨 + 等待回落确认。

        逻辑:
          1. 持续更新最高点 (peak_price)
          2. 当 price 从 peak 回落 ≥ PULLBACK_PCT → 确认回落, 执行卖出
          3. 如果 RSI 回到中性 → 取消监控, 回到 IDLE
        """
        st = self.st
        st['state_bars'] += 1

        # 更新最高点
        if price > st['peak_price']:
            old_peak = st['peak_price']
            st['peak_price'] = price
            if old_peak > 0:
                _log(f'  [SPIKE] 新高 Y{price:.2f} (前高 Y{old_peak:.2f}, 涨幅扩大 {(price - old_peak) / old_peak * 100:.2f}%)')

        peak = st['peak_price']
        if peak <= 0:
            return

        pullback = (peak - price) / peak if peak > 0 else 0
        rise_from_open = (peak - self._daily_open) / self._daily_open if self._daily_open > 0 else 0

        # ── 回落确认 → 卖出 ──
        if pullback >= PULLBACK_PCT:
            _log('')
            _log('╔' + '═' * 55 + '╗')
            _log('║  ✅ [卖出确认] 上涨回落信号触发!'.ljust(48) + '║')
            _log('╠' + '═' * 55 + '╣')
            _log('║  最高点:  Y{:.2f}'.format(peak).ljust(48) + '║')
            _log('║  当前价:  Y{:.2f}'.format(price).ljust(48) + '║')
            _log('║  回落幅度: {:.3f}%  (≥ {:.2f}% 确认线)'.format(
                pullback * 100, PULLBACK_PCT * 100).ljust(48) + '║')
            _log('║  涨幅(开): {:.2f}%'.format(
                rise_from_open * 100).ljust(48) + '║')
            # 预估毛利
            est_profit = (peak - price) * TRADE_LOT_SIZE
            _log('║  预估毛利: ~Y{:,.0f}'.format(est_profit).ljust(48) + '║')
            _log('╚' + '═' * 55 + '╝')
            self._execute_sell(price, peak, pullback)

        # ── RSI 回到中性 → 取消监控 ──
        rsi_zone = st.get('rsi_zone', 'neutral')
        if rsi_zone == 'neutral' and st['state_bars'] > 10:
            _log(f'[SPIKE取消] RSI回到中性区, 取消卖出监控 (最高Y{peak:.2f}, 当前Y{price:.2f})')
            st['fstate'] = STATE_IDLE
            st['peak_price'] = 0.0

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

    def _execute_buy(self, price, dip_price, bounce_pct):
        """
        执行买入 (下跌反弹确认)。

        Args:
            price: 当前价格
            dip_price: 最低点价格
            bounce_pct: 反弹比例
        """
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES:
            _log(f'[买入] 今日已达{MAX_DAILY_TRADES}笔上限, 跳过')
            self.st['fstate'] = STATE_DONE
            return

        avail_cash = self.st.get('avail_cash', 0)
        need = price * TRADE_LOT_SIZE * 1.01
        if avail_cash < need:
            _log(f'[买入失败] 资金不足 (需Y{need:,.0f} > Y{avail_cash:,.0f})')
            self.st['fstate'] = STATE_IDLE
            return

        self.st['trade_count'] = tc + 1
        self.st['fill_price'] = price
        self.st['trigger_price'] = dip_price
        _log(f'[RSI买入 #{tc+1}/{MAX_DAILY_TRADES}] 下跌反弹确认 @ Y{price:.2f} (最低Y{dip_price:.2f}, 反弹{bounce_pct*100:.2f}%)')

        snap = self._snapshot_account()
        if self.dry_run:
            _log(f'  [模拟] 买入 {STOCK_QMT} Y{price:.2f} x {TRADE_LOT_SIZE}股')
            self._verify_trade(snap, '下跌反弹买入(模拟)', price, TRADE_LOT_SIZE)
        else:
            order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            _time.sleep(0.5)
            self._verify_trade(snap, '下跌反弹买入', price, TRADE_LOT_SIZE)

        self.total_trades += 1
        self.st['fstate'] = STATE_DONE

        # 检查是否还有交易次数
        self._maybe_resume()

    def _execute_sell(self, price, peak_price, pullback_pct):
        """
        执行卖出 (上涨回落确认)。

        Args:
            price: 当前价格
            peak_price: 最高点价格
            pullback_pct: 回落比例
        """
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES:
            _log(f'[卖出] 今日已达{MAX_DAILY_TRADES}笔上限, 跳过')
            self.st['fstate'] = STATE_DONE
            return

        can_use = self.st.get('base_can_use', 0)
        if can_use < TRADE_LOT_SIZE:
            _log(f'[卖出失败] 无可用持仓 (T+0可卖{can_use}股)')
            self.st['fstate'] = STATE_IDLE
            return

        self.st['trade_count'] = tc + 1
        self.st['fill_price'] = price
        self.st['trigger_price'] = peak_price
        est_profit = (peak_price - price) * TRADE_LOT_SIZE
        _log(f'[RSI卖出 #{tc+1}/{MAX_DAILY_TRADES}] 上涨回落确认 @ Y{price:.2f} (最高Y{peak_price:.2f}, 回落{pullback_pct*100:.2f}%, 毛利~Y{est_profit:.0f})')

        snap = self._snapshot_account()
        if self.dry_run:
            _log(f'  [模拟] 卖出 {STOCK_QMT} Y{price:.2f} x {TRADE_LOT_SIZE}股')
            self._verify_trade(snap, '上涨回落卖出(模拟)', price, -TRADE_LOT_SIZE)
        else:
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            _time.sleep(0.5)
            self._verify_trade(snap, '上涨回落卖出', price, -TRADE_LOT_SIZE)

        self.total_trades += 1
        self.total_pnl += est_profit
        self.st['day_pnl'] += est_profit
        self.st['fstate'] = STATE_DONE

        # 检查是否还有交易次数
        self._maybe_resume()

    def _maybe_resume(self):
        """交易完成后检查是否还有剩余次数, 有则回到 IDLE"""
        st = self.st
        tc = st.get('trade_count', 0)
        if tc < MAX_DAILY_TRADES:
            rsi_zone = st.get('rsi_zone', 'neutral')
            if rsi_zone in ('oversold', 'extreme_oversold', 'overbought', 'extreme_overbought'):
                self._refresh_position()
                st['fstate'] = STATE_IDLE
                st['peak_price'] = 0.0
                st['dip_price'] = 0.0
                st['state_bars'] = 0
                _log(f'[恢复监控] 交易完成, 剩余{MAX_DAILY_TRADES - tc}次 → 回到 IDLE')
            else:
                _log(f'[交易完成] RSI已回中性, 停止监控')
        else:
            _log(f'[交易完成] 今日{tc}笔已达上限, 保持DONE')

    def _force_close(self, price, reason='尾盘'):
        """强制平仓"""
        fstate = self.st.get('fstate', STATE_IDLE)
        if fstate == STATE_MONITOR_SPIKE:
            _log(f'[{reason}] 强制卖出平仓 @ Y{price:.2f}')
            self.st['fstate'] = STATE_FORCED
            if not self.dry_run:
                snap = self._snapshot_account()
                order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
                _time.sleep(0.5)
                self._verify_trade(snap, f'{reason}强制卖出', 0, -TRADE_LOT_SIZE)
        elif fstate == STATE_MONITOR_DIP:
            _log(f'[{reason}] 取消买入监控, 不再等待反弹')
            self.st['fstate'] = STATE_FORCED

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
        _log(f'{STOCK_NAME} RSI策略 v2 MiniQMT版 启动')
        _log(f'  mode: {"信号监控(不下单)" if self.dry_run else "实盘交易"}')
        _log(f'  标的: {STOCK_NAME}({STOCK_QMT})')
        _log(f'  ★ v2: 下跌反弹买入 + 上涨回落卖出')
        _log(f'  RSI({RSI_PERIOD}) | 超买>{RSI_OVERBOUGHT} | 超卖<{RSI_OVERSOLD}')
        _log(f'  反弹确认: {BOUNCE_PCT*100:.2f}% | 回落确认: {PULLBACK_PCT*100:.2f}%')
        _log(f'  ★ 日志: {get_logger().log_path if get_logger() else "(未初始化)"}')

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

                    if self.st.get('trade_date', '') != today:
                        _log(f'[盘前] 新交易日 {today}, 初始化...')
                        try:
                            self._daily_init()
                        except Exception as e:
                            _log(f'[异常] {e}')

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

                fstate = self.st.get('fstate', STATE_IDLE)

                # ── ★ v2: 状态机路由 ──
                if fstate == STATE_IDLE:
                    self._handle_idle(price)
                elif fstate == STATE_MONITOR_DIP:
                    self._handle_monitor_dip(price)
                elif fstate == STATE_MONITOR_SPIKE:
                    self._handle_monitor_spike(price)
                elif fstate in (STATE_DONE, STATE_FORCED):
                    # 尝试恢复
                    tc = self.st.get('trade_count', 0)
                    if tc < MAX_DAILY_TRADES and now < '14:57:00':
                        rsi_zone = self.st.get('rsi_zone', 'neutral')
                        if rsi_zone in ('oversold', 'extreme_oversold', 'overbought', 'extreme_overbought'):
                            self._maybe_resume()

                # ── 尾盘强制平仓 ──
                if ENABLE_FORCE_CLOSE and now >= FORCE_CLOSE_TIME:
                    if fstate in (STATE_MONITOR_SPIKE, STATE_MONITOR_DIP):
                        self._force_close(price, '尾盘')

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
            _log(f'{STOCK_NAME} RSI策略 v2 已停止 | 累计 {self.total_trades}笔 | PnL ~Y{self.total_pnl:,.0f}')
            logger = get_logger()
            if logger is not None:
                _log('★ 日志已保存至: ' + logger.log_path)
                logger.close()

    # ═══════════════════════════════════════════════════════════════
    # 心跳 (v2: 含状态机细节)
    # ═══════════════════════════════════════════════════════════════

    def _heartbeat(self, price):
        """每分钟心跳 — 含完整状态机信息"""
        rsi = self.st.get('rsi_val', 50)
        zone_cn = self.st.get('rsi_zone_cn', '⚪ 中性')
        zone = self.st.get('rsi_zone', 'neutral')
        fstate = self.st.get('fstate', STATE_IDLE)
        trade_count = self.st.get('trade_count', 0)

        parts = [
            f'RSI={rsi:.1f}',
            f'{zone_cn}',
        ]

        if fstate == STATE_IDLE:
            parts.append('⏳ 等待触发')
        elif fstate == STATE_MONITOR_DIP:
            dip = self.st.get('dip_price', 0)
            if dip > 0:
                bounce = (price - dip) / dip * 100
                need = BOUNCE_PCT * 100 - bounce
                parts.append(f'📈 DIP监控')
                parts.append(f'最低Y{dip:.2f}')
                parts.append(f'反弹{bounce:.2f}%')
                parts.append(f'需{need:.2f}%确认')
            else:
                parts.append(f'📈 DIP监控 Y{price:.2f}')
        elif fstate == STATE_MONITOR_SPIKE:
            peak = self.st.get('peak_price', 0)
            if peak > 0:
                pullback = (peak - price) / peak * 100
                need = PULLBACK_PCT * 100 - pullback
                parts.append(f'📉 SPIKE监控')
                parts.append(f'最高Y{peak:.2f}')
                parts.append(f'回落{pullback:.2f}%')
                parts.append(f'需{need:.2f}%确认')
            else:
                parts.append(f'📉 SPIKE监控 Y{price:.2f}')
        elif fstate == STATE_DONE:
            parts.append('✅ 已完成')
        elif fstate == STATE_FORCED:
            parts.append('🔒 强制平仓')

        parts.append(f'Y{price:.2f}')
        parts.append(f'{trade_count}/{MAX_DAILY_TRADES}笔')

        _log('[RSI心跳] {}'.format(' | '.join(parts)))


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT RSI 买卖策略 v2 — 下跌反弹买入 + 上涨回落卖出',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v2_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v2_miniqmt.py" --mode live

RSI 策略 v2 说明:
  ★ v2 新增价格行为确认机制:
    - 超卖(RSI<30) + 下跌反弹 ≥ 0.10% → 买入
    - 超买(RSI>70) + 上涨回落 ≥ 0.10% → 卖出

  状态机:
    IDLE → MONITOR_DIP (超卖:跟踪最低点→反弹确认→买入)
    IDLE → MONITOR_SPIKE (超买:跟踪最高点→回落确认→卖出)

前置条件:
  1. 启动 MiniQMT: QMT -> 右上角"极简mode"
  2. MiniQMT 中已登录资金账号 8890145315
  3. QMT 已下载 601869 历史数据
        """
    )
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live'])
    args = parser.parse_args()

    logger = FileLogger(STOCK_CODE, version='RSI_v2')
    set_logger(logger)
    print(f'★ 日志文件: {logger.log_path}')

    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  ⚠ 即将启动实盘自动交易!')
        print(f'  策略: RSI 买卖策略 v2 (下跌反弹买入 + 上涨回落卖出)')
        print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
        print(f'  账号: {ACCOUNT}')
        print(f'  RSI周期: {RSI_PERIOD} | 超买线: {RSI_OVERBOUGHT} | 超卖线: {RSI_OVERSOLD}')
        print(f'  反弹确认: {BOUNCE_PCT*100:.2f}% | 回落确认: {PULLBACK_PCT*100:.2f}%')
        print('  请确认:')
        print('  1. MiniQMT 已启动 (极简mode)')
        print('  2. 资金账号已登录')
        print('  3. 了解RSI策略 + 价格行为确认机制风险')
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
