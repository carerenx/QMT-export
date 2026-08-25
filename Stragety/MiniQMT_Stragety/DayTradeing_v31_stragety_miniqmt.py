# -*- coding: utf-8 -*-
"""
MiniQMT 日内做T策略 v31

在 v30 基础上集中修复实盘安全问题：
1. MOM 默认关闭；主状态机与 MOM 统一预留可卖底仓。
2. 按 order_id 核对委托，部分成交先撤余单再推进状态。
3. 强平/止损/部分平仓失败时保留未平腿，不再误报完成。
4. 修复锁仓冷却、盘中初始化重试、开盘信号刷新和失效止损。
5. 日线指标只使用已完成交易日；MA 不再按价格猜测是否为今日K线。
6. 保存当日未平腿，进程重启后恢复并进入谨慎管理。
"""
import argparse
import json
import os
import sys
import time as _time
import traceback as _traceback
from datetime import datetime

import numpy as np
import pandas as pd

import DayTradeing_v30_stragety_miniqmt as v30
from core import config as cfg
from core.indicators import daily_range_ma
from core.signals import compute_signal
from infra.connector import get_trade_detail_data, order_shares, set_global_conn
from infra.logger import FileLogger, get_logger, set_logger, _log


ACCOUNT = v30.ACCOUNT
STOCK_CODE = v30.STOCK_CODE
STOCK_NAME = v30.STOCK_NAME
STOCK_QMT = v30.STOCK_QMT
TRADE_LOT_SIZE = v30.TRADE_LOT_SIZE

STATE_IDLE = v30.STATE_IDLE
STATE_SPIKING = v30.STATE_SPIKING
STATE_SOLD = v30.STATE_SOLD
STATE_DIPPING = v30.STATE_DIPPING
STATE_DONE = v30.STATE_DONE
STATE_FORCED = v30.STATE_FORCED
STATE_BT_DIPPING = v30.STATE_BT_DIPPING
STATE_BT_BOUGHT = v30.STATE_BT_BOUGHT
STATE_BT_SPIKING = v30.STATE_BT_SPIKING

LADDER_UP_STEP_PCT = v30.LADDER_UP_STEP_PCT
LADDER_DOWN_STEP_PCT = v30.LADDER_DOWN_STEP_PCT
FILL_TIMEOUT_SEC = v30.FILL_TIMEOUT_SEC

# v31 安全默认值。v30 文档称 MOM 已屏蔽，因此恢复为 False。
MOM_ENABLED = False
MOM_WINDOW_SEC = v30.MOM_WINDOW_SEC
MOM_ATR_WINDOW_SEC = v30.MOM_ATR_WINDOW_SEC
MOM_ATR_MULT = 3.0
MOM_TRIGGER_MIN_PCT = v30.MOM_TRIGGER_MIN_PCT
MOM_TRIGGER_MAX_PCT = v30.MOM_TRIGGER_MAX_PCT
MOM_SHORT_BUYBACK_PCT = 0.015
MOM_LONG_SELLBACK_PCT = 0.018
MOM_LOT_SIZE = v30.MOM_LOT_SIZE
MOM_MAX_DAILY_TRADES = v30.MOM_MAX_DAILY_TRADES
MOM_EMERGENCY_BUYBACK_ENABLED = True

MARKET_STATUS_PRINT_ENABLED = v30.MARKET_STATUS_PRINT_ENABLED
MARKET_STATUS_PRINT_INTERVAL_SEC = v30.MARKET_STATUS_PRINT_INTERVAL_SEC
MA20_RISK_RATIO = v30.MA20_RISK_RATIO

# 让继承自 v30 的非覆盖方法也遵循 v31 的安全开关。
v30.MOM_ENABLED = MOM_ENABLED
v30.MOM_ATR_MULT = MOM_ATR_MULT
v30.MOM_SHORT_BUYBACK_PCT = MOM_SHORT_BUYBACK_PCT
v30.MOM_LONG_SELLBACK_PCT = MOM_LONG_SELLBACK_PCT
v30.MOM_EMERGENCY_BUYBACK_ENABLED = MOM_EMERGENCY_BUYBACK_ENABLED


class StrategyRunner(v30.StrategyRunner):
    """v31：保留 v30 信号逻辑，覆盖订单、风控和恢复路径。"""

    _OPEN_LONG_LABELS = ('FWD-T buy', 'MOM long')
    _OPEN_SHORT_LABELS = ('REV-T sell', 'MOM short')
    _CLOSE_LONG_LABELS = ('FWD-T sell', 'FWD-T force sell', 'MOM sellback',
                          'MOM stop-loss', 'MOM force sell')

    def __init__(self, dry_run=False, restore_state=True):
        if get_logger() is None:
            set_logger(FileLogger(STOCK_CODE, version='v31'))
        super().__init__(dry_run=dry_run)
        self._state_persistence_enabled = bool(restore_state and not dry_run)
        self._state_restored = False
        self._last_state_fingerprint = None
        self._last_init_retry = 0.0
        self._opening_refresh_date = ''
        self._state_path = os.path.join(
            os.path.dirname(__file__), 'state',
            'DayTradeing_v31_{}_state.json'.format(STOCK_CODE))

    def _init_state(self):
        super()._init_state()
        self.st.update({
            'reconcile_required': False,
            'reconcile_reason': '',
            'last_order_id': None,
            'last_order_label': '',
            'stop_loss_retry_at': 0.0,
        })
        if getattr(self, '_state_persistence_enabled', False):
            self._restore_runtime_state()

    # ------------------------------------------------------------------
    # 进程重启恢复
    # ------------------------------------------------------------------
    def _runtime_state_fields(self):
        return (
            'trade_date', 'initialized', 'daily_signal', 'do_short', 'do_long',
            'fstate', 'peak_price', 'dip_price', 'sell_fill_price',
            'buyback_target', 'buyback_target_pct', 'short_legs', 'long_legs',
            'ladder_sell_target', 'ladder_buy_target', 'ladder_sold_count',
            'ladder_bought_count', 'trade_count_short', 'trade_count_long',
            'bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
            'bt_sellback_target', 'bt_sell_peak_price', 'mom_state',
            'mom_peak', 'mom_dip', 'mom_sell_price', 'mom_buy_price',
            'mom_leg_shares', 'mom_trade_count', 'reconcile_required',
            'reconcile_reason', 'state_enter_time', 'stop_loss_hit',
        )

    def _restore_runtime_state(self):
        try:
            if not os.path.exists(self._state_path):
                return
            with open(self._state_path, 'r', encoding='utf-8') as fh:
                saved = json.load(fh)
            today = datetime.now().strftime('%Y%m%d')
            if saved.get('trade_date') != today:
                return
            for key in self._runtime_state_fields():
                if key in saved:
                    self.st[key] = saved[key]
            self.st['short_legs'] = [tuple(x) for x in self.st.get('short_legs', [])]
            self.st['long_legs'] = [tuple(x) for x in self.st.get('long_legs', [])]
            has_open_leg = bool(
                self.st['short_legs'] or self.st['long_legs'] or
                self.st.get('mom_leg_shares', 0))
            if has_open_leg:
                self.st['reconcile_required'] = True
                self.st['reconcile_reason'] = '进程重启后恢复未平腿，请先核对账户'
            self._state_restored = True
            _log('[RECOVERY] 已恢复当日状态 fstate={} short={} long={} mom={}sh'.format(
                self.st.get('fstate'), self.st['short_legs'], self.st['long_legs'],
                self.st.get('mom_leg_shares', 0)))
        except Exception as e:
            self.st['reconcile_required'] = True
            self.st['reconcile_reason'] = '状态恢复失败: {}'.format(e)
            _log('[RECOVERY ERROR] {}'.format(e))

    def _persist_runtime_state(self, force=False):
        if not self._state_persistence_enabled:
            return
        data = {key: self.st.get(key) for key in self._runtime_state_fields()}
        fingerprint = repr(data)
        if not force and fingerprint == self._last_state_fingerprint:
            return
        try:
            state_dir = os.path.dirname(self._state_path)
            os.makedirs(state_dir, exist_ok=True)
            tmp_path = self._state_path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2,
                          default=self._json_default)
            os.replace(tmp_path, self._state_path)
            self._last_state_fingerprint = fingerprint
        except Exception as e:
            _log('[STATE SAVE ERROR] {}'.format(e))

    @staticmethod
    def _json_default(value):
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        raise TypeError('not JSON serializable: {!r}'.format(value))

    # ------------------------------------------------------------------
    # 日线口径和初始化
    # ------------------------------------------------------------------
    @staticmethod
    def _completed_closes_for_ma(hist_closes, last_close=None):
        """输入必须已按日期排除今日K线；相同收盘价不得被误删。"""
        closes = []
        for value in hist_closes:
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(price) and price > 0:
                closes.append(price)
        return closes

    @staticmethod
    def _index_dates(index):
        """兼容 DatetimeIndex、YYYYMMDD 和毫秒时间戳。"""
        values = list(index)
        if not values:
            return pd.DatetimeIndex([])
        first = values[0]
        try:
            if isinstance(first, (int, np.integer)):
                digits = len(str(abs(int(first))))
                if digits >= 13:
                    return pd.to_datetime(values, unit='ms', errors='coerce')
                if digits == 10:
                    return pd.to_datetime(values, unit='s', errors='coerce')
                return pd.to_datetime([str(v) for v in values], format='%Y%m%d', errors='coerce')
            return pd.to_datetime(values, errors='coerce')
        except Exception:
            return pd.DatetimeIndex([pd.NaT] * len(values))

    def _recompute_signal_from_completed_bars(self):
        bars = self.conn.get_daily_bars(cfg.HIST_DATA_LEN)
        if bars is None or len(bars) < 60:
            return False
        required = ('open', 'high', 'low', 'close', 'volume')
        if any(col not in bars.columns for col in required):
            return False

        dates = self._index_dates(bars.index)
        today = pd.Timestamp(datetime.now().date())
        valid_dates = ~pd.isna(dates)
        if valid_dates.any():
            completed = bars.loc[(dates < today) & valid_dates]
        else:
            # 无法识别日期时不猜价格，只使用缓存全量；同时要求人工关注。
            completed = bars
            self.st['reconcile_required'] = True
            self.st['reconcile_reason'] = '日线索引无法识别，无法确认是否包含今日K线'
        if len(completed) < 60:
            return False

        tick = self.ctx.get_full_tick([STOCK_QMT]).get(STOCK_QMT, {})
        last_close = float(tick.get('lastClose', 0) or 0)
        today_open = float(tick.get('open', 0) or 0)
        if today_open <= 0:
            today_open = last_close

        opens = completed['open'].astype(float).tolist()
        highs = completed['high'].astype(float).tolist()
        lows = completed['low'].astype(float).tolist()
        closes = completed['close'].astype(float).tolist()
        volumes = completed['volume'].astype(float).tolist()
        signal = compute_signal(opens, highs, lows, closes, volumes,
                                yesterday_close=last_close)
        if signal is None:
            return False

        # 指标使用完整历史日；只有当日触发价使用真实今开。
        if today_open > 0:
            range_pct = daily_range_ma(highs, lows, opens, 10)[-1]
            raw = today_open * (1.0 + signal['atr_pct'] * signal['sell_mult'] *
                                cfg.SELL_TRIGGER_SCALE)
            cap = today_open * (1.0 + range_pct * cfg.DAILY_RANGE_CAP_MULT)
            signal['sell_trigger_raw'] = round(raw, 2)
            signal['range_capped'] = bool(cfg.DAILY_RANGE_CAP_ENABLED and raw > cap)
            signal['sell_trigger'] = round(min(raw, cap), 2) if cfg.DAILY_RANGE_CAP_ENABLED else round(raw, 2)
            signal['open_price'] = today_open
        if last_close > 0:
            signal['close_yday'] = last_close

        self._refresh_position()
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0
        current = float(tick.get('lastPrice', 0) or today_open or signal['open_price'])
        min_lots = max(1, int(cfg.MIN_POSITION_LOTS))
        short_lots = min(base_can_use // TRADE_LOT_SIZE, cfg.MAX_DAILY_TRADES)
        cash_lots = int(avail_cash / (current * TRADE_LOT_SIZE * 1.01)) if current > 0 else 0
        sellable_lots = base_can_use // TRADE_LOT_SIZE
        long_lots = min(cash_lots, sellable_lots, cfg.MAX_DAILY_TRADES)

        do_short = bool(signal['do_short'] and short_lots >= min_lots)
        do_long = bool(long_lots >= min_lots)
        signal['do_short'] = do_short
        signal['short_reason'] = (signal.get('blocked_reason', '') if not signal['do_short']
                                  else '')
        floor = round(signal['open_price'] * (1.0 - cfg.BUY_TRIGGER_PCT), 2)
        trail = round(current * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
        signal['buy_trigger_floor'] = floor
        signal['buy_trigger_trail'] = trail
        signal['buy_trigger'] = max(floor, trail)
        signal['sellback_target_hint'] = round(
            signal['buy_trigger'] * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

        self.st.update({
            'daily_signal': signal, 'do_short': do_short, 'do_long': do_long,
            'short_lots': short_lots, 'long_lots': long_lots,
            'long_reason': '' if do_long else '现金或可卖底仓不足1手',
            'avail_cash': avail_cash,
            'pos_value': base_shares * current,
            'pos_pct': (base_shares * current /
                        (base_shares * current + avail_cash) * 100
                        if base_shares * current + avail_cash > 0 else 0),
            'ma_completed_closes': self._completed_closes_for_ma(closes),
            'initialized': True,
        })
        self.st.setdefault('trade_count_short', 0)
        self.st.setdefault('trade_count_long', 0)
        return True

    def _daily_init(self, force=False):
        today = datetime.now().strftime('%Y%m%d')
        already = (self.st.get('trade_date') == today and
                   self.st.get('initialized', False))
        if not already:
            super()._daily_init()
        else:
            self._refresh_position()
        if self.st.get('initialized', False) and (force or self._state_restored or not already):
            if not self._recompute_signal_from_completed_bars():
                self.st['initialized'] = False
                self.st['daily_signal'] = None
                _log('[INIT] 已完成日线不足或口径校验失败，等待重试')
            self._state_restored = False

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        if not getattr(self.conn, 'last_position_query_ok', bool(self.dry_run)):
            if not self.dry_run:
                self.st['reconcile_required'] = True
                self.st['reconcile_reason'] = '持仓查询失败'
            return False
        found = False
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = int(pos.m_nVolume)
                self.st['base_can_use'] = int(getattr(pos, 'm_nCanUseVolume', pos.m_nVolume))
                self.st['base_cost'] = float(pos.m_dOpenPrice)
                found = True
                break
        if not found:
            self.st['base_shares'] = 0
            self.st['base_can_use'] = 0
            self.st['base_cost'] = 0.0
        if self.st.get('reconcile_reason') == '持仓查询失败':
            self.st['reconcile_required'] = False
            self.st['reconcile_reason'] = ''
        return True

    def _clear_recovery_guard_if_flat(self):
        has_open_leg = bool(
            self.st.get('short_legs') or self.st.get('long_legs') or
            self.st.get('mom_leg_shares', 0))
        if (not has_open_leg and
                str(self.st.get('reconcile_reason', '')).startswith('进程重启后')):
            self.st['reconcile_required'] = False
            self.st['reconcile_reason'] = ''
            _log('[RECOVERY] 未平腿已处理完毕，解除新开腿保护')

    # ------------------------------------------------------------------
    # 仓位预留与订单生命周期
    # ------------------------------------------------------------------
    def _reserved_sellable_shares(self):
        reserved = self._leg_shares(self.st.get('long_legs', []))
        if self.st.get('mom_state') in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING'):
            reserved += int(self.st.get('mom_leg_shares', 0))
        return reserved

    def _snapshot_account(self):
        """容忍日信号尚未生成，但不把查询失败伪装成零持仓。"""
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        position_ok = getattr(self.conn, 'last_position_query_ok', bool(self.dry_run))
        shares = 0
        can_use = 0
        cost = 0.0
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                shares = int(pos.m_nVolume)
                can_use = int(getattr(pos, 'm_nCanUseVolume', pos.m_nVolume))
                cost = float(pos.m_dOpenPrice)
                break
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        account_ok = getattr(self.conn, 'last_account_query_ok', bool(self.dry_run))
        cash = float(account[0].m_dAvailable) if account else 0.0
        tick = self.ctx.get_full_tick([STOCK_QMT])
        price = float(tick.get(STOCK_QMT, {}).get('lastPrice', 0) or 0)
        if price <= 0:
            signal = self.st.get('daily_signal') or {}
            price = float(signal.get('open_price', 0) or 0)
        return {
            'shares': shares, 'can_use': can_use, 'cash': cash, 'cost': cost,
            'total_asset': shares * price + cash, 'price': price,
            'valid': bool(position_ok and account_ok),
        }

    def _clamp_order_shares(self, planned, price, label):
        planned = int(abs(planned))
        is_sell = (label in self._OPEN_SHORT_LABELS or
                   label in self._CLOSE_LONG_LABELS or
                   'sell' in label.lower())
        is_open_long = label in self._OPEN_LONG_LABELS
        is_close_long = label in self._CLOSE_LONG_LABELS
        can_use = int(self.st.get('base_can_use', 0))
        if not self.dry_run:
            if not self._refresh_position():
                return 0
            can_use = int(self.st.get('base_can_use', 0))
        reserved = self._reserved_sellable_shares()
        if is_sell:
            sellable = can_use if is_close_long else max(0, can_use - reserved)
            return min(planned, sellable)
        if price <= 0:
            price = self._cur_price()
        avail = self._available_cash()
        cash_shares = int(avail / (price * 1.001)) if price > 0 else 0
        allowed = min(planned, cash_shares)
        if is_open_long:
            allowed = min(allowed, max(0, can_use - reserved))
        return allowed

    def _submit_order(self, shares, price, label):
        is_opening = label in self._OPEN_LONG_LABELS or label in self._OPEN_SHORT_LABELS
        if self.st.get('reconcile_reason') == '持仓查询失败':
            self._refresh_position()
        if self.st.get('reconcile_required'):
            reason = str(self.st.get('reconcile_reason', ''))
            unknown_order = ('委托' in reason and
                             ('终态未确认' in reason or '无法查询' in reason))
            if is_opening or unknown_order:
                _log('[ORDER BLOCKED] {}: {}'.format(label, reason))
                return 'SKIP', 0
        if getattr(self.conn, 'order_pending', False):
            _log('[ORDER BUSY] {}: 尚有委托未结束'.format(label))
            return 'SKIP', 0
        actual = self._clamp_order_shares(abs(shares), price, label)
        if actual < cfg.MIN_LOT:
            _log('[{} SKIP] 可用资源不足: planned {} actual {}'.format(label, abs(shares), actual))
            return 'SKIP', 0
        signed = -actual if shares < 0 else actual
        snap = self._snapshot_account()
        if self.dry_run:
            _log('[SIM-ORDER-{}] Y{:.2f} × {} sh'.format(label, price, actual))
            return 'FILLED', signed
        order_id = order_shares(STOCK_QMT, signed, 'COMPETE', price, self.ctx, ACCOUNT)
        if not isinstance(order_id, int) or order_id <= 0:
            _log('[ORDER REJECTED-{}] order_id={}'.format(label, order_id))
            return 'TIMEOUT', 0
        self.st['last_order_id'] = order_id
        self.st['last_order_label'] = label
        return self._wait_for_fill(snap, signed, label, price, signed,
                                   timeout_sec=FILL_TIMEOUT_SEC,
                                   order_id=order_id)

    def _wait_for_fill(self, snap_before, expected_shares_delta,
                       label, trade_price, trade_shares,
                       timeout_sec=FILL_TIMEOUT_SEC, order_id=None):
        if self.dry_run:
            return 'FILLED', expected_shares_delta
        direction = 1 if expected_shares_delta > 0 else -1
        expected = abs(int(expected_shares_delta))
        deadline = _time.monotonic() + max(0.0, timeout_sec)
        last = None

        while True:
            last = self.conn.get_order_snapshot(order_id)
            if last is not None:
                traded = min(expected, int(last.get('traded_volume', 0) or 0))
                if traded >= expected:
                    self.conn.order_pending = False
                    self._refresh_position()
                    return 'FILLED', direction * traded
                if last.get('terminal'):
                    self.conn.order_pending = False
                    self._refresh_position()
                    return ('PARTIAL', direction * traded) if traded else ('TIMEOUT', 0)
            if _time.monotonic() >= deadline:
                break
            _time.sleep(0.25)

        # 无论是否已经部分成交，都撤掉余量，并等待终态，防止迟到成交失控。
        cancel_sent = self.conn.cancel_order(order_id)
        cancel_deadline = _time.monotonic() + 2.0
        while _time.monotonic() < cancel_deadline:
            last = self.conn.get_order_snapshot(order_id) or last
            if last is not None and last.get('terminal'):
                break
            _time.sleep(0.1)
        terminal_confirmed = bool(last is not None and last.get('terminal'))
        self.conn.order_pending = not terminal_confirmed
        if not cancel_sent or not terminal_confirmed:
            self.st['reconcile_required'] = True
            self.st['reconcile_reason'] = '委托 {} 撤单终态未确认'.format(order_id)
            _log('[ORDER RECONCILE-{}] {}'.format(label, self.st['reconcile_reason']))
        if last is None:
            self.st['reconcile_required'] = True
            self.st['reconcile_reason'] = '无法查询委托 {} 的最终状态'.format(order_id)
            _log('[ORDER UNKNOWN-{}] order_id={}'.format(label, order_id))
            return 'TIMEOUT', 0
        traded = min(expected, int(last.get('traded_volume', 0) or 0))
        self._refresh_position()
        if traded >= expected:
            return 'FILLED', direction * traded
        if traded > 0:
            return 'PARTIAL', direction * traded
        return 'TIMEOUT', 0

    @staticmethod
    def _remaining_legs(legs, closed_shares):
        remaining_to_close = max(0, int(closed_shares))
        remaining = []
        for price, shares in legs:
            shares = int(shares)
            used = min(shares, remaining_to_close)
            remaining_to_close -= used
            if shares > used:
                remaining.append((float(price), shares - used))
        return remaining

    # ------------------------------------------------------------------
    # 主状态机安全平腿
    # ------------------------------------------------------------------
    def _do_buyback(self, price, reason=''):
        legs = list(self.st.get('short_legs') or
                    [(self.st.get('sell_fill_price', price), TRADE_LOT_SIZE)])
        shares = self._leg_shares(legs)
        if shares <= 0:
            return 0
        _, delta = self._submit_order(shares, price, 'REV-T buyback({})'.format(reason))
        bought = max(0, delta)
        if bought <= 0:
            self.st['fstate'] = STATE_SOLD
            _log('[Buyback {}-FAIL] 未成交，保留全部未平腿'.format(reason))
            return 0
        self.st['short_legs'] = self._remaining_legs(legs, bought)
        if self.st['short_legs']:
            self.st['fstate'] = STATE_SOLD
            self.st['sell_fill_price'] = self._leg_avg_price(self.st['short_legs'])
            _log('[Buyback PARTIAL] 已买回{}股，剩余{}股'.format(
                bought, self._leg_shares(self.st['short_legs'])))
        else:
            self.st['ladder_sell_target'] = 0.0
            self.st['ladder_sold_count'] = 0
            self.st['fstate'] = STATE_DONE
            self._maybe_resume_trading()
            self._clear_recovery_guard_if_flat()
        self._persist_runtime_state(force=True)
        return bought

    def _force_buyback(self):
        _log('[FORCE buyback trig]')
        legs = list(self.st.get('short_legs', []))
        shares = self._leg_shares(legs)
        if shares <= 0:
            return
        bought = self._do_buyback(self._cur_price(), 'FORCE')
        if bought >= shares:
            self.st['fstate'] = STATE_FORCED
        else:
            self.st['fstate'] = STATE_SOLD

    def _handle_bt_spiking(self, price):
        st = self.st
        if price > st.get('bt_sell_peak_price', price):
            st['bt_sell_peak_price'] = price
        peak = st.get('bt_sell_peak_price', price)
        pullback = (peak - price) / peak if peak > 0 else 0
        if pullback < cfg.PULLBACK_PCT:
            return
        legs = list(st.get('long_legs') or
                    [(st.get('bt_buy_fill_price', price), TRADE_LOT_SIZE)])
        total = self._leg_shares(legs)
        _, delta = self._submit_order(-total, price, 'FWD-T sell')
        sold = max(0, -delta)
        if sold <= 0:
            st['fstate'] = STATE_BT_BOUGHT
            return
        closed = total - self._leg_shares(self._remaining_legs(legs, sold))
        gross = 0.0
        left = closed
        for buy_price, shares in legs:
            qty = min(shares, left)
            gross += (price - buy_price) * qty
            left -= qty
            if left <= 0:
                break
        st['long_legs'] = self._remaining_legs(legs, sold)
        self.total_pnl += gross
        if st['long_legs']:
            st['fstate'] = STATE_BT_BOUGHT
            st['bt_buy_fill_price'] = self._leg_avg_price(st['long_legs'])
        else:
            st['fstate'] = STATE_DONE
            st['ladder_buy_target'] = 0.0
            self.total_t_days += 1
            self._maybe_resume_trading()
            self._clear_recovery_guard_if_flat()
        self._persist_runtime_state(force=True)

    def _do_bt_force_sell(self):
        _log('[FWD-T force sell trig]')
        legs = list(self.st.get('long_legs', []))
        shares = self._leg_shares(legs)
        if shares <= 0:
            return
        _, delta = self._submit_order(-shares, self._cur_price(), 'FWD-T force sell')
        sold = max(0, -delta)
        self.st['long_legs'] = self._remaining_legs(legs, sold)
        if self.st['long_legs']:
            self.st['fstate'] = STATE_BT_BOUGHT
            _log('[FWD-T force sell FAIL/PARTIAL] 剩余{}股'.format(
                self._leg_shares(self.st['long_legs'])))
        else:
            self.st['fstate'] = STATE_FORCED
            self.st['ladder_buy_target'] = 0.0
            self._clear_recovery_guard_if_flat()
        self._persist_runtime_state(force=True)

    # ------------------------------------------------------------------
    # MOM 安全平腿（默认关闭，开启时仍保证部分成交可恢复）
    # ------------------------------------------------------------------
    def _mom_handle_dipping(self, price):
        st = self.st
        if price < st['mom_dip']:
            st['mom_dip'] = price
        dip = st['mom_dip'] or price
        if dip <= 0 or (price - dip) / dip < cfg.BOUNCE_PCT:
            return
        shares = int(st.get('mom_leg_shares', 0))
        _, delta = self._submit_order(shares, price, 'MOM buyback')
        bought = max(0, delta)
        if bought <= 0:
            return
        self.total_pnl += (st['mom_sell_price'] - price) * bought
        remaining = max(0, shares - bought)
        st['mom_leg_shares'] = remaining
        st['mom_state'] = 'MOM_SOLD' if remaining else 'MOM_IDLE'
        if not remaining:
            st['mom_sell_price'] = 0.0
            st['mom_dip'] = 0.0
            self.total_t_days += 1
            self._clear_recovery_guard_if_flat()
        self._persist_runtime_state(force=True)

    def _mom_handle_sold(self, price):
        st = self.st
        sell_price = float(st.get('mom_sell_price', 0) or 0)
        if sell_price <= 0:
            st['reconcile_required'] = True
            st['reconcile_reason'] = 'MOM卖出腿缺少成交价'
            return
        if (MOM_EMERGENCY_BUYBACK_ENABLED and
                price >= sell_price * (1.0 + cfg.EMERGENCY_BUYBACK_PCT)):
            shares = int(st.get('mom_leg_shares', 0))
            _, delta = self._submit_order(shares, price, 'MOM emrg buyback')
            bought = max(0, delta)
            if bought <= 0:
                return
            self.total_pnl += (sell_price - price) * bought
            remaining = max(0, shares - bought)
            st['mom_leg_shares'] = remaining
            st['mom_state'] = 'MOM_SOLD' if remaining else 'MOM_IDLE'
            if not remaining:
                st['mom_sell_price'] = 0.0
                self.total_t_days += 1
                self._clear_recovery_guard_if_flat()
            self._persist_runtime_state(force=True)
            return
        if price <= sell_price * (1.0 - MOM_SHORT_BUYBACK_PCT):
            st['mom_state'] = 'MOM_DIPPING'
            st['mom_dip'] = price

    def _mom_handle_bt_bought(self, price):
        st = self.st
        buy_price = float(st.get('mom_buy_price', 0) or 0)
        if buy_price <= 0:
            st['reconcile_required'] = True
            st['reconcile_reason'] = 'MOM买入腿缺少成交价'
            return
        if price <= buy_price * (1.0 - cfg.STOP_LOSS_PCT):
            shares = int(st.get('mom_leg_shares', 0))
            _, delta = self._submit_order(-shares, price, 'MOM stop-loss')
            sold = max(0, -delta)
            if sold <= 0:
                return
            self.total_pnl += (price - buy_price) * sold
            remaining = max(0, shares - sold)
            st['mom_leg_shares'] = remaining
            st['mom_state'] = 'MOM_BT_BOUGHT' if remaining else 'MOM_IDLE'
            if not remaining:
                st['mom_buy_price'] = 0.0
                self.total_t_days += 1
                self._clear_recovery_guard_if_flat()
            self._persist_runtime_state(force=True)
            return
        if price >= buy_price * (1.0 + MOM_LONG_SELLBACK_PCT):
            st['mom_state'] = 'MOM_BT_SPIKING'
            st['mom_peak'] = price

    def _mom_handle_bt_spiking(self, price):
        st = self.st
        if price > st['mom_peak']:
            st['mom_peak'] = price
        peak = st['mom_peak']
        if peak <= 0 or (peak - price) / peak < cfg.PULLBACK_PCT:
            return
        shares = int(st.get('mom_leg_shares', 0))
        _, delta = self._submit_order(-shares, price, 'MOM sellback')
        sold = max(0, -delta)
        if sold <= 0:
            return
        self.total_pnl += (price - st['mom_buy_price']) * sold
        remaining = max(0, shares - sold)
        st['mom_leg_shares'] = remaining
        st['mom_state'] = 'MOM_BT_BOUGHT' if remaining else 'MOM_IDLE'
        if not remaining:
            st['mom_buy_price'] = 0.0
            st['mom_peak'] = 0.0
            self.total_t_days += 1
            self._clear_recovery_guard_if_flat()
        self._persist_runtime_state(force=True)

    def _mom_force_close(self, price):
        st = self.st
        state = st.get('mom_state')
        shares = int(st.get('mom_leg_shares', 0))
        if state in ('MOM_SOLD', 'MOM_DIPPING') and shares > 0:
            _, delta = self._submit_order(shares, price, 'MOM force buyback')
            remaining = max(0, shares - max(0, delta))
            st['mom_leg_shares'] = remaining
            st['mom_state'] = 'MOM_SOLD' if remaining else 'MOM_IDLE'
        elif state in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING') and shares > 0:
            _, delta = self._submit_order(-shares, price, 'MOM force sell')
            remaining = max(0, shares - max(0, -delta))
            st['mom_leg_shares'] = remaining
            st['mom_state'] = 'MOM_BT_BOUGHT' if remaining else 'MOM_IDLE'
        elif state in ('MOM_SPIKING', 'MOM_BT_DIPPING'):
            st['mom_state'] = 'MOM_IDLE'
        self._clear_recovery_guard_if_flat()
        self._persist_runtime_state(force=True)

    # ------------------------------------------------------------------
    # 锁仓和主循环风控
    # ------------------------------------------------------------------
    def _assess_strength(self, price, now_ts):
        st = self.st
        open_price = st.get('daily_signal', {}).get('open_price', 0)
        if open_price <= 0:
            return
        st['price_history'].append((now_ts, price))
        cutoff = now_ts - cfg.LOCK_LOOKBACK_SEC
        while st['price_history'] and st['price_history'][0][0] < cutoff:
            st['price_history'].popleft()
        history = list(st['price_history'])
        if len(history) < 10:
            return
        prices = [p for _, p in history]
        first, current, high = prices[0], prices[-1], max(prices)
        should_lock = (
            current > open_price * (1.0 + cfg.LOCK_PRICE_RATIO) and
            first > 0 and (current - first) / first > cfg.LOCK_MOMENTUM_PCT and
            high > 0 and (high - current) / high < cfg.LOCK_DRAWDOWN_PCT)
        if should_lock:
            if not st.get('locked'):
                st['locked'] = True
                st['lock_since'] = cfg.now_hms()
                _log('[LOCK] 强势上涨，反T暂停')
            st['lock_cooldown_until'] = max(
                st.get('lock_cooldown_until', 0), now_ts + cfg.LOCK_COOLDOWN_SEC)
        elif st.get('locked') and now_ts >= st.get('lock_cooldown_until', 0):
            st['locked'] = False
            st['lock_reason'] = ''
            st['lock_since'] = ''
            _log('[UNLOCK] 强势保护冷却结束')

    def _check_short_stop_loss(self, price):
        if self.st.get('fstate') not in (STATE_SOLD, STATE_DIPPING):
            return
        legs = self.st.get('short_legs', [])
        if not legs:
            return
        unrealized = self._short_gross(legs, price)
        notional = sum(p * shares for p, shares in legs)
        self.st['day_pnl'] = unrealized
        now_ts = _time.time()
        already_hit = self.st.get('stop_loss_hit', False)
        if already_hit and now_ts < self.st.get('stop_loss_retry_at', 0):
            return
        if already_hit or unrealized < -notional * cfg.STOP_LOSS_PCT:
            self.st['stop_loss_hit'] = True
            self.st['stop_loss_retry_at'] = now_ts + 5.0
            _log('[STOP-LOSS] 反T浮亏 Y{:,.0f}，执行买回'.format(unrealized))
            self._force_buyback()

    def run(self):
        set_global_conn(self.conn, self.dry_run)
        if not self.conn.connect_data():
            _log('[ERROR] market data connect failed')
            return
        if not self.dry_run and not self.conn.connect_trade():
            _log('[ERROR] trade connect failed')
            self.conn.disconnect()
            return
        self._init_state()
        _log('[START] {} v31 {} {}'.format(
            STOCK_NAME, 'LIVE' if not self.dry_run else 'SIGNAL', STOCK_QMT))
        try:
            self._daily_init()
            if self.st.get('daily_signal'):
                self._print_daily_brief(self.st['daily_signal'])
            while self._running:
                now = cfg.now_hms()
                now_ts = _time.time()
                today = datetime.now().strftime('%Y%m%d')
                if not cfg.is_market_open(now):
                    if self.st.get('trade_date') != today:
                        self._daily_init()
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        _log('[WAIT/CLOSE {}]'.format(now))
                    self._persist_runtime_state()
                    _time.sleep(10)
                    continue

                # 交易时段初始化失败时持续重试；开盘首个有效周期强制刷新今开。
                if not self.st.get('initialized'):
                    if now_ts - self._last_init_retry >= 10:
                        self._last_init_retry = now_ts
                        self._daily_init()
                    _time.sleep(1)
                    continue
                if self._opening_refresh_date != today:
                    self._daily_init(force=True)
                    self._opening_refresh_date = today
                    if self.st.get('daily_signal'):
                        self._print_daily_brief(self.st['daily_signal'])

                tick = self.ctx.get_full_tick([STOCK_QMT])
                tick_data = tick.get(STOCK_QMT, {})
                price = float(tick_data.get('lastPrice', 0) or 0)
                if price <= 0:
                    _time.sleep(1)
                    continue
                self._maybe_print_market_status(tick_data, now_ts)

                fstate = self.st.get('fstate', STATE_IDLE)
                if MOM_ENABLED:
                    self._mom_tick(price, now_ts)
                if fstate == STATE_IDLE:
                    self._assess_strength(price, now_ts)
                    self._handle_idle(price)
                elif fstate == STATE_SPIKING:
                    self._handle_spiking(price)
                elif fstate == STATE_SOLD:
                    self._handle_sold(price)
                elif fstate == STATE_DIPPING:
                    self._handle_dipping(price)
                elif fstate == STATE_BT_DIPPING:
                    self._handle_bt_dipping(price)
                elif fstate == STATE_BT_BOUGHT:
                    self._handle_bt_bought(price)
                elif fstate == STATE_BT_SPIKING:
                    self._handle_bt_spiking(price)
                elif fstate in (STATE_DONE, STATE_FORCED) and now < cfg.FORCE_CLOSE_TIME:
                    self._maybe_resume_trading()

                if self.st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
                    self.st['sell_elapsed_bars'] = self.st.get('sell_elapsed_bars', 0) + 1
                self._check_short_stop_loss(price)

                # v31 对所有已开日内腿强制收尾，不受旧版关闭开关影响。
                if now >= cfg.FORCE_CLOSE_TIME:
                    state = self.st.get('fstate')
                    if state in (STATE_SOLD, STATE_DIPPING):
                        self._force_buyback()
                    elif state in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
                        self._do_bt_force_sell()
                    elif state in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE):
                        self.st['fstate'] = STATE_DONE
                    if MOM_ENABLED:
                        self._mom_force_close(price)

                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    self._heartbeat(price)
                self._persist_runtime_state()
                _time.sleep(0.5)
        except KeyboardInterrupt:
            _log('Interrupted by user')
        except Exception as e:
            _log('[ERROR] {}'.format(e))
            _traceback.print_exc()
        finally:
            self._persist_runtime_state(force=True)
            self.conn.disconnect()
            if self.st.get('short_legs') or self.st.get('long_legs') or self.st.get('mom_leg_shares', 0):
                _log('[WARN] 仍有未平腿，请人工核对账户！')
            _log('[STOP] {} v31 cum {} trades gross~Y{:,.0f}'.format(
                STOCK_NAME, self.total_t_days, self.total_pnl))
            logger = get_logger()
            if logger is not None:
                logger.close()


def run_backtest_mode(start='20250801', end='20260806'):
    """拒绝把旧 v10 引擎结果伪装成 v31 回测结果。"""
    raise SystemExit(
        'v31 包含逐笔订单与 T+1 底仓预留，现有 backtest_v10_xtdata 不支持；'
        '请使用 signal 模式或为 v31 建立专用事件回放器。')


def main():
    parser = argparse.ArgumentParser(description='MiniQMT day trading v31')
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250801')
    parser.add_argument('--end', default='20260806')
    args = parser.parse_args()
    if args.mode == 'backtest':
        run_backtest_mode(args.start, args.end)
        return
    set_logger(FileLogger(STOCK_CODE, version='v31'))
    dry_run = args.mode == 'signal'
    if args.mode == 'live':
        print('\n!!! LIVE TRADING CONFIRMATION !!!')
        print('Target: {}({}) Account: {}'.format(STOCK_NAME, STOCK_CODE, ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            get_logger().close()
            return
    StrategyRunner(dry_run=dry_run).run()


if __name__ == '__main__':
    main()
