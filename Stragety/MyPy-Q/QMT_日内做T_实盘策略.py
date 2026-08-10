# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 日内做T量化策略 — 实盘运行版 v3.0
================================================================================
 注意：以下函数由QMT运行时注入，不在本文件中定义（IDE报红可忽略）：
   - get_trade_detail_data()  获取交易明细
   - order_shares()           指定股数下单
   - passorder()              综合交易下单
   - order_callback()         委托回调
   - deal_callback()          成交回调
   - ContextInfo.get_history_data()    获取历史K线
   - ContextInfo.get_full_tick()       获取实时分笔
   - ContextInfo.run_time()            设置定时器
   - ContextInfo.set_universe()        设置股票池
   - ContextInfo.set_account()         设置交易账号
 ================================================================================

 【策略概述】
   在持有底仓的前提下，利用个股日内波动进行低买高卖（正T）或高卖低买（反T），
   在不改变底仓数量的情况下赚取日内价差收益。

 【A股T+1下的做T原理】
   - 正T（先买后卖）：开盘下跌 → 低位买入 → 反弹高位卖出（用底仓保证可卖）
   - 反T（先卖后买）：开盘上涨 → 高位卖出底仓 → 回落后低位买回

 【运行机制】
   - init()       : 初始化标的、账号、启动3秒定时器
   - handlebar()  : 每日第一根K线计算当日做T买卖信号
   - ontimer()    : 每3秒执行一次，监控实时行情并触发交易
   - on_order()   : 委托状态回调，追踪成交情况
   - on_deal()    : 成交回报回调，更新当日做T仓位

 【核心风控（反T四重熔断）】
   熔断1: 牛市趋势（收盘>MA20且MA5>MA20）→ 严禁反T
   熔断2: 开盘涨幅 > 2% → 禁止反T
   熔断3: 连涨 >= 3天 → 禁止反T
   熔断4: 震荡市MACD向上 → 禁止反T

 【定时器策略】
   使用 run_time() 每3秒触发一次 ontimer()：
   - 盘中(09:30-14:55)：监控价格、触发挂单、检查止损
   - 尾盘(14:55+)：强制平掉所有当日T仓位，确保不过夜

 【QMT运行方式】
   在QMT"模型交易"→ 新建Python策略 → 粘贴本文件全部代码 → 选择"实盘"运行。
   注意：使用前请先在"模拟"模式下充分验证！

 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置参数
# ============================================================================

# ---------- 标的设置 ----------
STOCK_CODE   = '601869'              # 股票代码
STOCK_NAME   = '长飞光纤'            # 股票名称
STOCK_QMT    = f'{STOCK_CODE}.SH'    # QMT代码格式 (.SH上交所 / .SZ深交所)

# ---------- 策略参数 ----------
ATR_PERIOD   = 14                    # ATR计算周期
VOL_BAND_MULT = 0.6                  # 正T买入带 ATR倍数
VOL_SELL_MULT = 0.8                  # 正T卖出带 ATR倍数
GRID_LEVELS  = 3                     # 网格层数
GRID_STEP_PCT = 0.015                # 每层间距(1.5%)
MAX_T_RATIO  = 0.50                  # 单日做T最大使用底仓的50%
STOP_LOSS_PCT = 0.03                 # 单日亏损超过3%止损
MIN_LOT      = 100                   # A股1手=100股

# ---------- 风控参数 ----------
VOLUME_FILTER_RATIO = 0.6            # 量比<0.6不操作
RSI_OVERBOUGHT = 80                  # RSI超买阈值
RSI_OVERSOLD   = 20                  # RSI超卖阈值
SHORT_T_MAX_GAP_UP    = 0.02         # 高开>2%禁反T
SHORT_T_MAX_UP_STREAK = 3            # 连涨>=3天禁反T
SHORT_T_BUYBACK_STOP  = 0.01         # 反T卖飞紧急买回线(1%)

# ---------- 阶梯买入（防踏空） ----------
LADDER_LEVELS = [
    {'mult': 0.30, 'ratio': 0.30},   # 第1层: -0.3ATR, 30%仓位
    {'mult': 0.60, 'ratio': 0.40},   # 第2层: -0.6ATR, 40%仓位
    {'mult': 1.00, 'ratio': 0.30},   # 第3层: -1.0ATR, 30%仓位
]

# ---------- 分批止盈（防卖飞） ----------
TAKE_PROFIT_LEVELS = [
    {'atr_mult': 1.0,  'ratio': 0.40},   # 第1批: +1ATR 卖40%
    {'atr_mult': 2.0,  'ratio': 0.35},   # 第2批: +2ATR 卖35%
    {'atr_mult': None, 'ratio': 0.25},   # 第3批: 收盘平仓25%
]

# ---------- 定时器 & 时间 ----------
TIMER_INTERVAL = '3nSecond'          # 定时器间隔(每3秒)
MORNING_START  = '09:30:00'          # 早盘开始
MORNING_END    = '11:30:00'          # 早盘结束
AFTERNOON_START = '13:00:00'         # 下午开始
FORCE_CLOSE_TIME = '14:55:00'        # 强制平仓时间

# ---------- 历史数据 ----------
HIST_DATA_LEN = 80                   # 取多少根日线计算指标

# ---------- 交易成本 ----------
COMMISSION  = 0.00025                # 佣金万分之2.5
STAMP_TAX   = 0.001                  # 印花税(仅卖出)


# ============================================================================
# 第二部分：技术指标计算（纯Python，不依赖numpy/pandas）
# ============================================================================

def _calc_ma(values, period):
    """简单移动平均。返回与values等长的list，前period-1位为0"""
    n = len(values)
    ma = [0.0] * n
    for i in range(period - 1, n):
        ma[i] = sum(values[i - period + 1 : i + 1]) / period
    return ma


def _calc_atr(highs, lows, closes, period=14):
    """
    计算ATR（平均真实波幅）。
    True Range = max(当日振幅, |高-昨收|, |低-昨收|)
    """
    n = len(closes)
    tr_list = [0.0] * n
    for i in range(1, n):
        tr_list[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
    atr = [0.0] * n
    for i in range(period, n):
        atr[i] = sum(tr_list[i - period + 1 : i + 1]) / period
    return atr


def _calc_rsi(closes, period=14):
    """计算RSI（相对强弱指标）。"""
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    rsi = [50.0] * n
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi[period] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi[i + 1] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    return rsi


def _calc_macd_hist(closes, fast=12, slow=26, signal=9):
    """计算MACD柱值（histogram）。返回与closes等长的list"""
    n = len(closes)
    ema_fast = [closes[0]] * n
    ema_slow = [closes[0]] * n
    macd_line = [0.0] * n
    sig_line  = [0.0] * n
    hist      = [0.0] * n
    af, bs, ss = 2.0 / (fast + 1), 2.0 / (slow + 1), 2.0 / (signal + 1)
    for i in range(1, n):
        ema_fast[i] = closes[i] * af + ema_fast[i - 1] * (1 - af)
        ema_slow[i] = closes[i] * bs + ema_slow[i - 1] * (1 - bs)
        macd_line[i] = ema_fast[i] - ema_slow[i]
        sig_line[i]  = macd_line[i] * ss + sig_line[i - 1] * (1 - ss)
        hist[i]      = macd_line[i] - sig_line[i]
    return hist


def _calc_up_streak(closes):
    """计算连涨天数。"""
    n = len(closes)
    streak = [0] * n
    for i in range(1, n):
        streak[i] = streak[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return streak


def _calc_gap(opens, closes):
    """计算每日开盘缺口（相对昨收）。"""
    n = len(opens)
    gap = [0.0] * n
    for i in range(1, n):
        gap[i] = (opens[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] > 0 else 0.0
    return gap


def compute_daily_signal(opens, highs, lows, closes, volumes):
    """
    根据历史日线数据，计算当日的做T信号和买卖价位。

    参数:
        opens/highs/lows/closes/volumes: list[float]，历史日线序列

    返回:
        dict — 当日的做T计划，包括:
          - do_long_t / do_short_t  : 正T/反T是否允许
          - trend                   : 'bull' | 'bear' | 'sideways'
          - buy_levels / sell_levels: 买卖挂单列表 [{'price':..., 'ratio':..., 'layer':...}, ...]
          - atr / atr_pct / rsi     : 当前技术指标值
          - open_price / ref_price  : 当日开盘价/参考价
    """
    n = len(closes)
    if n < 60:
        return None

    # ---- 最新值 ----
    curr_open   = opens[-1]
    curr_close  = closes[-1]
    curr_volume = volumes[-1]

    # ---- ATR ----
    atr_arr = _calc_atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1]
    if curr_atr <= 0:
        curr_atr = curr_close * 0.03
    curr_atr_pct = curr_atr / curr_close if curr_close > 0 else 0.03

    # ---- 均线 ----
    ma5  = _calc_ma(closes, 5)[-1]
    ma20 = _calc_ma(closes, 20)[-1]

    # ---- 趋势 ----
    trend_bull = (curr_close > ma20) and (ma5 > ma20)
    trend_bear = (curr_close < ma20) and (ma5 < ma20)
    trend = 'bull' if trend_bull else ('bear' if trend_bear else 'sideways')

    # ---- RSI ----
    curr_rsi = _calc_rsi(closes)[-1]

    # ---- MACD ----
    curr_macd_hist = _calc_macd_hist(closes)[-1]

    # ---- 量比 ----
    ma20_vol = _calc_ma(volumes, 20)
    curr_vol_ratio = curr_volume / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # ---- 连涨 ----
    curr_up_streak = _calc_up_streak(closes)[-1]

    # ---- 缺口 ----
    curr_gap = _calc_gap(opens, closes)[-1]

    # ================================================================
    #  方向决策
    # ================================================================
    do_long_t  = True
    do_short_t = True
    short_blocked = False

    # 缩量过滤
    if curr_vol_ratio < VOLUME_FILTER_RATIO:
        do_long_t = do_short_t = False

    # RSI极值
    if curr_rsi > RSI_OVERBOUGHT:
        do_long_t = False
    if curr_rsi < RSI_OVERSOLD:
        do_short_t = False

    # 趋势方向
    if trend == 'bull':
        do_short_t = False
        short_blocked = True

    # 反T四重熔断
    if do_short_t:
        if curr_gap > SHORT_T_MAX_GAP_UP:
            do_short_t = short_blocked = False
        if curr_up_streak >= SHORT_T_MAX_UP_STREAK:
            do_short_t = short_blocked = False
        if curr_macd_hist > 0 and trend != 'bear':
            do_short_t = short_blocked = False

    # ================================================================
    #  买卖价位计算
    # ================================================================
    buy_levels  = []
    sell_levels = []

    if do_long_t:
        for lv in LADDER_LEVELS:
            buy_levels.append({
                'price': round(curr_open - curr_atr * lv['mult'], 2),
                'ratio': lv['ratio'],
                'layer': f'正T买{lv["mult"]}ATR',
            })
        for lv in LADDER_LEVELS:
            for tp in TAKE_PROFIT_LEVELS:
                if tp['atr_mult'] is not None:
                    bp = curr_open - curr_atr * lv['mult']
                    sell_levels.append({
                        'price': round(bp * (1.0 + curr_atr_pct * tp['atr_mult']), 2),
                        'ratio': lv['ratio'] * tp['ratio'],
                        'layer': f'正T卖+{tp["atr_mult"]}ATR',
                    })

    if do_short_t:
        for lv in LADDER_LEVELS:
            sell_levels.append({
                'price': round(curr_open + curr_atr * lv['mult'] * 1.2, 2),
                'ratio': lv['ratio'],
                'layer': f'反T卖{lv["mult"]}ATR',
            })
        for lv in LADDER_LEVELS:
            for tp in TAKE_PROFIT_LEVELS:
                if tp['atr_mult'] is not None:
                    sp = curr_open + curr_atr * lv['mult'] * 1.2
                    buy_levels.append({
                        'price': round(sp * (1.0 - curr_atr_pct * tp['atr_mult']), 2),
                        'ratio': lv['ratio'] * tp['ratio'],
                        'layer': f'反T买回-{tp["atr_mult"]}ATR',
                    })

    # 网格做T
    if (do_long_t or do_short_t):
        for level in range(1, GRID_LEVELS + 1):
            gb = curr_open * (1.0 - GRID_STEP_PCT * level)
            gs = curr_open * (1.0 + GRID_STEP_PCT * level)
            gr = 1.0 / GRID_LEVELS / 3.0
            if do_long_t:
                buy_levels.append({'price': round(gb, 2), 'ratio': gr, 'layer': f'网格买L{level}'})
                sell_levels.append({'price': round(gs, 2), 'ratio': gr, 'layer': f'网格卖L{level}'})

    return {
        'do_long_t':     do_long_t,
        'do_short_t':    do_short_t,
        'short_blocked': short_blocked,
        'trend':         trend,
        'buy_levels':    buy_levels,
        'sell_levels':   sell_levels,
        'open_price':    curr_open,
        'close_yday':    curr_close,
        'atr':           curr_atr,
        'atr_pct':       curr_atr_pct,
        'rsi':           curr_rsi,
        'vol_ratio':     curr_vol_ratio,
        'macd_hist':     curr_macd_hist,
        'ma5':           ma5,
        'ma20':          ma20,
        'up_streak':     curr_up_streak,
        'gap':           curr_gap,
    }


# ============================================================================
# 第三部分：QMT 策略入口
# ============================================================================

def init(ContextInfo):
    """
    ================================================================
    QMT策略初始化 — 策略加载时由QMT框架自动调用一次。
    ================================================================

    完成以下工作：
      1. 设置股票池
      2. 设置交易账号
      3. 启动定时器（每3秒执行ontimer）
      4. 初始化策略状态字典
      5. 首次计算做T信号
    """
    # ---- 1. 设置股票池（QMT只推送池内股票的数据） ----
    # [Python API, set_universe]
    ContextInfo.set_universe([STOCK_QMT])

    # ---- 2. 设置交易账号 ----
    # 用 'ACCOUNT' 表示使用当前登录的默认账号
    # 如果有多账号，替换为具体资金账号如 '6000000248'
    # [Python API, set_account: 可多次调用设置多个账号]
    ContextInfo.set_account('ACCOUNT')

    # ---- 3. 初始化策略状态 ----
    # 所有在回调之间需要持久化的数据存于此字典，挂载到ContextInfo上
    state = {
        # === 信号 ===
        'daily_signal':       None,     # 当日做T信号(dict), 由handlebar计算
        'signal_date':        '',       # 信号对应的日期

        # === 持仓 ===
        'base_shares':        0,        # 底仓股数
        'base_cost':          0.0,      # 底仓成本价

        # === 当日做T追踪 ===
        'today_buy_vol':      0,        # 今日累计T买入股数
        'today_sell_vol':     0,        # 今日累计T卖出股数
        'max_t_shares':       0,        # 今日允许最大做T股数
        'stop_loss_triggered': False,   # 今日是否已触发止损
        'force_closed':       False,    # 今日尾盘是否已强制平仓

        # === 委托追踪 ===
        'pending_orders':     {},       # 未成交委托 {order_id: {'side':'buy'|'sell', 'shares':N, 'price':P}}
        'last_order_id':      0,        # 最近一笔委托ID

        # === 风控 ===
        'day_pnl':            0.0,      # 当日做T累计盈亏
        'day_buy_amount':     0.0,      # 当日T买入总金额
        'day_sell_amount':    0.0,      # 当日T卖出总金额
    }
    ContextInfo.st = state

    # ---- 4. 启动定时器（核心！） ----
    # run_time(回调函数名, 间隔, 首次启动时间, 市场)
    # '3nSecond' = 每3秒触发一次ontimer
    # 首次启动时间设为过去，让定时器立即开始
    # [Python API, run_time: 定时器函数，回测时无效]
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")

    # ---- 5. 尝试首次计算信号（handlebar可能还没触发） ----
    try:
        _init_signal(ContextInfo)
    except Exception as e:
        print(f'[init] 首次信号计算失败(可忽略,等待handlebar触发): {e}')

    print(f'\n{"="*60}')
    print(f'  QMT 日内做T策略 v3.0 — 实盘运行')
    print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
    print(f'  定时器: 每3秒检查行情 & 触发交易')
    print(f'  风控: 反T=4重熔断 | 尾盘{FORCE_CLOSE_TIME}强制平仓')
    print(f'{"="*60}\n')


def _init_signal(ContextInfo):
    """在init中提前计算一次做T信号（备用）"""
    st = ContextInfo.st
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')
    if STOCK_QMT in closes and len(closes[STOCK_QMT]) >= 60:
        signal = compute_daily_signal(
            opens[STOCK_QMT], highs[STOCK_QMT],
            lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
        )
        if signal:
            st['daily_signal'] = signal
            _print_signal(signal)


def _print_signal(signal):
    """打印做T信号到日志"""
    print(f'\n--- {STOCK_NAME} 当日做T计划 ---')
    print(f'  开盘={signal["open_price"]:.2f}  昨收={signal["close_yday"]:.2f}')
    print(f'  趋势={signal["trend"]}  RSI={signal["rsi"]:.1f}  ATR={signal["atr"]:.2f}')
    print(f'  正T={"✓" if signal["do_long_t"] else "✗"}  反T={"✓" if signal["do_short_t"] else "✗"}'
          f'{"(熔断)" if signal["short_blocked"] else ""}')
    if signal['buy_levels']:
        print(f'  买单价位({len(signal["buy_levels"])}层): '
              f'{", ".join(f"{l["price"]:.2f}" for l in signal["buy_levels"][:5])}')
    if signal['sell_levels']:
        print(f'  卖单价位({len(signal["sell_levels"])}层): '
              f'{", ".join(f"{l["price"]:.2f}" for l in signal["sell_levels"][:5])}')
    print()


# ============================================================================
# 第四部分：handlebar — 日线触发（每日信号计算）
# ============================================================================

def handlebar(ContextInfo):
    """
    ================================================================
    QMT 主逻辑入口 — 每根日线K线触发一次。

    在日线周期下，每个交易日收盘后触发（K线走完）。
    职责：刷新底仓信息 → 计算下一交易日的做T信号 → 存入state供定时器使用

    注意：handlebar在日线周期下收盘后才触发，信号实际是给下一个交易日用的。
          但实盘中，系统在集合竞价后（09:25+）可能就推了一根新的日线bar，
          这时就可以计算出当日的做T信号了。
    ================================================================
    """
    st = ContextInfo.st

    # ============================================================
    #  Step 1: 获取历史数据
    # ============================================================
    # get_history_data返回 dict: {代码: [值列表]}
    # period='1d' 日线, 取最近 HIST_DATA_LEN 根K线
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')

    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return  # 数据不足

    # ============================================================
    #  Step 2: 获取最新底仓
    # ============================================================
    # get_trade_detail_data(账号, 类型, 数据类别)
    #   'ACCOUNT' = 使用当前默认账号
    #   'STOCK'   = 股票
    #   'POSITION' = 持仓信息
    # 返回 list[Position对象]: m_strInstrumentID(代码), m_nVolume(持仓量),
    #                          m_dOpenPrice(成本价), m_strExchangeID(交易所)
    # [Python API, get_trade_detail_data, p.90]
    positions = get_trade_detail_data('ACCOUNT', 'STOCK', 'POSITION')
    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    if base_shares < MIN_LOT:
        # 无底仓，无法做T
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    # ============================================================
    #  Step 3: 计算做T信号
    # ============================================================
    signal = compute_daily_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    # ---- 计算最大做T股数 ----
    max_t_shares = int(base_shares * MAX_T_RATIO / MIN_LOT) * MIN_LOT
    if max_t_shares < MIN_LOT:
        max_t_shares = 0

    st['daily_signal']   = signal
    st['max_t_shares']   = max_t_shares

    # ============================================================
    #  Step 4: 新交易日重置当日追踪状态
    # ============================================================
    st['today_buy_vol']      = 0
    st['today_sell_vol']     = 0
    st['stop_loss_triggered'] = False
    st['force_closed']       = False
    st['day_pnl']            = 0.0
    st['day_buy_amount']     = 0.0
    st['day_sell_amount']    = 0.0

    _print_signal(signal)


# ============================================================================
# 第五部分：ontimer — 定时器回调（核心执行引擎！）
# ============================================================================

def ontimer(ContextInfo):
    """
    ================================================================
    定时器回调 — 由 run_time() 设定，每3秒触发一次。

    这是策略的实际执行引擎：
      1. 获取当前时间，判断交易时段
      2. 获取实时行情（tick数据）
      3. 对照做T信号中的买卖价位，触发交易
      4. 尾盘强制平仓
      5. 止损检查
    ================================================================
    """
    st = ContextInfo.st
    signal = st.get('daily_signal')

    if signal is None:
        return  # 还没计算出信号（handlebar未触发或数据不足）

    # ============================================================
    #  Step 1: 获取当前时间 & 判断交易时段
    # ============================================================
    now = _get_time_str()
    market_status = _get_market_status(now)

    if market_status == 'closed':
        return  # 非交易时段，不操作

    # ============================================================
    #  Step 2: 尾盘强制平仓（14:55 之后）
    # ============================================================
    if now >= FORCE_CLOSE_TIME and not st['force_closed']:
        _force_close_all(ContextInfo)
        st['force_closed'] = True
        return

    # 已强制平仓，不再开新仓
    if st['force_closed']:
        return

    # 已触发止损，不再开新仓
    if st['stop_loss_triggered']:
        return

    # ============================================================
    #  Step 3: 获取实时行情
    # ============================================================
    # get_full_tick() 返回最新分笔数据
    # 返回 dict: {代码: {lastPrice, askPrice[5档], bidPrice[5档],
    #                     askVol[5档], bidVol[5档], volume, amount, ...}}
    # [Python API, get_full_tick, p.55]
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return  # tick数据暂不可用

    if STOCK_QMT not in tick:
        return

    t = tick[STOCK_QMT]
    last_price  = t.get('lastPrice', 0)
    # bid1_price / ask1_price 可从tick获取，用于更精确的下单:
    #   bid1_price = t.get('bidPrice', [0]*5)[0]   # 买一价（卖出时参考）
    #   ask1_price = t.get('askPrice', [0]*5)[0]   # 卖一价（买入时参考）
    if last_price <= 0:
        return

    # ============================================================
    #  Step 4: 检查止损
    # ============================================================
    if st['day_pnl'] < 0 and abs(st['day_pnl']) > _get_stop_loss_limit(ContextInfo):
        print(f'[止损] 当日做T亏损触发止损: {st["day_pnl"]:.2f}元')
        st['stop_loss_triggered'] = True
        _force_close_all(ContextInfo)
        return

    # ============================================================
    #  Step 5: 检查买卖价位触发
    # ============================================================
    max_t_shares = st['max_t_shares']
    if max_t_shares < MIN_LOT:
        return

    # 剩余的做T额度
    remaining = max_t_shares - max(st['today_buy_vol'], st['today_sell_vol'])
    if remaining < MIN_LOT:
        return

    # ---- 5a. 检查买入价位 ----
    if signal['do_long_t'] or signal['do_short_t']:
        for lv in signal.get('buy_levels', []):
            # 若当前价 ≤ 挂单价，触发买入
            if last_price <= lv['price'] and remaining >= MIN_LOT:
                shares = int(max_t_shares * lv['ratio'] / MIN_LOT) * MIN_LOT
                shares = min(shares, remaining)
                if shares >= MIN_LOT:
                    _place_buy_order(ContextInfo, lv['price'], shares, lv['layer'])

    # ---- 5b. 检查卖出价位 ----
    if signal['do_long_t'] or signal['do_short_t']:
        for lv in signal.get('sell_levels', []):
            if last_price >= lv['price'] and remaining >= MIN_LOT:
                shares = int(max_t_shares * lv['ratio'] / MIN_LOT) * MIN_LOT
                shares = min(shares, remaining)
                if shares >= MIN_LOT:
                    _place_sell_order(ContextInfo, lv['price'], shares, lv['layer'])

    # ---- 5c. 反T紧急买回（卖飞保护） ----
    # 如果做了反T卖出后股价不跌反涨超过1%，立即买回
    if signal['do_short_t'] and st['today_sell_vol'] > st['today_buy_vol']:
        net_short = st['today_sell_vol'] - st['today_buy_vol']
        # 以昨收为基准判断是否涨超阈值
        if net_short > 0 and last_price > signal['close_yday'] * (1 + SHORT_T_BUYBACK_STOP):
            print(f'[反T紧急买回] 股价涨超{SHORT_T_BUYBACK_STOP*100:.1f}%, 买回{net_short}股')
            _place_buy_order(ContextInfo, last_price, net_short, '反T紧急买回')


# ============================================================================
# 第六部分：下单 & 强制平仓
# ============================================================================

def _place_buy_order(ContextInfo, price, shares, label):
    """
    下买入委托单。

    使用 passorder() 函数：
      opType=23     : 股票买入
      orderType=1101: 按股/手方式下单
      prType=11     : 指定价（限价单）
      quickTrade=1  : 立即触发下单（不等待K线走完）
                     [Python API: quickTrade=1在is_last_bar()=True时立即发单]

    同时也支持 order_shares() 简化下单：
      order_shares(代码, 股数, 'FIX', 价格, ContextInfo, 账号)
    """
    st = ContextInfo.st
    account_id = ContextInfo.accID if hasattr(ContextInfo, 'accID') else 'ACCOUNT'

    try:
        # 方式1: 使用 passorder（更底层、更灵活）
        # passorder(23, 1101, account_id, STOCK_QMT, 11, price, shares,
        #           STOCK_NAME, 1, f'T0_buy_{label}', ContextInfo)
        # [Python API, passorder, p.80-85]

        # 方式2: 使用 order_shares（更简洁）
        # shares为正=买入, 为负=卖出
        # style='FIX'=指定价, 后面的price参数是限价
        order_shares(STOCK_QMT, shares, 'FIX', price, ContextInfo, account_id)
        # [Python API, order_shares, p.102]

        st['today_buy_vol'] += shares
        print(f'[T买入] {label}: {price:.2f}元 × {shares}股')
    except Exception as e:
        print(f'[T买入失败] {label}: {e}')


def _place_sell_order(ContextInfo, price, shares, label):
    """
    下卖出委托单。

    order_shares 中 shares 为负数表示卖出。
    """
    st = ContextInfo.st
    account_id = ContextInfo.accID if hasattr(ContextInfo, 'accID') else 'ACCOUNT'

    try:
        # shares为负 = 卖出
        order_shares(STOCK_QMT, -shares, 'FIX', price, ContextInfo, account_id)

        st['today_sell_vol'] += shares
        print(f'[T卖出] {label}: {price:.2f}元 × {shares}股')
    except Exception as e:
        print(f'[T卖出失败] {label}: {e}')


def _force_close_all(ContextInfo):
    """
    尾盘强制平仓 — 把当日所有未配对T仓位平掉。

    平仓逻辑：
      - 如果当日T买入 > T卖出 → 净多头 → 卖出平仓
      - 如果当日T卖出 > T买入 → 净空头 → 买入回补
    """
    st = ContextInfo.st
    net = st['today_buy_vol'] - st['today_sell_vol']
    account_id = ContextInfo.accID if hasattr(ContextInfo, 'accID') else 'ACCOUNT'

    if net > 0:
        # 净多头 → 卖出平仓
        try:
            order_shares(STOCK_QMT, -net, 'LATEST', ContextInfo, account_id)
            st['today_sell_vol'] += net
            print(f'[尾盘平仓] 卖出平仓 {net}股 (最新价)')
        except Exception as e:
            print(f'[尾盘平仓失败] 卖出: {e}')
    elif net < 0:
        # 净空头 → 买入回补
        buyback = abs(net)
        try:
            order_shares(STOCK_QMT, buyback, 'LATEST', ContextInfo, account_id)
            st['today_buy_vol'] += buyback
            print(f'[尾盘平仓] 买入回补 {buyback}股 (最新价)')
        except Exception as e:
            print(f'[尾盘平仓失败] 买入: {e}')
    else:
        print(f'[尾盘] 无需平仓(已配对)')


def _get_stop_loss_limit(ContextInfo):
    """计算当日做T止损金额"""
    st = ContextInfo.st
    signal = st.get('daily_signal')
    if signal is None:
        return 999999
    # 止损金额 = 底仓市值 × 做T比例 × 止损百分比
    return st['base_shares'] * signal['open_price'] * MAX_T_RATIO * STOP_LOSS_PCT


# ============================================================================
# 第七部分：委托/成交回调 & 工具函数
# ============================================================================

def order_callback(ContextInfo, order):
    """
    委托状态回调 — 当委托状态发生变化时由QMT自动推送到此函数。

    order对象常用属性:
      m_strInstrumentID : 股票代码
      m_nOrderNum       : 委托编号
      m_nOrderStatus    : 委托状态 (50=已报, 52=部成, 53=全成, 54=部撤, 55=已撤, 56=废单)
      m_dOrderPrice     : 委托价格
      m_nVolumeTotal    : 委托总量
      m_nVolumeTraded   : 已成交数量
      m_strOrderRemark  : 备注(userOrderId)

    参考: [Python API, order_callback]
    """
    status_map = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    status = order.m_nOrderStatus
    status_name = status_map.get(status, f'未知({status})')

    # 记录委托状态变化
    if status in (53, 54, 55, 56):  # 终态
        print(f'[委托] {order.m_strInstrumentID} '
              f'¥{order.m_dOrderPrice:.2f} '
              f'{order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 '
              f'→ {status_name}')


def deal_callback(ContextInfo, deal):
    """
    成交回报回调 — 当委托成交时由QMT自动推送。

    deal对象常用属性:
      m_strInstrumentID : 股票代码
      m_nRef            : 成交编号
      m_nOrderNum       : 对应的委托编号
      m_dPrice          : 成交价格
      m_nVolume         : 成交数量
      m_nDirection      : 买卖方向 (0=未知, 1=买, 2=卖)
      m_strRemark       : 备注(userOrderId)
      m_fCommission     : 佣金
      m_fStampTax       : 印花税

    参考: [Python API, deal_callback]
    """
    st = ContextInfo.st
    direction = '买入' if deal.m_nDirection == 1 else '卖出'
    amount = deal.m_dPrice * deal.m_nVolume
    cost = deal.m_fCommission + deal.m_fStampTax  # 交易费用

    # 更新当日做T盈亏
    if deal.m_nDirection == 1:  # 买入
        st['day_buy_amount']  += amount
        st['day_pnl']         -= (amount + cost)
    else:                       # 卖出
        st['day_sell_amount'] += amount
        st['day_pnl']         += (amount - cost)

    print(f'[成交] {direction} {deal.m_strInstrumentID} '
          f'¥{deal.m_dPrice:.2f} × {deal.m_nVolume}股 '
          f'金额={amount:.0f}  当日PnL≈{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    """
    策略停止回调 — 策略被停止/暂停时触发。
    用于清理资源、打印最终状态。可选。
    """
    st = getattr(ContextInfo, 'st', None)
    if st:
        print(f'\n[{STOCK_NAME}] 策略已停止')
        print(f'  当日T买入: {st.get("today_buy_vol", 0)}股')
        print(f'  当日T卖出: {st.get("today_sell_vol", 0)}股')
        net = st.get('today_buy_vol', 0) - st.get('today_sell_vol', 0)
        if net != 0:
            print(f'  ⚠ 有未平T仓位: {net}股 (净{"多头" if net > 0 else "空头"})')


def _get_time_str():
    """获取当前时间字符串 HH:MM:SS"""
    import time as _time
    return _time.strftime('%H:%M:%S')


def _get_market_status(now):
    """
    判断当前是否在交易时段。

    返回: 'morning' | 'afternoon' | 'closed'

    简化版 — 不考虑节假日/临时休市，由QMT柜台自动处理。
    实际发单时如果不在交易时段，柜台会拒绝委托。
    """
    if '09:30:00' <= now <= '11:30:00':
        return 'morning'
    elif '13:00:00' <= now <= '15:00:00':
        return 'afternoon'
    else:
        return 'closed'
