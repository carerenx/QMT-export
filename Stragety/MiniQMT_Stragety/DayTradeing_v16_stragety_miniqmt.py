# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — QMT day trading v16 + 盘前信号摘要 & 交易计划
================================================================================
 VS Code connect xtquant MiniQMT run v16 strategy。

 [v16 改动] (vs v15)
   ★ 盘前(09:25)打印完整信号计算结果 + 结构化交易计划
   ★ _print_signal_summary()  — 原始信号数据面板
   ★ _print_trading_plan()    — 今日交易计划 (反T/正T/风控)
   ★ 预初始化成功后立即输出, 不再等到 _print_signal() 的 guard 逻辑

 [v15 → v16 模块结构]
   core/config.py         → 策略参数常量 + 时间工具
   core/indicators.py     → 技术指标 (_sma/_atr/_rsi/_up_streak/...)
   core/signals.py        → 动态乘数模型 + 当日信号计算
   infra/logger.py        → 文件日志系统 (FileLogger)
   infra/connector.py     → MiniQMT连接 + QMT接口模拟
   ★ 本文件               → 只含 StrategyRunner 状态机 + 主循环 + CLI

 [pre condition]
   1. MiniQMT running (QMT → 右上角"极简mode")
   2. MiniQMT logged in with account 8890145315
   3. QMT has downloaded 601869 daily chart data
   4. pip install xtquant numpy pandas

 [run mode]
   mode1 — signal mode:
     python "Stragety/MiniQMT_Stragety/DayTradeing_v16_stragety_miniqmt.py" --mode signal
   mode2 — real mode:
     python "Stragety/MiniQMT_Stragety/DayTradeing_v16_stragety_miniqmt.py" --mode live
   mode3 — backtest mode:
     python "Stragety/MiniQMT_Stragety/DayTradeing_v16_stragety_miniqmt.py" --mode backtest

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

# ── 导入拆分的模块 ──
from core import config as cfg
from core.signals import compute_signal
from infra.logger import FileLogger, set_logger, get_logger, _log
from infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares,
    set_global_conn,
)

# ── 常用常量别名 ──
ACCOUNT       = cfg.ACCOUNT
STOCK_CODE    = cfg.STOCK_CODE
STOCK_NAME    = cfg.STOCK_NAME
STOCK_QMT     = cfg.STOCK_QMT
TRADE_LOT_SIZE = cfg.TRADE_LOT_SIZE

# 状态机常量
STATE_IDLE       = cfg.STATE_IDLE
STATE_SPIKING    = cfg.STATE_SPIKING
STATE_SOLD       = cfg.STATE_SOLD
STATE_DIPPING    = cfg.STATE_DIPPING
STATE_DONE       = cfg.STATE_DONE
STATE_FORCED     = cfg.STATE_FORCED
STATE_BT_DIPPING = cfg.STATE_BT_DIPPING
STATE_BT_BOUGHT  = cfg.STATE_BT_BOUGHT
STATE_BT_SPIKING = cfg.STATE_BT_SPIKING


# ============================================================================
# StrategyRunner — 核心策略状态机 + 主循环
# ============================================================================

class StrategyRunner:
    """MiniQMT 策略运行器 — 状态机 + 主循环 + 锁仓 + 正反T"""

    def __init__(self, dry_run=False):
        # ★ v16: 日志系统 (必须在其他操作之前)
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='v16')
            set_logger(logger)

        self.conn = MiniQMTConnector()
        set_global_conn(self.conn, dry_run)

        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = dry_run

        self._last_heartbeat = 0.0
        self._last_trade_date = ''
        self._signal_printed = False
        self._running = True

        self.total_t_days = 0
        self.total_pnl = 0.0
        self.day_pnl = 0.0

    # ── 状态初始化 ──

    def _init_state(self):
        """初始化策略状态字典"""
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
            'last_heartbeat': 0.0, 'last_fstate': '', '_last_logged_transition': '',
            'ontimer_errors': 0, 'callback_errors': 0,
            'order_pending': False, 'order_side': '',
            'order_signal_price': 0.0, 'order_sent_at': 0.0,
            'order_retries': 0, 'order_retry_logged': False,
            # v12: 锁仓
            'locked': False, 'lock_reason': '', 'lock_since': '',
            'lock_cooldown_until': 0.0, 'price_history': deque(),
            # v14/v16: 盘前预初始化
            '_pre_market_done': '',
        })

    def _reset_daily(self):
        """重置日内状态 (保留累计统计和底仓)"""
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
        self.st['bt_dip_price'] = 0.0
        self.st['bt_buy_trigger'] = 0.0
        self.st['bt_buy_fill_price'] = 0.0
        self.st['bt_sellback_target'] = 0.0
        self.st['bt_max_trail'] = 0.0
        self.st['locked'] = False
        self.st['lock_reason'] = ''
        self.st['lock_since'] = ''
        self.st['bt_sell_peak_price'] = 0.0
        self.st['_pre_market_done'] = ''

    # ── 每日初始化 ──

    def _daily_init(self):
        """每日信号计算 + 仓位评估 + 状态机重置"""
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

        # 获取日线数据
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

        # 修正开盘价 + 获取实时价 (一次 tick 调用拿两个字段)
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

        # 计算当日信号 (from core.signals)
        signal = compute_signal(
            opens_list, hist_high[STOCK_QMT],
            hist_low[STOCK_QMT], hist_close[STOCK_QMT], hist_volume[STOCK_QMT]
        )
        if signal is None:
            return

        # ── 仓位评估 + 方向决策 + 动态手数 ──
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
                reasons.append('资金不足(需Y{:,.0f}>Y{:,.0f})'.format(
                    curr_price_now * TRADE_LOT_SIZE * 1.01, avail_cash))
            if long_lots_sell < cfg.MIN_POSITION_LOTS:
                reasons.append('T+1:无可卖持仓(可用{}股)'.format(base_can_use))
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
        self.st['fstate']             = STATE_IDLE
        self.st['peak_price']         = 0.0
        self.st['dip_price']          = 0.0
        self.st['sell_fill_price']    = 0.0
        self.st['buyback_target']     = 0.0
        self.st['buyback_target_pct'] = 0.0
        self.st['day_pnl']            = 0.0
        self.st['stop_loss_hit']      = False
        self.st['state_enter_time']   = cfg.now_hms()
        self.st['sell_elapsed_bars']  = 0
        self.st['initialized']        = True
        self.st['bt_dip_price']       = 0.0
        self.st['bt_buy_trigger']     = 0.0
        self.st['bt_buy_fill_price']  = 0.0
        self.st['bt_sellback_target'] = 0.0
        self.st['bt_max_trail']       = 0.0
        self.st['locked']             = False
        self.st['lock_reason']        = ''
        self.st['lock_since']         = ''
        self.st['bt_sell_peak_price'] = 0.0

        # v16: 不再在这里调用 _print_signal(), 改由调用方在合适时机分别输出

    def _refresh_position(self):
        """刷新当前持仓和可用资金"""
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                break

    # ═══════════════════════════════════════════════════════════════
    # v16: 盘前信号摘要 & 交易计划 (新增)
    # ═══════════════════════════════════════════════════════════════

    def _print_signal_summary(self, signal):
        """
        ★ v16 新增: 打印原始信号计算结果摘要。

        在盘前预初始化 (09:25) 时调用, 展示所有计算出的指标和因子。
        """
        _log('')
        _log('┌' + '─' * 58 + '┐')
        _log('│  📊 信号计算结果摘要' + ' ' * 40 + '│')
        _log('├' + '─' * 58 + '┤')

        # 趋势 & 市场环境
        trend = signal.get('trend', '?')
        trend_labels = {
            'strong_bull': '强牛 🟢', 'weak_bull': '弱牛 🟡',
            'sideways': '震荡 ⚪', 'bear': '熊市 🔴'
        }
        _log('│  趋势判定: {:28s}  连涨: {}天  │'.format(
            trend_labels.get(trend, trend), signal.get('up_streak', 0)))

        # 价格 & ATR
        open_p = signal.get('open_price', 0)
        close_y = signal.get('close_yday', 0)
        atr_v = signal.get('atr', 0)
        atr_p = signal.get('atr_pct', 0)
        atr_ratio = signal.get('atr_ratio', 0)
        _log('│  开盘: Y{:,.2f}         昨收: Y{:,.2f}         涨跌: {:+.2f}%  │'.format(
            open_p, close_y, (open_p / close_y - 1) * 100 if close_y else 0))
        _log('│  ATR: Y{:.2f} ({:.2f}%)    ATR/MA20: {:.2f}                    │'.format(
            atr_v, atr_p * 100, atr_ratio))

        # RSI & 量比
        rsi_v = signal.get('rsi', 0)
        vol_r = signal.get('vol_ratio', 0)
        _log('│  RSI: {:.1f}                   量比: {:.2f}                        │'.format(
            rsi_v, vol_r))

        # 动态乘数
        sell_mult = signal.get('sell_mult', 0)
        base_mult = signal.get('sell_mult_base', 0)
        _log('│  卖出乘数: {:.2f}  (基础: {:.2f})                              │'.format(
            sell_mult, base_mult))

        # 因子详情
        factor_details = signal.get('factor_details', {})
        if factor_details:
            factor_strs = []
            for name, dev in factor_details.items():
                sign = '+' if dev >= 0 else ''
                factor_strs.append('{}{:.2f}'.format(sign, dev))
            _log('│  因子偏差: {}                        │'.format(
                ' | '.join('{}: {}'.format(k, ('+' if v >= 0 else '') + '{:.2f}'.format(v))
                           for k, v in factor_details.items())))

        # 卖出触发线
        sell_trig = signal.get('sell_trigger', 0)
        sell_trig_raw = signal.get('sell_trigger_raw', 0)
        range_capped = signal.get('range_capped', False)
        _log('│  反T触发线: Y{:.2f}  (原始: Y{:.2f})        {}  │'.format(
            sell_trig, sell_trig_raw, '⚠ 振幅约束' if range_capped else ''))

        # 禁止信号 (如有)
        if not signal.get('do_short', True):
            reason = signal.get('blocked_reason', '未知')
            _log('│  ⛔ 反T已禁止: {:<43s} │'.format(reason))

        _log('└' + '─' * 58 + '┘')

    def _print_trading_plan(self, signal):
        """
        ★ v16 新增: 打印结构化交易计划。

        在盘前预初始化 (09:25) 时调用, 明确今日操作方向、触发线、风控。
        """
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

        # 账户快照
        _log('│  持仓: {:>5}股  Y{:>10,.0f}  ({:.0f}%)                       │'.format(
            base_shares, pos_value, pos_pct))
        _log('│  可用: Y{:>12,.0f}    总资产: Y{:>12,.0f}              │'.format(
            avail_cash, total_asset))
        _log('│  T+0可卖: {:>3}股 ({}手)   T+1锁定: {:>3}股                       │'.format(
            base_can_use, base_can_use // TRADE_LOT_SIZE, base_shares - base_can_use))
        _log('├' + '─' * 58 + '┤')

        # ═══ 反T 计划 ═══
        do_short = self.st.get('do_short', False)
        short_lots = self.st.get('short_lots', 0)

        if do_short:
            sell_trig = signal.get('sell_trigger', 0)
            open_p = signal.get('open_price', 0)
            atr_v = signal.get('atr', 0)
            sell_mult = signal.get('sell_mult', 0)

            _log('│  📉 反T (先卖后买) — ✅ 可用  {}手                         │'.format(short_lots))
            _log('│     ├─ 卖出触发: Y{:.2f}  (开盘 + ATR{} × {})           │'.format(
                sell_trig, '{:.2f}'.format(atr_v) if atr_v < 1000 else '{:.0f}'.format(atr_v), sell_mult))
            _log('│     ├─ 卖出确认: 回落 ≥ {:.2f}%                               │'.format(
                cfg.PULLBACK_PCT * 100))
            _log('│     ├─ 买回触发: 卖价 × (1 - ATR% × {:.2f})                  │'.format(
                cfg.BUYBACK_TRIGGER_MULT))
            _log('│     ├─ 买回确认: 回升 ≥ {:.2f}%                               │'.format(
                cfg.BOUNCE_PCT * 100))
            _log('│     ├─ 紧急买回: 卖价 +{:.1f}%                                  │'.format(
                cfg.EMERGENCY_BUYBACK_PCT * 100))
            _log('│     ├─ 止损线: -{:.1f}%                                        │'.format(
                cfg.STOP_LOSS_PCT * 100))
            if cfg.ENABLE_FORCE_CLOSE:
                _log('│     └─ 尾盘强平: {}                                    │'.format(
                    cfg.FORCE_CLOSE_TIME))
            else:
                _log('│     └─ 尾盘强平: 已禁用                                    │')
            _log('│     📝 示例: 若卖Y{:.2f}, 买回约Y{:.2f}                        │'.format(
                sell_trig,
                round(sell_trig * (1.0 - signal['atr_pct'] * cfg.BUYBACK_TRIGGER_MULT), 2)))
        else:
            reason = signal.get('short_reason', signal.get('blocked_reason', '未知'))
            _log('│  📉 反T (先卖后买) — ❌ 不可用                                │')
            _log('│     原因: {:<50s} │'.format(reason))

        _log('├' + '─' * 58 + '┤')

        # ═══ 正T 计划 ═══
        do_long = self.st.get('do_long', False)
        long_lots = self.st.get('long_lots', 0)

        if do_long:
            buy_trig = signal.get('buy_trigger', 0)
            buy_floor = signal.get('buy_trigger_floor', 0)
            buy_trail = signal.get('buy_trigger_trail', 0)
            sell_hint = signal.get('sellback_target_hint', 0)
            open_p = signal.get('open_price', 0)

            _log('│  📈 正T (先买后卖) — ✅ 可用  {}手                         │'.format(long_lots))
            _log('│     ├─ 买入触发: Y{:.2f}  (动态跟踪)                          │'.format(buy_trig))
            _log('│     │   底线(固定): Y{:.2f}  (开盘 - {:.1f}%)                  │'.format(
                buy_floor, cfg.BUY_TRIGGER_PCT * 100))
            _log('│     │   跟随(动态): Y{:.2f}  (当前价 - {:.1f}%)               │'.format(
                buy_trail, cfg.BUY_TRIGGER_TRAIL * 100))
            _log('│     ├─ 买入确认: 回升 ≥ {:.2f}%                               │'.format(
                cfg.BOUNCE_PCT * 100))
            _log('│     ├─ 卖出触发: Y{:.2f}  (买价 + {:.1f}%)                    │'.format(
                sell_hint, cfg.SELLBACK_RISE_PCT * 100))
            _log('│     ├─ 卖出确认: 回落 ≥ {:.2f}%                               │'.format(
                cfg.PULLBACK_PCT * 100))
            _log('│     ├─ 止损线: 买价 -{:.1f}%                                   │'.format(
                cfg.STOP_LOSS_PCT * 100))
            _log('│     ├─ 1手资金: Y{:,.0f}  (可用 Y{:,.0f})                        │'.format(
                curr_price * TRADE_LOT_SIZE, avail_cash))
            if cfg.ENABLE_FORCE_CLOSE:
                _log('│     └─ 尾盘强平: {}                                    │'.format(
                    cfg.FORCE_CLOSE_TIME))
            else:
                _log('│     └─ 尾盘强平: 已禁用                                    │')
        else:
            reason = self.st.get('long_reason', '未知')
            _log('│  📈 正T (先买后卖) — ❌ 不可用                                │')
            _log('│     原因: {:<50s} │'.format(reason))
            _log('│     1手资金: Y{:,.0f}  (可用 Y{:,.0f})                        │'.format(
                curr_price * TRADE_LOT_SIZE, avail_cash))

        _log('├' + '─' * 58 + '┤')

        # ═══ 风险提示 ═══
        trend = signal.get('trend', 'sideways')
        if trend == 'strong_bull':
            _log('│  ⚠ 风险: 强牛行情, 反T已自动禁用                            │')
        elif trend == 'bear':
            _log('│  ⚠ 风险: 熊市, 正T风险较高, 优先反T                          │')
        elif pos_pct > 80:
            _log('│  ⚠ 风险: 仓位过重({:.0f}%), 不建议加仓                        │'.format(pos_pct))

        _log('│  📋 状态监控: 反T={} 正T={} 锁仓=False 最大{}/{}次                 │'.format(
            '✅' if do_short else '❌',
            '✅' if do_long else '❌',
            cfg.MAX_DAILY_TRADES, cfg.MAX_DAILY_TRADES))
        _log('└' + '─' * 58 + '┘')
        _log('')

        # 累计统计
        if self.total_t_days > 0:
            _log('  📈 累计: {}笔 T+0  |  毛利 ~Y{:,.0f}'.format(self.total_t_days, self.total_pnl))
            _log('')

    # ═══════════════════════════════════════════════════════════════
    # 状态机处理器 (从 v15 移植, 未改动)
    # ═══════════════════════════════════════════════════════════════

    def _handle_idle(self, price):
        st = self.st
        signal = st.get('daily_signal', {})

        # ── 反T ──
        if st.get('do_short', False):
            trigger = signal.get('sell_trigger', 999999)
            if price >= trigger:
                can_use = st.get('base_can_use', st['base_shares'])
                if can_use < TRADE_LOT_SIZE:
                    _log(f'[反T跳过] 可用{can_use}股 < 1手')
                    return
                tc = st.get('trade_count_short', 0)
                if tc >= cfg.MAX_DAILY_TRADES:
                    _log('[反T跳过] 已达单日上限{}次'.format(cfg.MAX_DAILY_TRADES))
                    return
                if st.get('locked', False):
                    _log('[反T锁定] 盘面强势({}), 跳过'.format(st.get('lock_reason', '')))
                    return

                st['trade_count_short'] = tc + 1
                st['fstate'] = STATE_SPIKING
                st['peak_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[反T冲高#{}/{}] Y{:.2f} >= Y{:.2f}'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, trigger))
                return

        # ── 正T ──
        if st.get('do_long', False):
            buy_trigger = signal.get('buy_trigger', 0)
            if price <= buy_trigger:
                tc = st.get('trade_count_long', 0)
                if tc >= cfg.MAX_DAILY_TRADES:
                    _log('[正T跳过] 已达单日上限{}次'.format(cfg.MAX_DAILY_TRADES))
                    return

                st['trade_count_long'] = tc + 1
                st['fstate'] = STATE_BT_DIPPING
                st['bt_dip_price'] = price
                st['bt_buy_trigger'] = buy_trigger
                st['state_enter_time'] = cfg.now_hms()
                under_pct = (buy_trigger - price) / buy_trigger * 100
                _log('[正T探底#{}/{}] Y{:.2f} <= Y{:.2f}(-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, buy_trigger, under_pct))
                return

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
            st['fstate'] = STATE_SOLD
            st['state_enter_time'] = cfg.now_hms()

            _log(f'  买回触发线: Y{buyback_target:.2f} (卖价-{buyback_pct*100:.2f}%)')
            _log(f'  紧急买回线: Y{price*(1+cfg.EMERGENCY_BUYBACK_PCT):.2f} (卖价+{cfg.EMERGENCY_BUYBACK_PCT*100:.1f}%)')

            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)

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
            tightened_bt = sp * (
                1.0 - st['daily_signal']['atr_pct'] * cfg.BUYBACK_TRIGGER_MULT * cfg.BUYBACK_TIGHTEN_MULT
            )
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
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
        self.st['fstate'] = STATE_DONE
        _log(f'  >>> 下单买回({reason}): Y{price:.2f} x {TRADE_LOT_SIZE}股')

    def _force_buyback(self):
        _log(f'[强制买回] 对手价 x {TRADE_LOT_SIZE}股')
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
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
                _log(f'[正T买入失败] 资金不足 (需Y{need:,.0f} > Y{avail:,.0f})')
                st['fstate'] = STATE_IDLE
                return

            order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            st['fstate'] = STATE_BT_BOUGHT
            st['bt_buy_fill_price'] = price
            st['bt_sellback_target'] = round(price * (1.0 + cfg.SELLBACK_RISE_PCT), 2)
            _log(f'  买价Y{price:.2f} | 卖回触发线: Y{st["bt_sellback_target"]:.2f}(+{cfg.SELLBACK_RISE_PCT*100:.1f}%)')

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
            order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', price, self.ctx, ACCOUNT)
            st['fstate'] = STATE_DONE
            self.total_t_days += 1
            self.total_pnl += gross

    def _do_bt_force_sell(self):
        _log(f'[正T强制卖出] 对手价 x {TRADE_LOT_SIZE}股')
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, self.ctx, ACCOUNT)
        self.st['fstate'] = STATE_FORCED

    # ── v12: 锁仓强度评估 ──

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
    # 主循环
    # ═══════════════════════════════════════════════════════════════

    def run(self):
        """启动策略主循环"""
        set_global_conn(self.conn, self.dry_run)

        # 连接 MiniQMT
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
        _log(f'{STOCK_NAME} 迷你反T v16 MiniQMT版 启动')
        _log(f'  mode: {"信号监控(不下单)" if self.dry_run else "实盘交易"}')
        _log(f'  标的: {STOCK_NAME}({STOCK_QMT})')
        _log(f'  触发BASE: bear={cfg.SELL_TRIGGER_BASE_BEAR} sideways={cfg.SELL_TRIGGER_BASE_SIDEWAYS} weak_bull={cfg.SELL_TRIGGER_BASE_WEAK_BULL}')
        _log(f'  ★ 日志文件: {get_logger().log_path if get_logger() else "(未初始化)"}')
        _log(f'  ★ 模块: core/ + infra/ 拆分结构 (v16 盘前摘要增强)')

        try:
            self._daily_init()
            # v16: 首次初始化后立即打印信号摘要和交易计划
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

                    # ★ v16: 09:25 预初始化 — 打印信号摘要 + 交易计划
                    if '09:25:00' <= now < '09:30:00':
                        if self.st.get('_pre_market_done', '') != today:
                            _log(f'\n[盘前预初始化] {now} 集合竞价结束, 提前计算信号...')
                            try:
                                self._daily_init()
                                self.st['_pre_market_done'] = today

                                # ★ v16: 核心改进 — 打印信号结果 & 交易计划
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

                    # 跨日检测
                    if self.st.get('trade_date', '') != today:
                        _log(f'[盘前] 新交易日 {today}, 初始化...')
                        try:
                            self._daily_init()
                            # v16: 跨日初始化后打印
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

                # ── 交易时段 ──
                fstate = self.st.get('fstate', STATE_IDLE)
                if fstate in (STATE_DONE, STATE_FORCED):
                    _time.sleep(5)
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
                        reasons = []
                        if signal and not do_short:
                            reasons.append('反T:' + (signal.get('short_reason') or signal.get('blocked_reason', '禁止')))
                        if not do_long:
                            reasons.append('正T:' + self.st.get('long_reason', '禁止'))
                        _log('[待命] Y{:.2f} | {}'.format(price, '  |  '.join(reasons)))
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
            _log(f'{STOCK_NAME} v16 MiniQMT版 已停止 | 累计 {self.total_t_days}天 | 毛利~Y{self.total_pnl:,.0f}')

            logger = get_logger()
            if logger is not None:
                _log('★ 日志已保存至: ' + logger.log_path)
                logger.close()

    def _heartbeat(self, price):
        """每分钟心跳日志"""
        fs = self.st['fstate']
        sig = self.st.get('daily_signal', {})

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
                dist = st_trig - price
                parts.append('反T: 需涨{:.2f}至Y{:.2f}触发卖出'.format(dist, st_trig))
            else:
                reason = sig.get('short_reason', sig.get('blocked_reason', '禁止'))
                parts.append('反T: 禁止({})'.format(reason))

            if self.st.get('do_long'):
                bt_dyn = sig.get('buy_trigger', 0)
                dist = price - bt_dyn
                parts.append('正T: 需跌{:.2f}至Y{:.2f}触发买入'.format(dist, bt_dyn))
            else:
                parts.append('正T: 禁止({})'.format(self.st.get('long_reason', '资金不足')))

            if self.st.get('locked'):
                parts.append('锁仓: {}'.format(self.st.get('lock_reason', '')))
            _log('[心跳] {} | Y{:.2f} | {}'.format(fs, price, '  |  '.join(parts)))

        elif fs == STATE_SPIKING:
            peak = self.st.get('peak_price', 0)
            pullback = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[心跳] {} | Y{:.2f} | peak=Y{:.2f} 回落{:.2f}%(需>={:.2f}%)'.format(
                fs, price, peak, pullback, cfg.PULLBACK_PCT * 100))

        elif fs in (STATE_SOLD, STATE_DIPPING):
            sp = self.st.get('sell_fill_price', 0)
            bt = self.st.get('buyback_target', 0)
            if sp > 0:
                chg = (price - sp) / sp * 100
                _log('[心跳] {} | Y{:.2f} | 卖Y{:.2f} 现{}{:.2f}% | 买回线Y{:.2f}'.format(
                    fs, price, sp, '+' if chg >= 0 else '', chg, bt))

        elif fs == STATE_BT_DIPPING:
            dip = self.st.get('bt_dip_price', price)
            bounce = (price - dip) / dip * 100 if dip > 0 else 0
            _log('[心跳] {} | Y{:.2f} | dip=Y{:.2f} 回升{:.2f}%(需>={:.2f}%)'.format(
                fs, price, dip, bounce, cfg.BOUNCE_PCT * 100))

        elif fs == STATE_BT_BOUGHT:
            bp = self.st.get('bt_buy_fill_price', 0)
            target = self.st.get('bt_sellback_target', 0)
            if bp > 0:
                chg = (price - bp) / bp * 100
                to_target = target - price
                stop_price = bp * (1.0 - cfg.STOP_LOSS_PCT)
                _log('[心跳] {} | Y{:.2f} | 买Y{:.2f} 现{}{:.2f}% | 卖出线Y{:.2f}(需涨{:.2f}) | 止损线Y{:.2f} | 尾盘{}强平'.format(
                    fs, price, bp, '+' if chg >= 0 else '', chg, target, to_target, stop_price, cfg.FORCE_CLOSE_TIME))

        elif fs == STATE_BT_SPIKING:
            peak = self.st.get('bt_sell_peak_price', price)
            pullback = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[心跳] {} | Y{:.2f} | peak=Y{:.2f} 回落{:.2f}%(需>={:.2f}%)'.format(
                fs, price, peak, pullback, cfg.PULLBACK_PCT * 100))

        elif fs in (STATE_DONE, STATE_FORCED):
            _log('[心跳] {} | 今日交易已完成, 等待下一交易日'.format(fs))

        else:
            _log('[心跳] {} | Y{:.2f}'.format(fs, price))


# ============================================================================
# 回测mode
# ============================================================================

def run_backtest_mode(start='20250801', end='20260806'):
    """使用 xtdata 本地数据进行回测 (不需要 MiniQMT)"""
    print(f'\n{"="*55}')
    print(f'  回测mode — QMT 迷你反T v16 (模块化)')
    print(f'  数据源: xtdata.get_local_data() -> QMT本地.DAT')
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


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT 迷你反T v16 策略 — 盘前信号摘要增强版',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python "Stragety/MiniQMT_Stragety/DayTradeing_v16_stragety_miniqmt.py" --mode signal
  python "Stragety/MiniQMT_Stragety/DayTradeing_v16_stragety_miniqmt.py" --mode live
  python "Stragety/MiniQMT_Stragety/DayTradeing_v16_stragety_miniqmt.py" --mode backtest

v16 改动 (vs v15):
  ★ 盘前(09:25)打印完整信号计算结果 + 结构化交易计划
  ★ _print_signal_summary()  — 原始信号数据面板
  ★ _print_trading_plan()    — 今日交易计划 (反T/正T/风控)

前置条件:
  1. 启动 MiniQMT: QMT -> 右上角"极简mode"
  2. MiniQMT 中已登录资金账号 8890145315
  3. QMT 已下载 601869 历史数据
        """
    )
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live', 'backtest'],
                        help='运行mode (默认: signal)')
    parser.add_argument('--start', default='20250801', help='回测开始日期 YYYYMMDD')
    parser.add_argument('--end', default='20260806', help='回测结束日期 YYYYMMDD')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_backtest_mode(args.start, args.end)
        return

    # 初始化日志系统
    logger = FileLogger(STOCK_CODE, version='v16')
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
