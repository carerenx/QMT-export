# -*- coding: utf-8 -*-
"""CaptureT v4 — v1 的日内反T + 底仓的日线趋势风控开关。

v1/v2/v3 都是纯日内：底仓永远满着，只在日内做反T。三件套实测的结论是
「这条规则的价值几乎全部在反T 那一侧」（v1 +12,701.68 › v2 +9,581.56 › v3 +367.52）。
但三个版本有一个共同的、从未被检验过的前提：**底仓永远不动**。

2026 年 601869 从 6/24 的 579.71 跌到 8/3 的 262 附近，**回撤 −54.8%**，
底仓 800 股在这波里蒸发了约 25 万元 —— 而 v1 全年只跑赢持有 12,701 元。
**风控开关的量级比日内做T 大一个数量级。**

所以在 v1 之上加一层**日线**开关（这是本系列第一次用日线数据）：

  * 每个交易日开盘前，用**截至昨日**的日线计算：
      - ATR(14)：近 14 日 (high − low) 均值
      - peak：历史最高收盘价（含昨日）
      - MA20
  * 若 收盘价 < peak − K_ATR × ATR   → **清仓**（卖出全部底仓），当天不做T
  * 若已清仓 且 收盘价 > MA20        → **回场**（买回 800 股）
  * 其余情况维持现有仓位

日内仍然跑 v1 的 VWAP ± 3σ 反T，但**只在持有底仓时**（空仓没有底仓可卖）。

参数的选择过程（必须披露）：
  * K_ATR = 3.0、ATR 周期 14 都是教科书默认值，没有搜索；
  * 在 timing_lab 里扫过 K = 1.0~6.0：2.0/2.5/3.0/4.0/5.0/6.0 全部为正（+32k~+72k），
    **整段同号**；而另一族「回撤 x% 清仓」的参数面是锯齿状的
    （25% 给 +104k、30% 掉到 −0.3k），那是拟合的形状，所以弃用。
  * 横截面：同一套参数套到 13 个标的上，ATR 规则 **11/13 为正**、均值 +6.0pp，
    是全部候选里最好的。**规则是按稳健性选的，不是按最高分选的。**

无未来函数：日线快照用的是 `load_daily_snapshot`，回放框架自带断言，
保证返回的最后一根日线**严格早于当日**。信号在开盘前算完，当天成交。

This is NOT a continuation of the DayTradeing_v13-v41 / DayT_v39-v058 lines.
Its intraday core is CaptureT v1 unchanged; the addition is the daily overlay.
"""
K_SIGMA = 3.0           # 实验室在 IS-1 上选出并冻结；不要按结果回调
WARMUP_MINUTES = 30     # 开盘后先积累样本再算 VWAP/σ
FORCE_FLAT_TIME = '14:57:00'
NO_NEW_ENTRY_CUTOFF = '14:45:00'
MIN_SIGMA_SAMPLES = 20

# ── 日线风控开关（本系列第一次用日线数据）──
# 关键设计：开关状态是**日线序列的纯函数**，不需要跨日保存。
# 本系列每天重建策略实例（每日重置），任何跨日状态都会丢；
# 而「ATR 跟踪止损 + MA20 回场」的开关历史可以每天早上从日线重放出来。
ATR_PERIOD = 14         # 教科书默认
K_ATR = 3.0             # 教科书默认；LAB 扫过 1.0~6.0，K>=2.0 整段为正
MA_REENTRY = 20         # 回场条件：收盘重新站上 MA20
DAILY_LOOKBACK = 280    # 取多少根日线来重放
BASE_TARGET_SHARES = 800  # 风控回场时买回多少股（应与账户底仓一致）

import math
import os
import sys
import time as _time
import traceback as _traceback
from datetime import datetime

import numpy as np
import pandas as pd

_STRATEGY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPOSITORY_ROOT = os.path.dirname(os.path.dirname(_STRATEGY_ROOT))
for _module_root in (_REPOSITORY_ROOT, _STRATEGY_ROOT):
    if _module_root not in sys.path:
        sys.path.insert(0, _module_root)

from core import config as cfg
from core.execution_book import ExecutionBook
from Stragety.MiniQMT_Stragety.DayT.infra.logger import (
    FileLogger, set_logger, get_logger, _log, _log_file_only,
)
from Stragety.MiniQMT_Stragety.DayT.infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares, set_global_conn,
)

ACCOUNT = cfg.ACCOUNT
TRADE_LOT_SIZE = cfg.TRADE_LOT_SIZE
STATE_IDLE = cfg.STATE_IDLE
STATE_SOLD = cfg.STATE_SOLD
STATE_DONE = cfg.STATE_DONE

FILL_TIMEOUT_SEC = 8.0

# 风控开关的两条腿**不是 T 腿**，不进 ExecutionBook。
# 原因：本系列每个交易日重建策略实例，ExecutionBook 也跟着重建，
# 而风控的一条腿可能跨好几天（今天清仓、下周才回场）。
# 昨天的 RISK-OFF 记录今天已经不存在，RISK-RESTORE 会被当成
# 「平掉不存在的腿」直接抛错。风控的盈亏本来就体现在账户权益里，
# 不需要也不应该由 T 台账来记账。
RISK_LABELS = ('RISK-OFF sell', 'RISK-RESTORE buy')
TERMINAL_ORDER_STATUSES = (53, 54, 56, 57)


def session_vwap(amount, pvolume):
    """Cumulative session VWAP from the tick's running totals."""
    amount = float(amount or 0.0)
    pvolume = float(pvolume or 0.0)
    if amount <= 0 or pvolume <= 0:
        return 0.0
    return amount / pvolume


class StrategyRunner:
    """Full-position intraday VWAP reversion. One round trip per session.

    Labels are the REV-T ones on purpose: ExecutionBook classifies
    'REV-T sell' as opening the SHORT group and '*buyback*' as closing it,
    which is exactly what selling the base and buying it back is.
    """

    def __init__(self, portfolio, stock_qmt, stock_name=''):
        self.portfolio = portfolio
        self.stock_qmt = stock_qmt
        self.stock_code = stock_qmt.split('.')[0]
        self.stock_name = stock_name or stock_qmt
        self.trade_lot = TRADE_LOT_SIZE
        self.version = 'CaptureT_v4'
        self.conn = SymbolConnector(portfolio.conn, stock_qmt)
        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = portfolio.dry_run
        self._running = True
        self._last_heartbeat = 0.0
        self.total_t_days = 0
        self.total_pnl = 0.0
        self.execution_book = ExecutionBook()
        self._execution_price = None
        self._submitted_order_id = None
        self._last_cycle_gross = 0.0
        self._opened_minutes = 0

    def _log(self, message):
        _log('[{}]{}'.format(self.stock_code, message))

    def _file_log(self, message):
        _log_file_only('[{}]{}'.format(self.stock_code, message))

    def has_open_legs(self):
        return bool(self.st.get('short_legs')) or self.st.get('fstate') == STATE_SOLD

    # ═══ 状态 ═══

    def _init_state(self):
        self.st.update({
            'fstate': STATE_IDLE, 'initialized': False, 'trade_date': '',
            'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'short_legs': [], 'long_legs': [],
            'sell_fill_price': 0.0, 'sell_shares': 0,
            'prices': [], 'total_t_days': self.total_t_days,
            'risk_on': True, 'risk_reason': '', 'daily_ready': False,
            'atr': 0.0, 'peak': 0.0, 'ma20': 0.0, 'daily_close': 0.0,
            'total_pnl': self.total_pnl, 'day_pnl': 0.0,
            'vwap': 0.0, 'sigma': 0.0, 'upper': 0.0, 'lower': 0.0,
            'last_init_time': 0.0, 'lock_reason': '',
        })

    def _lock_all_trading(self, reason):
        self.st['initialized'] = False
        self.st['lock_reason'] = reason
        self._log('[TRADE-LOCK] {}; all trading disabled'.format(reason))

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        found = False
        for pos in positions:
            if pos.m_strInstrumentID == self.stock_code:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(
                    pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                found = True
                break
        if not found:
            self.st['base_shares'] = 0
            self.st['base_can_use'] = 0
            self.st['base_cost'] = 0.0

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if (self.st.get('trade_date', '') == today and
                self.st.get('initialized', False)):
            self._refresh_position()
            return
        self._init_state()
        self.st['trade_date'] = today
        self._refresh_position()
        self._update_risk_switch()
        # v1 把「没有底仓」当成致命错误直接锁死。v4 不能这么做：
        # 空仓正是风控开关**主动**造成的合法状态——
        #   * risk_on=False → 本来就该空着，等回场信号；
        #   * risk_on=True  → 今天正好是回场日，下面会买回来。
        # 两种情况都不是异常，所以 v4 不再因为空仓而锁死。
        self.st['initialized'] = True
        if self.st.get('base_can_use', 0) <= 0:
            self._log('[FLAT] 当前无可卖底仓; risk_on={}'.format(
                self.st.get('risk_on')))
        self._log('[INIT] base={} sh can_use={} risk_on={} | '
                  'K={:.1f}sigma warmup={}min force-flat={} | {}'.format(
                      self.st['base_shares'], self.st['base_can_use'],
                      self.st.get('risk_on'), K_SIGMA, WARMUP_MINUTES,
                      FORCE_FLAT_TIME, self.st.get('risk_reason', '')))

    # ═══ 规则 ═══

    def _update_reference(self, tick_data, price):
        """Accumulate the session sample and refresh VWAP / sigma / bands."""
        st = self.st
        vwap = session_vwap(tick_data.get('amount'), tick_data.get('pvolume'))
        if vwap > 0:
            st['vwap'] = vwap
        prices = st['prices']
        prices.append(float(price))
        if len(prices) > 240:
            del prices[:-240]
        if len(prices) < MIN_SIGMA_SAMPLES:
            return
        sigma = float(np.std(prices))
        st['sigma'] = sigma
        if st['vwap'] > 0 and sigma > 0:
            st['upper'] = st['vwap'] + K_SIGMA * sigma
            st['lower'] = st['vwap'] - K_SIGMA * sigma

    def _ready(self):
        return (self.st.get('upper', 0) > 0 and
                len(self.st.get('prices', [])) >= MIN_SIGMA_SAMPLES)

    # ═══ 日线趋势风控 ═══

    def _load_daily(self):
        """取**严格早于今日**的日线。回放里 load_daily_snapshot 自带这个断言。"""
        try:
            snapshot = self.conn.load_daily_snapshot(
                DAILY_LOOKBACK, stock_code=self.stock_qmt)
        except Exception as error:
            self._log('[DAILY-LOAD] {}'.format(error))
            return None
        if not snapshot:
            return None
        frame = snapshot.get('adjusted')
        if frame is None:
            frame = snapshot.get('raw')
        need = max(ATR_PERIOD, MA_REENTRY) + 5
        if frame is None or len(frame) < need:
            return None
        return frame

    def _update_risk_switch(self):
        """开盘前跑一次：从日线**重放**出今天的开关状态。

        无未来函数：frame 的最后一根是昨日，不含今日。
        无跨日状态：整条开关历史每天早上由日线重新推出来。
        """
        st = self.st
        frame = self._load_daily()
        if frame is None:
            return
        high = frame['high'].astype(float).to_numpy()
        low = frame['low'].astype(float).to_numpy()
        close = frame['close'].astype(float).to_numpy()
        n = len(close)
        if n < max(ATR_PERIOD, MA_REENTRY) + 1:
            return

        # 逐步重放：ATR / MA20 都只看当日及之前，peak 是运行最高收盘
        risk_on, peak = True, close[0]
        reason = 'INIT 满仓'
        for i in range(n):
            peak = max(peak, close[i])
            if i < max(ATR_PERIOD, MA_REENTRY):
                continue
            atr = float((high[i - ATR_PERIOD + 1:i + 1] -
                         low[i - ATR_PERIOD + 1:i + 1]).mean())
            ma = float(close[i - MA_REENTRY + 1:i + 1].mean())
            if risk_on:
                if atr > 0 and close[i] < peak - K_ATR * atr:
                    risk_on = False
                    reason = ('RISK-OFF Y{:.2f} < peak Y{:.2f} - {:.1f}xATR '
                              'Y{:.2f}'.format(close[i], peak, K_ATR,
                                               peak - K_ATR * atr))
            elif close[i] > ma:
                risk_on = True
                reason = 'RE-ENTRY Y{:.2f} > MA20 Y{:.2f}'.format(close[i], ma)

        atr_now = float((high[-ATR_PERIOD:] - low[-ATR_PERIOD:]).mean())
        st.update({'risk_on': risk_on, 'risk_reason': reason,
                   'daily_ready': True, 'atr': atr_now, 'peak': peak,
                   'ma20': float(close[-MA_REENTRY:].mean()),
                   'daily_close': float(close[-1])})

    def _apply_risk_switch(self, price):
        """把开关状态落到仓位上。返回 True 表示本日已处理完，跳过日内做T。"""
        st = self.st
        if not st.get('daily_ready'):
            return False
        holding = int(st.get('base_shares', 0)) > 0
        if not st.get('risk_on', True) and holding:
            can_use = int(st.get('base_can_use', 0))
            if can_use > 0:
                self._log('[RISK] {} → 清仓卖出 {} 股'.format(
                    st.get('risk_reason', ''), can_use))
                self._submit_order(-can_use, price, 'RISK-OFF sell')
            return True
        if st.get('risk_on', True) and not holding:
            shares = int(BASE_TARGET_SHARES)
            if shares > 0:
                self._log('[RISK] {} → 回场买回 {} 股'.format(
                    st.get('risk_reason', ''), shares))
                self._submit_order(shares, price, 'RISK-RESTORE buy')
            return True
        return False

    # ═══ 下单 ═══

    def _submit_order(self, shares, price, label, style='COMPETE'):
        self._last_executed_order = None
        side = 'SELL' if shares < 0 else 'BUY'
        if side == 'SELL':
            self._refresh_position()
            actual = int(min(abs(shares), self.st.get('base_can_use', 0)))
        else:
            actual = abs(shares)
        actual = actual // self.trade_lot * self.trade_lot
        if actual < self.trade_lot:
            self._log('[{} SKIP] {} 不足 (planned {})'.format(
                label, '可卖' if side == 'SELL' else '现金', abs(shares)))
            return 'SKIP', 0
        if self.dry_run:
            self._log('[SIGNAL-ORDER] {} {} {} sh @ {}'.format(
                label, side, actual, price))
            return 'SKIP', 0
        signed = -actual if side == 'SELL' else actual
        self._log('[ORDER-{}] {} × {} sh'.format(
            label, 'MKT' if price <= 0 else 'Y{:.2f}'.format(price), actual))
        order_id = order_shares(
            self.stock_qmt, signed, style, price, self.ctx, ACCOUNT)
        if order_id is None or str(order_id) in ('', '0', '-1'):
            raise RuntimeError('submission outcome unknown; inspect broker')
        self.portfolio.own_order_ids.add(str(order_id))
        self._submitted_order_id = order_id
        return self._wait_for_fill(signed, label, price)

    def _wait_for_fill(self, expected_shares_delta, label, trade_price,
                       timeout_sec=FILL_TIMEOUT_SEC):
        wanted = abs(expected_shares_delta)
        sign = 1 if expected_shares_delta > 0 else -1
        order_id = self._submitted_order_id
        deadline = _time.monotonic() + timeout_sec
        cancelled = False
        while True:
            try:
                order = self.conn.trader.query_stock_order(
                    self.conn._account_obj, order_id)
                if order is not None:
                    ids = (str(getattr(order, 'order_id', '')),
                           str(getattr(order, 'order_sysid', '')))
                    volume = int(order.traded_volume or 0)
                    price = float(order.traded_price or 0)
                    terminal = int(order.order_status) in TERMINAL_ORDER_STATUSES
                    if terminal and volume == 0:
                        return 'TIMEOUT', 0
                    if ((volume == wanted or terminal) and volume > 0 and
                            math.isfinite(price) and price > 0):
                        self._execution_price = price
                        if label in RISK_LABELS:
                            # 风控腿跨日，T 台账按日重建，记不了也不该记。
                            # 它的盈亏已经在账户现金/持仓里体现了。
                            gross, completed, cycle = 0.0, False, 0.0
                        else:
                            gross, completed, cycle = self.execution_book.record(
                                order_id, label, sign * volume, price)
                        self.total_pnl += gross
                        self.st['day_pnl'] = self.st.get('day_pnl', 0) + gross
                        self.total_t_days += int(completed)
                        self._last_cycle_gross = cycle
                        self._refresh_position()
                        self._log('[EXECUTION] {} qty={} avg=Y{:.4f} gross=Y{:.2f} '
                                  'cum=Y{:.2f}'.format(
                                      label, volume, price, gross, self.total_pnl))
                        return ('FILLED' if volume == wanted
                                else 'PARTIAL'), sign * volume
            except Exception as error:
                self._log('[EXECUTION-WAIT] {}'.format(error))
            if _time.monotonic() >= deadline:
                if not cancelled:
                    self.conn.cancel_order(order_id)
                    cancelled = True
                    deadline = _time.monotonic() + timeout_sec
                else:
                    raise RuntimeError('unresolved broker order ' + str(order_id))
            _time.sleep(0.5)

    # ═══ 主循环 ═══

    def run(self):
        self._init_state()
        self._log('[START] {} {}'.format(
            self.version, 'SIGNAL' if self.dry_run else 'LIVE'))
        try:
            self._daily_init()
        except Exception as e:
            self._lock_all_trading('daily init exception: {}'.format(e))
            self._log('[ERROR] init failed: {}'.format(e))
            _traceback.print_exc()

        try:
            while self._running:
                now = cfg.now_hms()
                now_ts = _time.time()
                today = datetime.now().strftime('%Y%m%d')

                if not cfg.is_market_open(now):
                    if (not self.st.get('initialized', False) or
                            self.st.get('trade_date', '') != today):
                        if now_ts - self.st.get('last_init_time', 0.0) >= 60.0:
                            self.st['last_init_time'] = now_ts
                            try:
                                self._daily_init()
                            except Exception as e:
                                self._lock_all_trading('init failed: {}'.format(e))
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        self._file_log('[WAIT {}]'.format(now))
                    (yield 10); continue

                tick = self.ctx.get_full_tick([self.stock_qmt])
                if self.stock_qmt not in tick:
                    (yield 1); continue
                tick_data = tick[self.stock_qmt]
                price = tick_data.get('lastPrice', 0)
                if not math.isfinite(price) or price <= 0:
                    (yield 1); continue

                self._update_reference(tick_data, price)
                fstate = self.st.get('fstate', STATE_IDLE)

                # ── 尾盘强平：唯一保证「每天都是平的」的机制 ──
                if fstate == STATE_SOLD and now >= FORCE_FLAT_TIME:
                    shares = int(self.st.get('sell_shares', 0))
                    self._log('[FORCE-FLAT] {} 强平 {} 股'.format(now, shares))
                    status, delta = self._submit_order(shares, price, 'REV-T buyback(FORCE)')
                    if delta:
                        price = self._execution_price
                    self.st['short_legs'] = list(
                        self.execution_book.legs.get('SHORT', []))
                    self.st['fstate'] = (STATE_IDLE if not self.st['short_legs']
                                         else STATE_SOLD)
                    (yield 0.5); continue

                # 日线开关优先：需要清仓/回场时先处理，空仓时不做T。
                if self.st.get('risk_switch_done') != today:
                    self.st['risk_switch_done'] = today
                    self._apply_risk_switch(price)
                if not self.st.get('risk_on', True):
                    (yield 0.5); continue

                if self._ready():
                    if fstate == STATE_IDLE and now < NO_NEW_ENTRY_CUTOFF:
                        if price >= self.st['upper']:
                            can_use = int(self.st.get('base_can_use', 0))
                            self._log('[SIGNAL] Y{:.2f} >= VWAP Y{:.2f} + {:.1f}σ '
                                      '(Y{:.2f}) → 全仓卖出 {} 股'.format(
                                          price, self.st['vwap'], K_SIGMA,
                                          self.st['upper'], can_use))
                            status, delta = self._submit_order(
                                -can_use, price, 'REV-T sell')
                            if delta:
                                price = self._execution_price
                            if status not in ('SKIP', 'TIMEOUT'):
                                sold = -delta
                                self.st['sell_shares'] = sold
                                self.st['sell_fill_price'] = price
                                self.st['short_legs'] = [(price, sold)]
                                self.st['fstate'] = STATE_SOLD
                    elif fstate == STATE_SOLD:
                        if price <= self.st['lower']:
                            shares = int(self.st.get('sell_shares', 0))
                            self._log('[SIGNAL] Y{:.2f} <= VWAP Y{:.2f} - {:.1f}σ '
                                      '(Y{:.2f}) → 全仓买回 {} 股'.format(
                                          price, self.st['vwap'], K_SIGMA,
                                          self.st['lower'], shares))
                            status, delta = self._submit_order(
                                shares, price, 'REV-T buyback(VWAP)')
                            if status not in ('SKIP', 'TIMEOUT'):
                                self.st['short_legs'] = []
                                self.st['fstate'] = STATE_DONE

                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    self._heartbeat(price)
                (yield 0.5)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            self._log('[ERROR] {}'.format(e))
            _traceback.print_exc()
        finally:
            if self.st.get('fstate') == STATE_SOLD:
                self._log('[WARN] session ended with the base position still sold')
            self._log('[STOP] {} cum {} trips gross~Y{:,.0f}'.format(
                self.stock_name, self.total_t_days, self.total_pnl))

    def _heartbeat(self, price):
        st = self.st
        if st.get('fstate') == STATE_SOLD:
            sp = st.get('sell_fill_price', 0)
            self._file_log('[HB] SOLD Y{:.2f} | sold Y{:.2f} ({:+.2f}%) | '
                           'buy back at Y{:.2f}'.format(
                               price, sp, (sp - price) / sp * 100 if sp else 0,
                               st.get('lower', 0)))
        else:
            self._file_log('[HB] {} Y{:.2f} | VWAP Y{:.2f} σ{:.3f} | '
                           'sell>=Y{:.2f} buy<=Y{:.2f}'.format(
                               st.get('fstate'), price, st.get('vwap', 0),
                               st.get('sigma', 0), st.get('upper', 0),
                               st.get('lower', 0)))


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


class PortfolioRunner:
    """One account, one symbol. No checkpoint, no reconciliation, no watchdog."""
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.conn = MiniQMTConnector()
        self.runners = {}
        self.order_uncertain = False
        self.own_order_ids = set()

    def reserved_cash(self, exclude):
        """A session holds at most one open sell; nothing else may spend its cash."""
        reserve = 0.0
        for code, runner in self.runners.items():
            if code == exclude:
                continue
            reserve += sum(price * shares
                           for price, shares in runner.st.get('short_legs', []))
        return reserve * 1.01

    def _prepare_trading_day(self):
        today = datetime.now().strftime('%Y%m%d')
        for runner in list(self.runners.values()):
            if (runner.st.get('initialized') and
                    runner.st.get('trade_date') == today):
                continue
            if runner.has_open_legs():
                _log('[DAILY-RESET] {} discarding open legs from {}'.format(
                    runner.stock_qmt, runner.st.get('trade_date')))
            runner._init_state()
            runner.execution_book = ExecutionBook()
            runner._daily_init()

    def save_checkpoint(self, force=False, settled=False):
        """Interface parity only; this family keeps no state across sessions."""
        return None

    def run(self):
        set_global_conn(self.conn, self.dry_run)
        if not self.conn.connect_data():
            _log('[PORTFOLIO-ERROR] market connection failed')
            return
        if not self.conn.connect_trade():
            _log('[PORTFOLIO-ERROR] account connection failed')
            return
        _log('[PORTFOLIO-START] CaptureT_v4; mode={}'.format(
            'SIGNAL' if self.dry_run else 'LIVE'))
        runner = StrategyRunner(self, cfg.STOCK_QMT, cfg.STOCK_NAME)
        self.runners[cfg.STOCK_QMT] = runner
        gen = runner.run()
        try:
            while True:
                next(gen)
        except StopIteration:
            pass
        finally:
            gen.close()
            self.conn.disconnect()


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='CaptureT v1: full-position intraday VWAP reversion, one trip per session')
    parser.add_argument('--mode', default='signal', choices=['signal', 'live'])
    args = parser.parse_args()
    logger = FileLogger('portfolio', version='capturet_v4')
    set_logger(logger)
    try:
        if args.mode == 'live':
            print('LIVE: CaptureT v4 on {}. Account: {}'.format(
                cfg.STOCK_QMT, ACCOUNT))
            if input('Type yes to continue: ').strip().lower() != 'yes':
                return
        PortfolioRunner(dry_run=args.mode == 'signal').run()
    finally:
        logger.close()


if __name__ == '__main__':
    main()
