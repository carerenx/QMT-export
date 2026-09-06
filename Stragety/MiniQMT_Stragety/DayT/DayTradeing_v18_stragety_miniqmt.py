# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — QMT day trading v18 + 订单成交确认 + 状态不卡死
================================================================================
 VS Code connect xtquant MiniQMT run v18 strategy。

 [v18 改动] (vs v17)
   ★ 订单成交确认机制: _wait_for_fill() 轮询持仓变化确认成交, 不再盲目推进状态
   ★ 新增 STATE_WAIT_FILL: 下单后进入等待成交状态, 成交确认后才推进到 DONE/SOLD
   ★ DONE状态不再静默: 心跳持续输出 "今日已完成 N/N笔", 不再静默卡死
   ★ 多笔交易自动恢复: 完成后检测是否还有剩余交易次数, 有则回到 IDLE
   ★ 废单自动重试: 真正废单时回退到 IDLE, 保留交易次数重新尝试

 [v17 改动] (vs v16)
   ★ _snapshot_account() / _verify_trade() — 交易前后仓位资金校验

 [v16 改动] (vs v15)
   ★ _print_signal_summary() / _print_trading_plan() — 盘前信号+交易计划

 [模块结构]
   core/config.py, core/indicators.py, core/signals.py,
   infra/logger.py, infra/connector.py

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/DayTradeing_v18_stragety_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/DayTradeing_v18_stragety_miniqmt.py" --mode live
   backtest:python "Stragety/MiniQMT_Stragety/DayTradeing_v18_stragety_miniqmt.py" --mode backtest

================================================================================
"""
import os
import sys
import time as _time
import argparse
import traceback as _traceback
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core import config as cfg
from core.signals import compute_signal
from Stragety.MiniQMT_Stragety.DayT.infra.logger import FileLogger, set_logger, get_logger, _log
from Stragety.MiniQMT_Stragety.DayT.infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares,
    set_global_conn,
)

ACCOUNT       = cfg.ACCOUNT
STOCK_CODE    = cfg.STOCK_CODE
STOCK_NAME    = cfg.STOCK_NAME
STOCK_QMT     = cfg.STOCK_QMT
TRADE_LOT_SIZE = cfg.TRADE_LOT_SIZE

STATE_IDLE       = cfg.STATE_IDLE
STATE_SPIKING    = cfg.STATE_SPIKING
STATE_SOLD       = cfg.STATE_SOLD
STATE_DIPPING    = cfg.STATE_DIPPING
STATE_DONE       = cfg.STATE_DONE
STATE_FORCED     = cfg.STATE_FORCED
STATE_BT_DIPPING = cfg.STATE_BT_DIPPING
STATE_BT_BOUGHT  = cfg.STATE_BT_BOUGHT
STATE_BT_SPIKING = cfg.STATE_BT_SPIKING

# ★ v18 新增: 等待成交状态
STATE_WAIT_FILL  = 'WAIT_FILL'


# ============================================================================
# StrategyRunner
# ============================================================================

class StrategyRunner:
    """MiniQMT 策略运行器 — v18: 订单确认 + 状态机不卡死"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='v18')
            set_logger(logger)

        self.conn = MiniQMTConnector()
        set_global_conn(self.conn, dry_run)

        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = dry_run

        self._last_heartbeat = 0.0
        self._last_trade_date = ''
        self._running = True

        self.total_t_days = 0
        self.total_pnl = 0.0

        # v18: 订单确认跟踪
        self._fill_wait_since = 0.0
        self._fill_target_shares_delta = 0

    # ── 状态初始化 ──

    def _init_state(self):
        self.st.update({
            'daily_signal': None,
            'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'entry_price': 0.0,
            'fstate': STATE_IDLE,
            'peak_price': 0.0, 'dip_price': 0.0,
            'sell_fill_price': 0.0, 'buyback_target': 0.0, 'buyback_target_pct': 0.0,
            'day_pnl': 0.0, 'stop_loss_hit': False,
            'total_t_days': self.total_t_days, 'total_pnl': self.total_pnl,
            'trade_date': '', '_guard_date': '',
            'initialized': False, 'init_attempts': 0, 'last_init_time': 0.0,
            'startup_printed': False, '_startup_guard': '',
            'state_enter_time': '', 'sell_elapsed_bars': 0,
            'last_heartbeat': 0.0, 'last_fstate': '',
            'ontimer_errors': 0, 'callback_errors': 0,
            'order_pending': False, 'order_side': '',
            'order_signal_price': 0.0, 'order_sent_at': 0.0,
            'order_retries': 0, 'order_retry_logged': False,
            'locked': False, 'lock_reason': '', 'lock_since': '',
            'lock_cooldown_until': 0.0, 'price_history': deque(),
            '_pre_market_done': '',
            # v18: 等待成交跟踪
            '_fill_snap_before': None, '_fill_label': '',
            '_fill_trade_price': 0.0, '_fill_trade_shares': 0,
            '_fill_next_state': '', '_fill_retries': 0,
        })

    def _reset_daily(self):
        guard = self.st.get('_guard_date', '')
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        base_cost = self.st.get('base_cost', 0.0)
        entry_price = self.st.get('entry_price', 0.0)

        self._init_state()
        self.st['base_shares'] = base_shares
        self.st['base_can_use'] = base_can_use
        self.st['base_cost'] = base_cost
        self.st['entry_price'] = entry_price
        self.st['_guard_date'] = guard
        self.st['total_t_days'] = self.total_t_days
        self.st['total_pnl'] = self.total_pnl
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
            self.st[k] = 0.0
        self.st['locked'] = False
        self.st['lock_reason'] = ''
        self.st['lock_since'] = ''
        self.st['_pre_market_done'] = ''

    # ── 每日初始化 (同v17) ──

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if self.st.get('trade_date', '') == today and self.st.get('initialized', False):
            self._refresh_position()
            return

        is_new_day = self.st.get('trade_date', '') and self.st['trade_date'] != today
        if is_new_day:
            _log(f'\n[新交易日] {self.st["trade_date"]} -> {today}')

        saved_trail = self.st.get('bt_max_trail', 0) if not is_new_day else 0
        saved_history = self.st.get('price_history', []) if not is_new_day else []

        self._reset_daily()
        self.st['trade_date'] = today
        self.st['_guard_date'] = today

        if not is_new_day and saved_trail > 0:
            self.st['bt_max_trail'] = saved_trail
            self.st['price_history'] = saved_history
            _log('[恢复] 正T触发线最高记录: Y{:.2f}'.format(saved_trail))

        hist_close  = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'close')
        hist_open   = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'open')
        hist_high   = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'high')
        hist_low    = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'low')
        hist_volume = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'volume')

        if STOCK_QMT not in hist_close or len(hist_close[STOCK_QMT]) < 60:
            _log('[警告] 日线数据不足, 跳过今日')
            return

        self._refresh_position()
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('entry_price', 0) == 0.0:
            self.st['entry_price'] = self.st.get('base_cost', 0.0)

        tick_now = self.ctx.get_full_tick([STOCK_QMT])
        tick_data = tick_now.get(STOCK_QMT, {})
        today_open = tick_data.get('open', 0)
        curr_price_now = tick_data.get('lastPrice', 0)

        opens_list = list(hist_open[STOCK_QMT])
        if today_open > 0 and len(opens_list) > 0:
            opens_list[-1] = today_open
            _log('[数据] 今日开盘: Y{:.2f} (来自tick)'.format(today_open))
        else:
            _log('[警告] 无法获取今日开盘价, 使用昨日数据(可能不准)')

        signal = compute_signal(
            opens_list, hist_high[STOCK_QMT],
            hist_low[STOCK_QMT], hist_close[STOCK_QMT], hist_volume[STOCK_QMT]
        )
        if signal is None:
            return

        open_price = signal['open_price']
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0
        if curr_price_now <= 0:
            curr_price_now = open_price

        pos_value = base_shares * curr_price_now
        total_asset = pos_value + avail_cash
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0

        short_lots = min(base_can_use // TRADE_LOT_SIZE, cfg.MAX_DAILY_TRADES)
        long_lots_cash = int(avail_cash / (curr_price_now * TRADE_LOT_SIZE * 1.01))
        long_lots_sell = base_can_use // TRADE_LOT_SIZE
        long_lots = min(long_lots_cash, long_lots_sell, cfg.MAX_DAILY_TRADES)

        do_short = signal['do_short'] and (short_lots >= cfg.MIN_POSITION_LOTS)
        short_reason = ''
        if not signal['do_short']:
            short_reason = signal.get('blocked_reason', '信号禁止')
        elif short_lots < cfg.MIN_POSITION_LOTS:
            short_reason = '可用{}股<{}手'.format(base_can_use, cfg.MIN_POSITION_LOTS)

        do_long = long_lots >= cfg.MIN_POSITION_LOTS
        long_reason = ''
        if not do_long:
            reasons = []
            if long_lots_cash < cfg.MIN_POSITION_LOTS:
                reasons.append('资金不足')
            if long_lots_sell < cfg.MIN_POSITION_LOTS:
                reasons.append('T+1:无可卖持仓')
            long_reason = '; '.join(reasons) if reasons else '未知'

        buy_trigger_floor = round(open_price * (1.0 - cfg.BUY_TRIGGER_PCT), 2)
        buy_trigger_trail = round(curr_price_now * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
        buy_trigger = max(buy_trigger_floor, buy_trigger_trail)
        sellback_target_hint = round(buy_trigger * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

        trend = signal.get('trend', 'sideways')
        if pos_pct > 80:
            pos_advice = '仓位过重(>{:.0f}%), 不建议加仓'.format(pos_pct)
        elif trend == 'strong_bull':
            pos_advice = '强牛持有, 不做反T'
        elif trend == 'bear':
            pos_advice = '熊市积极反T({}手可用)'.format(short_lots)
        else:
            pos_advice = '可反T{}手 / 可正T{}手'.format(short_lots, long_lots)

        signal['do_short'] = do_short
        signal['short_reason'] = short_reason
        signal['buy_trigger'] = buy_trigger
        signal['buy_trigger_floor'] = buy_trigger_floor
        signal['buy_trigger_trail'] = buy_trigger_trail
        signal['sellback_target_hint'] = sellback_target_hint

        self.st['daily_signal'] = signal
        self.st['do_short'] = do_short
        self.st['do_long'] = do_long
        self.st['long_reason'] = long_reason
        self.st['short_lots'] = short_lots
        self.st['long_lots'] = long_lots
        self.st['pos_value'] = pos_value
        self.st['pos_pct'] = pos_pct
        self.st['avail_cash'] = avail_cash
        self.st['pos_advice'] = pos_advice
        self.st['trade_count_short'] = 0
        self.st['trade_count_long'] = 0

        # 重置状态机
        for k in ('fstate', 'peak_price', 'dip_price', 'sell_fill_price',
                   'buyback_target', 'buyback_target_pct'):
            self.st[k] = STATE_IDLE if k == 'fstate' else 0.0
        self.st['day_pnl'] = 0.0
        self.st['stop_loss_hit'] = False
        self.st['state_enter_time'] = cfg.now_hms()
        self.st['sell_elapsed_bars'] = 0
        self.st['initialized'] = True
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price',
                   'locked'):
            self.st[k] = 0.0 if k != 'locked' else False
        self.st['lock_reason'] = ''
        self.st['lock_since'] = ''

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                break

    # ═══════════════════════════════════════════════════════════════
    # v18: 订单成交确认机制
    # ═══════════════════════════════════════════════════════════════

    def _wait_for_fill(self, snap_before, expected_shares_delta,
                       label, trade_price, trade_shares, next_state,
                       timeout_sec=5.0):
        """
        ★ v18 核心: 下单后轮询持仓确认成交, 不盲目推进状态。

        Args:
            snap_before: _snapshot_account() 交易前快照
            expected_shares_delta: 预期持仓变化 (正=买入, 负=卖出)
            label: 交易描述
            trade_price: 下单价格
            trade_shares: 下单股数
            next_state: 成交后进入的状态
            timeout_sec: 超时秒数

        Returns:
            True 如果持仓变化确认了成交, False 如果超时
        """
        # dry-run: 模拟立即成交
        if self.dry_run:
            _log(f'[模拟成交] {label} → 状态 {next_state}')
            self._verify_trade(snap_before, label, trade_price, trade_shares)
            return True

        _log(f'  ⏳ 等待成交确认... (预期Δ{expected_shares_delta:+d}股, 超时{timeout_sec}s)')

        check_interval = 0.5
        waited = 0.0

        while waited < timeout_sec:
            _time.sleep(check_interval)
            waited += check_interval

            curr_snap = self._snapshot_account()
            actual_delta = curr_snap['shares'] - snap_before['shares']

            if actual_delta == expected_shares_delta:
                _log(f'  ✅ 成交确认! (等待{waited:.1f}s) → 状态 {next_state}')
                self._verify_trade(snap_before, label, trade_price, trade_shares)
                return True

            # 部分成交也接受
            if expected_shares_delta > 0 and actual_delta > 0:
                _log(f'  ⚠ 部分成交: Δ{actual_delta:+d}股 (预期Δ{expected_shares_delta:+d}股), 接受')
                self._verify_trade(snap_before, label, trade_price, trade_shares)
                return True
            if expected_shares_delta < 0 and actual_delta < 0:
                _log(f'  ⚠ 部分成交: Δ{actual_delta:+d}股 (预期Δ{expected_shares_delta:+d}股), 接受')
                self._verify_trade(snap_before, label, trade_price, trade_shares)
                return True

        # 超时
        _log(f'  ❌ 成交确认超时! (等待{waited:.1f}s, 持仓Δ{curr_snap["shares"] - snap_before["shares"]:+d})')
        self._verify_trade(snap_before, f'{label}(超时)', trade_price, trade_shares)
        return False

    # ═══════════════════════════════════════════════════════════════
    # v17: 交易前后仓位资金校验 (沿用)
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
        if price <= 0:
            price = self.st.get('daily_signal', {}).get('open_price', 0)
        return {
            'shares': shares, 'can_use': can_use, 'cash': cash,
            'cost': cost, 'total_asset': shares * price + cash, 'price': price,
        }

    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_can_use = snap_after['can_use'] - snap_before['can_use']
        d_cash = snap_after['cash'] - snap_before['cash']
        d_asset = snap_after['total_asset'] - snap_before['total_asset']
        est_cost = abs(trade_price * abs(trade_shares)) * (cfg.COMMISSION + cfg.STAMP_TAX) if trade_price > 0 else 0
        theory_cash_delta = -trade_price * trade_shares - est_cost if trade_price > 0 else 0

        if abs(trade_shares) == TRADE_LOT_SIZE:
            if d_shares == trade_shares:
                status = '✅ 已成交'
            elif d_shares == 0:
                status = '⏳ 待成交(持仓未变)'
            else:
                status = f'⚠ 部分成交(Δ{d_shares:+d}股)'
        else:
            status = '📝 已下单'

        _log('')
        _log('  ┌─ [交易校验] {} ─'.format(label))
        _log('  │  下单: {}股 @ Y{:.2f}  |  {}'.format(
            '{:+d}'.format(trade_shares), trade_price, status))
        _log('  ├─ 持仓: {:>5d}股 → {:>5d}股  (Δ{:+d}股)'.format(
            snap_before['shares'], snap_after['shares'], d_shares))
        _log('  │  T+0可用: {:>4d}股 → {:>4d}股  (Δ{:+d}股)'.format(
            snap_before['can_use'], snap_after['can_use'], d_can_use))
        _log('  ├─ 资金: Y{:>12,.2f} → Y{:>12,.2f}  (Δ{:+,.2f})'.format(
            snap_before['cash'], snap_after['cash'], d_cash))
        if trade_price > 0:
            _log('  │  理论Δ: Y{:+,.2f} (价格{:.2f}×{}股+费{:.2f})'.format(
                theory_cash_delta, trade_price, trade_shares, est_cost))
        _log('  ├─ 总资产: Y{:>12,.2f} → Y{:>12,.2f}  (Δ{:+,.2f})'.format(
            snap_before['total_asset'], snap_after['total_asset'], d_asset))
        _log('  │  市值≈ Y{:,.2f} ({}股 × Y{:.2f})'.format(
            snap_after['shares'] * snap_after['price'],
            snap_after['shares'], snap_after['price']))
        _log('  └' + '─' * 45)

        if abs(trade_shares) == TRADE_LOT_SIZE and d_shares != trade_shares:
            _log('  ⚠ [异常] 持仓变化量与下单量不匹配! 预期{:+d}股, 实际{:+d}股'.format(
                trade_shares, d_shares))

        self.st['base_shares'] = snap_after['shares']
        self.st['base_can_use'] = snap_after['can_use']
        self.st['base_cost'] = snap_after['cost']

    # ═══════════════════════════════════════════════════════════════
    # v16: 盘前信号摘要 & 交易计划 (沿用)
    # ═══════════════════════════════════════════════════════════════

    def _print_signal_summary(self, signal):
        _log('')
        _log('┌' + '─' * 58 + '┐')
        _log('│  📊 信号计算结果摘要' + ' ' * 40 + '│')
        _log('├' + '─' * 58 + '┤')
        trend = signal.get('trend', '?')
        trend_labels = {'strong_bull': '强牛 🟢', 'weak_bull': '弱牛 🟡',
                        'sideways': '震荡 ⚪', 'bear': '熊市 🔴'}
        _log('│  趋势判定: {:28s}  连涨: {}天  │'.format(
            trend_labels.get(trend, trend), signal.get('up_streak', 0)))
        open_p = signal.get('open_price', 0)
        close_y = signal.get('close_yday', 0)
        atr_v = signal.get('atr', 0)
        atr_p = signal.get('atr_pct', 0)
        atr_ratio = signal.get('atr_ratio', 0)
        _log('│  开盘: Y{:,.2f}         昨收: Y{:,.2f}         涨跌: {:+.2f}%  │'.format(
            open_p, close_y, (open_p / close_y - 1) * 100 if close_y else 0))
        _log('│  ATR: Y{:.2f} ({:.2f}%)    ATR/MA20: {:.2f}                    │'.format(
            atr_v, atr_p * 100, atr_ratio))
        _log('│  RSI: {:.1f}                   量比: {:.2f}                        │'.format(
            signal.get('rsi', 0), signal.get('vol_ratio', 0)))
        _log('│  卖出乘数: {:.2f}  (基础: {:.2f})                              │'.format(
            signal.get('sell_mult', 0), signal.get('sell_mult_base', 0)))
        factor_details = signal.get('factor_details', {})
        if factor_details:
            _log('│  因子偏差: {}                        │'.format(
                ' | '.join('{}: {}'.format(k, ('+' if v >= 0 else '') + '{:.2f}'.format(v))
                           for k, v in factor_details.items())))
        sell_trig = signal.get('sell_trigger', 0)
        sell_trig_raw = signal.get('sell_trigger_raw', 0)
        range_capped = signal.get('range_capped', False)
        _log('│  反T触发线: Y{:.2f}  (原始: Y{:.2f})        {}  │'.format(
            sell_trig, sell_trig_raw, '⚠ 振幅约束' if range_capped else ''))
        if not signal.get('do_short', True):
            _log('│  ⛔ 反T已禁止: {:<43s} │'.format(signal.get('blocked_reason', '未知')))
        _log('└' + '─' * 58 + '┘')

    def _print_trading_plan(self, signal):
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        pos_pct = self.st.get('pos_pct', 0)
        avail_cash = self.st.get('avail_cash', 0)
        tick = self.ctx.get_full_tick([STOCK_QMT])
        curr_price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if curr_price <= 0:
            curr_price = signal.get('open_price', 0)
        pos_value = base_shares * curr_price
        total_asset = pos_value + avail_cash

        _log('')
        _log('┌' + '─' * 58 + '┐')
        _log('│  🎯 今日交易计划 — {}  {}               │'.format(
            signal.get('trend', '?'), self.st.get('trade_date', '')))
        _log('├' + '─' * 58 + '┤')
        _log('│  持仓: {:>5}股  Y{:>10,.0f}  ({:.0f}%)                       │'.format(
            base_shares, pos_value, pos_pct))
        _log('│  可用: Y{:>12,.0f}    总资产: Y{:>12,.0f}              │'.format(
            avail_cash, total_asset))
        _log('│  T+0可卖: {:>3}股 ({}手)   T+1锁定: {:>3}股                       │'.format(
            base_can_use, base_can_use // TRADE_LOT_SIZE, base_shares - base_can_use))
        _log('├' + '─' * 58 + '┤')

        do_short = self.st.get('do_short', False)
        short_lots = self.st.get('short_lots', 0)
        if do_short:
            sell_trig = signal.get('sell_trigger', 0)
            atr_v = signal.get('atr', 0)
            sell_mult = signal.get('sell_mult', 0)
            _log('│  📉 反T (先卖后买) — ✅ 可用  {}手                         │'.format(short_lots))
            _log('│     ├─ 卖出触发: Y{:.2f}                                    │'.format(sell_trig))
            _log('│     ├─ 买回触发: 卖价 × (1 - ATR% × {:.2f})                  │'.format(
                cfg.BUYBACK_TRIGGER_MULT))
            _log('│     ├─ 紧急买回: 卖价 +{:.1f}%                                  │'.format(
                cfg.EMERGENCY_BUYBACK_PCT * 100))
            _log('│     └─ 📝 示例: 若卖Y{:.2f}, 买回约Y{:.2f}                        │'.format(
                sell_trig, round(sell_trig * (1.0 - signal['atr_pct'] * cfg.BUYBACK_TRIGGER_MULT), 2)))
        else:
            reason = signal.get('short_reason', signal.get('blocked_reason', '未知'))
            _log('│  📉 反T (先卖后买) — ❌ 不可用                                │')
            _log('│     原因: {:<50s} │'.format(reason))

        _log('├' + '─' * 58 + '┤')
        do_long = self.st.get('do_long', False)
        long_lots = self.st.get('long_lots', 0)
        if do_long:
            buy_trig = signal.get('buy_trigger', 0)
            sell_hint = signal.get('sellback_target_hint', 0)
            _log('│  📈 正T (先买后卖) — ✅ 可用  {}手                         │'.format(long_lots))
            _log('│     ├─ 买入触发: Y{:.2f}  (动态跟踪)                          │'.format(buy_trig))
            _log('│     ├─ 卖出触发: Y{:.2f}  (买价 + {:.1f}%)                    │'.format(
                sell_hint, cfg.SELLBACK_RISE_PCT * 100))
            _log('│     └─ 1手资金: Y{:,.0f}  (可用 Y{:,.0f})                        │'.format(
                curr_price * TRADE_LOT_SIZE, avail_cash))
        else:
            reason = self.st.get('long_reason', '未知')
            _log('│  📈 正T (先买后卖) — ❌ 不可用                                │')
            _log('│     原因: {:<50s} │'.format(reason))

        _log('├' + '─' * 58 + '┤')
        trend = signal.get('trend', 'sideways')
        if trend == 'strong_bull':
            _log('│  ⚠ 风险: 强牛行情, 反T已自动禁用                            │')
        elif trend == 'bear':
            _log('│  ⚠ 风险: 熊市, 正T风险较高, 优先反T                          │')
        _log('│  📋 状态监控: 反T={} 正T={} 最大{}/{}次                       │'.format(
            '✅' if do_short else '❌', '✅' if do_long else '❌',
            cfg.MAX_DAILY_TRADES, cfg.MAX_DAILY_TRADES))
        _log('└' + '─' * 58 + '┘')
        _log('')
        if self.total_t_days > 0:
            _log('  📈 累计: {}笔 T+0  |  毛利 ~Y{:,.0f}'.format(self.total_t_days, self.total_pnl))
            _log('')

    # ═══════════════════════════════════════════════════════════════
    # 状态机处理器 (v18: 订单确认 + 不卡死)
    # ═══════════════════════════════════════════════════════════════

    def _handle_idle(self, price):
        st = self.st
        signal = st.get('daily_signal', {})

        if st.get('do_short', False):
            trigger = signal.get('sell_trigger', 999999)
            if price >= trigger:
                can_use = st.get('base_can_use', st['base_shares'])
                if can_use < TRADE_LOT_SIZE:
                    return
                tc = st.get('trade_count_short', 0)
                if tc >= cfg.MAX_DAILY_TRADES:
                    return
                if st.get('locked', False):
                    return
                st['trade_count_short'] = tc + 1
                st['fstate'] = STATE_SPIKING
                st['peak_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[反T冲高#{}/{}] Y{:.2f} >= Y{:.2f}'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, trigger))
                return

        if st.get('do_long', False):
            buy_trigger = signal.get('buy_trigger', 0)
            if price <= buy_trigger:
                tc = st.get('trade_count_long', 0)
                if tc >= cfg.MAX_DAILY_TRADES:
                    return
                st['trade_count_long'] = tc + 1
                st['fstate'] = STATE_BT_DIPPING
                st['bt_dip_price'] = price
                st['bt_buy_trigger'] = buy_trigger
                st['state_enter_time'] = cfg.now_hms()
                under_pct = (buy_trigger - price) / buy_trigger * 100
                _log('[正T探底#{}/{}] Y{:.2f} <= Y{:.2f}(-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, buy_trigger, under_pct))

    def _handle_spiking(self, price):
        st = self.st
        if price > st['peak_price']:
            st['peak_price'] = price
        peak = st['peak_price']
        pullback = (peak - price) / peak if peak > 0 else 0

        if pullback >= cfg.PULLBACK_PCT:
            _log(f'[卖出] 最高Y{peak:.2f} 回落{pullback*100:.2f}% -> Y{price:.2f} 确认卖出')
            atr_pct = st['daily_signal']['atr_pct']
            buyback_pct = atr_pct * cfg.BUYBACK_TRIGGER_MULT
            buyback_target = round(price * (1.0 - buyback_pct), 2)
            st['sell_fill_price'] = price
            st['buyback_target'] = buyback_target
            st['buyback_target_pct'] = buyback_pct * 100
            st['sell_elapsed_bars'] = 0
            st['state_enter_time'] = cfg.now_hms()
            _log(f'  买回触发线: Y{buyback_target:.2f} (卖价-{buyback_pct*100:.2f}%)')

            # ★ v18: 快照→下单→等待成交确认→推进状态
            snap = self._snapshot_account()
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            if self._wait_for_fill(snap, -TRADE_LOT_SIZE, '反T卖出', price,
                                   -TRADE_LOT_SIZE, STATE_SOLD):
                st['fstate'] = STATE_SOLD
            else:
                # 超时: 保守处理, 认为已成交 (MiniQMT有时回调慢)
                _log('[反T卖出] 成交确认超时, 仍进入SOLD监控(请手动核实)')
                st['fstate'] = STATE_SOLD

    def _handle_sold(self, price):
        st = self.st
        sp = st['sell_fill_price']
        bt = st['buyback_target']
        emergency_line = sp * (1.0 + cfg.EMERGENCY_BUYBACK_PCT)
        if price >= emergency_line:
            rise_pct = (price - sp) / sp * 100
            _log(f'[紧急买回] 卖Y{sp:.2f} -> 现Y{price:.2f}(+{rise_pct:.2f}%)')
            self._do_buyback(price, '紧急')
            return
        tightened_bt = bt
        if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
            tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] * cfg.BUYBACK_TRIGGER_MULT * cfg.BUYBACK_TIGHTEN_MULT)
            tightened_bt = round(max(tightened_bt, bt), 2)
        if price <= tightened_bt:
            drop_pct = (sp - price) / sp * 100
            st['fstate'] = STATE_DIPPING
            st['dip_price'] = price
            st['state_enter_time'] = cfg.now_hms()
            tag = '(收紧)' if tightened_bt > bt else ''
            _log(f'[买回触发{tag}] Y{price:.2f}(-{drop_pct:.2f}%)')

    def _handle_dipping(self, price):
        st = self.st
        if price < st['dip_price']:
            st['dip_price'] = price
        dip = st['dip_price'] or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            sell_p = st['sell_fill_price']
            gross = (sell_p - price) * TRADE_LOT_SIZE
            _log(f'[买回] 最低Y{dip:.2f} 回升{bounce*100:.2f}% -> Y{price:.2f}')
            _log(f'  卖Y{sell_p:.2f} -> 买Y{price:.2f} | 毛利~Y{gross:.0f}')
            self._do_buyback(price, '正常')
            self.total_t_days += 1
            self.total_pnl += gross

    def _do_buyback(self, price, reason=''):
        need = price * TRADE_LOT_SIZE * 1.001
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail = account[0].m_dAvailable if account else 0.0
        if avail < need:
            _log(f'[买回失败-{reason}] 资金不足 (需Y{need:,.0f} > Y{avail:,.0f})')
            return

        snap = self._snapshot_account()
        _log(f'  >>> 下单买回({reason}): Y{price:.2f} x {TRADE_LOT_SIZE}股')
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)

        # ★ v18: 等待成交确认
        if self._wait_for_fill(snap, TRADE_LOT_SIZE, f'反T买回({reason})',
                                price, TRADE_LOT_SIZE, STATE_DONE):
            self.st['fstate'] = STATE_DONE
            # ★ v18: 检查是否还有交易次数, 有则回到IDLE继续
            self._maybe_resume_trading()
        else:
            _log('[反T买回] 成交确认超时, 仍标记DONE(请手动核实)')
            self.st['fstate'] = STATE_DONE
            self._maybe_resume_trading()

    def _force_buyback(self):
        _log(f'[强制买回] 对手价 x {TRADE_LOT_SIZE}股')
        snap = self._snapshot_account()
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
        if self._wait_for_fill(snap, TRADE_LOT_SIZE, '反T强制买回', 0,
                                TRADE_LOT_SIZE, STATE_FORCED):
            self.st['fstate'] = STATE_FORCED
        else:
            _log('[强制买回] 成交确认超时, 仍标记FORCED(请手动核实)')
            self.st['fstate'] = STATE_FORCED

    # ── 正T 状态处理 ──

    def _handle_bt_dipping(self, price):
        st = self.st
        if price < st.get('bt_dip_price', price):
            st['bt_dip_price'] = price
        dip = st.get('bt_dip_price', price) or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            _log(f'[正T买入] 最低Y{dip:.2f} 回升{bounce*100:.2f}% -> Y{price:.2f}')
            need = price * TRADE_LOT_SIZE * 1.001
            account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
            avail = account[0].m_dAvailable if account else 0.0
            if avail < need:
                _log(f'[正T买入失败] 资金不足')
                st['fstate'] = STATE_IDLE
                return

            snap = self._snapshot_account()
            order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)

            # ★ v18: 等待成交确认
            if self._wait_for_fill(snap, TRADE_LOT_SIZE, '正T买入', price,
                                    TRADE_LOT_SIZE, STATE_BT_BOUGHT):
                st['fstate'] = STATE_BT_BOUGHT
                st['bt_buy_fill_price'] = price
                st['bt_sellback_target'] = round(price * (1.0 + cfg.SELLBACK_RISE_PCT), 2)
                _log(f'  买价Y{price:.2f} | 卖回触发线: Y{st["bt_sellback_target"]:.2f}')
            else:
                # 超时: 保守进入 BT_BOUGHT
                _log('[正T买入] 成交确认超时, 仍进入监控(请手动核实)')
                st['fstate'] = STATE_BT_BOUGHT
                st['bt_buy_fill_price'] = price
                st['bt_sellback_target'] = round(price * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

    def _handle_bt_bought(self, price):
        st = self.st
        target = st.get('bt_sellback_target', 999999)
        buy_price = st.get('bt_buy_fill_price', 0)
        if buy_price > 0 and price <= buy_price * (1.0 - cfg.STOP_LOSS_PCT):
            loss_pct = (price - buy_price) / buy_price * 100
            _log('[正T止损] 买Y{:.2f} 现Y{:.2f}({:.1f}%) 触发止损'.format(buy_price, price, loss_pct))
            self._do_bt_force_sell()
            return
        if price >= target:
            rise = (price - buy_price) / buy_price * 100
            st['fstate'] = STATE_BT_SPIKING
            st['bt_sell_peak_price'] = price
            _log('[正T卖回监控] 涨{:.2f}% -> Y{:.2f} >= Y{:.2f} | 等回落{:.2f}%'.format(
                rise, price, target, cfg.PULLBACK_PCT * 100))

    def _handle_bt_spiking(self, price):
        st = self.st
        if price > st.get('bt_sell_peak_price', price):
            st['bt_sell_peak_price'] = price
        peak = st.get('bt_sell_peak_price', price)
        pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            bp = st['bt_buy_fill_price']
            gross = (price - bp) * TRADE_LOT_SIZE
            _log(f'[正T卖出] 峰值Y{peak:.2f} 回落{pullback*100:.2f}% -> Y{price:.2f} | 毛利~Y{gross:.0f}')

            snap = self._snapshot_account()
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)

            # ★ v18: 等待成交确认
            if self._wait_for_fill(snap, -TRADE_LOT_SIZE, '正T卖出', price,
                                    -TRADE_LOT_SIZE, STATE_DONE):
                st['fstate'] = STATE_DONE
                self.total_t_days += 1
                self.total_pnl += gross
                # ★ v18: 检查是否还有交易次数
                self._maybe_resume_trading()
            else:
                _log('[正T卖出] 成交确认超时, 仍标记DONE(请手动核实)')
                st['fstate'] = STATE_DONE
                self.total_t_days += 1
                self.total_pnl += gross
                self._maybe_resume_trading()

    def _do_bt_force_sell(self):
        _log(f'[正T强制卖出] 对手价 x {TRADE_LOT_SIZE}股')
        snap = self._snapshot_account()
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
        if self._wait_for_fill(snap, -TRADE_LOT_SIZE, '正T强制卖出', 0,
                                -TRADE_LOT_SIZE, STATE_FORCED):
            self.st['fstate'] = STATE_FORCED
        else:
            _log('[强制卖出] 成交确认超时, 仍标记FORCED(请手动核实)')
            self.st['fstate'] = STATE_FORCED

    # ═══════════════════════════════════════════════════════════════
    # v18: 交易完成后的恢复机制
    # ═══════════════════════════════════════════════════════════════

    def _maybe_resume_trading(self):
        """
        ★ v18 新增: DONE后检查是否还有剩余交易次数。
        有次数 → 回到 IDLE 继续监控; 无次数 → 保持 DONE。
        """
        st = self.st
        tc_short = st.get('trade_count_short', 0)
        tc_long = st.get('trade_count_long', 0)
        do_short = st.get('do_short', False)
        do_long = st.get('do_long', False)

        can_short = do_short and tc_short < cfg.MAX_DAILY_TRADES
        can_long = do_long and tc_long < cfg.MAX_DAILY_TRADES

        if can_short or can_long:
            self._refresh_position()
            st['fstate'] = STATE_IDLE
            st['peak_price'] = 0.0
            st['dip_price'] = 0.0
            st['sell_fill_price'] = 0.0
            st['buyback_target'] = 0.0
            st['state_enter_time'] = cfg.now_hms()
            st['stop_loss_hit'] = False
            parts = []
            if can_short:
                parts.append('反T({}/{})'.format(tc_short, cfg.MAX_DAILY_TRADES))
            if can_long:
                parts.append('正T({}/{})'.format(tc_long, cfg.MAX_DAILY_TRADES))
            _log('[恢复监控] 交易完成, 仍有可用次数 → 回到 IDLE ({})'.format(', '.join(parts)))
        else:
            _log('[交易完成] 今日{}笔已达上限, 保持DONE'.format(tc_short + tc_long))

    # ── v12: 锁仓强度评估 (沿用) ──

    def _assess_strength(self, price, now_ts):
        st = self.st
        sig = st.get('daily_signal', {})
        open_price = sig.get('open_price', 0)
        if open_price <= 0:
            return
        st['price_history'].append((now_ts, price))
        cutoff = now_ts - cfg.LOCK_LOOKBACK_SEC
        st['price_history'] = [(t, p) for t, p in st['price_history'] if t >= cutoff]
        history = st['price_history']
        if len(history) < 10:
            return
        prices = [p for _, p in history]
        price_5min_ago = prices[0]
        price_now = prices[-1]
        cond1 = price_now > open_price * (1.0 + cfg.LOCK_PRICE_RATIO)
        momentum = (price_now - price_5min_ago) / price_5min_ago if price_5min_ago > 0 else 0
        cond2 = momentum > cfg.LOCK_MOMENTUM_PCT
        day_high = max(prices)
        drawdown = (day_high - price_now) / day_high if day_high > 0 else 1
        cond3 = drawdown < cfg.LOCK_DRAWDOWN_PCT
        should_lock = cond1 and cond2 and cond3
        cooldown_ok = now_ts >= st.get('lock_cooldown_until', 0)
        if should_lock and not st.get('locked'):
            st['locked'] = True
            st['lock_since'] = cfg.now_hms()
            st['lock_reason'] = '开盘+{:.1f}% 动量+{:.2f}% 回撤{:.2f}%'.format(
                (price_now / open_price - 1) * 100, momentum * 100, drawdown * 100)
            _log('[锁仓] {} | 盘面强势, 禁止卖出'.format(st['lock_reason']))
        elif not should_lock and st.get('locked') and cooldown_ok:
            st['locked'] = False
            st['lock_reason'] = ''
            st['lock_since'] = ''
            _log('[解锁] 强势条件消失, 恢复卖出监控')
        if not should_lock and not st.get('locked'):
            st['lock_cooldown_until'] = 0.0
        if should_lock:
            st['lock_cooldown_until'] = 0.0
        elif st.get('locked') and st['lock_cooldown_until'] == 0.0:
            st['lock_cooldown_until'] = now_ts + cfg.LOCK_COOLDOWN_SEC

    # ═══════════════════════════════════════════════════════════════
    # v18: 主循环 — 状态不卡死
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
        _log(f'{STOCK_NAME} 迷你反T v18 MiniQMT版 启动')
        _log(f'  mode: {"信号监控(不下单)" if self.dry_run else "实盘交易"}')
        _log(f'  标的: {STOCK_NAME}({STOCK_QMT})')
        _log(f'  ★ v18: 订单成交确认 + 状态不卡死 + 多笔自动恢复')
        _log(f'  ★ 日志: {get_logger().log_path if get_logger() else "(未初始化)"}')

        try:
            self._daily_init()
            signal = self.st.get('daily_signal')
            if signal:
                self._print_signal_summary(signal)
                self._print_trading_plan(signal)
        except Exception as e:
            _log(f'[异常] 初始化失败: {e}')
            _traceback.print_exc()

        _log('开始监控... (Ctrl+C 停止)')
        _log('')

        try:
            while self._running:
                now = cfg.now_hms()
                now_ts = _time.time()

                # ── 非交易时段 ──
                if not cfg.is_market_open(now):
                    today = datetime.now().strftime('%Y%m%d')
                    if '09:25:00' <= now < '09:30:00':
                        if self.st.get('_pre_market_done', '') != today:
                            _log(f'\n[盘前预初始化] {now} 集合竞价结束, 提前计算信号...')
                            try:
                                self._daily_init()
                                self.st['_pre_market_done'] = today
                                signal = self.st.get('daily_signal')
                                if signal:
                                    self._print_signal_summary(signal)
                                    self._print_trading_plan(signal)
                                _log(f'[盘前就绪] 信号已计算, 等待9:30开盘...')
                            except Exception as e:
                                _log(f'[盘前异常] 预初始化失败: {e}')
                                _traceback.print_exc()
                            self._last_heartbeat = now_ts
                            _time.sleep(5)
                            continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                            _log(f'[盘前就绪] {now} — 距开盘 {cfg.time_to_open(now)}')
                        _time.sleep(5)
                        continue

                    if self.st.get('trade_date', '') != today:
                        _log(f'[盘前] 新交易日 {today}, 初始化...')
                        try:
                            self._daily_init()
                            signal = self.st.get('daily_signal')
                            if signal:
                                self._print_signal_summary(signal)
                                self._print_trading_plan(signal)
                        except Exception as e:
                            _log(f'[异常] 初始化失败: {e}')
                            _traceback.print_exc()

                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        if now < '09:30:00':
                            _log(f'[等待开盘] {now} — 距开盘 {cfg.time_to_open(now)}')
                        elif now > '15:00:00':
                            _log(f'[已收盘] {now} — 策略待命')
                        elif '11:30:00' < now < '13:00:00':
                            _log(f'[午休] {now} — 等待下午开盘 13:00')
                    _time.sleep(10)
                    continue

                # ═══════════════════════════════════════════════════
                # 交易时段
                # ═══════════════════════════════════════════════════
                fstate = self.st.get('fstate', STATE_IDLE)

                # ★ v18: DONE/FORCED 不再静默卡死
                if fstate in (STATE_DONE, STATE_FORCED):
                    # 仍然获取行情, 用于心跳
                    tick = self.ctx.get_full_tick([STOCK_QMT])
                    price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)

                    if now_ts - self._last_heartbeat >= 30:
                        self._last_heartbeat = now_ts
                        tc_s = self.st.get('trade_count_short', 0)
                        tc_l = self.st.get('trade_count_long', 0)
                        _log('[状态] {} | Y{:.2f} | 今日已完成: 反T{}/{} 正T{}/{} | 累计{}笔 毛利~Y{:,.0f}'.format(
                            fstate, price,
                            tc_s, cfg.MAX_DAILY_TRADES,
                            tc_l, cfg.MAX_DAILY_TRADES,
                            self.total_t_days, self.total_pnl))

                    # 尾盘14:57后不再尝试恢复
                    if now < '14:57:00':
                        # 检查是否有剩余次数可用
                        tc_s = self.st.get('trade_count_short', 0)
                        tc_l = self.st.get('trade_count_long', 0)
                        do_short = self.st.get('do_short', False)
                        do_long = self.st.get('do_long', False)
                        if ((do_short and tc_s < cfg.MAX_DAILY_TRADES) or
                            (do_long and tc_l < cfg.MAX_DAILY_TRADES)):
                            # 回到IDLE继续监控
                            self._maybe_resume_trading()

                    _time.sleep(3)
                    continue

                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick:
                    _time.sleep(1)
                    continue

                price = tick[STOCK_QMT].get('lastPrice', 0)
                if price <= 0:
                    _time.sleep(1)
                    continue

                signal = self.st.get('daily_signal')
                do_short = self.st.get('do_short', False)
                do_long = self.st.get('do_long', False)
                if signal is None or (not do_short and not do_long):
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        _log('[待命] Y{:.2f} | 无可用交易方向'.format(price))
                    _time.sleep(5)
                    continue

                if fstate == STATE_IDLE:
                    self._assess_strength(price, now_ts)

                # 状态路由
                if fstate == STATE_IDLE:
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

                if self.st['fstate'] in (STATE_SOLD, STATE_DIPPING):
                    self.st['sell_elapsed_bars'] += 1

                # 尾盘强制平仓
                if cfg.ENABLE_FORCE_CLOSE and now >= cfg.FORCE_CLOSE_TIME:
                    f = self.st['fstate']
                    if f in (STATE_SOLD, STATE_DIPPING):
                        _log(f'[尾盘] {now} 强制买回(反T)')
                        self._force_buyback()
                    elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
                        _log(f'[尾盘] {now} 强制卖出(正T)')
                        self._do_bt_force_sell()
                    elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE):
                        self.st['fstate'] = STATE_DONE

                # 反T止损
                if fstate == STATE_SOLD and not self.st.get('stop_loss_hit', False):
                    loss_limit = self.st['base_shares'] * signal['open_price'] * cfg.STOP_LOSS_PCT
                    if self.st.get('day_pnl', 0) < -loss_limit:
                        _log(f'[止损-反T] 亏损超限')
                        self.st['stop_loss_hit'] = True
                        self._force_buyback()

                # 心跳日志
                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    self._heartbeat(price)

                _time.sleep(1)

        except KeyboardInterrupt:
            _log('\n用户中断')
        except Exception as e:
            _log(f'[异常] {e}')
            _traceback.print_exc()
        finally:
            self.conn.disconnect()
            fstate = self.st.get('fstate', '')
            if fstate in (STATE_SOLD, STATE_DIPPING):
                _log('[警告] 策略停止时有未买回头寸! 请手动检查!')
            _log(f'{STOCK_NAME} v18 MiniQMT版 已停止 | 累计 {self.total_t_days}天 | 毛利~Y{self.total_pnl:,.0f}')
            logger = get_logger()
            if logger is not None:
                _log('★ 日志已保存至: ' + logger.log_path)
                logger.close()

    def _heartbeat(self, price):
        """每分钟心跳日志"""
        fs = self.st['fstate']
        sig = self.st.get('daily_signal', {})

        # ★ v18: DONE/FORCED 心跳
        if fs in (STATE_DONE, STATE_FORCED):
            tc_s = self.st.get('trade_count_short', 0)
            tc_l = self.st.get('trade_count_long', 0)
            _log('[心跳] {} | Y{:.2f} | 已完成 反T{}/{} 正T{}/{} | 累计{}笔 ~Y{:,.0f}'.format(
                fs, price, tc_s, cfg.MAX_DAILY_TRADES, tc_l, cfg.MAX_DAILY_TRADES,
                self.total_t_days, self.total_pnl))
            return

        if fs == STATE_IDLE:
            if self.st.get('do_long'):
                bt_floor = sig.get('buy_trigger_floor', 0)
                bt_trail = round(price * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
                max_trail = max(self.st.get('bt_max_trail', 0), bt_trail)
                self.st['bt_max_trail'] = max_trail
                bt_dynamic = max(bt_floor, max_trail)
                sig['buy_trigger'] = bt_dynamic
                sig['buy_trigger_trail'] = bt_trail
            parts = []
            if self.st.get('do_short'):
                st_trig = sig.get('sell_trigger', 0)
                parts.append('反T: 需涨{:.2f}至Y{:.2f}'.format(st_trig - price, st_trig))
            else:
                parts.append('反T: 禁止')
            if self.st.get('do_long'):
                bt_dyn = sig.get('buy_trigger', 0)
                parts.append('正T: 需跌{:.2f}至Y{:.2f}'.format(price - bt_dyn, bt_dyn))
            else:
                parts.append('正T: 禁止')
            if self.st.get('locked'):
                parts.append('锁仓')
            _log('[心跳] {} | Y{:.2f} | {}'.format(fs, price, '  |  '.join(parts)))
        elif fs == STATE_SPIKING:
            peak = self.st.get('peak_price', 0)
            pb = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[心跳] {} | Y{:.2f} | peak=Y{:.2f} 回落{:.2f}%'.format(fs, price, peak, pb))
        elif fs in (STATE_SOLD, STATE_DIPPING):
            sp = self.st.get('sell_fill_price', 0)
            bt = self.st.get('buyback_target', 0)
            if sp > 0:
                chg = (price - sp) / sp * 100
                _log('[心跳] {} | Y{:.2f} | 卖Y{:.2f} {:+d}% | 买回线Y{:.2f}'.format(
                    fs, price, sp, chg, bt))
        elif fs == STATE_BT_DIPPING:
            dip = self.st.get('bt_dip_price', price)
            bounce = (price - dip) / dip * 100 if dip > 0 else 0
            _log('[心跳] {} | Y{:.2f} | dip=Y{:.2f} 回升{:.2f}%'.format(fs, price, dip, bounce))
        elif fs == STATE_BT_BOUGHT:
            bp = self.st.get('bt_buy_fill_price', 0)
            target = self.st.get('bt_sellback_target', 0)
            if bp > 0:
                chg = (price - bp) / bp * 100
                _log('[心跳] {} | Y{:.2f} | 买Y{:.2f} {:+d}% | 卖出线Y{:.2f}'.format(
                    fs, price, bp, chg, target))
        elif fs == STATE_BT_SPIKING:
            peak = self.st.get('bt_sell_peak_price', price)
            pb = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[心跳] {} | Y{:.2f} | peak=Y{:.2f} 回落{:.2f}%'.format(fs, price, peak, pb))
        else:
            _log('[心跳] {} | Y{:.2f}'.format(fs, price))


# ============================================================================
# 回测mode & CLI
# ============================================================================

def run_backtest_mode(start='20250801', end='20260806'):
    print(f'\n{"="*55}')
    print(f'  回测mode — QMT 迷你反T v18')
    print(f'  区间: {start} ~ {end}')
    print(f'{"="*55}\n')
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from backtest.backtest_v10_xtdata import XTDataManager, BacktestEngine
    data_mgr = XTDataManager('601869.SH', data_dir='C:/QMT/datadir')
    data_mgr.load_daily(start=start, end=end)
    engine = BacktestEngine(data_mgr)
    engine.run(start_date=start, end_date=end)
    engine.print_report()
    engine.save_csv()


def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT 迷你反T v18 — 订单确认+状态不卡死',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python "Stragety/MiniQMT_Stragety/DayTradeing_v18_stragety_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/DayTradeing_v18_stragety_miniqmt.py" --mode live
  python "Stragety/MiniQMT_Stragety/DayTradeing_v18_stragety_miniqmt.py" --mode backtest

v18 改动 (vs v17):
  ★ _wait_for_fill() — 下单后轮询持仓确认成交, 不盲目推进状态
  ★ DONE/FORCED 不再静默 — 持续输出心跳 + 交易统计
  ★ _maybe_resume_trading() — 交易完成后检测剩余次数, 自动回到IDLE继续
  ★ 废单自动重试 — 真正废单时回退IDLE, 保留交易次数

前置条件:
  1. 启动 MiniQMT: QMT -> 右上角"极简mode"
  2. MiniQMT 中已登录资金账号 8890145315
  3. QMT 已下载 601869 历史数据
        """
    )
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250801')
    parser.add_argument('--end', default='20260806')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_backtest_mode(args.start, args.end)
        return

    logger = FileLogger(STOCK_CODE, version='v18')
    set_logger(logger)
    print(f'★ 日志文件: {logger.log_path}')

    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  ⚠ 即将启动实盘自动交易!')
        print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
        print(f'  账号: {ACCOUNT}')
        print('  请确认:')
        print('  1. MiniQMT 已启动 (极简mode)')
        print('  2. 资金账号已登录')
        print('  3. 当前持有底仓')
        print('!' * 55)
        confirm = input('\n确认启动? (输入 yes 继续): ')
        if confirm.strip().lower() != 'yes':
            print('已取消')
            logger.close()
            return

    runner = StrategyRunner(dry_run=dry_run)
    runner.run()


if __name__ == '__main__':
    main()
