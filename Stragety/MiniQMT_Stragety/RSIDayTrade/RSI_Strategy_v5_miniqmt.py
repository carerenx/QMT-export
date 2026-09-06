# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — RSI Trading Strategy v6 (Real-time RSI + tick.lastClose baseline)
================================================================================
 Intraday trading strategy based on RSI(14) relative strength index.

 RSI Value      Status        Meaning
 > 70           Overbought    Strong upward momentum, price may be overheated,
                              pullback risk → SELL signal
 30 - 70        Neutral       Normal trading range → HOLD / WAIT
 < 30           Oversold      Selling pressure exhausted, price may be oversold,
                              bounce opportunity → BUY signal

 * v6 fix (vs v5):
   v5 compared closes[-1] to tick.lastClose to decide whether to strip
   "today's partial bar". This breaks when xtdata daily data is stale
   (e.g. QMT hasn't downloaded recent days). The gap triggers stripping
   incorrectly, corrupting the RSI baseline.

   v6: removed all stripping logic. Always compute RSI on all available
   closes. Always use tick.lastClose as prev_close for live RSI updates.
   The live RSI (diff = current_price - tick.lastClose) is correct
   regardless of daily data freshness. Warns if xtdata data appears stale.

 * v3 retained: RealTimeRSI — tick-by-tick incremental updates
 * v2 retained: Price-action confirmation (dip-bounce buy + spike-pullback sell)

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v5_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/RSI_Strategy_v5_miniqmt.py" --mode live

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
# Strategy Parameters
# ============================================================================

ACCOUNT          = '8890145315'
STOCK_CODE       = '601869'
STOCK_NAME       = 'Changfei Fiber'
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

# Price-action confirmation
BOUNCE_PCT          = 0.0010
PULLBACK_PCT        = 0.0010
EMERGENCY_BUYBACK_PCT = 0.03
STOP_LOSS_PCT       = 0.03

# Data & fees
HIST_DATA_LEN    = 80
COMMISSION       = 0.00025
STAMP_TAX        = 0.001

# Trading parameters
MAX_POSITION_LOTS  = 5
MIN_POSITION_LOTS  = 1
MAX_DAILY_TRADES   = 2
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
# * v5: RealTimeRSI — prev_close from tick.lastClose (single source of truth)
# ============================================================================

class RealTimeRSI:
    """
    Real-time RSI calculator — Wilder's smoothing recurrence + tick-by-tick updates.

    * v5: prev_close obtained from tick.lastClose (exchange official prev close).
      Does NOT depend on whether xtdata daily bars include today's partial bar.

    Formula:
      avg_gain[t] = (avg_gain[t-1] * (period-1) + gain[t]) / period
      avg_loss[t] = (avg_loss[t-1] * (period-1) + loss[t]) / period
      RS = avg_gain / avg_loss
      RSI = 100 - 100 / (1 + RS)
    """

    def __init__(self, period=14):
        self.period = period
        self.avg_gain = 0.0          # Wilder-smoothed avg_gain final value (from daily history)
        self.avg_loss = 0.0          # Wilder-smoothed avg_loss final value
        self.prev_close = 0.0        # * Yesterday's close (from tick.lastClose)
        self.current_rsi = 50.0
        self.daily_rsi = 50.0        # Static RSI (based on daily history only)
        self.rsi_series = []
        self.initialized = False
        self._daily_closes = []

    # -- Full RSI computation --

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

    # -- * v5/v6: init — prev_close always from tick.lastClose, no stripping --

    def init_from_daily(self, closes, yesterday_close):
        """
        Initialize from daily close price series.

        * v6 fix: removed the fragile "detect and strip today bar" logic.
          xtdata daily data may be stale (missing recent days). Comparing
          closes[-1] to tick.lastClose fails when data is not up-to-date.
          Instead: always compute RSI on ALL available closes, always use
          tick.lastClose as prev_close for live RSI updates. The live RSI
          formula (diff = current_price - tick.lastClose) is correct
          regardless of daily data freshness.

        Args:
          closes: daily close price series (as-is from xtdata)
          yesterday_close: exchange official prev close (from tick.lastClose)
        """
        n = len(closes)
        if n < self.period + 1:
            _log(f'[RealTimeRSI] Insufficient data: {n} days < {self.period + 1} days')
            return False

        # * v6: use all closes as-is — no stripping
        self._daily_closes = list(closes)
        self._yesterday_close = yesterday_close

        # Check how stale the xtdata daily data is
        last_data_close = closes[-1]
        gap_pct = abs(last_data_close - yesterday_close) / yesterday_close if yesterday_close > 0 else 0
        data_fresh = (gap_pct < 0.005)  # within 0.5% = data is current

        rsi_series, avg_g, avg_l = self.compute_rsi_full(closes, self.period)

        self.rsi_series = rsi_series
        self.avg_gain = avg_g
        self.avg_loss = avg_l
        self.prev_close = yesterday_close     # * from tick.lastClose (always correct)
        self.daily_rsi = rsi_series[-1]
        self.current_rsi = rsi_series[-1]
        self.initialized = True

        _log(f'[RealTimeRSI] Init complete: {n} daily bars from xtdata')
        _log(f'  xtdata last bar close = Y{last_data_close:.2f}')
        _log(f'  tick.lastClose        = Y{yesterday_close:.2f}')
        if not data_fresh:
            _log(f'  ** WARNING: xtdata daily data appears stale (gap {gap_pct*100:.2f}%)')
            _log(f'  ** RSI baseline from xtdata, but live RSI uses correct tick.lastClose')
        _log(f'  prev_close (tick.lastClose) = Y{self.prev_close:.2f}')
        _log(f'  avg_gain={self.avg_gain:.4f}  avg_loss={self.avg_loss:.4f}')
        if self.avg_loss > 0:
            _log(f'  RS={self.avg_gain/self.avg_loss:.4f}')
        _log(f'  RSI_daily (xtdata baseline) = {self.daily_rsi:.2f}')
        _log(f'  -> Live RSI: diff = current_price - Y{self.prev_close:.2f}(tick.lastClose)')

        return True

    # -- Tick-by-tick real-time update --

    def update_tick(self, current_price):
        """
        Update RSI in real-time using current price.

        diff = current_price - prev_close (tick.lastClose)
        """
        if not self.initialized or self.prev_close <= 0:
            return self.current_rsi

        diff = current_price - self.prev_close
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
        """Get detailed live RSI info (for heartbeat printing)."""
        if not self.initialized:
            return None

        diff = current_price - self.prev_close
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        new_avg_g = (self.avg_gain * (self.period - 1) + gain) / self.period
        new_avg_l = (self.avg_loss * (self.period - 1) + loss) / self.period

        rs = new_avg_g / new_avg_l if new_avg_l > 0 else float('inf')
        zone, zone_label = self._classify_zone(self.current_rsi)

        return {
            'live_rsi': round(self.current_rsi, 2),
            'daily_rsi': round(self.daily_rsi, 2),
            'rsi_delta': round(self.current_rsi - self.daily_rsi, 2),
            'avg_gain': round(new_avg_g, 4),
            'avg_loss': round(new_avg_l, 4),
            'rs': round(rs, 2) if rs != float('inf') else '\u221e',
            'today_gain': round(gain, 2),
            'today_loss': round(loss, 2),
            'today_diff': round(diff, 2),
            'today_diff_pct': round(diff / self.prev_close * 100, 2) if self.prev_close > 0 else 0,
            'prev_close': self.prev_close,
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
            if i == pos: chars.append('\u25bc')
            elif i == os_mark or i == ob_mark: chars.append('\u2503')
            elif i < os_mark: chars.append('\u2591')
            elif i < ob_mark: chars.append('\u2500')
            else: chars.append('\u2593')
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
        if len(self.rsi_series) < 2: return ''
        prev = self.rsi_series[-2]; curr = self.daily_rsi
        signals = []
        if prev < RSI_OVERSOLD and curr >= RSI_OVERSOLD:
            signals.append('CROSS_UP_30 (exit oversold)')
        if prev > RSI_OVERBOUGHT and curr <= RSI_OVERBOUGHT:
            signals.append('CROSS_DOWN_70 (exit overbought)')
        if prev < RSI_OVERBOUGHT and curr >= RSI_OVERBOUGHT:
            signals.append('CROSS_UP_70 (enter overbought)')
        if prev > RSI_OVERSOLD and curr <= RSI_OVERSOLD:
            signals.append('CROSS_DOWN_30 (enter oversold)')
        return ' | '.join(signals) if signals else ''


# ============================================================================
# Utility Functions
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
# RSI Strategy Runner v5
# ============================================================================

class RSIStrategyRunner:
    """MiniQMT RSI v5 — tick.lastClose as prev_close"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='RSI_v5')
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

    def _init_state(self):
        self.st.update({
            'trade_date': '', 'initialized': False,
            'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'avail_cash': 0.0, 'pos_value': 0.0, 'pos_pct': 0.0, 'total_asset': 0.0,
            'daily_open': 0.0, 'yesterday_close': 0.0,
            'rsi_live': 50.0, 'rsi_daily': 50.0,
            'rsi_zone': 'neutral', 'rsi_zone_label': 'NEUTRAL',
            'fstate': STATE_IDLE,
            'peak_price': 0.0, 'dip_price': 0.0,
            'trigger_price': 0.0, 'fill_price': 0.0,
            'state_entered_at': '', 'state_bars': 0,
            'trade_count': 0, 'day_pnl': 0.0, 'stop_loss_hit': False,
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

    # -- * v5: Daily init — get prev_close from tick.lastClose --

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if self.st.get('trade_date', '') == today and self.st.get('initialized', False):
            self._refresh_position()
            return

        is_new_day = self.st.get('trade_date', '') and self.st['trade_date'] != today
        if is_new_day:
            _log(f'\n[New Trading Day] {self.st["trade_date"]} -> {today}')

        self._reset_daily()
        self.st['trade_date'] = today

        # Fetch daily history
        hist_close = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'close')
        hist_open  = self.ctx.get_history_data(HIST_DATA_LEN, '1d', 'open')

        if STOCK_QMT not in hist_close or len(hist_close[STOCK_QMT]) < RSI_PERIOD + 2:
            _log(f'[Warning] Insufficient daily data (need >= {RSI_PERIOD+2} days), skipping today')
            return

        closes = list(hist_close[STOCK_QMT])
        opens  = list(hist_open[STOCK_QMT])

        # * v5: get exchange official prev close from tick data
        tick = self.ctx.get_full_tick([STOCK_QMT])
        tick_data = tick.get(STOCK_QMT, {})

        # lastClose / preClose = exchange official yesterday close
        yesterday_close = tick_data.get('lastClose', 0)
        if yesterday_close <= 0:
            yesterday_close = tick_data.get('preClose', 0)
        today_open = tick_data.get('open', 0)
        curr_price = tick_data.get('lastPrice', 0)

        # Fallback: if tick didn't return prev close
        if yesterday_close <= 0:
            yesterday_close = closes[-1]
            _log('[Warning] tick did not return lastClose, fallback to closes[-1]')

        if today_open > 0 and len(opens) > 0:
            opens[-1] = today_open
        self._daily_open = today_open if today_open > 0 else opens[-1]

        if curr_price <= 0:
            curr_price = closes[-1]

        # * v6 diagnostics: print all price sources + staleness check
        _log('')
        _log('=' * 55)
        _log('  RealTimeRSI v6 Init (tick.lastClose baseline, no stripping)')
        _log('=' * 55)
        _log(f'  tick.lastClose (prev close) = Y{yesterday_close:.2f}')
        _log(f'  tick.open     (today open)  = Y{today_open:.2f}')
        _log(f'  tick.lastPrice               = Y{curr_price:.2f}')
        _log(f'  xtdata last bar close        = Y{closes[-1]:.2f}')
        _log(f'  xtdata bar count             = {len(closes)}')
        gap_pct = abs(closes[-1] - yesterday_close) / yesterday_close * 100 if yesterday_close > 0 else 0
        if gap_pct < 0.5:
            _log(f'  -> xtdata data OK (gap {gap_pct:.2f}%)')
        else:
            _log(f'  -> ** WARNING: xtdata data may be stale (gap {gap_pct:.2f}%)')
            _log(f'  -> RSI baseline uses xtdata, but live RSI uses correct tick.lastClose')

        ok = self.rsi_engine.init_from_daily(closes, yesterday_close)
        if not ok:
            _log('[Error] RSI engine init failed')
            return

        daily_rsi = self.rsi_engine.daily_rsi

        # First live RSI update
        live_rsi = self.rsi_engine.update_tick(curr_price)
        live_zone, live_zone_label = self.rsi_engine._classify_zone(live_rsi)
        detail = self.rsi_engine.get_live_detail(curr_price)

        _log(f'  -> Today change: Y{detail["today_diff"]:+.2f} ({detail["today_diff_pct"]:+.2f}%)')
        _log(f'  -> RSI_live={live_rsi:.2f}  RSI_daily={daily_rsi:.2f}  delta={live_rsi-daily_rsi:+.2f}')
        _log('')

        # Refresh position
        self._refresh_position()
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('base_cost', 0) == 0.0:
            self.st['base_cost'] = yesterday_close

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
            'rsi_zone': live_zone, 'rsi_zone_label': live_zone_label,
            'fstate': STATE_IDLE, 'trade_count': 0, 'day_pnl': 0.0,
        })

        self._print_rsi_calculation(curr_price, live_rsi)
        self._print_trading_plan(curr_price)

    # =======================================================================
    # RSI Calculation Output
    # =======================================================================

    def _print_rsi_calculation(self, curr_price, live_rsi):
        eng = self.rsi_engine
        daily_rsi = eng.daily_rsi
        detail = eng.get_live_detail(curr_price)

        _log('')
        _log('+' + '-' * 62 + '+')
        _log('|  RSI(14) Live Calculation v5 -- {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', '')).ljust(53) + '|')
        _log('|' + '-' * 62 + '|')

        _log('|  * Baseline Prices (tick.lastClose):'.ljust(53) + '|')
        _log('|  prev_close = Y{:.2f}  |  today_open = Y{:.2f}  |  current = Y{:.2f}'.format(
            eng.prev_close, self._daily_open, curr_price).ljust(53) + '|')
        if detail:
            _log('|  Today change: Y{:+.2f} ({:+.2f}%)'.format(
                detail['today_diff'], detail['today_diff_pct']).ljust(53) + '|')

        _log('|' + '-' * 62 + '|')

        changes = eng.get_daily_changes(RSI_PERIOD)
        _log('|  Last {:d} daily changes:'.format(RSI_PERIOD).ljust(53) + '|')
        _log('|  {:>4s}  {:>10s}  {:>10s}  {:>12s}  |'.format('T-N', 'Close', 'Change', 'Change%'))
        for ch in changes:
            arrow = 'UP' if ch['change'] > 0 else ('DN' if ch['change'] < 0 else '--')
            _log('|  T-{:d}  Y{:>10.2f}  {:>+10.2f}  {:>+11.2f}%  {} |'.format(
                ch['day_offset'], ch['close'], ch['change'], ch['change_pct'], arrow))

        _log('|' + '-' * 62 + '|')
        _log('|  Historical avg_gain={:.4f}  avg_loss={:.4f}'.format(
            eng.avg_gain, eng.avg_loss).ljust(53) + '|')
        if detail:
            _log('|  Live avg_gain={:.4f}  avg_loss={:.4f}  RS={}'.format(
                detail['avg_gain'], detail['avg_loss'], detail['rs']).ljust(53) + '|')
            _log('|  -> RSI_live = **{:.2f}**'.format(live_rsi).ljust(53) + '|')

        _log('|' + '-' * 62 + '|')
        zone_bar = eng.zone_bar(live_rsi)
        _, zone_label = eng._classify_zone(live_rsi)
        _log('|  {} |'.format(zone_bar))
        _log('|  RSI_live={:.1f} | RSI_daily={:.1f} | delta={:+.1f} | {} |'.format(
            live_rsi, daily_rsi, live_rsi - daily_rsi, zone_label))
        dist_ob = RSI_OVERBOUGHT - live_rsi
        dist_os = live_rsi - RSI_OVERSOLD
        _log('|  Dist to OB(70): {:+.1f} pts  |  Dist to OS(30): {:+.1f} pts |'.format(dist_ob, dist_os))

        cross = eng.detect_cross_signal()
        if cross:
            _log('|' + '-' * 62 + '|')
            _log('|  RSI Cross Signal: {} |'.format(cross))

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
        pos_pct = self.st.get('pos_pct', 0)
        total_asset = self.st.get('total_asset', 0)

        _log('+' + '-' * 62 + '+')
        _log('|  RSI v5 Trading Plan -- {}  {}  {}'.format(
            STOCK_NAME, self.st.get('trade_date', ''), zone_label).ljust(53) + '|')
        _log('|  RSI_live={:.1f}  RSI_daily={:.1f}  delta={:+.1f}  prev_close=Y{:.2f}'.format(
            live_rsi, detail['daily_rsi'], detail['rsi_delta'], eng.prev_close).ljust(53) + '|')
        _log('|' + '-' * 62 + '|')
        _log('|  Open: Y{:.2f}  Current: Y{:.2f}  ({:+.2f}%)  Today: {:+.2f}%'.format(
            self._daily_open, curr_price,
            (curr_price/self._daily_open - 1)*100 if self._daily_open > 0 else 0,
            detail['today_diff_pct']).ljust(53) + '|')
        _log('|  Position: {:>5} sh  Y{:>10,.0f}  ({:.0f}%)  Total: Y{:>12,.0f}'.format(
            base_shares, base_shares*curr_price, pos_pct, total_asset).ljust(53) + '|')
        _log('|  Avail Cash: Y{:>12,.0f}    T+0 Avail: {:>3} sh ({} lots)'.format(
            avail_cash, base_can_use, base_can_use//TRADE_LOT_SIZE).ljust(53) + '|')
        _log('|' + '-' * 62 + '|')

        if rsi_zone in ('oversold', 'extreme_oversold'):
            _log('|  **OVERSOLD** -> Dip & Bounce Buy (bounce >={:.2f}%)'.format(BOUNCE_PCT*100).ljust(53) + '|')
            buy_lots = min(int(avail_cash*POSITION_PCT_BUY/(curr_price*TRADE_LOT_SIZE*1.01)), MAX_POSITION_LOTS)
            if buy_lots >= MIN_POSITION_LOTS:
                _log('|  --> BUY OK: {} lots x Y{:.2f} = Y{:,.0f}'.format(
                    buy_lots, curr_price, buy_lots*TRADE_LOT_SIZE*curr_price).ljust(53) + '|')
            else:
                _log('|  --> NO: insufficient cash'.ljust(53) + '|')
        elif rsi_zone in ('overbought', 'extreme_overbought'):
            _log('|  **OVERBOUGHT** -> Spike & Pullback Sell (pullback >={:.2f}%)'.format(PULLBACK_PCT*100).ljust(53) + '|')
            sell_lots = min(base_can_use//TRADE_LOT_SIZE, MAX_POSITION_LOTS)
            if sell_lots >= MIN_POSITION_LOTS:
                _log('|  --> SELL OK: {} lots'.format(sell_lots).ljust(53) + '|')
            else:
                _log('|  --> NO: no available position'.ljust(53) + '|')
        else:
            _log('|  **NEUTRAL** -- Waiting for RSI to enter OB/OS zone'.ljust(53) + '|')

        _log('|' + '-' * 62 + '|')
        _log('|  Params: RSI({}) | OB>{} | OS<{} | Bounce {:.2f}% | Pullback {:.2f}% | Max {} trades/day'.format(
            RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
            BOUNCE_PCT*100, PULLBACK_PCT*100, MAX_DAILY_TRADES).ljust(53) + '|')
        if self.total_trades > 0:
            wr = self.win_count/self.total_trades*100 if self.total_trades > 0 else 0
            _log('|  History: {} trades | WinRate {:.0f}% | PnL ~Y{:,.0f}'.format(
                self.total_trades, wr, self.total_pnl).ljust(53) + '|')
        _log('+' + '-' * 62 + '+')
        _log('')

    # =======================================================================
    # State Machine
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
            _log('\n' + '-' * 55)
            _log('  [ACTIVATE] Dip-Bounce Buy Monitor (RSI_live={:.1f})'.format(st['rsi_live']))
            _log('  Y{:.2f} | Bounce {:.2f}% confirm -> BUY'.format(price, BOUNCE_PCT*100))
            _log('-' * 55)
        elif rsi_zone in ('overbought', 'extreme_overbought'):
            st['fstate'] = STATE_MONITOR_SPIKE
            st['peak_price'] = price; st['dip_price'] = 0.0
            st['state_entered_at'] = now_hms(); st['state_bars'] = 0
            _log('\n' + '-' * 55)
            _log('  [ACTIVATE] Spike-Pullback Sell Monitor (RSI_live={:.1f})'.format(st['rsi_live']))
            _log('  Y{:.2f} | Pullback {:.2f}% confirm -> SELL'.format(price, PULLBACK_PCT*100))
            _log('-' * 55)

    def _handle_monitor_dip(self, price):
        st = self.st; st['state_bars'] += 1
        if price < st['dip_price']: st['dip_price'] = price
        dip = st['dip_price']
        if dip <= 0: return
        bounce = (price - dip) / dip
        if bounce >= BOUNCE_PCT:
            _log('\n' + '=' * 55)
            _log('  [BUY CONFIRMED] Bounce {:.3f}%  Low Y{:.2f} -> Current Y{:.2f}'.format(bounce*100, dip, price))
            _log('=' * 55)
            self._execute_buy(price, dip, bounce)
        elif st.get('rsi_zone') == 'neutral' and st['state_bars'] > 10:
            _log('[DIP CANCELLED] RSI returned to neutral'); st['fstate'] = STATE_IDLE; st['dip_price'] = 0.0

    def _handle_monitor_spike(self, price):
        st = self.st; st['state_bars'] += 1
        if price > st['peak_price']: st['peak_price'] = price
        peak = st['peak_price']
        if peak <= 0: return
        pullback = (peak - price) / peak
        if pullback >= PULLBACK_PCT:
            est = (peak - price) * TRADE_LOT_SIZE
            _log('\n' + '=' * 55)
            _log('  [SELL CONFIRMED] Pullback {:.3f}%  High Y{:.2f} -> Current Y{:.2f}  PnL ~Y{:,.0f}'.format(
                pullback*100, peak, price, est))
            _log('=' * 55)
            self._execute_sell(price, peak, pullback)
        elif st.get('rsi_zone') == 'neutral' and st['state_bars'] > 10:
            _log('[SPIKE CANCELLED] RSI returned to neutral'); st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0

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
        _log('[RSI BUY #{}/{}] Bounce {:.2f}% @ Y{:.2f}'.format(tc+1, MAX_DAILY_TRADES, bounce_pct*100, price))
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
        _log('[RSI SELL #{}/{}] Pullback {:.2f}% @ Y{:.2f} PnL ~Y{:.0f}'.format(tc+1, MAX_DAILY_TRADES, pullback_pct*100, price, est))
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
                _log('[Resume] {} trades remaining -> IDLE'.format(MAX_DAILY_TRADES - st['trade_count']))

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
        _log('{} RSI Strategy v5 Started'.format(STOCK_NAME))
        _log('  * v5: tick.lastClose as prev_close (single source of truth)')
        _log('  RSI({}) | OB > {} | OS < {}'.format(RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD))
        _log('  Bounce {:.2f}% | Pullback {:.2f}% | Max {} trades/day'.format(
            BOUNCE_PCT*100, PULLBACK_PCT*100, MAX_DAILY_TRADES))
        _log('  * Log: {}'.format(get_logger().log_path if get_logger() else '?'))

        try: self._daily_init()
        except Exception as e: _log('[Exception] Init failed: {}'.format(e)); _traceback.print_exc()

        _log('Monitoring... (Ctrl+C to stop)\n')

        try:
            while self._running:
                now = now_hms(); now_ts = _time.time()
                if not is_market_open(now):
                    today = datetime.now().strftime('%Y%m%d')
                    if '09:25:00' <= now < '09:30:00':
                        if self.st.get('trade_date', '') != today:
                            try: self._daily_init(); self._last_heartbeat = now_ts
                            except Exception as e: _log('[Pre-market exception] {}'.format(e))
                            _time.sleep(5); continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                        _time.sleep(5); continue
                    if self.st.get('trade_date', '') != today:
                        try: self._daily_init()
                        except Exception as e: _log('[Exception] {}'.format(e))
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                    _time.sleep(10); continue

                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick: _time.sleep(1); continue
                price = tick[STOCK_QMT].get('lastPrice', 0)
                if price <= 0: _time.sleep(1); continue

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

                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    self._heartbeat(price)

                _time.sleep(2)

        except KeyboardInterrupt: _log('\nUser interrupt')
        except Exception as e: _log('[Exception] {}'.format(e)); _traceback.print_exc()
        finally:
            self.conn.disconnect()
            _log('{} RSI Strategy v5 Stopped | {} trades | PnL ~Y{:,.0f}'.format(
                STOCK_NAME, self.total_trades, self.total_pnl))
            logger = get_logger()
            if logger: _log('* Log saved: ' + logger.log_path); logger.close()

    def _heartbeat(self, price):
        eng = self.rsi_engine
        detail = eng.get_live_detail(price)
        fstate = self.st.get('fstate', STATE_IDLE)
        tc = self.st.get('trade_count', 0)
        if detail is None: _log('[RSI] Not initialized'); return

        parts = [f'RSI_live={detail["live_rsi"]:.1f}', f'{detail["zone_label"]}']
        # * v5: show real change based on tick.lastClose
        parts.append(f'vsPrevClose{detail["today_diff"]:+.2f}({detail["today_diff_pct"]:+.2f}%)')
        rsi_delta = detail['rsi_delta']
        if abs(rsi_delta) > 0.5:
            parts.append(f'(daily={detail["daily_rsi"]:.1f} d={rsi_delta:+.1f})')
        if fstate == STATE_MONITOR_DIP:
            dip = self.st.get('dip_price', 0)
            if dip > 0:
                b = (price - dip)/dip*100; need = max(0, BOUNCE_PCT*100 - b)
                parts.append(f'DIP:lowY{dip:.2f} bounce{b:.2f}% need{need:.2f}%')
        elif fstate == STATE_MONITOR_SPIKE:
            peak = self.st.get('peak_price', 0)
            if peak > 0:
                pb = (peak - price)/peak*100; need = max(0, PULLBACK_PCT*100 - pb)
                parts.append(f'SPIKE:highY{peak:.2f} pullback{pb:.2f}% need{need:.2f}%')
        elif fstate == STATE_IDLE: parts.append('WAIT')
        elif fstate == STATE_DONE: parts.append('DONE')
        parts.append(f'Y{price:.2f}')
        parts.append(f'{tc}/{MAX_DAILY_TRADES} trades')
        _log('[RSI] {}'.format(' | '.join(parts)))


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT RSI v5 -- tick.lastClose prev_close baseline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v5_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/RSI_Strategy_v5_miniqmt.py" --mode live

v5 fix (vs v4):
  prev_close = tick.lastClose (exchange official prev close)
  Does NOT depend on whether xtdata daily data includes today's partial bar.
        """)
    parser.add_argument('--mode', '-m', default='signal', choices=['signal', 'live'])
    args = parser.parse_args()
    logger = FileLogger(STOCK_CODE, version='RSI_v5')
    set_logger(logger)
    print(f'* Log file: {logger.log_path}')
    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  WARNING: About to start LIVE auto-trading!')
        print(f'  Strategy: RSI v5 (tick.lastClose baseline + real-time RSI)')
        print(f'  Symbol: {STOCK_NAME}({STOCK_CODE})')
        print(f'  RSI({RSI_PERIOD}) | OB > {RSI_OVERBOUGHT} | OS < {RSI_OVERSOLD}')
        print('!' * 55)
        confirm = input('\nConfirm? (type yes to proceed): ')
        if confirm.strip().lower() != 'yes': print('Cancelled'); logger.close(); return
    runner = RSIStrategyRunner(dry_run=dry_run)
    runner.run()


if __name__ == '__main__':
    main()
