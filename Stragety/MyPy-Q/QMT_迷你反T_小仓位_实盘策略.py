# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 迷你反T策略 — 小仓位专用版 v1.0
================================================================================
 注意：以下函数由QMT运行时注入，IDE报红可忽略：
   - get_trade_detail_data() / order_shares() / passorder()
   - ContextInfo.get_history_data() / get_full_tick() / run_time()
   - order_callback() / deal_callback()
 ================================================================================

 【适用场景】
   底仓只有 1~5手（100~500股）的极小仓位。
   现有标准版（3层×3批）和满仓版（3层卖出×3批买回）都需要把仓位切分成多笔，
   在MIN_LOT=100股的约束下，小仓位无法拆分 → 需要本迷你版。

 【策略原理 — 单层单次反T】
   每天只做一次完整的"卖出→买回"循环，1手进、1手出。

   示例（长飞光纤, 2手=200股, 现价≈30元）:
     开盘价 30.00  ATR=0.90(3%)

     ① 卖出信号: 现价 ≥ 30.00 + 0.90×1.0 = 30.90
        → 卖出1手(100股) × 30.90 = 3,090元

     ② 买回信号: 现价 ≤ 30.90 × (1 - 3%×1.0) = 29.97
        → 买回1手(100股) × 29.97 = 2,997元

     ③ 做T收益: 3,090 - 2,997 = 93元
        扣除佣金(约13元) → 净赚 ≈ 80元

     ④ 14:50前未买回 → 强制买回（不管盈亏）

 【交易成本分析 — 小仓位做T是否划算？】
   1手 × 30元 = 3,000元交易:
     买入佣金: max(3000×0.025%, 5元) = 5元
     卖出佣金: max(3000×0.025%, 5元) = 5元
     印花税:   3000 × 0.1% = 3元
     合计: ≈13元 (占交易金额0.43%)

   → 股价波动至少 0.5% 才能覆盖成本
   → 长飞日均振幅3~5%，理论上可以做
   → 但建议底仓至少3~5手才能获得更好收益

 【与满仓版的区别】
  ┌──────────────┬─────────────────────┬─────────────────────┐
  │              │ 满仓版              │ 迷你版（本文件）    │
  ├──────────────┼─────────────────────┼─────────────────────┤
  │ 最小底仓     │ 至少10手(1000股)    │ 1~5手(100~500股)    │
  │ 卖出层级     │ 3层阶梯             │ 单层（只有1层）     │
  │ 买回批次     │ 3批                 │ 单批（只有1批）     │
  │ 每日最大交易 │ 底仓×30% (多手)     │ 1手（固定）          │
  │ 网格叠加     │ 3层网格             │ 无                   │
  │ ATR倍数      │ 0.4~1.2             │ 1.0（宽触发,保证价差）│
  │ 紧急买回     │ 0.8% / 1.5%         │ 1.5%（稍宽松）      │
  │ 尾盘兜底     │ 14:50               │ 14:50               │
  └──────────────┴─────────────────────┴─────────────────────┘

 【参数调优建议】
   底仓 1手 → MAX_T_SHARES=100 (只能做1手, 设置100即可)
   底仓 2手 → MAX_T_SHARES=100 (做1手, 留1手)
   底仓 3手 → MAX_T_SHARES=100 or 200 (最多做2手)
   底仓 5手 → MAX_T_SHARES=200 (做2手, 交易成本摊薄)
 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置
# ============================================================================

# ---------- 标的 ----------
STOCK_CODE   = '601869'
STOCK_NAME   = '长飞光纤'
STOCK_QMT    = f'{STOCK_CODE}.SH'

# ---------- ★ 仓位控制（迷你版核心参数） ★ ----------
# 每次卖出的固定手数 = 1手(100股)
# 小仓位无法拆分，只能用1手作为最小交易单元
TRADE_LOT_SIZE = 100                  # 每次做T 1手（100股）
MIN_LOT        = 100                  # A股最小交易单位

# ---------- 反T价位参数 ----------
ATR_PERIOD     = 14                   # ATR计算周期
# 卖出触发: 现价 ≥ 开盘价 + ATR × SELL_ATR_MULT
# 设为1.0以上确保价差足够覆盖佣金
SELL_ATR_MULT  = 1.0                  # 卖出ATR倍数（宽触发）
# 买回触发: 现价 ≤ 卖出成交价 × (1 - ATR% × BUYBACK_ATR_MULT)
BUYBACK_ATR_MULT = 1.0                # 买回ATR倍数

# ---------- 熔断 & 过滤 ----------
# 牛市不做反T（铁律）
VOLUME_FILTER_RATIO = 0.5             # 量比<0.5不操作
RSI_OVERBOUGHT      = 75              # RSI>75不做反T

# ---------- 紧急买回 ----------
EMERGENCY_BUYBACK_PCT = 0.015         # 卖出后涨超1.5%立即买回
STOP_LOSS_PCT         = 0.02          # 当日反T亏损上限2%

# ---------- 时间 ----------
TIMER_INTERVAL   = '3nSecond'         # 3秒定时器
FORCE_CLOSE_TIME = '14:50:00'         # 尾盘强制买回

# ---------- 数据 & 成本 ----------
HIST_DATA_LEN = 80
COMMISSION    = 0.00025               # 佣金
STAMP_TAX     = 0.001                 # 印花税


# ============================================================================
# 第二部分：技术指标（纯Python，零依赖）
# ============================================================================

def _sma(values, period):
    """简单移动平均"""
    n = len(values)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _atr(highs, lows, closes, period=14):
    """ATR = SMA(TrueRange, period)"""
    n = len(closes)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
    result = [0.0] * n
    for i in range(period, n):
        result[i] = sum(tr[i - period + 1 : i + 1]) / period
    return result


def _rsi(closes, period=14):
    """RSI 相对强弱指标"""
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
    rsi[period] = 100.0 - 100.0 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    return rsi


def compute_mini_signal(opens, highs, lows, closes, volumes):
    """
    迷你版反T信号计算 — 单层单次。
    只输出一个卖出价和一个买回价。
    """
    n = len(closes)
    if n < 60:
        return None

    curr_open   = opens[-1]
    curr_close  = closes[-1]
    curr_volume = volumes[-1]

    # ATR
    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1]
    if curr_atr <= 0:
        curr_atr = curr_close * 0.03
    curr_atr_pct = curr_atr / curr_close if curr_close > 0 else 0.03

    # 趋势
    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]
    trend = 'bull' if (curr_close > ma20 and ma5 > ma20) else \
            ('bear' if (curr_close < ma20 and ma5 < ma20) else 'sideways')

    # RSI
    curr_rsi = _rsi(closes)[-1]

    # 量比
    ma20_vol = _sma(volumes, 20)
    curr_vol_ratio = curr_volume / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # ================================================================
    #  反T决策
    # ================================================================
    do_short_t = True
    blocked_reason = ''

    # 熔断1: 牛市不反T
    if trend == 'bull':
        do_short_t = False
        blocked_reason = '牛市趋势'

    # 缩量
    if do_short_t and curr_vol_ratio < VOLUME_FILTER_RATIO:
        do_short_t = False
        blocked_reason = f'缩量(量比={curr_vol_ratio:.2f})'

    # RSI超买
    if do_short_t and curr_rsi > RSI_OVERBOUGHT:
        do_short_t = False
        blocked_reason = f'RSI超买({curr_rsi:.0f})'

    # ================================================================
    #  单层买卖价位
    # ================================================================
    # 卖出价 = 开盘价 + ATR × SELL_ATR_MULT
    sell_price = round(curr_open + curr_atr * SELL_ATR_MULT, 2)

    # 买回价 = 卖出价 × (1 - ATR% × BUYBACK_ATR_MULT)
    buyback_price = round(sell_price * (1.0 - curr_atr_pct * BUYBACK_ATR_MULT), 2)

    # 检查价差是否足够覆盖交易成本
    spread_pct = (sell_price - buyback_price) / buyback_price * 100 if buyback_price > 0 else 0
    min_spread = 0.5  # 最小需要0.5%价差覆盖佣金
    if do_short_t and spread_pct < min_spread:
        do_short_t = False
        blocked_reason = f'价差不足({spread_pct:.2f}% < {min_spread}%)'

    return {
        'do_short_t':      do_short_t,
        'blocked_reason':  blocked_reason,
        'trend':           trend,
        'sell_price':      sell_price,
        'buyback_price':   buyback_price,
        'open_price':      curr_open,
        'close_yday':      curr_close,
        'atr':             curr_atr,
        'atr_pct':         curr_atr_pct,
        'rsi':             curr_rsi,
        'vol_ratio':       curr_vol_ratio,
        'spread_pct':      spread_pct,
        'ma5':             ma5,
        'ma20':            ma20,
    }


# ============================================================================
# 第三部分：QMT 策略入口
# ============================================================================

def init(ContextInfo):
    """策略初始化"""
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account('ACCOUNT')

    state = {
        # 信号
        'daily_signal':      None,     # 当日反T信号

        # 持仓
        'base_shares':       0,        # 底仓股数
        'base_cost':         0.0,      # 底仓成本

        # ★ 迷你版简化状态（只需追踪1手的状态）
        'sold':              False,    # 今日是否已卖出
        'bought_back':       False,    # 今日是否已买回
        'sell_fill_price':   0.0,      # 实际卖出成交价

        # 风控
        'stop_loss_hit':     False,
        'force_closed':      False,
        'day_pnl':           0.0,
    }
    ContextInfo.st = state

    # 启动定时器
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")

    # 首次信号
    try:
        _init_signal(ContextInfo)
    except Exception as e:
        print(f'[init] 首次信号计算失败(可忽略): {e}')

    print(f'\n{"="*60}')
    print(f'  QMT 迷你反T策略 v1.0 — 小仓位专用')
    print(f'  标的: {STOCK_NAME}({STOCK_CODE})')
    print(f'  模式: 单层单次反T | 每次{TRADE_LOT_SIZE}股')
    print(f'  触发: 涨ATR×{SELL_ATR_MULT}卖出 | 跌ATR%×{BUYBACK_ATR_MULT}买回')
    print(f'  紧急买回: +{EMERGENCY_BUYBACK_PCT*100:.1f}%')
    print(f'  尾盘兜底: {FORCE_CLOSE_TIME}')
    print(f'{"="*60}\n')


def _init_signal(ContextInfo):
    """预计算信号"""
    st = ContextInfo.st
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')
    if STOCK_QMT in closes and len(closes[STOCK_QMT]) >= 60:
        signal = compute_mini_signal(
            opens[STOCK_QMT], highs[STOCK_QMT],
            lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
        )
        if signal:
            st['daily_signal'] = signal
            _print_signal(signal)


def _print_signal(signal):
    """打印信号"""
    print(f'\n--- {STOCK_NAME} 迷你反T ---')
    print(f'  开盘={signal["open_price"]:.2f}  昨收={signal["close_yday"]:.2f}')
    print(f'  趋势={signal["trend"]}  RSI={signal["rsi"]:.1f}  '
          f'ATR={signal["atr"]:.2f}({signal["atr_pct"]*100:.2f}%)')

    if signal['do_short_t']:
        print(f'  状态: ✓ 允许反T')
        print(f'  卖出触发价: {signal["sell_price"]:.2f} '
              f'(开盘+ATR×{SELL_ATR_MULT})')
        print(f'  买回触发价: {signal["buyback_price"]:.2f} '
              f'(预期价差{signal["spread_pct"]:.2f}%)')
    else:
        print(f'  状态: ✗ 禁止反T ({signal["blocked_reason"]})')
    print()


# ============================================================================
# 第四部分：handlebar — 日线触发
# ============================================================================

def handlebar(ContextInfo):
    """日线触发 — 刷新底仓 & 计算当日信号"""
    st = ContextInfo.st

    # 获取历史数据
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')
    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return

    # 获取底仓
    positions = get_trade_detail_data('ACCOUNT', 'STOCK', 'POSITION')
    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    if base_shares < TRADE_LOT_SIZE:
        print(f'[警告] 底仓不足1手({base_shares}股 < {TRADE_LOT_SIZE}股), 无法做T')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    # ★ 迷你版检查：底仓是否够做1手
    if base_shares < TRADE_LOT_SIZE + MIN_LOT:
        # 只有刚好1手，卖出后就没了 → 可以卖但必须确保能买回来
        print(f'[注意] 底仓仅{base_shares}股(刚好{base_shares//MIN_LOT}手), '
              f'反T卖出后底仓归零, 务必当日买回!')

    # 确认小仓位状态
    acct_list = get_trade_detail_data('ACCOUNT', 'STOCK', 'ACCOUNT')
    if acct_list:
        avail_cash = acct_list[0].m_dAvailable
        pos_value  = base_shares * closes[STOCK_QMT][-1]
        total_val  = avail_cash + pos_value
        print(f'[账户] {base_shares}股 × ¥{closes[STOCK_QMT][-1]:.2f} '
              f'= ¥{pos_value:,.0f} | 现金¥{avail_cash:,.0f}')

    # 计算信号
    signal = compute_mini_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    st['daily_signal'] = signal

    # 新交易日重置
    st['sold']         = False
    st['bought_back']  = False
    st['sell_fill_price'] = 0.0
    st['stop_loss_hit']   = False
    st['force_closed']    = False
    st['day_pnl']         = 0.0

    _print_signal(signal)


# ============================================================================
# 第五部分：ontimer — 核心执行引擎（3秒一次）
# ============================================================================

def ontimer(ContextInfo):
    """
    迷你版定时器 — 3秒检查一次。

    状态机:
      初始 → [价格≥卖出价] → 已卖出 → [价格≤买回价] → 已买回(完成)
                ↓                         ↓
            紧急买回检查              尾盘强制买回
    """
    st = ContextInfo.st
    signal = st.get('daily_signal')

    if signal is None or not signal.get('do_short_t'):
        return

    # 时间检查
    now = _now()
    if not _is_market_open(now):
        return

    # ★ 已完成（卖出+买回都做了）→ 今天不再操作
    if st['sold'] and st['bought_back']:
        return

    # ★ 尾盘强制买回（14:50之后，且已卖出但未买回）
    if now >= FORCE_CLOSE_TIME and st['sold'] and not st['bought_back'] and not st['force_closed']:
        _force_buyback(ContextInfo)
        st['force_closed'] = True
        return

    if st['force_closed'] or st['stop_loss_hit']:
        return

    # 获取实时价格
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return
    if STOCK_QMT not in tick:
        return

    last_price = tick[STOCK_QMT].get('lastPrice', 0)
    if last_price <= 0:
        return

    # ================================================================
    #  状态1: 还未卖出 → 检查是否触发卖出
    # ================================================================
    if not st['sold']:
        if last_price >= signal['sell_price']:
            _mini_sell(ContextInfo, signal['sell_price'])
            st['sold'] = True
            st['sell_fill_price'] = last_price

            # ★ 动态更新买回价（基于实际成交价而非预估卖出价）
            # 买回价 = 实际卖出价 × (1 - ATR% × BUYBACK_ATR_MULT)
            actual_buyback = round(
                last_price * (1.0 - signal['atr_pct'] * BUYBACK_ATR_MULT), 2
            )
            signal['buyback_price'] = actual_buyback
            print(f'[动态买回价] 基于实际成交价{last_price:.2f} → 买回价{actual_buyback:.2f}')

    # ================================================================
    #  状态2: 已卖出、未买回 → 检查买回触发 或 紧急买回
    # ================================================================
    elif not st['bought_back']:
        sell_price = st['sell_fill_price']

        # 正常买回: 现价 ≤ 买回价
        if last_price <= signal['buyback_price']:
            _mini_buyback(ContextInfo, signal['buyback_price'])

        # 紧急买回: 股价涨超卖出价 × (1 + 1.5%)
        elif last_price >= sell_price * (1.0 + EMERGENCY_BUYBACK_PCT):
            rise = (last_price - sell_price) / sell_price * 100
            print(f'[紧急买回] 🔴 {STOCK_NAME} 卖出价{sell_price:.2f} → '
                  f'现价{last_price:.2f}(+{rise:.2f}%) 立即买回!')
            _mini_buyback(ContextInfo, last_price)

        # 止损: 亏损超限
        elif last_price >= sell_price and st['day_pnl'] < 0:
            loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
            if abs(st['day_pnl']) > loss_limit:
                print(f'[止损] 当日亏损{st["day_pnl"]:.0f}元触发止损, 强制买回')
                _mini_buyback(ContextInfo, last_price)
                st['stop_loss_hit'] = True


# ============================================================================
# 第六部分：下单 & 强制平仓（迷你版）
# ============================================================================

def _mini_sell(ContextInfo, price):
    """
    迷你卖出: 1手(TRADE_LOT_SIZE) × 指定价。
    order_shares(代码, 正=买/负=卖, 下单方式, 价格, ContextInfo, 账号)
    """
    st = ContextInfo.st
    acc = _accid(ContextInfo)

    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, acc)
        print(f'[反T卖出] {price:.2f}元 × {TRADE_LOT_SIZE}股')
        st['sold'] = True
    except Exception as e:
        print(f'[反T卖出失败] {e}')


def _mini_buyback(ContextInfo, price):
    """
    迷你买回: 1手(TRADE_LOT_SIZE) × 指定价。
    买入前检查可用资金是否足够。
    """
    st = ContextInfo.st
    acc = _accid(ContextInfo)

    # 检查可用资金
    need_cash = price * TRADE_LOT_SIZE * 1.001
    avail = _avail_cash(ContextInfo)
    if avail < need_cash:
        print(f'[买回失败] 可用资金不足: 需{need_cash:.0f} | 可用{avail:.0f}')
        return

    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, acc)
        print(f'[反T买回] {price:.2f}元 × {TRADE_LOT_SIZE}股')
        st['bought_back'] = True
    except Exception as e:
        print(f'[反T买回失败] {e}')


def _force_buyback(ContextInfo):
    """
    尾盘强制买回 — 用对手价快速成交。
    """
    st = ContextInfo.st
    acc = _accid(ContextInfo)

    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, acc)
        st['bought_back'] = True
        print(f'[尾盘强制买回] 对手价 × {TRADE_LOT_SIZE}股 — 底仓已恢复')
    except Exception as e:
        print(f'[尾盘强制买回失败!!] {e} — 请手动买回{TRADE_LOT_SIZE}股!')


def _avail_cash(ContextInfo):
    """获取可用资金"""
    try:
        acct = get_trade_detail_data('ACCOUNT', 'STOCK', 'ACCOUNT')
        if acct:
            return acct[0].m_dAvailable
    except Exception:
        pass
    return 0.0


def _accid(ContextInfo):
    """获取账号ID"""
    return ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else 'ACCOUNT'


# ============================================================================
# 第七部分：委托/成交回调 & 工具函数
# ============================================================================

def order_callback(ContextInfo, order):
    """委托状态回调"""
    st_map = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    if order.m_nOrderStatus in (53, 54, 55, 56):
        s = st_map.get(order.m_nOrderStatus, '?')
        print(f'[委托] ¥{order.m_dOrderPrice:.2f} '
              f'{order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {s}')


def deal_callback(ContextInfo, deal):
    """成交回报回调"""
    st = ContextInfo.st
    d = '买入' if deal.m_nDirection == 1 else '卖出'
    amt = deal.m_dPrice * deal.m_nVolume
    cost = deal.m_fCommission + deal.m_fStampTax

    if deal.m_nDirection == 1:  # 买回
        st['day_pnl'] -= (amt + cost)
    else:                        # 卖出
        st['day_pnl'] += (amt - cost)

    print(f'[成交] {d} ¥{deal.m_dPrice:.2f} × {deal.m_nVolume}股 '
          f'PnL≈{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    """策略停止"""
    st = getattr(ContextInfo, 'st', None)
    if st:
        sold = st.get('sold', False)
        bought = st.get('bought_back', False)
        print(f'\n[{STOCK_NAME}] 迷你反T策略已停止')
        if sold and not bought:
            print(f'  ⚠⚠ 已卖出{TRADE_LOT_SIZE}股未买回！请手动补仓！')
        else:
            print(f'  底仓完好 ✓  |  PnL≈{st.get("day_pnl", 0):.0f}')


def _now():
    import time as _t
    return _t.strftime('%H:%M:%S')


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
