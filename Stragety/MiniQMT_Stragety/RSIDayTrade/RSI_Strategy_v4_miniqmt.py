# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — RSI 买卖策略 v4 (实时RSI + 昨收基线修复)
================================================================================
 基于 RSI(14) 相对强弱指标的日内交易策略。

 RSI数值       状态       含义
 > 70          超买       上涨动能强劲，价格可能短期过热，存在回调风险 → 卖出信号
 30 - 70       中性       市场处于正常波动区间 → 持有/观望
 < 30          超卖       下跌动能充分释放，价格可能短期超跌，存在反弹机会 → 买入信号

 ★ v4 修复 (vs v3):
   修复实时RSI计算不准的问题:
   - xtdata.get_local_data() 返回的数据包含今日partial bar (close=当前价)
   - v3 误将 closes[-1] (今日部分K线) 当作"昨收"
   - 导致 prev_close ≈ 当前价 → diff ≈ 0 → RSI_live 几乎不动
   - v4: 检测并剥离今日bar, 用 closes[-2] 作为真正的昨收
   - RSI序列也从 closes[:-1] (不含今日) 计算
   - 诊断输出: 明确显示 昨收价 / 今日开盘 / 今日当前价

   ★ v3 保留: RealTimeRSI — 逐tick增量更新
   ★ v2 保留: 价格行为确认机制 (下跌反弹买入 + 上涨回落卖出)

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v4_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/RSI_Strategy_v4_miniqmt.py" --mode live

================================================================================
"""
import os
import sys
import time as _time
import argparse
import traceback as _traceback
from datetime import datetime, timedelta
from typing import Optional

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
# 策略参数
# ============================================================================

ACCOUNT          = '8890145315'
STOCK_CODE       = '601869'
STOCK_NAME       = '长飞光纤'
STOCK_QMT        = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE   = 100
MINIQMT_PATH     = 'C:/QMT/userdata_mini'
SESSION_ID       = 0

# RSI
RSI_PERIOD       = 14
RSI_OVERBOUGHT   = 70
RSI_OVERSOLD     = 30
RSI_EXTREME_HIGH = 80
RSI_EXTREME_LOW  = 20

# 价格行为确认
BOUNCE_PCT          = 0.0010
PULLBACK_PCT        = 0.0010
EMERGENCY_BUYBACK_PCT = 0.03
STOP_LOSS_PCT       = 0.03

# 数据 & 费率
HIST_DATA_LEN    = 80
COMMISSION       = 0.00025
STAMP_TAX        = 0.001

# 交易参数
MAX_POSITION_LOTS  = 5
MIN_POSITION_LOTS  = 1
MAX_DAILY_TRADES   = 2
POSITION_PCT_BUY   = 0.30
POSITION_PCT_SELL  = 0.30

# 状态机
STATE_IDLE          = 'IDLE'
STATE_MONITOR_DIP   = 'MONITOR_DIP'
STATE_MONITOR_SPIKE = 'MONITOR_SPIKE'
STATE_DONE          = 'DONE'
STATE_FORCED        = 'FORCED'

FORCE_CLOSE_TIME   = '14:57:00'
ENABLE_FORCE_CLOSE = True


# ============================================================================
# ★ v4: RealTimeRSI — 修复昨收基线
# ============================================================================

class RealTimeRSI:
    """
    实时 RSI 计算器 — Wilder's smoothing 递推 + 逐tick更新。

    ★ v4 修复: closes[-1] 是xtdata返回的今日partial bar, 不是昨收。
      正确做法: 昨收 = closes[-2], RSI序列从 closes[:-1] 计算。

    公式:
      avg_gain[t] = (avg_gain[t-1] × (period-1) + gain[t]) / period
      avg_loss[t] = (avg_loss[t-1] × (period-1) + loss[t]) / period
      RS = avg_gain / avg_loss
      RSI = 100 - 100 / (1 + RS)
    """

    def __init__(self, period=14):
        self.period = period
        self.avg_gain = 0.0          # ★ 昨日收盘时 Wilder 平滑的 avg_gain 终值
        self.avg_loss = 0.0          # ★ 昨日收盘时 Wilder 平滑的 avg_loss 终值
        self.prev_close = 0.0        # ★ 真正的昨收价 (不含今日)
        self.current_rsi = 50.0      # 实时RSI (逐tick更新)
        self.daily_rsi = 50.0        # 昨日收盘RSI (静态)
        self.rsi_series = []         # 历史RSI序列 (不含今日)
        self.initialized = False
        self._daily_closes = []      # 历史日线收盘价 (不含今日)
        self._yesterday_close = 0.0  # 昨收 (诊断用)

    # ── 完整RSI计算 ──

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

    # ── ★ v4: 初始化 — 剥离今日bar, 正确设置昨收 ──

    def init_from_daily(self, closes, today_open=0.0):
        """
        从日线收盘价序列初始化。

        ★ v4 修复:
          xtdata 在交易时段返回的数据包含今日 partial bar (close≈当前价)。
          必须剥离今日bar, 用昨日数据初始化RSI基线。

        Args:
          closes: 日线收盘价序列 (最后一项可能是今日partial bar)
          today_open: 今日开盘价 (0表示未开盘/非交易日, 用于检测今日bar)
        """
        n = len(closes)
        if n < self.period + 2:  # 至少需要 period+1 天历史 + 1天余量
            _log(f'[RealTimeRSI] 数据不足: {n}天 < {self.period + 2}天')
            return False

        # ★ v4: 检测最后一项是否为今日partial bar
        # 如果 today_open > 0, 说明今日已开盘, xtdata会包含今日bar
        has_today_bar = (today_open > 0)

        if has_today_bar:
            # 剥离今日bar: 历史数据 = 不含今日
            hist_closes = closes[:-1]
            self._yesterday_close = closes[-2]   # 倒数第二个 = 昨收
            today_bar_close = closes[-1]          # 最后一个 = 今日当前价(partial)
            _log(f'[RealTimeRSI] ★ 检测到今日partial bar, 已剥离')
            _log(f'  今日bar close(来自xtdata) = Y{today_bar_close:.2f}')
        else:
            # 今日未开盘, 最后一项就是昨收
            hist_closes = closes
            self._yesterday_close = closes[-1]
            _log(f'[RealTimeRSI] 今日未开盘, 使用全部历史数据')

        # 用历史数据(不含今日) 计算RSI
        self._daily_closes = list(hist_closes)
        rsi_series, avg_g, avg_l = self.compute_rsi_full(hist_closes, self.period)

        self.rsi_series = rsi_series
        self.avg_gain = avg_g          # ★ 昨日收盘时的avg_gain终值
        self.avg_loss = avg_l          # ★ 昨日收盘时的avg_loss终值
        self.prev_close = self._yesterday_close  # ★ 真正的昨收!
        self.daily_rsi = rsi_series[-1]
        self.current_rsi = rsi_series[-1]
        self.initialized = True

        _log(f'[RealTimeRSI] 初始化完成: {len(hist_closes)}天历史日线')
        _log(f'  昨收 (prev_close) = Y{self.prev_close:.2f}')
        _log(f'  avg_gain = {self.avg_gain:.4f}  |  avg_loss = {self.avg_loss:.4f}')
        if self.avg_loss > 0:
            _log(f'  RS = {self.avg_gain/self.avg_loss:.4f}')
        _log(f'  RSI_daily (昨日收盘静态) = {self.daily_rsi:.2f}')
        _log(f'  → 交易时段: diff = 当前价 - Y{self.prev_close:.2f}(昨收)')

        return True

    # ── ★ 逐tick实时更新 (v4: 基于正确的昨收) ──

    def update_tick(self, current_price):
        """
        用当前价格实时更新 RSI。

        diff = current_price - 昨收 (真正的昨日收盘价)
        gain = max(diff, 0)
        loss = max(-diff, 0)
        Wilder递推 → 实时RSI
        """
        if not self.initialized or self.prev_close <= 0:
            return self.current_rsi

        diff = current_price - self.prev_close       # ★ 当前价 vs 真正昨收
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        new_avg_g = (self.avg_gain * (self.period - 1) + gain) / self.period
        new_avg_l = (self.avg_loss * (self.period - 1) + loss) / self.period

        if new_avg_l == 0:
            self.current_rsi = 100.0
        else:
            rs = new_avg_g / new_avg_l
            self.current_rsi = 100.0 - 100.0 / (1.0 + rs)

        return self.current_rsi

    def get_live_detail(self, current_price):
        """获取实时RSI详细信息"""
        if not self.initialized:
            return None

        diff = current_price - self.prev_close
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        new_avg_g = (self.avg_gain * (self.period - 1) + gain) / self.period
        new_avg_l = (self.avg_loss * (self.period - 1) + loss) / self.period

        rs = new_avg_g / new_avg_l if new_avg_l > 0 else float('inf')

        zone, zone_cn = self._classify_zone(self.current_rsi)

        return {
            'live_rsi': round(self.current_rsi, 2),
            'daily_rsi': round(self.daily_rsi, 2),
            'rsi_delta': round(self.current_rsi - self.daily_rsi, 2),
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

    def zone_bar(self, rsi_val=None):
        if rsi_val is None:
            rsi_val = self.current_rsi
        bar_len = 50
        pos = max(0, min(bar_len, int(rsi_val / 100 * bar_len)))
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
        if len(self.rsi_series) < 2:
            return ''
        prev = self.rsi_series[-2]
        curr = self.daily_rsi
        signals = []
        if prev < RSI_OVERSOLD and curr >= RSI_OVERSOLD:
            signals.append('🟢 上穿30 → 脱离超卖')
        if prev > RSI_OVERBOUGHT and curr <= RSI_OVERBOUGHT:
            signals.append('🔴 下穿70 → 脱离超买')
        if prev < RSI_OVERBOUGHT and curr >= RSI_OVERBOUGHT:
            signals.append('⚠ 上穿70 → 进入超买区')
        if prev > RSI_OVERSOLD and curr <= RSI_OVERSOLD:
            signals.append('⚠ 下穿30 → 进入超卖区')
        return ' | '.join(signals) if signals else ''


# ============================================================================
# 工具函数
# ============================================================================

def compute_sma(values, period):
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
# RSI Strategy Runner v4
# ============================================================================

class RSIStrategyRunner:
    """MiniQMT RSI 策略运行器 v4 — 修复昨收基线"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='RSI_v4')
            set_logger(logger)

        self.conn = MiniQMTConnector()
        set_global_conn(self.conn, dry_run)

        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = dry_run

        self._last_heartbeat = 0.0
        self._running = True

        self.total_trades = 0
        self.total_pnl = 0.0
        self.win_count = 0
        self.loss_count = 0

        self.rsi_engine = RealTimeRSI(period=RSI_PERIOD)
        self._daily_open = 0.0

    # ── 状态 ──

    def _init_state(self):
        self.st.update({
            'trade_date': '', 'initialized': False,
            'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'avail_cash': 0.0, 'pos_value': 0.0, 'pos_pct': 0.0, 'total_asset': 0.0,
            'daily_open': 0.0, 'yesterday_close': 0.0,
            'rsi_live': 50.0, 'rsi_daily': 50.0,
            'rsi_zone': 'neutral', 'rsi_zone_cn': '⚪ 中性',
            'fstate': STATE_IDLE,
            'peak_price': 0.0, 'dip_price': 0.0,
            'trigger_price': 0.0, 'fill_price': 0.0,
            'state_entered_at': '', 'state_bars': 0,
            'trade_count': 0, 'day_pnl': 0.0, 'stop_loss_hit': False,
        })

    def _reset_daily(self):
        saved = {k: self.st.get(k, 0) for k in
                 ('base_shares', 'base_can_use', 'base_cost')}
        self._init_state()
        self.st.update(saved)

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                break

    # ── ★ v4: 每日初始化 — 正确处理今日bar ──

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

        if STOCK_QMT not in hist_close or len(hist_close[STOCK_QMT]) < RSI_PERIOD + 2:
            _log(f'[警告] 日线数据不足 (需要至少{RSI_PERIOD+2}天), 跳过今日')
            return

        closes = list(hist_close[STOCK_QMT])
        opens  = list(hist_open[STOCK_QMT])
        highs  = list(hist_high[STOCK_QMT])
        lows   = list(hist_low[STOCK_QMT])

        # tick 数据
        tick = self.ctx.get_full_tick([STOCK_QMT])
        tick_data = tick.get(STOCK_QMT, {})
        today_open = tick_data.get('open', 0)
        curr_price = tick_data.get('lastPrice', 0)

        # ★ v4: 检测并修正今日bar
        # 如果 today_open > 0 且 opens[-1] ≈ today_open, 则最后一根bar是今日
        if today_open > 0 and len(opens) > 0:
            opens[-1] = today_open  # 用tick的开盘价覆盖
        self._daily_open = today_open if today_open > 0 else opens[-1]

        if curr_price <= 0:
            curr_price = closes[-1]

        # ★ v4: 初始化实时RSI引擎 (传入today_open用于检测今日bar)
        _log('')
        _log('═' * 55)
        _log('  RealTimeRSI 引擎初始化 (v4: 昨收基线修复)')
        _log('═' * 55)
        ok = self.rsi_engine.init_from_daily(closes, today_open=today_open)
        if not ok:
            _log('[错误] RSI引擎初始化失败')
            return

        yesterday_close = self.rsi_engine.prev_close
        daily_rsi = self.rsi_engine.daily_rsi

        # ★ v4 诊断: 明确打印基线价格
        _log(f'  [诊断] 昨收 = Y{yesterday_close:.2f}')
        _log(f'  [诊断] 今日开盘 = Y{self._daily_open:.2f}')
        _log(f'  [诊断] 当前价 = Y{curr_price:.2f}')
        _log(f'  [诊断] 今日涨跌 = Y{curr_price - yesterday_close:+.2f} ({(curr_price/yesterday_close - 1)*100:+.2f}%)')

        # 首次实时RSI
        live_rsi = self.rsi_engine.update_tick(curr_price)
        live_zone, live_zone_cn = self.rsi_engine._classify_zone(live_rsi)

        # 刷新持仓
        self._refresh_position()
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('base_cost', 0) == 0.0:
            self.st['base_cost'] = yesterday_close

        # 资金
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0

        pos_value = base_shares * curr_price
        total_asset = pos_value + avail_cash
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0

        self.st.update({
            'initialized': True,
            'daily_open': self._daily_open,
            'yesterday_close': yesterday_close,
            'base_shares': base_shares, 'base_can_use': base_can_use,
            'avail_cash': avail_cash, 'pos_value': pos_value,
            'pos_pct': pos_pct, 'total_asset': total_asset,
            'rsi_live': live_rsi, 'rsi_daily': daily_rsi,
            'rsi_zone': live_zone, 'rsi_zone_cn': live_zone_cn,
            'fstate': STATE_IDLE, 'trade_count': 0, 'day_pnl': 0.0,
        })

        self._print_rsi_calculation(curr_price, live_rsi)
        self._print_trading_plan(curr_price)

    # ═══════════════════════════════════════════════════════════════
    # RSI 计算输出 (v4: 含昨收/今日对比)
    # ═══════════════════════════════════════════════════════════════

    def _print_rsi_calculation(self, curr_price, live_rsi):
        eng = self.rsi_engine
        daily_rsi = eng.daily_rsi
        detail = eng.get_live_detail(curr_price)

        _log('')
        _log('╔' + '═' * 62 + '╗')
        _log('║  📊 RSI(14) 实时计算 v4 — {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', '')).ljust(51) + '║')
        _log('╠' + '═' * 62 + '╣')

        # ★ v4: 基线价格对比
        _log('║  ★ 基线价格 (v4修复):'.ljust(55) + '║')
        _log('║  昨收 (prev_close) = Y{:.2f}'.format(eng.prev_close).ljust(55) + '║')
        _log('║  今日开盘           = Y{:.2f}'.format(self._daily_open).ljust(55) + '║')
        _log('║  当前价             = Y{:.2f}  ({:+.2f}%)'.format(
            curr_price, (curr_price/eng.prev_close - 1)*100).ljust(55) + '║')
        if detail:
            _log('║  今日涨跌: Y{:+.2f} ({:+.2f}%)   ← diff = 当前价-昨收'.format(
                detail['today_diff'], detail['today_diff_pct']).ljust(55) + '║')

        _log('╠' + '═' * 62 + '╣')

        # 历史14天
        changes = eng.get_daily_changes(RSI_PERIOD)
        _log('║  历史{:d}天日线涨跌:'.format(RSI_PERIOD).ljust(55) + '║')
        _log('║  {:>4s}  {:>10s}  {:>10s}  {:>12s}  ║'.format('T-N', '收盘价', '涨跌额', '涨跌幅%'))
        _log('║  ' + '─' * 52 + '  ║')
        for ch in changes:
            arrow = '📈' if ch['change'] > 0 else ('📉' if ch['change'] < 0 else '➡')
            _log('║  T-{:d}  Y{:>10.2f}  {:>+10.2f}  {:>+11.2f}%  {} ║'.format(
                ch['day_offset'], ch['close'], ch['change'], ch['change_pct'], arrow))

        _log('╠' + '═' * 62 + '╣')
        _log('║  历史 avg_gain={:.4f}  avg_loss={:.4f}'.format(
            eng.avg_gain, eng.avg_loss).ljust(55) + '║')

        if detail:
            _log('║  实时 avg_gain={:.4f}  avg_loss={:.4f}'.format(
                detail['avg_gain'], detail['avg_loss']).ljust(55) + '║')
            _log('║  实时 RS={}  →  RSI_live = **{:.2f}**'.format(
                detail['rs'], live_rsi).ljust(55) + '║')

        _log('╠' + '═' * 62 + '╣')

        zone_bar = eng.zone_bar(live_rsi)
        _, zone_cn = eng._classify_zone(live_rsi)
        _log('║  {} ║'.format(zone_bar))
        _log('║  RSI_live={:.1f} | RSI_daily={:.1f} | Δ={:+.1f} | {} ║'.format(
            live_rsi, daily_rsi, live_rsi - daily_rsi, zone_cn))

        dist_ob = RSI_OVERBOUGHT - live_rsi
        dist_os = live_rsi - RSI_OVERSOLD
        _log('║  距超买(70): {:+.1f}点  |  距超卖(30): {:+.1f}点 ║'.format(dist_ob, dist_os))

        cross = eng.detect_cross_signal()
        if cross:
            _log('╠' + '═' * 62 + '╣')
            _log('║  ⚡ RSI 交叉: {} ║'.format(cross))

        _log('╚' + '═' * 62 + '╝')
        _log('')

    # ═══════════════════════════════════════════════════════════════
    # 交易计划
    # ═══════════════════════════════════════════════════════════════

    def _print_trading_plan(self, curr_price):
        eng = self.rsi_engine
        detail = eng.get_live_detail(curr_price)
        if detail is None:
            return
        live_rsi = detail['live_rsi']
        rsi_zone = detail['zone']
        zone_cn = detail['zone_cn']

        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        avail_cash = self.st.get('avail_cash', 0)
        pos_pct = self.st.get('pos_pct', 0)
        total_asset = self.st.get('total_asset', 0)

        _log('┌' + '─' * 62 + '┐')
        _log('│  🎯 RSI v4 交易计划 — {}  {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', ''), zone_cn).ljust(55) + '│')
        _log('│  RSI_live={:.1f} (实时)  RSI_daily={:.1f} (昨收)  Δ={:+.1f}'.format(
            live_rsi, detail['daily_rsi'], detail['rsi_delta']).ljust(55) + '│')
        _log('├' + '─' * 62 + '┤')
        _log('│  昨收: Y{:.2f}  开盘: Y{:.2f}  当前: Y{:.2f}  ({:+.2f}%)'.format(
            eng.prev_close, self._daily_open, curr_price,
            (curr_price/self._daily_open - 1)*100 if self._daily_open > 0 else 0).ljust(56) + '│')
        _log('│  持仓: {:>5}股  Y{:>10,.0f}  ({:.0f}%)  总资产: Y{:>12,.0f}'.format(
            base_shares, base_shares*curr_price, pos_pct, total_asset).ljust(56) + '│')
        _log('│  可用资金: Y{:>12,.0f}    T+0可卖: {:>3}股 ({}手)'.format(
            avail_cash, base_can_use, base_can_use//TRADE_LOT_SIZE).ljust(56) + '│')
        _log('├' + '─' * 62 + '┤')
        _log('│  🔄 价格行为确认:'.ljust(55) + '│')

        if rsi_zone in ('oversold', 'extreme_oversold'):
            _log('│  📈 **超卖** → 下跌反弹买入 (反弹≥{:.2f}%)'.format(BOUNCE_PCT*100).ljust(55) + '│')
            buy_lots = min(int(avail_cash*POSITION_PCT_BUY/(curr_price*TRADE_LOT_SIZE*1.01)), MAX_POSITION_LOTS)
            if buy_lots >= MIN_POSITION_LOTS:
                _log('│  └─ ✅ 买入可行: {}手 × Y{:.2f} ≈ Y{:,.0f}'.format(
                    buy_lots, curr_price, buy_lots*TRADE_LOT_SIZE*curr_price).ljust(55) + '│')
            else:
                _log('│  └─ ❌ 资金不足'.ljust(55) + '│')
        elif rsi_zone in ('overbought', 'extreme_overbought'):
            _log('│  📉 **超买** → 上涨回落卖出 (回落≥{:.2f}%)'.format(PULLBACK_PCT*100).ljust(55) + '│')
            sell_lots = min(base_can_use//TRADE_LOT_SIZE, MAX_POSITION_LOTS)
            if sell_lots >= MIN_POSITION_LOTS:
                _log('│  └─ ✅ 卖出可行: {}手'.format(sell_lots).ljust(55) + '│')
            else:
                _log('│  └─ ❌ 无可用持仓'.ljust(55) + '│')
        else:
            _log('│  ⚪ **中性** — 等待RSI进入超买/超卖'.ljust(55) + '│')

        _log('├' + '─' * 62 + '┤')
        _log('│  📋 RSI({}) | 超买{} | 超卖{} | 反弹{:.2f}% | 回落{:.2f}% | 日{}笔'.format(
            RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
            BOUNCE_PCT*100, PULLBACK_PCT*100, MAX_DAILY_TRADES).ljust(55) + '│')
        if self.total_trades > 0:
            wr = self.win_count/self.total_trades*100 if self.total_trades > 0 else 0
            _log('│  📈 历史: {}笔 | 胜率{:.0f}% | PnL ~Y{:,.0f}'.format(
                self.total_trades, wr, self.total_pnl).ljust(55) + '│')
        _log('└' + '─' * 62 + '┘')
        _log('')

    # ═══════════════════════════════════════════════════════════════
    # 状态机
    # ═══════════════════════════════════════════════════════════════

    def _handle_idle(self, price):
        st = self.st
        rsi_zone = st.get('rsi_zone', 'neutral')
        if st.get('trade_count', 0) >= MAX_DAILY_TRADES:
            st['fstate'] = STATE_DONE
            _log('[状态] 已达{}笔上限 → DONE'.format(MAX_DAILY_TRADES))
            return
        if rsi_zone in ('oversold', 'extreme_oversold'):
            st['fstate'] = STATE_MONITOR_DIP
            st['dip_price'] = price; st['peak_price'] = 0.0
            st['state_entered_at'] = now_hms(); st['state_bars'] = 0
            _log('\n┌' + '─' * 55 + '┐')
            _log('│  🟢 [激活] 下跌反弹买入 (RSI_live={:.1f})'.format(st['rsi_live']).ljust(48) + '│')
            _log('│  Y{:.2f} | 反弹{:.2f}%确认→买入'.format(price, BOUNCE_PCT*100).ljust(48) + '│')
            _log('└' + '─' * 55 + '┘')
        elif rsi_zone in ('overbought', 'extreme_overbought'):
            st['fstate'] = STATE_MONITOR_SPIKE
            st['peak_price'] = price; st['dip_price'] = 0.0
            st['state_entered_at'] = now_hms(); st['state_bars'] = 0
            _log('\n┌' + '─' * 55 + '┐')
            _log('│  🔴 [激活] 上涨回落卖出 (RSI_live={:.1f})'.format(st['rsi_live']).ljust(48) + '│')
            _log('│  Y{:.2f} | 回落{:.2f}%确认→卖出'.format(price, PULLBACK_PCT*100).ljust(48) + '│')
            _log('└' + '─' * 55 + '┘')

    def _handle_monitor_dip(self, price):
        st = self.st; st['state_bars'] += 1
        if price < st['dip_price']:
            st['dip_price'] = price
        dip = st['dip_price']
        if dip <= 0: return
        bounce = (price - dip) / dip
        if bounce >= BOUNCE_PCT:
            _log('\n╔' + '═' * 55 + '╗')
            _log('║  ✅ [买入] 反弹{:.3f}% 最低Y{:.2f}→当前Y{:.2f}'.format(
                bounce*100, dip, price).ljust(48) + '║')
            _log('╚' + '═' * 55 + '╝')
            self._execute_buy(price, dip, bounce)
        elif st.get('rsi_zone') == 'neutral' and st['state_bars'] > 10:
            _log(f'[DIP取消] RSI回中性')
            st['fstate'] = STATE_IDLE; st['dip_price'] = 0.0
        elif (self._daily_open - dip)/self._daily_open > STOP_LOSS_PCT and st['state_bars'] > 60:
            _log(f'[DIP止损] 放弃买入')
            st['fstate'] = STATE_DONE

    def _handle_monitor_spike(self, price):
        st = self.st; st['state_bars'] += 1
        if price > st['peak_price']:
            st['peak_price'] = price
        peak = st['peak_price']
        if peak <= 0: return
        pullback = (peak - price) / peak
        if pullback >= PULLBACK_PCT:
            est = (peak - price) * TRADE_LOT_SIZE
            _log('\n╔' + '═' * 55 + '╗')
            _log('║  ✅ [卖出] 回落{:.3f}% 最高Y{:.2f}→当前Y{:.2f} 毛利~Y{:,.0f}'.format(
                pullback*100, peak, price, est).ljust(48) + '║')
            _log('╚' + '═' * 55 + '╝')
            self._execute_sell(price, peak, pullback)
        elif st.get('rsi_zone') == 'neutral' and st['state_bars'] > 10:
            _log(f'[SPIKE取消] RSI回中性')
            st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0

    # ═══════════════════════════════════════════════════════════════
    # 交易执行
    # ═══════════════════════════════════════════════════════════════

    def _snapshot_account(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        shares = can_use = cost = 0
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
                'cost': cost, 'price': price}

    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_cash = snap_after['cash'] - snap_before['cash']
        status = '✅' if d_shares == trade_shares else ('⏳' if d_shares == 0 else f'⚠ Δ{d_shares:+d}')
        _log('  [校验] {}: {}股@Y{:.2f} {} | 持仓{}→{} | 资金Δ{:+,.2f}'.format(
            label, '{:+d}'.format(trade_shares), trade_price, status,
            snap_before['shares'], snap_after['shares'], d_cash))
        self.st['base_shares'] = snap_after['shares']
        self.st['base_can_use'] = snap_after['can_use']
        self.st['base_cost'] = snap_after['cost']

    def _execute_buy(self, price, dip_price, bounce_pct):
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES: self.st['fstate'] = STATE_DONE; return
        need = price * TRADE_LOT_SIZE * 1.01
        if self.st.get('avail_cash', 0) < need:
            _log(f'[买入失败] 资金不足'); self.st['fstate'] = STATE_IDLE; return
        self.st['trade_count'] = tc + 1
        _log(f'[RSI买入 #{tc+1}/{MAX_DAILY_TRADES}] 反弹{bounce_pct*100:.2f}% @ Y{price:.2f}')
        snap = self._snapshot_account()
        if self.dry_run:
            self._verify_trade(snap, '买入(模拟)', price, TRADE_LOT_SIZE)
        else:
            order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            _time.sleep(0.5)
            self._verify_trade(snap, '买入', price, TRADE_LOT_SIZE)
        self.total_trades += 1; self.st['fstate'] = STATE_DONE
        self._maybe_resume()

    def _execute_sell(self, price, peak_price, pullback_pct):
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES: self.st['fstate'] = STATE_DONE; return
        if self.st.get('base_can_use', 0) < TRADE_LOT_SIZE:
            _log(f'[卖出失败] 无可用持仓'); self.st['fstate'] = STATE_IDLE; return
        self.st['trade_count'] = tc + 1
        est = (peak_price - price) * TRADE_LOT_SIZE
        _log(f'[RSI卖出 #{tc+1}/{MAX_DAILY_TRADES}] 回落{pullback_pct*100:.2f}% @ Y{price:.2f} 毛利~Y{est:.0f}')
        snap = self._snapshot_account()
        if self.dry_run:
            self._verify_trade(snap, '卖出(模拟)', price, -TRADE_LOT_SIZE)
        else:
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            _time.sleep(0.5)
            self._verify_trade(snap, '卖出', price, -TRADE_LOT_SIZE)
        self.total_trades += 1; self.total_pnl += est
        self.st['day_pnl'] += est; self.st['fstate'] = STATE_DONE
        self._maybe_resume()

    def _maybe_resume(self):
        st = self.st
        if st.get('trade_count', 0) < MAX_DAILY_TRADES:
            if st.get('rsi_zone') in ('oversold', 'extreme_oversold', 'overbought', 'extreme_overbought'):
                self._refresh_position()
                st['fstate'] = STATE_IDLE
                st['peak_price'] = st['dip_price'] = 0.0
                st['state_bars'] = 0
                _log(f'[恢复] 剩余{MAX_DAILY_TRADES - st["trade_count"]}次 → IDLE')

    def _force_close(self, price, reason='尾盘'):
        fstate = self.st.get('fstate', STATE_IDLE)
        if fstate == STATE_MONITOR_SPIKE:
            _log(f'[{reason}] 强制卖出 @ Y{price:.2f}')
            self.st['fstate'] = STATE_FORCED
            if not self.dry_run:
                snap = self._snapshot_account()
                order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
                _time.sleep(0.5)
                self._verify_trade(snap, f'{reason}强卖', 0, -TRADE_LOT_SIZE)
        elif fstate == STATE_MONITOR_DIP:
            _log(f'[{reason}] 取消买入监控')
            self.st['fstate'] = STATE_FORCED

    # ═══════════════════════════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════════════════════════

    def run(self):
        set_global_conn(self.conn, self.dry_run)

        if not self.dry_run:
            if not self.conn.connect_data(): _log('[错误] 行情连接失败'); return
            if not self.conn.connect_trade(): _log('[错误] 交易连接失败'); self.conn.disconnect(); return
        else:
            if not self.conn.connect_data(): _log('[错误] 行情连接失败'); return
            _log('[信号mode] 行情已连接, 不下单')

        self._init_state()
        _log(f'{STOCK_NAME} RSI策略 v4 启动')
        _log(f'  ★ v4: 修复昨收基线 (剥离今日partial bar)')
        _log(f'  RSI({RSI_PERIOD}) | 超买>{RSI_OVERBOUGHT} | 超卖<{RSI_OVERSOLD}')
        _log(f'  反弹{BOUNCE_PCT*100:.2f}% | 回落{PULLBACK_PCT*100:.2f}%')
        _log(f'  ★ 日志: {get_logger().log_path if get_logger() else "?"}')

        try:
            self._daily_init()
        except Exception as e:
            _log(f'[异常] 初始化失败: {e}')
            _traceback.print_exc()

        _log('开始监控... (Ctrl+C 停止)\n')

        try:
            while self._running:
                now = now_hms()
                now_ts = _time.time()

                if not is_market_open(now):
                    today = datetime.now().strftime('%Y%m%d')
                    if '09:25:00' <= now < '09:30:00':
                        if self.st.get('trade_date', '') != today:
                            try: self._daily_init(); self._last_heartbeat = now_ts
                            except Exception as e: _log(f'[盘前异常] {e}')
                            _time.sleep(5); continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                            _log(f'[盘前] {now} 距开盘 {time_to_open(now)}')
                        _time.sleep(5); continue
                    if self.st.get('trade_date', '') != today:
                        try: self._daily_init()
                        except Exception as e: _log(f'[异常] {e}')
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        _log(f'[休市] {now} | RSI_live={self.st.get("rsi_live",50):.1f}')
                    _time.sleep(10); continue

                # ── 交易时段 ──
                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick: _time.sleep(1); continue

                price = tick[STOCK_QMT].get('lastPrice', 0)
                if price <= 0: _time.sleep(1); continue

                # ★ v4: 实时RSI更新
                live_rsi = self.rsi_engine.update_tick(price)
                live_zone, live_zone_cn = self.rsi_engine._classify_zone(live_rsi)
                self.st['rsi_live'] = live_rsi
                self.st['rsi_zone'] = live_zone
                self.st['rsi_zone_cn'] = live_zone_cn

                fstate = self.st.get('fstate', STATE_IDLE)

                if fstate == STATE_IDLE:
                    self._handle_idle(price)
                elif fstate == STATE_MONITOR_DIP:
                    self._handle_monitor_dip(price)
                elif fstate == STATE_MONITOR_SPIKE:
                    self._handle_monitor_spike(price)
                elif fstate in (STATE_DONE, STATE_FORCED):
                    if (self.st.get('trade_count', 0) < MAX_DAILY_TRADES
                            and now < '14:57:00'
                            and live_zone in ('oversold', 'extreme_oversold',
                                              'overbought', 'extreme_overbought')):
                        self._maybe_resume()

                if ENABLE_FORCE_CLOSE and now >= FORCE_CLOSE_TIME:
                    if fstate in (STATE_MONITOR_SPIKE, STATE_MONITOR_DIP):
                        self._force_close(price, '尾盘')

                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    self._heartbeat(price)

                _time.sleep(2)

        except KeyboardInterrupt: _log('\n用户中断')
        except Exception as e: _log(f'[异常] {e}'); _traceback.print_exc()
        finally:
            self.conn.disconnect()
            _log(f'{STOCK_NAME} RSI策略 v4 已停止 | {self.total_trades}笔 | PnL ~Y{self.total_pnl:,.0f}')
            logger = get_logger()
            if logger: _log('★ 日志: ' + logger.log_path); logger.close()

    # ═══════════════════════════════════════════════════════════════
    # ★ v4 心跳: 含诊断信息
    # ═══════════════════════════════════════════════════════════════

    def _heartbeat(self, price):
        eng = self.rsi_engine
        detail = eng.get_live_detail(price)
        fstate = self.st.get('fstate', STATE_IDLE)
        tc = self.st.get('trade_count', 0)

        if detail is None: _log('[RSI] 未初始化'); return

        parts = [
            f'RSI_live={detail["live_rsi"]:.1f}',
            f'{detail["zone_cn"]}',
            f'vs昨收{detail["today_diff"]:+.2f}({detail["today_diff_pct"]:+.2f}%)',
        ]

        # 与daily对比
        rsi_delta = detail['rsi_delta']
        if abs(rsi_delta) > 0.5:
            parts.append(f'(daily={detail["daily_rsi"]:.1f} Δ={rsi_delta:+.1f})')

        # 状态
        if fstate == STATE_MONITOR_DIP:
            dip = self.st.get('dip_price', 0)
            if dip > 0:
                b = (price - dip)/dip*100
                need = max(0, BOUNCE_PCT*100 - b)
                parts.append(f'DIP:低Y{dip:.2f} 反{b:.2f}% 需{need:.2f}%')
        elif fstate == STATE_MONITOR_SPIKE:
            peak = self.st.get('peak_price', 0)
            if peak > 0:
                pb = (peak - price)/peak*100
                need = max(0, PULLBACK_PCT*100 - pb)
                parts.append(f'SPIKE:高Y{peak:.2f} 回{pb:.2f}% 需{need:.2f}%')
        elif fstate == STATE_IDLE:
            parts.append('⏳')
        elif fstate == STATE_DONE:
            parts.append('✅')

        parts.append(f'Y{price:.2f}')
        parts.append(f'{tc}/{MAX_DAILY_TRADES}笔')

        _log('[RSI] {}'.format(' | '.join(parts)))


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT RSI v4 — 实时RSI + 昨收基线修复',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v4_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v4_miniqmt.py" --mode live

v4 修复 (vs v3):
  ★ xtdata 返回的数据包含今日partial bar (close≈当前价)
  ★ v3 误将 closes[-1] 当作"昨收" → diff≈0 → RSI几乎不动
  ★ v4: 检测并剥离今日bar, 用 closes[-2] 作为真正昨收
  ★ 诊断输出: 明确显示 [诊断] 昨收/今日开盘/当前价
        """
    )
    parser.add_argument('--mode', '-m', default='signal', choices=['signal', 'live'])
    args = parser.parse_args()

    logger = FileLogger(STOCK_CODE, version='RSI_v4')
    set_logger(logger)
    print(f'★ 日志文件: {logger.log_path}')

    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  ⚠ 即将启动实盘自动交易!')
        print(f'  策略: RSI v4 (昨收基线修复 + 实时RSI)')
        print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
        print(f'  RSI({RSI_PERIOD}) | 超买>{RSI_OVERBOUGHT} | 超卖<{RSI_OVERSOLD}')
        print('!' * 55)
        confirm = input('\n确认启动? (输入 yes 继续): ')
        if confirm.strip().lower() != 'yes':
            print('已取消'); logger.close(); return

    runner = RSIStrategyRunner(dry_run=dry_run)
    runner.run()


if __name__ == '__main__':
    main()
