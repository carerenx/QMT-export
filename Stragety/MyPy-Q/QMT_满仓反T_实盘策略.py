# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 日内反T策略 — 满仓专用版 v1.0
================================================================================
 注意：以下函数由QMT运行时注入，IDE报红可忽略：
   - get_trade_detail_data()  获取交易明细（持仓/账号/委托）
   - order_shares()           指定股数下单
   - passorder()              综合交易下单
   - order_callback()         委托状态回调
   - deal_callback()          成交回报回调
   - ContextInfo.get_history_data()    获取历史K线
   - ContextInfo.get_full_tick()       获取实时分笔
   - ContextInfo.run_time()            设置定时器
   - ContextInfo.set_universe()        设置股票池
   - ContextInfo.set_account()         设置交易账号
 ================================================================================

 【满仓做T原理 — 只做反T】
  你满仓持有某只股票，账户几乎没有可用现金，无法做"先买后卖"的正T。
  但可以利用手中底仓做"先卖后买"的反T：

     早盘冲高 → 卖出N股（用底仓）→ 拿到现金 → 盘中回落 → 买回N股
     结果：底仓股数不变，现金多了（赚了日内价差）

  A股规则保障：
   - 卖出后资金T+0到账，当天可用（可以买回）
   - 买入后股票T+0到账，可以当天再卖出（用底仓额度）
   - 所以反T在同一天内完全可以完成"卖→买回"的闭环

 【与标准版（正T+反T）的区别】
  ┌──────────────┬─────────────────────┬─────────────────────┐
  │              │ 标准版              │ 满仓版（本文件）    │
  ├──────────────┼─────────────────────┼─────────────────────┤
  │ 正T(先买后卖)│ ✓                   │ ✗（无现金）        │
  │ 反T(先卖后买)│ ✓（牛市禁）         │ ✓（唯一方向）      │
  │ 可用现金检查 │ 不做                │ ✓ 每笔卖出后确认    │
  │ 熔断机制     │ 4重熔断禁反T        │ 仅熔断1(牛市禁反T)  │
  │ 底仓保护     │ MAX_T_RATIO=50%     │ MAX_T_RATIO=30%     │
  │ 紧急买回     │ 涨超1%买回          │ 涨超0.8%买回(更紧)  │
  │ 尾盘兜底     │ 14:55强制平仓       │ 14:50强制平仓(更早) │
  └──────────────┴─────────────────────┴─────────────────────┘

 【满仓反T的额外风控】
  1. 可用现金实时检测 — 卖出后才能拿到现金，买回需要现金
  2. 底仓保护 — 单日最多卖底仓的30%（绝不丢底仓）
  3. 紧急买回线更紧 — 涨超0.8%立即买回（标准版1%）
  4. 尾盘更早兜底 — 14:50开始强制买回（标准版14:55）
  5. 熔断更保守 — 牛市不做反T（这是铁律）

 【反T四步流程】
  交易日:
    09:25  集合竞价结束 → handlebar计算出反T信号
    09:30-14:50  ontimer每3秒检查: 价格触及卖出价→卖→触及买回价→买
    14:50  尾盘时间到 → 强制买回所有未配对的反T仓位
    15:00  收盘 → 底仓完整，日内反T收益落袋

 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置参数
# ============================================================================

# ---------- 标的设置 ----------
STOCK_CODE   = '601869'                # 股票代码
STOCK_NAME   = '长飞光纤'              # 股票名称
STOCK_QMT    = f'{STOCK_CODE}.SH'      # QMT完整代码 (.SH=上交所)

# ---------- 反T仓位控制 ----------
# ★ 满仓核心参数：单日最多卖出底仓的30%来做反T
#   卖多了可能买不回来 → 丢底仓 → 这是满仓反T的最大风险
MAX_T_RATIO  = 0.30                    # 单日最多用底仓30%做反T
MIN_LOT      = 100                     # 1手=100股

# ---------- 反T卖出/买回价位参数 ----------
ATR_PERIOD   = 14                      # ATR计算周期
# 卖出价 = 开盘价 + ATR × SELL_MULT（越高越难触发，但每次收益越大）
SELL_ATR_MULT = 0.8                    # 卖出ATR倍数
# 买回价 = 卖出成交价 × (1 - ATR% × BUYBACK_MULT)
BUYBACK_ATR_MULT = 0.6                 # 买回ATR倍数

# 阶梯反T：3层卖出 + 3批买回
LADDER_LEVELS = [
    {'mult': 0.40, 'ratio': 0.25},     # 第1层: +0.4ATR 卖25%仓位
    {'mult': 0.80, 'ratio': 0.40},     # 第2层: +0.8ATR 卖40%仓位
    {'mult': 1.20, 'ratio': 0.35},     # 第3层: +1.2ATR 卖35%仓位
]

BUYBACK_LEVELS = [
    {'atr_mult': 0.4, 'ratio': 0.40},  # 第1批: 跌0.4ATR买回40%
    {'atr_mult': 0.8, 'ratio': 0.35},  # 第2批: 跌0.8ATR买回35%
    {'atr_mult': 1.2, 'ratio': 0.25},  # 第3批: 跌1.2ATR买回25%
]

# ---------- 反T熔断参数 ----------
# ★ 满仓情况下，熔断比标准版更保守，因为丢了底仓是永久损失
# 熔断1: 牛市趋势不做反T（铁律）
# 注意：满仓版去掉了"高开禁反T"和"连涨禁反T"，
#       因为这恰恰是反T的最佳时机——高开高走先卖，等回落再买
#       保留牛市禁反T是因为牛市趋势中卖飞风险极高
STOP_LOSS_PCT = 0.02                   # 单日反T亏损上限(2%, 比标准版3%更紧)

# ---------- 紧急买回（防卖飞底仓） ----------
# ★ 这是满仓反T最重要的防线
# 反T卖出后股价不跌反涨，说明判断错了 → 必须立即买回
EMERGENCY_BUYBACK_PCT = 0.008          # 卖价之上再涨0.8%即买回(标准版1%)
# 二级紧急买回：如果继续涨到1.5%
EMERGENCY_BUYBACK_PCT2 = 0.015         # 卖价之上再涨1.5%全量买回

# ---------- 成交量过滤 ----------
VOLUME_FILTER_RATIO = 0.5              # 量比<0.5不操作(缩量不做T)

# ---------- RSI极值 ----------
RSI_OVERBOUGHT = 75                    # RSI>75不做反T(超买可能继续涨)

# ---------- 定时器 & 时间 ----------
TIMER_INTERVAL     = '3nSecond'        # 3秒检查一次
FORCE_CLOSE_TIME   = '14:50:00'        # 强制买回时间(比标准版早5分钟)

# ---------- 历史数据 ----------
HIST_DATA_LEN = 80

# ---------- 交易成本 ----------
COMMISSION  = 0.00025                  # 佣金万分之2.5
STAMP_TAX   = 0.001                    # 印花税千分之一(卖出收)


# ============================================================================
# 第二部分：技术指标计算（纯Python，不依赖第三方库）
# ============================================================================

def _calc_ma(values, period):
    """简单移动平均"""
    n = len(values)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _calc_atr(highs, lows, closes, period=14):
    """
    ATR（平均真实波幅）
    True Range = max(当日振幅, |最高-昨收|, |最低-昨收|)
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
    """RSI（相对强弱指标）"""
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


def compute_short_t_signal(opens, highs, lows, closes, volumes):
    """
    满仓反T信号计算引擎。

    只计算"先卖后买"的反T信号，不做正T。
    返回做空（卖出）价位和买回价位。

    参数:
        opens/highs/lows/closes/volumes: list[float] 日线序列

    返回:
        dict or None — 反T信号:
          - do_short_t          : 是否允许反T
          - trend               : 'bull'|'bear'|'sideways'
          - sell_levels         : 卖出挂单价位 [{'price':P, 'ratio':R, 'layer':L}, ...]
          - buyback_levels      : 买回挂单价位 [{'price':P, 'ratio':R, 'layer':L}, ...]
          - open_price          : 当日开盘价
          - atr / atr_pct / rsi : 技术指标
    """
    n = len(closes)
    if n < 60:
        return None

    curr_open   = opens[-1]
    curr_close  = closes[-1]
    curr_volume = volumes[-1]

    # ---- ATR ----
    atr_arr = _calc_atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1]
    if curr_atr <= 0:
        curr_atr = curr_close * 0.03
    curr_atr_pct = curr_atr / curr_close if curr_close > 0 else 0.03

    # ---- 均线 & 趋势 ----
    ma5  = _calc_ma(closes, 5)[-1]
    ma20 = _calc_ma(closes, 20)[-1]
    trend_bull = (curr_close > ma20) and (ma5 > ma20)
    trend_bear = (curr_close < ma20) and (ma5 < ma20)
    trend = 'bull' if trend_bull else ('bear' if trend_bear else 'sideways')

    # ---- RSI ----
    curr_rsi = _calc_rsi(closes)[-1]

    # ---- 量比 ----
    ma20_vol = _calc_ma(volumes, 20)
    curr_vol_ratio = curr_volume / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # ================================================================
    #  反T方向决策（满仓版 — 简化熔断）
    # ================================================================
    do_short_t = True

    # 熔断1: 牛市不做反T（铁律 — 牛市中卖飞底仓损失远超做T收益）
    if trend == 'bull':
        do_short_t = False

    # 缩量不操作
    if curr_vol_ratio < VOLUME_FILTER_RATIO:
        do_short_t = False

    # RSI超买不操作（可能继续拉升）
    if curr_rsi > RSI_OVERBOUGHT:
        do_short_t = False

    # ================================================================
    #  买卖价位计算（只做反T）
    # ================================================================
    sell_levels    = []   # 卖出价位
    buyback_levels = []   # 买回价位

    if do_short_t:
        # 反T卖出价 = 开盘价 + ATR × 阶梯倍数
        for lv in LADDER_LEVELS:
            sell_price = curr_open + curr_atr * lv['mult']
            sell_levels.append({
                'price': round(sell_price, 2),
                'ratio': lv['ratio'],
                'filled': False,       # 该层是否已成交卖出
                'fill_price': 0.0,     # 实际卖出成交价
                'layer': f'反T卖{lv["mult"]}ATR',
            })

        # 反T买回价 = 卖出成交价 × (1 - ATR% × 买回倍数)
        # 注意：买回价依赖卖出价，在原版策略中用卖出价计算
        # 但在实盘中是"卖出后才计算"，所以这里用开盘价做参考
        for lv in LADDER_LEVELS:
            sell_ref = curr_open + curr_atr * lv['mult']  # 该层参考卖出价
            for bb in BUYBACK_LEVELS:
                buyback_price = sell_ref * (1.0 - curr_atr_pct * bb['atr_mult'])
                buyback_levels.append({
                    'price': round(buyback_price, 2),
                    'ratio': lv['ratio'] * bb['ratio'],
                    'filled': False,
                    'layer': f'反T买回-{bb["atr_mult"]}ATR',
                })

    return {
        'do_short_t':     do_short_t,
        'trend':          trend,
        'sell_levels':    sell_levels,
        'buyback_levels': buyback_levels,
        'open_price':     curr_open,
        'close_yday':     curr_close,
        'atr':            curr_atr,
        'atr_pct':        curr_atr_pct,
        'rsi':            curr_rsi,
        'vol_ratio':      curr_vol_ratio,
        'ma5':            ma5,
        'ma20':           ma20,
    }


# ============================================================================
# 第三部分：QMT 策略入口
# ============================================================================

def init(ContextInfo):
    """
    QMT策略初始化。

    1. 设置股票池 & 交易账号
    2. 检查账户状态（确认满仓）
    3. 启动3秒定时器（核心执行引擎）
    4. 初始化状态字典
    """
    # ---- 1. 设置股票池和账号 ----
    # [Python API] ContextInfo.set_universe: 设置订阅的股票池
    ContextInfo.set_universe([STOCK_QMT])
    # [Python API] ContextInfo.set_account: 设置交易账号
    ContextInfo.set_account('ACCOUNT')

    # ---- 2. 初始化策略状态 ----
    state = {
        # === 信号 ===
        'daily_signal':     None,      # 当日反T信号(dict)

        # === 持仓 ===
        'base_shares':      0,         # 底仓股数
        'base_cost':        0.0,       # 底仓成本

        # === 反T追踪 ===
        'short_sold_vol':   0,         # 当日累计反T卖出股数
        'short_bought_vol': 0,         # 当日累计反T买回股数
        'short_sold_amount': 0.0,      # 当日反T卖出总金额
        'short_bought_amount': 0.0,    # 当日反T买回总金额
        'day_pnl':          0.0,       # 当日反T盈亏
        'max_t_shares':     0,         # 当日允许最大反T股数

        # === 风控 ===
        'stop_loss_hit':    False,     # 止损触发
        'force_closed':     False,     # 尾盘强制买回已完成
        'emergency_buyback': False,    # 紧急买回触发

        # === 每层卖出成交价（用于计算动态买回价） ===
        'layer_fill_prices': {},       # {layer_index: fill_price}
    }
    ContextInfo.st = state

    # ---- 3. 启动定时器 ----
    # [Python API] run_time(funcName, period, startTime, market)
    # period='3nSecond'=每3秒触发ontimer; startTime=过去的时间=立即启动
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")

    # ---- 4. 尝试首次信号计算 ----
    try:
        _init_signal(ContextInfo)
    except Exception as e:
        print(f'[init] 首次信号计算失败(可忽略): {e}')

    print(f'\n{"="*60}')
    print(f'  QMT 满仓反T策略 v1.0')
    print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
    print(f'  模式: 仅反T(先卖后买) — 满仓专用')
    print(f'  单日最大卖出: 底仓×{MAX_T_RATIO*100:.0f}%')
    print(f'  紧急买回: 涨超{EMERGENCY_BUYBACK_PCT*100:.1f}% / {EMERGENCY_BUYBACK_PCT2*100:.1f}%')
    print(f'  尾盘强制买回: {FORCE_CLOSE_TIME}')
    print(f'  定时器: 每3秒')
    print(f'{"="*60}\n')


def _init_signal(ContextInfo):
    """init中预计算一次信号"""
    st = ContextInfo.st
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')
    if STOCK_QMT in closes and len(closes[STOCK_QMT]) >= 60:
        signal = compute_short_t_signal(
            opens[STOCK_QMT], highs[STOCK_QMT],
            lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
        )
        if signal:
            st['daily_signal'] = signal
            _print_signal(signal)


def _print_signal(signal):
    """打印反T信号到日志"""
    print(f'\n--- {STOCK_NAME} 满仓反T信号 ---')
    print(f'  开盘={signal["open_price"]:.2f}  昨收={signal["close_yday"]:.2f}')
    print(f'  趋势={signal["trend"]}  RSI={signal["rsi"]:.1f}  '
          f'ATR={signal["atr"]:.2f}({signal["atr_pct"]*100:.2f}%)')
    status = '✓ 允许反T' if signal['do_short_t'] else '✗ 禁止反T'
    if not signal['do_short_t'] and signal['trend'] == 'bull':
        status += ' (熔断1: 牛市)'
    print(f'  状态: {status}')
    if signal['sell_levels']:
        print(f'  卖出价: {", ".join(f"{l["price"]:.2f}" for l in signal["sell_levels"])}')
    if signal['buyback_levels']:
        print(f'  买回价: {", ".join(f"{l["price"]:.2f}" for l in signal["buyback_levels"][:5])}')
    print()


# ============================================================================
# 第四部分：handlebar — 日线触发
# ============================================================================

def handlebar(ContextInfo):
    """
    日线触发 — 每个交易日计算反T信号，重置当日追踪状态。
    """
    st = ContextInfo.st

    # ---- 1. 获取历史数据 ----
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')

    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return

    # ---- 2. 获取底仓 ----
    # [Python API] get_trade_detail_data(账号, 'STOCK', 'POSITION')
    positions = get_trade_detail_data('ACCOUNT', 'STOCK', 'POSITION')
    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    if base_shares < MIN_LOT:
        print(f'[警告] 无底仓({STOCK_NAME}), 反T策略无法运行')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    # ---- 3. 确认满仓状态 ----
    # [Python API] get_trade_detail_data(账号, 'STOCK', 'ACCOUNT')
    # Account对象: m_dAvailable(可用资金), m_dBalance(总资产)
    acct_list = get_trade_detail_data('ACCOUNT', 'STOCK', 'ACCOUNT')
    if acct_list:
        avail_cash = acct_list[0].m_dAvailable
        pos_value  = base_shares * closes[STOCK_QMT][-1]
        total_val  = avail_cash + pos_value
        cash_pct   = avail_cash / total_val * 100 if total_val > 0 else 0
        print(f'[账户] 可用现金: {avail_cash:,.0f}元 ({cash_pct:.1f}%) | '
              f'持仓市值: {pos_value:,.0f}元 | 底仓: {base_shares}股')
        if cash_pct < 5:
            print(f'[确认] 满仓状态 ✓ — 只做反T(先卖后买)')

    # ---- 4. 计算反T信号 ----
    signal = compute_short_t_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    # ---- 5. 计算最大反T股数 ----
    max_t_shares = int(base_shares * MAX_T_RATIO / MIN_LOT) * MIN_LOT
    if max_t_shares < MIN_LOT:
        max_t_shares = 0

    st['daily_signal'] = signal
    st['max_t_shares'] = max_t_shares

    # ---- 6. 重置当日状态 ----
    st['short_sold_vol']    = 0
    st['short_bought_vol']  = 0
    st['short_sold_amount'] = 0.0
    st['short_bought_amount'] = 0.0
    st['day_pnl']           = 0.0
    st['stop_loss_hit']     = False
    st['force_closed']      = False
    st['emergency_buyback'] = False
    st['layer_fill_prices'] = {}

    _print_signal(signal)


# ============================================================================
# 第五部分：ontimer — 定时器（核心执行引擎）
# ============================================================================

def ontimer(ContextInfo):
    """
    ★ 核心引擎 ★ — 每3秒执行一次。

    满仓反T执行流程:
      1. 判断交易时段
      2. 获取实时价格
      3. 检查卖出价位 → 触发卖出
      4. 检查买回价位 → 触发买回
      5. 检查紧急买回条件（防卖飞底仓！）
      6. 止损检查
      7. 尾盘14:50强制买回所有未配对仓位
    """
    st = ContextInfo.st
    signal = st.get('daily_signal')

    if signal is None or not signal.get('do_short_t'):
        return  # 无信号或反T被禁止

    # ---- 1. 时间检查 ----
    now = _get_time_str()
    market_status = _get_market_status(now)
    if market_status == 'closed':
        return

    # ---- 2. 尾盘强制买回（14:50之后） ----
    if now >= FORCE_CLOSE_TIME and not st['force_closed']:
        _force_buyback_all(ContextInfo)
        st['force_closed'] = True
        return

    if st['force_closed']:
        return  # 已强制平仓，不再操作

    # ---- 3. 止损阻止开新仓 ----
    if st['stop_loss_hit']:
        return

    # ---- 4. 获取实时行情 ----
    # [Python API] ContextInfo.get_full_tick() 返回最新分笔
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return
    if STOCK_QMT not in tick:
        return

    t = tick[STOCK_QMT]
    last_price = t.get('lastPrice', 0)
    if last_price <= 0:
        return

    max_t_shares = st['max_t_shares']
    if max_t_shares < MIN_LOT:
        return

    # ---- 5. 剩余可卖额度 ----
    can_sell = max_t_shares - st['short_sold_vol']
    can_buy  = st['short_sold_vol'] - st['short_bought_vol']  # 已卖未买回的量

    # ---- 6. 检查卖出触发 ----
    if can_sell >= MIN_LOT:
        for i, lv in enumerate(signal.get('sell_levels', [])):
            if lv.get('filled'):
                continue  # 该层已卖出
            if last_price >= lv['price']:
                shares = int(max_t_shares * lv['ratio'] / MIN_LOT) * MIN_LOT
                shares = min(shares, can_sell)
                if shares >= MIN_LOT:
                    _short_sell(ContextInfo, lv['price'], shares, lv['layer'])
                    lv['filled'] = True
                    lv['fill_price'] = last_price
                    st['layer_fill_prices'][i] = last_price

    # ---- 7. 检查买回触发 ----
    if can_buy >= MIN_LOT:
        for lv in signal.get('buyback_levels', []):
            if lv.get('filled'):
                continue
            if last_price <= lv['price']:
                shares = int(max_t_shares * lv['ratio'] / MIN_LOT) * MIN_LOT
                shares = min(shares, can_buy)
                if shares >= MIN_LOT:
                    _short_buyback(ContextInfo, lv['price'], shares, lv['layer'])
                    lv['filled'] = True

    # ---- 8. 紧急买回检查（防卖飞底仓！） ----
    # ★ 这是满仓反T最关键的防线 ★
    # 逻辑：反T卖出后，如果股价继续上涨（而不是预期的回落），
    #       说明判断错误，必须立即买回，防止永久丢失底仓
    if can_buy >= MIN_LOT and not st['emergency_buyback']:
        # 拿最近一笔卖出成交价来计算
        fill_prices = st['layer_fill_prices']
        if fill_prices:
            avg_sell_price = sum(fill_prices.values()) / len(fill_prices)
            rise_pct = (last_price - avg_sell_price) / avg_sell_price

            # 二级紧急买回(先检查更严重的情况)：卖出后涨超1.5%
            if rise_pct >= EMERGENCY_BUYBACK_PCT2:
                print(f'[紧急买回L2] 🔴 卖出均价{avg_sell_price:.2f} → 现价{last_price:.2f}'
                      f'(+{rise_pct*100:.2f}%) 全量买回{can_buy}股!!')
                _short_buyback(ContextInfo, last_price, can_buy, '紧急买回L2')
                st['emergency_buyback'] = True

            # 一级紧急买回：卖出后涨超0.8%
            elif rise_pct >= EMERGENCY_BUYBACK_PCT:
                print(f'[紧急买回L1] 🟡 卖出均价{avg_sell_price:.2f} → 现价{last_price:.2f}'
                      f'(+{rise_pct*100:.2f}%) 立即买回{can_buy}股!')
                _short_buyback(ContextInfo, last_price, can_buy, '紧急买回L1')
                st['emergency_buyback'] = True

    # ---- 9. 止损检查 ----
    if st['day_pnl'] < 0 and abs(st['day_pnl']) > _calc_stop_loss(ContextInfo):
        print(f'[反T止损] 当日亏损{st["day_pnl"]:.0f}元触发止损')
        st['stop_loss_hit'] = True
        _force_buyback_all(ContextInfo)


# ============================================================================
# 第六部分：反T下单 & 平仓
# ============================================================================

def _short_sell(ContextInfo, price, shares, label):
    """
    反T卖出：卖出底仓股票。

    使用 order_shares() — QMT内置的指定股数下单函数。
    shares为负数表示卖出。

    [Python API] order_shares(stockcode, shares, style, price, ContextInfo, accId)
      - stockcode: 如 '601869.SH'
      - shares: 正=买, 负=卖
      - style: 'FIX'=指定价, 'LATEST'=最新价, 'COMPETE'=对手价
      - price: 限价（style='FIX'时使用）
    """
    st = ContextInfo.st
    account_id = _get_account_id(ContextInfo)

    try:
        # 卖出: shares为负
        order_shares(STOCK_QMT, -shares, 'FIX', price, ContextInfo, account_id)

        st['short_sold_vol']    += shares
        st['short_sold_amount'] += price * shares
        print(f'[反T卖出] {label}: {price:.2f}元 × {shares}股 '
              f'(累计卖出{st["short_sold_vol"]}股)')
    except Exception as e:
        print(f'[反T卖出失败] {label}: {e}')


def _short_buyback(ContextInfo, price, shares, label):
    """
    反T买回：买回同等数量的股票，恢复底仓。

    shares为正数表示买入。
    """
    st = ContextInfo.st
    account_id = _get_account_id(ContextInfo)

    # 检查可用现金是否足够
    avail_cash = _get_available_cash(ContextInfo)
    need_cash  = price * shares * 1.001  # 留一点余量

    if avail_cash < need_cash:
        # 现金不够，用可用资金全量买回
        actual_shares = int(avail_cash / (price * 1.001) / MIN_LOT) * MIN_LOT
        if actual_shares < MIN_LOT:
            print(f'[反T买回失败] 现金不足: 需{need_cash:.0f} 可用{avail_cash:.0f}')
            return
        shares = min(shares, actual_shares)
        print(f'[反T买回] 现金受限: 原计划{shares}股 → 实际{actual_shares}股')

    try:
        order_shares(STOCK_QMT, shares, 'FIX', price, ContextInfo, account_id)

        st['short_bought_vol']    += shares
        st['short_bought_amount'] += price * shares
        print(f'[反T买回] {label}: {price:.2f}元 × {shares}股 '
              f'(累计买回{st["short_bought_vol"]}股)')
    except Exception as e:
        print(f'[反T买回失败] {label}: {e}')


def _force_buyback_all(ContextInfo):
    """
    ★ 尾盘强制买回 ★ — 14:50之后调用。

    无论盈亏，把当日所有未买回的仓位全部买回来，恢复底仓。
    这是满仓反T的生命线：底仓不能丢。

    如果已卖出但未买回，用最新价/对手价强制买回。
    """
    st = ContextInfo.st
    net = st['short_sold_vol'] - st['short_bought_vol']
    account_id = _get_account_id(ContextInfo)

    if net <= 0:
        print(f'[尾盘] 反T仓位已全部买回 ✓')
        return

    avail_cash = _get_available_cash(ContextInfo)
    # 用最新价估算
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
        close_price = tick[STOCK_QMT].get('lastPrice', 0)
    except Exception:
        close_price = 0

    if close_price <= 0:
        close_price = (st['short_sold_amount'] / st['short_sold_vol']
                       if st['short_sold_vol'] > 0 else 10.0)

    # 计算能买回多少（受限于可用现金）
    actual_shares = min(net, int(avail_cash / (close_price * 1.001) / MIN_LOT) * MIN_LOT)
    if actual_shares < MIN_LOT:
        print(f'[尾盘失败] 现金不足以买回: 需买{net}股 可用{avail_cash:.0f}元 现价{close_price:.2f}')
        return

    try:
        # 用对手价快速买回
        order_shares(STOCK_QMT, actual_shares, 'COMPETE', ContextInfo, account_id)
        st['short_bought_vol'] += actual_shares
        print(f'[尾盘强制买回] {actual_shares}股(对手价) '
              f'剩余未买回: {net - actual_shares}股')
    except Exception as e:
        print(f'[尾盘强制买回失败] {e}')


def _calc_stop_loss(ContextInfo):
    """计算反T止损金额 = 底仓市值 × 做T比例 × 止损%"""
    st = ContextInfo.st
    signal = st.get('daily_signal')
    if signal is None:
        return 999999
    return st['base_shares'] * signal['open_price'] * MAX_T_RATIO * STOP_LOSS_PCT


def _get_available_cash(ContextInfo):
    """获取当前可用资金"""
    try:
        acct = get_trade_detail_data('ACCOUNT', 'STOCK', 'ACCOUNT')
        if acct:
            return acct[0].m_dAvailable
    except Exception:
        pass
    return 0.0


def _get_account_id(ContextInfo):
    """获取当前交易账号ID"""
    if hasattr(ContextInfo, 'accID') and ContextInfo.accID:
        return ContextInfo.accID
    return 'ACCOUNT'


# ============================================================================
# 第七部分：委托/成交回调 & 工具函数
# ============================================================================

def order_callback(ContextInfo, order):
    """
    委托状态回调 — QMT自动推送。

    [Python API] order对象常用属性:
      m_nOrderStatus: 50=已报, 52=部成, 53=全成, 54=部撤, 55=已撤, 56=废单
      m_dOrderPrice:  委托价格
      m_nVolumeTotal:  委托数量
      m_nVolumeTraded: 已成交数量
    """
    status_map = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    status = order.m_nOrderStatus
    if status in (53, 54, 55, 56):  # 终态
        status_name = status_map.get(status, f'未知({status})')
        print(f'[委托] {order.m_strInstrumentID} '
              f'¥{order.m_dOrderPrice:.2f} '
              f'{order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {status_name}')


def deal_callback(ContextInfo, deal):
    """
    成交回报回调 — QMT自动推送。

    [Python API] deal对象常用属性:
      m_dPrice:   成交价格
      m_nVolume:  成交数量
      m_nDirection: 1=买入, 2=卖出
      m_fCommission: 佣金
      m_fStampTax:   印花税
    """
    st = ContextInfo.st
    direction = '买入' if deal.m_nDirection == 1 else '卖出'
    amount = deal.m_dPrice * deal.m_nVolume
    total_cost = deal.m_fCommission + deal.m_fStampTax

    # 更新当日盈亏
    if deal.m_nDirection == 1:  # 买入(买回) → 支出
        st['day_pnl'] -= (amount + total_cost)
    else:                        # 卖出 → 收入
        st['day_pnl'] += (amount - total_cost)

    net = st['short_sold_vol'] - st['short_bought_vol']
    print(f'[成交] {direction} {deal.m_strInstrumentID} '
          f'¥{deal.m_dPrice:.2f} × {deal.m_nVolume}股 '
          f'当日PnL≈{st["day_pnl"]:.0f} | 净卖出{net}股(待买回)')


def stop(ContextInfo):
    """
    策略停止回调 — 检查是否有未平仓位。
    """
    st = getattr(ContextInfo, 'st', None)
    if st:
        net = st.get('short_sold_vol', 0) - st.get('short_bought_vol', 0)
        print(f'\n[{STOCK_NAME}] 满仓反T策略已停止')
        print(f'  反T卖出: {st.get("short_sold_vol", 0)}股')
        print(f'  反T买回: {st.get("short_bought_vol", 0)}股')
        if net > 0:
            print(f'  ⚠⚠ 警告: 有{net}股净卖出未买回！请手动补仓恢复底仓！')
        else:
            print(f'  底仓完整 ✓')
        print(f'  当日PnL: {st.get("day_pnl", 0):.0f}元')


def _get_time_str():
    """当前时间字符串 HH:MM:SS"""
    import time as _time
    return _time.strftime('%H:%M:%S')


def _get_market_status(now):
    """交易时段判断"""
    if '09:30:00' <= now <= '11:30:00':
        return 'morning'
    elif '13:00:00' <= now <= '15:00:00':
        return 'afternoon'
    else:
        return 'closed'
