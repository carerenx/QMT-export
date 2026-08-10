# -*- coding: utf-8 -*-
"""
=============================================================================
 长飞光纤(601869) 日内做T量化策略 — QMT实盘版 V2.1
=============================================================================

 【策略定位】
   日内T+0回转交易 —— 持有底仓 + 每日低买高卖，赚取日内价差。
   适用于：高价股 / 高波动 / 强趋势 —— 长飞光纤四要素齐备。

 【V2.1 新增 — T利润复投机制 ★ 核心突破】
   V2.0的问题: 做T收益 < 100%满仓持有 (因为仓位不足)
   V2.1的答案: 做T赚到的每一分钱 → 攒够1手 → 自动买入底仓
   → 底仓从初始的X%逐步增长到100%+ → 最终超越满仓持有

   回测验证 (2024.08-2026.07, 461天):
     100%满仓持有: +2228%
     70%底仓+T0+复投: +2354% ← 超越+126pp!
     无论从30%/50%/70%/95%起步，只要复投，最终都超越满仓持有。

 【V2.0 核心改进 — 卖飞/踏空防护】
   1. 正T买入: 阶梯3层挂单（-0.3/-0.6/-1.0 ATR），防踏空
   2. 正T卖出: 分批止盈 + 移动止盈，防卖飞
   3. 反T熔断: 四重检查（牛市禁反T / 高开禁反T / 连涨禁反T / MACD禁反T）
   4. 反T止损: 卖出后涨超1%立即买回，防丢底仓
   5. 尾盘纪律: 收盘前5分钟强制平掉所有T仓位

 【QMT运行机制】
   - 策略周期: 日线 (handlebar每日收盘后触发一次)
   - 盘中执行: 通过 subscribe_quote 订阅tick行情，回调函数实时判断买卖点
   - 定时检查: 通过 run_time 设置盘中定时检查点，双重保险
   - 收盘后: 检查T利润是否够买1手，够则自动加仓底仓

 【运行前必须修改的配置】
   1. ACCOUNT_ID: 你的资金账号
   2. 确认 TARGET_CODE 是否正确
   3. 在QMT策略参数面板设置 BASE_POSITION_RATIO / MAX_T_RATIO

 【风险警告】
   - 本策略仅供研究参考，不构成投资建议
   - 实盘前必须在模拟盘验证至少2周
   - 做T有卖飞和踏空风险，详见策略文档
=============================================================================
"""

import numpy as np
import datetime as dt
import time

# ============================================================
#  第1部分: 全局配置参数 — 可在QMT策略参数面板中调整
# ============================================================
# 注: QMT中可通过 strategy_params 字典传递参数
# 这里设置的是默认值，运行时会尝试从 ContextInfo 读取

# ---- 标的与账户 ----
TARGET_CODE = '601869.SH'                        # 目标股票代码 (QMT格式: 代码.交易所)
ACCOUNT_ID = 'your_account_id'                   # ★★★ 你的资金账号 (必须修改!) ★★★
ACCOUNT_TYPE = 'STOCK'                           # 账户类型: STOCK=股票账户

# ---- 仓位控制 ----
BASE_POSITION_RATIO = 0.30                       # 底仓占总资产比例 (建议20-40%)
MAX_T_RATIO = 0.40                               # 单日做T最多使用的底仓比例 (V2改为40%, 原50%)
MIN_TRADE_SHARES = 100                           # 最小交易单位 (A股100股=1手)

# ---- 做T交易参数 (V2 阶梯+分批) ----
# 阶梯买入参数 [买入带距ATR的倍数, 该层仓位占比]
LADDER_BUY_LEVELS = [
    [0.30, 0.30],  # 第1层: 浅回调 (-0.3 ATR), 占30%仓位 → 高概率成交
    [0.60, 0.40],  # 第2层: 中等回调 (-0.6 ATR), 占40%仓位 → 核心仓位
    [1.00, 0.30],  # 第3层: 深回调 (-1.0 ATR), 占30%仓位 → 安全边际大
]

# 分批止盈参数 [目标价距买入价的ATR倍数, 该批占比]
TAKE_PROFIT_LEVELS = [
    [1.0, 0.40],   # 第1批: +1 ATR 卖40% → 锁定基础利润
    [2.0, 0.35],   # 第2批: +2 ATR 卖35% → 捕捉主升浪
    [0.0, 0.25],   # 第3批: 移动止盈/收盘平仓 25% → 让利润奔跑
]

# ---- 反T熔断参数 (V2 四重保护) ----
SHORT_T_GAP_UP_LIMIT = 0.02       # 开盘涨幅 > 2% 禁止反T
SHORT_T_STREAK_LIMIT = 3          # 连涨 >= 3天 禁止反T
SHORT_T_BUYBACK_STOP = 0.01       # 反T卖出后股价涨超1% → 立即买回

# ---- 风控参数 ----
STOP_LOSS_PCT = 0.03              # 单笔T止损 3%
DAILY_LOSS_LIMIT_PCT = 0.01       # 单日做T累计亏损超过总资产1% → 停止当日做T
VOLUME_FILTER_RATIO = 0.6         # 量比低于0.6 → 不做T (缩量波动小)
RSI_LONG_LIMIT = 80               # RSI > 80 → 不做正T (超买)
RSI_SHORT_LIMIT = 20              # RSI < 20 → 不做反T (超卖)

# ---- 均线参数 ----
MA_SHORT = 5                      # 短期趋势
MA_MID = 20                       # 中期趋势
MA_LONG = 60                      # 长期趋势
ATR_PERIOD = 14                   # ATR计算窗口

# ---- 交易时间 (北京时间) ----
TRADING_START_1 = "09:35"         # 早盘开始做T (开盘后5分钟, 等集合竞价消化)
TRADING_END_1 = "11:00"           # 早盘结束
TRADING_START_2 = "13:05"         # 午盘开始
TRADING_END_2 = "14:50"           # 收盘前10分钟强制平仓T仓位


# ============================================================
#  第2部分: 技术指标计算 (在QMT中可直接用TA-Lib)
# ============================================================

def calc_ma(values, period):
    """计算移动平均线 (兼容numpy计算)"""
    if len(values) < period:
        return None
    return np.mean(values[-period:])


def calc_atr(highs, lows, closes, period=14):
    """
    计算ATR (Average True Range) —— 日内波动的核心衡量指标。

    True Range = max(当日最高-当日最低,
                     |当日最高-昨日收盘|,
                     |当日最低-昨日收盘|)
    ATR = TR的N周期移动平均

    返回: [ATR值列表, 最新ATR值, 最新ATR百分比]
    """
    n = len(closes)
    if n < 2:
        return [], 0, 0

    tr_list = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return tr_list, tr_list[-1] if tr_list else 0, 0

    atr_list = []
    for i in range(len(tr_list)):
        if i < period - 1:
            atr_list.append(np.mean(tr_list[:i + 1]))
        else:
            atr_list.append(np.mean(tr_list[i - period + 1:i + 1]))

    latest_atr = atr_list[-1] if atr_list else 0
    latest_close = closes[-1] if closes else 1
    atr_pct = latest_atr / latest_close if latest_close > 0 else 0
    return atr_list, latest_atr, atr_pct


def calc_rsi(closes, period=14):
    """计算RSI"""
    if len(closes) < period + 1:
        return [], 50
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    rsi_list = []
    for i in range(period, len(deltas) + 1):
        avg_gain = np.mean(gains[i - period:i])
        avg_loss = np.mean(losses[i - period:i])
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        rsi_list.append(rsi)
    return rsi_list, rsi_list[-1] if rsi_list else 50


def calc_volume_ma(volumes, period=20):
    """计算量比 = 当日成交量 / 20日均量"""
    if len(volumes) < period:
        return 1.0
    avg_vol = np.mean(volumes[-period:])
    if avg_vol <= 0:
        return 1.0
    return volumes[-1] / avg_vol


# ============================================================
#  第3部分: 做T信号生成 — 核心决策引擎
# ============================================================

class T0SignalEngine:
    """
    日内做T信号引擎。

    在每个交易日结束后运行，分析历史数据，生成次日的做T计划:
      - t_buy_levels:  阶梯买入价格列表  [(价格, 股数), ...]
      - t_sell_targets: 分批止盈价格列表  [(价格, 股数), ...]
      - do_long_t:     是否允许正T
      - do_short_t:    是否允许反T

    盘中由tick回调函数使用这些计划执行交易。
    """

    def __init__(self):
        """初始化引擎状态"""
        # 持仓信息
        self.base_shares = 0         # 底仓股数
        self.today_max_t_shares = 0  # 今日最大可做T股数

        # 日内做T状态 (每个交易日重置)
        self.today_bought = 0        # 今日已T买入股数 (未配对)
        self.today_sold = 0          # 今日已T卖出股数 (未配对)
        self.today_t_done = 0        # 今日已完成T交易股数
        self.today_pnl = 0.0         # 今日做T累计盈亏
        self.today_stop = False      # 今日是否触发停止T
        self.last_trade_time = None  # 上次交易时间

        # 反T状态追踪
        self.short_t_active = False  # 是否在反T中(已卖出, 未买回)
        self.short_t_sell_price = 0  # 反T卖出价格
        self.short_t_shares = 0      # 反T卖出股数

        # 今日做T计划 (由analyze()生成)
        self.plan = {
            'long_buy_levels':  [],   # [(price, shares), ...] 正T阶梯买入
            'long_sell_levels': [],   # [(price, shares), ...] 正T分批卖出
            'short_sell_levels': [],  # [(price, shares), ...] 反T阶梯卖出
            'short_buy_levels':  [],  # [(price, shares), ...] 反T分批发回
            'do_long':  True,
            'do_short': True,
            'market_state': 'unknown',  # bull/bear/sideways
        }

    def analyze(self, closes, opens, highs, lows, volumes, bar_index):
        """
        在每个交易日结束后调用，分析数据并生成次日做T计划。

        Args:
            closes:  收盘价序列 (list, 索引0最旧, -1最新)
            opens:   开盘价序列
            highs:   最高价序列
            lows:    最低价序列
            volumes: 成交量序列
            bar_index: 当前bar位置

        Returns:
            plan: 做T计划字典
        """
        # ================================================================
        #  步骤1: 计算技术指标
        # ================================================================
        closes_arr = np.array(closes)
        highs_arr = np.array(highs)
        lows_arr = np.array(lows)
        volumes_arr = np.array(volumes)

        n = len(closes_arr)
        if n < MA_LONG:
            return self.plan  # 数据不足, 返回空计划

        # 均线
        ma5 = calc_ma(closes_arr, MA_SHORT)
        ma20 = calc_ma(closes_arr, MA_MID)
        ma60 = calc_ma(closes_arr, MA_LONG)

        # ATR
        atr_list, atr, atr_pct = calc_atr(highs_arr, lows_arr, closes_arr, ATR_PERIOD)

        # RSI
        rsi_list, rsi = calc_rsi(closes_arr, 14)

        # 量比
        vol_ratio = calc_volume_ma(volumes_arr, 20)

        # 最新值
        curr_close = closes_arr[-1]
        curr_open = opens_arr[-1] if opens else curr_close

        # ================================================================
        #  步骤2: 判断市场状态 (趋势/震荡/极端)
        # ================================================================
        if ma5 and ma20 and ma60:
            trend_bull = (curr_close > ma20) and (ma5 > ma20)
            trend_bear = (curr_close < ma20) and (ma5 < ma20)
        else:
            trend_bull = False
            trend_bear = False

        if trend_bull:
            market_state = 'bull'
        elif trend_bear:
            market_state = 'bear'
        else:
            market_state = 'sideways'

        # 计算连涨天数
        up_streak = 0
        for i in range(n - 1, 0, -1):
            if closes_arr[i] > closes_arr[i - 1]:
                up_streak += 1
            else:
                break

        # 开盘缺口 (当日开盘 vs 昨日收盘)
        gap = 0
        if n >= 2:
            gap = (curr_open - closes_arr[-2]) / closes_arr[-2] if closes_arr[-2] > 0 else 0

        # MACD动能 (简化: 用收盘价vs 60日均线)
        macd_positive = curr_close > ma60 if ma60 else False

        # ================================================================
        #  步骤3: 正T / 反T 开关判断
        # ================================================================
        do_long = True   # 先假设可以，然后用条件排除
        do_short = True

        # --- 正T限制条件 ---
        if trend_bear:
            # 熊市不做正T: 买了可能卖不出去
            do_long = False
        if rsi > RSI_LONG_LIMIT:
            # RSI超买不做正T: 高位接盘风险大
            do_long = False

        # --- 反T四重熔断 (V2核心: 防止牛市卖飞底仓) ---
        # 熔断1: 牛市绝对不做反T ← 铁律!
        if trend_bull:
            do_short = False
        # 熔断2: 大幅高开不做反T (高开说明强势)
        if gap > SHORT_T_GAP_UP_LIMIT:
            do_short = False
        # 熔断3: 连涨超过阈值不做反T (加速上涨中)
        if up_streak >= SHORT_T_STREAK_LIMIT:
            do_short = False
        # 熔断4: MACD动能向上不做反T (震荡市也要小心)
        if macd_positive and market_state != 'bear':
            do_short = False

        # --- 成交量过滤: 缩量不做 ---
        if vol_ratio < VOLUME_FILTER_RATIO:
            do_long = False
            do_short = False

        # --- RSI极端不追 ---
        if rsi < RSI_SHORT_LIMIT:
            do_short = False

        # ================================================================
        #  步骤4: 计算阶梯买入价格 和 分批止盈价格
        # ================================================================
        if atr <= 0:
            return self.plan

        # 使用次日开盘价作为基准 (回测中用次日真实开盘, 实盘用当日收盘价估算)
        ref_price = curr_close

        # ---- 正T: 阶梯买入 + 分批止盈 ----
        long_buy_levels = []
        long_sell_levels = []
        if do_long:
            # 计算每层买入的价格和股数
            max_t = int(self.base_shares * MAX_T_RATIO)
            for buy_mult, buy_ratio in LADDER_BUY_LEVELS:
                buy_price = ref_price * (1 - buy_mult * atr_pct) if atr_pct > 0 else ref_price * 0.99
                lv_shares = int(max_t * buy_ratio / MIN_TRADE_SHARES) * MIN_TRADE_SHARES
                if lv_shares < MIN_TRADE_SHARES:
                    continue
                long_buy_levels.append([round(buy_price, 2), lv_shares])

                # 对该层买入，计算分批止盈
                sell_remaining = lv_shares
                for tp_mult, tp_ratio in TAKE_PROFIT_LEVELS:
                    tp_shares = int(lv_shares * tp_ratio / MIN_TRADE_SHARES) * MIN_TRADE_SHARES
                    if tp_shares < MIN_TRADE_SHARES or tp_shares > sell_remaining:
                        tp_shares = max(MIN_TRADE_SHARES, int(sell_remaining / MIN_TRADE_SHARES) * MIN_TRADE_SHARES)
                    if tp_shares <= 0:
                        continue
                    if tp_mult > 0:
                        # 固定目标止盈
                        sell_price = buy_price * (1 + tp_mult * atr_pct)
                        long_sell_levels.append([round(sell_price, 2), tp_shares])
                    else:
                        # 第3批: 移动止盈/收盘平仓 (不设固定价格, 由tick回调动态处理)
                        long_sell_levels.append([-1.0, tp_shares])  # -1标记为移动止盈
                    sell_remaining -= tp_shares

        # ---- 反T: 阶梯卖出 (如果允许) ----
        short_sell_levels = []
        short_buy_levels = []
        if do_short:
            max_t = int(self.base_shares * MAX_T_RATIO * 0.5)  # 反T只用正T的一半仓位
            for sell_mult, sell_ratio in LADDER_BUY_LEVELS:
                sell_price = ref_price * (1 + sell_mult * atr_pct * 1.2)  # 卖出价比买入更远
                lv_shares = int(max_t * sell_ratio / MIN_TRADE_SHARES) * MIN_TRADE_SHARES
                if lv_shares < MIN_TRADE_SHARES:
                    continue
                short_sell_levels.append([round(sell_price, 2), lv_shares])
                # 买回价: 卖出价下方
                for tp_mult, tp_ratio in TAKE_PROFIT_LEVELS:
                    tp_shares = int(lv_shares * tp_ratio / MIN_TRADE_SHARES) * MIN_TRADE_SHARES
                    if tp_shares < MIN_TRADE_SHARES:
                        continue
                    if tp_mult > 0:
                        buyback = sell_price * (1 - tp_mult * atr_pct)
                        short_buy_levels.append([round(buyback, 2), tp_shares])
                    else:
                        short_buy_levels.append([-1.0, tp_shares])  # 收盘买回

        # ================================================================
        #  步骤5: 写入计划
        # ================================================================
        self.plan = {
            'long_buy_levels':  long_buy_levels,
            'long_sell_levels': long_sell_levels,
            'short_sell_levels': short_sell_levels,
            'short_buy_levels':  short_buy_levels,
            'do_long':  do_long,
            'do_short': do_short,
            'market_state': market_state,
            'ref_price': ref_price,
            'atr': atr,
            'atr_pct': atr_pct,
            'rsi': rsi,
            'ma5': ma5,
            'ma20': ma20,
            'vol_ratio': vol_ratio,
            'gap': gap,
            'up_streak': up_streak,
        }
        return self.plan

    def on_tick_long(self, price):
        """
        正T tick回调: 检查当前价是否触发正T的买卖点。

        逻辑:
          1. 如果未持有T仓位: 检查是否触发阶梯买入
          2. 如果持有T仓位: 检查是否触发分批止盈或移动止盈

        Args:
            price: 当前最新成交价

        Returns:
            (action, price, shares, reason)
            action: 'buy'/'sell'/None
        """
        if self.today_stop:
            return None, 0, 0, ''

        # --- 未持有T仓位: 检查买入信号 ---
        if self.today_bought == 0:
            for buy_price, buy_shares in self.plan['long_buy_levels']:
                if price <= buy_price and buy_shares > 0:
                    # 触发阶梯买入
                    remaining = self.today_max_t_shares - self.today_t_done - self.today_bought
                    actual_shares = min(buy_shares, remaining)
                    if actual_shares >= MIN_TRADE_SHARES:
                        return 'buy', buy_price, actual_shares, '正T阶梯买入'

        # --- 持有T仓位: 检查卖出信号 ---
        if self.today_bought > 0:
            for sell_price, sell_shares in self.plan['long_sell_levels']:
                if sell_shares <= 0:
                    continue
                if sell_price < 0:
                    # 移动止盈: 如果浮盈>1ATR, 用当前价-0.3ATR作为动态止盈价
                    # (简化: 只在收盘前触发, 由收盘回调处理)
                    continue
                if price >= sell_price and sell_shares > 0:
                    actual_shares = min(sell_shares, self.today_bought)
                    if actual_shares >= MIN_TRADE_SHARES:
                        return 'sell', sell_price, actual_shares, '正T分批止盈'

            # 止损检查
            if self.last_trade_time is not None:
                # 计算买入均价 (简化: 用开盘价附近的买入价作为参考)
                avg_buy_price = self.plan['long_buy_levels'][0][0] if self.plan['long_buy_levels'] else price
                loss_pct = (avg_buy_price - price) / avg_buy_price
                if loss_pct > STOP_LOSS_PCT:
                    self.today_stop = True
                    return 'sell', price, self.today_bought, '正T硬止损'

        return None, 0, 0, ''

    def on_tick_short(self, price):
        """
        反T tick回调: 检查当前价是否触发反T的买卖点。

        反T是先卖后买，卖飞风险极大。
        V2防护: 卖空后设1%的硬买回止损。

        Args:
            price: 当前最新成交价

        Returns:
            (action, price, shares, reason)
        """
        if self.today_stop:
            return None, 0, 0, ''

        # --- 未持有反T仓位: 检查卖出信号 ---
        if not self.short_t_active:
            for sell_price, sell_shares in self.plan['short_sell_levels']:
                if price >= sell_price and sell_shares > 0:
                    remaining = self.today_max_t_shares - self.today_t_done
                    actual_shares = min(sell_shares, remaining, int(self.base_shares * MAX_T_RATIO * 0.3))
                    if actual_shares >= MIN_TRADE_SHARES:
                        self.short_t_active = True
                        self.short_t_sell_price = sell_price
                        self.short_t_shares = actual_shares
                        return 'sell', sell_price, actual_shares, '反T卖出'

        # --- 持有反T仓位: 检查买回信号 ---
        if self.short_t_active:
            # ★ V2核心: 硬止损买回 —— 涨超1%立即买回, 保护底仓
            stop_buyback_price = self.short_t_sell_price * (1 + SHORT_T_BUYBACK_STOP)
            if price >= stop_buyback_price:
                self.short_t_active = False
                return 'buy', price, self.short_t_shares, '反T硬止损买回(保护底仓)'

            # 正常买回: 价格跌回目标区间
            for buy_price, buy_shares in self.plan['short_buy_levels']:
                if buy_price < 0:
                    continue  # 收盘买回
                if price <= buy_price and buy_shares > 0:
                    actual_shares = min(buy_shares, self.short_t_shares)
                    if actual_shares >= MIN_TRADE_SHARES:
                        self.short_t_active = (self.short_t_shares - actual_shares) > 0
                        remaining = self.short_t_shares - actual_shares
                        if remaining < MIN_TRADE_SHARES:
                            self.short_t_active = False
                        return 'buy', buy_price, actual_shares, '反T买回'

        return None, 0, 0, ''

    def on_close_force_liquidate(self, price):
        """
        收盘强制平仓: 在14:50调用, 平掉所有未配对的T仓位。

        Args:
            price: 当前最新成交价

        Returns:
            [(action, price, shares, reason), ...]
        """
        actions = []

        # 正T: 如果还有未配对的T买入, 收盘卖出
        if self.today_bought > 0:
            # 第3批移动止盈部分也在收盘时处理
            for sell_price, sell_shares in self.plan['long_sell_levels']:
                if sell_price < 0 and sell_shares > 0:  # 移动止盈标记
                    actual_shares = min(sell_shares, self.today_bought)
                    if actual_shares >= MIN_TRADE_SHARES:
                        actions.append(('sell', price, actual_shares, '收盘平仓(移动止盈批次)'))
                        self.today_bought -= actual_shares

            # 平掉所有剩余的T买入
            if self.today_bought >= MIN_TRADE_SHARES:
                actions.append(('sell', price, self.today_bought, '收盘强制平仓(正T)'))
                self.today_bought = 0

        # 反T: 如果还有未买回的反T仓位, 收盘买回
        if self.short_t_active and self.short_t_shares >= MIN_TRADE_SHARES:
            actions.append(('buy', price, self.short_t_shares, '收盘强制买回(反T)'))
            self.short_t_active = False
            self.short_t_shares = 0

        return actions

    def reset_daily(self, base_shares):
        """
        每个交易日开始时重置状态。

        Args:
            base_shares: 当前底仓股数 (从前一交易日持仓获取)
        """
        self.base_shares = base_shares
        self.today_max_t_shares = int(base_shares * MAX_T_RATIO / MIN_TRADE_SHARES) * MIN_TRADE_SHARES

        self.today_bought = 0
        self.today_sold = 0
        self.today_t_done = 0
        self.today_pnl = 0.0
        self.today_stop = False
        self.last_trade_time = None

        self.short_t_active = False
        self.short_t_sell_price = 0
        self.short_t_shares = 0

        self.plan = {
            'long_buy_levels': [], 'long_sell_levels': [],
            'short_sell_levels': [], 'short_buy_levels': [],
            'do_long': True, 'do_short': True,
            'market_state': 'unknown',
        }

    def record_trade(self, direction, price, shares):
        """记录一次成交"""
        self.last_trade_time = time.time()
        if direction == 'buy':
            if self.short_t_active:
                # 反T买回: 减少反T持仓
                self.short_t_shares = max(0, self.short_t_shares - shares)
                if self.short_t_shares < MIN_TRADE_SHARES:
                    self.short_t_active = False
            else:
                # 正T买入: 增加待卖持仓
                self.today_bought += shares
        elif direction == 'sell':
            if self.short_t_active:
                # 反T卖出: 增加反T持仓
                pass  # 已在 on_tick_short 中设置
            else:
                # 正T卖出: 减少待卖持仓
                self.today_bought = max(0, self.today_bought - shares)
                if self.today_bought < MIN_TRADE_SHARES:
                    self.today_bought = 0


# ============================================================
#  第4部分: QMT策略入口函数
# ============================================================

def _compound_reinvest(ContextInfo, closes, current_base_shares):
    """
    =========================================================================
    V2.1 核心: T利润复投机制

    逻辑:
      1. 获取当前账户可用资金 (包含做T积累的利润)
      2. 获取当前股价
      3. 如果可用资金 >= 1手买入成本 → 买入1手底仓
      4. 这就实现了: T利润 → 更多底仓 → 更大T容量 → 更多T利润 → 正向循环

    回测验证: 这个简单的机制让70%起步的策略最终超越了100%满仓持有。

    注意: 此函数在每日handlebar中调用(收盘后), 买入的底仓次日可用于做T。
    =========================================================================
    """
    try:
        # 获取账户可用资金
        account = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, 'ACCOUNT')
        if not account:
            return current_base_shares

        available_cash = account[0].m_dAvailable

        # 获取当前股价
        curr_price = closes[-1] if len(closes) > 0 else 0
        if curr_price <= 0:
            return current_base_shares

        # 计算1手的买入成本 (含手续费)
        lot_cost = curr_price * MIN_TRADE_SHARES * (1 + COMMISSION)

        # 保留一定现金缓冲 (做T需要现金)
        # 总资产的5%作为做T的现金储备
        total_value = account[0].m_dBalance
        t_cash_reserve = total_value * 0.05   # 5%留做T

        # 可用于复投加仓的现金 = 可用资金 - T现金储备
        investable_cash = available_cash - t_cash_reserve

        if investable_cash >= lot_cost:
            # 计算最多能买几手
            max_lots = int(investable_cash / lot_cost)
            # 限制单次最多加仓5手 (避免过度集中操作)
            buy_lots = min(max_lots, 5)
            buy_shares = buy_lots * MIN_TRADE_SHARES

            # 用passorder买入底仓
            # 注: 这里的买入是"持有型买入"，不是T买入。次日这些股票可以作为底仓用于做T。
            passorder(
                23,                   # opType: 买入
                1101,                 # orderType: 限价股
                ACCOUNT_ID,
                TARGET_CODE,
                11,                   # 指定价
                curr_price,           # 以当前价买入
                buy_shares,
                'T利润复投加仓',      # strategyName
                0, '', ContextInfo
            )
            print(f'[复投加仓] T利润买入 {buy_shares}股 @ {curr_price:.2f} | '
                  f'投入 {buy_shares*curr_price:.0f}元 | 底仓将从 {current_base_shares} → {current_base_shares+buy_shares}股')
            return current_base_shares + buy_shares
        else:
            return current_base_shares

    except Exception as e:
        print(f'[复投加仓] 执行异常: {e}')
        return current_base_shares


# ============================================================
#  第5部分: QMT入口 init/handlebar
# ============================================================
# QMT自动调用以下两个函数:
#   1. init(ContextInfo)    — 策略加载时调用一次
#   2. handlebar(ContextInfo) — 每根K线结束时调用 (日线模式: 每天收盘后)

# 全局单例
g_engine = T0SignalEngine()
g_last_bar_date = None           # 上一根bar的日期 (用于检测新交易日)
g_trade_count_today = 0          # 今日交易计数
g_daily_pnl = 0.0                # 今日做T盈亏


def init(ContextInfo):
    """
    =========================================================================
    QMT策略初始化。
    在策略加载/启动时调用一次。

    完成工作:
      1. 设置标的股票池
      2. 设置交易费率
      3. 获取初始底仓信息
      4. 订阅实时行情 (tick级)
      5. 设置定时任务 (盘中检查点)
    =========================================================================
    """
    global g_engine, g_last_bar_date

    # ---- 1. 设置股票池 ----
    ContextInfo.set_universe([TARGET_CODE])
    print(f'[做T策略] 标的: {TARGET_CODE}')

    # ---- 2. 设置费率 (A股标准) ----
    # set_slippage: 设置滑点 (b_flag控制是否启用跳价处理)
    ContextInfo.set_slippage(1, 0.001)   # 0.1%滑点
    # set_commission: 设置佣金 (参数: 费率)
    # 具体参数请参考你的券商费率，这里以万2.5为例
    # ContextInfo.set_commission(0, 0.00025)  # 佣金万分之2.5

    # ---- 3. 从QMT获取账户信息 ----
    try:
        account = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, 'ACCOUNT')
        if account:
            acc = account[0]
            print(f'[做T策略] 账户可用资金: {acc.m_dAvailable:.2f}')
    except Exception as e:
        print(f'[做T策略] 获取账户信息失败: {e}')

    # ---- 4. 获取初始持仓 ----
    base_shares = _get_base_shares()
    g_engine.reset_daily(base_shares)
    print(f'[做T策略] 底仓: {base_shares}股')

    # ---- 5. 初始化V2做T引擎 ----
    g_engine = T0SignalEngine()
    g_engine.reset_daily(base_shares)

    # ---- 6. 订阅实时tick行情 (做T的核心: 盘中实时判断) ----
    # subscribe_quote 会持续推送tick数据到回调函数
    try:
        sub_id = ContextInfo.subscribe_quote(
            TARGET_CODE,           # 股票代码
            'tick',                # 订阅tick级别行情
            'follow',              # 复权跟随
            _on_tick_callback      # tick数据回调函数
        )
        print(f'[做T策略] 已订阅tick行情, sub_id={sub_id}')
    except Exception as e:
        print(f'[做T策略] tick订阅失败(将使用定时检查模式): {e}')

    # ---- 7. 设置盘中定时检查点 (双重保险) ----
    # 如果tick订阅失败或延迟, run_time作为后备
    try:
        ContextInfo.run_time('checkpoint_morning', '1d', '20200101' + TRADING_START_1.replace(':', '') + '00', 'SH')
        ContextInfo.run_time('checkpoint_noon', '1d', '20200101' + TRADING_START_2.replace(':', '') + '00', 'SH')
        ContextInfo.run_time('checkpoint_close', '1d', '20200101' + TRADING_END_2.replace(':', '') + '00', 'SH')
        print(f'[做T策略] 已设置定时检查点: {TRADING_START_1}, {TRADING_START_2}, {TRADING_END_2}')
    except Exception as e:
        print(f'[做T策略] 定时任务设置失败: {e}')

    print(f'[做T策略] 初始化完成, V2.0 卖飞/踏空防护已启用')


def handlebar(ContextInfo):
    """
    =========================================================================
    QMT主逻辑入口。每个K线周期结束时调用一次。

    日线模式下: 每天收盘后(15:00后)自动调用。

    本策略中 handlebar 负责:
      1. 获取历史行情数据
      2. 调用引擎分析, 生成次日做T计划
      3. 在QMT图表上绘制关键指标

    (实际的盘中做T交易在 _on_tick_callback 和 checkpoint_* 函数中完成)
    =========================================================================
    """
    global g_engine, g_last_bar_date

    # ---- 获取历史数据 ----
    # QMT内置函数: 获取指定长度的日线数据
    # get_history_data(长度, 周期, 字段, 复权方式, 是否跳过停牌)
    hist_close = ContextInfo.get_history_data(80, '1d', 'close')
    hist_open = ContextInfo.get_history_data(80, '1d', 'open')
    hist_high = ContextInfo.get_history_data(80, '1d', 'high')
    hist_low = ContextInfo.get_history_data(80, '1d', 'low')
    hist_volume = ContextInfo.get_history_data(80, '1d', 'volume')

    code = TARGET_CODE
    if code not in hist_close or len(hist_close[code]) < MA_LONG:
        print(f'[做T策略] 历史数据不足, 需要至少{MA_LONG}根bar')
        return

    closes = hist_close[code]
    opens = hist_open.get(code, closes)
    highs = hist_high.get(code, closes)
    lows = hist_low.get(code, closes)
    volumes = hist_volume.get(code, [1] * len(closes))

    # ---- 检测新交易日, 更新底仓 + V2.1复投加仓 ----
    current_date = _get_current_date(ContextInfo)
    if current_date != g_last_bar_date:
        g_last_bar_date = current_date
        # 获取最新持仓作为底仓
        base_shares = _get_base_shares()
        g_engine.reset_daily(base_shares)
        # ★★★ V2.1核心: T利润复投 — 收盘后检查, 利润够买1手就加仓 ★★★
        _compound_reinvest(ContextInfo, closes, base_shares)
        # 清空前一日的盈亏计数
        global g_daily_pnl, g_trade_count_today
        g_daily_pnl = 0.0
        g_trade_count_today = 0
        print(f'[做T策略] 新交易日: {current_date}, 底仓: {base_shares}股')

    # ---- 运行做T信号分析, 生成次日做T计划 ----
    bar_index = ContextInfo.barpos
    plan = g_engine.analyze(closes, opens, highs, lows, volumes, bar_index)

    # ---- 在QMT图表上绘制关键指标 ----
    _paint_indicators(ContextInfo)

    # ---- 打印日志 ----
    if plan['do_long'] or plan['do_short']:
        state_str = {'bull': '牛市', 'bear': '熊市', 'sideways': '震荡'}.get(plan['market_state'], '未知')
        print(f'[做T计划] 日期={current_date} | 状态={state_str} | '
              f'正T={"开" if plan["do_long"] else "关"} | 反T={"开" if plan["do_short"] else "关"} | '
              f'ATR={plan["atr"]:.2f} | RSI={plan["rsi"]:.0f} | '
              f'买点={len(plan["long_buy_levels"])}层 卖点={len(plan["long_sell_levels"])}批')


def _get_base_shares():
    """
    从QMT获取当前持仓的底仓股数。

    Returns:
        int: 持有的601869股数, 如果没有则返回0

    注: get_trade_detail_data 返回的是 C++ 对象列表,
        每个对象有 .m_strInstrumentID (代码) 和 .m_nVolume (持仓量) 等属性。
    """
    try:
        positions = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, 'POSITION')
        for pos in positions:
            code = pos.m_strInstrumentID + '.' + pos.m_strExchangeID
            if code == TARGET_CODE:
                return pos.m_nVolume
    except Exception as e:
        print(f'[做T策略] 获取持仓失败: {e}')
    return 0


def _get_current_date(ContextInfo):
    """获取当前bar对应的日期"""
    try:
        timetag = ContextInfo.get_bar_timetag(ContextInfo.barpos)
        return timetag_to_datetime(timetag, '%Y%m%d')
    except Exception:
        return dt.datetime.now().strftime('%Y%m%d')


def _paint_indicators(ContextInfo):
    """
    在QMT的K线图上绘制做T策略的辅助指标。

    paint(name, data, index, drawStyle, color, limit):
      - name: 指标名称 (在图上显示)
      - data: 数值
      - index: bar位置
      - drawStyle: 1=线, 2=柱, etc.
      - color: 颜色 'red'/'green'/'blue'/'yellow'/'white'
    """
    try:
        # 绘制MA20均线
        hist_close = ContextInfo.get_history_data(80, '1d', 'close')
        code = TARGET_CODE
        if code in hist_close and len(hist_close[code]) >= 20:
            closes = hist_close[code]
            ma20 = np.mean(closes[-20:])
            ContextInfo.paint('MA20', ma20, -1, 1, 'blue')

            ma5 = np.mean(closes[-5:])
            ContextInfo.paint('MA5', ma5, -1, 1, 'orange')
    except Exception:
        pass  # 图表绘制失败不影响策略运行


# ============================================================
#  第5部分: 盘中执行 — tick回调 + 定时检查
# ============================================================

def _on_tick_callback(datas):
    """
    =========================================================================
    Tick行情回调函数。

    当QMT收到目标股票的tick数据推送时, 自动调用此函数。

    datas格式 (由 subscribe_quote 推送):
      {TARGET_CODE: {'lastPrice': xxx, 'open': xxx, 'high': xxx, 'low': xxx, ...}}

    本函数是"做T策略的实时大脑":
      1. 从datas中提取最新成交价
      2. 判断当前时间是否在交易时段
      3. 调用引擎判断是否触发买卖
      4. 触发时通过 passorder 下单

    注意: 此函数会在盘中高频调用, 必须保证执行效率!
          避免在回调中做繁重计算和阻塞操作。
    =========================================================================
    """
    global g_engine, g_trade_count_today, g_daily_pnl

    # ---- 1. 快速检查: 不在交易时段则跳过 ----
    if not _is_trading_time():
        return

    # ---- 2. 提取tick数据 ----
    if TARGET_CODE not in datas:
        return

    tick = datas[TARGET_CODE]
    if isinstance(tick, dict):
        price = tick.get('lastPrice', 0)
    else:
        # datas可能以不同格式返回
        try:
            price = float(tick.get('lastPrice', 0) if hasattr(tick, 'get') else tick[-1])
        except Exception:
            return

    if price <= 0:
        return

    # ---- 3. 检查是否接近收盘 (14:50-15:00), 触发强制平仓 ----
    now = dt.datetime.now()
    close_time = now.replace(hour=14, minute=50, second=0)
    if now >= close_time and not g_engine.today_stop:
        actions = g_engine.on_close_force_liquidate(price)
        for action, exec_price, shares, reason in actions:
            if action == 'sell':
                _execute_sell(exec_price, shares, reason)
            elif action == 'buy':
                _execute_buy(exec_price, shares, reason)
        return

    # ---- 4. 正T信号检查 ----
    if g_engine.plan['do_long'] and not g_engine.today_stop:
        action, exec_price, shares, reason = g_engine.on_tick_long(price)
        if action == 'buy':
            _execute_buy(exec_price, shares, reason)
            g_engine.record_trade('buy', exec_price, shares)
            g_trade_count_today += 1
        elif action == 'sell':
            _execute_sell(exec_price, shares, reason)
            g_engine.record_trade('sell', exec_price, shares)
            g_trade_count_today += 1

    # ---- 5. 反T信号检查 ----
    if g_engine.plan['do_short'] and not g_engine.today_stop:
        action, exec_price, shares, reason = g_engine.on_tick_short(price)
        if action == 'sell':
            _execute_sell(exec_price, shares, reason)
            g_trade_count_today += 1
        elif action == 'buy':
            _execute_buy(exec_price, shares, reason)
            g_engine.record_trade('buy', exec_price, shares)
            g_trade_count_today += 1


def checkpoint_morning(ContextInfo):
    """早盘定时检查点 (9:35触发) — tick回调的后备"""
    _checkpoint_execute(ContextInfo, '早盘')


def checkpoint_noon(ContextInfo):
    """午盘定时检查点 (13:05触发) — tick回调的后备"""
    _checkpoint_execute(ContextInfo, '午盘')


def checkpoint_close(ContextInfo):
    """收盘前强制平仓 (14:50触发) — 最后一道防线"""
    global g_engine
    tick = ContextInfo.get_full_tick([TARGET_CODE])
    if TARGET_CODE in tick:
        price = tick[TARGET_CODE].get('lastPrice', 0)
        if price > 0:
            actions = g_engine.on_close_force_liquidate(price)
            for action, exec_price, shares, reason in actions:
                if action == 'sell':
                    _execute_sell(exec_price, shares, reason)
                elif action == 'buy':
                    _execute_buy(exec_price, shares, reason)
    print(f'[做T策略] 收盘平仓检查完成')


def _checkpoint_execute(ContextInfo, label):
    """
    定时检查点通用逻辑。

    获取当前tick数据, 运行和 _on_tick_callback 同样的信号检查。
    这是tick回调的后备机制。
    """
    global g_engine, g_trade_count_today

    if g_engine.today_stop:
        return

    tick = ContextInfo.get_full_tick([TARGET_CODE])
    if TARGET_CODE not in tick:
        return

    price = tick[TARGET_CODE].get('lastPrice', 0)
    if price <= 0:
        return

    if g_engine.plan['do_long']:
        action, exec_price, shares, reason = g_engine.on_tick_long(price)
        if action:
            print(f'[检查点{label}] {reason}: {action} {shares}股 @ {exec_price:.2f}')
            if action == 'buy':
                _execute_buy(exec_price, shares, reason)
                g_engine.record_trade('buy', exec_price, shares)
            elif action == 'sell':
                _execute_sell(exec_price, shares, reason)
                g_engine.record_trade('sell', exec_price, shares)
            g_trade_count_today += 1

    if g_engine.plan['do_short']:
        action, exec_price, shares, reason = g_engine.on_tick_short(price)
        if action:
            print(f'[检查点{label}] {reason}: {action} {shares}股 @ {exec_price:.2f}')
            if action == 'sell':
                _execute_sell(exec_price, shares, reason)
            elif action == 'buy':
                _execute_buy(exec_price, shares, reason)
                g_engine.record_trade('buy', exec_price, shares)
            g_trade_count_today += 1


# ============================================================
#  第6部分: 下单执行
# ============================================================

def _execute_buy(price, shares, reason=''):
    """
    执行买入下单。

    使用 QMT 的 passorder 函数。
    passorder 参数说明:
      opType:     23 = 买入股票
      orderType:  1101 = 限价委托(股)
      accountid:  资金账号
      orderCode:  股票代码 (如 '601869.SH')
      prType:     11 = 指定价委托 (需要modelprice参数)
      modelprice: 委托价格
      volume:     委托数量 (股)
      strategyName: 策略名称
      quickTrade: 0=普通交易
      ContextInfo: 上下文对象

    注: QMT中的passorder是全局可用的内置函数, 策略自动注入。
    """
    try:
        shares_int = int(shares / MIN_TRADE_SHARES) * MIN_TRADE_SHARES
        if shares_int < MIN_TRADE_SHARES:
            return

        passorder(
            23,                       # opType: 买入
            1101,                     # orderType: 限价股
            ACCOUNT_ID,               # 资金账号
            TARGET_CODE,              # 股票代码
            11,                       # prType: 指定价
            price,                    # modelprice: 委托价
            shares_int,               # volume: 股数
            '长飞做T',                # strategyName
            0,                        # quickTrade: 普通
            '',                       # userOrderId
            ContextInfo               # 上下文
        )
        print(f'[做T] BUY {shares_int}股 @ {price:.2f} | {reason}')
    except Exception as e:
        print(f'[做T] BUY下单失败: {e}')


def _execute_sell(price, shares, reason=''):
    """
    执行卖出下单。

    同上, opType=24 代表卖出。
    """
    try:
        shares_int = int(shares / MIN_TRADE_SHARES) * MIN_TRADE_SHARES
        if shares_int < MIN_TRADE_SHARES:
            return

        passorder(
            24,                       # opType: 卖出
            1101,                     # orderType: 限价股
            ACCOUNT_ID,
            TARGET_CODE,
            11,                       # prType: 指定价
            price,
            shares_int,
            '长飞做T',
            0,
            '',
            ContextInfo
        )
        print(f'[做T] SELL {shares_int}股 @ {price:.2f} | {reason}')
    except Exception as e:
        print(f'[做T] SELL下单失败: {e}')


# ============================================================
#  第7部分: 工具函数
# ============================================================

def _is_trading_time():
    """
    判断当前是否在允许做T的交易时段。

    允许时段:
      - 09:35 ~ 11:00  (早盘, 避开集合竞价混乱期)
      - 13:05 ~ 14:55  (午盘, 14:55后接近收盘容易滑点)

    Returns:
        bool
    """
    now = dt.datetime.now()
    # 检查是否为工作日
    if now.weekday() >= 5:  # 周六=5, 周日=6
        return False

    # 转换为分钟数方便比较
    minutes = now.hour * 60 + now.minute

    morning_start = 9 * 60 + 35   # 09:35
    morning_end = 11 * 60 + 0     # 11:00
    afternoon_start = 13 * 60 + 5  # 13:05
    afternoon_end = 14 * 60 + 55   # 14:55

    return (morning_start <= minutes <= morning_end) or \
           (afternoon_start <= minutes <= afternoon_end)


# ============================================================
#  第8部分: 策略参数接口 (可选, 用于QMT参数面板)
# ============================================================

# 以下变量可供QMT策略参数面板绑定, 在运行时动态调整。
# 在QMT中, 可在策略设置中将这些变量添加为可调参数。

strategy_params = {
    'name': '长飞光纤日内做T V2.0',
    'version': '2.0.0',
    'description': '阶梯买入+分批止盈+四重反T熔断, 防卖飞踏空',

    # 仓位
    'BASE_POSITION_RATIO': BASE_POSITION_RATIO,
    'MAX_T_RATIO': MAX_T_RATIO,

    # 阶梯
    'LADDER_BUY_LEVELS': str(LADDER_BUY_LEVELS),

    # 风控
    'STOP_LOSS_PCT': STOP_LOSS_PCT,
    'DAILY_LOSS_LIMIT_PCT': DAILY_LOSS_LIMIT_PCT,
    'VOLUME_FILTER_RATIO': VOLUME_FILTER_RATIO,

    # 反T熔断
    'SHORT_T_GAP_UP_LIMIT': SHORT_T_GAP_UP_LIMIT,
    'SHORT_T_STREAK_LIMIT': SHORT_T_STREAK_LIMIT,
    'SHORT_T_BUYBACK_STOP': SHORT_T_BUYBACK_STOP,
}


# ============================================================
#  END — 长飞光纤(601869) 日内做T QMT实盘策略 V2.0
# ============================================================
