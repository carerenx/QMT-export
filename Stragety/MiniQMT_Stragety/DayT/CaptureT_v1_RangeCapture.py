# -*- coding: utf-8 -*-
"""CaptureT v1 — new family. Trade the full base position against intraday VWAP.

This is NOT a continuation of the DayTradeing_v13-v41 / DayT_v39-v058 lines.
Those size each leg at one lot via T_TARGET_VALUE and fight for a few basis
points.  This family exists to test one question, and the lab that produced it
(analysis/capturet_v1_lab_20260919.py) is part of the deliverable:

    how much of a day's high-low range can any causal rule actually take?

Measured answer, across three disjoint intervals and three symbols, with rule
parameters chosen on IS-1 only and then frozen:

  * perfect foresight (the hard ceiling, cash- and T+1-aware): 0.82% - 3.40%/day
  * best rule family, "VWAP deviation":                        +0.01% - +0.03%/day
  * naive causal rules (open-to-close, overnight):             negative
  * multi-trip variants of the same rule:                      worse than one trip

So the winning rule captures roughly 0.5%-1.0% of the available range.  It is
the only rule tested that is positive on all three intervals, which is why it is
the one implemented here — not because it gets anywhere near the stated 2%/day
target.  The report states the gap plainly; this file does not pretend to close
it.

Mechanism, exactly as measured in the lab:
  * build the session's VWAP and price standard deviation from the ticks so far
  * price >= VWAP + K*sigma  -> sell the whole base position
  * price <= VWAP - K*sigma  -> buy it all back
  * 14:57                    -> force the position flat, no exceptions
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
STATE_SOLD = cfg.STATE_SOLD
STATE_DONE = cfg.STATE_DONE

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
        self.version = 'CaptureT_v1'
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
        if self.st.get('base_can_use', 0) <= 0:
            self._lock_all_trading('no sellable base position')
            return
        self.st['initialized'] = True
        self._log('[INIT] base={} sh can_use={} | K={:.1f}sigma warmup={}min '
                  'force-flat={}'.format(
                      self.st['base_shares'], self.st['base_can_use'],
                      K_SIGMA, WARMUP_MINUTES, FORCE_FLAT_TIME))

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
        _log('[PORTFOLIO-START] CaptureT_v1; mode={}'.format(
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
    logger = FileLogger('portfolio', version='capturet_v1')
    set_logger(logger)
    try:
        if args.mode == 'live':
            print('LIVE: CaptureT v1 on {}. Account: {}'.format(
                cfg.STOCK_QMT, ACCOUNT))
            if input('Type yes to continue: ').strip().lower() != 'yes':
                return
        PortfolioRunner(dry_run=args.mode == 'signal').run()
    finally:
        logger.close()


if __name__ == '__main__':
    main()
