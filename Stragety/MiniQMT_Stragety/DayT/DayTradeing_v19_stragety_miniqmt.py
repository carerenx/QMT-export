# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — QMT day trading v19 + 精简日志格式
================================================================================

 [v19 改动] (vs v18)
   ★ 去掉所有右侧竖线 — 不再用 ─┐ ├┤ └┘ 封闭边框, 中英文混排对齐问题彻底消除
   ★ 精简空白行 — 去掉面板间的多余 _log('') 换行
   ★ 信号摘要/交易计划/交易校验 全部改为左缩进 + ── 分隔的紧凑格式
   ★ 代码逻辑完全继承 v18 (订单确认 + 状态不卡死 + 多笔恢复)

 [run mode]
   signal:  python "Stragety/MiniQMT_Stragety/DayTradeing_v19_stragety_miniqmt.py" --mode signal
   live:    python "Stragety/MiniQMT_Stragety/DayTradeing_v19_stragety_miniqmt.py" --mode live
   backtest:python "Stragety/MiniQMT_Stragety/DayTradeing_v19_stragety_miniqmt.py" --mode backtest

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


class StrategyRunner:
    """MiniQMT 策略运行器 — v19: 精简日志格式"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='v19')
            set_logger(logger)
        self.conn = MiniQMTConnector()
        set_global_conn(self.conn, dry_run)
        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = dry_run
        self._last_heartbeat = 0.0
        self._running = True
        self.total_t_days = 0
        self.total_pnl = 0.0

    # ── 状态初始化 ──

    def _init_state(self):
        self.st.update({
            'daily_signal': None, 'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'entry_price': 0.0, 'fstate': STATE_IDLE,
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
            'locked': False, 'lock_reason': '', 'lock_since': '',
            'lock_cooldown_until': 0.0, 'price_history': deque(),
            '_pre_market_done': '',
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

    # ── 每日初始化 ──

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if self.st.get('trade_date', '') == today and self.st.get('initialized', False):
            self._refresh_position()
            return
        is_new_day = self.st.get('trade_date', '') and self.st['trade_date'] != today
        if is_new_day:
            _log(f'[新交易日] {self.st["trade_date"]} -> {today}')
        saved_trail = self.st.get('bt_max_trail', 0) if not is_new_day else 0
        saved_history = self.st.get('price_history', []) if not is_new_day else []
        self._reset_daily()
        self.st['trade_date'] = today
        self.st['_guard_date'] = today
        if not is_new_day and saved_trail > 0:
            self.st['bt_max_trail'] = saved_trail
            self.st['price_history'] = saved_history

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

        signal = compute_signal(
            opens_list, hist_high[STOCK_QMT], hist_low[STOCK_QMT],
            hist_close[STOCK_QMT], hist_volume[STOCK_QMT])
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

        for k in ('fstate', 'peak_price', 'dip_price', 'sell_fill_price',
                   'buyback_target', 'buyback_target_pct'):
            self.st[k] = STATE_IDLE if k == 'fstate' else 0.0
        self.st['day_pnl'] = 0.0
        self.st['stop_loss_hit'] = False
        self.st['state_enter_time'] = cfg.now_hms()
        self.st['sell_elapsed_bars'] = 0
        self.st['initialized'] = True
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
            self.st[k] = 0.0
        self.st['locked'] = False
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
    # 订单成交确认 (同 v18)
    # ═══════════════════════════════════════════════════════════════

    def _wait_for_fill(self, snap_before, expected_shares_delta,
                       label, trade_price, trade_shares, next_state, timeout_sec=5.0):
        if self.dry_run:
            self._verify_trade(snap_before, label, trade_price, trade_shares)
            return True
        check_interval = 0.5
        waited = 0.0
        while waited < timeout_sec:
            _time.sleep(check_interval)
            waited += check_interval
            curr_snap = self._snapshot_account()
            actual_delta = curr_snap['shares'] - snap_before['shares']
            if actual_delta == expected_shares_delta:
                _log(f'  ✅ 成交确认({waited:.1f}s) → {next_state}')
                self._verify_trade(snap_before, label, trade_price, trade_shares)
                return True
            if (expected_shares_delta > 0 and actual_delta > 0) or \
               (expected_shares_delta < 0 and actual_delta < 0):
                _log(f'  ⚠ 部分成交 Δ{actual_delta:+d}股, 接受')
                self._verify_trade(snap_before, label, trade_price, trade_shares)
                return True
        _log(f'  ❌ 成交确认超时({waited:.1f}s), 持仓Δ{curr_snap["shares"] - snap_before["shares"]:+d}')
        self._verify_trade(snap_before, f'{label}(超时)', trade_price, trade_shares)
        return False

    # ═══════════════════════════════════════════════════════════════
    # 账户快照 & 交易校验
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
        return {'shares': shares, 'can_use': can_use, 'cash': cash,
                'cost': cost, 'total_asset': shares * price + cash, 'price': price}

    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        """★ v19: 精简校验日志 — 单行摘要 + 两行详情"""
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_cash = snap_after['cash'] - snap_before['cash']
        d_asset = snap_after['total_asset'] - snap_before['total_asset']

        if abs(trade_shares) == TRADE_LOT_SIZE:
            status = '✅' if d_shares == trade_shares else ('⏳' if d_shares == 0 else '⚠')
        else:
            status = '📝'

        _log('  ── [校验] {}: {}股@Y{:.2f} {} | 持仓{:+d}股({}→{}) | 资金Δ{:+,.0f} | 资产Δ{:+,.0f}'.format(
            label, '{:+d}'.format(trade_shares), trade_price, status,
            d_shares, snap_before['shares'], snap_after['shares'], d_cash, d_asset))

        if abs(trade_shares) == TRADE_LOT_SIZE and d_shares != trade_shares:
            _log('  ⚠ 持仓变化异常! 预期{:+d}股 实际{:+d}股'.format(trade_shares, d_shares))

        self.st['base_shares'] = snap_after['shares']
        self.st['base_can_use'] = snap_after['can_use']
        self.st['base_cost'] = snap_after['cost']

    # ═══════════════════════════════════════════════════════════════
    # v19: 信号摘要 — 无边框紧凑格式
    # ═══════════════════════════════════════════════════════════════

    def _print_signal_summary(self, signal):
        """★ v19: 去掉封闭边框, 纯左缩进格式"""
        trend = signal.get('trend', '?')
        trend_labels = {'strong_bull': '强牛', 'weak_bull': '弱牛',
                        'sideways': '震荡', 'bear': '熊市'}
        open_p = signal.get('open_price', 0)
        close_y = signal.get('close_yday', 0)
        atr_v = signal.get('atr', 0)
        atr_p = signal.get('atr_pct', 0)
        sell_trig = signal.get('sell_trigger', 0)
        range_capped = signal.get('range_capped', False)

        _log('── 信号摘要 ───────────────────────────────────────────────')
        _log('  趋势: {}  连涨: {}天  开盘: Y{:.2f}  昨收: Y{:.2f}  ATR: {:.1f}%  RSI: {:.0f}  量比: {:.2f}'.format(
            trend_labels.get(trend, trend), signal.get('up_streak', 0),
            open_p, close_y, atr_p * 100, signal.get('rsi', 0), signal.get('vol_ratio', 0)))
        _log('  卖出乘数: {:.2f}(基{:.2f}) 反T触发: Y{:.2f}{}  do_short: {}'.format(
            signal.get('sell_mult', 0), signal.get('sell_mult_base', 0),
            sell_trig, ' ⚠振幅约束' if range_capped else '',
            '✅' if signal.get('do_short', False) else '❌ ' + signal.get('blocked_reason', '')))

        factor_details = signal.get('factor_details', {})
        if factor_details:
            _log('  因子: {}'.format(
                ' '.join('{}{:+.2f}'.format(k, v) for k, v in factor_details.items())))

    def _print_trading_plan(self, signal):
        """★ v19: 去掉右侧竖线, 精简交易计划"""
        base_shares = self.st.get('base_shares', 0)
        base_can_use = self.st.get('base_can_use', 0)
        pos_pct = self.st.get('pos_pct', 0)
        avail_cash = self.st.get('avail_cash', 0)
        tick = self.ctx.get_full_tick([STOCK_QMT])
        curr_price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if curr_price <= 0:
            curr_price = signal.get('open_price', 0)
        pos_value = base_shares * curr_price

        do_short = self.st.get('do_short', False)
        do_long = self.st.get('do_long', False)
        short_lots = self.st.get('short_lots', 0)
        long_lots = self.st.get('long_lots', 0)

        _log('── 交易计划 [{} {}] ──────────────────────────────────'.format(
            signal.get('trend', '?'), self.st.get('trade_date', '')))
        _log('  持仓: {}股 Y{:,.0f}({:.0f}%)  可用: Y{:,.0f}  T+0: {}股({}手)  T+1: {}股'.format(
            base_shares, pos_value, pos_pct, avail_cash,
            base_can_use, base_can_use // TRADE_LOT_SIZE, base_shares - base_can_use))

        # 反T
        if do_short:
            sell_trig = signal.get('sell_trigger', 0)
            _log('  📉 反T ✅ {}手  触发: Y{:.2f}  买回: 卖价×(1-ATR%×{:.2f})  紧急: +{:.0f}%'.format(
                short_lots, sell_trig, cfg.BUYBACK_TRIGGER_MULT, cfg.EMERGENCY_BUYBACK_PCT * 100))
        else:
            reason = signal.get('short_reason', signal.get('blocked_reason', '未知'))
            _log('  📉 反T ❌ {}'.format(reason))

        # 正T
        if do_long:
            buy_trig = signal.get('buy_trigger', 0)
            sell_hint = signal.get('sellback_target_hint', 0)
            _log('  📈 正T ✅ {}手  买入: Y{:.2f}  卖出: Y{:.2f}(买价+{:.1f}%)  1手≈Y{:,.0f}'.format(
                long_lots, buy_trig, sell_hint, cfg.SELLBACK_RISE_PCT * 100,
                curr_price * TRADE_LOT_SIZE))
        else:
            reason = self.st.get('long_reason', '未知')
            _log('  📈 正T ❌ {}  1手≈Y{:,.0f}'.format(reason, curr_price * TRADE_LOT_SIZE))

        _log('  状态: 反T={} 正T={}  最大{}/{}次'.format(
            '✅' if do_short else '❌', '✅' if do_long else '❌',
            cfg.MAX_DAILY_TRADES, cfg.MAX_DAILY_TRADES))
        if self.total_t_days > 0:
            _log('  累计: {}笔 毛利~Y{:,.0f}'.format(self.total_t_days, self.total_pnl))

    # ═══════════════════════════════════════════════════════════════
    # 状态机处理器 (同 v18)
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
                if tc >= cfg.MAX_DAILY_TRADES or st.get('locked', False):
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
                _log('[正T探底#{}/{}] Y{:.2f} <= Y{:.2f}(-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, buy_trigger,
                    (buy_trigger - price) / buy_trigger * 100))

    def _handle_spiking(self, price):
        st = self.st
        if price > st['peak_price']:
            st['peak_price'] = price
        peak = st['peak_price']
        pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            _log('[反T卖出] 峰值Y{:.2f} 回落{:.2f}% → Y{:.2f}'.format(peak, pullback * 100, price))
            atr_pct = st['daily_signal']['atr_pct']
            buyback_pct = atr_pct * cfg.BUYBACK_TRIGGER_MULT
            buyback_target = round(price * (1.0 - buyback_pct), 2)
            st['sell_fill_price'] = price
            st['buyback_target'] = buyback_target
            st['buyback_target_pct'] = buyback_pct * 100
            st['sell_elapsed_bars'] = 0
            st['state_enter_time'] = cfg.now_hms()
            _log('  买回线: Y{:.2f}  紧急线: Y{:.2f}'.format(
                buyback_target, price * (1 + cfg.EMERGENCY_BUYBACK_PCT)))
            snap = self._snapshot_account()
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            if self._wait_for_fill(snap, -TRADE_LOT_SIZE, '反T卖出', price,
                                   -TRADE_LOT_SIZE, STATE_SOLD):
                st['fstate'] = STATE_SOLD
            else:
                _log('[反T卖出] 成交确认超时, 仍进入SOLD(请核实)')
                st['fstate'] = STATE_SOLD

    def _handle_sold(self, price):
        st = self.st
        sp = st['sell_fill_price']
        bt = st['buyback_target']
        emergency_line = sp * (1.0 + cfg.EMERGENCY_BUYBACK_PCT)
        if price >= emergency_line:
            _log('[紧急买回] 卖Y{:.2f} → 现Y{:.2f}(+{:.2f}%)'.format(
                sp, price, (price - sp) / sp * 100))
            self._do_buyback(price, '紧急')
            return
        tightened_bt = bt
        if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
            tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] *
                                 cfg.BUYBACK_TRIGGER_MULT * cfg.BUYBACK_TIGHTEN_MULT)
            tightened_bt = round(max(tightened_bt, bt), 2)
        if price <= tightened_bt:
            st['fstate'] = STATE_DIPPING
            st['dip_price'] = price
            st['state_enter_time'] = cfg.now_hms()
            tag = '(收紧)' if tightened_bt > bt else ''
            _log('[买回触发{}] Y{:.2f}(-{:.2f}%)'.format(
                tag, price, (sp - price) / sp * 100))

    def _handle_dipping(self, price):
        st = self.st
        if price < st['dip_price']:
            st['dip_price'] = price
        dip = st['dip_price'] or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            sell_p = st['sell_fill_price']
            gross = (sell_p - price) * TRADE_LOT_SIZE
            _log('[反T买回] 最低Y{:.2f} 回升{:.2f}% → Y{:.2f}  毛利~Y{:,.0f}'.format(
                dip, bounce * 100, price, gross))
            self._do_buyback(price, '正常')
            self.total_t_days += 1
            self.total_pnl += gross

    def _do_buyback(self, price, reason=''):
        need = price * TRADE_LOT_SIZE * 1.001
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail = account[0].m_dAvailable if account else 0.0
        if avail < need:
            _log('[买回失败-{}] 资金不足 (需Y{:,.0f} > Y{:,.0f})'.format(reason, need, avail))
            return
        snap = self._snapshot_account()
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
        if self._wait_for_fill(snap, TRADE_LOT_SIZE, f'反T买回({reason})',
                                price, TRADE_LOT_SIZE, STATE_DONE):
            self.st['fstate'] = STATE_DONE
            self._maybe_resume_trading()
        else:
            _log('[反T买回] 成交确认超时, 仍标记DONE(请核实)')
            self.st['fstate'] = STATE_DONE
            self._maybe_resume_trading()

    def _force_buyback(self):
        _log('[强制买回] 对手价 x {}股'.format(TRADE_LOT_SIZE))
        snap = self._snapshot_account()
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
        if self._wait_for_fill(snap, TRADE_LOT_SIZE, '反T强制买回', 0,
                                TRADE_LOT_SIZE, STATE_FORCED):
            self.st['fstate'] = STATE_FORCED
        else:
            self.st['fstate'] = STATE_FORCED

    # ── 正T ──

    def _handle_bt_dipping(self, price):
        st = self.st
        if price < st.get('bt_dip_price', price):
            st['bt_dip_price'] = price
        dip = st.get('bt_dip_price', price) or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            _log('[正T买入] 最低Y{:.2f} 回升{:.2f}% → Y{:.2f}'.format(dip, bounce * 100, price))
            need = price * TRADE_LOT_SIZE * 1.001
            account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
            avail = account[0].m_dAvailable if account else 0.0
            if avail < need:
                _log('[正T买入失败] 资金不足')
                st['fstate'] = STATE_IDLE
                return
            snap = self._snapshot_account()
            order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            if self._wait_for_fill(snap, TRADE_LOT_SIZE, '正T买入', price,
                                    TRADE_LOT_SIZE, STATE_BT_BOUGHT):
                st['fstate'] = STATE_BT_BOUGHT
                st['bt_buy_fill_price'] = price
                st['bt_sellback_target'] = round(price * (1.0 + cfg.SELLBACK_RISE_PCT), 2)
            else:
                st['fstate'] = STATE_BT_BOUGHT
                st['bt_buy_fill_price'] = price
                st['bt_sellback_target'] = round(price * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

    def _handle_bt_bought(self, price):
        st = self.st
        target = st.get('bt_sellback_target', 999999)
        buy_price = st.get('bt_buy_fill_price', 0)
        if buy_price > 0 and price <= buy_price * (1.0 - cfg.STOP_LOSS_PCT):
            _log('[正T止损] 买Y{:.2f} 现Y{:.2f}({:.1f}%)'.format(
                buy_price, price, (price - buy_price) / buy_price * 100))
            self._do_bt_force_sell()
            return
        if price >= target:
            st['fstate'] = STATE_BT_SPIKING
            st['bt_sell_peak_price'] = price
            _log('[正T卖回监控] 涨{:.2f}% → Y{:.2f} >= Y{:.2f}'.format(
                (price - buy_price) / buy_price * 100, price, target))

    def _handle_bt_spiking(self, price):
        st = self.st
        if price > st.get('bt_sell_peak_price', price):
            st['bt_sell_peak_price'] = price
        peak = st.get('bt_sell_peak_price', price)
        pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            bp = st['bt_buy_fill_price']
            gross = (price - bp) * TRADE_LOT_SIZE
            _log('[正T卖出] 峰值Y{:.2f} 回落{:.2f}% → Y{:.2f}  毛利~Y{:,.0f}'.format(
                peak, pullback * 100, price, gross))
            snap = self._snapshot_account()
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            if self._wait_for_fill(snap, -TRADE_LOT_SIZE, '正T卖出', price,
                                    -TRADE_LOT_SIZE, STATE_DONE):
                st['fstate'] = STATE_DONE
                self.total_t_days += 1
                self.total_pnl += gross
                self._maybe_resume_trading()
            else:
                st['fstate'] = STATE_DONE
                self.total_t_days += 1
                self.total_pnl += gross
                self._maybe_resume_trading()

    def _do_bt_force_sell(self):
        _log('[正T强制卖出] 对手价 x {}股'.format(TRADE_LOT_SIZE))
        snap = self._snapshot_account()
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
        if self._wait_for_fill(snap, -TRADE_LOT_SIZE, '正T强制卖出', 0,
                                -TRADE_LOT_SIZE, STATE_FORCED):
            self.st['fstate'] = STATE_FORCED
        else:
            self.st['fstate'] = STATE_FORCED

    # ── 交易恢复 ──

    def _maybe_resume_trading(self):
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
            _log('[恢复] → IDLE  ({})'.format(', '.join(parts)))
        else:
            _log('[完成] 今日{}/{}笔 已达上限'.format(tc_short + tc_long, cfg.MAX_DAILY_TRADES * 2))

    # ── 锁仓 ──

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
            st['lock_reason'] = '+{:.1f}% 动量+{:.2f}% 回撤{:.2f}%'.format(
                (price_now / open_price - 1) * 100, momentum * 100, drawdown * 100)
            _log('[锁仓] {}'.format(st['lock_reason']))
        elif not should_lock and st.get('locked') and cooldown_ok:
            st['locked'] = False
            st['lock_reason'] = ''
            st['lock_since'] = ''
            _log('[解锁]')
        if not should_lock and not st.get('locked'):
            st['lock_cooldown_until'] = 0.0
        if should_lock:
            st['lock_cooldown_until'] = 0.0
        elif st.get('locked') and st['lock_cooldown_until'] == 0.0:
            st['lock_cooldown_until'] = now_ts + cfg.LOCK_COOLDOWN_SEC

    # ═══════════════════════════════════════════════════════════════
    # 主循环 (v18 逻辑: DONE不静默 + 多笔恢复)
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

        self._init_state()
        _log('{} 迷你反T v19 启动 | {} | {}'.format(
            STOCK_NAME, '实盘' if not self.dry_run else '信号', STOCK_QMT))
        _log('日志: {}'.format(get_logger().log_path if get_logger() else '?'))

        try:
            self._daily_init()
            signal = self.st.get('daily_signal')
            if signal:
                self._print_signal_summary(signal)
                self._print_trading_plan(signal)
        except Exception as e:
            _log('[异常] 初始化失败: {}'.format(e))
            _traceback.print_exc()

        _log('开始监控... (Ctrl+C 停止)')

        try:
            while self._running:
                now = cfg.now_hms()
                now_ts = _time.time()

                if not cfg.is_market_open(now):
                    today = datetime.now().strftime('%Y%m%d')
                    if '09:25:00' <= now < '09:30:00':
                        if self.st.get('_pre_market_done', '') != today:
                            _log('[盘前 {}] 集合竞价结束, 计算信号...'.format(now))
                            try:
                                self._daily_init()
                                self.st['_pre_market_done'] = today
                                signal = self.st.get('daily_signal')
                                if signal:
                                    self._print_signal_summary(signal)
                                    self._print_trading_plan(signal)
                            except Exception as e:
                                _log('[盘前异常] {}'.format(e))
                            self._last_heartbeat = now_ts
                            _time.sleep(5)
                            continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                            _log('[盘前 {}] 距开盘 {}'.format(now, cfg.time_to_open(now)))
                        _time.sleep(5)
                        continue
                    if self.st.get('trade_date', '') != today:
                        _log('[盘前] 新交易日 {}'.format(today))
                        try:
                            self._daily_init()
                            signal = self.st.get('daily_signal')
                            if signal:
                                self._print_signal_summary(signal)
                                self._print_trading_plan(signal)
                        except Exception as e:
                            _log('[异常] 初始化失败: {}'.format(e))
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        if now < '09:30:00':
                            _log('[等待] {}  距开盘 {}'.format(now, cfg.time_to_open(now)))
                        elif now > '15:00:00':
                            _log('[收盘] {}  策略待命'.format(now))
                        elif '11:30:00' < now < '13:00:00':
                            _log('[午休] {}'.format(now))
                    _time.sleep(10)
                    continue

                # ── 交易时段 ──
                fstate = self.st.get('fstate', STATE_IDLE)

                # ★ v19: DONE/FORCED 持续输出状态
                if fstate in (STATE_DONE, STATE_FORCED):
                    tick = self.ctx.get_full_tick([STOCK_QMT])
                    price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
                    if now_ts - self._last_heartbeat >= 30:
                        self._last_heartbeat = now_ts
                        tc_s = self.st.get('trade_count_short', 0)
                        tc_l = self.st.get('trade_count_long', 0)
                        _log('[状态] {} | Y{:.2f} | 反T{}/{} 正T{}/{} | 累计{}笔 ~Y{:,.0f}'.format(
                            fstate, price, tc_s, cfg.MAX_DAILY_TRADES,
                            tc_l, cfg.MAX_DAILY_TRADES, self.total_t_days, self.total_pnl))
                    if now < '14:57:00':
                        tc_s = self.st.get('trade_count_short', 0)
                        tc_l = self.st.get('trade_count_long', 0)
                        do_short = self.st.get('do_short', False)
                        do_long = self.st.get('do_long', False)
                        if ((do_short and tc_s < cfg.MAX_DAILY_TRADES) or
                            (do_long and tc_l < cfg.MAX_DAILY_TRADES)):
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

                if cfg.ENABLE_FORCE_CLOSE and now >= cfg.FORCE_CLOSE_TIME:
                    f = self.st['fstate']
                    if f in (STATE_SOLD, STATE_DIPPING):
                        _log('[尾盘 {}] 强制买回(反T)'.format(now))
                        self._force_buyback()
                    elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
                        _log('[尾盘 {}] 强制卖出(正T)'.format(now))
                        self._do_bt_force_sell()
                    elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE):
                        self.st['fstate'] = STATE_DONE

                if fstate == STATE_SOLD and not self.st.get('stop_loss_hit', False):
                    loss_limit = self.st['base_shares'] * signal['open_price'] * cfg.STOP_LOSS_PCT
                    if self.st.get('day_pnl', 0) < -loss_limit:
                        _log('[止损] 反T亏损超限')
                        self.st['stop_loss_hit'] = True
                        self._force_buyback()

                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts
                    self._heartbeat(price)

                _time.sleep(1)

        except KeyboardInterrupt:
            _log('用户中断')
        except Exception as e:
            _log('[异常] {}'.format(e))
            _traceback.print_exc()
        finally:
            self.conn.disconnect()
            fstate = self.st.get('fstate', '')
            if fstate in (STATE_SOLD, STATE_DIPPING):
                _log('[警告] 策略停止时有未买回头寸!')
            _log('{} v19 已停止 | 累计{}天 | 毛利~Y{:,.0f}'.format(
                STOCK_NAME, self.total_t_days, self.total_pnl))
            logger = get_logger()
            if logger is not None:
                _log('日志: ' + logger.log_path)
                logger.close()

    def _heartbeat(self, price):
        fs = self.st['fstate']
        sig = self.st.get('daily_signal', {})

        if fs in (STATE_DONE, STATE_FORCED):
            tc_s = self.st.get('trade_count_short', 0)
            tc_l = self.st.get('trade_count_long', 0)
            _log('[心跳] {} | Y{:.2f} | 反T{}/{} 正T{}/{} | 累计{}笔 ~Y{:,.0f}'.format(
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
                _log('[心跳] {} | Y{:.2f} | 卖Y{:.2f} {:+.1f}% | 买回线Y{:.2f}'.format(
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
                _log('[心跳] {} | Y{:.2f} | 买Y{:.2f} {:+.1f}% | 卖回线Y{:.2f}'.format(
                    fs, price, bp, chg, target))
        elif fs == STATE_BT_SPIKING:
            peak = self.st.get('bt_sell_peak_price', price)
            pb = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[心跳] {} | Y{:.2f} | peak=Y{:.2f} 回落{:.2f}%'.format(fs, price, peak, pb))
        else:
            _log('[心跳] {} | Y{:.2f}'.format(fs, price))


# ============================================================================
# 回测 & CLI
# ============================================================================

def run_backtest_mode(start='20250801', end='20260806'):
    print('\n' + '=' * 55)
    print('  回测 — QMT 迷你反T v19')
    print('  区间: {} ~ {}'.format(start, end))
    print('=' * 55 + '\n')
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from backtest.backtest_v10_xtdata import XTDataManager, BacktestEngine
    data_mgr = XTDataManager('601869.SH', data_dir='C:/QMT/datadir')
    data_mgr.load_daily(start=start, end=end)
    engine = BacktestEngine(data_mgr)
    engine.run(start_date=start, end_date=end)
    engine.print_report()
    engine.save_csv()


def main():
    parser = argparse.ArgumentParser(description='MiniQMT 迷你反T v19 — 精简日志')
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250801')
    parser.add_argument('--end', default='20260806')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_backtest_mode(args.start, args.end)
        return

    logger = FileLogger(STOCK_CODE, version='v19')
    set_logger(logger)
    print('★ 日志: {}'.format(logger.log_path))

    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n' + '!' * 55)
        print('  ⚠ 即将启动实盘自动交易!')
        print('  标的: {}({})  账号: {}'.format(STOCK_NAME, STOCK_CODE, ACCOUNT))
        print('  请确认: MiniQMT已启动 | 账号已登录 | 持有底仓')
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
