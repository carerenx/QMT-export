# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 迷你反T策略 v2.0 — 冲高回落卖出 + 下跌回升买入
================================================================================
 注意：以下函数由QMT运行时注入，IDE报红可忽略：
   - get_trade_detail_data() / order_shares()
   - ContextInfo.get_history_data() / get_full_tick() / run_time()
   - order_callback() / deal_callback()
 ================================================================================

 【核心理念 — 为什么"冲高回落"比"触及即卖"更好？】
                    ┌─────────────────────────────────┐
   31.50 ─          │  如果冲到31.50才回落             │
         │  ╱╲      │  冲高回落卖在31.10               │
   31.00 ─│╱    ╲   │  比30.90触及即卖多赚0.20/股      │
         │        ╲ │                                  │
   30.90 ───────────│─ 触发线(开盘+ATR×1.0)            │
         │          │  旧策略: 到这里就卖了              │
   30.60 ─          │  新策略: 等它冲完回落再卖          │
                    └─────────────────────────────────┘

   卖在更高的位置 + 避开"假突破"（冲到30.92就掉头的陷阱）

 【状态机 — 五个状态】
                             ┌─────────────────────┐
                             │   [盘中监控]         │
                             │  STATE_IDLE          │ ← 等待价格触发
                             └──────┬──────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │ 价格 ≥ 卖出触发线              │ 价格 ≤ 买回触发线
                    ▼ (进入"冲高监控")             ▼ (进入"下跌监控")
         ┌──────────────────┐          ┌──────────────────┐
         │ STATE_SPIKING    │          │ STATE_DIPPING    │
         │ 记录最高价        │          │ 记录最低价        │
         │ 判断是否回落      │          │ 判断是否回升      │
         └────────┬─────────┘          └────────┬─────────┘
                  │                              │
       ┌──────────┼──────────┐        ┌──────────┼──────────┐
       │ 从最高价     │ 价格跌回  │        │ 从最低价     │ 价格涨回  │
       │ 回落≥阈值    │ 触发线下  │        │ 回升≥阈值    │ 触发线上  │
       ▼              ▼          │        ▼              ▼          │
  ┌─────────┐   ┌──────────┐    │   ┌─────────┐   ┌──────────┐    │
  │确认见顶! │   │假突破     │    │   │确认见底! │   │假跌破     │    │
  │→ 卖出   │   │→回到IDLE │    │   │→ 买回   │   │→回到SOLD │    │
  └────┬────┘   └──────────┘    │   └────┬────┘   └──────────┘    │
       │                         │        │                         │
       ▼                         │        ▼                         │
  ┌──────────┐                   │   ┌──────────┐                  │
  │STATE_SOLD│ ← 已卖出,等待买回  │   │STATE_DONE│ ← 今日完成       │
  └──────────┘                   │   └──────────┘                  │
       │                         │                                  │
       │ 14:50前未买回 → 强制买回 │                                  │
       │ 涨超紧急买回线 → 强制买回│                                  │
       └─────────────────────────┘

 【"冲高回落"和"下跌回升"的详细逻辑】

   冲高回落卖出:
     1. 现价 ≥ 卖出触发线(open + ATR×1.0) → 进入冲高监控
     2. 持续追踪最高价(peak_price), 每3秒更新
     3. 当 现价 ≤ peak_price × (1 - 回落比例) → 确认见顶 → 执行卖出
     4. 如果 现价 < 卖出触发线 → 假突破 → 撤销监控, 回到IDLE

   下跌回升买入:
     1. 已卖出后, 现价 ≤ 买回触发线(sell_price - ATR%×1.0) → 进入下跌监控
     2. 持续追踪最低价(dip_price), 每3秒更新
     3. 当 现价 ≥ dip_price × (1 + 回升比例) → 确认见底 → 执行买回
     4. 如果 现价 > 买回触发线 → 假跌破 → 撤销监控, 回到SOLD

 【参数说明】
   PULLBACK_PCT: 从最高点回落多少比例确认见顶 (默认0.3%)
   BOUNCE_PCT:   从最低点回升多少比例确认见底 (默认0.3%)
   → 值越大越"迟钝"(等更明确的信号), 值越小越"敏感"(快进快出)
   → 对于日内振幅3%+的股票, 0.2%~0.5%是比较合理的范围

 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置
# ============================================================================

# ---------- 标的 ----------
STOCK_CODE   = '601869'
STOCK_NAME   = '长飞光纤'
STOCK_QMT    = f'{STOCK_CODE}.SH'

# ---------- 仓位 ----------
TRADE_LOT_SIZE = 100                  # 每次做T 1手
MIN_LOT        = 100

# ---------- 反T 触发线参数 ----------
ATR_PERIOD     = 14
# 卖出触发线: 价格越过这条线 → 进入"冲高监控"
SELL_TRIGGER_MULT = 1.0               # 卖出触发线 = 开盘 + ATR×此值
# 买回触发线: 价格跌破这条线 → 进入"下跌监控"
BUYBACK_TRIGGER_MULT = 1.0            # 买回触发线 = 卖出价 × (1 - ATR%×此值)

# ---------- ★ 冲高回落 & 下跌回升参数（核心新增） ★ ----------
# 回落确认: 从最高点回落超过此比例 → 确认见顶 → 卖出
PULLBACK_PCT = 0.003                  # 0.3% (对于30元股票 ≈ 0.09元)
# 回升确认: 从最低点回升超过此比例 → 确认见底 → 买回
BOUNCE_PCT   = 0.003                  # 0.3%

# ---------- 熔断 & 过滤 ----------
VOLUME_FILTER_RATIO = 0.5             # 量比<0.5不操作
RSI_OVERBOUGHT      = 75              # RSI>75不操作

# ---------- 紧急买回 & 止损 ----------
EMERGENCY_BUYBACK_PCT = 0.015         # 卖出后涨超1.5%紧急买回
STOP_LOSS_PCT         = 0.02          # 单日亏损上限2%

# ---------- 时间 ----------
TIMER_INTERVAL   = '3nSecond'         # 3秒定时器
FORCE_CLOSE_TIME = '14:50:00'         # 尾盘强制买回

# ---------- 数据 ----------
HIST_DATA_LEN = 80
COMMISSION    = 0.00025
STAMP_TAX     = 0.001


# ============================================================================
# 第二部分：技术指标（纯Python）
# ============================================================================

def _sma(values, period):
    n = len(values)
    r = [0.0] * n
    for i in range(period - 1, n):
        r[i] = sum(values[i - period + 1 : i + 1]) / period
    return r


def _atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i]  - closes[i - 1]))
    r = [0.0] * n
    for i in range(period, n):
        r[i] = sum(tr[i - period + 1 : i + 1]) / period
    return r


def _rsi(closes, period=14):
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    rsi = [50.0] * n
    g, l = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        g.append(d if d > 0 else 0)
        l.append(abs(d) if d < 0 else 0)
    ag = sum(g[:period]) / period
    al = sum(l[:period]) / period
    rsi[period] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    for i in range(period, n - 1):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    return rsi


def compute_signal(opens, highs, lows, closes, volumes):
    """计算当日反T信号"""
    n = len(closes)
    if n < 60:
        return None

    co = opens[-1]
    cc = closes[-1]
    cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]
    trend = 'bull' if (cc > ma20 and ma5 > ma20) else \
            ('bear' if (cc < ma20 and ma5 < ma20) else 'sideways')

    curr_rsi = _rsi(closes)[-1]

    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # 方向决策
    do_short = True
    reason   = ''
    if trend == 'bull':
        do_short, reason = False, '牛市趋势'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short, reason = False, f'缩量({curr_vr:.2f})'
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short, reason = False, f'RSI超买({curr_rsi:.0f})'

    # 价位计算
    sell_trigger    = round(co + curr_atr * SELL_TRIGGER_MULT, 2)
    buyback_trigger = round(sell_trigger * (1.0 - curr_atr_pct * BUYBACK_TRIGGER_MULT), 2)

    # 价差检查
    spread = (sell_trigger - buyback_trigger) / buyback_trigger * 100 if buyback_trigger > 0 else 0
    if do_short and spread < 0.5:
        do_short, reason = False, f'价差不足({spread:.2f}%)'

    return {
        'do_short':        do_short,
        'blocked_reason':  reason,
        'trend':           trend,
        'sell_trigger':    sell_trigger,       # 卖出触发线（越过此线进入冲高监控）
        'buyback_trigger': buyback_trigger,    # 买回触发线（跌破此线进入下跌监控）
        'open_price':      co,
        'close_yday':      cc,
        'atr':             curr_atr,
        'atr_pct':         curr_atr_pct,
        'rsi':             curr_rsi,
        'vol_ratio':       curr_vr,
        'spread_pct':      spread,
    }


# ============================================================================
# 第三部分：QMT 策略入口
# ============================================================================

# ---- 状态常量（使代码更具可读性） ----
STATE_IDLE    = 'IDLE'     # 等待触发
STATE_SPIKING = 'SPIKING'  # 冲高监控（价格越过卖出触发线, 等回落）
STATE_DIPPING = 'DIPPING'  # 下跌监控（价格跌破买回触发线, 等回升）
STATE_SOLD    = 'SOLD'     # 已卖出, 等待买回时机
STATE_DONE    = 'DONE'     # 今日完成(卖出+买回=配对)
STATE_FORCED  = 'FORCED'   # 强制买回完成


def init(ContextInfo):
    """策略初始化"""
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account('ACCOUNT')

    state = {
        # 信号
        'daily_signal': None,

        # 持仓
        'base_shares': 0,
        'base_cost':   0.0,

        # ★ 状态机核心变量
        'fstate':        STATE_IDLE,  # 当前状态 (Finite State)
        'peak_price':    0.0,         # 冲高监控中的最高价
        'dip_price':     0.0,         # 下跌监控中的最低价
        'sell_fill_price': 0.0,       # 实际卖出成交价

        # 风控
        'day_pnl':        0.0,
        'stop_loss_hit':  False,
    }
    ContextInfo.st = state

    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")

    print(f'\n{"="*60}')
    print(f'  QMT 迷你反T策略 v2.0 — 冲高回落卖出 + 下跌回升买入')
    print(f'  标的: {STOCK_NAME}({STOCK_CODE}) | 每次{TRADE_LOT_SIZE}股')
    print(f'  卖出触发线: 开盘+ATR×{SELL_TRIGGER_MULT}')
    print(f'  冲高回落确认: 从最高点回落{PULLBACK_PCT*100:.1f}% → 卖出')
    print(f'  下跌回升确认: 从最低点回升{BOUNCE_PCT*100:.1f}% → 买回')
    print(f'  紧急买回: +{EMERGENCY_BUYBACK_PCT*100:.1f}% | 尾盘: {FORCE_CLOSE_TIME}')
    print(f'{"="*60}\n')


# ============================================================================
# 第四部分：handlebar — 日线触发
# ============================================================================

def handlebar(ContextInfo):
    """日线触发"""
    st = ContextInfo.st

    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')
    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return

    positions = get_trade_detail_data('ACCOUNT', 'STOCK', 'POSITION')
    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    if base_shares < TRADE_LOT_SIZE:
        print(f'[警告] 底仓不足1手({base_shares}股), 无法做T')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    signal = compute_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    st['daily_signal'] = signal

    # 新交易日重置状态机
    st['fstate']          = STATE_IDLE
    st['peak_price']      = 0.0
    st['dip_price']       = 0.0
    st['sell_fill_price'] = 0.0
    st['day_pnl']         = 0.0
    st['stop_loss_hit']   = False

    _print_signal(ContextInfo)


def _print_signal(ContextInfo):
    s = ContextInfo.st['daily_signal']
    st_name = {STATE_IDLE:'等待触发', STATE_SPIKING:'冲高监控', STATE_DIPPING:'下跌监控',
               STATE_SOLD:'已卖出待买回', STATE_DONE:'完成', STATE_FORCED:'强制平仓'}
    print(f'\n--- {STOCK_NAME} 迷你反T v2.0 ---')
    print(f'  状态: {st_name[ContextInfo.st["fstate"]]}')
    print(f'  开盘={s["open_price"]:.2f}  昨收={s["close_yday"]:.2f}')
    print(f'  趋势={s["trend"]}  RSI={s["rsi"]:.1f}  ATR={s["atr"]:.2f}({s["atr_pct"]*100:.2f}%)')
    if s['do_short']:
        print(f'  反T: ✓')
        print(f'  卖出触发线: {s["sell_trigger"]:.2f} (越过→冲高监控→回落{PULLBACK_PCT*100:.1f}%卖出)')
        print(f'  买回触发线: {s["buyback_trigger"]:.2f} (跌破→下跌监控→回升{BOUNCE_PCT*100:.1f}%买回)')
    else:
        print(f'  反T: ✗ ({s["blocked_reason"]})')
    print()


# ============================================================================
# 第五部分：ontimer — ★ 状态机驱动核心 ★
# ============================================================================

def ontimer(ContextInfo):
    """
    定时器回调 — 状态机驱动。

    5个状态: IDLE → SPIKING → SOLD → DIPPING → DONE
              ↑        ↓(假突破)               ↑
              └────────┘                 ┌──────┘(假跌破)
                                        SOLD ←──┘
    """
    st = ContextInfo.st
    signal = st.get('daily_signal')

    if signal is None or not signal.get('do_short'):
        return

    now = _now()
    if not _is_market_open(now):
        return

    # 已结束状态 — 不再操作
    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return

    # 获取实时价格
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return
    if STOCK_QMT not in tick:
        return

    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0:
        return

    fstate  = st['fstate']
    trig_s  = signal['sell_trigger']     # 卖出触发线
    trig_b  = signal['buyback_trigger']  # 买回触发线

    # ================================================================
    #  状态机路由
    # ================================================================

    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price, trig_s)

    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price, trig_s)

    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price, trig_b)

    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price, trig_b)

    # ================================================================
    #  全局安全检查（不依赖状态）
    # ================================================================

    # 尾盘强制买回: 14:50后, 只要卖出过但未买回
    if now >= FORCE_CLOSE_TIME:
        if fstate in (STATE_SOLD, STATE_DIPPING):
            _force_buyback(ContextInfo)
            return
        if fstate in (STATE_SPIKING, STATE_IDLE):
            # 还没卖出, 错过今天的机会了
            st['fstate'] = STATE_DONE

    # 止损: 不在任何活跃监控状态中触发, 只在SOLD中触发
    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st['day_pnl'] < -loss_limit:
            print(f'[止损] 亏损{st["day_pnl"]:.0f}元 触发止损')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 第六部分：状态处理函数
# ============================================================================

def _handle_idle(ContextInfo, price, sell_trigger):
    """
    状态: IDLE（等待触发）

    唯一事件: 价格越过卖出触发线 → 进入 SPIKING（冲高监控）
    """
    st = ContextInfo.st

    if price >= sell_trigger:
        st['fstate']     = STATE_SPIKING
        st['peak_price'] = price  # 记录当前最高价
        print(f'[冲高监控] 🔍 价格{price:.2f}越过卖出触发线{sell_trigger:.2f}'
              f' → 等待回落{PULLBACK_PCT*100:.1f}%确认')


def _handle_spiking(ContextInfo, price, sell_trigger):
    """
    状态: SPIKING（冲高监控）

    事件A: 价格继续涨 → 更新最高价
    事件B: 从最高点回落 ≥ PULLBACK_PCT → 确认见顶 → 卖出 → 进入SOLD
    事件C: 价格跌回卖出触发线以下 → 假突破 → 回到IDLE
    """
    st = ContextInfo.st

    # 事件A: 更新最高价
    if price > st['peak_price']:
        st['peak_price'] = price

    peak = st['peak_price']
    pullback_from_peak = (peak - price) / peak

    # 事件B: 回落确认 → 卖出!
    if pullback_from_peak >= PULLBACK_PCT:
        print(f'[冲高回落确认] ✅ 最高{peak:.2f} → 现价{price:.2f}'
              f'(回落{pullback_from_peak*100:.2f}%) → 执行卖出!')
        _mini_sell(ContextInfo, price)
        st['fstate']          = STATE_SOLD
        st['sell_fill_price'] = price

        # ★ 动态更新买回触发线（基于实际卖出价）
        signal = st['daily_signal']
        new_buyback = round(price * (1.0 - signal['atr_pct'] * BUYBACK_TRIGGER_MULT), 2)
        signal['buyback_trigger'] = new_buyback
        print(f'  买回触发线更新为: {new_buyback:.2f}')

    # 事件C: 假突破 → 回到等待
    elif price < sell_trigger:
        print(f'[假突破] 价格{price:.2f}跌回触发线下{sell_trigger:.2f} → 撤销监控')
        st['fstate']     = STATE_IDLE
        st['peak_price'] = 0.0


def _handle_sold(ContextInfo, price, buyback_trigger):
    """
    状态: SOLD（已卖出，等待买回）

    事件A: 价格跌破买回触发线 → 进入 DIPPING（下跌监控）
    事件B: 紧急买回 — 价格涨超卖出成交价×1.5% → 强制买回
    """
    st = ContextInfo.st
    sell_price = st['sell_fill_price']

    # 事件B: 紧急买回（先检查，优先级最高）
    if price >= sell_price * (1.0 + EMERGENCY_BUYBACK_PCT):
        rise = (price - sell_price) / sell_price * 100
        print(f'[紧急买回] 🔴 卖出价{sell_price:.2f} → 现价{price:.2f}(+{rise:.2f}%) 立即买回!')
        _mini_buyback(ContextInfo, price)
        return

    # 事件A: 进入下跌监控
    if price <= buyback_trigger:
        st['fstate']    = STATE_DIPPING
        st['dip_price'] = price
        print(f'[下跌监控] 🔍 价格{price:.2f}跌破买回触发线{buyback_trigger:.2f}'
              f' → 等待回升{BOUNCE_PCT*100:.1f}%确认')


def _handle_dipping(ContextInfo, price, buyback_trigger):
    """
    状态: DIPPING（下跌监控）

    事件A: 价格继续跌 → 更新最低价
    事件B: 从最低点回升 ≥ BOUNCE_PCT → 确认见底 → 买回 → 进入DONE
    事件C: 价格涨回买回触发线以上 → 假跌破 → 回到SOLD
    """
    st = ContextInfo.st

    # 事件A: 更新最低价
    if price < st['dip_price']:
        st['dip_price'] = price

    dip = st['dip_price']
    bounce_from_dip = (price - dip) / dip

    # 事件B: 回升确认 → 买回!
    if bounce_from_dip >= BOUNCE_PCT:
        print(f'[下跌回升确认] ✅ 最低{dip:.2f} → 现价{price:.2f}'
              f'(回升{bounce_from_dip*100:.2f}%) → 执行买回!')
        _mini_buyback(ContextInfo, price)
        # 买回后计算收益
        sell_p = st['sell_fill_price']
        gross  = (sell_p - price) * TRADE_LOT_SIZE
        print(f'  今日反T完成: 卖{sell_p:.2f} 买{price:.2f} 毛利≈{gross:.0f}元')

    # 事件C: 假跌破 → 回到等待
    elif price > buyback_trigger:
        print(f'[假跌破] 价格{price:.2f}涨回触发线上{buyback_trigger:.2f} → 撤销监控')
        st['fstate']    = STATE_SOLD
        st['dip_price'] = 0.0


# ============================================================================
# 第七部分：下单 & 强制平仓
# ============================================================================

def _mini_sell(ContextInfo, price):
    """迷你卖出: 1手 × 指定价"""
    st = ContextInfo.st
    acc = _acc(ContextInfo)
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, acc)
        print(f'  [反T卖出] {price:.2f}元 × {TRADE_LOT_SIZE}股')
    except Exception as e:
        print(f'  [卖出失败] {e}')
        st['fstate'] = STATE_IDLE  # 回退


def _mini_buyback(ContextInfo, price):
    """迷你买回: 1手 × 指定价"""
    st = ContextInfo.st
    acc = _acc(ContextInfo)
    need = price * TRADE_LOT_SIZE * 1.001
    if _cash(ContextInfo) < need:
        print(f'  [买回失败] 资金不足: 需{need:.0f}')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, acc)
        print(f'  [反T买回] {price:.2f}元 × {TRADE_LOT_SIZE}股')
        st['fstate'] = STATE_DONE
    except Exception as e:
        print(f'  [买回失败] {e}')


def _force_buyback(ContextInfo):
    """尾盘强制买回 — 对手价"""
    st = ContextInfo.st
    acc = _acc(ContextInfo)
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, acc)
        st['fstate'] = STATE_FORCED
        print(f'[尾盘强制买回] {TRADE_LOT_SIZE}股(对手价) — 底仓已恢复')
    except Exception as e:
        print(f'[尾盘失败!!] {e} — 请手动买回{TRADE_LOT_SIZE}股')
        st['fstate'] = STATE_FORCED


def _cash(ContextInfo):
    try:
        a = get_trade_detail_data('ACCOUNT', 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception:
        return 0.0


def _acc(ContextInfo):
    return ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else 'ACCOUNT'


# ============================================================================
# 第八部分：委托/成交回调 & 工具函数
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    if order.m_nOrderStatus in sm:
        print(f'[委托] ¥{order.m_dOrderPrice:.2f} '
              f'{order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    d = '买入' if deal.m_nDirection == 1 else '卖出'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    st['day_pnl'] += (amt - fee) if deal.m_nDirection == 2 else -(amt + fee)
    print(f'[成交] {d} ¥{deal.m_dPrice:.2f} × {deal.m_nVolume}股 PnL≈{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        fs = st.get('fstate', '?')
        print(f'\n[{STOCK_NAME}] 迷你反T v2.0 已停止 | 最终状态: {fs}')
        if fs in (STATE_SOLD, STATE_DIPPING):
            print(f'  ⚠⚠ 已卖出未买回！请手动补仓{TRADE_LOT_SIZE}股!')


def _now():
    import time as _t
    return _t.strftime('%H:%M:%S')


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
