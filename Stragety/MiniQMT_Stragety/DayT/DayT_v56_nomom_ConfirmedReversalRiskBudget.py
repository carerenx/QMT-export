# -*- coding: utf-8 -*-
"""
v56 ConfirmedReversalRiskBudget — v55 with stricter reversal confirmation and a protected core position.
Direction admission and serialized cycle ownership are under validation.
Live mode is disabled. Legacy checkpoint import requires explicit reconciliation;
do not replace the v51 state file with a v52 research checkpoint.
"""
# v52研究参数：只限制新增正向周期，旧周期退出不受方向准入影响。
DIRECTIONAL_THRESHOLD = 0.20  # 候选仅 0 / 0.20 / 0.40；不得按留出结果继续调参。
DIRECTIONAL_ENABLED = True
LONG_RESEARCH_DISABLED = True  # v56：正T消融为负，冻结该通道直至独立样本验证通过。
OVERNIGHT_ENABLED = True
MAX_OPEN_CYCLES = 2  # 两条主策略研究通道；阶梯属于原周期。
CYCLE_EXPOSURE_FRACTION = 0.50  # 未平数量绝对值合计/原始底仓，双向不得抵消。
CYCLE_FEE_RATE = 0.0005  # 研究预留假设，不代表券商真实费率。
CYCLE_MINIMUM_FEE = 5.0
CYCLE_AGE_ALERT_DAYS = 3
CYCLE_MAX_HOLDING_DAYS = 3
SHORT_FIVE_DAY_RETURN_MAX = 0.03
SHORT_NEW_ENTRY_CUTOFF = '14:20:00'
SHORT_CONFIRM_MIN_BARS = 2
SHORT_CONFIRM_MIN_EXTENSION_PCT = 0.0015
SHORT_CONFIRM_MIN_PULLBACK_PCT = 0.0020
import os, sys, time as _time, argparse, math, traceback as _traceback
from collections import deque
from datetime import datetime, timedelta
from typing import Optional
import numpy as np, pandas as pd

# Permit the documented ``python Stragety/.../DayT_v52...py`` command.
_STRATEGY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPOSITORY_ROOT = os.path.dirname(os.path.dirname(_STRATEGY_ROOT))
for _module_root in (_REPOSITORY_ROOT, _STRATEGY_ROOT):
    if _module_root not in sys.path:
        sys.path.insert(0, _module_root)

from core.directional_overnight import DirectionalAdmission, Cycle, exposure_limit, validate_cycles
from core import config as cfg
from core.signals import compute_signal
from core.quantile_trend_regime import compute_quantile_trend_regime
from core.atr_reentry import calculate_atr_reentry
from core.t_position_size import calculate_t_shares
from core.execution_book import ExecutionBook
from core.intraday_rebound import rebound_reference
from core.intraday_strength import IntradayStrength, StrengthConfig, lower_excursion_units
from core.dayt_checkpoint import read_checkpoint, write_checkpoint
from core.runtime_watchdog import RuntimeWatchdog, instrument_rpc
from core.connection_monitor import probe_connections, quote_freshness
from Stragety.MiniQMT_Stragety.DayT.infra.logger import FileLogger, set_logger, get_logger, _log
from Stragety.MiniQMT_Stragety.DayT.infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares, set_global_conn,
)

# 兼容旧v49/v50账户状态；只接续已核对账本，不从持仓反推成交。
# 普通运行每 30 秒保存；每笔委托前、处理完成后强制保存。
# v52沿用v49账户检查点，保留已确认的次数、交易腿与账本。不得同时运行。
# 仅调整未开仓反T启动价；原开盘价阈值和已有买回目标保持不变。
# shadow：旧v50规则实际执行，新规则只观察；active须完成回放/影子验收后人工启用。
INTRADAY_REFERENCE_MODE = 'shadow'
QUANTILE_UNITS_SCALE = 0.60    # 首轮quantile_units乘此系数；1=原值，0.8=降低20%，须大于0
REENTRY_UP_UNITS_SCALE = 0.80  # 第二轮及以后反T上行系数的独立缩放；1=原值，0.8=卖出距离缩短20%，须大于0
                             # 不叠乘首轮系数；不改变正T买入阈值或已成交交易的退出目标。
STRENGTH_HISTORY_DAYS = 80       # 仅已完成日线；不得使用当前未完成日线
STRENGTH_LOWER_QUANTILE = 0.35   # 整理时历史上行ATR单位分位
STRENGTH_LOOKBACK_MIN = 10      # 近期上行和低点观察窗口
STRENGTH_SMOOTH_MIN = 3         # 三个完整分钟强度平均
STRENGTH_WARMUP_MIN = 15        # 启动/午休/断流后连续完整观察分钟
STRENGTH_OPEN_UNITS = 0.25      # 相对开盘涨此倍ATR时，该分量为1
STRENGTH_AVERAGE_UNITS = 0.10   # 相对日内均价涨此倍ATR时，该分量为1
STRENGTH_MOMENTUM_UNITS = 0.10  # 10分钟涨此倍ATR时，该分量为1
STRENGTH_STRONG = 0.8          # 平滑强度达到此值撤回降低后的未成交监控
STRENGTH_REBOUND_UNITS = 0.25   # 近期低点反弹要求
STRENGTH_REBOUND_MIN = 0.008    # 最小反弹0.8%
STRENGTH_REBOUND_MAX = 0.02     # 最大反弹要求2%，不是止损上限
STRENGTH_AVERAGE_BUFFER = 0.05 # 日内均价上方此倍ATR
STRENGTH_MAX_GAP_SEC = 90       # 采样断流回退；不替代行情时间戳健康检查
STRENGTH_LOG_INTERVAL_SEC = 300 # 阶段变化即时输出，其余5分钟汇总
REBOUND_ENABLED = True
REBOUND_WINDOW_SEC = 600       # 近期低点窗口，10分钟
REBOUND_CONFIRM_SEC = 180      # 连续弱势确认，3分钟
REBOUND_ATR_UNITS = 0.25       # 从近期低点至少反弹多少倍日ATR
REBOUND_MIN_PCT = 0.008        # 最小反弹0.8%，过滤微小波动
REBOUND_MAX_PCT = 0.02         # 反弹距离最高2%，不等于收益或止损上限
REBOUND_AVERAGE_UNITS = 0.05   # 卖出参考价至少在日内均价上方0.05倍ATR
REBOUND_WEAK_UNITS = 0.08      # 低于开盘价、均价至少此ATR幅度才累计弱势时间
REBOUND_STRONG_UNITS = 0.15    # 收复均价并超过此ATR幅度，撤回降阈值
REENTRY_WEAK_DISCOUNT = 0.35   # 第二轮及以后：弱势最强时，上行ATR系数最多折减35%
REENTRY_WEAK_FULL_UNITS = 0.25 # 低于开盘价/均价较低者0.25倍ATR时，弱势程度达到1
CHECKPOINT_INTERVAL_SEC = 30
STATE_SAVE_LOG_INTERVAL_SEC = 300  # 保存成功日志最多每5分钟打印一次；不影响实际保存频率
# 非交易时段的连接、仓位与普通状态保存间隔；独立看门狗仍持续工作。
OFF_HOURS_REFRESH_SEC = 300
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state', 'v56_{}.json'.format(cfg.ACCOUNT))

SHORT_TREND_LOOKBACK = 3


def short_trend_guard(completed_closes, lookback=SHORT_TREND_LOOKBACK):
    """Allow a new REV-T only when completed-session momentum is nonpositive."""
    closes = [float(value) for value in completed_closes]
    if len(closes) <= lookback:
        return {
            'allowed': False,
            'return': None,
            'reason': 'trend history shorter than {} sessions'.format(lookback),
        }
    anchor = closes[-lookback - 1]
    latest = closes[-1]
    if anchor <= 0 or latest <= 0:
        return {
            'allowed': False,
            'return': None,
            'reason': 'invalid completed close for trend guard',
        }
    trend_return = latest / anchor - 1.0
    return {
        'allowed': trend_return <= 0.0,
        'return': trend_return,
        'reason': '3-session return {:+.2f}%'.format(trend_return * 100),
    }

def short_five_day_momentum_guard(completed_closes,
                                  maximum=SHORT_FIVE_DAY_RETURN_MAX):
    """Allow a new REV-T only when five-session momentum is not excessive."""
    closes = [float(value) for value in completed_closes]
    if len(closes) < 6 or closes[-6] <= 0 or closes[-1] <= 0:
        return {'allowed': False, 'return': None}
    five_day_return = closes[-1] / closes[-6] - 1.0
    return {
        'allowed': five_day_return <= maximum + 1e-12,
        'return': five_day_return,
    }


def confirmed_short_reversal(trigger, peak, price, armed_bars,
                             minimum_bars=SHORT_CONFIRM_MIN_BARS,
                             minimum_extension_pct=SHORT_CONFIRM_MIN_EXTENSION_PCT,
                             minimum_pullback_pct=SHORT_CONFIRM_MIN_PULLBACK_PCT):
    """Require time, excess extension and a material peak reversal."""
    if trigger <= 0 or peak <= 0 or price <= 0:
        return False
    if armed_bars < minimum_bars:
        return False
    extension = peak / trigger - 1.0
    pullback = (peak - price) / peak
    return extension >= minimum_extension_pct and pullback >= minimum_pullback_pct


ACCOUNT = cfg.ACCOUNT
TRADE_LOT_SIZE = cfg.TRADE_LOT_SIZE
STATE_IDLE = cfg.STATE_IDLE; STATE_SPIKING = cfg.STATE_SPIKING
STATE_SOLD = cfg.STATE_SOLD; STATE_DIPPING = cfg.STATE_DIPPING
STATE_DONE = cfg.STATE_DONE; STATE_FORCED = cfg.STATE_FORCED
STATE_BT_DIPPING = cfg.STATE_BT_DIPPING; STATE_BT_BOUGHT = cfg.STATE_BT_BOUGHT
STATE_BT_SPIKING = cfg.STATE_BT_SPIKING

# 反T卖出后先跌到卖价99%，未触及原买回价即反弹回99%时买回

# 阶梯加仓/减仓参数 (成功卖出/买入后, 在成交价基础上加减价监测追加)
LADDER_UP_STEP_PCT   = 0.015   # 反T: 卖出后价格再涨 +1.5% → 追加冲高回落卖出
LADDER_DOWN_STEP_PCT = 0.015   # 正T: 买入后价格再跌 -1.5% → 追加探底回升买入

# 成交判定
FILL_TIMEOUT_SEC = 8.0   # 等待满额成交的超时秒数

# 短线动量反转机制 (2分钟事件驱动, 独立于日线信号)

# v39: Freeze new legs near the normal 10% upper limit, then require a meaningful opening-board retreat to persist for 120s.
LIMIT_UP_GUARD_PCT = 0.095
LIMIT_UP_RELEASE_PCT = 0.085
LIMIT_UP_RELEASE_HOLD_SEC = 120.0

# Check live connectivity every 30 seconds.  The monitor only logs; it does
# not reconnect, change strategy state, or place an order.
CONNECTION_CHECK_INTERVAL_SEC = 30.0
QUOTE_STALE_AFTER_SEC = 90       # 盘中源报价超过90秒仅告警，不阻止交易。
QUOTE_FUTURE_TOLERANCE_SEC = 5   # 源报价超前本机超过5秒，记录时钟异常。
QUOTE_HEALTH_LOG_INTERVAL_SEC = 300  # 新鲜度状态变化立即打印，同状态每5分钟汇总。

# 每隔多少秒读取账户持仓，把新持仓加入策略；已卖出的股票保留买回状态。
PORTFOLIO_REFRESH_SEC = 60.0

# 新开一笔做T的目标金额；高价股不足一手时仍按一手，不是硬性资金上限。
WATCHDOG_WARN_SEC = 15.0  # 独立线程每15秒检查持续调用
READ_RPC_TIMEOUT_SEC = 5.0  # BigQMT实时只读RPC等待上限（底层路由仍可能另有超时）
CAPACITY_REFRESH_SEC = 30.0
T_TARGET_VALUE = 40000.0
# 最多使用未被其他正T占用的可卖底仓的此比例；不足一手但有整手时按一手。
T_POSITION_FRACTION = 0.40
# 单笔股数覆盖，键为完整代码，例如 {'600000.SH': 100}。
# 默认普通标的100股，688/689开头标的200股；特殊标的可在这里指定。
SYMBOL_LOT_OVERRIDES = {}


def calculate_execution_capacity(base_can_use, available_cash, price,
                                 max_daily_trades, lot_size=TRADE_LOT_SIZE):
    """Calculate executable REV/FWD lots; zero lots are never enabled."""
    sellable_shares = max(0, int(base_can_use or 0))
    sellable_lots = sellable_shares // lot_size
    cash_lots = 0
    if price and price > 0:
        cash_lots = int(float(available_cash or 0) /
                        (float(price) * lot_size * 1.01))
    short_lots = min(sellable_lots, max_daily_trades)
    long_lots = min(cash_lots, sellable_lots, max_daily_trades)
    can_short = short_lots >= 1
    can_long = long_lots >= 1

    short_reason = '' if can_short else 'sellable {} sh < {} sh'.format(
        sellable_shares, lot_size)
    long_reasons = []
    if cash_lots < 1:
        long_reasons.append('insufficient cash for 1 lot')
    if sellable_lots < 1:
        long_reasons.append(
            'T+1: sellable base shares {} sh < {} sh'.format(
                sellable_shares, lot_size))
    return {
        'short_lots': short_lots,
        'long_lots': long_lots,
        'cash_lots': cash_lots,
        'sellable_lots': sellable_lots,
        'can_short': can_short,
        'can_long': can_long,
        'short_reason': short_reason,
        'long_reason': '; '.join(long_reasons),
    }


def limit_up_guard_transition(active, release_since, price, last_close,
                              now_ts):
    """Return ``(active, release_since, event)`` for the new-leg guard."""
    if price <= 0 or last_close <= 0:
        return bool(active), float(release_since or 0.0), ''
    rise = price / last_close - 1.0
    if rise >= LIMIT_UP_GUARD_PCT:
        return True, 0.0, 'LOCK' if not active else ''
    if not active:
        return False, 0.0, ''
    if rise <= LIMIT_UP_RELEASE_PCT:
        if not release_since:
            return True, float(now_ts), 'RELEASE_PENDING'
        if now_ts - release_since >= LIMIT_UP_RELEASE_HOLD_SEC:
            return False, 0.0, 'UNLOCK'
        return True, float(release_since), ''
    return True, 0.0, ''


def calculate_intraday_average(amount, pvolume, last_price=0.0):
    """Return intraday VWAP from cumulative amount/raw volume, or 0 if bad."""
    amount = float(amount or 0.0)
    pvolume = float(pvolume or 0.0)
    if amount <= 0 or pvolume <= 0:
        return 0.0
    average = amount / pvolume
    # Fail closed on a unit/data mismatch. A valid cumulative VWAP should stay
    # reasonably close to the latest traded price even on a volatile day.
    last_price = float(last_price or 0.0)
    if average <= 0 or (last_price > 0 and
                        not last_price * 0.5 <= average <= last_price * 1.5):
        return 0.0
    return round(average, 4)


def scale_reentry_signal(signal):
    """Scale the next-cycle sell distance from its original units, never cumulatively."""
    if not signal or signal.get('trigger_base') != 'CLOSE_FILL_ATR':
        return signal
    scale = float(REENTRY_UP_UNITS_SCALE)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('REENTRY_UP_UNITS_SCALE must be finite and greater than zero')
    result = signal['reentry']
    raw = result.setdefault('unscaled_up_units', result['up_units'])
    units = raw * scale
    base, atr_pct = result['base'], result['atr_pct']
    trigger = round(math.ceil((base + max(base * atr_pct * units, 0.01)) /
                              0.01 - 1e-9) * 0.01, 2)
    result.update(up_units=units, up_units_scale=scale, sell_trigger=trigger)
    signal.update(sell_trigger=trigger, sell_trigger_raw=trigger,
                  atr_pct=atr_pct, trigger_units=units, trigger_pct=atr_pct * units,
                  sell_mult=units, sell_mult_base=units)
    return signal


def scale_quantile_signal(signal):
    """Apply the configured first-entry scale once, including restored signals."""
    if not signal or not signal.get('quantile_trend_active') or signal.get('trigger_base') == 'CLOSE_FILL_ATR':
        return signal
    scale = float(QUANTILE_UNITS_SCALE)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('QUANTILE_UNITS_SCALE must be finite and greater than zero')
    regime = signal['quantile_trend']
    atr_pct = float(signal['atr_pct'])
    raw = regime.get('unscaled_trigger_units')
    if raw is None:
        raw = regime['trigger_pct'] / atr_pct if atr_pct > 0 else regime['trigger_units']
        regime['unscaled_trigger_units'] = raw
    units = raw * scale
    opening = float(signal.get('open_price', regime['open_price']))
    trigger = round(opening * (1 + atr_pct * units), 2)
    regime.update(trigger_units=units, trigger_pct=atr_pct * units,
                  sell_trigger=trigger, units_scale=scale)
    signal.update(trigger_units=units, trigger_pct=atr_pct * units,
                  sell_mult=units, sell_mult_base=units,
                  sell_trigger=trigger, sell_trigger_raw=trigger)
    return signal


def apply_quantile_trend_regime(signal, opens, highs, lows, closes, volumes,
                                today_open):
    """Replace fixed MA/ATR multiplier logic with trend-quantile thresholds."""
    regime = compute_quantile_trend_regime(
        opens, highs, lows, closes, volumes, today_open=today_open)
    if regime is None:
        signal['quantile_trend_active'] = False
        return signal

    prior_reason = signal.get('blocked_reason', '')
    signal.update({
        'quantile_trend_active': True,
        'quantile_trend': regime,
        'trend': regime['style'],
        'sell_trigger': regime['sell_trigger'],
        'sell_trigger_raw': regime['sell_trigger'],
        'sell_mult': regime['trigger_units'],
        'sell_mult_base': regime['trigger_units'],
        'trigger_units': regime['trigger_units'],
        'trigger_pct': regime['trigger_pct'],
        'atr_pct': regime['atr_pct'],
        'range_capped': False,
        'factor_details': {
            'trend_score': regime['trend_score'],
            'trend_rank': regime['trend_rank'],
            'trigger_q': regime['trigger_quantile'],
        },
    })
    # Only the strongest historical trend tail is blocked. Ordinary positive
    # trends remain eligible, but receive a higher quantile threshold.
    if regime['style'] == 'bull':
        signal['do_short'] = True
        signal['blocked_reason'] = 'extreme trend-rank guard'
    elif prior_reason.startswith('strong bull'):
        signal['do_short'] = True
        signal['blocked_reason'] = ''
    return scale_quantile_signal(signal)



MA_REPORT_INTERVAL_SEC = 600.0
MA20_RISK_RATIO = 0.97


def calculate_ma_position(completed_closes, current_price):
    """Return intraday MA5/MA20 positions using price as today's close."""
    price = float(current_price or 0.0)
    closes = [float(value) for value in completed_closes
              if math.isfinite(float(value)) and float(value) > 0]
    if price <= 0 or len(closes) < 19:
        return None

    ma5 = (sum(closes[-4:]) + price) / 5.0
    ma20 = (sum(closes[-19:]) + price) / 20.0
    return {
        'ma5': ma5, 'ma20': ma20,
        'ma5_position': 'ABOVE' if price > ma5 else ('BELOW' if price < ma5 else 'AT'),
        'ma20_position': 'ABOVE' if price > ma20 else ('BELOW' if price < ma20 else 'AT'),
        'ma5_gap_pct': (price / ma5 - 1.0) * 100.0,
        'ma20_gap_pct': (price / ma20 - 1.0) * 100.0,
        'ma20_risk_price': ma20 * MA20_RISK_RATIO,
        'risk': price < ma20 * MA20_RISK_RATIO,
    }


def resolve_signal_open(now_hms, tick_open, latest_complete_close):
    """Choose the signal open price and describe its source for logging."""
    after_hours = now_hms < '09:30:00' or now_hms >= '15:00:00'
    latest_close = float(latest_complete_close or 0.0)
    if after_hours and latest_close > 0:
        return latest_close, 'AFTER-HOURS latest_complete_close'
    tick_price = float(tick_open or 0.0)
    if tick_price > 0:
        return tick_price, 'TICK_OPEN'
    return latest_close, 'LATEST_COMPLETE_CLOSE fallback'


def format_signal_base_source(source):
    """Return a concise, operator-facing label for the signal base price."""
    source_labels = {
        'AFTER-HOURS latest_complete_close':
            'latest complete close; after-hours',
        'TICK_OPEN': 'today open tick',
        'LATEST_COMPLETE_CLOSE fallback': 'latest complete close; tick-open fallback',
    }
    return source_labels.get(source, str(source or 'unknown'))


class ExecutionRunner:
    """MiniQMT v56 — confirmed reversals with a protected core position."""

    def __init__(self, portfolio, stock_qmt, stock_name=''):
        self.portfolio = portfolio
        self.stock_qmt = stock_qmt
        self.stock_code = stock_qmt.split('.')[0]
        self.stock_name = stock_name or stock_qmt
        self.trade_lot = SYMBOL_LOT_OVERRIDES.get(
            stock_qmt, 200 if self.stock_code.startswith(('688', '689')) else 100)
        self.version = 'v56'
        self.conn = SymbolConnector(portfolio.conn, stock_qmt)
        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = portfolio.dry_run
        self._last_heartbeat = 0.0
        self._last_connection_check = 0.0
        self._connection_healthy = None
        self._quote_health_status = None
        self._quote_health_log_time = 0.0
        self._quote_last_received = None
        self._quote_last_fresh = None
        self._quote_market_open = None
        self._running = True
        self.total_t_days = 0
        self.total_pnl = 0.0
        self.execution_book = ExecutionBook()
        self._execution_price = None
        self._last_capacity_refresh = 0.0
        self._reset_strength_reference()


    def has_open_legs(self):
        return (any(self.execution_book.legs.values()) or
                bool(self.st.get('short_legs') or self.st.get('long_legs')) or
                self.st.get('fstate') in (STATE_SOLD, STATE_DIPPING, STATE_BT_BOUGHT, STATE_BT_SPIKING))

    def checkpoint_record(self):
        return dict(name=self.stock_name, state=self.st,
                    orders=self.execution_book.orders, legs=self.execution_book.legs,
                    cycle_gross=self.execution_book.cycle_gross,
                    total_pnl=self.total_pnl, total_t_days=self.total_t_days,
                    last_executed_order=getattr(self, '_last_executed_order', None))

    def restore_record(self, record):
        self._init_state()
        saved_state = record['state']
        legacy_state = saved_state.get('mom_state', 'MOM_IDLE')
        legacy_shares = int(saved_state.get('mom_leg_shares', 0) or 0)
        if legacy_shares > 0 or legacy_state not in ('', 'MOM_IDLE', None):
            raise RuntimeError(
                'legacy MOM execution state requires manual reconciliation')
        self.st.update(saved_state)
        for key in tuple(self.st):
            if key.startswith('mom_'):
                del self.st[key]
        scale_quantile_signal(self.st.get('daily_signal'))
        scale_reentry_signal(self.st.get('daily_signal'))
        self.execution_book.orders = record['orders']
        self.execution_book.legs = record['legs']
        self.execution_book.cycle_gross = record['cycle_gross']
        self.total_pnl = record['total_pnl']
        self.total_t_days = record['total_t_days']
        if record.get('last_executed_order'):
            self._last_executed_order = record['last_executed_order']
        if self.st.get('reentry_pending'):
            self.st['reentry_pending']['retry_at'] = 0
        self.st.update(rebound_memory={}, rebound_effective=None, rebound_identity=None)
        self._reset_strength_reference()
        if (INTRADAY_REFERENCE_MODE == 'active' and self.st.get('rebound_armed') and
                self.st.get('fstate') == STATE_SPIKING and not self.has_open_legs()):
            self.st['strength_armed'] = True
            self._strength_cancel_arm('restart: rewarm before lowered entry')
        self._restored = True
        self._log('[STATE-RESTORED] ledger state={} REV-count={} FWD-count={}'.format(
            self.st.get('fstate'), self.st.get('trade_count_short', 0),
            self.st.get('trade_count_long', 0)))

    def _log(self, message):
        _log('[{}] {}'.format(self.stock_qmt, message))

    def _monitor_connections(self, now_ts):
        """Log connection degradation/recovery without changing trade state."""
        market_open = cfg.is_market_open(cfg.now_hms())
        interval = CONNECTION_CHECK_INTERVAL_SEC if market_open else OFF_HOURS_REFRESH_SEC
        if self._quote_market_open == market_open and now_ts - self._last_connection_check < interval:
            return
        self._quote_market_open = market_open
        self._last_connection_check = now_ts
        snapshot = {}
        healthy, detail = probe_connections(
            self.conn, self.ctx, self.stock_qmt, check_trade=True, snapshot=snapshot)
        if not healthy:
            self._log('[CONNECTION-ALERT] {}'.format(detail))
        elif self._connection_healthy is False:
            self._log('[CONNECTION-RECOVERED] {}'.format(detail))
        elif self._connection_healthy is None:
            self._log('[CONNECTION] {}'.format(detail))
        self._connection_healthy = healthy
        if snapshot.get('received'):
            self._quote_last_received = now_ts
        health = quote_freshness(snapshot.get('quote'), now_ts, market_open,
                                 QUOTE_STALE_AFTER_SEC, QUOTE_FUTURE_TOLERANCE_SEC)
        status = health['status']
        if status == 'FRESH':
            self._quote_last_fresh = now_ts
        if status != self._quote_health_status or now_ts - self._quote_health_log_time >= QUOTE_HEALTH_LOG_INTERVAL_SEC:
            tag = 'QUOTE-RECOVERED' if status == 'FRESH' and self._quote_health_status in (
                'STALE', 'UNKNOWN', 'CLOCK_SKEW', 'UNAVAILABLE') else 'QUOTE-HEALTH'
            def display_time(value):
                return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S') if value is not None else 'UNKNOWN'
            self._log('[{}] {} connection={} quote_time={} age={} last_rpc_success={} '
                      'last_confirmed_fresh={} reason={} action=LOG_ONLY'.format(
                          tag, status, 'OK' if healthy else 'ERROR', display_time(health['quote_time']),
                          '{:.1f}s'.format(health['age']) if health['age'] is not None else 'UNKNOWN',
                          display_time(self._quote_last_received), display_time(self._quote_last_fresh), health['reason']))
            self._quote_health_log_time = now_ts
        self._quote_health_status = status

    def _init_state(self):
        self.st.update({
            'daily_signal': None, 'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'entry_price': 0.0, 'fstate': STATE_IDLE,
            'peak_price': 0.0, 'dip_price': 0.0,
            'sell_fill_price': 0.0, 'buyback_target': 0.0, 'buyback_target_pct': 0.0,
            'day_pnl': 0.0,
            'total_t_days': self.total_t_days, 'total_pnl': self.total_pnl,
            'trade_date': '', '_guard_date': '',
            'initialized': False, 'init_attempts': 0, 'last_init_time': 0.0,
            'state_enter_time': '', 'sell_elapsed_bars': 0,
            'locked': False, 'lock_reason': '', 'lock_since': '',
            'lock_cooldown_until': 0.0, 'price_history': deque(),
            'limit_up_guard': False, 'limit_up_release_since': 0.0,
            'intraday_avg_price': 0.0, 'intraday_avg_valid': False,
            'next_t_cycle': 0,
            'rebound_memory': {}, 'rebound_effective': None, 'rebound_identity': None,
            'rebound_armed': False, 'strength_armed': False,
            'short_arm_bars': 0, 'short_arm_trigger': 0.0,
            'reentry_pending': None, 'reentry_history': None,
            '_pre_market_done': '', '_market_open_logged': False,
            # ★ v22/v23: 阶梯加仓/减仓状态 — 腿记录为 (成交价, 成交股数)
            'ladder_sell_target': 0.0, 'ladder_buy_target': 0.0,
            'ladder_sold_count': 0, 'ladder_bought_count': 0,
            'short_legs': [], 'long_legs': [],
            # Completed closes plus the current intraday price form MA5/MA20.
            'ma_completed_closes': [], 'ma_history_date': '', 'last_ma_report_time': 0.0,
        })

    def _reset_daily(self):
        guard = self.st.get('_guard_date', '')
        bs, bu, bc = self.st.get('base_shares', 0), self.st.get('base_can_use', 0), self.st.get('base_cost', 0.0)
        ep = self.st.get('entry_price', 0.0)
        self._init_state()
        self.st['base_shares'] = bs; self.st['base_can_use'] = bu; self.st['base_cost'] = bc
        self.st['entry_price'] = ep; self.st['_guard_date'] = guard
        self.st['total_t_days'] = self.total_t_days; self.st['total_pnl'] = self.total_pnl
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
            self.st[k] = 0.0
        self.st['locked'] = False; self.st['lock_reason'] = ''; self.st['lock_since'] = ''
        self.st['_pre_market_done'] = ''; self.st['_market_open_logged'] = False

    def _lock_all_trading(self, reason):
        """Fail closed when the daily-data initialization path is unhealthy."""
        self.st['daily_signal'] = {}
        self.st['do_short'] = False
        self.st['do_long'] = False
        self.st['initialized'] = False
        self.st['locked'] = True
        self.st['lock_reason'] = reason
        self.st['lock_since'] = cfg.now_hms()
        self._log('[TRADE-LOCK] {}; all trading disabled'.format(reason))

    def _update_limit_up_guard(self, price, tick_data, now_ts):
        last_close = float(tick_data.get('lastClose', 0) or 0)
        active, release_since, event = limit_up_guard_transition(
            self.st.get('limit_up_guard', False),
            self.st.get('limit_up_release_since', 0.0),
            float(price or 0), last_close, now_ts)
        self.st['limit_up_guard'] = active
        self.st['limit_up_release_since'] = release_since
        rise_pct = (price / last_close - 1.0) * 100 if last_close > 0 else 0.0
        if event == 'LOCK':
            self._log('[LIMIT-UP GUARD] Y{:.2f} / lastClose Y{:.2f} ({:+.2f}%) '
                 '-> freeze all new legs'.format(price, last_close, rise_pct))
        elif event == 'RELEASE_PENDING':
            self._log('[LIMIT-UP GUARD] opening-board retreat {:+.2f}%; hold {:.0f}s '
                 'before release'.format(rise_pct, LIMIT_UP_RELEASE_HOLD_SEC))
        elif event == 'UNLOCK':
            self._log('[LIMIT-UP GUARD RELEASE] retreat stable for {:.0f}s; '
                 'new legs enabled by capacity'.format(
                     LIMIT_UP_RELEASE_HOLD_SEC))
        return active

    def _new_leg_block_reason(self):
        if self.portfolio.order_uncertain:
            return 'account order outcome uncertain; reconcile before restarting'
        if self.st.get('reentry_pending'):
            return 'next-T awaiting confirmed closing price / valid ATR history'
        if self.st.get('locked', False):
            return self.st.get('lock_reason', 'strategy locked')
        if self.st.get('limit_up_guard', False):
            return 'near upper limit guard active'
        return ''

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if self.st.get('trade_date', '') == today and self.st.get('initialized', False):
            self._refresh_position(); return
        is_new_day = self.st.get('trade_date', '') and self.st['trade_date'] != today
        saved_trail = self.st.get('bt_max_trail', 0) if not is_new_day else 0
        saved_history = self.st.get('price_history', []) if not is_new_day else []
        self._reset_daily()
        self.st['trade_date'] = today; self.st['_guard_date'] = today
        if not is_new_day and saved_trail > 0:
            self.st['bt_max_trail'] = saved_trail; self.st['price_history'] = saved_history

        # ★ 跨日刷新日线缓存，确保指标基于最新数据
        # 当前时点价格与完整历史日线分开管理，禁止用 tick 覆盖历史K线。
        tick_data = self.ctx.get_full_tick([self.stock_qmt]).get(self.stock_qmt, {})
        today_open = float(tick_data.get('open', 0) or 0)
        curr_price_now = float(tick_data.get('lastPrice', 0) or 0)
        last_close = float(tick_data.get('lastClose', 0) or 0)
        self._update_limit_up_guard(
            curr_price_now, tick_data, _time.time())

        self.conn.refresh_daily_cache()
        snapshot = self.conn.load_daily_snapshot(
            cfg.HIST_DATA_LEN, today=today, tick_last_close=last_close,
            tick_time=tick_data.get('timetag') or tick_data.get('time'),
            retries=3, retry_delay=1.0)
        if snapshot is None:
            self._refresh_position()
            self._lock_all_trading('daily data unavailable or stale')
            return

        hist = snapshot['adjusted']
        self.st['reentry_history'] = hist.copy()
        if len(hist) < 60:
            self._lock_all_trading(
                'complete daily bars {} < 60'.format(len(hist)))
            return
        self._refresh_position()
        base_shares = self.st.get('base_shares', 0); base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('entry_price', 0) == 0.0:
            self.st['entry_price'] = self.st.get('base_cost', 0.0)

        opens_list = hist['open'].astype(float).tolist()
        highs_list = hist['high'].astype(float).tolist()
        lows_list = hist['low'].astype(float).tolist()
        closes_list = hist['close'].astype(float).tolist()
        self.st['ma_completed_closes'] = closes_list[-19:]
        self.st['ma_history_date'] = today
        volume_list = hist['volume'].astype(float).tolist()
        latest_complete_close = float(snapshot['raw'].iloc[-1]['close'])
        signal_open, signal_open_source = resolve_signal_open(
            cfg.now_hms(), today_open, latest_complete_close)
        self._log('[DATA-DBG] last_complete={} hist_open[-3:]={} | tick_open={:.2f} | '
             'lastClose={:.2f} | signal_open={:.2f} ({})'.format(
                 snapshot['last_complete_date'],
                 '[' + ', '.join('{:.2f}'.format(value) for value in opens_list[-3:]) + ']',
                 today_open, last_close, signal_open, signal_open_source))
        if signal_open_source.startswith('AFTER-HOURS'):
            self._log('[AFTER-HOURS] signal open uses latest complete close Y{:.2f}'.format(
                signal_open))
        self._log('[VOLUME-DATA] complete_count={} tail={}'.format(
            len(volume_list), volume_list[-3:] if len(volume_list) >= 3 else volume_list))

        signal = compute_signal(
            opens_list, highs_list, lows_list, closes_list, volume_list,
            yesterday_close=last_close, today_open=signal_open)
        if signal is None: return
        signal = apply_quantile_trend_regime(
            signal, opens_list, highs_list, lows_list, closes_list,
            volume_list, signal_open)
        signal['open_price_source'] = signal_open_source
        previous10_volumes = volume_list[-11:-1] if len(volume_list) >= 11 else []
        volume_avg10 = (sum(previous10_volumes) / len(previous10_volumes)
                        if previous10_volumes else 0.0)
        volume_ratio10 = (signal['volume_current'] / volume_avg10
                          if volume_avg10 > 0 else None)
        signal['volume_avg10'] = volume_avg10
        signal['volume_ratio10'] = volume_ratio10
        signal['volume_baseline_count10'] = len(previous10_volumes)
        open_price = signal['open_price']
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0
        if curr_price_now <= 0: curr_price_now = open_price
        pos_value = base_shares * curr_price_now
        total_asset = account[0].m_dBalance if account else 0.0
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0
        capacity = calculate_execution_capacity(
            base_can_use, avail_cash, curr_price_now, cfg.MAX_DAILY_TRADES, self.trade_lot)
        short_lots = capacity['short_lots']
        long_lots = capacity['long_lots']

        signal['short_signal_allowed'] = signal['do_short']
        signal['short_signal_reason'] = signal.get('blocked_reason', '')
        do_short = signal['do_short'] and capacity['can_short']
        short_reason = ''
        if not signal['do_short']:
            short_reason = signal.get('blocked_reason', 'signal blocked')
        elif not capacity['can_short']:
            short_reason = capacity['short_reason']
        do_long = capacity['can_long']
        long_reason = capacity['long_reason'] if not do_long else ''

        buy_trigger_floor = round(open_price * (1.0 - cfg.BUY_TRIGGER_PCT), 2)
        buy_trigger_trail = round(curr_price_now * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
        buy_trigger = max(buy_trigger_floor, buy_trigger_trail)
        sellback_target_hint = round(buy_trigger * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

        signal['do_short'] = do_short; signal['short_reason'] = short_reason
        signal['buy_trigger'] = buy_trigger; signal['buy_trigger_floor'] = buy_trigger_floor
        signal['buy_trigger_trail'] = buy_trigger_trail
        signal['buy_trigger_max_trail'] = buy_trigger_trail
        signal['sellback_target_hint'] = sellback_target_hint

        self.st['daily_signal'] = signal
        self.st['do_short'] = do_short; self.st['do_long'] = do_long
        self.st['long_reason'] = long_reason; self.st['short_lots'] = short_lots
        self.st['long_lots'] = long_lots; self.st['pos_value'] = pos_value
        self.st['pos_pct'] = pos_pct; self.st['avail_cash'] = avail_cash
        self.st['trade_count_short'] = 0; self.st['trade_count_long'] = 0

        for k in ('fstate', 'peak_price', 'dip_price', 'sell_fill_price', 'buyback_target', 'buyback_target_pct'):
            self.st[k] = STATE_IDLE if k == 'fstate' else 0.0
        self.st['day_pnl'] = 0.0
        self.st['state_enter_time'] = cfg.now_hms(); self.st['sell_elapsed_bars'] = 0
        self.st['initialized'] = True
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
            self.st[k] = 0.0
        self.st['bt_max_trail'] = buy_trigger_trail
        self.st['locked'] = False; self.st['lock_reason'] = ''; self.st['lock_since'] = ''

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        found = False
        for pos in positions:
            if pos.m_strInstrumentID == self.stock_code:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                found = True
                break
        if not found:
            self.st['base_shares'] = 0
            self.st['base_can_use'] = 0
            self.st['base_cost'] = 0.0

    def _update_intraday_average(self, tick_data):
        """Cache VWAP and report intraday MA position every ten minutes."""
        average = calculate_intraday_average(
            tick_data.get('amount', 0), tick_data.get('pvolume', 0),
            tick_data.get('lastPrice', 0))
        self.st['intraday_avg_valid'] = average > 0
        if average > 0:
            self.st['intraday_avg_price'] = average
        self._maybe_report_ma(float(tick_data.get('lastPrice', 0) or 0), _time.time())
        return average

    def _maybe_report_ma(self, price, now_ts):
        last_report = float(self.st.get('last_ma_report_time', 0.0) or 0.0)
        if now_ts - last_report < MA_REPORT_INTERVAL_SEC:
            return False
        position = calculate_ma_position(self.st.get('ma_completed_closes', []), price)
        if position is None:
            return False
        self.st['last_ma_report_time'] = now_ts
        self._log('[MA-POS] price Y{:.2f} | MA5 Y{:.2f}: {} {:+.2f}% | MA20 Y{:.2f}: {} {:+.2f}%'.format(
            price, position['ma5'], position['ma5_position'], position['ma5_gap_pct'],
            position['ma20'], position['ma20_position'], position['ma20_gap_pct']))
        if position['risk']:
            self._log('[MA20 RISK] price Y{:.2f} is below 97% of MA20 (risk line Y{:.2f})'.format(
                price, position['ma20_risk_price']))
        return True
    def _recalculate_next_t_triggers(self, completed_by):
        """Re-anchor entries only after a complete closing leg is confirmed."""
        st = self.st
        signal = st.get('daily_signal') or {}
        closing_order = getattr(self, '_last_executed_order', None)
        st['reentry_pending'] = dict(closing_order or {}, completed_by=completed_by)
        return self._retry_atr_reentry()

    def _retry_atr_reentry(self):
        pending = self.st.get('reentry_pending')
        if not pending:
            return False
        now = _time.monotonic()
        if now < pending.get('retry_at', 0):
            return False
        pending['retry_at'] = now + 5.0
        try:
            order_id = pending.get('order_id')
            if order_id is None:
                raise ValueError('closing order id unavailable')
            order = self.conn.trader.query_stock_order(self.conn._account_obj, order_id)
            if order is None or getattr(order, 'stock_code', '') != self.stock_qmt:
                raise ValueError('closing order not returned for this symbol')
            ids = (str(getattr(order, 'order_id', '')),
                   str(getattr(order, 'order_sysid', '')))
            if str(order_id) not in ids:
                raise ValueError('closing order id mismatch')
            price = float(getattr(order, 'traded_price', 0) or 0)
            quantity = int(getattr(order, 'traded_volume', 0) or 0)
            if not math.isfinite(price) or price <= 0 or quantity < pending.get('shares', 1):
                raise ValueError('closing execution price/volume not yet confirmed')
            history = self.st.get('reentry_history')
            if history is None:
                raise ValueError('completed daily history unavailable')
            result = calculate_atr_reentry(price, *[
                history[field].tolist() for field in ('open', 'high', 'low', 'close')])
            if result is None:
                raise ValueError('ATR history invalid or insufficient')
        except Exception as error:
            self._log('[NEXT-T WAIT] {}; new entries paused'.format(error))
            return False

        signal = self.st.get('daily_signal') or {}
        signal['sell_trigger'] = result['sell_trigger']
        signal['sell_trigger_raw'] = result['sell_trigger']
        signal['buy_trigger'] = result['buy_trigger']
        signal['buy_trigger_floor'] = result['buy_trigger']
        signal['buy_trigger_trail'] = result['buy_trigger']
        signal['buy_trigger_max_trail'] = result['buy_trigger']
        signal['sellback_target_hint'] = round(
            result['buy_trigger'] * (1 + cfg.SELLBACK_RISE_PCT), 2)
        signal['trigger_base'] = 'CLOSE_FILL_ATR'
        signal['trigger_base_price'] = price
        signal['reentry'] = result
        scale_reentry_signal(signal)
        self.st['daily_signal'] = signal
        self.st['bt_max_trail'] = result['buy_trigger']
        self.st['next_t_cycle'] = self.st.get('next_t_cycle', 0) + 1
        self.st['reentry_pending'] = None
        self._log('[NEXT-T #{}] {} | closing order={} avg=Y{:.4f} | '
                  'ATR={:.2f}% Q={:.2f} samples={} | '
                  'REV=base*(1+ATR*{:.4f}) -> Y{:.2f} | '
                  'FWD=base*(1-ATR*{:.4f}) -> Y{:.2f} (outward tick rounding)'.format(
                      self.st['next_t_cycle'], pending['completed_by'], order_id, price,
                      result['atr_pct'] * 100, result['quantile'], result['sample_count'],
                      result['up_units'], result['sell_trigger'],
                      result['down_units'], result['buy_trigger']))
        self._log('[REENTRY-UNIT-FORMULA] raw {:.6f} * scale {:.3f} = {:.6f}'.format(
            result['unscaled_up_units'], result['up_units_scale'], result['up_units']))
        return True

    # ═══ v23: 下单前仓位/现金检查 + 严格成交判定 ═══

    def _cur_price(self):
        tick = self.ctx.get_full_tick([self.stock_qmt])
        price = tick.get(self.stock_qmt, {}).get('lastPrice', 0)
        if price <= 0:
            price = self.st.get('daily_signal', {}).get('open_price', 0)
        return price

    def _available_cash(self):
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        cash = account[0].m_dAvailable if account else 0.0
        return max(0.0, cash - self.portfolio.reserved_cash(exclude=self.stock_qmt))

    def _paired_long_capacity(self, price):
        """Capacity for a same-day buy leg backed by still-sellable old shares."""
        self._refresh_position()
        main_reserved = self._leg_shares(self.st.get('long_legs', []))
        reserved = main_reserved
        sellable = max(0, int(self.st.get('base_can_use', 0) or 0))
        pairing_shares = max(0, sellable - reserved)
        capacity = calculate_execution_capacity(
            pairing_shares, self._available_cash(), price, 1, self.trade_lot)
        capacity['reserved_long_shares'] = reserved
        capacity['pairing_shares'] = pairing_shares
        if pairing_shares < self.trade_lot:
            capacity['long_reason'] = (
                'T+1 sellable base shares pairing capacity {} sh '
                '(sellable {} - reserved {}) < {} sh'
                .format(pairing_shares, sellable, reserved, self.trade_lot))
        return capacity

    def _clamp_sell_shares(self, planned):
        """卖出前检查可卖仓位: 实际可卖 = min(计划, base_can_use)。"""
        # 每笔卖单都以券商最新可卖数为准，不能信任先前 tick 的正数缓存。
        self._refresh_position()
        can_use = self.st.get('base_can_use', 0)
        return int(min(planned, can_use))

    def _clamp_buy_shares(self, planned, price):
        """买入前检查现金: 实际可买 = min(计划, 现金可买股数)。"""
        if price <= 0:
            price = self._cur_price()
        avail = self._available_cash()
        if price <= 0:
            return 0
        max_by_cash = int(avail / (price * 1.001))
        return int(min(planned, max_by_cash))

    def _leg_shares(self, legs):
        return sum(s for _, s in legs)

    def _leg_avg_price(self, legs):
        sh = self._leg_shares(legs)
        return sum(p * s for p, s in legs) / sh if sh > 0 else 0.0

    def _short_gross(self, legs, buyback_price):
        """反T毛利 = Σ(各腿卖价 - 买回价) × 各腿股数。"""
        return sum((p - buyback_price) * s for p, s in legs)

    def _buyback_limit_price(self, fallback_price):
        """Use ask1 directly; fall back to the trigger price if ask1 is absent."""
        tick = self.ctx.get_full_tick([self.stock_qmt]).get(self.stock_qmt, {})
        ask_prices = tick.get('askPrice', []) or []
        ask1 = float(ask_prices[0]) if len(ask_prices) > 0 and ask_prices[0] else 0.0
        base_price = ask1 if ask1 > 0 else float(fallback_price or 0.0)
        if base_price <= 0:
            return 0.0
        return round(base_price, 2)

    def _submit_buyback_order(self, shares, fallback_price, label):
        limit_price = self._buyback_limit_price(fallback_price)
        if limit_price <= 0:
            self._log('[{} SKIP] FIX buyback price unavailable'.format(label))
            return 'SKIP', 0
        self._last_buyback_price = limit_price
        self._log('[FIX-BUYBACK] trigger Y{:.2f} -> ask1/fallback limit Y{:.2f}'.format(
            fallback_price, limit_price))
        status, delta = self._submit_order(shares, limit_price, label, style='FIX')
        if delta:
            self._last_buyback_price = self._execution_price
        return status, delta

    def _new_t_shares(self, price, side):
        capacity = self._paired_long_capacity(price)
        cash = None
        if side == 'BUY':
            # 新开正T不能占用任何股票已卖待买回的资金（包括本股票）。
            own_reserve = (self.portfolio.reserved_cash(exclude='') -
                           self.portfolio.reserved_cash(exclude=self.stock_qmt))
            cash = max(0.0, self._available_cash() - own_reserve)
        return calculate_t_shares(
            float(price), capacity['pairing_shares'], self.trade_lot,
            T_TARGET_VALUE, T_POSITION_FRACTION, cash)

    def _submit_order(self, shares, price, label, style='COMPETE'):
        """下单 + 等待成交。shares>0 买入, <0 卖出。

        下单前检查可用仓位(卖出)/现金(买入), 以实际可下单数量下单。
        返回 (status, actual_delta):
          status: 'FILLED' 满额 | 'PARTIAL' 部分 | 'TIMEOUT' 未成交 | 'SKIP' 无可用
          actual_delta: 实际成交股数(带符号, 买正卖负)
        """
        self._last_executed_order = None
        side = 'SELL' if shares < 0 else 'BUY'
        is_new_leg = label in ('REV-T sell', 'FWD-T buy')
        planned = self._new_t_shares(price, side) if is_new_leg else abs(shares)
        if side == 'SELL':
            actual = self._clamp_sell_shares(planned)
        else:
            actual = self._clamp_buy_shares(planned, price)
        if is_new_leg:
            actual = actual // self.trade_lot * self.trade_lot
            self._log('[T-SIZE] {} | price=Y{:.2f} target=Y{:.0f} '
                      'base-fraction={:.0f}% | {} units x {} sh = {} sh (~Y{:.0f})'.format(
                          label, price, T_TARGET_VALUE, T_POSITION_FRACTION * 100,
                          actual // self.trade_lot, self.trade_lot, actual, price * actual))
        if self.dry_run:
            self._log('[SIGNAL-ORDER] {} {} shares={} price={}; no order submitted'.format(
                label, side, actual, price))
            return 'SKIP', 0
        if actual < self.trade_lot:
            self._log('[{} SKIP] {} 不足: planned {} actual {}'.format(
                label, '可卖' if side == 'SELL' else '现金', planned, actual))
            return 'SKIP', 0
        signed = -actual if side == 'SELL' else actual
        snap = self._snapshot_account()
        price_str = 'MKT' if price <= 0 else 'Y{:.2f}'.format(price)
        self._log('[ORDER-{}] {} × {} sh'.format(label, price_str, actual))
        if self.portfolio.order_uncertain:
            self._log('[ORDER-BLOCKED] account has unresolved order')
            return 'SKIP', 0
        self.portfolio.before_submit(self.stock_qmt, label, signed, price)
        with self.portfolio.watchdog.scope('SUBMIT ' + self.stock_qmt):
            order_id = order_shares(self.stock_qmt, signed, style, price, self.ctx, ACCOUNT)
        if order_id is None or str(order_id) in ('', '0', '-1'):
            self._log('[ORDER-REJECTED] no valid order id; no closing price recorded')
            self.portfolio.order_uncertain = True
            raise RuntimeError('submission outcome unknown; inspect broker before resuming')
        if str(order_id) in self.portfolio.own_order_ids:
            self._log('[ORDER-ID-REUSED] broker returned existing order={}; '
                      'account orders stopped before execution lookup'.format(order_id))
            self.portfolio.order_uncertain = True
            raise RuntimeError('duplicate broker order id {}; inspect broker before resuming'.format(
                order_id))
        self.portfolio.own_order_ids.add(str(order_id))
        self._submitted_order_id = order_id
        status, delta = self._wait_for_fill(snap, signed, label, price, signed)
        if delta:
            self._last_executed_order = dict(order_id=order_id, shares=abs(delta))
        return status, delta

    # ═══ 成交确认 ═══

    def _wait_for_fill(self, snap_before, expected_shares_delta,
                       label, trade_price, trade_shares, timeout_sec=FILL_TIMEOUT_SEC):
        """Use this order's broker execution, never account position differences."""
        wanted = abs(expected_shares_delta)
        sign = 1 if expected_shares_delta > 0 else -1
        order_id = self._submitted_order_id
        deadline = _time.monotonic() + timeout_sec
        cancelled = False
        while True:
            try:
                order = self.conn.trader.query_stock_order(self.conn._account_obj, order_id)
                if order is not None:
                    ids = (str(getattr(order, 'order_id', '')), str(getattr(order, 'order_sysid', '')))
                    if order.stock_code != self.stock_qmt or str(order_id) not in ids:
                        raise ValueError('order identity mismatch')
                    volume = int(order.traded_volume or 0)
                    actual_price = float(order.traded_price or 0)
                    # xtquant.xtconstant: PART_CANCEL=53 CANCELED=54 SUCCEEDED=56 JUNK=57.
                    terminal = int(order.order_status) in (53, 54, 56, 57)
                    if volume > wanted:
                        raise ValueError('execution quantity exceeds submitted quantity')
                    if terminal and volume == 0:
                        return 'TIMEOUT', 0
                    if (volume == wanted or terminal) and volume > 0 and math.isfinite(actual_price) and actual_price > 0:
                        self._execution_price = actual_price
                        gross, completed, cycle = self.execution_book.record(
                            order_id, label, sign * volume, actual_price)
                        self.total_pnl += gross
                        self.st['day_pnl'] = self.st.get('day_pnl', 0) + gross
                        self.total_t_days += int(completed)
                        self._last_cycle_gross = cycle
                        if completed:
                            self._log('[CYCLE-CLOSED] {} gross=Y{:.2f} fees=NOT_INCLUDED'.format(label, cycle))
                        self.portfolio.own_order_ids.update(value for value in ids if value)
                        self._refresh_position()
                        self._log('[EXECUTION] order={} {} qty={} avg=Y{:.4f} '
                                  'reference=Y{:.4f} realized-gross=Y{:.2f} '
                                  'total-gross=Y{:.2f} fees=NOT_INCLUDED'.format(
                                      order_id, label, volume, actual_price, trade_price, gross, self.total_pnl))
                        return ('FILLED' if volume == wanted else 'PARTIAL'), sign * volume
            except Exception as error:
                self._log('[EXECUTION-WAIT] order={} {}'.format(order_id, error))
            if _time.monotonic() >= deadline:
                if not cancelled:
                    self.conn.cancel_order(order_id)
                    cancelled = True
                    deadline = _time.monotonic() + timeout_sec
                else:
                    self.portfolio.order_uncertain = True
                    self._log('[ORDER-UNCERTAIN] order={}; account orders paused; reconcile broker orders'.format(order_id))
                    raise RuntimeError('unresolved broker order ' + str(order_id))
            _time.sleep(0.5)

    # ═══ 快照 & 校验 ═══

    def _snapshot_account(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        shares = 0; can_use = 0; cost = 0.0
        for pos in positions:
            if pos.m_strInstrumentID == self.stock_code:
                shares = pos.m_nVolume; can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                cost = pos.m_dOpenPrice; break
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        cash = account[0].m_dAvailable if account else 0.0
        tick = self.ctx.get_full_tick([self.stock_qmt])
        price = tick.get(self.stock_qmt, {}).get('lastPrice', 0)
        if price <= 0: price = self.st.get('daily_signal', {}).get('open_price', 0)
        return {'shares': shares, 'can_use': can_use, 'cash': cash,
                'cost': cost, 'total_asset': account[0].m_dBalance if account else 0.0, 'price': price}

    # ★ v21: 成交日志优化 — 清晰打印价格×数量+持仓变化

    # ═══ v21: 信号+计划合并输出 (无分隔线, 无空行) ═══

    def _print_daily_brief(self, signal):
        if not cfg.is_market_open(cfg.now_hms()):
            self._log('[NON-TRADING PREVIEW] indicative plans only; no orders outside trading hours; '
                      'next trading day recalculates from its opening price')
        reference = float(signal.get('open_price', 0) or 0)
        if signal.get('trigger_base') == 'CLOSE_FILL_ATR':
            reference = signal['reentry']['base']
        if reference > 0:
            self._log('[T-SIZE PLAN] at base Y{:.2f}: REV {} sh / FWD {} sh; '
                      'rechecked at order submission'.format(
                          reference, self._new_t_shares(reference, 'SELL'),
                          self._new_t_shares(reference, 'BUY')))
        trend = signal.get('trend', '?')
        trend_labels = {'strong_bull': 'STRONG-BULL', 'weak_bull': 'WEAK-BULL', 'bull': 'ADAPTIVE-BULL', 'sideways': 'ADAPTIVE-SIDEWAYS', 'bear': 'ADAPTIVE-BEAR'}
        open_p = signal.get('open_price', 0); close_y = signal.get('close_yday', 0)
        open_source = format_signal_base_source(signal.get('open_price_source'))
        if signal.get('trigger_base') == 'CLOSE_FILL_ATR':
            open_p = signal['reentry']['base']
            open_source = 'confirmed closing fill; next-cycle base'
        atr_pct = signal.get('atr_pct', 0) * 100; rsi_v = signal.get('rsi', 0)
        vol_r = signal.get('vol_ratio'); sell_mult = signal.get('sell_mult', 0)
        volume_valid = signal.get('volume_valid', False)
        vol_display = '{:.2f}'.format(vol_r) if volume_valid and vol_r is not None else 'N/A'
        sell_base = signal.get('sell_mult_base', 0); sell_trig = signal.get('sell_trigger', 0)
        range_capped = signal.get('range_capped', False)
        do_short = signal.get('do_short', False)
        base_shares = self.st.get('base_shares', 0); base_can_use = self.st.get('base_can_use', 0)
        pos_pct = self.st.get('pos_pct', 0); avail_cash = self.st.get('avail_cash', 0)
        do_long = self.st.get('do_long', False)
        short_lots = self.st.get('short_lots', 0); long_lots = self.st.get('long_lots', 0)

        tick = self.ctx.get_full_tick([self.stock_qmt])
        curr_price = tick.get(self.stock_qmt, {}).get('lastPrice', 0)
        if curr_price <= 0: curr_price = open_p
        pos_value = base_shares * curr_price
        trend_cn = trend_labels.get(trend, trend)

        # 行1: 核心指标
        self._log('[SIGNAL] {} | SignalBase Y{:.2f} (source: {}) | ATR {:.1f}% | RSI {:.0f} | Vol_ratio {} | Mult {:.2f} | base-trigger Y{:.2f}{} {}'.format(
            trend_cn, open_p, open_source, atr_pct, rsi_v, vol_display, sell_mult, sell_trig,
            '(range-capped)' if range_capped else '',
            '[REV-T blocked:{}]'.format(
                signal.get('short_reason') or
                signal.get('blocked_reason', 'unknown')) if not do_short else ''))
        regime = signal.get('quantile_trend') or {}
        self._log('[REFERENCE] ' + self._reference_log())
        if signal.get('trigger_base') == 'CLOSE_FILL_ATR':
            reentry = signal['reentry']
            self._log('[REENTRY-UNIT-FORMULA] close-fill Y{:.4f} | raw {:.6f} * scale {:.3f} = {:.6f}'.format(
                reentry['base'], reentry.get('unscaled_up_units', reentry['up_units']),
                reentry.get('up_units_scale', 1.0), reentry['up_units']))
        elif signal.get('quantile_trend_active'):
            self._log('[QUANTILE-TREND] score {score:+.3f} | rank {rank:.3f} | '
                 'trigger-Q {quantile:.3f} | samples {samples} | span {span}d | '
                 'ATR-units {units:.3f}'.format(
                     score=regime.get('trend_score', 0.0),
                     rank=regime.get('trend_rank', 0.0),
                     quantile=regime.get('trigger_quantile', 0.0),
                     samples=regime.get('sample_count', 0),
                     span=regime.get('lookback_span', 0),
                     units=regime.get('trigger_units', 0.0)))
            self._log('[QUANTILE-FORMULA] units=Quantile(next-day open-to-high, '
                 'tail {tail:.3f} + trend-rank {rank:.3f}*(1-2*tail)) '
                 '= Q{quantile:.3f} -> raw {raw:.4f} * scale {scale:.2f} = {units:.4f}'.format(
                     tail=regime.get('tail_rank', 0.0),
                     rank=regime.get('trend_rank', 0.0),
                     quantile=regime.get('trigger_quantile', 0.0),
                     raw=regime.get('unscaled_trigger_units', regime.get('trigger_units', 0.0)),
                     scale=regime.get('units_scale', 1.0),
                     units=regime.get('trigger_units', 0.0)))
        vol_last = signal.get('volume_current', 0)
        vol10_ratio = signal.get('volume_ratio10')
        vol20_ratio = signal.get('vol_ratio')
        self._log('[VOLUME] last {:.0f} | prev10_avg {:.0f} (last/10d {}) | '
             'prev20_avg {:.0f} (last/20d {}) | n10={} n20={} | {}'.format(
                 vol_last, signal.get('volume_avg10', 0),
                 '{:.2f}x'.format(vol10_ratio) if vol10_ratio is not None else 'N/A',
                 signal.get('volume_avg20', 0),
                 '{:.2f}x'.format(vol20_ratio) if vol20_ratio is not None else 'N/A',
                 signal.get('volume_baseline_count10', 0),
                 signal.get('volume_baseline_count', 0),
                 'VALID' if volume_valid else 'INVALID-neutral'))

        # 因子
        fd = signal.get('factor_details', {})
        if fd:
            self._log('[FACTOR] {}'.format(' '.join('{} {:+.2f} | '.format(k, v) for k, v in fd.items())))

        # 行2: 持仓 + 方向
        bits = ['Position:{} sh Y{:,.0f}({:.0f}%)'.format(base_shares, pos_value, pos_pct),
                'Cash:Y{:,.0f}'.format(avail_cash),
                'Sellable:{} lots({} sh)'.format(
                    base_can_use // self.trade_lot, base_can_use)]
        self._log('[ACCOUNT] {}'.format(' | '.join(bits)))

        planned_short = self._new_t_shares(curr_price, 'SELL')
        planned_long = self._new_t_shares(curr_price, 'BUY')
        remaining_short = max(0, cfg.MAX_DAILY_TRADES - self.st.get('trade_count_short', 0))
        remaining_long = max(0, cfg.MAX_DAILY_TRADES - self.st.get('trade_count_long', 0))
        # 行3-4: 反T / 正T
        guard_active = self.st.get('limit_up_guard', False)
        atr_fraction = float(signal.get('atr_pct', 0.0) or 0.0)
        rev_buyback_pct = atr_fraction * cfg.BUYBACK_TRIGGER_MULT
        execution_trig = self._rev_sell_trigger()
        rev_buyback_plan = round(execution_trig * (1.0 - rev_buyback_pct), 2)
        sell_raw = float(signal.get('sell_trigger_raw', sell_trig) or sell_trig)
        if signal.get('trigger_base') == 'CLOSE_FILL_ATR':
            reentry = signal['reentry']
            sell_formula = ('base-trigger Y{sell:.2f} = close-fill Y{base:.4f}*'
                            '(1+ATR {atr:.4f}%*(reentry_units {raw:.6f}*scale {scale:.3f}))'
                            ' [ceil cent; min distance Y0.01]').format(
                                sell=sell_trig, base=reentry['base'],
                                atr=reentry['atr_pct'] * 100,
                                raw=reentry.get('unscaled_up_units', reentry['up_units']),
                                scale=reentry.get('up_units_scale', 1.0))
        elif signal.get('quantile_trend_active'):
            sell_formula = ('base-trigger Y{sell:.2f} = open Y{open:.2f}*'
                            '(1+ATR {atr:.2f}%*(raw {raw:.6f}*scale {scale:.3f}))').format(
                                sell=sell_trig, open=open_p,
                                atr=atr_fraction * 100,
                                raw=regime.get('unscaled_trigger_units', regime.get('trigger_units', 0)),
                                scale=regime.get('units_scale', 1.0))
        else:
            sell_formula = ('base-trigger Y{sell:.2f} = open Y{open:.2f}*'
                            '(1+ATR {atr:.2f}%*mult {mult:.2f}*scale {scale:.2f})').format(
                                sell=sell_trig, open=open_p, atr=atr_fraction * 100,
                                mult=sell_mult, scale=cfg.SELL_TRIGGER_SCALE)
        if signal.get('range_capped', False):
            range_cap = open_p * (
                1.0 + float(signal.get('daily_range_ma10', 0.0) or 0.0) *
                cfg.DAILY_RANGE_CAP_MULT)
            sell_formula += ' | raw Y{:.2f}, range-cap Y{:.2f}'.format(
                sell_raw, range_cap)
        rev_plan = ('{} | buyback Y{buyback:.2f} = '
                    'Y{sell:.2f}*(1-{atr:.2f}%*{mult:.2f})').format(
                        sell_formula + ' | ' + self._reference_log(),
                        sell=execution_trig, buyback=rev_buyback_plan,
                        atr=atr_fraction * 100,
                        mult=cfg.BUYBACK_TRIGGER_MULT)
        if guard_active:
            self._log('[REV-T] FROZEN near upper limit; capacity {} lots | {}'.format(
                short_lots, rev_plan))
        elif do_short:
            self._log('[REV-T] {} plan {} sh / remaining {} entries | {}'.format('ENABLED' if cfg.is_market_open(cfg.now_hms()) else 'NON-TRADING PREVIEW', planned_short, remaining_short, rev_plan))
        else:
            reason = signal.get('short_reason', signal.get('blocked_reason', 'unknown'))
            self._log('[REV-T] BLOCKED {} | {}'.format(reason, rev_plan))

        buy_trig = signal.get('buy_trigger', 0)
        buy_floor = signal.get('buy_trigger_floor', 0)
        buy_trail = signal.get('buy_trigger_trail', 0)
        sell_hint = signal.get('sellback_target_hint', 0)
        max_trail = signal.get('buy_trigger_max_trail', buy_trail)
        fwd_plan = ('plan buy Y{buy:.2f} = max(floor Y{floor:.2f}, max-trail '
                    'Y{max_trail:.2f}); current-trail Y{trail:.2f} | plan sell Y{sell:.2f} = '
                    'Y{buy:.2f}*(1+{rise:.1f}%) | planned value~Y{lot:,.0f}').format(
                        buy=buy_trig, floor=buy_floor, trail=buy_trail,
                        max_trail=max_trail,
                        sell=sell_hint, rise=cfg.SELLBACK_RISE_PCT * 100,
                        lot=buy_trig * planned_long)
        if guard_active:
            self._log('[FWD-T] FROZEN near upper limit; capacity {} lots | {}'.format(
                long_lots, fwd_plan))
        elif do_long:
            self._log('[FWD-T] {} plan {} sh / remaining {} entries | {}'.format('ENABLED' if cfg.is_market_open(cfg.now_hms()) else 'NON-TRADING PREVIEW', planned_long, remaining_long, fwd_plan))
        else:
            self._log('[FWD-T] BLOCKED {} | {}'.format(
                self.st.get('long_reason', 'unknown'), fwd_plan))

        # 累计
        if self.total_t_days > 0:
            self._log('[CUM] {} trades gross~Y{:,.0f}'.format(self.total_t_days, self.total_pnl))

    # ═══ 状态机 ═══



    def _reset_strength_reference(self):
        self.strength_engine = IntradayStrength(StrengthConfig(
            history_days=STRENGTH_HISTORY_DAYS, quantile=STRENGTH_LOWER_QUANTILE,
            lookback=STRENGTH_LOOKBACK_MIN, smooth=STRENGTH_SMOOTH_MIN,
            warmup=STRENGTH_WARMUP_MIN, open_units=STRENGTH_OPEN_UNITS,
            average_units=STRENGTH_AVERAGE_UNITS, momentum_units=STRENGTH_MOMENTUM_UNITS,
            rebound_units=STRENGTH_REBOUND_UNITS, rebound_min=STRENGTH_REBOUND_MIN,
            rebound_max=STRENGTH_REBOUND_MAX, average_buffer=STRENGTH_AVERAGE_BUFFER,
            strong=STRENGTH_STRONG, max_gap=STRENGTH_MAX_GAP_SEC))
        self._strength_key = None
        self._strength_lower = None
        self._strength_result = {}
        self._strength_phase = None
        self._strength_log_time = 0
        self._shadow_strength_armed = False

    def _strength_cancel_arm(self, reason):
        st = self.st
        if st.get('fstate') == STATE_SPIKING and st.get('strength_armed'):
            st['fstate'] = STATE_IDLE
            st['trade_count_short'] = max(0, st.get('trade_count_short', 0) - 1)
            st['peak_price'] = 0
            st['strength_armed'] = False
            st['rebound_armed'] = False
            self._log('[STRENGTH-ARM CANCELED] ' + reason)

    def _invalidate_strength(self, reason):
        original = (self.st.get('daily_signal') or {}).get('sell_trigger', 0)
        self._strength_result = self.strength_engine.invalidate(original, reason)
        self._shadow_strength_armed = False
        if self._strength_phase != reason:
            self._strength_phase = reason
            self._log('[STRENGTH {}] {} minutes=0; original reference, observation reset'.format(
                INTRADAY_REFERENCE_MODE.upper(), reason))
        if INTRADAY_REFERENCE_MODE == 'active':
            self._strength_cancel_arm(reason)

    def _update_strength_reference(self, price, now_ts, tick_data):
        st = self.st
        sig = st.get('daily_signal') or {}
        original = float(sig.get('sell_trigger', 0) or 0)
        reentry = sig.get('reentry') or {}
        is_reentry = sig.get('trigger_base') == 'CLOSE_FILL_ATR'
        base = float(reentry.get('base', 0) if is_reentry else sig.get('open_price', 0) or 0)
        atr = float(reentry.get('atr_pct', sig.get('atr_pct', 0)) if is_reentry else sig.get('atr_pct', 0) or 0)
        opening = float(tick_data.get('open', 0) or 0)
        average = st.get('intraday_avg_price', 0) if st.get('intraday_avg_valid') else 0
        key = (st.get('trade_date'), base, original, atr, st.get('next_t_cycle', 0))
        if key != self._strength_key:
            self.strength_engine.reset()
            self._strength_key, self._strength_lower = key, None
            self._shadow_strength_armed = False
        error = None
        if self._strength_lower is None:
            try:
                history = st.get('reentry_history')
                if history is None:
                    raise ValueError('completed daily history unavailable')
                self._strength_lower = lower_excursion_units(*[
                    history[column].astype(float).tolist() for column in ('open', 'high', 'low', 'close')],
                    config=self.strength_engine.config)
            except (ValueError, KeyError, TypeError) as exc:
                error = str(exc)
        active = INTRADAY_REFERENCE_MODE == 'active'
        frozen = st.get('fstate') == STATE_SPIKING if active else self._shadow_strength_armed
        if error:
            result = self.strength_engine.invalidate(original, 'INVALID_HISTORY')
            result['reason'] = error
        else:
            result = self.strength_engine.update(
                now_ts, price, opening, average, atr, base, original, self._strength_lower,
                frozen=frozen, blocked=self.has_open_legs())
        self._strength_result = result
        if active:
            if result['cancel'] or result['phase'] in ('INVALID', 'INVALID_HISTORY', 'GAP', 'WARMUP'):
                self._strength_cancel_arm(result['phase'])
        elif result['cancel'] or result['phase'] in ('INVALID', 'INVALID_HISTORY', 'GAP', 'WARMUP', 'EXISTING_LEG'):
            self._shadow_strength_armed = False
        elif (result['touched'] and not self._shadow_strength_armed and
              st.get('fstate') == STATE_IDLE and st.get('do_short') and
              not self._new_leg_block_reason()):
            self._shadow_strength_armed = True
            self._log('[STRENGTH-SHADOW-TOUCH] price=Y{:.2f} candidate=Y{:.2f}; '
                      'hypothetical only, no order/count change'.format(price, result['effective']))
        phase = result['phase']
        if phase != self._strength_phase or now_ts - self._strength_log_time >= STRENGTH_LOG_INTERVAL_SEC:
            self._strength_phase, self._strength_log_time = phase, now_ts
            self._log('[STRENGTH {}] {} minutes={} components={} S={:.3f} '
                      'base=Y{:.2f} base-trigger=Y{:.2f} candidate={} execution=Y{:.2f} '
                      'U0={:.4f} Ul={:.4f} U={:.4f} limit={} reason={}'.format(
                          INTRADAY_REFERENCE_MODE.upper(), phase, result['minutes'],
                          [round(v, 3) for v in result.get('components', [])], result.get('strength', 0),
                          base, original, self._candidate_log(), self._rev_sell_trigger(),
                          result.get('upper_units', 0), result.get('lower_units', self._strength_lower or 0),
                          result.get('effective_units', 0), result.get('limit', '-'),
                          result.get('reason', phase)))

    def _candidate_log(self):
        if not cfg.is_market_open(cfg.now_hms()):
            return 'NON_TRADING'
        result = self._strength_result
        if result.get('minutes', 0) < STRENGTH_WARMUP_MIN or result.get('phase') not in (
                'ADAPTIVE', 'STRONG', 'FROZEN', 'TOUCHED', 'BELOW_MARKET_HOLD'):
            return 'NOT_READY'
        return 'Y{:.2f}'.format(result['effective'])

    def _reference_log(self):
        base_trigger = (self.st.get('daily_signal') or {}).get('sell_trigger', 0)
        return 'base-trigger Y{:.2f} | execution Y{:.2f} | {}-candidate {}'.format(
            base_trigger, self._rev_sell_trigger(), INTRADAY_REFERENCE_MODE, self._candidate_log())

    def _rev_sell_trigger(self):
        original = (self.st.get('daily_signal') or {}).get('sell_trigger', 999999)
        if INTRADAY_REFERENCE_MODE == 'active':
            if self.has_open_legs() or self.st.get('fstate') not in (STATE_IDLE, STATE_SPIKING):
                return original
            return self._strength_result.get('effective', original) or original
        return (self.st.get('rebound_effective') or original) if REBOUND_ENABLED else original

    def _update_rebound_reference(self, price, now_ts, tick_data):
        st = self.st
        signal = st.get('daily_signal') or {}
        original = signal.get('sell_trigger', 0)
        if not original:
            return
        identity = [st.get('trade_date'), signal.get('trigger_base'),
                    signal.get('trigger_base_price'), original, st.get('next_t_cycle', 0)]
        if st.get('rebound_identity') != identity:
            st.update(rebound_identity=identity, rebound_memory={}, rebound_effective=original)
        if not REBOUND_ENABLED:
            st['rebound_effective'] = original
            return
        # Do not change existing legs or ladder entries.
        if self.has_open_legs() or st.get('fstate') not in (STATE_IDLE, STATE_SPIKING):
            st['rebound_effective'] = original
            return
        opening = float(tick_data.get('open', 0) or 0)
        average = st.get('intraday_avg_price', 0) if st.get('intraday_avg_valid') else 0
        previous = st.get('rebound_effective', original)
        reentry = signal.get('reentry', {}) if signal.get('trigger_base') == 'CLOSE_FILL_ATR' else {}
        effective, mode = rebound_reference(
            st.setdefault('rebound_memory', {}), now_ts, price, opening, average,
            signal.get('atr_pct', 0), original, REBOUND_WINDOW_SEC,
            REBOUND_CONFIRM_SEC, REBOUND_ATR_UNITS, REBOUND_MIN_PCT,
            REBOUND_MAX_PCT, REBOUND_AVERAGE_UNITS, REBOUND_WEAK_UNITS,
            REBOUND_STRONG_UNITS, reentry.get('base', 0), reentry.get('up_units', 0),
            REENTRY_WEAK_DISCOUNT, REENTRY_WEAK_FULL_UNITS)
        st['rebound_effective'] = effective
        if st.get('rebound_armed') and st.get('fstate') == STATE_SPIKING and mode in ('STRONG', 'INVALID', 'WARMUP'):
            st['fstate'] = STATE_IDLE
            st['trade_count_short'] = max(0, st.get('trade_count_short', 0) - 1)
            st['peak_price'] = 0
            st['rebound_armed'] = False
            self._log('[REB-ARM CANCELED] {}; return to original entry reference'.format(mode))
        if abs(effective - previous) >= .005:
            memory = st['rebound_memory']
            if mode == 'REBOUND' and 'effective_units' in memory:
                self._log('[REB-REENTRY] original=Y{:.2f} effective=Y{:.2f} | '
                          'units={:.4f}*(1-{:.2f}*weakness {:.3f})={:.4f}; '
                          'min(original, ceil-cent(max(base Y{:.2f}*(1+ATR {:.2f}%*units), '
                          'avg Y{:.2f}*(1+ATR*{:.2f})))); reference held during rebound'.format(
                              original, effective, memory['reentry_units'], memory['reentry_discount'],
                              memory['weakness'], memory['effective_units'], memory['reentry_base'],
                              signal.get('atr_pct', 0)*100, memory.get('average', average), REBOUND_AVERAGE_UNITS))
                return
            self._log('[REB-REF] {} original=Y{:.2f} effective=Y{:.2f} | '
                      'min(original, max(low Y{:.2f}*(1+{:.2f}%), avg Y{:.2f}*(1+ATR*{:.2f})))'.format(
                          mode, original, effective, memory.get('low', 0),
                          memory.get('rebound', 0)*100, average, REBOUND_AVERAGE_UNITS))

    def _update_fwd_buy_trigger(self, price):
        st = self.st
        signal = st.get('daily_signal') or {}
        if (st.get('fstate') != STATE_IDLE or
                signal.get('trigger_base') in ('INTRADAY_AVG', 'CLOSE_FILL_ATR') or
                not math.isfinite(price) or price <= 0):
            return
        current_trail = round(price * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
        max_trail = max(st.get('bt_max_trail', 0), current_trail)
        floor = signal.get('buy_trigger_floor', 0)
        st['bt_max_trail'] = max_trail
        signal['buy_trigger_trail'] = current_trail
        signal['buy_trigger_max_trail'] = max_trail
        signal['buy_trigger'] = max(floor, max_trail)
        signal['sellback_target_hint'] = round(
            signal['buy_trigger'] * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

    def _handle_idle(self, price):
        st = self.st; signal = st.get('daily_signal', {})
        if self._new_leg_block_reason():
            return
        if st.get('do_short', False):
            if cfg.now_hms() >= SHORT_NEW_ENTRY_CUTOFF:
                return
            trigger = self._rev_sell_trigger()
            if price >= trigger:
                can_use = st.get('base_can_use', st['base_shares'])
                if can_use < self.trade_lot: return
                tc = st.get('trade_count_short', 0)
                if tc >= cfg.MAX_DAILY_TRADES or st.get('locked', False): return
                st['trade_count_short'] = tc + 1
                st['strength_armed'] = (INTRADAY_REFERENCE_MODE == 'active' and
                                        trigger < signal.get('sell_trigger', trigger))
                st['rebound_armed'] = trigger < signal.get('sell_trigger', trigger)
                st['fstate'] = STATE_SPIKING; st['peak_price'] = price
                st['short_arm_bars'] = 0
                st['short_arm_trigger'] = trigger
                st['state_enter_time'] = cfg.now_hms()
                self._log('[REV-T spike #{}/{}] Y{:.2f} >= Y{:.2f}'.format(tc + 1, cfg.MAX_DAILY_TRADES, price, trigger))
                return
        if st.get('do_long', False):
            buy_trigger = signal.get('buy_trigger', 0)
            if price <= buy_trigger:
                tc = st.get('trade_count_long', 0)
                if tc >= cfg.MAX_DAILY_TRADES: return
                st['trade_count_long'] = tc + 1
                st['fstate'] = STATE_BT_DIPPING; st['bt_dip_price'] = price
                st['bt_buy_trigger'] = buy_trigger; st['state_enter_time'] = cfg.now_hms()
                self._log('[FWD-T dip #{}/{}] Y{:.2f} <= Y{:.2f}(-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, buy_trigger, (buy_trigger - price) / buy_trigger * 100))

    def _handle_spiking(self, price):
        st = self.st
        st['short_arm_bars'] = st.get('short_arm_bars', 0) + 1
        if price > st['peak_price']: st['peak_price'] = price
        peak = st['peak_price']; pullback = (peak - price) / peak if peak > 0 else 0
        trigger = st.get('short_arm_trigger', self._rev_sell_trigger())
        if confirmed_short_reversal(trigger, peak, price, st['short_arm_bars']):
            block_reason = self._new_leg_block_reason()
            if block_reason:
                self._log('[REV-T ARM CANCELED] {}'.format(block_reason))
                st['trade_count_short'] = max(
                    0, st.get('trade_count_short', 0) - 1)
                st['fstate'] = STATE_IDLE
                st['peak_price'] = 0.0
                return
            self._log('[REV-T sell trig] peak Y{:.2f} pullback {:.2f}% → Y{:.2f}'.format(peak, pullback * 100, price))
            atr_pct = st['daily_signal']['atr_pct']; buyback_pct = atr_pct * cfg.BUYBACK_TRIGGER_MULT
            buyback_target = round(price * (1.0 - buyback_pct), 2)
            st['buyback_target'] = buyback_target
            st['buyback_target_pct'] = buyback_pct * 100
            st['sell_elapsed_bars'] = 0; st['state_enter_time'] = cfg.now_hms()
            # ★ v23: 下单前检查可卖仓位, 以实际可卖数量下单
            status, delta = self._submit_order(-self.trade_lot, price, 'REV-T sell')
            if delta: price = self._execution_price
            if status in ('SKIP', 'TIMEOUT'):
                if status == 'TIMEOUT':
                    self._log('[REV-T sell TIMEOUT] 未成交, 回 IDLE')
                st['trade_count_short'] = max(0, st.get('trade_count_short', 0) - 1)
                st['fstate'] = STATE_IDLE
                return
            # FILLED / PARTIAL: 按实际成交股数入腿
            actual_sold = -delta
            st['buyback_target'] = round(price * (1.0 - buyback_pct), 2)
            st['sell_fill_price'] = price
            st['short_legs'].append((price, actual_sold))
            st['ladder_sell_target'] = round(price * (1.0 + LADDER_UP_STEP_PCT), 2) if status == 'FILLED' else 0.0
            if status == 'PARTIAL':
                self._log('[REV-T sell PARTIAL] 实际卖出 {} sh'.format(actual_sold))
            st['fstate'] = STATE_SOLD

    def _handle_sold(self, price):
        st = self.st; sp = st['sell_fill_price']; bt = st['buyback_target']
        # ★ v22: 阶梯加卖 — 价格涨至更高一档(卖价+阶梯幅度) → 追加冲高回落卖出
        ladder = st.get('ladder_sell_target', 0.0)
        if ladder > 0 and price >= ladder:
            tc = st.get('trade_count_short', 0)
            can_use = st.get('base_can_use', st['base_shares'])
            if (can_use >= self.trade_lot and tc < cfg.MAX_DAILY_TRADES and
                    not self._new_leg_block_reason()):
                st['trade_count_short'] = tc + 1
                st['ladder_sold_count'] = st.get('ladder_sold_count', 0) + 1
                st['fstate'] = STATE_SPIKING; st['peak_price'] = price
                st['short_arm_bars'] = 0
                st['short_arm_trigger'] = ladder
                st['state_enter_time'] = cfg.now_hms()
                self._log('[REV-T ladder sell #{}/{}] Y{:.2f} >= Y{:.2f}(sell+{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, ladder, LADDER_UP_STEP_PCT * 100))
                return
        tightened_bt = bt
        if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
            tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] *
                                 cfg.BUYBACK_TRIGGER_MULT * cfg.BUYBACK_TIGHTEN_MULT)
            tightened_bt = round(max(tightened_bt, bt), 2)
        # 原买回触发价优先：一旦触及，继续沿用v34的探底回升买回流程。
        if price <= tightened_bt:
            st['fstate'] = STATE_DIPPING; st['dip_price'] = price
            st['state_enter_time'] = cfg.now_hms()
            self._log('[Buyback trig {}] Y{:.2f}(-{:.2f}%)'.format(
                '(tightened)' if tightened_bt > bt else '', price, (sp - price) / sp * 100))
            return


    def _handle_dipping(self, price):
        st = self.st
        if price < st['dip_price']: st['dip_price'] = price
        dip = st['dip_price'] or price; bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            legs = st['short_legs'] or [(st['sell_fill_price'], self.trade_lot)]
            total_shares = self._leg_shares(legs)
            self._log('[REV-T buyback trig] low Y{:.2f} bounce {:.2f}% → Y{:.2f}'.format(
                dip, bounce * 100, price))
            bought = self._do_buyback(price, 'NORMAL')
            if bought >= total_shares and total_shares > 0:
                buyback_price = getattr(self, '_last_buyback_price', price)
                gross = self._last_cycle_gross
                self._log('[REV-T done] buyback Y{:.2f} x {}sh gross~Y{:,.0f}'.format(
                    buyback_price, bought, gross))

    def _do_buyback(self, price, reason=''):
        st = self.st
        # ★ v23: 一次性买回全部未平仓反T腿 (按实际股数)
        legs = st['short_legs'] or [(st.get('sell_fill_price', price), self.trade_lot)]
        shares = self._leg_shares(legs)
        if shares <= 0:
            return 0
        status, delta = self._submit_buyback_order(
            shares, price, 'REV-T buyback({})'.format(reason))
        if delta: price = self._execution_price
        bought = delta if delta > 0 else 0
        if bought <= 0:
            self._log('[Buyback {}-FAIL] 未成交, 保持 SOLD 继续监控'.format(reason))
            st['fstate'] = STATE_SOLD
            return 0
        if bought >= shares:
            st['short_legs'] = []
            st['ladder_sell_target'] = 0.0; st['ladder_sold_count'] = 0
            st['fstate'] = STATE_DONE
            self._recalculate_next_t_triggers('REV-T')
            self._maybe_resume_trading()
            return bought
        # 部分买回: 保留未买回部分继续监控
        remaining = shares - bought
        st['short_legs'] = list(self.execution_book.legs.get('SHORT', []))
        st['ladder_sell_target'] = 0.0
        st['fstate'] = STATE_SOLD
        self._log('[Buyback PARTIAL] 已买回 {} sh, 剩余 {} sh 继续监控'.format(bought, remaining))
        return bought

    def _handle_bt_dipping(self, price):
        st = self.st
        if price < st.get('bt_dip_price', price): st['bt_dip_price'] = price
        dip = st.get('bt_dip_price', price) or price; bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            block_reason = self._new_leg_block_reason()
            if block_reason:
                self._log('[FWD-T ARM CANCELED] {}'.format(block_reason))
                st['trade_count_long'] = max(
                    0, st.get('trade_count_long', 0) - 1)
                st['fstate'] = STATE_BT_BOUGHT if st.get('long_legs') else STATE_IDLE
                st['bt_dip_price'] = 0.0
                return
            capacity = self._paired_long_capacity(price)
            if not capacity['can_long']:
                self._log('[FWD-T BLOCKED] {}'.format(capacity['long_reason']))
                st['trade_count_long'] = max(
                    0, st.get('trade_count_long', 0) - 1)
                st['fstate'] = STATE_BT_BOUGHT if st.get('long_legs') else STATE_IDLE
                return
            self._log('[FWD-T buy trig] low Y{:.2f} bounce {:.2f}% → Y{:.2f}'.format(dip, bounce * 100, price))
            # ★ v23: 下单前检查现金, 以实际可买数量下单
            status, delta = self._submit_order(self.trade_lot, price, 'FWD-T buy')
            if delta: price = self._execution_price
            if status in ('SKIP', 'TIMEOUT'):
                st['trade_count_long'] = max(0, st.get('trade_count_long', 0) - 1)
                st['fstate'] = STATE_BT_BOUGHT if st.get('long_legs') else STATE_IDLE
                return
            st['fstate'] = STATE_BT_BOUGHT
            st['bt_buy_fill_price'] = price
            st['long_legs'].append((price, delta))
            avg_bp = self._leg_avg_price(st['long_legs'])
            st['bt_sellback_target'] = round(avg_bp * (1.0 + cfg.SELLBACK_RISE_PCT), 2)
            st['ladder_buy_target'] = round(price * (1.0 - LADDER_DOWN_STEP_PCT), 2) if status == 'FILLED' else 0.0
            if status == 'PARTIAL':
                self._log('[FWD-T buy PARTIAL] 实际买入 {} sh'.format(delta))

    def _handle_bt_bought(self, price):
        st = self.st; target = st.get('bt_sellback_target', 999999); bp = st.get('bt_buy_fill_price', 0)
        # ★ v22: 阶梯加买 — 价格跌至更低一档(买价-阶梯幅度) → 追加探底回升买入
        ladder = st.get('ladder_buy_target', 0.0)
        if ladder > 0 and price <= ladder:
            tc = st.get('trade_count_long', 0)
            account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
            avail = account[0].m_dAvailable if account else 0.0
            if (avail >= price * self.trade_lot * 1.01 and
                    tc < cfg.MAX_DAILY_TRADES and
                    not self._new_leg_block_reason()):
                st['trade_count_long'] = tc + 1
                st['ladder_bought_count'] = st.get('ladder_bought_count', 0) + 1
                st['fstate'] = STATE_BT_DIPPING; st['bt_dip_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                self._log('[FWD-T ladder buy #{}/{}] Y{:.2f} <= Y{:.2f}(buy-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, ladder, LADDER_DOWN_STEP_PCT * 100))
                return
        # ★ v23: 止损/卖回以均价为准
        legs = st['long_legs']; avg_bp = self._leg_avg_price(legs) if legs else bp
        if avg_bp > 0 and price <= avg_bp * (1.0 - cfg.STOP_LOSS_PCT):
            self._log('[FWD-T stop-loss trig] avg Y{:.2f} now Y{:.2f}({:.1f}%)'.format(avg_bp, price, (price - avg_bp) / avg_bp * 100))
            self._do_bt_force_sell(); return
        if price >= target:
            st['fstate'] = STATE_BT_SPIKING; st['bt_sell_peak_price'] = price
            self._log('[FWD-T sellback watch] +{:.2f}% → Y{:.2f}'.format((price - avg_bp) / avg_bp * 100, price))

    def _handle_bt_spiking(self, price):
        st = self.st
        if price > st.get('bt_sell_peak_price', price): st['bt_sell_peak_price'] = price
        peak = st.get('bt_sell_peak_price', price); pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            # ★ v23: 多腿毛利 = Σ(卖价 - 各腿买价) × 各腿股数
            legs = st['long_legs'] or [(st.get('bt_buy_fill_price', price), self.trade_lot)]
            total_shares = self._leg_shares(legs)
            gross = sum((price - p) * s for p, s in legs)
            self._log('[FWD-T sell trig] peak Y{:.2f} pullback {:.2f}% → Y{:.2f} gross~Y{:,.0f}'.format(
                peak, pullback * 100, price, gross))
            status, delta = self._submit_order(-total_shares, price, 'FWD-T sell')
            if delta: price = self._execution_price
            if status in ('SKIP', 'TIMEOUT'):
                self._log('[FWD-T sell FAIL] 未成交, 回 BT_BOUGHT')
                st['fstate'] = STATE_BT_BOUGHT
                return
            sold = -delta
            gross = sum((price - p) * n for p, n in legs)
            if sold >= total_shares:
                st['long_legs'] = []; st['ladder_buy_target'] = 0.0; st['ladder_bought_count'] = 0
                st['fstate'] = STATE_DONE
                self._recalculate_next_t_triggers('FWD-T')
                self._maybe_resume_trading()
            else:
                remaining = total_shares - sold
                st['long_legs'] = list(self.execution_book.legs.get('LONG', []))
                st['ladder_buy_target'] = 0.0
                st['fstate'] = STATE_BT_BOUGHT
                self._log('[FWD-T sell PARTIAL] 已卖 {} sh, 剩余 {} sh'.format(sold, remaining))

    def _do_bt_force_sell(self):
        self._log('[FWD-T force sell trig]')
        st = self.st
        shares = self._leg_shares(st['long_legs']) or self.trade_lot
        price = self._cur_price()
        _, delta = self._submit_order(-shares, price, 'FWD-T force sell')
        if delta: price = self._execution_price
        sold = max(0, -delta)
        if sold >= shares:
            st['long_legs'] = []
            st['ladder_buy_target'] = 0.0; st['ladder_bought_count'] = 0
            st['fstate'] = STATE_FORCED
            self._recalculate_next_t_triggers('FWD-T force')
        elif sold > 0:
            st['long_legs'] = list(self.execution_book.legs.get('LONG', []))
            st['fstate'] = STATE_BT_BOUGHT
            self._log('[FWD-T force PARTIAL] 已卖出 {} sh, 剩余 {} sh'.format(
                sold, shares - sold))
        else:
            st['fstate'] = STATE_BT_BOUGHT
            self._log('[WARN] FWD-T force sell 未成交, 保持监控')

    def _refresh_execution_capacity(self, force=False):
        now = _time.monotonic()
        interval = CAPACITY_REFRESH_SEC if cfg.is_market_open(cfg.now_hms()) else OFF_HOURS_REFRESH_SEC
        if not force and now - self._last_capacity_refresh < interval:
            return
        self._last_capacity_refresh = now
        signal = self.st.get('daily_signal') or {}
        if not signal:
            return
        self._refresh_position()
        asset = self.conn.query_account()
        if asset is None:
            self.st['do_short'] = self.st['do_long'] = False
            self._log('[CAPACITY-WAIT] account unavailable')
            return
        price = self._cur_price()
        cash = max(0.0, float(asset.cash) - self.portfolio.reserved_cash(exclude=''))
        reserved = self._leg_shares(self.st.get('long_legs', []))
        free = max(0, self.st.get('base_can_use', 0) - reserved)
        cap = calculate_execution_capacity(free, cash, price, cfg.MAX_DAILY_TRADES, self.trade_lot)
        allowed = signal.get('short_signal_allowed', signal.get('do_short', False))
        short = allowed and cap['can_short'] and self.st.get('trade_count_short', 0) < cfg.MAX_DAILY_TRADES
        long = cap['can_long'] and self.st.get('trade_count_long', 0) < cfg.MAX_DAILY_TRADES
        changed = (short, long) != (self.st.get('do_short'), self.st.get('do_long'))
        self.st.update(do_short=short, do_long=long, avail_cash=asset.cash,
                       long_reason=cap['long_reason'])
        reason = signal.get('short_signal_reason', '') if not allowed else cap['short_reason']
        signal.update(do_short=short, short_reason=reason)
        if changed or force:
            self._log('[CAPACITY] sellable={} reserved={} free={} cash-free=Y{:.2f} '
                      'REV={} FWD={} | {} {}'.format(
                          self.st.get('base_can_use', 0), reserved, free, cash,
                          short, long, reason, cap['long_reason']))

    def _maybe_resume_trading(self):
        self._refresh_execution_capacity(force=True)
        st = self.st
        tc_s = st.get('trade_count_short', 0); tc_l = st.get('trade_count_long', 0)
        do_short = st.get('do_short', False); do_long = st.get('do_long', False)
        can_s = do_short and tc_s < cfg.MAX_DAILY_TRADES
        can_l = do_long and tc_l < cfg.MAX_DAILY_TRADES
        block_reason = self._new_leg_block_reason()
        if (can_s or can_l) and not block_reason:
            self._refresh_position()
            st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0; st['dip_price'] = 0.0
            st['sell_fill_price'] = 0.0; st['buyback_target'] = 0.0
            # ★ v22: 清空阶梯状态
            st['short_legs'] = []; st['long_legs'] = []
            st['ladder_sell_target'] = 0.0; st['ladder_buy_target'] = 0.0
            st['ladder_sold_count'] = 0; st['ladder_bought_count'] = 0
            st['state_enter_time'] = cfg.now_hms()
            parts = []
            if can_s: parts.append('REV-T {}/{}'.format(tc_s, cfg.MAX_DAILY_TRADES))
            if can_l: parts.append('FWD-T {}/{}'.format(tc_l, cfg.MAX_DAILY_TRADES))
            self._log('[RESUME] → IDLE ({})'.format(', '.join(parts)))
        elif block_reason and (can_s or can_l):
            self._log('[RESUME BLOCKED] {}'.format(block_reason))
        else:
            self._log('[DONE] {}/{} trades at limit'.format(tc_s + tc_l, cfg.MAX_DAILY_TRADES * 2))

    def _assess_strength(self, price, now_ts):
        st = self.st; sig = st.get('daily_signal', {}); open_price = sig.get('open_price', 0)
        if open_price <= 0: return
        st['price_history'].append((now_ts, price))
        cutoff = now_ts - cfg.LOCK_LOOKBACK_SEC
        st['price_history'] = [(t, p) for t, p in st['price_history'] if t >= cutoff]
        history = st['price_history']
        if len(history) < 10: return
        prices = [p for _, p in history]; p5 = prices[0]; pn = prices[-1]
        cond1 = pn > open_price * (1.0 + cfg.LOCK_PRICE_RATIO)
        cond2 = (pn - p5) / p5 > cfg.LOCK_MOMENTUM_PCT if p5 > 0 else False
        dh = max(prices); cond3 = (dh - pn) / dh < cfg.LOCK_DRAWDOWN_PCT if dh > 0 else False
        should_lock = cond1 and cond2 and cond3
        if should_lock:
            # A renewed strength signal cancels any pending unlock.  This
            # prevents threshold jitter from turning protection on and off.
            st['lock_cooldown_until'] = 0.0
            if not st.get('locked'):
                st['locked'] = True; st['lock_since'] = cfg.now_hms()
                st['lock_reason'] = 'P+{:.1f}% M {:.2f}% D {:.2f}%'.format(
                    (pn / open_price - 1) * 100, (pn - p5) / p5 * 100,
                    (dh - pn) / dh * 100 if dh > 0 else 0)
                self._log('[LOCK] {}'.format(st['lock_reason']))
        elif st.get('locked'):
            # Start the timer on the first non-strength tick and only release
            # after the condition has remained absent continuously.
            cooldown_until = st.get('lock_cooldown_until', 0.0)
            if cooldown_until <= 0.0:
                st['lock_cooldown_until'] = now_ts + cfg.LOCK_COOLDOWN_SEC
                self._log('[UNLOCK PENDING] {}s'.format(cfg.LOCK_COOLDOWN_SEC))
            elif now_ts >= cooldown_until:
                st['locked'] = False; st['lock_reason'] = ''; st['lock_since'] = ''
                st['lock_cooldown_until'] = 0.0
                self._log('[UNLOCK]')
        else:
            st['lock_cooldown_until'] = 0.0

    # ═══ v25/v26/v33: 短线动量反转机制 (2分钟自适应ATR + REV让权 + 独立回撤) ═══

    def run(self):
        if not getattr(self, '_restored', False):
            self._init_state()
        self._log('[START] {} {}'.format(self.version, 'SIGNAL' if self.dry_run else 'LIVE'))
        try:
            self._daily_init()
            signal = self.st.get('daily_signal')
            if signal: self._print_daily_brief(signal)
        except Exception as e:
            self._lock_all_trading('daily init exception: {}'.format(e))
            self._log('[ERROR] init failed: {}'.format(e)); _traceback.print_exc()

        try:
            while self._running:
                if self.paused_reason:
                    self._monitor_connections(_time.time())
                    self._log('[CYCLE-PAUSED] {}; targets and quantities retained'.format(self.paused_reason))
                    (yield 30)
                    continue
                self._rollover_cycle_day()
                now = cfg.now_hms(); now_ts = _time.time()
                if (self.st.get('trade_date') not in ('', datetime.now().strftime('%Y%m%d'))
                        and self.has_open_legs()):
                    self.portfolio.order_uncertain = True
                    raise RuntimeError('overnight T legs require manual reconciliation; state retained')
                self._monitor_connections(now_ts)
                self._retry_atr_reentry()
                self._refresh_execution_capacity()
                if not cfg.is_market_open(now):
                    self._invalidate_strength('NON_TRADING')
                    today = datetime.now().strftime('%Y%m%d')
                    if '09:30:00' <= now < '09:30:59':
                        if self.st.get('_pre_market_done', '') != today:
                            self._log('[PRE-MKT {}] computing signal...'.format(now))
                            try:
                                self._daily_init(); self.st['_pre_market_done'] = today
                                signal = self.st.get('daily_signal')
                                if signal: self._print_daily_brief(signal)
                            except Exception as e:
                                self._lock_all_trading(
                                    'daily init exception: {}'.format(e))
                                self._log('[PRE-MKT ERROR] {}'.format(e))
                            self._last_heartbeat = now_ts; (yield 5); continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                            self._log('[PRE-MKT {}] to open {}'.format(now, cfg.time_to_open(now)))
                        (yield 5); continue
                    if self.st.get('trade_date', '') != today:
                        try:
                            self._daily_init()
                            signal = self.st.get('daily_signal')
                            if signal: self._print_daily_brief(signal)
                        except Exception as e:
                            self._lock_all_trading(
                                'daily init exception: {}'.format(e))
                            self._log('[ERROR] init failed: {}'.format(e))
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        if now < '09:30:00': self._log('[WAIT {}] to open {}'.format(now, cfg.time_to_open(now)))
                        elif now > '15:00:00': self._log('[CLOSE {}]'.format(now))
                        elif '11:30:00' < now < '13:00:00': self._log('[LUNCH {}]'.format(now))
                    (yield 10); continue

                fstate = self.st.get('fstate', STATE_IDLE)
                if fstate in (STATE_DONE, STATE_FORCED):
                    tick = self.ctx.get_full_tick([self.stock_qmt])
                    tick_data = tick.get(self.stock_qmt, {})
                    price = tick_data.get('lastPrice', 0)
                    self._update_intraday_average(tick_data)
                    if self.paused_reason:
                        (yield 30)
                        continue
                    self._update_limit_up_guard(price, tick_data, now_ts)
                    if now_ts - self._last_heartbeat >= 30:
                        self._last_heartbeat = now_ts
                        tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
                        self._log('[STATE] {} Y{:.2f} REV-T {}/{} FWD-T {}/{} cum {} trades~Y{:,.0f}'.format(
                            fstate, price, tc_s, cfg.MAX_DAILY_TRADES,
                            tc_l, cfg.MAX_DAILY_TRADES, self.total_t_days, self.total_pnl))
                    if now < '14:57:00':
                        tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
                        if ((self.st.get('do_short', False) and tc_s < cfg.MAX_DAILY_TRADES) or
                            (self.st.get('do_long', False) and tc_l < cfg.MAX_DAILY_TRADES)):
                            self._maybe_resume_trading()
                    (yield 3); continue

                tick = self.ctx.get_full_tick([self.stock_qmt])
                if self.stock_qmt not in tick:
                    self._invalidate_strength('MISSING_TICK')
                    (yield 1); continue
                tick_data = tick[self.stock_qmt]
                price = tick_data.get('lastPrice', 0)
                if not math.isfinite(price) or price <= 0:
                    self._invalidate_strength('INVALID_PRICE')
                    (yield 1); continue
                self._update_intraday_average(tick_data)
                if self.paused_reason:
                    (yield 30)
                    continue
                self._update_limit_up_guard(price, tick_data, now_ts)
                self._update_fwd_buy_trigger(price)
                # ★ v21: 开盘首个有效tick打印行情确认
                if not self.st.get('_market_open_logged', True):
                    self.st['_market_open_logged'] = True
                    sig_chk = self.st.get('daily_signal', {})
                    # 用今日开盘价重算 sell_trigger (盘前计算可能用了昨日开盘价)
                    _open_now = self.ctx.get_full_tick([self.stock_qmt]).get(self.stock_qmt, {}).get('open', 0)
                    _open_old = sig_chk.get('open_price', 0)
                    if (sig_chk.get('trigger_base') != 'CLOSE_FILL_ATR' and _open_now > 0 and
                            (_open_old <= 0 or abs(_open_now - _open_old) >= 0.005)):
                        _sm = sig_chk.get('sell_mult', 0.40)
                        _atr = sig_chk.get('atr_pct', 0.03)
                        _units = sig_chk.get('trigger_units')
                        _effective_units = (
                            float(_units) if _units is not None
                            else _sm * cfg.SELL_TRIGGER_SCALE)
                        _new_raw = _open_now * (1.0 + _atr * _effective_units)
                        _range_capped = False
                        if _units is None:
                            _range_ma = sig_chk.get('daily_range_ma10', 0.0)
                            _range_cap = _open_now * (
                                1.0 + _range_ma * cfg.DAILY_RANGE_CAP_MULT)
                            _range_capped = (cfg.DAILY_RANGE_CAP_ENABLED and
                                             _new_raw > _range_cap)
                            _new_trig = round(
                                _range_cap if _range_capped else _new_raw, 2)
                        else:
                            _new_trig = round(_new_raw, 2)
                        _old_trig = sig_chk.get('sell_trigger', 0)
                        sig_chk['sell_trigger'] = _new_trig
                        sig_chk['sell_trigger_raw'] = round(_new_raw, 2)
                        sig_chk['range_capped'] = _range_capped
                        sig_chk['open_price'] = _open_now
                        _buy_floor = round(
                            _open_now * (1.0 - cfg.BUY_TRIGGER_PCT), 2)
                        sig_chk['buy_trigger_floor'] = _buy_floor
                        self._update_fwd_buy_trigger(price)
                        sig_chk['sellback_target_hint'] = round(
                            sig_chk['buy_trigger'] *
                            (1.0 + cfg.SELLBACK_RISE_PCT), 2)
                        self._log('[SELL-TRIG RECALC] open Y{:.2f}→Y{:.2f} trig Y{:.2f}→Y{:.2f} (units {:.3f} ATR {:.1f}%)'.format(
                            _open_old, _open_now, _old_trig, _new_trig,
                            _effective_units, _atr * 100))
                    st_trig = sig_chk.get('sell_trigger', 0)
                    bt_trig = sig_chk.get('buy_trigger', 0)
                    bits = ['OPEN', 'Y{:.2f}'.format(price)]
                    if st_trig > 0:
                        if price >= st_trig:
                            bits.append('REV-T exceeded by Y{:.2f}'.format(
                                price - st_trig))
                        else:
                            bits.append('REV-T needs +{:.2f}% to Y{:.2f}'.format(
                                (st_trig - price) / price * 100, st_trig))
                    if bt_trig > 0:
                        if price <= bt_trig:
                            bits.append('FWD-T threshold reached')
                        else:
                            bits.append('FWD-T needs -{:.2f}% to Y{:.2f}'.format(
                                (price - bt_trig) / price * 100, bt_trig))
                    if self.st.get('locked'): bits.append('LOCKED')
                    if self.st.get('limit_up_guard'): bits.append('LIMIT-UP-GUARD')
                    self._log('[{}]'.format('] ['.join(bits)))
                if INTRADAY_REFERENCE_MODE == 'shadow':
                    self._update_rebound_reference(price, now_ts, tick_data)
                self._update_strength_reference(price, now_ts, tick_data)
                fstate = self.st.get('fstate', STATE_IDLE)
                signal = self.st.get('daily_signal')
                do_short = self.st.get('do_short', False); do_long = self.st.get('do_long', False)
                if fstate == STATE_IDLE and (not signal or (not do_short and not do_long)):
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        self._log('[STANDBY] Y{:.2f} no trade direction'.format(price))
                    (yield 5); continue
                if fstate == STATE_IDLE: self._assess_strength(price, now_ts)
                if fstate == STATE_IDLE: self._handle_idle(price)
                elif fstate == STATE_SPIKING: self._handle_spiking(price)
                elif fstate == STATE_SOLD: self._handle_sold(price)
                elif fstate == STATE_DIPPING: self._handle_dipping(price)
                elif fstate == STATE_BT_DIPPING: self._handle_bt_dipping(price)
                elif fstate == STATE_BT_BOUGHT: self._handle_bt_bought(price)
                elif fstate == STATE_BT_SPIKING: self._handle_bt_spiking(price)
                if self.st['fstate'] in (STATE_SOLD, STATE_DIPPING): self.st['sell_elapsed_bars'] += 1
                if cfg.ENABLE_FORCE_CLOSE and now >= cfg.FORCE_CLOSE_TIME:
                    f = self.st['fstate']
                    if f in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
                        self._do_bt_force_sell()
                    elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE):
                        self.st['fstate'] = STATE_DONE
                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts; self._heartbeat(price)
                (yield 0.5)
        except KeyboardInterrupt: raise
        except Exception as e: self._log('[ERROR] {}'.format(e)); _traceback.print_exc()
        finally:
            if self.st.get('fstate', '') in (STATE_SOLD, STATE_DIPPING): self._log('[WARN] position not bought back!')
            self._log('[STOP] {} v56 cum {} days gross~Y{:,.0f}'.format(
                self.stock_name, self.total_t_days, self.total_pnl))

    def _heartbeat(self, price):
        fs = self.st['fstate']; sig = self.st.get('daily_signal', {})
        if fs in (STATE_DONE, STATE_FORCED):
            tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
            self._log('[HB] {} Y{:.2f} REV-T {}/{} FWD-T {}/{} cum {} trades~Y{:,.0f}'.format(
                fs, price, tc_s, cfg.MAX_DAILY_TRADES, tc_l, cfg.MAX_DAILY_TRADES,
                self.total_t_days, self.total_pnl)); return
        if fs == STATE_IDLE:
            guard_active = self.st.get('limit_up_guard', False)
            parts = []
            if self.st.get('do_short'):
                st_trig = self._rev_sell_trigger()
                if price >= st_trig:
                    parts.append('REV-T: exceeded Y{:.2f} by Y{:.2f}'.format(
                        st_trig, price - st_trig))
                else:
                    parts.append('REV-T: needs +Y{:.2f} to Y{:.2f}'.format(
                        st_trig - price, st_trig))
            else:
                parts.append('REV-T: off ({})'.format(
                    sig.get('short_reason', 'not executable')))
            if self.st.get('do_long'):
                bt_dyn = sig.get('buy_trigger', 0)
                trail_detail = ('floor Y{:.2f} current-trail Y{:.2f} '
                                'max-trail Y{:.2f} effective Y{:.2f}').format(
                                    sig.get('buy_trigger_floor', 0),
                                    sig.get('buy_trigger_trail', 0),
                                    sig.get('buy_trigger_max_trail', 0),
                                    bt_dyn)
                if price <= bt_dyn:
                    parts.append('FWD-T: threshold reached Y{:.2f} ({})'.format(
                        bt_dyn, trail_detail))
                else:
                    parts.append('FWD-T: needs -Y{:.2f} to Y{:.2f} ({})'.format(
                        price - bt_dyn, bt_dyn, trail_detail))
            else:
                parts.append('FWD-T: off ({})'.format(
                    self.st.get('long_reason', 'not executable')))
            if self.st.get('locked'): parts.append('LOCKED')
            if guard_active: parts.append('LIMIT-UP-GUARD')
            parts.append(self._reference_log())
            self._log('[HB] {} Y{:.2f} {}'.format(fs, price, ' | '.join(parts)))
        elif fs == STATE_SPIKING:
            peak = self.st.get('peak_price', 0); pb = (peak - price) / peak * 100 if peak > 0 else 0
            self._log('[HB] {} Y{:.2f} peak Y{:.2f} pullback {:.2f}% | {}'.format(fs, price, peak, pb, self._reference_log()))
        elif fs in (STATE_SOLD, STATE_DIPPING):
            sp = self.st.get('sell_fill_price', 0); bt = self.st.get('buyback_target', 0)
            ladder = self.st.get('ladder_sell_target', 0)
            extra = ' ladder Y{:.2f}'.format(ladder) if ladder > 0 else ''
            if sp > 0: self._log('[HB] {} Y{:.2f} sell Y{:.2f} {:+.1f}% buyback Y{:.2f}{}'.format(
                fs, price, sp, (price - sp) / sp * 100, bt, extra))
        elif fs == STATE_BT_DIPPING:
            dip = self.st.get('bt_dip_price', price); bounce = (price - dip) / dip * 100 if dip > 0 else 0
            self._log('[HB] {} Y{:.2f} dip Y{:.2f} bounce {:.2f}%'.format(fs, price, dip, bounce))
        elif fs == STATE_BT_BOUGHT:
            bp = self.st.get('bt_buy_fill_price', 0); target = self.st.get('bt_sellback_target', 0)
            ladder = self.st.get('ladder_buy_target', 0)
            extra = ' ladder Y{:.2f}'.format(ladder) if ladder > 0 else ''
            if bp > 0: self._log('[HB] {} Y{:.2f} buy Y{:.2f} {:+.1f}% sellback Y{:.2f}{}'.format(
                fs, price, bp, (price - bp) / bp * 100, target, extra))
        elif fs == STATE_BT_SPIKING:
            peak = self.st.get('bt_sell_peak_price', price); pb = (peak - price) / peak * 100 if peak > 0 else 0
            self._log('[HB] {} Y{:.2f} peak Y{:.2f} pullback {:.2f}%'.format(fs, price, peak, pb))
        else: self._log('[HB] {} Y{:.2f}'.format(fs, price))


class SymbolConnector:
    """Share account/transport, but isolate each symbol's daily cache."""
    def __init__(self, shared, stock_qmt):
        self.shared = shared
        self.stock_qmt = stock_qmt
        self.refresh_daily_cache()

    def __getattr__(self, name):
        return getattr(self.shared, name)

    def refresh_daily_cache(self):
        self._daily_data_cache = None
        self._daily_raw_cache = None
        self._daily_snapshot_meta = None

    def load_daily_snapshot(self, *args, **kwargs):
        kwargs['stock_code'] = self.stock_qmt
        return MiniQMTConnector.load_daily_snapshot(self, *args, **kwargs)


class ExecutionPortfolio:
    """One cooperative scheduler, one account, independent symbol states."""
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.conn = MiniQMTConnector()
        self.runners = {}
        self.tasks = {}
        self.last_refresh = None
        self.order_uncertain = False
        self.own_order_ids = set()
        self.watchdog = RuntimeWatchdog(_log, WATCHDOG_WARN_SEC)
        self.rpc_originals = []
        self.seen_trades = None
        self.checkpoint_active = False
        self.inflight = None
        self._last_checkpoint = 0.0
        self._last_checkpoint_log = None


    def _broker_snapshot(self):
        # Account-wide orders also detect manual/other-strategy changes during downtime.
        positions = self.conn.trader.query_stock_positions(self.conn._account_obj)
        orders = self.conn.trader.query_stock_orders(self.conn._account_obj)
        if positions is None or orders is None:
            raise RuntimeError('reconciliation query unavailable')
        if any(int(order.order_status) not in (53, 54, 56, 57) for order in orders):
            raise RuntimeError('account has unfinished orders; reconcile before starting')
        return dict(
            positions=sorted([[str(p.stock_code), int(p.volume), int(p.can_use_volume)]
                              for p in positions if int(p.volume) > 0]),
            orders=sorted([[str(o.order_id), str(o.stock_code), int(o.order_status),
                            int(o.traded_volume or 0), float(o.traded_price or 0)]
                           for o in orders]))

    def restore_checkpoint(self):
        if self.dry_run:
            return
        saved = read_checkpoint(STATE_FILE, ACCOUNT)
        if saved and (saved['inflight'] or saved['order_uncertain']):
            raise RuntimeError('STATE-BLOCKED: interrupted/uncertain order; inspect broker and checkpoint')
        today = datetime.now().strftime('%Y%m%d')
        # Validate every record before restoring any symbol. A previous v52
        # checkpoint may contain today's envelope but yesterday's flat runner.
        stale_codes = set()
        if saved:
            for code, record in saved['runners'].items():
                state = record['state']
                date = state.get('trade_date', '')
                detail = '{} record-date={} expected={} initialized={}'.format(
                    code, date, today, state.get('initialized'))
                if not date or date > today or not state.get('initialized'):
                    raise RuntimeError('STATE-BLOCKED: ' + detail + '; incomplete runner initialization')
                if date < today or saved['date'] < today:
                    probe = StrategyRunner(self, code)
                    probe.st.update(state)
                    probe.execution_book.legs = record['legs']
                    if probe.has_open_legs():
                        raise RuntimeError('STATE-BLOCKED: ' + detail + '; overnight T legs require reconciliation')
                    stale_codes.add(code)
            if saved['date'] > today:
                raise RuntimeError('STATE-BLOCKED: checkpoint date is in the future')
        if saved and saved['date'] != today:
            # All records were checked for outstanding legs above.
            saved = None
        broker = self._broker_snapshot()
        if saved:
            if saved['broker'] != broker:
                raise RuntimeError('STATE-BLOCKED: broker positions/orders changed since checkpoint')
            self.own_order_ids = saved['own_order_ids']
            for code, record in saved['runners'].items():
                runner = StrategyRunner(self, code, record['name'])
                if code in stale_codes:
                    runner.total_pnl = record['total_pnl']
                    runner.total_t_days = record['total_t_days']
                    runner._init_state()
                    runner._log('[STATE-ROLLOVER] flat record-date={} expected={}; daily initialization required'.format(
                        record['state']['trade_date'], today))
                else:
                    runner.restore_record(record)
                self.runners[code] = runner
                self.tasks[code] = (runner.run(), 0.0)
        else:
            _log('[STATE-NEW] no same-day compatible v49/v52 checkpoint; unrecorded T legs/counts are NOT reconstructed')
        self.checkpoint_active = True

    def save_checkpoint(self, force=False, settled=False):
        if not self.checkpoint_active or self.dry_run:
            return
        now = _time.monotonic()
        interval = CHECKPOINT_INTERVAL_SEC if cfg.is_market_open(cfg.now_hms()) else OFF_HOURS_REFRESH_SEC
        if not force and not self.inflight and now - self._last_checkpoint < interval:
            return
        # Do not label a partially rolled portfolio as a current-day checkpoint.
        today = datetime.now().strftime('%Y%m%d')
        for code, runner in self.runners.items():
            if not runner.st.get('initialized') or runner.st.get('trade_date') != today:
                raise RuntimeError('STATE-BLOCKED: {} record-date={} expected={} initialized={}; '
                                   'cannot checkpoint incomplete rollover'.format(
                                       code, runner.st.get('trade_date'), today, runner.st.get('initialized')))
        pending = None if settled and not self.order_uncertain else self.inflight
        data = dict(schema=1, account=str(ACCOUNT),
                    date=today, inflight=pending,
                    order_uncertain=self.order_uncertain, own_order_ids=self.own_order_ids,
                    runners={code: r.checkpoint_record() for code, r in self.runners.items()},
                    broker=self._broker_snapshot())
        write_checkpoint(STATE_FILE, data, log=_log)
        self.inflight = pending
        self._last_checkpoint = now
        if (self._last_checkpoint_log is None or
                now - self._last_checkpoint_log >= STATE_SAVE_LOG_INTERVAL_SEC):
            _log('[STATE-SAVED] symbols={} inflight={} path={}'.format(
                len({r.stock_qmt for r in self.runners.values()}), bool(pending), STATE_FILE))
            self._last_checkpoint_log = now

    def before_submit(self, code, label, shares, price):
        if not self.checkpoint_active:
            return
        self.inflight = dict(symbol=code, label=label, shares=shares, price=price)
        # If this fails, submission never happens. The marker remains until the
        # complete caller state transition reaches the scheduler's next yield.
        self.save_checkpoint(force=True)

    def _prepare_trading_day(self):
        """Finish the whole portfolio's rollover before a tick can save or submit."""
        today = datetime.now().strftime('%Y%m%d')
        pending = [r for r in self.runners.values()
                   if not r.st.get('initialized') or r.st.get('trade_date') != today]
        # Inspect all old runners before resetting any of them.
        for runner in pending:
            if runner.has_open_legs():
                raise RuntimeError('STATE-BLOCKED: {} record-date={} expected={}; '
                                   'overnight/uninitialized T legs require reconciliation'.format(
                                       runner.stock_qmt, runner.st.get('trade_date'), today))
        for runner in pending:
            runner._init_state()
            runner.execution_book = ExecutionBook()
            runner._daily_init()
            if not runner.st.get('initialized') or runner.st.get('trade_date') != today:
                raise RuntimeError('STATE-BLOCKED: {} daily initialization incomplete; expected={}'.format(
                    runner.stock_qmt, today))
            runner._restored = True

    def _audit_account_trades(self):
        try:
            trades = self.conn.trader.query_stock_trades(self.conn._account_obj)
            if trades is None:
                raise ValueError('trade query returned None')
            baseline = self.seen_trades is None
            if baseline:
                self.seen_trades = set()
            for trade in trades:
                oid = str(getattr(trade, 'order_id', ''))
                sysid = str(getattr(trade, 'order_sysid', ''))
                key = (oid, str(getattr(trade, 'traded_id', '')),
                       str(getattr(trade, 'traded_time', '')),
                       str(getattr(trade, 'stock_code', '')),
                       str(getattr(trade, 'traded_volume', '')),
                       str(getattr(trade, 'traded_price', '')))
                if key in self.seen_trades:
                    continue
                self.seen_trades.add(key)
                if not baseline and oid not in self.own_order_ids and sysid not in self.own_order_ids:
                    _log('[ACCOUNT-UNATTRIBUTED] order={} symbol={} qty={} avg={} '
                         'strategy={}; not booked as this strategy T'.format(
                             oid, key[3], key[4], key[5], getattr(trade, 'strategy_name', '')))
        except Exception as error:
            _log('[ACCOUNT-AUDIT-WAIT] {}'.format(error))

    def reserved_cash(self, exclude):
        reserve = 0.0
        for code, runner in self.runners.items():
            if code == exclude:
                continue
            st = runner.st
            reserve += sum(price * shares for price, shares in st.get('short_legs', []))
        return reserve * 1.01

    def refresh_holdings(self, now):
        interval = PORTFOLIO_REFRESH_SEC if cfg.is_market_open(cfg.now_hms()) else OFF_HOURS_REFRESH_SEC
        if self.last_refresh is not None and now - self.last_refresh < interval:
            return
        self.last_refresh = now
        self._audit_account_trades()
        # Read-only account access is also required in signal mode to discover holdings.
        try:
            positions = self.conn.trader.query_stock_positions(self.conn._account_obj)
            if positions is None:
                raise RuntimeError('position query returned None')
        except Exception as error:
            _log('[PORTFOLIO-ALERT] holdings query failed: {}'.format(error))
            return
        for pos in positions:
            code = str(getattr(pos, 'stock_code', ''))
            if getattr(pos, 'volume', 0) <= 0 or code in self.runners:
                continue
            if '.' not in code:
                _log('[PORTFOLIO-SKIP] missing exchange suffix: {}'.format(code))
                continue
            runner = StrategyRunner(self, code, getattr(pos, 'stock_name', ''))
            self.runners[code] = runner
            self.tasks[code] = (runner.run(), 0.0)
            _log('[PORTFOLIO-ADD] {} shares={} lot={}'.format(
                code, pos.volume, runner.trade_lot))
        # Keep runners with zero current holdings: their sold legs may still need buyback.

    def run(self):
        set_global_conn(self.conn, self.dry_run)
        self.watchdog.start()
        try:
            if not self.conn.connect_data():
                _log('[PORTFOLIO-ERROR] market connection failed')
                return
            if not self.conn.connect_trade():
                _log('[PORTFOLIO-ERROR] account connection failed')
                return
            if str(getattr(self.conn._account_obj, 'account_id', '')) != str(ACCOUNT):
                _log('[PORTFOLIO-ERROR] connected account differs from configured account')
                return
            for endpoint in (self.conn.xtdata, self.conn.trader):
                client = getattr(endpoint, 'client', None)
                if client is not None and type(client).__module__.endswith('xtquant_compat') and all(client is not item[0] for item in self.rpc_originals):
                    original = instrument_rpc(client, self.watchdog, READ_RPC_TIMEOUT_SEC)
                    self.rpc_originals.append((client, original))
            _log('[RUNTIME] watchdog={}s read-RPC-timeout={}s instrumented-clients={}; '
                 'native/router calls may have separate timeouts'.format(
                     WATCHDOG_WARN_SEC, READ_RPC_TIMEOUT_SEC, len(self.rpc_originals)))
            _log('[PORTFOLIO-START] all account holdings; mode={}'.format(
                'SIGNAL' if self.dry_run else 'LIVE'))
            self.restore_checkpoint()
            while True:
                now = _time.monotonic()
                with self.watchdog.scope('REFRESH holdings'):
                    self.refresh_holdings(now)
                # Initialize the complete portfolio before any symbol can submit:
                # all saved cash reservations must be present in the checkpoint.
                self._prepare_trading_day()
                for code, (task, due) in list(self.tasks.items()):
                    if now < due:
                        continue
                    # The inherited order wrappers use this connection. Only one
                    # task executes at a time; order confirmation stays synchronous.
                    set_global_conn(self.conn, self.dry_run)
                    try:
                        with self.watchdog.scope('TICK ' + code):
                            delay = next(task)
                        self.save_checkpoint(settled=True)
                        self.tasks[code] = (task, _time.monotonic() + float(delay or 0))
                    except StopIteration:
                        self.order_uncertain = True
                        raise RuntimeError('STATE-BLOCKED: worker stopped; preserve checkpoint and reconcile')
                _time.sleep(0.2)
        except KeyboardInterrupt:
            _log('[PORTFOLIO-STOP] interrupted')
            if self.checkpoint_active and not self.inflight:
                self.save_checkpoint(force=True)
        except Exception as error:
            self.order_uncertain = True
            _log('[STATE-BLOCKED] {}; automatic trading stopped; checkpoint retained'.format(error))
        finally:
            for task, _ in self.tasks.values():
                task.close()
            self.conn.disconnect()
            for client, original in self.rpc_originals:
                client.call = original
            self.watchdog.stop()



class StrategyRunner(ExecutionRunner):
    """Research lane: one independently owned cycle per lane."""
    def __init__(self, portfolio, stock_qmt, stock_name='', lane=0):
        super().__init__(portfolio, stock_qmt, stock_name)
        self.lane = lane
        self.baseline_shares = None
        self.cycle = None
        self.cycle_history = []
        self.admission = DirectionalAdmission(DIRECTIONAL_THRESHOLD)
        self._minute_pending = None
        self.paused_reason = ''
        self._age_checked_day = None
        self.imported_entry_counts = {}

    def checkpoint_record(self):
        if self.cycle:
            self.cycle.targets = {key: self.st.get(key) for key in (
                'buyback_target', 'bt_sellback_target',
                'ladder_sell_target', 'ladder_buy_target')}
        record = super().checkpoint_record()
        record['v52'] = dict(lane=self.lane, baseline=self.baseline_shares,
                            cycle=self.cycle.record() if self.cycle else None,
                            history=self.cycle_history, paused=self.paused_reason,
                            imported_counts=self.imported_entry_counts)
        return record

    def _log(self, message):
        super()._log('[LANE {}] {}'.format(getattr(self, 'lane', 0), message))

    def restore_record(self, record):
        extra = record.get('v52')
        if not extra:
            raise RuntimeError('v52 cycle ownership missing; no implicit migration')
        super().restore_record(record)
        self.baseline_shares = extra['baseline']
        self.lane = extra['lane']
        self.cycle = Cycle(**extra['cycle']) if extra['cycle'] else None
        self.cycle_history = extra['history']
        self.paused_reason = extra.get('paused', '')
        self.imported_entry_counts = extra.get('imported_counts', {})
        ledger_quantity = sum(q for legs in self.execution_book.legs.values() for _, q in legs)
        state_quantity = (sum(q for _, q in self.st.get('short_legs', [])) +
                          sum(q for _, q in self.st.get('long_legs', [])))
        if ledger_quantity != (self.cycle.quantity if self.cycle else 0) or state_quantity != ledger_quantity:
            raise RuntimeError('restored state/execution ledger/cycle quantities disagree')
        if self.has_open_legs() and (not self.cycle or not self.st.get('initialized')):
            raise RuntimeError('restored open execution state has no initialized cycle owner')
        self.admission.reset()
        self._minute_pending = None

    def _daily_init(self):
        if self.cycle and self.cycle.quantity and self.st.get('initialized') and OVERNIGHT_ENABLED:
            self._rollover_cycle_day()
            self._refresh_position()
            return
        super()._daily_init()
        if not self.st.get('initialized'):
            return
        history = self.st.get('reentry_history')
        closes = [] if history is None else history['close'].astype(float).tolist()
        decision = short_trend_guard(closes)
        signal = self.st.get('daily_signal') or {}
        signal['short_trend_guard'] = decision
        if decision['allowed']:
            self._log('[TREND-GUARD PASS] {}'.format(decision['reason']))
            return
        reason = 'REV-T blocked by {}'.format(decision['reason'])
        signal['short_signal_allowed'] = False
        signal['short_signal_reason'] = reason
        signal['do_short'] = False
        signal['short_reason'] = reason
        self.st['do_short'] = False
        self._log('[TREND-GUARD BLOCK] {}'.format(decision['reason']))

    def _rollover_cycle_day(self):
        today = datetime.now().strftime('%Y%m%d')
        previous = self.st.get('trade_date')
        if not previous or previous == today:
            return
        self.admission.reset()
        self._minute_pending = None
        if self.has_open_legs():
            if not OVERNIGHT_ENABLED:
                return  # inherited overnight stop remains effective
            if not self.cycle or self.cycle.quantity <= 0:
                raise RuntimeError('unattributed overnight execution legs')
            # Targets, signal ATR, execution book and monetary amounts remain untouched.
            self.st['trade_date'] = today
            for key in ('trade_count_short', 'trade_count_long'):
                self.st[key] = 0
            self.st['price_history'] = []
            self._reset_strength_reference()
            self.conn.refresh_daily_cache()
            self._refresh_position()
        else:
            self._daily_init()

    def _update_intraday_average(self, tick_data):
        # QMT Python API p.171: 16=volatility interruption, 17=temporary suspension,
        # 20=suspended through close. Unknown status 0/10 is not a suspension claim.
        if tick_data.get('stockStatus') in (16, 17, 20):
            self.paused_reason = 'verified exchange interruption/suspension: {}'.format(tick_data['stockStatus'])
            self.admission.reset()
            self._minute_pending = None
            self._log('[CYCLE-PAUSED] ' + self.paused_reason)
            return
        super()._update_intraday_average(tick_data)
        now = _time.time()
        minute = int(now // 60) * 60
        signal = self.st.get('daily_signal') or {}
        atr = (signal.get('reentry') or {}).get('atr_pct', signal.get('atr_pct', 0))
        current = (minute, float(tick_data.get('lastPrice', 0) or 0),
                   self.st.get('intraday_avg_price', 0) if self.st.get('intraday_avg_valid') else 0,
                   float(atr or 0))
        if not cfg.is_market_open(cfg.now_hms()) or not all(math.isfinite(v) and v > 0 for v in current):
            self.admission.reset()
            self._minute_pending = None
            return
        if self._minute_pending and minute != self._minute_pending[0]:
            if minute - self._minute_pending[0] == 60:
                self.admission.on_minute(*self._minute_pending)
            else:
                self.admission.reset()
        self._minute_pending = current
        today = datetime.now().strftime('%Y%m%d')
        if self.cycle and self._age_checked_day != today:
            # Only complete historical sessions plus a currently observed session count.
            snapshot = self.conn.load_daily_snapshot(80)
            raw = snapshot['raw']
            last_close = float(tick_data.get('lastClose', 0) or 0)
            if not len(raw) or last_close <= 0 or abs(float(raw.iloc[-1]['close'])-last_close) > .02:
                self.paused_reason = 'raw history / lastClose mismatch; corporate action requires reconciliation'
                self._log('[CYCLE-PAUSED] ' + self.paused_reason)
                return
            dates = [str(index)[:8] for index in raw.index if str(index)[:8] < today]
            stamp = tick_data.get('time', 0)
            try:
                stamp = float(stamp)
                if stamp > 1e11: stamp /= 1000
                current_session = datetime.fromtimestamp(stamp).strftime('%Y%m%d') == today
            except (TypeError, ValueError, OverflowError, OSError):
                current_session = False
            if current_session and float(tick_data.get('volume', 0) or 0) > 0:
                dates.append(today)
                age = self.cycle.advance_verified(dates)
                self._age_checked_day = today
                if age >= CYCLE_AGE_ALERT_DAYS:
                    self._log('[CYCLE-AGE] id={} trading-days={} action=ALERT_ONLY; exit target unchanged'.format(
                        self.cycle.cycle_id, age))

    def _new_leg_block_reason(self):
        if self.cycle and self.cycle.quantity and getattr(self, '_ladder_context', False):
            active = [r.cycle for r in self.portfolio.symbol_runners(self.stock_qmt) if r.cycle]
            free = exposure_limit(self.baseline_shares, self.trade_lot, CYCLE_EXPOSURE_FRACTION) - sum(c.quantity for c in active)
            return self.paused_reason or ('cycle exposure cap' if free < self.trade_lot else super()._new_leg_block_reason())
        return self.paused_reason or ('lane already owns an unfinished cycle'
                                     if self.cycle and self.cycle.quantity else super()._new_leg_block_reason())

    def _invalidate_strength(self, reason):
        super()._invalidate_strength(reason)
        self.admission.reset()
        self._minute_pending = None

    def _ladder_call(self, method, price, label):
        self._ladder_context = bool(self.cycle and self.cycle.label == label)
        try:
            return method(price)
        finally:
            self._ladder_context = False

    def _handle_sold(self, price):
        if self._try_short_cycle_risk_exit(price):
            return None
        return self._ladder_call(super()._handle_sold, price, 'REV-T sell')

    def _handle_dipping(self, price):
        if self._try_short_cycle_risk_exit(price):
            return None
        return super()._handle_dipping(price)

    def _short_cycle_risk_reason(self, price):
        cycle = self.cycle
        if not cycle or cycle.direction != 'SHORT' or cycle.quantity <= 0:
            return ''
        average = cycle.average
        if average <= 0 or price <= 0:
            return ''
        if len(cycle.trading_days) >= CYCLE_MAX_HOLDING_DAYS:
            return 'MAX_HOLDING_DAYS'
        return ''

    def _try_short_cycle_risk_exit(self, price):
        reason = self._short_cycle_risk_reason(price)
        if not reason:
            return False
        cycle = self.cycle
        self._log('[CYCLE-RISK-EXIT] id={} reason={} days={} adverse={:.2f}%'.format(
            cycle.cycle_id,
            reason,
            len(cycle.trading_days),
            (price / cycle.average - 1.0) * 100))
        self._risk_buyback(price, reason)
        return True

    def _risk_buyback(self, price, reason):
        st = self.st
        legs = st.get('short_legs', [])
        shares = self._leg_shares(legs)
        if shares <= 0:
            raise RuntimeError('cycle risk exit has no attributable short quantity')
        label = 'REV-T risk buyback({})'.format(reason)
        status, delta = self._submit_order(
            shares,
            price,
            label,
            'COMPETE')
        bought = max(0, delta)
        if bought >= shares:
            st['short_legs'] = []
            st['ladder_sell_target'] = 0.0
            st['ladder_sold_count'] = 0
            st['fstate'] = STATE_FORCED
            self._recalculate_next_t_triggers(label)
            self._maybe_resume_trading()
            return bought
        if bought > 0:
            st['short_legs'] = list(self.execution_book.legs.get('SHORT', []))
            st['ladder_sell_target'] = 0.0
            st['fstate'] = STATE_SOLD
            self._log('[CYCLE-RISK-EXIT PARTIAL] bought={} remaining={}'.format(
                bought,
                shares - bought))
            return bought
        st['fstate'] = STATE_SOLD
        self._log('[CYCLE-RISK-EXIT RETRY] reason={} status={}'.format(
            reason,
            status))
        return 0

    def _handle_spiking(self, price):
        return self._ladder_call(super()._handle_spiking, price, 'REV-T sell')

    def _handle_bt_bought(self, price):
        return self._ladder_call(super()._handle_bt_bought, price, 'FWD-T buy')

    def _handle_bt_dipping(self, price):
        return self._ladder_call(super()._handle_bt_dipping, price, 'FWD-T buy')

    def _new_t_shares(self, price, side):
        original = super()._new_t_shares(price, side)
        if self.baseline_shares is None:
            return 0
        siblings = self.portfolio.symbol_runners(self.stock_qmt)
        active = [r.cycle for r in siblings if r.cycle and r.cycle.quantity]
        remaining = exposure_limit(self.baseline_shares, self.trade_lot, CYCLE_EXPOSURE_FRACTION)
        remaining -= sum(c.quantity for c in active)
        if not getattr(self, '_ladder_context', False) and (len(active) >= MAX_OPEN_CYCLES or self.cycle and self.cycle.quantity):
            return 0
        return min(original, max(0, remaining) // self.trade_lot * self.trade_lot)

    def _submit_order(self, shares, price, label, style='COMPETE'):
        opening = label in ('REV-T sell', 'FWD-T buy')
        ladder = bool(opening and self.cycle and getattr(self, '_ladder_context', False) and self.cycle.label == label)
        if self.paused_reason:
            self._log('[CYCLE-PAUSED] ' + self.paused_reason)
            return 'SKIP', 0
        if opening:
            if label == 'REV-T sell':
                closes = self.st.get('ma_completed_closes', [])
                guard = short_five_day_momentum_guard(closes)
                if not guard['allowed']:
                    return_value = guard['return']
                    display = 'unavailable' if return_value is None else '{:.2f}%'.format(
                        return_value * 100)
                    self._log('[MOMENTUM-GUARD] five-day return {} exceeds/has no safe history; REV-T blocked'.format(
                        display))
                    return 'SKIP', 0
            if LONG_RESEARCH_DISABLED and label == 'FWD-T buy':
                return 'SKIP', 0
            if self._new_leg_block_reason():
                return 'SKIP', 0
            today = datetime.now().strftime('%Y%m%d')
            records = []
            for sibling in self.portfolio.symbol_runners(self.stock_qmt):
                records.extend(sibling.cycle_history)
                if sibling.cycle:
                    records.append(sibling.cycle.record())
            count = sum(record['opened_day'] == today and
                        record.get('label') == label for record in records)
            count += sum(sibling.imported_entry_counts.get(today, {}).get(
                label, 0)
                for sibling in self.portfolio.symbol_runners(self.stock_qmt))
            if count >= cfg.MAX_DAILY_TRADES:
                self._log('[CYCLE-BLOCKED] shared symbol daily entry limit reached')
                return 'SKIP', 0
            if not ladder and label == 'FWD-T buy' and DIRECTIONAL_ENABLED and not self.admission.allowed:
                self._log('[DIRECTION-BLOCKED] score={} threshold={} ready={}'.format(
                    self.admission.score, DIRECTIONAL_THRESHOLD, self.admission.allowed))
                return 'SKIP', 0
            snapshot = self._snapshot_account()
            if snapshot['cash'] < self.portfolio.reserved_cash(exclude=''):
                self._log('[CYCLE-BLOCKED] buyback reserve deficit; existing exits remain enabled')
                return 'SKIP', 0
        elif not self.cycle or abs(shares) > self.cycle.quantity:
            raise RuntimeError('exit has no unique cycle owner or exceeds remaining quantity')
        status, delta = super()._submit_order(shares, price, label, style)
        if delta:
            oid = self._submitted_order_id
            if opening and not ladder:
                day = datetime.now().strftime('%Y%m%d')
                atr = self.st['daily_signal']['atr_pct']
                self.cycle = Cycle('{}:{}:{}'.format(self.stock_qmt, self.lane, oid), self.lane,
                                   'LONG' if delta > 0 else 'SHORT', day, datetime.now().isoformat(), atr)
                self.cycle.label = label
                self.cycle.advance(day)
            order = self.conn.trader.query_stock_order(self.conn._account_obj, oid)
            fee = getattr(order, 'fee', None)  # unknown live fee is never presented as zero
            self.cycle.fill(oid, abs(delta), self._execution_price, opening, fee)
            if not self.cycle.quantity:
                self.cycle_history.append(self.cycle.record())
                self.cycle = None
        return status, delta

    def _clamp_buy_shares(self, planned, price):
        snapshot = self._snapshot_account()
        reserved = self.portfolio.reserved_cash(exclude='')
        if self.cycle and self.cycle.direction == 'SHORT':
            reserved -= self.cycle.reserve(snapshot['price'], CYCLE_FEE_RATE, CYCLE_MINIMUM_FEE)
        available = max(0.0, snapshot['cash'] - reserved)
        if price <= 0:
            return 0
        quantity = min(planned, int(available / price))
        while quantity > 0 and quantity * price + max(CYCLE_MINIMUM_FEE, quantity * price * CYCLE_FEE_RATE) > available:
            quantity -= 1
        return quantity

    def _clamp_sell_shares(self, planned):
        supported = super()._clamp_sell_shares(planned)
        if self.cycle is None:
            paired = sum(r.cycle.quantity for r in self.portfolio.symbol_runners(self.stock_qmt)
                         if r.cycle and r.cycle.direction == 'LONG')
            supported = min(supported, max(0, self.st.get('base_can_use', 0) - paired))
        return supported


class PortfolioRunner(ExecutionPortfolio):
    """Two serialized per-stock lanes. Live entry remains disabled during research."""
    def symbol_runners(self, code):
        return [r for r in self.runners.values() if r.stock_qmt == code]

    def _audit_account_trades(self):
        super()._audit_account_trades()
        orders = self.conn.trader.query_stock_orders(self.conn._account_obj)
        if orders is None:
            raise RuntimeError('order audit unavailable')
        seen = getattr(self, '_audited_order_ids', None)
        current = {str(order.order_id) for order in orders}
        if seen is not None:
            for order in orders:
                oid = str(order.order_id)
                if oid not in seen and oid not in self.own_order_ids:
                    for runner in self.symbol_runners(str(order.stock_code)):
                        runner.paused_reason = 'external order detected: ' + oid
                        runner._log('[CYCLE-PAUSED] ' + runner.paused_reason)
        self._audited_order_ids = current if seen is None else seen | current

    def reserved_cash(self, exclude):
        total = 0.0
        for runner in self.runners.values():
            if runner.stock_qmt == exclude or not runner.cycle:
                continue
            tick = self.conn.get_full_tick([runner.stock_qmt]).get(runner.stock_qmt, {})
            price = float(tick.get('lastPrice', 0) or 0)
            if price <= 0 and runner.cycle.direction == 'SHORT':
                return float('inf')
            total += runner.cycle.reserve(price, CYCLE_FEE_RATE, CYCLE_MINIMUM_FEE)
        return total

    def _prepare_trading_day(self):
        for runner in self.runners.values():
            if not runner.st.get('initialized'):
                runner._init_state()
                runner._daily_init()
                runner._restored = True
            else:
                runner._rollover_cycle_day()
        # Active cycles run first, in opening order, before any flat lane.
        self.tasks = dict(sorted(self.tasks.items(), key=lambda item: (
            0 if self.runners[item[0]].cycle else 1,
            self.runners[item[0]].cycle.opened_time if self.runners[item[0]].cycle else item[0])))

    def refresh_holdings(self, now):
        old = set(self.runners)
        super().refresh_holdings(now)
        positions = {p.stock_code: int(p.volume) for p in self.conn.query_positions()}
        for key in set(self.runners) - old:
            runner = self.runners[key]
            runner.baseline_shares = positions.get(runner.stock_qmt, 0)
            if '#' not in key and MAX_OPEN_CYCLES > 1:
                sibling = StrategyRunner(self, runner.stock_qmt, runner.stock_name, lane=1)
                sibling.baseline_shares = runner.baseline_shares
                self.runners[key + '#1'] = sibling
                self.tasks[key + '#1'] = (sibling.run(), 0.0)
        for code in {r.stock_qmt for r in self.runners.values()}:
            siblings = self.symbol_runners(code)
            try:
                base = siblings[0].baseline_shares
                if base is None or any(r.baseline_shares != base for r in siblings):
                    raise ValueError('inconsistent original baseline')
                cycles = [r.cycle for r in siblings if r.cycle]
                validate_cycles(cycles, base, positions.get(code, 0))
            except ValueError as error:
                for runner in siblings:
                    runner.paused_reason = str(error)
                _log('[CYCLE-PAUSED] {} {}'.format(code, error))

    def restore_checkpoint(self):
        if self.dry_run:
            return
        saved = read_checkpoint(STATE_FILE, ACCOUNT)
        if not saved:
            # Read-only upgrade is deliberately restricted to a uniquely flat ledger.
            old_path = os.path.join(os.path.dirname(STATE_FILE), 'v49_{}.json'.format(ACCOUNT))
            saved = read_checkpoint(old_path, ACCOUNT)
            if saved:
                if saved.get('inflight') or saved.get('order_uncertain'):
                    raise RuntimeError('UPGRADE BLOCKED: unresolved legacy order; source checkpoint retained')
                if saved['broker'] != self._broker_snapshot():
                    raise RuntimeError('UPGRADE BLOCKED: legacy broker snapshot differs')
                positions = {row[0]: row[1] for row in saved['broker']['positions']}
                for code, record in saved['runners'].items():
                    state = record['state']
                    if (any(record['legs'].values()) or state.get('short_legs') or
                            state.get('long_legs') or
                            state.get('mom_leg_shares', 0) or
                            state.get('mom_state', 'MOM_IDLE') not in (
                                '', 'MOM_IDLE', None)):
                        raise RuntimeError('UPGRADE BLOCKED: legacy open legs lack unique order-to-cycle ownership')
                    record['v52'] = dict(lane=0, baseline=positions.get(code, 0),
                        cycle=None, history=[], paused='', imported_counts={state['trade_date']: {
                            'REV-T sell': state.get('trade_count_short', 0),
                            'FWD-T buy': state.get('trade_count_long', 0)}})
                _log('[UPGRADE] flat legacy ledger imported read-only; old file unchanged')
            else:
                self.checkpoint_active = True
                return
        if saved.get('inflight') or saved.get('order_uncertain'):
            raise RuntimeError('v52 unresolved broker order; retained checkpoint requires reconciliation')
        current = self._broker_snapshot()
        previous = saved['broker']
        # T+1 sellable changes at a verified new day; total inventory and orders must still agree.
        def inventory(snapshot):
            return [(row[0], row[1]) for row in snapshot['positions']]
        if inventory(previous) != inventory(current) or previous['orders'] != current['orders']:
            raise RuntimeError('v52 broker inventory/orders differ from checkpoint')
        self.own_order_ids = set(saved.get('own_order_ids', []))
        for key, record in saved['runners'].items():
            code = key.split('#')[0]
            runner = StrategyRunner(self, code, record.get('name', ''))
            runner.restore_record(record)
            self.runners[key] = runner
            self.tasks[key] = (runner.run(), 0.0)
        self.checkpoint_active = True


def main():
    parser = argparse.ArgumentParser(description='v56: confirmed reversals and protected core position')
    parser.add_argument('--mode', default='signal', choices=['signal', 'live'])
    args = parser.parse_args()
    if INTRADAY_REFERENCE_MODE not in ('shadow', 'active'):
        raise ValueError('INTRADAY_REFERENCE_MODE must be shadow or active')
    logger = FileLogger('portfolio', version='v56')
    set_logger(logger)
    try:
        if args.mode == 'live':
            print('LIVE: apply T strategy to ALL current and newly detected account holdings. Account: {}'.format(ACCOUNT))
            if input('Type yes to continue: ').strip().lower() != 'yes':
                return
        _log('[REFERENCE-MODE] {}: coefficient scaling affects execution baseline; shadow intraday strength is observation only; active requires user-approved validation'.format(
            INTRADAY_REFERENCE_MODE))
        PortfolioRunner(dry_run=args.mode == 'signal').run()
    finally:
        logger.close()


if __name__ == '__main__':
    main()
