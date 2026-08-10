# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — RSI Intraday Strategy v1 (10-Minute Bars)
================================================================================
 Intraday RSI trading strategy based on 1-minute bar RSI(14).

 RSI Value      Status        Meaning
 > 70           Overbought    Strong upward momentum, pullback risk -> SELL
 30 - 70        Neutral       Normal range -> HOLD / WAIT
 < 30           Oversold      Selling exhausted, bounce opportunity -> BUY

 * Bar Period: 10 minutes
 * RSI Period: 14 bars (140 minutes lookback)
 * ~24 bars per trading day, need ~200 bars history (~8 trading days)

 * Real-time update:
   - RSI baseline from completed 1-min bars
   - Live RSI updated every tick: diff = current_price - last_bar_close
   - Wilder's smoothing recurrence applied incrementally

 * Price-action confirmation:
   - Oversold + dip-bounce -> BUY
   - Overbought + spike-pullback -> SELL

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/RSI_Intraday_Strategy_v1_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/RSI_Intraday_Strategy_v1_miniqmt.py" --mode live

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

from infra.logger import FileLogger, set_logger, get_logger, _log
from infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares,
    set_global_conn,
)


# ============================================================================
# Strategy Parameters
# ============================================================================

ACCOUNT          = '8890145315'
STOCK_CODE       = '601869'
STOCK_NAME       = 'Changfei Fiber'
STOCK_QMT        = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE   = 100
MINIQMT_PATH     = 'C:/QMT/userdata_mini'
SESSION_ID       = 0

# RSI (intraday)
# Supported periods: '1m', '5m', '15m', '30m', '1h'
BAR_PERIOD       = '1m'         # 1-minute bars
RSI_PERIOD       = 6            # RSI lookback: 6 bars = 6 minutes
INTRA_LOOKBACK   = 500          # fetch 500 bars (~2 trading days)
RSI_OVERBOUGHT   = 70
RSI_OVERSOLD     = 30
RSI_EXTREME_HIGH = 80
RSI_EXTREME_LOW  = 20

# Price-action confirmation
BOUNCE_PCT          = 0.0010     # 0.10% bounce = buy trigger
PULLBACK_PCT        = 0.0010     # 0.10% pullback = sell trigger
EMERGENCY_BUYBACK_PCT = 0.02     # 2% emergency buyback
STOP_LOSS_PCT       = 0.02       # 2% stop loss

# Data & fees
COMMISSION       = 0.00025
STAMP_TAX        = 0.001

# Trading
MAX_POSITION_LOTS  = 5
MIN_POSITION_LOTS  = 1
MAX_DAILY_TRADES   = 3
POSITION_PCT_BUY   = 0.30
POSITION_PCT_SELL  = 0.30

# State machine
STATE_IDLE          = 'IDLE'
STATE_MONITOR_DIP   = 'MONITOR_DIP'
STATE_MONITOR_SPIKE = 'MONITOR_SPIKE'
STATE_DONE          = 'DONE'
STATE_FORCED        = 'FORCED'

FORCE_CLOSE_TIME   = '14:57:00'
ENABLE_FORCE_CLOSE = True


# ============================================================================
# RealTimeRSI_Intraday — RSI on 1-min bars with live tick updates
# ============================================================================

class RealTimeRSI_Intraday:
    """
    Real-time RSI calculator for intraday bars (1-minute).

    - RSI baseline: computed from completed 1-min bars via Wilder's smoothing
    - Live RSI: updated every tick using current_price vs last completed bar close
    - Formula: same Wilder recurrence as daily version

    diff = current_price - last_bar_close (close of most recent completed 1-min bar)
    """

    def __init__(self, period=14):
        self.period = period
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self.last_bar_close = 0.0       # close of last COMPLETED 1-min bar
        self.current_rsi = 50.0
        self.bar_rsi = 50.0             # RSI based on completed bars only (static)
        self.rsi_series = []
        self.initialized = False
        self._bar_closes = []           # completed 1-min bar closes
        self._bar_count = 0
        self._today_bar_count = 0

    # -- Full RSI computation on bar closes --

    @staticmethod
    def compute_rsi_full(closes, period=14):
        """Compute full RSI series from close prices (Wilder's smoothing)."""
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

    # -- Init from 1-min bar closes --

    def init_from_bars(self, closes):
        """
        Initialize RSI from completed 1-minute bar closes.

        Args:
            closes: list of close prices from completed 1-min bars
        """
        n = len(closes)
        if n < self.period + 1:
            _log(f'[RSI-10m] Insufficient bars: {n} < {self.period + 1}')
            return False

        self._bar_closes = list(closes)
        self._bar_count = n
        rsi_series, avg_g, avg_l = self.compute_rsi_full(closes, self.period)

        self.rsi_series = rsi_series
        self.avg_gain = avg_g
        self.avg_loss = avg_l
        self.last_bar_close = closes[-1]    # close of most recent completed bar
        self.bar_rsi = rsi_series[-1]
        self.current_rsi = rsi_series[-1]
        self.initialized = True

        _log(f'[RSI-10m] Init: {n} bars ({n * 10 / 60:.1f} trading hours)')
        _log(f'  last_bar_close = Y{self.last_bar_close:.2f}')
        _log(f'  avg_gain = {self.avg_gain:.4f}  avg_loss = {self.avg_loss:.4f}')
        if self.avg_loss > 0:
            _log(f'  RS = {self.avg_gain/self.avg_loss:.4f}')
        _log(f'  bar_RSI (completed bars) = {self.bar_rsi:.2f}')
        return True

    # -- Live RSI update on each tick --

    def update_tick(self, current_price):
        """
        Update live RSI using current price vs last completed bar close.

        This is the intra-bar RSI — it shows what RSI WOULD be if the
        current 1-min bar closed at this price.
        """
        if not self.initialized or self.last_bar_close <= 0:
            return self.current_rsi

        diff = current_price - self.last_bar_close
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        new_avg_g = (self.avg_gain * (self.period - 1) + gain) / self.period
        new_avg_l = (self.avg_loss * (self.period - 1) + loss) / self.period

        if new_avg_l == 0:
            self.current_rsi = 100.0
        else:
            self.current_rsi = 100.0 - 100.0 / (1.0 + new_avg_g / new_avg_l)

        return self.current_rsi

    def get_live_detail(self, current_price):
        """Get detailed live RSI info."""
        if not self.initialized:
            return None

        diff = current_price - self.last_bar_close
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        new_avg_g = (self.avg_gain * (self.period - 1) + gain) / self.period
        new_avg_l = (self.avg_loss * (self.period - 1) + loss) / self.period

        rs = new_avg_g / new_avg_l if new_avg_l > 0 else float('inf')
        zone, zone_label = self._classify_zone(self.current_rsi)

        return {
            'live_rsi': round(self.current_rsi, 2),
            'bar_rsi': round(self.bar_rsi, 2),
            'rsi_delta': round(self.current_rsi - self.bar_rsi, 2),
            'avg_gain': round(new_avg_g, 4),
            'avg_loss': round(new_avg_l, 4),
            'rs': round(rs, 2) if rs != float('inf') else 'inf',
            'today_gain': round(gain, 2),
            'today_loss': round(loss, 2),
            'today_diff': round(diff, 2),
            'today_diff_pct': round(diff / self.last_bar_close * 100, 2) if self.last_bar_close > 0 else 0,
            'last_bar_close': self.last_bar_close,
            'bar_count': self._bar_count,
            'zone': zone,
            'zone_label': zone_label,
        }

    def _classify_zone(self, rsi_val):
        if rsi_val > RSI_EXTREME_HIGH:
            return 'extreme_overbought', 'EXTREME OB'
        elif rsi_val > RSI_OVERBOUGHT:
            return 'overbought', 'OVERBOUGHT'
        elif rsi_val < RSI_EXTREME_LOW:
            return 'extreme_oversold', 'EXTREME OS'
        elif rsi_val < RSI_OVERSOLD:
            return 'oversold', 'OVERSOLD'
        else:
            return 'neutral', 'NEUTRAL'

    def zone_bar(self, rsi_val=None):
        if rsi_val is None:
            rsi_val = self.current_rsi
        bar_len = 50
        pos = max(0, min(bar_len, int(rsi_val / 100 * bar_len)))
        os_mark = int(30 / 100 * bar_len)
        ob_mark = int(70 / 100 * bar_len)
        chars = []
        for i in range(bar_len + 1):
            if i == pos: chars.append('v')
            elif i == os_mark or i == ob_mark: chars.append('|')
            elif i < os_mark: chars.append('.')
            elif i < ob_mark: chars.append('-')
            else: chars.append('#')
        return '0 ' + ''.join(chars) + ' 100'

    def get_bar_changes(self, lookback=None):
        """Get recent bar-by-bar changes for display."""
        closes = self._bar_closes
        if lookback is None:
            lookback = min(self.period, len(closes))
        n = len(closes)
        changes = []
        start = max(1, n - lookback - 1)
        for i in range(start, n):
            chg = closes[i] - closes[i - 1]
            chg_pct = (chg / closes[i - 1]) * 100 if closes[i - 1] > 0 else 0
            changes.append({
                'bar_offset': n - 1 - i,
                'close': closes[i],
                'change': round(chg, 2),
                'change_pct': round(chg_pct, 2),
            })
        return changes[-lookback:] if len(changes) >= lookback else changes


# ============================================================================
# Utility Functions
# ============================================================================

def now_hms():
    return _time.strftime('%H:%M:%S')


def now_minute():
    """Return current minute of trading day (for 1-min bar tracking)."""
    now = datetime.now()
    return now.hour * 60 + now.minute


def is_market_open(now_str=None):
    if now_str is None: now_str = now_hms()
    return ('09:30:00' <= now_str <= '11:30:00') or ('13:00:00' <= now_str <= '15:00:00')


def time_to_open(now_str):
    h, m, s = int(now_str[:2]), int(now_str[3:5]), int(now_str[6:8])
    now_secs = h * 3600 + m * 60 + s
    if now_str < '09:30:00': target = 9 * 3600 + 30 * 60
    elif '11:30:00' < now_str < '13:00:00': target = 13 * 3600
    else: return '--'
    remain = target - now_secs
    if remain > 3600: return f'{remain // 3600}h{(remain % 3600) // 60}m'
    elif remain > 60: return f'{remain // 60}m{remain % 60}s'
    else: return f'{remain}s'


# ============================================================================
# RSI Intraday Strategy Runner v1
# ============================================================================

class RSIIntradayRunner:
    """MiniQMT RSI Intraday v1 -- 1-minute bar RSI"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='RSI_10m_v1')
            set_logger(logger)
        self.conn = MiniQMTConnector()
        set_global_conn(self.conn, dry_run)
        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = dry_run
        self._last_heartbeat = 0.0
        self._last_bar_refresh = 0.0
        self._running = True
        self.total_trades = 0
        self.total_pnl = 0.0
        self.win_count = 0
        self.loss_count = 0
        self.rsi_engine = RealTimeRSI_Intraday(period=RSI_PERIOD)
        self._daily_open = 0.0
        self._current_bar_minute = 0   # track which 1-min bar we're in

    def _init_state(self):
        self.st.update({
            'trade_date': '', 'initialized': False,
            'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'avail_cash': 0.0, 'pos_value': 0.0, 'pos_pct': 0.0, 'total_asset': 0.0,
            'daily_open': 0.0, 'last_bar_close': 0.0,
            'rsi_live': 50.0, 'rsi_bar': 50.0,
            'rsi_zone': 'neutral', 'rsi_zone_label': 'NEUTRAL',
            'fstate': STATE_IDLE,
            'peak_price': 0.0, 'dip_price': 0.0,
            'trigger_price': 0.0, 'fill_price': 0.0,
            'state_entered_at': '', 'state_bars': 0,
            'trade_count': 0, 'day_pnl': 0.0, 'stop_loss_hit': False,
            'bar_count': 0,
        })

    def _reset_daily(self):
        saved = {k: self.st.get(k, 0) for k in ('base_shares', 'base_can_use', 'base_cost')}
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

    # -- Init: fetch 1-min bars and build RSI --

    def _init_intraday(self):
        """Fetch 1-min bar data and initialize RSI engine."""
        today = datetime.now().strftime('%Y%m%d')

        _log('')
        _log('=' * 55)
        _log('  RSI Intraday v1 Init ({})'.format(BAR_PERIOD))
        _log('=' * 55)

        # Fetch intraday bars via connector
        data = self.conn.get_intraday_bars(period=BAR_PERIOD, count=INTRA_LOOKBACK)
        if data is None or len(data.get('close', [])) < RSI_PERIOD + 1:
            _log('[RSI-10m] Insufficient intraday data from xtdata')
            _log('[RSI-10m] Make sure QMT has downloaded 1-min data for 601869')
            return False

        closes = data['close']
        _log(f'  Fetched {len(closes)} {BAR_PERIOD} bars from xtdata')

        # Count today's bars
        today_bars = 0
        for c in closes[-30:]:  # rough: check if any bars are from today
            pass  # xtdata index dates not easily accessible; skip precise count

        ok = self.rsi_engine.init_from_bars(closes)
        if not ok:
            return False

        bar_rsi = self.rsi_engine.bar_rsi
        last_bar_close = self.rsi_engine.last_bar_close

        # Tick data for current price
        tick = self.conn.get_full_tick([STOCK_QMT])
        tick_data = tick.get(STOCK_QMT, {})
        today_open = tick_data.get('open', 0)
        curr_price = tick_data.get('lastPrice', 0)
        if curr_price <= 0:
            curr_price = last_bar_close
        self._daily_open = today_open if today_open > 0 else closes[-1]

        # First live update
        live_rsi = self.rsi_engine.update_tick(curr_price)
        live_zone, live_zone_label = self.rsi_engine._classify_zone(live_rsi)
        detail = self.rsi_engine.get_live_detail(curr_price)

        _log(f'  tick.open      = Y{today_open:.2f}')
        _log(f'  tick.lastPrice = Y{curr_price:.2f}')
        _log(f'  -> live RSI    = {live_rsi:.2f}  bar_RSI = {bar_rsi:.2f}')
        _log('')

        self.st.update({
            'initialized': True,
            'daily_open': self._daily_open,
            'last_bar_close': last_bar_close,
            'rsi_live': live_rsi, 'rsi_bar': bar_rsi,
            'rsi_zone': live_zone, 'rsi_zone_label': live_zone_label,
            'bar_count': len(closes),
        })

        # Print
        self._print_rsi_calc(curr_price, live_rsi)
        self._print_trading_plan(curr_price)

        return True

    # -- Periodic refresh: refetch bars every N minutes to get newly completed bars --

    def _refresh_bars(self):
        """Refetch 1-min bars to pick up newly completed bars."""
        data = self.conn.get_intraday_bars(period=BAR_PERIOD, count=INTRA_LOOKBACK)
        if data is None:
            return

        closes = data['close']
        if len(closes) <= self.rsi_engine._bar_count:
            return  # no new bars yet

        # New bars available — reinitialize
        _log(f'[RSI-10m] New bars: {len(closes)} (was {self.rsi_engine._bar_count})')
        self.rsi_engine.init_from_bars(closes)
        self.st['bar_count'] = len(closes)
        self.st['rsi_bar'] = self.rsi_engine.bar_rsi
        self.st['last_bar_close'] = self.rsi_engine.last_bar_close

    # =======================================================================
    # Output
    # =======================================================================

    def _print_rsi_calc(self, curr_price, live_rsi):
        eng = self.rsi_engine
        bar_rsi = eng.bar_rsi
        detail = eng.get_live_detail(curr_price)

        _log('')
        _log('+' + '-' * 62 + '+')
        _log('|  RSI(14) on {} bars -- {}  {}'.format(
            BAR_PERIOD, STOCK_NAME, self.st.get('trade_date', '')).ljust(53) + '|')
        _log('|' + '-' * 62 + '|')
        _log('|  last_bar_close = Y{:.2f}  |  current = Y{:.2f}'.format(
            eng.last_bar_close, curr_price).ljust(53) + '|')
        if detail:
            _log('|  Intra-bar change: Y{:+.2f} ({:+.2f}%)'.format(
                detail['today_diff'], detail['today_diff_pct']).ljust(53) + '|')

        _log('|' + '-' * 62 + '|')
        # Show last N bar changes (compressed)
        changes = eng.get_bar_changes(min(14, len(eng._bar_closes)))
        _log('|  Last {:d} bar changes ({} bars):'.format(len(changes), BAR_PERIOD).ljust(53) + '|')
        _log('|  {:>4s}  {:>10s}  {:>10s}  {:>12s}  |'.format('Bar', 'Close', 'Change', 'Change%'))
        for ch in changes:
            arrow = '+' if ch['change'] > 0 else ('-' if ch['change'] < 0 else '0')
            _log('|  {:>3d}  Y{:>10.2f}  {:>+10.2f}  {:>+11.2f}%  {} |'.format(
                ch['bar_offset'], ch['close'], ch['change'], ch['change_pct'], arrow))

        _log('|' + '-' * 62 + '|')
        _log('|  Historical avg_gain={:.4f}  avg_loss={:.4f}'.format(
            eng.avg_gain, eng.avg_loss).ljust(53) + '|')
        if detail:
            _log('|  Live avg_gain={:.4f}  avg_loss={:.4f}  RS={}'.format(
                detail['avg_gain'], detail['avg_loss'], detail['rs']).ljust(53) + '|')
            _log('|  -> RSI_live = **{:.2f}**  (bar_RSI={:.2f})'.format(live_rsi, bar_rsi).ljust(53) + '|')

        _log('|' + '-' * 62 + '|')
        zone_bar = eng.zone_bar(live_rsi)
        _, zone_label = eng._classify_zone(live_rsi)
        _log('|  {} |'.format(zone_bar))
        _log('|  RSI_live={:.1f} | bar_RSI={:.1f} | delta={:+.1f} | {}'.format(
            live_rsi, bar_rsi, live_rsi - bar_rsi, zone_label).ljust(53) + '|')
        dist_ob = RSI_OVERBOUGHT - live_rsi
        dist_os = live_rsi - RSI_OVERSOLD
        _log('|  Dist to OB(70): {:+.1f}  |  Dist to OS(30): {:+.1f}'.format(dist_ob, dist_os).ljust(53) + '|')
        _log('+' + '-' * 62 + '+')
        _log('')

    def _print_trading_plan(self, curr_price):
        eng = self.rsi_engine
        detail = eng.get_live_detail(curr_price)
        if detail is None: return
        live_rsi = detail['live_rsi']
        rsi_zone = detail['zone']
        zone_label = detail['zone_label']
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        avail_cash = self.st.get('avail_cash', 0)

        _log('+' + '-' * 62 + '+')
        _log('|  RSI-10m Trading Plan -- {}  {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', ''), zone_label).ljust(53) + '|')
        _log('|  RSI_live={:.1f}  bar_RSI={:.1f}  bars={}'.format(
            live_rsi, detail['bar_rsi'], detail['bar_count']).ljust(53) + '|')
        _log('|' + '-' * 62 + '|')
        _log('|  Open: Y{:.2f}  Current: Y{:.2f}  ({:+.2f}%)'.format(
            self._daily_open, curr_price,
            (curr_price/self._daily_open - 1)*100 if self._daily_open > 0 else 0).ljust(53) + '|')
        _log('|  Position: {:>5} sh  Avail Cash: Y{:>12,.0f}'.format(
            base_shares, avail_cash).ljust(53) + '|')
        _log('|' + '-' * 62 + '|')

        if rsi_zone in ('oversold', 'extreme_oversold'):
            _log('|  **OVERSOLD** -> Dip-Bounce Buy (bounce >= {:.2f}%)'.format(BOUNCE_PCT*100).ljust(53) + '|')
        elif rsi_zone in ('overbought', 'extreme_overbought'):
            _log('|  **OVERBOUGHT** -> Spike-Pullback Sell (pullback >= {:.2f}%)'.format(PULLBACK_PCT*100).ljust(53) + '|')
        else:
            _log('|  **NEUTRAL** -- Waiting for OB/OS signal'.ljust(53) + '|')

        _log('|' + '-' * 62 + '|')
        _log('|  Params: {}-bar RSI({}) | OB>{} | OS<{} | Max {} trades/day'.format(
            BAR_PERIOD, RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD, MAX_DAILY_TRADES).ljust(53) + '|')
        _log('+' + '-' * 62 + '+')
        _log('')

    # =======================================================================
    # State Machine (same pattern as daily RSI)
    # =======================================================================

    def _handle_idle(self, price):
        st = self.st
        if st.get('trade_count', 0) >= MAX_DAILY_TRADES:
            st['fstate'] = STATE_DONE; return
        rsi_zone = st.get('rsi_zone', 'neutral')
        if rsi_zone in ('oversold', 'extreme_oversold'):
            st['fstate'] = STATE_MONITOR_DIP
            st['dip_price'] = price; st['peak_price'] = 0.0
            st['state_entered_at'] = now_hms(); st['state_bars'] = 0
            _log('[ACTIVATE] Dip-Bounce Buy Monitor (RSI_live={:.1f})'.format(st['rsi_live']))
        elif rsi_zone in ('overbought', 'extreme_overbought'):
            st['fstate'] = STATE_MONITOR_SPIKE
            st['peak_price'] = price; st['dip_price'] = 0.0
            st['state_entered_at'] = now_hms(); st['state_bars'] = 0
            _log('[ACTIVATE] Spike-Pullback Sell Monitor (RSI_live={:.1f})'.format(st['rsi_live']))

    def _handle_monitor_dip(self, price):
        st = self.st; st['state_bars'] += 1
        if price < st['dip_price']: st['dip_price'] = price
        dip = st['dip_price']
        if dip <= 0: return
        bounce = (price - dip) / dip
        if bounce >= BOUNCE_PCT:
            _log('[BUY CONFIRMED] Bounce {:.3f}%  Low Y{:.2f} -> Y{:.2f}'.format(bounce*100, dip, price))
            self._execute_buy(price, dip, bounce)
        elif st.get('rsi_zone') == 'neutral' and st['state_bars'] > 10:
            _log('[DIP CANCELLED] RSI back to neutral'); st['fstate'] = STATE_IDLE; st['dip_price'] = 0.0

    def _handle_monitor_spike(self, price):
        st = self.st; st['state_bars'] += 1
        if price > st['peak_price']: st['peak_price'] = price
        peak = st['peak_price']
        if peak <= 0: return
        pullback = (peak - price) / peak
        if pullback >= PULLBACK_PCT:
            est = (peak - price) * TRADE_LOT_SIZE
            _log('[SELL CONFIRMED] Pullback {:.3f}%  High Y{:.2f} -> Y{:.2f}  PnL ~Y{:,.0f}'.format(pullback*100, peak, price, est))
            self._execute_sell(price, peak, pullback)
        elif st.get('rsi_zone') == 'neutral' and st['state_bars'] > 10:
            _log('[SPIKE CANCELLED] RSI back to neutral'); st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0

    # =======================================================================
    # Trade Execution
    # =======================================================================

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
        return {'shares': shares, 'can_use': can_use, 'cash': cash, 'cost': cost, 'price': price}

    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_cash = snap_after['cash'] - snap_before['cash']
        status = 'FILLED' if d_shares == trade_shares else ('PENDING' if d_shares == 0 else f'PARTIAL d{d_shares:+d}')
        _log('  [Verify] {}: {}sh@Y{:.2f} {} | pos {}->{} | cash d{:+,.2f}'.format(
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
            _log('[BUY FAILED] Insufficient cash'); self.st['fstate'] = STATE_IDLE; return
        self.st['trade_count'] = tc + 1
        _log('[BUY #{}/{}] Bounce {:.2f}% @ Y{:.2f}'.format(tc+1, MAX_DAILY_TRADES, bounce_pct*100, price))
        snap = self._snapshot_account()
        if self.dry_run:
            self._verify_trade(snap, 'BUY(sim)', price, TRADE_LOT_SIZE)
        else:
            order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            _time.sleep(0.5)
            self._verify_trade(snap, 'BUY', price, TRADE_LOT_SIZE)
        self.total_trades += 1; self.st['fstate'] = STATE_DONE
        self._maybe_resume()

    def _execute_sell(self, price, peak_price, pullback_pct):
        tc = self.st.get('trade_count', 0)
        if tc >= MAX_DAILY_TRADES: self.st['fstate'] = STATE_DONE; return
        if self.st.get('base_can_use', 0) < TRADE_LOT_SIZE:
            _log('[SELL FAILED] No available position'); self.st['fstate'] = STATE_IDLE; return
        self.st['trade_count'] = tc + 1
        est = (peak_price - price) * TRADE_LOT_SIZE
        _log('[SELL #{}/{}] Pullback {:.2f}% @ Y{:.2f} PnL ~Y{:.0f}'.format(tc+1, MAX_DAILY_TRADES, pullback_pct*100, price, est))
        snap = self._snapshot_account()
        if self.dry_run:
            self._verify_trade(snap, 'SELL(sim)', price, -TRADE_LOT_SIZE)
        else:
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            _time.sleep(0.5)
            self._verify_trade(snap, 'SELL', price, -TRADE_LOT_SIZE)
        self.total_trades += 1; self.total_pnl += est
        self.st['day_pnl'] += est; self.st['fstate'] = STATE_DONE
        self._maybe_resume()

    def _maybe_resume(self):
        st = self.st
        if st.get('trade_count', 0) < MAX_DAILY_TRADES:
            if st.get('rsi_zone') in ('oversold', 'extreme_oversold', 'overbought', 'extreme_overbought'):
                self._refresh_position()
                st['fstate'] = STATE_IDLE
                st['peak_price'] = st['dip_price'] = 0.0; st['state_bars'] = 0
                _log('[Resume] {} trades left -> IDLE'.format(MAX_DAILY_TRADES - st['trade_count']))

    def _force_close(self, price, reason='EOD'):
        fstate = self.st.get('fstate', STATE_IDLE)
        if fstate == STATE_MONITOR_SPIKE:
            _log('[{}] Force sell @ Y{:.2f}'.format(reason, price))
            self.st['fstate'] = STATE_FORCED
            if not self.dry_run:
                snap = self._snapshot_account()
                order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
                _time.sleep(0.5)
                self._verify_trade(snap, f'{reason}_force_sell', 0, -TRADE_LOT_SIZE)
        elif fstate == STATE_MONITOR_DIP:
            _log('[{}] Cancel buy monitor'.format(reason))
            self.st['fstate'] = STATE_FORCED

    # =======================================================================
    # Main Loop
    # =======================================================================

    def run(self):
        set_global_conn(self.conn, self.dry_run)
        if not self.dry_run:
            if not self.conn.connect_data(): _log('[Error] Data connection failed'); return
            if not self.conn.connect_trade(): _log('[Error] Trade connection failed'); self.conn.disconnect(); return
        else:
            if not self.conn.connect_data(): _log('[Error] Data connection failed'); return
            _log('[signal mode] Data connected, no orders')

        self._init_state()
        today = datetime.now().strftime('%Y%m%d')
        self.st['trade_date'] = today

        _log('{} RSI Intraday v1 Started ({})'.format(STOCK_NAME, BAR_PERIOD))
        _log('  Bar period: {} | RSI({}) | OB > {} | OS < {}'.format(BAR_PERIOD, RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD))
        _log('  * Log: {}'.format(get_logger().log_path if get_logger() else '?'))

        if not self._init_intraday():
            _log('[Error] Failed to initialize intraday RSI')
            return

        _log('Monitoring... (Ctrl+C to stop)\n')

        try:
            while self._running:
                now = now_hms(); now_ts = _time.time()

                if not is_market_open(now):
                    if '09:25:00' <= now < '09:30:00':
                        if self.st.get('trade_date', '') != today:
                            self._init_intraday()
                        _time.sleep(5); continue
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                    _time.sleep(10); continue

                # Tick data
                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick: _time.sleep(1); continue
                price = tick[STOCK_QMT].get('lastPrice', 0)
                if price <= 0: _time.sleep(1); continue

                # Refresh bars every 2 minutes to catch newly completed 1-min bars
                if now_ts - self._last_bar_refresh >= 120:
                    self._last_bar_refresh = now_ts
                    self._refresh_bars()

                # Live RSI update
                live_rsi = self.rsi_engine.update_tick(price)
                live_zone, live_zone_label = self.rsi_engine._classify_zone(live_rsi)
                self.st['rsi_live'] = live_rsi
                self.st['rsi_zone'] = live_zone
                self.st['rsi_zone_label'] = live_zone_label

                fstate = self.st.get('fstate', STATE_IDLE)
                if fstate == STATE_IDLE: self._handle_idle(price)
                elif fstate == STATE_MONITOR_DIP: self._handle_monitor_dip(price)
                elif fstate == STATE_MONITOR_SPIKE: self._handle_monitor_spike(price)
                elif fstate in (STATE_DONE, STATE_FORCED):
                    if (self.st.get('trade_count', 0) < MAX_DAILY_TRADES
                            and now < '14:57:00'
                            and live_zone in ('oversold', 'extreme_oversold',
                                              'overbought', 'extreme_overbought')):
                        self._maybe_resume()

                if ENABLE_FORCE_CLOSE and now >= FORCE_CLOSE_TIME:
                    if fstate in (STATE_MONITOR_SPIKE, STATE_MONITOR_DIP):
                        self._force_close(price, 'EOD')

                # Tick output every second
                self._tick_output(price)

                _time.sleep(1)

        except KeyboardInterrupt: _log('\nUser interrupt')
        except Exception as e: _log('[Exception] {}'.format(e)); _traceback.print_exc()
        finally:
            self.conn.disconnect()
            _log('{} RSI Intraday v1 Stopped | {} trades | PnL ~Y{:,.0f}'.format(
                STOCK_NAME, self.total_trades, self.total_pnl))
            logger = get_logger()
            if logger: _log('* Log: ' + logger.log_path); logger.close()

    def _tick_output(self, price):
        """Per-tick output: RSI, price, next action plan."""
        eng = self.rsi_engine
        detail = eng.get_live_detail(price)
        fstate = self.st.get('fstate', STATE_IDLE)
        tc = self.st.get('trade_count', 0)
        if detail is None: return

        rsi = detail['live_rsi']
        zone = detail['zone']

        # Build next-action plan based on current state
        if fstate == STATE_IDLE:
            if zone in ('oversold', 'extreme_oversold'):
                plan = f'BUY_SETUP: waiting for dip&bounce>={BOUNCE_PCT*100:.2f}%'
            elif zone in ('overbought', 'extreme_overbought'):
                plan = f'SELL_SETUP: waiting for spike&pullback>={PULLBACK_PCT*100:.2f}%'
            else:
                dist_ob = RSI_OVERBOUGHT - rsi
                dist_os = rsi - RSI_OVERSOLD
                if rsi > 55:
                    plan = f'HOLD: {dist_ob:+.1f}pts to OB(70)'
                elif rsi < 45:
                    plan = f'HOLD: {dist_os:+.1f}pts to OS(30)'
                else:
                    plan = 'HOLD: mid-range'

        elif fstate == STATE_MONITOR_DIP:
            dip = self.st.get('dip_price', price)
            bounce = (price - dip) / dip * 100 if dip > 0 else 0
            need = max(0, BOUNCE_PCT * 100 - bounce)
            plan = f'BUY_MON: dip=Y{dip:.2f} bounce={bounce:.3f}% need={need:.3f}%'

        elif fstate == STATE_MONITOR_SPIKE:
            peak = self.st.get('peak_price', price)
            pb = (peak - price) / peak * 100 if peak > 0 else 0
            need = max(0, PULLBACK_PCT * 100 - pb)
            plan = f'SELL_MON: peak=Y{peak:.2f} pullback={pb:.3f}% need={need:.3f}%'

        elif fstate == STATE_DONE:
            plan = f'DONE: {tc}/{MAX_DAILY_TRADES} trades completed today'

        else:
            plan = f'{fstate}'

        _log(f'Y{price:.2f} | RSI={rsi:.1f}(bar={detail["bar_rsi"]:.1f}) | {zone} | {plan} | trades={tc}/{MAX_DAILY_TRADES}')


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT RSI Intraday v1 -- 1-minute bar RSI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
  python "Stragety/MiniQMT_Stragety/RSI_Intraday_Strategy_v1_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/RSI_Intraday_Strategy_v1_miniqmt.py" --mode live

RSI Intraday Strategy:
  Bar period: 10 minutes
  RSI period: 14 bars (140 minutes lookback)
  ~24 bars per trading day, ~200 bars history (~8 trading days)
  Live RSI updates every tick during current 1-min bar formation
  Price-action confirmation: dip-bounce buy, spike-pullback sell

Prerequisites:
  1. Start MiniQMT
  2. Download 1-minute historical data for 601869 in QMT
        """)
    parser.add_argument('--mode', '-m', default='signal', choices=['signal', 'live'])
    args = parser.parse_args()
    logger = FileLogger(STOCK_CODE, version='RSI_10m_v1')
    set_logger(logger)
    print(f'* Log file: {logger.log_path}')
    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  WARNING: About to start LIVE auto-trading!')
        print(f'  Strategy: RSI Intraday v1 ({BAR_PERIOD} bars)')
        print(f'  Symbol: {STOCK_NAME}({STOCK_CODE})')
        print('!' * 55)
        confirm = input('\nConfirm? (type yes to proceed): ')
        if confirm.strip().lower() != 'yes': print('Cancelled'); logger.close(); return
    runner = RSIIntradayRunner(dry_run=dry_run)
    runner.run()


if __name__ == '__main__':
    main()
