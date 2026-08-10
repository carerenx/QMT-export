# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — RSI 买卖策略 v3 (实时RSI + 价格行为确认)
================================================================================
 基于 RSI(14) 相对强弱指标的日内交易策略 — ★ v3: 逐tick实时RSI

 RSI数值       状态       含义
 > 70          超买       上涨动能强劲，价格可能短期过热，存在回调风险 → 卖出信号
 30 - 70       中性       市场处于正常波动区间 → 持有/观望
 < 30          超卖       下跌动能充分释放，价格可能短期超跌，存在反弹机会 → 买入信号

 ★ v3 核心改进: 实时RSI计算
   v1/v2 只在开盘时用昨日收盘价计算一次RSI, 全天冻结。这不够"实时"。
   v3 用 RealTimeRSI 类实现增量更新:
   ┌─ RealTimeRSI 工作原理:
   │   1. 开盘时从日线数据初始化完整RSI序列
   │   2. 存储 avg_gain / avg_loss (Wilder平滑的中间状态)
   │   3. 交易时段每tick: diff = 当前价 - 昨收
   │      gain = max(diff, 0), loss = max(-diff, 0)
   │      avg_gain = (avg_gain × 13 + gain) / 14   ← Wilder递推
   │      avg_loss = (avg_loss × 13 + loss) / 14
   │      RS = avg_gain / avg_loss
   │      RSI_live = 100 - 100/(1+RS)
   │   4. 实时RSI随着当前价格波动而动态变化!
   └──────────────────────────────────────────────

 ★ v2 保留: 价格行为确认机制 (下跌反弹买入 + 上涨回落卖出)
   RSI 判断方向，价格行为确认时机。

 特点:
   ★ 实时RSI: 每2秒根据最新价格更新, 不再全天冻结
   ★ 心跳日志输出 "RSI_live" (当前价格计算) vs "RSI_daily" (昨日收盘计算)
   ★ 完整 RSI 计算过程 + 可视化刻度条
   ★ 状态机实时监控 (当前阶段 + peak/dip跟踪 + 距确认线距离)

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v3_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/RSI_Strategy_v3_miniqmt.py" --mode live

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
# 策略参数
# ============================================================================

ACCOUNT          = '8890145315'
STOCK_CODE       = '601869'
STOCK_NAME       = '长飞光纤'
STOCK_QMT        = f'{STOCK_CODE}.SH'
STRATEGY_NAME    = 'RSI策略_v3'

TRADE_LOT_SIZE   = 100
MINIQMT_PATH     = 'C:/QMT/userdata_mini'
SESSION_ID       = 0

# ── RSI 参数 ──
RSI_PERIOD       = 14
RSI_OVERBOUGHT   = 70
RSI_OVERSOLD     = 30
RSI_EXTREME_HIGH = 80
RSI_EXTREME_LOW  = 20

# ── 价格行为确认参数 ──
BOUNCE_PCT          = 0.0010      # 反弹确认 0.10%
PULLBACK_PCT        = 0.0010      # 回落确认 0.10%
EMERGENCY_BUYBACK_PCT = 0.03      # 紧急买回 3%
STOP_LOSS_PCT       = 0.03        # 止损 3%

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

# ── 状态机 ──
STATE_IDLE          = 'IDLE'
STATE_MONITOR_DIP   = 'MONITOR_DIP'
STATE_MONITOR_SPIKE = 'MONITOR_SPIKE'
STATE_DONE          = 'DONE'
STATE_FORCED        = 'FORCED'

FORCE_CLOSE_TIME   = '14:57:00'
ENABLE_FORCE_CLOSE = True


# ============================================================================
# ★ v3 核心: RealTimeRSI — 实时增量RSI计算器
# ============================================================================

class RealTimeRSI:
    """
    实时 RSI 计算器 — 支持每日初始化和逐tick增量更新。

    ★ 核心原理:
      Wilder's smoothing 递推公式:
        avg_gain[t] = (avg_gain[t-1] × (period-1) + gain[t]) / period
        avg_loss[t] = (avg_loss[t-1] × (period-1) + loss[t]) / period
        RS = avg_gain / avg_loss
        RSI = 100 - 100 / (1 + RS)

    ★ 实时更新:
      开盘后用日线数据初始化, 得到历史 avg_gain/avg_loss 终值。
      交易时段每tick: 用 (当前价 - 昨收) 作为今日的 gain/loss,
      应用 Wilder 递推, 实时更新 RSI。
      当前价波动 → gain/loss 变化 → RSI 实时变化!

    使用示例:
      rsi_engine = RealTimeRSI(period=14)
      rsi_engine.init_from_daily(daily_closes)   # 开盘初始化
      live_rsi = rsi_engine.update_tick(464.50)   # 每tick更新
    """

    def __init__(self, period=14):
        self.period = period
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self.prev_close = 0.0      # 昨收 (用于计算今日涨跌)
        self.current_rsi = 50.0
        self.daily_rsi = 50.0      # 基于昨日收盘的"静态"RSI
        self.rsi_series = []       # 完整历史RSI序列
        self.initialized = False
        self._daily_closes = []    # 历史日线收盘价 (用于打印)

    # ── 日线级别 RSI 计算 (非增量, 完整计算) ──

    @staticmethod
    def compute_rsi_full(closes, period=14):
        """从收盘价序列完整计算 RSI (Wilder's smoothing)"""
        n = len(closes)
        if n < period + 1:
            return [50.0] * n, 0.0, 0.0

        rsi_vals = [50.0] * n
        gains, losses = [], []
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains.append(diff if diff > 0 else 0.0)
            losses.append(abs(diff) if diff < 0 else 0.0)

        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period

        if avg_l == 0:
            rsi_vals[period] = 100.0
        else:
            rsi_vals[period] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)

        for i in range(period, n - 1):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            if avg_l == 0:
                rsi_vals[i + 1] = 100.0
            else:
                rsi_vals[i + 1] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)

        return rsi_vals, avg_g, avg_l

    # ── 初始化: 从日线数据构建RSI序列 ──

    def init_from_daily(self, closes):
        """
        从日线收盘价序列初始化。

        完成:
          1. 计算完整历史 RSI 序列
          2. 存储 avg_gain / avg_loss 终值 (后续逐tick递推的起点)
          3. 存储昨收价 (计算今日涨跌的基准)
        """
        n = len(closes)
        if n < self.period + 1:
            _log(f'[RealTimeRSI] 数据不足: {n}天 < {self.period + 1}天')
            return False

        self._daily_closes = list(closes)
        rsi_series, avg_g, avg_l = self.compute_rsi_full(closes, self.period)

        self.rsi_series = rsi_series
        self.avg_gain = avg_g        # ★ 历史终值, 逐tick更新的起点
        self.avg_loss = avg_l        # ★ 历史终值
        self.daily_rsi = rsi_series[-1]
        self.current_rsi = rsi_series[-1]
        self.prev_close = closes[-1]  # ★ 昨收 = 最后一天收盘价
        self.initialized = True

        # 打印初始化信息
        _log(f'[RealTimeRSI] 初始化完成: {n}天日线数据')
        _log(f'  avg_gain={self.avg_gain:.4f}  avg_loss={self.avg_loss:.4f}')
        _log(f'  prev_close(昨收)=Y{self.prev_close:.2f}')
        _log(f'  daily_RSI(静态)={self.daily_rsi:.2f}')
        _log(f'  → 交易时段将用 当前价vs昨收 逐tick更新RSI')

        return True

    # ── ★ 逐tick实时更新 ──

    def update_tick(self, current_price):
        """
        用当前价格实时更新 RSI。

        ★ 工作原理:
          diff = current_price - prev_close (昨收)
          gain = max(diff, 0)   → 今日涨幅
          loss = max(-diff, 0)  → 今日跌幅
          用 Wilder 递推更新 avg_gain / avg_loss
          计算实时 RSI

        Args:
            current_price: 当前最新成交价

        Returns:
            float: 实时 RSI 值
        """
        if not self.initialized or self.prev_close <= 0:
            return self.current_rsi

        # 今日涨跌 (当前价 vs 昨收)
        diff = current_price - self.prev_close
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        # ★ Wilder 递推: 用今日涨跌更新移动平均
        new_avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
        new_avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period

        # 计算 RSI (不修改存储的 avg_gain/avg_loss, 因为这是临时更新)
        # 注: 这里用临时值计算, 不覆盖存储值, 确保每次更新都是从历史基线出发
        if new_avg_loss == 0:
            self.current_rsi = 100.0
        else:
            rs = new_avg_gain / new_avg_loss
            self.current_rsi = 100.0 - 100.0 / (1.0 + rs)

        return self.current_rsi

    # ── 获取实时RSI详情 ──

    def get_live_detail(self, current_price):
        """获取实时RSI的详细信息 (用于心跳打印)"""
        if not self.initialized:
            return None

        diff = current_price - self.prev_close
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        new_avg_g = (self.avg_gain * (self.period - 1) + gain) / self.period
        new_avg_l = (self.avg_loss * (self.period - 1) + loss) / self.period

        rs = new_avg_g / new_avg_l if new_avg_l > 0 else float('inf')
        live_rsi = self.current_rsi

        # 区域
        zone, zone_cn = self._classify_zone(live_rsi)

        return {
            'live_rsi': round(live_rsi, 2),
            'daily_rsi': round(self.daily_rsi, 2),
            'rsi_delta': round(live_rsi - self.daily_rsi, 2),
            'avg_gain': round(new_avg_g, 4),
            'avg_loss': round(new_avg_l, 4),
            'rs': round(rs, 2) if rs != float('inf') else '∞',
            'today_gain': round(gain, 2),
            'today_loss': round(loss, 2),
            'today_diff': round(diff, 2),
            'today_diff_pct': round(diff / self.prev_close * 100, 2) if self.prev_close > 0 else 0,
            'prev_close': self.prev_close,
            'zone': zone,
            'zone_cn': zone_cn,
        }

    def _classify_zone(self, rsi_val):
        """RSI 区域分类"""
        if rsi_val > RSI_EXTREME_HIGH:
            return 'extreme_overbought', '🔴🔴 极度超买'
        elif rsi_val > RSI_OVERBOUGHT:
            return 'overbought', '🔴 超买'
        elif rsi_val < RSI_EXTREME_LOW:
            return 'extreme_oversold', '🟢🟢 极度超卖'
        elif rsi_val < RSI_OVERSOLD:
            return 'oversold', '🟢 超卖'
        else:
            return 'neutral', '⚪ 中性'

    # ── RSI 可视化 ──

    def zone_bar(self, rsi_val=None):
        """RSI 可视化刻度条"""
        if rsi_val is None:
            rsi_val = self.current_rsi
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

    def get_daily_changes(self, lookback=None):
        """获取最近N天的逐日涨跌明细 (用于盘前打印)"""
        closes = self._daily_closes
        if lookback is None:
            lookback = self.period
        n = len(closes)
        changes = []
        start = max(1, n - lookback - 1)
        for i in range(start, n):
            chg = closes[i] - closes[i - 1]
            chg_pct = (chg / closes[i - 1]) * 100 if closes[i - 1] > 0 else 0
            changes.append({
                'day_offset': n - 1 - i,
                'close': closes[i],
                'prev_close': closes[i - 1],
                'change': round(chg, 2),
                'change_pct': round(chg_pct, 2),
            })
        return changes[-lookback:] if len(changes) >= lookback else changes

    def detect_cross_signal(self):
        """检测RSI交叉信号 (基于daily RSI的前后变化)"""
        if len(self.rsi_series) < 2:
            return ''
        prev = self.rsi_series[-2]
        curr = self.daily_rsi
        signals = []
        if prev < RSI_OVERSOLD and curr >= RSI_OVERSOLD:
            signals.append('🟢 上穿30 → 脱离超卖, **买入信号**')
        if prev > RSI_OVERBOUGHT and curr <= RSI_OVERBOUGHT:
            signals.append('🔴 下穿70 → 脱离超买, **卖出信号**')
        if prev < RSI_OVERBOUGHT and curr >= RSI_OVERBOUGHT:
            signals.append('⚠ 上穿70 → 进入超买区')
        if prev > RSI_OVERSOLD and curr <= RSI_OVERSOLD:
            signals.append('⚠ 下穿30 → 进入超卖区')
        return ' | '.join(signals) if signals else ''


# ============================================================================
# 工具函数
# ============================================================================

def compute_sma(values, period):
    """简单移动平均"""
    n = len(values)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


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
# RSI Strategy Runner v3
# ============================================================================

class RSIStrategyRunner:
    """
    MiniQMT RSI 策略运行器 v3

    ★ v3 核心改进: RealTimeRSI — 逐tick实时更新
      不再冻结在开盘时的RSI值, 而是每tick根据当前价格重新计算。
    ★ v2 保留: 价格行为确认状态机
    """

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='RSI_v3')
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

        # ★ v3: 实时RSI引擎
        self.rsi_engine = RealTimeRSI(period=RSI_PERIOD)

        self._daily_open = 0.0

    # ── 状态初始化 ──

    def _init_state(self):
        self.st.update({
            'trade_date': '',
            'initialized': False,
            'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'avail_cash': 0.0, 'pos_value': 0.0, 'pos_pct': 0.0, 'total_asset': 0.0,
            'daily_open': 0.0,
            # RSI (实时)
            'rsi_live': 50.0,
            'rsi_daily': 50.0,
            'rsi_zone': 'neutral',
            'rsi_zone_cn': '⚪ 中性',
            # 状态机
            'fstate': STATE_IDLE,
            'peak_price': 0.0, 'dip_price': 0.0,
            'trigger_price': 0.0, 'fill_price': 0.0,
            'state_entered_at': '', 'state_bars': 0,
            # 交易
            'trade_count': 0, 'day_pnl': 0.0, 'stop_loss_hit': False,
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

        # tick 修正今日开盘
        tick = self.ctx.get_full_tick([STOCK_QMT])
        tick_data = tick.get(STOCK_QMT, {})
        today_open = tick_data.get('open', 0)
        if today_open > 0 and len(opens) > 0:
            opens[-1] = today_open
        self._daily_open = today_open if today_open > 0 else opens[-1]

        # ★ v3: 初始化实时RSI引擎
        _log('')
        _log('═' * 55)
        _log('  RealTimeRSI 引擎初始化...')
        _log('═' * 55)
        ok = self.rsi_engine.init_from_daily(closes)
        if not ok:
            _log('[错误] RSI引擎初始化失败')
            return

        daily_rsi = self.rsi_engine.daily_rsi
        rsi_series = self.rsi_engine.rsi_series
        prev_rsi = rsi_series[-2] if len(rsi_series) >= 2 else 50.0
        _, zone_cn = self.rsi_engine._classify_zone(daily_rsi)

        curr_price = tick_data.get('lastPrice', closes[-1])
        if curr_price <= 0:
            curr_price = closes[-1]

        # 首次实时RSI更新
        live_rsi = self.rsi_engine.update_tick(curr_price)
        live_zone, live_zone_cn = self.rsi_engine._classify_zone(live_rsi)

        # 刷新持仓
        self._refresh_position()
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('base_cost', 0) == 0.0:
            self.st['base_cost'] = closes[-1]

        # 资金
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0

        pos_value = base_shares * curr_price
        total_asset = pos_value + avail_cash
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0

        # 保存状态
        self.st.update({
            'initialized': True,
            'daily_open': self._daily_open,
            'base_shares': base_shares, 'base_can_use': base_can_use,
            'avail_cash': avail_cash, 'pos_value': pos_value,
            'pos_pct': pos_pct, 'total_asset': total_asset,
            'rsi_live': live_rsi,
            'rsi_daily': daily_rsi,
            'rsi_zone': live_zone,
            'rsi_zone_cn': live_zone_cn,
            'fstate': STATE_IDLE, 'trade_count': 0, 'day_pnl': 0.0,
        })

        # 打印
        self._print_rsi_calculation(curr_price, live_rsi)
        self._print_trading_plan(curr_price)

    # ═══════════════════════════════════════════════════════════════
    # RSI 详细计算输出 (v3: 含实时RSI)
    # ═══════════════════════════════════════════════════════════════

    def _print_rsi_calculation(self, curr_price, live_rsi):
        """打印 RSI(14) 详细计算过程"""
        eng = self.rsi_engine
        daily_rsi = eng.daily_rsi
        detail = eng.get_live_detail(curr_price)

        _log('')
        _log('╔' + '═' * 62 + '╗')
        _log('║  📊 RSI(14) 实时计算 — {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', '')).ljust(51) + '║')
        _log('╠' + '═' * 62 + '╣')

        # 历史14天涨跌
        changes = eng.get_daily_changes(RSI_PERIOD)
        _log('║  历史{:d}天逐日涨跌 (日线收盘):'.format(RSI_PERIOD).ljust(55) + '║')
        _log('║  {:>4s}  {:>10s}  {:>10s}  {:>12s}  ║'.format('T-N', '收盘价', '涨跌额', '涨跌幅%'))
        _log('║  ' + '─' * 52 + '  ║')
        for ch in changes:
            arrow = '📈' if ch['change'] > 0 else ('📉' if ch['change'] < 0 else '➡')
            _log('║  T-{:d}  Y{:>10.2f}  {:>+10.2f}  {:>+11.2f}%  {} ║'.format(
                ch['day_offset'], ch['close'], ch['change'], ch['change_pct'], arrow))

        _log('╠' + '═' * 62 + '╣')
        _log('║  历史 avg_gain: {:.4f}   历史 avg_loss: {:.4f}'.format(
            eng.avg_gain, eng.avg_loss).ljust(55) + '║')
        _log('║  昨收 (prev_close): Y{:.2f}'.format(eng.prev_close).ljust(55) + '║')
        _log('╠' + '═' * 62 + '╣')

        # ★ 实时RSI vs 静态RSI
        _log('║  ★ 实时RSI (基于当前价 Y{:.2f}):'.format(curr_price).ljust(55) + '║')
        if detail:
            _log('║  今日涨跌: Y{:+.2f} ({:+.2f}%)'.format(
                detail['today_diff'], detail['today_diff_pct']).ljust(55) + '║')
            _log('║  实时 avg_gain: {:.4f}   实时 avg_loss: {:.4f}'.format(
                detail['avg_gain'], detail['avg_loss']).ljust(55) + '║')
            _log('║  实时 RS: {}  →  RSI_live = **{:.2f}**'.format(
                detail['rs'], live_rsi).ljust(55) + '║')

        _log('╠' + '═' * 62 + '╣')

        # RSI 刻度条
        zone_bar = eng.zone_bar(live_rsi)
        _, zone_cn = eng._classify_zone(live_rsi)
        _log('║  RSI 实时位置:'.ljust(55) + '║')
        _log('║  {} ║'.format(zone_bar))
        _log('║  RSI_live={:.1f} (实时) | RSI_daily={:.1f} (静态) | Δ={:+.1f} ║'.format(
            live_rsi, daily_rsi, live_rsi - daily_rsi))
        _log('║  区域: {} ║'.format(zone_cn))

        # 距阈值
        dist_ob = RSI_OVERBOUGHT - live_rsi
        dist_os = live_rsi - RSI_OVERSOLD
        _log('║  距超买线(70): {:+.1f}点   |  距超卖线(30): {:+.1f}点 ║'.format(dist_ob, dist_os))

        # 交叉信号
        cross = eng.detect_cross_signal()
        if cross:
            _log('╠' + '═' * 62 + '╣')
            _log('║  ⚡ RSI 交叉信号: {} ║'.format(cross))

        _log('╚' + '═' * 62 + '╝')
        _log('')

    # ═══════════════════════════════════════════════════════════════
    # 交易计划
    # ═══════════════════════════════════════════════════════════════

    def _print_trading_plan(self, curr_price):
        """打印当日交易计划 (v3: 实时RSI)"""
        eng = self.rsi_engine
        live_detail = eng.get_live_detail(curr_price)
        if live_detail is None:
            return
        live_rsi = live_detail['live_rsi']
        rsi_zone = live_detail['zone']
        zone_cn = live_detail['zone_cn']
        daily_rsi = live_detail['daily_rsi']

        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        avail_cash = self.st.get('avail_cash', 0)
        pos_pct = self.st.get('pos_pct', 0)
        total_asset = self.st.get('total_asset', 0)

        _log('┌' + '─' * 62 + '┐')
        _log('│  🎯 RSI v3 交易计划 — {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', '')).ljust(55) + '│')
        _log('│  RSI_live={:.1f} (实时)  RSI_daily={:.1f} (静态)  {}'.format(
            live_rsi, daily_rsi, zone_cn).ljust(55) + '│')
        _log('├' + '─' * 62 + '┤')
        _log('│  开盘: Y{:.2f}  当前: Y{:.2f}  ({:+.2f}%)'.format(
            self._daily_open, curr_price,
            (curr_price / self._daily_open - 1) * 100 if self._daily_open > 0 else 0).ljust(56) + '│')
        _log('│  持仓: {:>5}股  Y{:>10,.0f}  ({:.0f}%)  总资产: Y{:>12,.0f}'.format(
            base_shares, base_shares * curr_price, pos_pct, total_asset).ljust(56) + '│')
        _log('│  可用资金: Y{:>12,.0f}    T+0可卖: {:>3}股 ({}手)'.format(
            avail_cash, base_can_use, base_can_use // TRADE_LOT_SIZE).ljust(56) + '│')
        _log('├' + '─' * 62 + '┤')
        _log('│  🔄 价格行为确认机制:'.ljust(55) + '│')

        if rsi_zone in ('oversold', 'extreme_oversold'):
            _log('│  📈 **超卖区域** → 下跌反弹买入监控'.ljust(55) + '│')
            _log('│  ├─ 跟踪最低点 → 反弹≥{:.2f}% → 买入'.format(BOUNCE_PCT*100).ljust(55) + '│')
            buy_lots = min(
                int(avail_cash * POSITION_PCT_BUY / (curr_price * TRADE_LOT_SIZE * 1.01)),
                MAX_POSITION_LOTS
            )
            if buy_lots >= MIN_POSITION_LOTS:
                _log('│  └─ ✅ 买入可行: {}手 × Y{:.2f} ≈ Y{:,.0f}'.format(
                    buy_lots, curr_price, buy_lots * TRADE_LOT_SIZE * curr_price).ljust(55) + '│')
            else:
                _log('│  └─ ❌ 资金不足: 需Y{:,.0f}/手'.format(
                    curr_price * TRADE_LOT_SIZE).ljust(55) + '│')

        elif rsi_zone in ('overbought', 'extreme_overbought'):
            _log('│  📉 **超买区域** → 上涨回落卖出监控'.ljust(55) + '│')
            _log('│  ├─ 跟踪最高点 → 回落≥{:.2f}% → 卖出'.format(PULLBACK_PCT*100).ljust(55) + '│')
            sell_lots = min(base_can_use // TRADE_LOT_SIZE, MAX_POSITION_LOTS)
            if sell_lots >= MIN_POSITION_LOTS:
                _log('│  └─ ✅ 卖出可行: {}手'.format(sell_lots).ljust(55) + '│')
            else:
                _log('│  └─ ❌ 无可用持仓'.ljust(55) + '│')

        else:
            _log('│  ⚪ **中性区域** — 等待RSI进入超买/超卖'.ljust(55) + '│')
            if live_rsi > 55:
                _log('│  ├─ RSI偏强, 距超买{:.1f}点'.format(RSI_OVERBOUGHT - live_rsi).ljust(55) + '│')
            elif live_rsi < 45:
                _log('│  ├─ RSI偏弱, 距超卖{:.1f}点'.format(live_rsi - RSI_OVERSOLD).ljust(55) + '│')
            _log('│  └─ 建议: 等待RSI进入超买/超卖区域'.ljust(55) + '│')

        _log('├' + '─' * 62 + '┤')
        _log('│  📋 RSI({}) | 超买{} | 超卖{} | 反弹{:.2f}% | 回落{:.2f}% | 日{}笔'.format(
            RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
            BOUNCE_PCT*100, PULLBACK_PCT*100, MAX_DAILY_TRADES).ljust(55) + '│')
        if self.total_trades > 0:
            wr = self.win_count / self.total_trades * 100 if self.total_trades > 0 else 0
            _log('│  📈 历史: {}笔 | 胜率{:.0f}% | PnL ~Y{:,.0f}'.format(
                self.total_trades, wr, self.total_pnl).ljust(55) + '│')
        _log('└' + '─' * 62 + '┘')
        _log('')

    # ═══════════════════════════════════════════════════════════════
    # 状态机 (同v2)
    # ═══════════════════════════════════════════════════════════════

    def _handle_idle(self, price):
        st = self.st
        rsi_zone = st.get('rsi_zone', 'neutral')
        trade_count = st.get('trade_count', 0)

        if trade_count >= MAX_DAILY_TRADES:
            st['fstate'] = STATE_DONE
            _log('[状态] 今日交易已达上限{}笔 → DONE'.format(MAX_DAILY_TRADES))
            return

        if rsi_zone in ('oversold', 'extreme_oversold'):
            st['fstate'] = STATE_MONITOR_DIP
            st['dip_price'] = price
            st['peak_price'] = 0.0
            st['state_entered_at'] = now_hms()
            st['state_bars'] = 0
            _log('')
            _log('┌' + '─' * 55 + '┐')
            _log('│  🟢 [激活] 下跌反弹买入监控 (RSI_live={:.1f})'.format(st['rsi_live']).ljust(48) + '│')
            _log('│  当前价: Y{:.2f}  |  开盘: Y{:.2f}'.format(price, self._daily_open).ljust(48) + '│')
            _log('│  跟踪最低点 → 反弹{:.2f}%确认 → 买入'.format(BOUNCE_PCT*100).ljust(48) + '│')
            _log('└' + '─' * 55 + '┘')

        elif rsi_zone in ('overbought', 'extreme_overbought'):
            st['fstate'] = STATE_MONITOR_SPIKE
            st['peak_price'] = price
            st['dip_price'] = 0.0
            st['state_entered_at'] = now_hms()
            st['state_bars'] = 0
            _log('')
            _log('┌' + '─' * 55 + '┐')
            _log('│  🔴 [激活] 上涨回落卖出监控 (RSI_live={:.1f})'.format(st['rsi_live']).ljust(48) + '│')
            _log('│  当前价: Y{:.2f}  |  开盘: Y{:.2f}'.format(price, self._daily_open).ljust(48) + '│')
            _log('│  跟踪最高点 → 回落{:.2f}%确认 → 卖出'.format(PULLBACK_PCT*100).ljust(48) + '│')
            _log('└' + '─' * 55 + '┘')

    def _handle_monitor_dip(self, price):
        st = self.st
        st['state_bars'] += 1

        if price < st['dip_price']:
            old_dip = st['dip_price']
            st['dip_price'] = price
            if old_dip > 0:
                _log(f'  [DIP] 新低 Y{price:.2f} (前低 Y{old_dip:.2f})')

        dip = st['dip_price']
        if dip <= 0:
            return

        bounce = (price - dip) / dip
        drop_from_open = (self._daily_open - dip) / self._daily_open if self._daily_open > 0 else 0

        # 止损
        if drop_from_open > STOP_LOSS_PCT and st['state_bars'] > 60:
            _log(f'[DIP止损] 跌幅 {drop_from_open*100:.2f}% > {STOP_LOSS_PCT*100:.1f}%, 放弃买入')
            st['fstate'] = STATE_DONE
            return

        # 反弹确认 → 买入
        if bounce >= BOUNCE_PCT:
            _log('')
            _log('╔' + '═' * 55 + '╗')
            _log('║  ✅ [买入确认] 下跌反弹信号触发!'.ljust(48) + '║')
            _log('╠' + '═' * 55 + '╣')
            _log('║  最低点: Y{:.2f}  当前价: Y{:.2f}'.format(dip, price).ljust(48) + '║')
            _log('║  反弹: {:.3f}% (≥{:.2f}%)  跌幅(开): {:.2f}%'.format(
                bounce*100, BOUNCE_PCT*100, drop_from_open*100).ljust(48) + '║')
            _log('╚' + '═' * 55 + '╝')
            self._execute_buy(price, dip, bounce)

        # RSI 回中性 → 取消
        rsi_zone = st.get('rsi_zone', 'neutral')
        if rsi_zone == 'neutral' and st['state_bars'] > 10:
            _log(f'[DIP取消] RSI回中性, 取消买入监控 (最低Y{dip:.2f})')
            st['fstate'] = STATE_IDLE
            st['dip_price'] = 0.0

    def _handle_monitor_spike(self, price):
        st = self.st
        st['state_bars'] += 1

        if price > st['peak_price']:
            old_peak = st['peak_price']
            st['peak_price'] = price
            if old_peak > 0:
                _log(f'  [SPIKE] 新高 Y{price:.2f} (前高 Y{old_peak:.2f})')

        peak = st['peak_price']
        if peak <= 0:
            return

        pullback = (peak - price) / peak
        rise_from_open = (peak - self._daily_open) / self._daily_open if self._daily_open > 0 else 0

        # 回落确认 → 卖出
        if pullback >= PULLBACK_PCT:
            est_profit = (peak - price) * TRADE_LOT_SIZE
            _log('')
            _log('╔' + '═' * 55 + '╗')
            _log('║  ✅ [卖出确认] 上涨回落信号触发!'.ljust(48) + '║')
            _log('╠' + '═' * 55 + '╣')
            _log('║  最高点: Y{:.2f}  当前价: Y{:.2f}'.format(peak, price).ljust(48) + '║')
            _log('║  回落: {:.3f}% (≥{:.2f}%)  涨幅(开): {:.2f}%'.format(
                pullback*100, PULLBACK_PCT*100, rise_from_open*100).ljust(48) + '║')
            _log('║  预估毛利: ~Y{:,.0f}'.format(est_profit).ljust(48) + '║')
            _log('╚' + '═' * 55 + '╝')
            self._execute_sell(price, peak, pullback)

        # RSI 回中性 → 取消
        rsi_zone = st.get('rsi_zone', 'neutral')
        if rsi_zone == 'neutral' and st['state_bars'] > 10:
            _log(f'[SPIKE取消] RSI回中性, 取消卖出监控 (最高Y{peak:.2f})')
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
        return {'shares': shares, 'can_use': can_use, 'cash': cash,
                'cost': cost, 'total_asset': shares * price + cash, 'price': price}

    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_cash = snap_after['cash'] - snap_before['cash']
        if abs(trade_shares) == TRADE_LOT_SIZE:
            status = '✅ 已成交' if d_shares == trade_shares else ('⏳ 待成交' if d_shares == 0 else f'⚠ 部分成交(Δ{d_shares:+d}股)')
        else:
            status = '📝 已下单'
        _log('  ┌─ [RSI交易校验] {} ─'.format(label))
        _log('  │  下单: {}股 @ Y{:.2f}  |  {}'.format('{:+d}'.format(trade_shares), trade_price, status))
        _log('  ├─ 持仓: {:>5}股 → {:>5}股  (Δ{:+d}股)'.format(snap_before['shares'], snap_after['shares'], d_shares))
        _log('  ├─ 资金: Y{:>12,.2f} → Y{:>12,.2f}  (Δ{:+,.2f})'.format(snap_before['cash'], snap_after['cash'], d_cash))
        _log('  └' + '─' * 45)
        self.st['base_shares'] = snap_after['shares']
        self.st['base_can_use'] = snap_after['can_use']
        self.st['base_cost'] = snap_after['cost']

    def _execute_buy(self, price, dip_price, bounce_pct):
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES:
            _log(f'[买入] 已达{MAX_DAILY_TRADES}笔上限')
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
        self._maybe_resume()

    def _execute_sell(self, price, peak_price, pullback_pct):
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES:
            _log(f'[卖出] 已达{MAX_DAILY_TRADES}笔上限')
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
        self._maybe_resume()

    def _maybe_resume(self):
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
                _log(f'[恢复监控] 剩余{MAX_DAILY_TRADES - tc}次 → IDLE')
            else:
                _log(f'[交易完成] RSI已回中性, 停止监控')
        else:
            _log(f'[交易完成] {tc}笔已达上限')

    def _force_close(self, price, reason='尾盘'):
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
            _log(f'[{reason}] 取消买入监控')
            self.st['fstate'] = STATE_FORCED

    # ═══════════════════════════════════════════════════════════════
    # 主循环 (v3: 每tick更新实时RSI)
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
        _log(f'{STOCK_NAME} RSI策略 v3 MiniQMT版 启动')
        _log(f'  mode: {"信号监控(不下单)" if self.dry_run else "实盘交易"}')
        _log(f'  ★ v3: RealTimeRSI — 逐tick实时更新')
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
                            _log(f'\n[盘前预初始化] {now}')
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
                        live_rsi = self.st.get('rsi_live', 50)
                        zone_cn = self.st.get('rsi_zone_cn', '⚪ 中性')
                        if now < '09:30:00':
                            _log(f'[等待开盘] {now} | RSI_live={live_rsi:.1f} {zone_cn}')
                        elif now > '15:00:00':
                            _log(f'[已收盘] {now} | RSI_live={live_rsi:.1f} {zone_cn}')
                        elif '11:30:00' < now < '13:00:00':
                            _log(f'[午休] {now} | RSI_live={live_rsi:.1f} {zone_cn}')
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

                # ★ v3 核心: 每tick用当前价格更新实时RSI
                live_rsi = self.rsi_engine.update_tick(price)
                live_zone, live_zone_cn = self.rsi_engine._classify_zone(live_rsi)
                self.st['rsi_live'] = live_rsi
                self.st['rsi_zone'] = live_zone
                self.st['rsi_zone_cn'] = live_zone_cn

                fstate = self.st.get('fstate', STATE_IDLE)

                # ── 状态机路由 ──
                if fstate == STATE_IDLE:
                    self._handle_idle(price)
                elif fstate == STATE_MONITOR_DIP:
                    self._handle_monitor_dip(price)
                elif fstate == STATE_MONITOR_SPIKE:
                    self._handle_monitor_spike(price)
                elif fstate in (STATE_DONE, STATE_FORCED):
                    tc = self.st.get('trade_count', 0)
                    if tc < MAX_DAILY_TRADES and now < '14:57:00':
                        if live_zone in ('oversold', 'extreme_oversold', 'overbought', 'extreme_overbought'):
                            self._maybe_resume()

                # ── 尾盘强平 ──
                if ENABLE_FORCE_CLOSE and now >= FORCE_CLOSE_TIME:
                    if fstate in (STATE_MONITOR_SPIKE, STATE_MONITOR_DIP):
                        self._force_close(price, '尾盘')

                # ── 心跳 (每分钟) ──
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
            _log(f'{STOCK_NAME} RSI策略 v3 已停止 | 累计 {self.total_trades}笔 | PnL ~Y{self.total_pnl:,.0f}')
            logger = get_logger()
            if logger is not None:
                _log('★ 日志已保存至: ' + logger.log_path)
                logger.close()

    # ═══════════════════════════════════════════════════════════════
    # 心跳 (v3: 含实时RSI更新详情)
    # ═══════════════════════════════════════════════════════════════

    def _heartbeat(self, price):
        """每分钟心跳 — ★ v3: 显示实时RSI更新过程"""
        eng = self.rsi_engine
        detail = eng.get_live_detail(price)
        fstate = self.st.get('fstate', STATE_IDLE)
        trade_count = self.st.get('trade_count', 0)

        if detail is None:
            _log('[RSI心跳] RSI引擎未初始化')
            return

        parts = [
            f'RSI_live={detail["live_rsi"]:.1f}',
            f'{detail["zone_cn"]}',
        ]

        # ★ 实时RSI更新细节
        parts.append(f'今日{detail["today_diff"]:+.2f}({detail["today_diff_pct"]:+.2f}%)')
        parts.append(f'RSI_daily={detail["daily_rsi"]:.1f} Δ={detail["rsi_delta"]:+.1f}')

        # 状态机细节
        if fstate == STATE_IDLE:
            parts.append('⏳')
        elif fstate == STATE_MONITOR_DIP:
            dip = self.st.get('dip_price', 0)
            if dip > 0:
                bounce = (price - dip) / dip * 100
                need = max(0, BOUNCE_PCT * 100 - bounce)
                parts.append(f'DIP:低Y{dip:.2f} 反{bounce:.2f}% 需{need:.2f}%')
        elif fstate == STATE_MONITOR_SPIKE:
            peak = self.st.get('peak_price', 0)
            if peak > 0:
                pb = (peak - price) / peak * 100
                need = max(0, PULLBACK_PCT * 100 - pb)
                parts.append(f'SPIKE:高Y{peak:.2f} 回{pb:.2f}% 需{need:.2f}%')
        elif fstate == STATE_DONE:
            parts.append('✅')
        elif fstate == STATE_FORCED:
            parts.append('🔒')

        parts.append(f'Y{price:.2f}')
        parts.append(f'{trade_count}/{MAX_DAILY_TRADES}笔')

        _log('[RSI] {}'.format(' | '.join(parts)))


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT RSI 买卖策略 v3 — 实时RSI + 价格行为确认',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v3_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v3_miniqmt.py" --mode live

RSI 策略 v3 (★ 实时RSI + 价格行为确认):
  ★ v3 核心: RealTimeRSI — 逐tick增量更新
    开盘后用日线初始化 avg_gain/avg_loss
    交易时段每tick: diff = 当前价 - 昨收
    → Wilder递推更新 → RSI实时变化!
    心跳可见: RSI_live(当前价) vs RSI_daily(昨收) 对比

  状态机:
    IDLE → MONITOR_DIP (超卖: 跟踪最低点→反弹确认→买入)
    IDLE → MONITOR_SPIKE (超买: 跟踪最高点→回落确认→卖出)

前置条件:
  1. 启动 MiniQMT: QMT -> 右上角"极简mode"
  2. MiniQMT 中已登录资金账号 8890145315
  3. QMT 已下载 601869 历史数据
        """
    )
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live'])
    args = parser.parse_args()

    logger = FileLogger(STOCK_CODE, version='RSI_v3')
    set_logger(logger)
    print(f'★ 日志文件: {logger.log_path}')

    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  ⚠ 即将启动实盘自动交易!')
        print(f'  策略: RSI 买卖策略 v3 (实时RSI + 价格确认)')
        print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
        print(f'  RSI周期: {RSI_PERIOD} | 超买>{RSI_OVERBOUGHT} | 超卖<{RSI_OVERSOLD}')
        print(f'  反弹确认: {BOUNCE_PCT*100:.2f}% | 回落确认: {PULLBACK_PCT*100:.2f}%')
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
