# -*- coding: utf-8 -*-
"""CaptureT v3 — 只做正T（先买后卖），v1 的镜像。

v1（只有反T）、v2（反T+正T）都已实测。v2 的结论是：正T 这条腿本身不差
（替代掉反T 的那 2 笔还多赚 1,767 元），漏损全部来自 4 个 v1 整天不出手的日子
（合计 -4,589 元）—— 那些天价格先跌破下带、全天再没回上带。

那么一个自然的问题是：**如果只做正T，不做反T，会怎样？**
本文件就是为了回答它：把 v1 的上带分支整个删掉，只保留 v2 的下带买入腿。
这是三件套里最后一个对照，也是唯一一个**不需要卖出底仓**就能开仓的版本 ——
代价是它必须占用现金，且 A股 T+1 下当天买的不能当天卖，
卖出只能来自底仓。

正T 在 T+1 下的落地方式（与 v2 完全一致）：
  1. 价格 <= VWAP - 3σ 时用现金**买入** N 股（N = min(半仓, 现金可买)）
     此时持仓 800+N，多出的 N 股当日不可卖
  2. 价格 >= VWAP + 3σ 时**卖出底仓** N 股（不是当天买的那批）
     持仓回到 800，P&L = N × (卖价 − 买价)
  3. 14:57 若仍未触发② → 同样卖底仓 N 股，把持仓归位

K_SIGMA 等参数与 v1/v2 完全一致，**没有重新调参** —— 在这条线上调参
就是把噪声拟合进结果，前面的实验已经证明过一次（K 敏感性会翻号）。

This is NOT a continuation of the DayTradeing_v13-v41 / DayT_v39-v058 lines.
Those size each leg at one lot via T_TARGET_VALUE and fight for a few basis
points.  This family exists to test one question, and the lab that produced it
(analysis/capturet_v1_lab_20260919.py) is part of the deliverable:

    how much of a day's high-low range can any causal rule actually take?

Mechanism, exactly as measured in the lab:
  * build the session's VWAP and price standard deviation from the ticks so far
  * price <= VWAP - K*sigma  -> buy N with cash              (正T, opens LONG)
  * price >= VWAP + K*sigma  -> sell N of the base to close it
  * 14:57                    -> force the net position back to the base, no exceptions
  * at most ONE round trip per session (the lab found extra trips lose money
    once fees are counted)
"""
K_SIGMA = 3.0           # 实验室在 IS-1 上选出并冻结；不要按结果回调
WARMUP_MINUTES = 30     # 开盘后先积累样本再算 VWAP/σ
FORCE_FLAT_TIME = '14:57:00'
NO_NEW_ENTRY_CUTOFF = '14:45:00'
MIN_SIGMA_SAMPLES = 20

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
STATE_DONE = cfg.STATE_DONE
STATE_BOUGHT = 'BT_BOUGHT'   # 正T：已用现金买入，等卖底仓

FILL_TIMEOUT_SEC = 8.0
TERMINAL_ORDER_STATUSES = (53, 54, 56, 57)


def session_vwap(amount, pvolume):
    """Cumulative session VWAP from the tick's running totals."""
    amount = float(amount or 0.0)
    pvolume = float(pvolume or 0.0)
    if amount <= 0 or pvolume <= 0:
        return 0.0
    return amount / pvolume


class StrategyRunner:
    """Forward-only intraday VWAP reversion. One trip per session.

    Only one lane: 'FWD-T buy' opens the LONG group, 'FWD-T sell*' closes it.
    The round trip leaves the net position back at the base — it buys with cash
    and sells the base back.  The cash spent on the entry is what caps the leg,
    so it shrinks as the price rises relative to the account's cash.
    """

    def __init__(self, portfolio, stock_qmt, stock_name=''):
        self.portfolio = portfolio
        self.stock_qmt = stock_qmt
        self.stock_code = stock_qmt.split('.')[0]
        self.stock_name = stock_name or stock_qmt
        self.trade_lot = TRADE_LOT_SIZE
        self.version = 'CaptureT_v3'
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
        return (bool(self.st.get('short_legs')) or
                self.st.get('fstate') == STATE_BOUGHT)

    # ═══ 状态 ═══

    def _init_state(self):
        self.st.update({
            'fstate': STATE_IDLE, 'initialized': False, 'trade_date': '',
            'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'short_legs': [], 'long_legs': [],
            'buy_fill_price': 0.0, 'buy_shares': 0,
            'cash_available': 0.0,
            'prices': [], 'total_t_days': self.total_t_days,
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

    def _refresh_cash(self):
        """Available cash — the binding constraint on every 正T entry."""
        available = None
        try:
            for acc in get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT'):
                value = getattr(acc, 'm_dAvailable', None)
                if value is None:
                    value = getattr(acc, 'cash', 0.0)
                available = float(value)
                break
        except Exception as error:
            self._log('[CASH-QUERY] {}'.format(error))
        if available is not None:
            self.st['cash_available'] = available
        return float(self.st.get('cash_available', 0.0) or 0.0)

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if (self.st.get('trade_date', '') == today and
                self.st.get('initialized', False)):
            self._refresh_position()
            return
        self._init_state()
        self.st['trade_date'] = today
        self._refresh_position()
        self._refresh_cash()
        if self.st.get('base_can_use', 0) <= 0:
            self._lock_all_trading('no sellable base position')
            return
        self.st['initialized'] = True
        self._log('[INIT] base={} sh can_use={} cash=Y{:,.0f} | K={:.1f}sigma '
                  'warmup={}min force-flat={}'.format(
                      self.st['base_shares'], self.st['base_can_use'],
                      self.st['cash_available'], K_SIGMA, WARMUP_MINUTES,
                      FORCE_FLAT_TIME))

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

    # ═══ 下单 ═══

    def _submit_order(self, shares, price, label, style='COMPETE'):
        self._last_executed_order = None
        side = 'SELL' if shares < 0 else 'BUY'
        if side == 'SELL':
            self._refresh_position()
            actual = int(min(abs(shares), self.st.get('base_can_use', 0)))
        else:
            actual = int(abs(shares))
            cash = self._refresh_cash()
            if price > 0 and cash > 0:
                affordable = int(cash / price) // self.trade_lot * self.trade_lot
                actual = min(actual, affordable)
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

    def _leg_shares(self, price):
        """Half the base, rounded to lots, capped by what the cash can buy."""
        base = int(self.st.get('base_can_use', 0) or 0)
        target = base // 2 // self.trade_lot * self.trade_lot
        cash = self._refresh_cash()
        if price > 0 and cash > 0:
            target = min(target, int(cash / price) // self.trade_lot * self.trade_lot)
        return target

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

                # ── 尾盘归位：唯一保证「每天收盘净持仓 = 底仓」的机制 ──
                # 正T 未卖出 → 卖底仓，把净持仓打回底仓。
                if now >= FORCE_FLAT_TIME and fstate == STATE_BOUGHT:
                    # 卖出底仓（不是卖出刚买的那批，那批 T+1 不可卖）→ 负股数 = SELL
                    shares = -int(self.st.get('buy_shares', 0))
                    self._log('[FORCE-FLAT] {} 强平 {} 股'.format(now, shares))
                    status, delta = self._submit_order(
                        shares, price, 'FWD-T sell(FORCE)')
                    if delta:
                        price = self._execution_price
                    self.st['long_legs'] = list(
                        self.execution_book.legs.get('LONG', []))
                    self.st['fstate'] = (STATE_BOUGHT if self.st['long_legs']
                                         else STATE_DONE)
                    (yield 0.5); continue

                if self._ready():
                    if fstate == STATE_IDLE and now < NO_NEW_ENTRY_CUTOFF:
                        if price <= self.st['lower']:
                            # 正T：用现金买入，卖出时卖底仓（T+1）
                            shares = self._leg_shares(price)
                            self._log('[SIGNAL] Y{:.2f} <= VWAP Y{:.2f} - {:.1f}σ '
                                      '(Y{:.2f}) → 正T 买入 {} 股'.format(
                                          price, self.st['vwap'], K_SIGMA,
                                          self.st['lower'], shares))
                            status, delta = self._submit_order(
                                shares, price, 'FWD-T buy')
                            if delta:
                                price = self._execution_price
                            if status not in ('SKIP', 'TIMEOUT'):
                                bought = delta
                                self.st['buy_shares'] = bought
                                self.st['buy_fill_price'] = price
                                self.st['long_legs'] = [(price, bought)]
                                self.st['fstate'] = STATE_BOUGHT
                    elif fstate == STATE_BOUGHT:
                        if price >= self.st['upper']:
                            shares = int(self.st.get('buy_shares', 0))
                            self._log('[SIGNAL] Y{:.2f} >= VWAP Y{:.2f} + {:.1f}σ '
                                      '(Y{:.2f}) → 正T 卖出底仓 {} 股'.format(
                                          price, self.st['vwap'], K_SIGMA,
                                          self.st['upper'], shares))
                            status, delta = self._submit_order(
                                -shares, price, 'FWD-T sell')
                            if status not in ('SKIP', 'TIMEOUT'):
                                self.st['long_legs'] = []
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
            if self.st.get('fstate') == STATE_BOUGHT:
                self._log('[WARN] session ended with the T leg still open ({})'
                          .format(self.st.get('fstate')))
            self._log('[STOP] {} cum {} trips gross~Y{:,.0f}'.format(
                self.stock_name, self.total_t_days, self.total_pnl))

    def _heartbeat(self, price):
        st = self.st
        if st.get('fstate') == STATE_BOUGHT:
            bp = st.get('buy_fill_price', 0)
            self._file_log('[HB] BOUGHT Y{:.2f} | bought Y{:.2f} ({:+.2f}%) | '
                           'sell base at Y{:.2f}'.format(
                               price, bp, (price - bp) / bp * 100 if bp else 0,
                               st.get('upper', 0)))
        else:
            self._file_log('[HB] {} Y{:.2f} | VWAP Y{:.2f} σ{:.3f} | '
                           'buy<=Y{:.2f} sell>=Y{:.2f}'.format(
                               st.get('fstate'), price, st.get('vwap', 0),
                               st.get('sigma', 0), st.get('lower', 0),
                               st.get('upper', 0)))


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
        """A 正T leg has spent its cash up front; nothing else may spend it."""
        reserve = 0.0
        for code, runner in self.runners.items():
            if code == exclude:
                continue
            reserve += sum(price * shares
                           for price, shares in runner.st.get('long_legs', []))
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
        _log('[PORTFOLIO-START] CaptureT_v3; mode={}'.format(
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
        description='CaptureT v3: forward-only VWAP reversion (FWD-T), one trip per session')
    parser.add_argument('--mode', default='signal', choices=['signal', 'live'])
    args = parser.parse_args()
    logger = FileLogger('portfolio', version='capturet_v3')
    set_logger(logger)
    try:
        if args.mode == 'live':
            print('LIVE: CaptureT v3 on {}. Account: {}'.format(
                cfg.STOCK_QMT, ACCOUNT))
            if input('Type yes to continue: ').strip().lower() != 'yes':
                return
        PortfolioRunner(dry_run=args.mode == 'signal').run()
    finally:
        logger.close()


if __name__ == '__main__':
    main()
