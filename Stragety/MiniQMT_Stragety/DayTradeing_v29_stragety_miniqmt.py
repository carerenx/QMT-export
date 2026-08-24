# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT — QMT day trading v29 + 阶梯加仓机制 + 严格成交判定 + sell_trigger已消耗更新
================================================================================

 [v29 改动] (vs v28)
   ★ MOM短线机制屏蔽 — 新增 MOM_ENABLED=False 开关, 完全屏蔽短线动量反转机制:
     不采集tick价格历史、不下单、不打印心跳, 仅保留主状态机(反T/正T)

 [v28 改动] (vs v27)
   ★ sell_trigger 已消耗更新 — 反T交易完成后(买回→IDLE), 将 sell_trigger 提升至
     实际卖出价(sell_fill_price), 避免心跳继续显示已成交过的旧阈值

 [v27 改动] (vs v26)
   ★ 紧急买回开关 — 短线动量反T的紧急买回机制(反涨3%止损买回)增加开关
     MOM_EMERGENCY_BUYBACK_ENABLED, 默认 False (关闭)

 [v26 改动] (vs v25)
   ★ 触发幅度自适应 — 2分钟涨跌阈值由固定2%改为 2×最近10分钟ATR,
     随波动率自动缩放 (低波动日阈值收窄、高波动日阈值放宽), 并设上下限
     MOM_TRIGGER_MIN_PCT=1% / MOM_TRIGGER_MAX_PCT=6% 防极端

 [v25 改动] (vs v24)
   ★ 短线动量反转机制 — 新增独立于日线信号的事件驱动机制:
     · 2分钟涨≥阈值 → 冲高回落卖出(回落后卖1手) → 卖价跌1.2% → 探底回升买入(回升后买回)
     · 2分钟跌≥阈值 → 探底回升买入(回升后买1手) → 买价涨1.5% → 冲高回落卖出(回落后卖出)
     · 单独手数 MOM_LOT_SIZE=1手(200股), 单独日交易上限 MOM_MAX_DAILY_TRADES
     · 与主机制并行独立运行, 下单前同样做仓位/现金钳制, 尾盘强制平掉短线腿

 [v24 改动] (vs v23)
   ★ 心跳日志优化 — [HB] IDLE 行当价格已越过监控触发价时, 在 need 后打印
     ! 提示已触发 (REV-T 涨超卖点 / FWD-T 跌破买点), 并修正 need 值正负号显示

 [v23 改动] (vs v22)
   ★ 严格成交判定 — _wait_for_fill 只在"满额成交"时判成功, 返回
     FILLED / PARTIAL / TIMEOUT 三态; 部分成交/未成交不再被误判为成功
   ★ 下单前仓位检查 — 卖出按"可卖数量"、买入按"现金可买数量"钳制下单量,
     仓位小于计划量时以实际仓位下单 (不再只按整手跳过)
   ★ 腿按"实际成交股数"记账 — short_legs / long_legs 由 [价] 改为 [(价,股数)],
     买回/卖出按实际股数平仓, 毛利按 Σ(价差×股数) 计算
   ★ 未成交/部分成交恢复 — 卖出未成交回 IDLE, 买回未成交保持 SOLD 继续监控;
     超时自动撤单, 避免挂单残留导致意外成交

 [v22 改动] (vs v21)
   ★ 反T阶梯加卖 — 卖出成功后, 若价格继续涨至"卖价×(1+阶梯幅度)", 则进入
     冲高回落监测, 追加卖出下一手(向上分批卖出)
   ★ 正T阶梯加买 — 买入成功后, 若价格继续跌至"买价×(1-阶梯幅度)", 则进入
     探底回升监测, 追加买入下一手(向下分批买入)
   ★ 多腿仓位管理 — 追加的每手计入未平仓腿, 买回/卖出时一次性平掉全部腿

 [run mode]
 python "Stragety/MiniQMT_Stragety/DayTradeing_v29_stragety_miniqmt.py" --mode signal
 python "Stragety/MiniQMT_Stragety/DayTradeing_v29_stragety_miniqmt.py" --mode live
 python "Stragety/MiniQMT_Stragety/DayTradeing_v29_stragety_miniqmt.py" --mode backtest

================================================================================
"""
import os, sys, time as _time, argparse, traceback as _traceback
from collections import deque
from datetime import datetime, timedelta
from typing import Optional
import numpy as np, pandas as pd

from core import config as cfg
from core.signals import compute_signal
from infra.logger import FileLogger, set_logger, get_logger, _log
from infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares, set_global_conn,
)

ACCOUNT = cfg.ACCOUNT; STOCK_CODE = cfg.STOCK_CODE; STOCK_NAME = cfg.STOCK_NAME
STOCK_QMT = cfg.STOCK_QMT; TRADE_LOT_SIZE = cfg.TRADE_LOT_SIZE
STATE_IDLE = cfg.STATE_IDLE; STATE_SPIKING = cfg.STATE_SPIKING
STATE_SOLD = cfg.STATE_SOLD; STATE_DIPPING = cfg.STATE_DIPPING
STATE_DONE = cfg.STATE_DONE; STATE_FORCED = cfg.STATE_FORCED
STATE_BT_DIPPING = cfg.STATE_BT_DIPPING; STATE_BT_BOUGHT = cfg.STATE_BT_BOUGHT
STATE_BT_SPIKING = cfg.STATE_BT_SPIKING

# ★ v22: 阶梯加仓/减仓参数 (成功卖出/买入后, 在成交价基础上加减价监测追加)
LADDER_UP_STEP_PCT   = 0.015   # 反T: 卖出后价格再涨 +1.5% → 追加冲高回落卖出
LADDER_DOWN_STEP_PCT = 0.015   # 正T: 买入后价格再跌 -1.5% → 追加探底回升买入

# ★ v23: 成交判定
FILL_TIMEOUT_SEC = 8.0   # 等待满额成交的超时秒数

# ★ v25/v26: 短线动量反转机制 (2分钟事件驱动, 独立于日线信号)
MOM_ENABLED           = True
MOM_WINDOW_SEC        = 120           # 检测窗口: 2分钟内价格变化
MOM_ATR_WINDOW_SEC    = 600           # ATR计算窗口: 最近10分钟
MOM_ATR_MULT          = 3.5           # 触发幅度 = 2 × 最近10分钟ATR (自适应)
MOM_TRIGGER_MIN_PCT   = 0.01          # 触发幅度下限 1% (防超低波动日过度触发)
MOM_TRIGGER_MAX_PCT   = 0.06          # 触发幅度上限 6% (防超高波动日无法触发)
MOM_SHORT_BUYBACK_PCT = 0.015         # 反T: 卖出后价格跌1.2% → 触发探底回升买入
MOM_LONG_SELLBACK_PCT = 0.018         # 正T: 买入后价格涨1.5% → 触发冲高回落卖出
MOM_LOT_SIZE          = cfg.TRADE_LOT_SIZE_MOM  # 短线机制单独手数 = 1手 (100股)
MOM_MAX_DAILY_TRADES  = 3             # 短线机制当日最大开腿次数(防过度交易)
# ★ v27: 紧急买回开关 — 反T卖后价格反涨超卖价3%时强制买回止损
MOM_EMERGENCY_BUYBACK_ENABLED = False # 默认关闭; True=开启紧急买回止损
# ★ v29: MOM机制总开关 — False=完全屏蔽短线动量反转, 不采集tick不下单



class StrategyRunner:
    """MiniQMT v29 — 阶梯加仓 + 严格成交判定 + 下单前仓位检查 + sell_trigger已消耗更新 (MOM短线屏蔽机制)"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='v29')
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
            'state_enter_time': '', 'sell_elapsed_bars': 0,
            'locked': False, 'lock_reason': '', 'lock_since': '',
            'lock_cooldown_until': 0.0, 'price_history': deque(),
            '_pre_market_done': '', '_market_open_logged': False,
            # ★ v22/v23: 阶梯加仓/减仓状态 — 腿记录为 (成交价, 成交股数)
            'ladder_sell_target': 0.0, 'ladder_buy_target': 0.0,
            'ladder_sold_count': 0, 'ladder_bought_count': 0,
            'short_legs': [], 'long_legs': [],
            # ★ v25: 短线动量反转机制状态 (独立于主状态机)
            'mom_state': 'MOM_IDLE', 'mom_peak': 0.0, 'mom_dip': 0.0,
            'mom_sell_price': 0.0, 'mom_buy_price': 0.0,
            'mom_leg_shares': 0, 'mom_trade_count': 0,
            'mom_price_history': deque(), 'mom_last_hb': 0.0,
            # ★ v26: 自适应ATR — 10分钟价格窗口 + 当前触发幅度(2×ATR)
            'mom_atr_history': deque(), 'mom_trigger_pct': 0.0,
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
        self.conn.refresh_daily_cache()
        hist_close  = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'close')
        hist_open   = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'open')
        hist_high   = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'high')
        hist_low    = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'low')
        hist_volume = self.ctx.get_history_data(cfg.HIST_DATA_LEN, '1d', 'volume')
        if STOCK_QMT not in hist_close or len(hist_close[STOCK_QMT]) < 60:
            return
        self._refresh_position()
        base_shares = self.st.get('base_shares', 0); base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('entry_price', 0) == 0.0:
            self.st['entry_price'] = self.st.get('base_cost', 0.0)

        tick_now = self.ctx.get_full_tick([STOCK_QMT])
        tick_data = tick_now.get(STOCK_QMT, {})
        today_open = tick_data.get('open', 0); curr_price_now = tick_data.get('lastPrice', 0)
        last_close = tick_data.get('lastClose', 0)   # ★ v22: 昨收(真实价, 与今开同源)
        opens_list = list(hist_open[STOCK_QMT])
        # ★ 调试: 打印历史open末尾 + tick今开, 排查数据源异常
        _log('[DATA-DBG] hist_open[-3:]= {} | tick_open={} | lastClose={}'.format(
            opens_list[-3:] if len(opens_list) >= 3 else opens_list, today_open, last_close))
        if today_open > 0 and len(opens_list) > 0:
            opens_list[-1] = today_open

        signal = compute_signal(opens_list, hist_high[STOCK_QMT], hist_low[STOCK_QMT],
                                hist_close[STOCK_QMT], hist_volume[STOCK_QMT],
                                yesterday_close=last_close)
        if signal is None: return
        open_price = signal['open_price']
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0
        if curr_price_now <= 0: curr_price_now = open_price
        pos_value = base_shares * curr_price_now
        total_asset = pos_value + avail_cash
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0
        short_lots = min(base_can_use // TRADE_LOT_SIZE, cfg.MAX_DAILY_TRADES)
        long_lots_cash = int(avail_cash / (curr_price_now * TRADE_LOT_SIZE * 1.01))
        long_lots_sell = base_can_use // TRADE_LOT_SIZE
        long_lots = min(long_lots_cash, long_lots_sell, cfg.MAX_DAILY_TRADES)

        do_short = signal['do_short'] and (short_lots >= cfg.MIN_POSITION_LOTS)
        short_reason = ''
        if not signal['do_short']: short_reason = signal.get('blocked_reason', 'signal blocked')
        elif short_lots < cfg.MIN_POSITION_LOTS: short_reason = 'available {} sh < {} lots'.format(base_can_use, cfg.MIN_POSITION_LOTS)
        do_long = long_lots >= cfg.MIN_POSITION_LOTS
        long_reason = ''
        if not do_long:
            reasons = []
            if long_lots_cash < cfg.MIN_POSITION_LOTS: reasons.append('insufficient cash')
            if long_lots_sell < cfg.MIN_POSITION_LOTS: reasons.append('T+1: no sellable shares')
            long_reason = '; '.join(reasons) if reasons else 'unknown'

        buy_trigger_floor = round(open_price * (1.0 - cfg.BUY_TRIGGER_PCT), 2)
        buy_trigger_trail = round(curr_price_now * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
        buy_trigger = max(buy_trigger_floor, buy_trigger_trail)
        sellback_target_hint = round(buy_trigger * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

        signal['do_short'] = do_short; signal['short_reason'] = short_reason
        signal['buy_trigger'] = buy_trigger; signal['buy_trigger_floor'] = buy_trigger_floor
        signal['buy_trigger_trail'] = buy_trigger_trail
        signal['sellback_target_hint'] = sellback_target_hint

        self.st['daily_signal'] = signal
        self.st['do_short'] = do_short; self.st['do_long'] = do_long
        self.st['long_reason'] = long_reason; self.st['short_lots'] = short_lots
        self.st['long_lots'] = long_lots; self.st['pos_value'] = pos_value
        self.st['pos_pct'] = pos_pct; self.st['avail_cash'] = avail_cash
        self.st['trade_count_short'] = 0; self.st['trade_count_long'] = 0

        for k in ('fstate', 'peak_price', 'dip_price', 'sell_fill_price', 'buyback_target', 'buyback_target_pct'):
            self.st[k] = STATE_IDLE if k == 'fstate' else 0.0
        self.st['day_pnl'] = 0.0; self.st['stop_loss_hit'] = False
        self.st['state_enter_time'] = cfg.now_hms(); self.st['sell_elapsed_bars'] = 0
        self.st['initialized'] = True
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
            self.st[k] = 0.0
        self.st['locked'] = False; self.st['lock_reason'] = ''; self.st['lock_since'] = ''

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                break

    # ═══ v23: 下单前仓位/现金检查 + 严格成交判定 ═══

    def _cur_price(self):
        tick = self.ctx.get_full_tick([STOCK_QMT])
        price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if price <= 0:
            price = self.st.get('daily_signal', {}).get('open_price', 0)
        return price

    def _available_cash(self):
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return account[0].m_dAvailable if account else 0.0

    def _clamp_sell_shares(self, planned):
        """卖出前检查可卖仓位: 实际可卖 = min(计划, base_can_use)。"""
        can_use = self.st.get('base_can_use', 0)
        if can_use <= 0:
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

    def _submit_order(self, shares, price, label):
        """下单 + 等待成交。shares>0 买入, <0 卖出。

        下单前检查可用仓位(卖出)/现金(买入), 以实际可下单数量下单。
        返回 (status, actual_delta):
          status: 'FILLED' 满额 | 'PARTIAL' 部分 | 'TIMEOUT' 未成交 | 'SKIP' 无可用
          actual_delta: 实际成交股数(带符号, 买正卖负)
        """
        side = 'SELL' if shares < 0 else 'BUY'
        planned = abs(shares)
        if side == 'SELL':
            actual = self._clamp_sell_shares(planned)
        else:
            actual = self._clamp_buy_shares(planned, price)
        if actual < cfg.MIN_LOT:
            _log('[{} SKIP] {} 不足: planned {} actual {}'.format(
                label, '可卖' if side == 'SELL' else '现金', planned, actual))
            return 'SKIP', 0
        signed = -actual if side == 'SELL' else actual
        snap = self._snapshot_account()
        price_str = 'MKT' if price <= 0 else 'Y{:.2f}'.format(price)
        _log('[ORDER-{}] {} × {} sh'.format(label, price_str, actual))
        order_shares(STOCK_QMT, signed, 'COMPETE', price, self.ctx, ACCOUNT)
        return self._wait_for_fill(snap, signed, label, price, signed)

    # ═══ 成交确认 ═══

    def _wait_for_fill(self, snap_before, expected_shares_delta,
                       label, trade_price, trade_shares, timeout_sec=FILL_TIMEOUT_SEC):
        """严格等待满额成交。返回 (status, actual_delta)。

        status: 'FILLED' 满额 | 'PARTIAL' 部分 | 'TIMEOUT' 未成交。
        任何结果下 base_shares/base_can_use/base_cost 都会重同步到最新;
        TIMEOUT 时自动撤掉残留挂单, 避免后续意外成交。
        """
        if self.dry_run:
            self._verify_trade(snap_before, label, trade_price, trade_shares)
            return 'FILLED', expected_shares_delta
        waited = 0.0
        while waited < timeout_sec:
            _time.sleep(0.5); waited += 0.5
            curr_snap = self._snapshot_account()
            actual_delta = curr_snap['shares'] - snap_before['shares']
            if actual_delta == expected_shares_delta:
                self._verify_trade(snap_before, label, trade_price, trade_shares)
                return 'FILLED', actual_delta
        # 超时: 重同步持仓, 判定部分/未成交
        self._verify_trade(snap_before, f'{label}(TIMEOUT)', trade_price, trade_shares)
        actual_delta = self.st['base_shares'] - snap_before['shares']
        if actual_delta != 0 and actual_delta * expected_shares_delta > 0:
            return 'PARTIAL', actual_delta
        self.conn.cancel_order(self.conn.last_order_id)
        return 'TIMEOUT', 0

    # ═══ 快照 & 校验 ═══

    def _snapshot_account(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        shares = 0; can_use = 0; cost = 0.0
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                shares = pos.m_nVolume; can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                cost = pos.m_dOpenPrice; break
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        cash = account[0].m_dAvailable if account else 0.0
        tick = self.ctx.get_full_tick([STOCK_QMT])
        price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if price <= 0: price = self.st.get('daily_signal', {}).get('open_price', 0)
        return {'shares': shares, 'can_use': can_use, 'cash': cash,
                'cost': cost, 'total_asset': shares * price + cash, 'price': price}

    # ★ v21: 成交日志优化 — 清晰打印价格×数量+持仓变化
    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_cash = snap_after['cash'] - snap_before['cash']
        # ★ v22: 兼容多手成交确认 (单手时与原逻辑一致)
        status = 'OK' if d_shares == trade_shares else ('PENDING' if d_shares == 0 else 'PARTIAL')

        # ★ v21: [成交] 行 — 价格 × 数量 + 持仓变化
        price_str = 'MKT' if trade_price <= 0 else 'Y{:.2f}'.format(trade_price)
        _log('[FILL-{}] {} × {} sh | pos {}→{} | cash {:+,.0f}'.format(
            label, price_str, abs(trade_shares),
            snap_before['shares'], snap_after['shares'], d_cash))

        # 仅在异常时输出校验详情
        if status != 'OK':
            _log('[VERIFY] {}: status {} | exp {:+d} act {:+d} | cashΔ {:+,.0f}'.format(
                label, status, trade_shares, d_shares, d_cash))

        self.st['base_shares'] = snap_after['shares']
        self.st['base_can_use'] = snap_after['can_use']
        self.st['base_cost'] = snap_after['cost']

    # ═══ v21: 信号+计划合并输出 (无分隔线, 无空行) ═══

    def _print_daily_brief(self, signal):
        trend = signal.get('trend', '?')
        trend_labels = {'strong_bull': 'STRONG-BULL', 'weak_bull': 'WEAK-BULL', 'sideways': 'SIDEWAYS', 'bear': 'BEAR'}
        open_p = signal.get('open_price', 0); close_y = signal.get('close_yday', 0)
        atr_pct = signal.get('atr_pct', 0) * 100; rsi_v = signal.get('rsi', 0)
        vol_r = signal.get('vol_ratio', 0); sell_mult = signal.get('sell_mult', 0)
        sell_base = signal.get('sell_mult_base', 0); sell_trig = signal.get('sell_trigger', 0)
        range_capped = signal.get('range_capped', False)
        do_short = signal.get('do_short', False)
        base_shares = self.st.get('base_shares', 0); base_can_use = self.st.get('base_can_use', 0)
        pos_pct = self.st.get('pos_pct', 0); avail_cash = self.st.get('avail_cash', 0)
        do_long = self.st.get('do_long', False)
        short_lots = self.st.get('short_lots', 0); long_lots = self.st.get('long_lots', 0)

        tick = self.ctx.get_full_tick([STOCK_QMT])
        curr_price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if curr_price <= 0: curr_price = open_p
        pos_value = base_shares * curr_price
        trend_cn = trend_labels.get(trend, trend)

        # 行1: 核心指标
        _log('[SIGNAL] {} | Open Y{:.2f} | ATR {:.1f}% | RSI {:.0f} | Vol_ratio {:.2f} | Mult {:.2f} | Trig Y{:.2f}{} {}'.format(
            trend_cn, open_p, atr_pct, rsi_v, vol_r, sell_mult, sell_trig,
            '(range-capped)' if range_capped else '',
            '⛔REV-T blocked:{}'.format(signal.get('blocked_reason', '')) if not do_short else ''))

        # 因子
        fd = signal.get('factor_details', {})
        if fd:
            _log('[FACTOR] {}'.format(' '.join('{} {:+.2f} | '.format(k, v) for k, v in fd.items())))

        # 行2: 持仓 + 方向
        bits = ['Position:{} sh Y{:,.0f}({:.0f}%)'.format(base_shares, pos_value, pos_pct),
                'Cash:Y{:,.0f}'.format(avail_cash),
                'T+0:{} lots({} sh)'.format(base_can_use // TRADE_LOT_SIZE, base_can_use)]
        _log('[ACCOUNT] {}'.format(' | '.join(bits)))

        # 行3-4: 反T / 正T
        if do_short:
            _log('[REV-T] ✅ {} lots trig Y{:.2f} buyback=sell×(1-ATR%×{:.2f}) emerg +{:.0f}% / emergTrig{}'.format(
                short_lots, sell_trig, cfg.BUYBACK_TRIGGER_MULT, cfg.EMERGENCY_BUYBACK_PCT * 100,
                'True' if cfg.EMERGENCY_BUYBACK else 'False'))
        else:
            reason = signal.get('short_reason', signal.get('blocked_reason', 'unknown'))
            _log('[REV-T] ❌ {}'.format(reason))
        if do_long:
            buy_trig = signal.get('buy_trigger', 0)
            sell_hint = signal.get('sellback_target_hint', 0)
            _log('[FWD-T] ✅ {} lots buy Y{:.2f} sell Y{:.2f}(+{:.1f}%) 1 lot≈Y{:,.0f}'.format(
                long_lots, buy_trig, sell_hint, cfg.SELLBACK_RISE_PCT * 100, curr_price * TRADE_LOT_SIZE))
        else:
            _log('[FWD-T] ❌ {}  1 lot≈Y{:,.0f}'.format(self.st.get('long_reason', 'unknown'), curr_price * TRADE_LOT_SIZE))

        # ★ v25/v26/v27: 短线动量反转机制 (独立于日线信号, 触发幅度自适应ATR)
        # ★ v29: MOM_ENABLED=False 时显示"已屏蔽"
        if MOM_ENABLED:
            _log('[MOM] {}min±2×ATR10m({:.1f}%~{:.1f}%) 反T买回-{:.1f}% 正T卖回+{:.1f}% 1手({}sh) 上限{}次 紧急买回{}'.format(
                MOM_WINDOW_SEC // 60,
                MOM_TRIGGER_MIN_PCT * 100, MOM_TRIGGER_MAX_PCT * 100,
                MOM_SHORT_BUYBACK_PCT * 100, MOM_LONG_SELLBACK_PCT * 100,
                MOM_LOT_SIZE, MOM_MAX_DAILY_TRADES,
                '开' if MOM_EMERGENCY_BUYBACK_ENABLED else '关'))
        else:
            _log('[MOM] 已屏蔽 (v29 MOM_ENABLED=False)')

        # 累计
        if self.total_t_days > 0:
            _log('[CUM] {} trades gross~Y{:,.0f}'.format(self.total_t_days, self.total_pnl))

    # ═══ 状态机 ═══

    def _handle_idle(self, price):
        st = self.st; signal = st.get('daily_signal', {})
        if st.get('do_short', False):
            trigger = signal.get('sell_trigger', 999999)
            if price >= trigger:
                can_use = st.get('base_can_use', st['base_shares'])
                if can_use < cfg.MIN_LOT: return
                tc = st.get('trade_count_short', 0)
                if tc >= cfg.MAX_DAILY_TRADES or st.get('locked', False): return
                st['trade_count_short'] = tc + 1
                st['fstate'] = STATE_SPIKING; st['peak_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[REV-T spike #{}/{}] Y{:.2f} >= Y{:.2f}'.format(tc + 1, cfg.MAX_DAILY_TRADES, price, trigger))
                return
        if st.get('do_long', False):
            buy_trigger = signal.get('buy_trigger', 0)
            if price <= buy_trigger:
                tc = st.get('trade_count_long', 0)
                if tc >= cfg.MAX_DAILY_TRADES: return
                st['trade_count_long'] = tc + 1
                st['fstate'] = STATE_BT_DIPPING; st['bt_dip_price'] = price
                st['bt_buy_trigger'] = buy_trigger; st['state_enter_time'] = cfg.now_hms()
                _log('[FWD-T dip #{}/{}] Y{:.2f} <= Y{:.2f}(-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, buy_trigger, (buy_trigger - price) / buy_trigger * 100))

    def _handle_spiking(self, price):
        st = self.st
        if price > st['peak_price']: st['peak_price'] = price
        peak = st['peak_price']; pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            _log('[REV-T sell trig] peak Y{:.2f} pullback {:.2f}% → Y{:.2f}'.format(peak, pullback * 100, price))
            atr_pct = st['daily_signal']['atr_pct']; buyback_pct = atr_pct * cfg.BUYBACK_TRIGGER_MULT
            buyback_target = round(price * (1.0 - buyback_pct), 2)
            st['buyback_target'] = buyback_target
            st['buyback_target_pct'] = buyback_pct * 100
            st['sell_elapsed_bars'] = 0; st['state_enter_time'] = cfg.now_hms()
            # ★ v23: 下单前检查可卖仓位, 以实际可卖数量下单
            status, delta = self._submit_order(-TRADE_LOT_SIZE, price, 'REV-T sell')
            if status in ('SKIP', 'TIMEOUT'):
                if status == 'TIMEOUT':
                    _log('[REV-T sell TIMEOUT] 未成交, 回 IDLE')
                st['trade_count_short'] = max(0, st.get('trade_count_short', 0) - 1)
                st['fstate'] = STATE_IDLE
                return
            # FILLED / PARTIAL: 按实际成交股数入腿
            actual_sold = -delta
            st['sell_fill_price'] = price
            st['short_legs'].append((price, actual_sold))
            st['ladder_sell_target'] = round(price * (1.0 + LADDER_UP_STEP_PCT), 2) if status == 'FILLED' else 0.0
            if status == 'PARTIAL':
                _log('[REV-T sell PARTIAL] 实际卖出 {} sh'.format(actual_sold))
            st['fstate'] = STATE_SOLD

    def _handle_sold(self, price):
        st = self.st; sp = st['sell_fill_price']; bt = st['buyback_target']
        # ★ v22: 阶梯加卖 — 价格涨至更高一档(卖价+阶梯幅度) → 追加冲高回落卖出
        ladder = st.get('ladder_sell_target', 0.0)
        if ladder > 0 and price >= ladder:
            tc = st.get('trade_count_short', 0)
            can_use = st.get('base_can_use', st['base_shares'])
            if can_use >= cfg.MIN_LOT and tc < cfg.MAX_DAILY_TRADES and not st.get('locked', False):
                st['trade_count_short'] = tc + 1
                st['ladder_sold_count'] = st.get('ladder_sold_count', 0) + 1
                st['fstate'] = STATE_SPIKING; st['peak_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[REV-T ladder sell #{}/{}] Y{:.2f} >= Y{:.2f}(sell+{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, ladder, LADDER_UP_STEP_PCT * 100))
                return
        if cfg.EMERGENCY_BUYBACK and price >= sp * (1.0 + cfg.EMERGENCY_BUYBACK_PCT):
            _log('[EMERG buyback trig] Y{:.2f}→Y{:.2f}(+{:.2f}%)'.format(sp, price, (price - sp) / sp * 100))
            self._do_buyback(price, 'EMERG'); return
        tightened_bt = bt
        if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
            tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] *
                                 cfg.BUYBACK_TRIGGER_MULT * cfg.BUYBACK_TIGHTEN_MULT)
            tightened_bt = round(max(tightened_bt, bt), 2)
        if price <= tightened_bt:
            st['fstate'] = STATE_DIPPING; st['dip_price'] = price
            st['state_enter_time'] = cfg.now_hms()
            _log('[Buyback trig {}] Y{:.2f}(-{:.2f}%)'.format(
                '(tightened)' if tightened_bt > bt else '', price, (sp - price) / sp * 100))

    def _handle_dipping(self, price):
        st = self.st
        if price < st['dip_price']: st['dip_price'] = price
        dip = st['dip_price'] or price; bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            legs = st['short_legs'] or [(st['sell_fill_price'], TRADE_LOT_SIZE)]
            total_shares = self._leg_shares(legs)
            gross = self._short_gross(legs, price)
            _log('[REV-T buyback trig] low Y{:.2f} bounce {:.2f}% → Y{:.2f} gross~Y{:,.0f}'.format(dip, bounce * 100, price, gross))
            bought = self._do_buyback(price, 'NORMAL')
            if bought >= total_shares and total_shares > 0:
                self.total_t_days += 1
                self.total_pnl += gross

    def _do_buyback(self, price, reason=''):
        st = self.st
        # ★ v23: 一次性买回全部未平仓反T腿 (按实际股数)
        legs = st['short_legs'] or [(st.get('sell_fill_price', price), TRADE_LOT_SIZE)]
        shares = self._leg_shares(legs)
        if shares <= 0:
            return 0
        status, delta = self._submit_order(shares, price, 'REV-T buyback({})'.format(reason))
        bought = delta if delta > 0 else 0
        if bought <= 0:
            _log('[Buyback {}-FAIL] 未成交, 保持 SOLD 继续监控'.format(reason))
            st['fstate'] = STATE_SOLD
            return 0
        if bought >= shares:
            st['short_legs'] = []
            st['ladder_sell_target'] = 0.0; st['ladder_sold_count'] = 0
            st['fstate'] = STATE_DONE
            self._maybe_resume_trading()
            return bought
        # 部分买回: 保留未买回部分继续监控
        remaining = shares - bought
        st['short_legs'] = [(st.get('sell_fill_price', price), remaining)]
        st['ladder_sell_target'] = 0.0
        st['fstate'] = STATE_SOLD
        _log('[Buyback PARTIAL] 已买回 {} sh, 剩余 {} sh 继续监控'.format(bought, remaining))
        return bought

    def _force_buyback(self):
        _log('[FORCE buyback trig]')
        st = self.st
        shares = self._leg_shares(st['short_legs']) or TRADE_LOT_SIZE
        price = self._cur_price()
        self._submit_order(shares, price, 'REV-T force buyback')
        st['short_legs'] = []; st['ladder_sell_target'] = 0.0; st['ladder_sold_count'] = 0
        st['fstate'] = STATE_FORCED

    def _handle_bt_dipping(self, price):
        st = self.st
        if price < st.get('bt_dip_price', price): st['bt_dip_price'] = price
        dip = st.get('bt_dip_price', price) or price; bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            _log('[FWD-T buy trig] low Y{:.2f} bounce {:.2f}% → Y{:.2f}'.format(dip, bounce * 100, price))
            # ★ v23: 下单前检查现金, 以实际可买数量下单
            status, delta = self._submit_order(TRADE_LOT_SIZE, price, 'FWD-T buy')
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
                _log('[FWD-T buy PARTIAL] 实际买入 {} sh'.format(delta))

    def _handle_bt_bought(self, price):
        st = self.st; target = st.get('bt_sellback_target', 999999); bp = st.get('bt_buy_fill_price', 0)
        # ★ v22: 阶梯加买 — 价格跌至更低一档(买价-阶梯幅度) → 追加探底回升买入
        ladder = st.get('ladder_buy_target', 0.0)
        if ladder > 0 and price <= ladder:
            tc = st.get('trade_count_long', 0)
            account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
            avail = account[0].m_dAvailable if account else 0.0
            if avail >= price * TRADE_LOT_SIZE * 1.01 and tc < cfg.MAX_DAILY_TRADES:
                st['trade_count_long'] = tc + 1
                st['ladder_bought_count'] = st.get('ladder_bought_count', 0) + 1
                st['fstate'] = STATE_BT_DIPPING; st['bt_dip_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[FWD-T ladder buy #{}/{}] Y{:.2f} <= Y{:.2f}(buy-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, ladder, LADDER_DOWN_STEP_PCT * 100))
                return
        # ★ v23: 止损/卖回以均价为准
        legs = st['long_legs']; avg_bp = self._leg_avg_price(legs) if legs else bp
        if avg_bp > 0 and price <= avg_bp * (1.0 - cfg.STOP_LOSS_PCT):
            _log('[FWD-T stop-loss trig] avg Y{:.2f} now Y{:.2f}({:.1f}%)'.format(avg_bp, price, (price - avg_bp) / avg_bp * 100))
            self._do_bt_force_sell(); return
        if price >= target:
            st['fstate'] = STATE_BT_SPIKING; st['bt_sell_peak_price'] = price
            _log('[FWD-T sellback watch] +{:.2f}% → Y{:.2f}'.format((price - avg_bp) / avg_bp * 100, price))

    def _handle_bt_spiking(self, price):
        st = self.st
        if price > st.get('bt_sell_peak_price', price): st['bt_sell_peak_price'] = price
        peak = st.get('bt_sell_peak_price', price); pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            # ★ v23: 多腿毛利 = Σ(卖价 - 各腿买价) × 各腿股数
            legs = st['long_legs'] or [(st.get('bt_buy_fill_price', price), TRADE_LOT_SIZE)]
            total_shares = self._leg_shares(legs)
            gross = sum((price - p) * s for p, s in legs)
            _log('[FWD-T sell trig] peak Y{:.2f} pullback {:.2f}% → Y{:.2f} gross~Y{:,.0f}'.format(
                peak, pullback * 100, price, gross))
            status, delta = self._submit_order(-total_shares, price, 'FWD-T sell')
            if status in ('SKIP', 'TIMEOUT'):
                _log('[FWD-T sell FAIL] 未成交, 回 BT_BOUGHT')
                st['fstate'] = STATE_BT_BOUGHT
                return
            sold = -delta
            if sold >= total_shares:
                st['long_legs'] = []; st['ladder_buy_target'] = 0.0; st['ladder_bought_count'] = 0
                st['fstate'] = STATE_DONE
                self.total_t_days += 1; self.total_pnl += gross
                self._maybe_resume_trading()
            else:
                remaining = total_shares - sold
                st['long_legs'] = [(st.get('bt_buy_fill_price', price), remaining)]
                st['ladder_buy_target'] = 0.0
                st['fstate'] = STATE_BT_BOUGHT
                _log('[FWD-T sell PARTIAL] 已卖 {} sh, 剩余 {} sh'.format(sold, remaining))

    def _do_bt_force_sell(self):
        _log('[FWD-T force sell trig]')
        st = self.st
        shares = self._leg_shares(st['long_legs']) or TRADE_LOT_SIZE
        price = self._cur_price()
        self._submit_order(-shares, price, 'FWD-T force sell')
        st['long_legs'] = []; st['ladder_buy_target'] = 0.0; st['ladder_bought_count'] = 0
        st['fstate'] = STATE_FORCED

    def _maybe_resume_trading(self):
        st = self.st
        tc_s = st.get('trade_count_short', 0); tc_l = st.get('trade_count_long', 0)
        do_short = st.get('do_short', False); do_long = st.get('do_long', False)
        can_s = do_short and tc_s < cfg.MAX_DAILY_TRADES
        can_l = do_long and tc_l < cfg.MAX_DAILY_TRADES
        if can_s or can_l:
            # ★ v28: 反T完成后, 将 sell_trigger 提升至实际卖出价
            #   避免心跳继续显示已成交过的旧阈值 (如 Y380.74 成交后仍反复出现)
            sfp = st.get('sell_fill_price', 0)
            if sfp > 0:
                sig = st.get('daily_signal', {})
                old_trig = sig.get('sell_trigger', 0)
                if sfp > old_trig:
                    sig['sell_trigger'] = sfp
                    _log('[SELL-TRIG UPD] Y{:.2f}→Y{:.2f} (已消耗, 提升至实际卖出价)'.format(old_trig, sfp))
            self._refresh_position()
            st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0; st['dip_price'] = 0.0
            st['sell_fill_price'] = 0.0; st['buyback_target'] = 0.0
            # ★ v22: 清空阶梯状态
            st['short_legs'] = []; st['long_legs'] = []
            st['ladder_sell_target'] = 0.0; st['ladder_buy_target'] = 0.0
            st['ladder_sold_count'] = 0; st['ladder_bought_count'] = 0
            st['state_enter_time'] = cfg.now_hms(); st['stop_loss_hit'] = False
            parts = []
            if can_s: parts.append('REV-T {}/{}'.format(tc_s, cfg.MAX_DAILY_TRADES))
            if can_l: parts.append('FWD-T {}/{}'.format(tc_l, cfg.MAX_DAILY_TRADES))
            _log('[RESUME] → IDLE ({})'.format(', '.join(parts)))
        else:
            _log('[DONE] {}/{} trades at limit'.format(tc_s + tc_l, cfg.MAX_DAILY_TRADES * 2))

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
        cool_ok = now_ts >= st.get('lock_cooldown_until', 0)
        if should_lock and not st.get('locked'):
            st['locked'] = True; st['lock_since'] = cfg.now_hms()
            st['lock_reason'] = 'P+{:.1f}% M {:.2f}% D {:.2f}%'.format(
                (pn / open_price - 1) * 100, (pn - p5) / p5 * 100, (dh - pn) / dh * 100 if dh > 0 else 0)
            _log('[LOCK] {}'.format(st['lock_reason']))
        elif not should_lock and st.get('locked') and cool_ok:
            st['locked'] = False; st['lock_reason'] = ''; st['lock_since'] = ''; _log('[UNLOCK]')
        if not should_lock and not st.get('locked'): st['lock_cooldown_until'] = 0.0
        if should_lock: st['lock_cooldown_until'] = 0.0
        elif st.get('locked') and st['lock_cooldown_until'] == 0.0:
            st['lock_cooldown_until'] = now_ts + cfg.LOCK_COOLDOWN_SEC

    # ═══ v25/v26: 短线动量反转机制 (2分钟±2×ATR10m 事件驱动, 独立于日线信号) ═══

    def _mom_update_history(self, price, now_ts):
        """维护2分钟检测窗口 + 10分钟ATR窗口。"""
        hist = self.st['mom_price_history']
        hist.append((now_ts, price))
        cutoff = now_ts - MOM_WINDOW_SEC
        while hist and hist[0][0] < cutoff:
            hist.popleft()
        # ★ v26: 10分钟ATR窗口 (独立于2分钟检测窗口)
        atr_hist = self.st['mom_atr_history']
        atr_hist.append((now_ts, price))
        atr_cutoff = now_ts - MOM_ATR_WINDOW_SEC
        while atr_hist and atr_hist[0][0] < atr_cutoff:
            atr_hist.popleft()

    def _mom_compute_atr(self):
        """计算最近10分钟ATR (近似: 各1分钟桶内高低区间之中位数)。

        用中位数而非均值, 可剔除跨分钟边界产生的"半桶"低区间干扰。
        """
        hist = self.st['mom_atr_history']
        if len(hist) < 2:
            return 0.0
        buckets = {}
        for ts, px in hist:
            b = int(ts // 60)          # 按1分钟分桶
            rec = buckets.get(b)
            if rec is None:
                buckets[b] = [px, px]
            else:
                if px > rec[0]: rec[0] = px
                if px < rec[1]: rec[1] = px
        ranges = sorted(h - l for h, l in buckets.values())
        if not ranges:
            return 0.0
        n = len(ranges)
        mid = n // 2
        if n % 2 == 1:
            return ranges[mid]
        return (ranges[mid - 1] + ranges[mid]) / 2.0

    def _mom_detect(self, price):
        """检测2分钟内涨/跌是否超过自适应阈值。返回 'UP' / 'DOWN' / None。"""
        trig = self.st.get('mom_trigger_pct', 0.0)
        if trig <= 0:
            return None
        hist = self.st['mom_price_history']
        if len(hist) < 2:
            return None
        base = hist[0][1]          # 窗口内最早价 (约2分钟前)
        if base <= 0 or price <= 0:
            return None
        chg = (price - base) / base
        if chg >= trig:
            return 'UP'
        if chg <= -trig:
            return 'DOWN'
        return None

    def _mom_tick(self, price, now_ts):
        """短线动量机制主入口 (每tick调用, 与主机制并行)。"""
        self._mom_update_history(price, now_ts)
        # ★ v26: 自适应触发幅度 = 2 × 最近10分钟ATR
        #   除以"2分钟基准价"而非当前价, 与 _mom_detect 的涨跌幅同基准, 避免自参考偏差
        atr = self._mom_compute_atr()
        hist = self.st['mom_price_history']
        base = hist[0][1] if hist else 0.0
        if base > 0 and atr > 0:
            trig = MOM_ATR_MULT * atr / base
        else:
            trig = MOM_TRIGGER_MIN_PCT    # 数据不足/平静市 → 用下限, 避免永不触发
        trig = min(max(trig, MOM_TRIGGER_MIN_PCT), MOM_TRIGGER_MAX_PCT)
        self.st['mom_trigger_pct'] = trig
        ms = self.st.get('mom_state', 'MOM_IDLE')
        if ms == 'MOM_IDLE':
            self._mom_handle_idle(price)
        elif ms == 'MOM_SPIKING':
            self._mom_handle_spiking(price)
        elif ms == 'MOM_SOLD':
            self._mom_handle_sold(price)
        elif ms == 'MOM_DIPPING':
            self._mom_handle_dipping(price)
        elif ms == 'MOM_BT_DIPPING':
            self._mom_handle_bt_dipping(price)
        elif ms == 'MOM_BT_BOUGHT':
            self._mom_handle_bt_bought(price)
        elif ms == 'MOM_BT_SPIKING':
            self._mom_handle_bt_spiking(price)
        # 尾盘强制平掉短线腿 (短线不隔夜)
        if cfg.now_hms() >= cfg.FORCE_CLOSE_TIME:
            self._mom_force_close(price)
        # 心跳: 非空闲时每60s打印一次短线状态
        if ms != 'MOM_IDLE' and now_ts - self.st.get('mom_last_hb', 0) >= 60:
            self.st['mom_last_hb'] = now_ts
            self._mom_heartbeat(price)

    def _mom_status(self):
        """返回短线机制状态摘要。"""
        st = self.st; ms = st.get('mom_state', 'MOM_IDLE')
        if ms == 'MOM_IDLE':
            return ''
        if ms == 'MOM_SPIKING':
            return '冲高回落卖出: peak Y{:.2f}'.format(st['mom_peak'])
        if ms == 'MOM_SOLD':
            return '已卖Y{:.2f} 等跌{:.1f}%买回'.format(st['mom_sell_price'], MOM_SHORT_BUYBACK_PCT * 100)
        if ms == 'MOM_DIPPING':
            return '探底回升买回: dip Y{:.2f}'.format(st['mom_dip'])
        if ms == 'MOM_BT_DIPPING':
            return '探底回升买入: dip Y{:.2f}'.format(st['mom_dip'])
        if ms == 'MOM_BT_BOUGHT':
            return '已买Y{:.2f} 等涨{:.1f}%卖回'.format(st['mom_buy_price'], MOM_LONG_SELLBACK_PCT * 100)
        if ms == 'MOM_BT_SPIKING':
            return '冲高回落卖出: peak Y{:.2f}'.format(st['mom_peak'])
        return ms

    def _mom_heartbeat(self, price):
        trig = self.st.get('mom_trigger_pct', 0.0) * 100
        _log('[MOM-HB] {} Y{:.2f} trig±{:.2f}%'.format(self._mom_status(), price, trig))

    def _mom_handle_idle(self, price):
        st = self.st
        # 尾盘不再开新腿
        if cfg.now_hms() >= cfg.FORCE_CLOSE_TIME:
            return
        if st.get('mom_trade_count', 0) >= MOM_MAX_DAILY_TRADES:
            return
        sig = self._mom_detect(price)
        trig = st.get('mom_trigger_pct', 0.0) * 100
        if sig == 'UP':
            st['mom_state'] = 'MOM_SPIKING'; st['mom_peak'] = price
            _log('[MOM spike] 2min +{:.2f}% (2×ATR10m {:.2f}%) Y{:.2f} → 冲高回落卖出监测'.format(
                trig, trig, price))
        elif sig == 'DOWN':
            st['mom_state'] = 'MOM_BT_DIPPING'; st['mom_dip'] = price
            _log('[MOM dip] 2min -{:.2f}% (2×ATR10m {:.2f}%) Y{:.2f} → 探底回升买入监测'.format(
                trig, trig, price))

    def _mom_handle_spiking(self, price):
        """冲高回落卖出: 跟踪峰值, 回落PULLBACK_PCT后卖出1手。"""
        st = self.st
        if price > st['mom_peak']:
            st['mom_peak'] = price
        peak = st['mom_peak']
        pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            _log('[MOM sell trig] peak Y{:.2f} 回落{:.2f}% → Y{:.2f}'.format(peak, pullback * 100, price))
            status, delta = self._submit_order(-MOM_LOT_SIZE, price, 'MOM short')
            if status in ('SKIP', 'TIMEOUT'):
                if status == 'TIMEOUT':
                    _log('[MOM short TIMEOUT] 未成交, 回 IDLE')
                st['mom_state'] = 'MOM_IDLE'; st['mom_peak'] = 0.0
                return
            st['mom_sell_price'] = price
            st['mom_leg_shares'] = abs(delta)
            st['mom_trade_count'] = st.get('mom_trade_count', 0) + 1
            st['mom_state'] = 'MOM_SOLD'
            _log('[MOM sold] Y{:.2f} x {} sh | 买回触发 ≤Y{:.2f}'.format(
                price, st['mom_leg_shares'], round(price * (1 - MOM_SHORT_BUYBACK_PCT), 2)))

    def _mom_handle_sold(self, price):
        """卖出后: 跌1.2%进入探底回升买回; (可选)反涨3%紧急止损买回。"""
        st = self.st
        sp = st['mom_sell_price']
        if sp <= 0:
            st['mom_state'] = 'MOM_IDLE'; return
        # ★ v27: 紧急止损买回开关 — 价格反涨超卖价3%时强制买回 (强牛不回落时防越亏越多)
        if MOM_EMERGENCY_BUYBACK_ENABLED and price >= sp * (1.0 + cfg.EMERGENCY_BUYBACK_PCT):
            _log('[MOM EMERG buyback] Y{:.2f}→Y{:.2f}(+{:.2f}%) 止损买回'.format(
                sp, price, (price - sp) / sp * 100))
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _, delta = self._submit_order(shares, price, 'MOM emrg buyback')
            if delta > 0:
                gross = (sp - price) * delta
                self.total_t_days += 1; self.total_pnl += gross
                _log('[MOM short done(EMERG)] 卖Y{:.2f} 买Y{:.2f} gross~Y{:,.0f}'.format(sp, price, gross))
                st['mom_state'] = 'MOM_IDLE'
                st['mom_sell_price'] = 0.0; st['mom_leg_shares'] = 0
            return
        # 正常买回触发: 跌1.2% → 探底回升买回
        if price <= sp * (1.0 - MOM_SHORT_BUYBACK_PCT):
            st['mom_state'] = 'MOM_DIPPING'; st['mom_dip'] = price
            _log('[MOM buyback trig] Y{:.2f} ≤Y{:.2f}(-{:.2f}%) → 探底回升买回'.format(
                price, round(sp * (1 - MOM_SHORT_BUYBACK_PCT), 2), MOM_SHORT_BUYBACK_PCT * 100))

    def _mom_handle_dipping(self, price):
        """探底回升买回: 跟踪谷值, 回升BOUNCE_PCT后买回1手。"""
        st = self.st
        if price < st['mom_dip']:
            st['mom_dip'] = price
        dip = st['mom_dip'] or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM buyback buy] low Y{:.2f} 回升{:.2f}% → Y{:.2f}'.format(dip, bounce * 100, price))
            _, delta = self._submit_order(shares, price, 'MOM buyback')
            if delta <= 0:
                _log('[MOM buyback FAIL] 未成交, 保持 DIPPING')
                return
            gross = (st['mom_sell_price'] - price) * delta
            self.total_t_days += 1; self.total_pnl += gross
            _log('[MOM short done] 卖Y{:.2f} 买Y{:.2f} x {}sh gross~Y{:,.0f}'.format(
                st['mom_sell_price'], price, delta, gross))
            st['mom_state'] = 'MOM_IDLE'
            st['mom_sell_price'] = 0.0; st['mom_dip'] = 0.0; st['mom_leg_shares'] = 0

    def _mom_handle_bt_dipping(self, price):
        """探底回升买入: 跟踪谷值, 回升BOUNCE_PCT后买入1手。"""
        st = self.st
        if price < st['mom_dip']:
            st['mom_dip'] = price
        dip = st['mom_dip'] or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            _log('[MOM buy trig] low Y{:.2f} 回升{:.2f}% → Y{:.2f}'.format(dip, bounce * 100, price))
            status, delta = self._submit_order(MOM_LOT_SIZE, price, 'MOM long')
            if status in ('SKIP', 'TIMEOUT'):
                if status == 'TIMEOUT':
                    _log('[MOM long TIMEOUT] 未成交, 回 IDLE')
                st['mom_state'] = 'MOM_IDLE'; st['mom_dip'] = 0.0
                return
            st['mom_buy_price'] = price
            st['mom_leg_shares'] = abs(delta)
            st['mom_trade_count'] = st.get('mom_trade_count', 0) + 1
            st['mom_state'] = 'MOM_BT_BOUGHT'
            _log('[MOM bought] Y{:.2f} x {} sh | 卖回触发 ≥Y{:.2f}'.format(
                price, st['mom_leg_shares'], round(price * (1 + MOM_LONG_SELLBACK_PCT), 2)))

    def _mom_handle_bt_bought(self, price):
        """买入后: 涨1.5%进入冲高回落卖出; 反跌1.5%止损卖出。"""
        st = self.st
        bp = st['mom_buy_price']
        if bp <= 0:
            st['mom_state'] = 'MOM_IDLE'; return
        # 止损卖出: 价格反跌破买价1.5%
        if price <= bp * (1.0 - cfg.STOP_LOSS_PCT):
            _log('[MOM stop-loss] Y{:.2f}→Y{:.2f}(-{:.2f}%) 止损卖出'.format(
                bp, price, (bp - price) / bp * 100))
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _, delta = self._submit_order(-shares, price, 'MOM stop-loss')
            if delta < 0:
                gross = (price - bp) * (-delta)
                self.total_t_days += 1; self.total_pnl += gross
                _log('[MOM long done(stop)] 买Y{:.2f} 卖Y{:.2f} gross~Y{:,.0f}'.format(bp, price, gross))
                st['mom_state'] = 'MOM_IDLE'
                st['mom_buy_price'] = 0.0; st['mom_leg_shares'] = 0
            return
        # 正常卖回触发: 涨1.5% → 冲高回落卖出
        if price >= bp * (1.0 + MOM_LONG_SELLBACK_PCT):
            st['mom_state'] = 'MOM_BT_SPIKING'; st['mom_peak'] = price
            _log('[MOM sellback trig] Y{:.2f} ≥Y{:.2f}(+{:.2f}%) → 冲高回落卖出'.format(
                price, round(bp * (1 + MOM_LONG_SELLBACK_PCT), 2), MOM_LONG_SELLBACK_PCT * 100))

    def _mom_handle_bt_spiking(self, price):
        """冲高回落卖出: 跟踪峰值, 回落PULLBACK_PCT后卖出1手。"""
        st = self.st
        if price > st['mom_peak']:
            st['mom_peak'] = price
        peak = st['mom_peak']
        pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM sellback sell] peak Y{:.2f} 回落{:.2f}% → Y{:.2f}'.format(peak, pullback * 100, price))
            _, delta = self._submit_order(-shares, price, 'MOM sellback')
            sold = -delta
            if sold <= 0:
                _log('[MOM sellback FAIL] 未成交, 保持 SPIKING')
                return
            gross = (price - st['mom_buy_price']) * sold
            self.total_t_days += 1; self.total_pnl += gross
            _log('[MOM long done] 买Y{:.2f} 卖Y{:.2f} x {}sh gross~Y{:,.0f}'.format(
                st['mom_buy_price'], price, sold, gross))
            st['mom_state'] = 'MOM_IDLE'
            st['mom_buy_price'] = 0.0; st['mom_peak'] = 0.0; st['mom_leg_shares'] = 0

    def _mom_force_close(self, price):
        """尾盘强制平掉短线腿 (卖出的买回 / 买入的卖出), 短线不隔夜。"""
        st = self.st
        ms = st.get('mom_state')
        if ms == 'MOM_SOLD':
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM force buyback] Y{:.2f}'.format(price))
            _, delta = self._submit_order(shares, price, 'MOM force buyback')
            if delta <= 0:
                _log('[WARN] MOM force buyback 未成交, 短线腿可能残留!')
            st['mom_state'] = 'MOM_IDLE'; st['mom_sell_price'] = 0.0; st['mom_leg_shares'] = 0
        elif ms in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING'):
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM force sell] Y{:.2f}'.format(price))
            _, delta = self._submit_order(-shares, price, 'MOM force sell')
            if delta >= 0:
                _log('[WARN] MOM force sell 未成交, 短线腿可能残留!')
            st['mom_state'] = 'MOM_IDLE'; st['mom_buy_price'] = 0.0; st['mom_leg_shares'] = 0
        elif ms in ('MOM_SPIKING', 'MOM_DIPPING', 'MOM_BT_DIPPING'):
            # 尚未开腿, 直接复位
            st['mom_state'] = 'MOM_IDLE'; st['mom_peak'] = 0.0; st['mom_dip'] = 0.0

    # ═══ 主循环 ═══

    def run(self):
        set_global_conn(self.conn, self.dry_run)
        if not self.dry_run:
            if not self.conn.connect_data(): _log('[ERROR] market data connect failed'); return
            if not self.conn.connect_trade(): _log('[ERROR] trade connect failed'); self.conn.disconnect(); return
        else:
            if not self.conn.connect_data(): _log('[ERROR] market data connect failed'); return
        self._init_state()
        _log('[START] {} v29 {} {}'.format(STOCK_NAME, 'LIVE' if not self.dry_run else 'SIGNAL', STOCK_QMT))

        try:
            self._daily_init()
            signal = self.st.get('daily_signal')
            if signal: self._print_daily_brief(signal)
        except Exception as e:
            _log('[ERROR] init failed: {}'.format(e)); _traceback.print_exc()

        try:
            while self._running:
                now = cfg.now_hms(); now_ts = _time.time()
                if not cfg.is_market_open(now):
                    today = datetime.now().strftime('%Y%m%d')
                    if '09:30:00' <= now < '09:30:59':
                        if self.st.get('_pre_market_done', '') != today:
                            _log('[PRE-MKT {}] computing signal...'.format(now))
                            try:
                                self._daily_init(); self.st['_pre_market_done'] = today
                                signal = self.st.get('daily_signal')
                                if signal: self._print_daily_brief(signal)
                            except Exception as e: _log('[PRE-MKT ERROR] {}'.format(e))
                            self._last_heartbeat = now_ts; _time.sleep(5); continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                            _log('[PRE-MKT {}] to open {}'.format(now, cfg.time_to_open(now)))
                        _time.sleep(5); continue
                    if self.st.get('trade_date', '') != today:
                        try:
                            self._daily_init()
                            signal = self.st.get('daily_signal')
                            if signal: self._print_daily_brief(signal)
                        except Exception as e: _log('[ERROR] init failed: {}'.format(e))
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        if now < '09:30:00': _log('[WAIT {}] to open {}'.format(now, cfg.time_to_open(now)))
                        elif now > '15:00:00': _log('[CLOSE {}]'.format(now))
                        elif '11:30:00' < now < '13:00:00': _log('[LUNCH {}]'.format(now))
                    _time.sleep(10); continue

                fstate = self.st.get('fstate', STATE_IDLE)
                if fstate in (STATE_DONE, STATE_FORCED):
                    tick = self.ctx.get_full_tick([STOCK_QMT])
                    price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
                    # ★ v25: 短线动量机制在主机制收尾(DONE/FORCED)时仍独立运行
                    if MOM_ENABLED and price > 0:
                        self._mom_tick(price, now_ts)
                    if now_ts - self._last_heartbeat >= 30:
                        self._last_heartbeat = now_ts
                        tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
                        _log('[STATE] {} Y{:.2f} REV-T {}/{} FWD-T {}/{} cum {} trades~Y{:,.0f}'.format(
                            fstate, price, tc_s, cfg.MAX_DAILY_TRADES,
                            tc_l, cfg.MAX_DAILY_TRADES, self.total_t_days, self.total_pnl))
                    if now < '14:57:00':
                        tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
                        if ((self.st.get('do_short', False) and tc_s < cfg.MAX_DAILY_TRADES) or
                            (self.st.get('do_long', False) and tc_l < cfg.MAX_DAILY_TRADES)):
                            self._maybe_resume_trading()
                    _time.sleep(3); continue

                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick: _time.sleep(1); continue
                price = tick[STOCK_QMT].get('lastPrice', 0)
                if price <= 0: _time.sleep(1); continue
                # ★ v25: 短线动量机制 (独立于日线信号/主状态机, 每tick运行)
                if MOM_ENABLED:
                    self._mom_tick(price, now_ts)
                # ★ v21: 开盘首个有效tick打印行情确认
                if not self.st.get('_market_open_logged', True):
                    self.st['_market_open_logged'] = True
                    sig_chk = self.st.get('daily_signal', {})
                    # ★ v28: 用今日开盘价重算 sell_trigger (盘前计算可能用了昨日开盘价)
                    _open_now = self.ctx.get_full_tick([STOCK_QMT]).get(STOCK_QMT, {}).get('open', 0)
                    _open_old = sig_chk.get('open_price', 0)
                    if _open_now > 0 and _open_old > 0 and abs(_open_now - _open_old) / _open_old > 0.005:
                        _sm = sig_chk.get('sell_mult', 0.40)
                        _atr = sig_chk.get('atr_pct', 0.03)
                        _new_trig = round(_open_now * (1.0 + _atr * _sm * cfg.SELL_TRIGGER_SCALE), 2)
                        _old_trig = sig_chk.get('sell_trigger', 0)
                        sig_chk['sell_trigger'] = _new_trig
                        sig_chk['open_price'] = _open_now
                        _log('[SELL-TRIG RECALC] open Y{:.2f}→Y{:.2f} trig Y{:.2f}→Y{:.2f} (Mult {:.2f} ATR {:.1f}%)'.format(
                            _open_old, _open_now, _old_trig, _new_trig, _sm, _atr * 100))
                    st_trig = sig_chk.get('sell_trigger', 0)
                    bt_trig = sig_chk.get('buy_trigger', 0)
                    bits = ['OPEN', 'Y{:.2f}'.format(price)]
                    if st_trig > 0: bits.append('REV-T trig Y{:.2f}(need +{:.2f}%)'.format(st_trig, (st_trig - price) / price * 100))
                    if bt_trig > 0: bits.append('FWD-T trig Y{:.2f}(need -{:.2f}%)'.format(bt_trig, (price - bt_trig) / price * 100))
                    if self.st.get('locked'): bits.append('LOCKED')
                    _log('[{}]'.format('] ['.join(bits)))
                signal = self.st.get('daily_signal')
                do_short = self.st.get('do_short', False); do_long = self.st.get('do_long', False)
                if signal is None or (not do_short and not do_long):
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        _log('[STANDBY] Y{:.2f} no trade direction'.format(price))
                    _time.sleep(5); continue
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
                    if f in (STATE_SOLD, STATE_DIPPING): self._force_buyback()
                    elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING): self._do_bt_force_sell()
                    elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE): self.st['fstate'] = STATE_DONE
                if fstate == STATE_SOLD and not self.st.get('stop_loss_hit', False):
                    loss_limit = self.st['base_shares'] * signal['open_price'] * cfg.STOP_LOSS_PCT
                    if self.st.get('day_pnl', 0) < -loss_limit:
                        _log('[STOP-LOSS] REV-T loss over limit'); self.st['stop_loss_hit'] = True; self._force_buyback()
                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts; self._heartbeat(price)
                _time.sleep(0.5)
        except KeyboardInterrupt: _log('Interrupted by user')
        except Exception as e: _log('[ERROR] {}'.format(e)); _traceback.print_exc()
        finally:
            self.conn.disconnect()
            if self.st.get('fstate', '') in (STATE_SOLD, STATE_DIPPING): _log('[WARN] position not bought back!')
            # ★ v25: 短线腿残留警告 (v29: MOM_ENABLED=False时跳过)
            if MOM_ENABLED:
                mom_ms = self.st.get('mom_state', '')
                if mom_ms in ('MOM_SOLD', 'MOM_DIPPING'):
                    _log('[WARN] MOM short leg not bought back!')
                elif mom_ms in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING'):
                    _log('[WARN] MOM long leg not sold!')
            _log('[STOP] {} v29 cum {} days gross~Y{:,.0f}'.format(STOCK_NAME, self.total_t_days, self.total_pnl))
            logger = get_logger()
            if logger is not None: logger.close()

    def _heartbeat(self, price):
        fs = self.st['fstate']; sig = self.st.get('daily_signal', {})
        if fs in (STATE_DONE, STATE_FORCED):
            tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
            _log('[HB] {} Y{:.2f} REV-T {}/{} FWD-T {}/{} cum {} trades~Y{:,.0f}'.format(
                fs, price, tc_s, cfg.MAX_DAILY_TRADES, tc_l, cfg.MAX_DAILY_TRADES,
                self.total_t_days, self.total_pnl)); return
        if fs == STATE_IDLE:
            if self.st.get('do_long'):
                bt_floor = sig.get('buy_trigger_floor', 0)
                bt_trail = round(price * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
                self.st['bt_max_trail'] = max(self.st.get('bt_max_trail', 0), bt_trail)
                sig['buy_trigger'] = max(bt_floor, self.st['bt_max_trail'])
                sig['buy_trigger_trail'] = bt_trail
            parts = []
            if self.st.get('do_short'):
                st_trig = sig.get('sell_trigger', 0)
                # ★ v24: 价格已涨超卖点(触发价)时, need 后加 ! 提示已触发
                over = '!' if price >= st_trig else ''
                parts.append('REV-T: need{} {:+.2f} to Y{:.2f}'.format(over, st_trig - price, st_trig))
            else: parts.append('REV-T: off')
            if self.st.get('do_long'):
                bt_dyn = sig.get('buy_trigger', 0)
                # ★ v24: 价格已跌破买点(触发价)时, need 后加 ! 提示已触发
                over = '!' if price <= bt_dyn else ''
                parts.append('FWD-T: need{} {:+.2f} to Y{:.2f}'.format(over, bt_dyn - price, bt_dyn))
            else: parts.append('FWD-T: off')
            if self.st.get('locked'): parts.append('LOCKED')
            _log('[HB] {} Y{:.2f} {}'.format(fs, price, ' | '.join(parts)))
        elif fs == STATE_SPIKING:
            peak = self.st.get('peak_price', 0); pb = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[HB] {} Y{:.2f} peak Y{:.2f} pullback {:.2f}%'.format(fs, price, peak, pb))
        elif fs in (STATE_SOLD, STATE_DIPPING):
            sp = self.st.get('sell_fill_price', 0); bt = self.st.get('buyback_target', 0)
            ladder = self.st.get('ladder_sell_target', 0)
            extra = ' ladder Y{:.2f}'.format(ladder) if ladder > 0 else ''
            if sp > 0: _log('[HB] {} Y{:.2f} sell Y{:.2f} {:+.1f}% buyback Y{:.2f}{}'.format(
                fs, price, sp, (price - sp) / sp * 100, bt, extra))
        elif fs == STATE_BT_DIPPING:
            dip = self.st.get('bt_dip_price', price); bounce = (price - dip) / dip * 100 if dip > 0 else 0
            _log('[HB] {} Y{:.2f} dip Y{:.2f} bounce {:.2f}%'.format(fs, price, dip, bounce))
        elif fs == STATE_BT_BOUGHT:
            bp = self.st.get('bt_buy_fill_price', 0); target = self.st.get('bt_sellback_target', 0)
            ladder = self.st.get('ladder_buy_target', 0)
            extra = ' ladder Y{:.2f}'.format(ladder) if ladder > 0 else ''
            if bp > 0: _log('[HB] {} Y{:.2f} buy Y{:.2f} {:+.1f}% sellback Y{:.2f}{}'.format(
                fs, price, bp, (price - bp) / bp * 100, target, extra))
        elif fs == STATE_BT_SPIKING:
            peak = self.st.get('bt_sell_peak_price', price); pb = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[HB] {} Y{:.2f} peak Y{:.2f} pullback {:.2f}%'.format(fs, price, peak, pb))
        else: _log('[HB] {} Y{:.2f}'.format(fs, price))


def run_backtest_mode(start='20250801', end='20260806'):
    print('=' * 55 + '\n  Backtest QMT mini REV-T v29\n  Range: {} ~ {}\n'.format(start, end) + '=' * 55)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from backtest.backtest_v10_xtdata import XTDataManager, BacktestEngine
    data_mgr = XTDataManager('601869.SH', data_dir='C:/QMT/datadir')
    data_mgr.load_daily(start=start, end=end)
    engine = BacktestEngine(data_mgr); engine.run(start_date=start, end_date=end)
    engine.print_report(); engine.save_csv()


def main():
    parser = argparse.ArgumentParser(description='MiniQMT internal daily Trading v29 — 严格成交判定 + 下单前仓位检查 + sell_trigger已消耗更新 (MOM短线已屏蔽)')
    parser.add_argument('--mode', '-m', default='signal', choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250801'); parser.add_argument('--end', default='20260806')
    args = parser.parse_args()
    if args.mode == 'backtest': run_backtest_mode(args.start, args.end); return
    logger = FileLogger(STOCK_CODE, version='v29'); set_logger(logger)
    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n!!! LIVE TRADING CONFIRMATION !!!\nTarget: {}({}) Account: {}'.format(STOCK_NAME, STOCK_CODE, ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes': print('Cancelled'); logger.close(); return
    StrategyRunner(dry_run=dry_run).run()


if __name__ == '__main__':
    main()
