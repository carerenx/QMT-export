# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你反T策略 v5.0 — 动态买回版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()
 ================================================================================

 【v5.0 核心改动 — 买回触发线改为基于"实际卖出价"动态计算】

  v4及之前:
    买回触发线 = 开盘价计算出的固定值 (全天不变, 不管你实际卖在什么价位)
    问题: 你390卖出的, 买回线可能是375(基于开盘价算的), 跌3.8%才触发, 太慢

  v5.0:
    买回触发线 = 实际卖出成交价 × (1 - ATR% × BUYBACK_MULT)
    效果: ATR%=7%, BUYBACK_MULT=0.15 → 7%×0.15≈1.05% → 卖出后跌约1%就触发买回

    为什么用 ATR% × 0.15 而不是硬编码1%?
      - 低波动股(ATR%=2%): 2%×0.15=0.3%   → 小幅回撤就买回(快进快出)
      - 高波动股(ATR%=8%): 8%×0.15=1.2%   → 跌够1.2%才触发(避免假跌破)
      → 自适应: 波动越大, 容忍回撤越大, 避免频繁交易

 【状态机 — 简化为3个活跃状态】
  IDLE → SPIKING(冲高监控,等回落) → SOLD(已卖出,等跌到买回线)
                                            │
                              ┌─────────────┼─────────────┐
                              ▼             ▼             ▼
                          跌到买回线     涨超紧急线    14:57尾盘
                              │             │             │
                              ▼             ▼             ▼
                          DIPPING      立即买回       强制买回
                         (等回升)
                            │
                            ▼
                          买回 → DONE

 【详细日志示例】
  [09:35:12] [冲高] 465.20越过464.88(+0.07%) → 等回落0.10%确认
  [09:35:12]   peak=465.20 | 回落线=464.73 | 距卖=0.47元
  [09:36:45] [卖出] 最高466.80回落0.12%→466.24 | 卖在466.24
  [09:36:45]   买入触发线=461.40(466.24-1.04%) | ATR%=6.5%×乘数0.16
  [09:36:45]   止损线=481.30(+3.2%) | 紧急买回=475.56(+2.0%)
  [10:15:33] [买回触发] 460.80 ≤ 461.40 | 距卖-1.17% → 进入下跌监控
  [10:15:33]   dip=460.80 | 回升线=461.26(+0.10%) | 需回升0.46元
  [10:18:09] [买回] 最低459.20回升0.15%→459.89 | 买在459.89
  [10:18:09]   毛利=(466.24-459.89)×100=635元 | 净利≈635-70=565元

 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置
# ============================================================================
ACCOUNT = '8890145315'

STOCK_CODE      = '601869'
STOCK_NAME      = '长飞光纤'
STOCK_QMT       = f'{STOCK_CODE}.SH'

# 仓位
TRADE_LOT_SIZE  = 100
MIN_LOT         = 100

# 检测周期
TIMER_INTERVAL  = '1nSecond'

# ---- 反T触发线 ----
ATR_PERIOD           = 14
SELL_TRIGGER_MULT    = 1.0              # 卖出触发线 = 开盘 + ATR×此值

# ★ v5: 高ATR自适应（不变）
ATR_HIGH_THRESHOLD   = 0.05
SELL_TRIGGER_MULT_HI = 0.7
ATR_VERYHIGH_THRESH  = 0.07
SELL_TRIGGER_MULT_VH = 0.5

# ---- 冲高回落确认（卖出用） ----
PULLBACK_PCT = 0.0010                   # 从最高点回落0.10% → 确认见顶 → 卖出

# ---- ★ v5核心: 买回触发 — 基于卖出价动态计算 ★ ----
# 买回触发线 = 实际卖出成交价 × (1 - ATR% × BUYBACK_MULT)
#   ATR%=7% → 7%×0.15=1.05% → 卖出价跌约1%触发买回监控
#   ATR%=3% → 3%×0.15=0.45% → 卖出价跌约0.5%触发买回监控
#   → 波动越大的股票, 容忍回撤幅度越大(自适应)
BUYBACK_TRIGGER_MULT = 0.15             # 乘数: 0.15 → 约1%回撤(ATR%=7%时)

# ★ v5: 买回回升确认（不变, 但日志更详细）
BOUNCE_PCT = 0.0010                     # 从最低点回升0.10% → 确认见底 → 买回

# ---- 熔断 & 过滤 ----
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75

# ---- 紧急买回 & 止损 ----
EMERGENCY_BUYBACK_PCT = 0.02            # 卖出后涨超2%紧急买回
STOP_LOSS_PCT         = 0.015           # 单日亏损上限1.5%

# ---- 时间 ----
FORCE_CLOSE_TIME = '14:57:00'

# ---- 数据 ----
HIST_DATA_LEN = 80
COMMISSION    = 0.00025
STAMP_TAX     = 0.001


# ============================================================================
# 第二部分：技术指标
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
    result = [0.0] * n
    for i in range(period, n):
        result[i] = sum(tr[i - period + 1 : i + 1]) / period
    return result


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
    """计算当日反T信号 — 只输出卖出相关, 买回在卖出后动态计算"""
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

    # ATR自适应乘数
    if curr_atr_pct > ATR_VERYHIGH_THRESH:
        sell_mult = SELL_TRIGGER_MULT_VH
    elif curr_atr_pct > ATR_HIGH_THRESHOLD:
        sell_mult = SELL_TRIGGER_MULT_HI
    else:
        sell_mult = SELL_TRIGGER_MULT

    # 方向决策
    do_short, reason = True, ''
    if trend == 'bull':
        do_short, reason = False, '牛市禁反T'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short, reason = False, f'缩量(量比{curr_vr:.2f})'
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short, reason = False, f'RSI超买({curr_rsi:.0f})'

    sell_trigger = round(co + curr_atr * sell_mult, 2)

    return {
        'do_short':        do_short,
        'blocked_reason':  reason,
        'trend':           trend,
        'sell_trigger':    sell_trigger,
        'open_price':      co,
        'close_yday':      cc,
        'atr':             curr_atr,
        'atr_pct':         curr_atr_pct,
        'rsi':             curr_rsi,
        'vol_ratio':       curr_vr,
        'sell_mult_used':  sell_mult,
        # ★ v5: 买回触发线不再预计算, 卖出后用实际成交价动态算
        # 但记录参数供日志展示
        'buyback_mult':    BUYBACK_TRIGGER_MULT,
        'bounce_pct':      BOUNCE_PCT,
    }


# ============================================================================
# 第三部分：QMT 策略入口
# ============================================================================

STATE_IDLE    = 'IDLE'
STATE_SPIKING = 'SPIKING'
STATE_DIPPING = 'DIPPING'
STATE_SOLD    = 'SOLD'
STATE_DONE    = 'DONE'
STATE_FORCED  = 'FORCED'


def init(ContextInfo):
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account(ACCOUNT)

    state = {
        'daily_signal':     None,
        'base_shares':      0,
        'base_cost':        0.0,
        'fstate':           STATE_IDLE,
        'peak_price':       0.0,
        'dip_price':        0.0,
        'sell_fill_price':  0.0,
        # ★ v5: 卖出时计算的动态买回触发线(基于实际sell_fill_price)
        'buyback_target':   0.0,        # 买回触发线 = sell_price × (1 - ATR% × BUYBACK_MULT)
        'buyback_target_pct': 0.0,      # 买回目标跌幅百分比(日志用)
        'day_pnl':          0.0,
        'stop_loss_hit':    False,
        'total_t_days':     0,
        'total_pnl':        0.0,
        'entry_price':      0.0,
        'startup_printed':  False,
        # ★ v5: 时间追踪(日志用)
        'state_enter_time': '',         # 进入当前状态的时间
    }
    ContextInfo.st = state
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


# ============================================================================
# 第四部分：handlebar — 日线触发
# ============================================================================

def handlebar(ContextInfo):
    st = ContextInfo.st
    is_live = ContextInfo.is_last_bar()

    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')
    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return

    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    accounts  = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')

    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    if base_shares < TRADE_LOT_SIZE:
        if is_live:
            _log(f'[警告] 底仓不足1手({base_shares}股) - 策略等待中')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    if st['entry_price'] == 0.0:
        st['entry_price'] = base_cost

    curr_close = closes[STOCK_QMT][-1]
    avail_cash = accounts[0].m_dAvailable if accounts else 0.0
    pos_value  = base_shares * curr_close
    total_val  = pos_value + avail_cash

    signal = compute_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    st['daily_signal'] = signal

    # 新交易日重置
    st['fstate']            = STATE_IDLE
    st['peak_price']        = 0.0
    st['dip_price']         = 0.0
    st['sell_fill_price']   = 0.0
    st['buyback_target']    = 0.0
    st['buyback_target_pct'] = 0.0
    st['day_pnl']           = 0.0
    st['stop_loss_hit']     = False
    st['state_enter_time']  = _now()

    if is_live:
        _print_status(ContextInfo, curr_close, avail_cash, pos_value, total_val)
        _print_signal(ContextInfo)
    else:
        if not st['startup_printed']:
            _log(f'{"="*55}')
            _log(f'  {STOCK_NAME} v5.0 动态买回版 — 已加载')
            _log(f'  现价 ¥{curr_close:.2f} | ATR {signal["atr"]:.2f}元({signal["atr_pct"]*100:.1f}%)')
            _log(f'  卖出乘数 {signal["sell_mult_used"]}(基准{SELL_TRIGGER_MULT})')
            _log(f'  买回乘数 {signal["buyback_mult"]} → 约{signal["atr_pct"]*signal["buyback_mult"]*100:.2f}%回撤触发')
            _log(f'  回升确认 {signal["bounce_pct"]*100:.2f}%')
            _log(f'{"="*55}')
            st['startup_printed'] = True


def _print_status(ContextInfo, curr_close, avail_cash, pos_value, total_val):
    st = ContextInfo.st
    cost = st['entry_price']
    unreal_pnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_close / cost - 1) * 100 if cost > 0 else 0

    _log(f'━━━ {"账户状态":─^20} ━━━')
    _log(f'  持仓: {st["base_shares"]}股 × ¥{curr_close:.2f} = ¥{pos_value:,.0f}')
    _log(f'  浮动盈亏: ¥{unreal_pnl:,.0f}({unreal_pct:+.1f}%) | 现金: ¥{avail_cash:,.0f} | 总资产: ¥{total_val:,.0f}')
    lot_cost = curr_close * TRADE_LOT_SIZE
    if avail_cash >= lot_cost * 1.01:
        _log(f'  正T: ✓ 可用(1手需¥{lot_cost:,.0f})')
    else:
        _log(f'  正T: ✗ 不可用(1手需¥{lot_cost:,.0f} > 现金¥{avail_cash:,.0f} 缺口¥{lot_cost-avail_cash:,.0f})')
    if st['total_t_days'] > 0:
        _log(f'  累计反T: {st["total_t_days"]}天 | 盈亏: ¥{st["total_pnl"]:,.0f}')


def _print_signal(ContextInfo):
    s = ContextInfo.st['daily_signal']
    _log(f'━━━ {"做T信号":─^20} ━━━')
    _log(f'  开盘 ¥{s["open_price"]:.2f} | ATR ¥{s["atr"]:.2f}({s["atr_pct"]*100:.2f}%) | 乘数 {s["sell_mult_used"]}')
    _log(f'  趋势 {s["trend"]} | RSI {s["rsi"]:.1f} | 量比 {s["vol_ratio"]:.2f}')

    if s['do_short']:
        _log(f'  反T: ✓ 允许')
        pullback_yuan = s['sell_trigger'] * PULLBACK_PCT
        # 估算买回触发(基于开盘价估算, 实际会基于卖出价重算)
        est_buyback_pct = s['atr_pct'] * s['buyback_mult'] * 100
        _log(f'  卖出触发线: ¥{s["sell_trigger"]:.2f} (开盘+ATR×{s["sell_mult_used"]})')
        _log(f'    冲高→回落 {PULLBACK_PCT*100:.2f}%(≈¥{pullback_yuan:.2f}) → 卖出')
        _log(f'  卖出后 跌≈{est_buyback_pct:.2f}%(ATR%×{s["buyback_mult"]}) → 进入买回监控')
        _log(f'    回升 {s["bounce_pct"]*100:.2f}% → 确认买回')
        _log(f'  止损线: 卖价+{STOP_LOSS_PCT*100:.1f}% | 紧急买回: 卖价+{EMERGENCY_BUYBACK_PCT*100:.1f}%')
    else:
        reason = s['blocked_reason']
        if reason == '牛市禁反T':
            st = ContextInfo.st
            if st['entry_price'] > 0:
                up = (s['close_yday'] / st['entry_price'] - 1) * 100
                _log(f'  反T: ✗ 牛市禁反T (持仓+{up:.0f}% — 持有待涨是对的!)')
            else:
                _log(f'  反T: ✗ 牛市禁反T')
        else:
            _log(f'  反T: ✗ ({reason})')


# ============================================================================
# 第五部分：ontimer — 状态机驱动
# ============================================================================

def ontimer(ContextInfo):
    st = ContextInfo.st
    signal = st.get('daily_signal')
    if signal is None or not signal.get('do_short'):
        return

    now = _now()
    if not _is_market_open(now):
        return

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return

    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return
    if STOCK_QMT not in tick:
        return

    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0:
        return

    fstate = st['fstate']

    # 状态路由
    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price)

    # 尾盘强制买回
    if now >= FORCE_CLOSE_TIME:
        if fstate in (STATE_SOLD, STATE_DIPPING):
            elapsed = _elapsed(st.get('state_enter_time', ''), now)
            _log(f'[尾盘] {now} 强制买回 | 已等待{elapsed}')
            _force_buyback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_IDLE):
            st['fstate'] = STATE_DONE

    # 止损
    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st['day_pnl'] < -loss_limit:
            _log(f'[止损] 亏损 ¥{st["day_pnl"]:.0f} > ¥{loss_limit:.0f}({STOP_LOSS_PCT*100:.1f}%) 强制买回')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 第六部分：状态处理（v5: 买回逻辑重写）
# ============================================================================

def _handle_idle(ContextInfo, price):
    st = ContextInfo.st
    s  = st['daily_signal']
    trigger = s['sell_trigger']

    if price >= trigger:
        over_pct = (price - trigger) / trigger * 100
        st['fstate']     = STATE_SPIKING
        st['peak_price'] = price
        st['state_enter_time'] = _now()
        _log(f'[冲高] 🔍 ¥{price:.2f} 越过 ¥{trigger:.2f}(+{over_pct:.2f}%) → 进入冲高监控')
        _log(f'  peak=¥{price:.2f} | 回落线=¥{price*(1-PULLBACK_PCT):.2f}(-{PULLBACK_PCT*100:.2f}%) | 需回落¥{price*PULLBACK_PCT:.2f}')


def _handle_spiking(ContextInfo, price):
    st = ContextInfo.st
    s  = st['daily_signal']
    trigger = s['sell_trigger']

    # 更新最高价
    if price > st['peak_price']:
        old_peak = st['peak_price']
        st['peak_price'] = price
        _log(f'  [新高] ¥{old_peak:.2f} → ¥{price:.2f}(+{(price-old_peak)/old_peak*100:.2f}%)')

    peak = st['peak_price']
    pullback = (peak - price) / peak

    # ★ 回落确认 → 卖出
    if pullback >= PULLBACK_PCT:
        _log(f'[卖出] ✅ 冲高回落确认!')
        _log(f'  最高 ¥{peak:.2f} → 现价 ¥{price:.2f} (回落 {pullback*100:.2f}% = ¥{peak-price:.2f})')
        _mini_sell(ContextInfo, price)
        st['fstate']          = STATE_SOLD
        st['sell_fill_price'] = price
        st['state_enter_time'] = _now()

        # ★★★ v5核心: 基于实际卖出价动态计算买回触发线 ★★★
        atr_pct = s['atr_pct']
        buyback_pct = atr_pct * s['buyback_mult']              # 动态回撤比例
        buyback_target = round(price * (1.0 - buyback_pct), 2) # 买回触发线
        st['buyback_target']     = buyback_target
        st['buyback_target_pct'] = buyback_pct * 100

        # 详细日志
        _log(f'  ┌─ 动态买回参数 ─────────────────────')
        _log(f'  │ 卖出成交价: ¥{price:.2f}')
        _log(f'  │ ATR%={atr_pct*100:.2f}% × 乘数{s["buyback_mult"]} = 回撤阈值{buyback_pct*100:.2f}%')
        _log(f'  │ 买回触发线: ¥{buyback_target:.2f} (比卖价跌¥{price-buyback_target:.2f})')
        _log(f'  │ 回升确认线: 最低点 +{s["bounce_pct"]*100:.2f}% → 执行买回')
        _log(f'  │ 紧急买回线: ¥{round(price*(1+EMERGENCY_BUYBACK_PCT),2):.2f} (卖价+{EMERGENCY_BUYBACK_PCT*100:.1f}%)')
        _log(f'  │ 止损线:     ¥{round(price*(1+STOP_LOSS_PCT),2):.2f} (卖价+{STOP_LOSS_PCT*100:.1f}%)')
        _log(f'  │ 尾盘兜底:   {FORCE_CLOSE_TIME} (不管盈亏强制买回)')
        _log(f'  └─────────────────────────────────────')

    # 假突破 → 回退
    elif price < trigger:
        _log(f'[假突破] ¥{price:.2f} 跌回触发线下 ¥{trigger:.2f} | 最高触及 ¥{peak:.2f}')
        st['fstate']     = STATE_IDLE
        st['peak_price'] = 0.0
        st['state_enter_time'] = _now()


def _handle_sold(ContextInfo, price):
    """
    ★ v5 重写: SOLD状态的买回判断

    三个触发条件(优先级递减):
      1. 紧急买回: price ≥ sell_price × (1 + EMERGENCY_BUYBACK_PCT)
      2. 进入买回监控: price ≤ buyback_target (基于实际卖出价动态计算)
      3. 止损: 亏损超限 → 强制买回(在ontimer中统一检查)
    """
    st = ContextInfo.st
    sp = st['sell_fill_price']
    bt = st['buyback_target']

    # 条件1: 紧急买回
    emergency_line = sp * (1.0 + EMERGENCY_BUYBACK_PCT)
    if price >= emergency_line:
        rise_pct = (price - sp) / sp * 100
        loss_est = (sp - price) * TRADE_LOT_SIZE
        _log(f'[紧急买回] 🔴 卖飞防护触发!')
        _log(f'  卖价 ¥{sp:.2f} → 现价 ¥{price:.2f}(+{rise_pct:.2f}%) > 紧急线 ¥{emergency_line:.2f}')
        _log(f'  估算损失: ¥{loss_est:.0f} | 立即买回止损!')
        _mini_buyback(ContextInfo, price, '紧急')
        return

    # ★ 条件2: 跌到买回触发线 → 进入下跌监控
    if price <= bt:
        drop_pct = (sp - price) / sp * 100
        elapsed = _elapsed(st.get('state_enter_time', ''), _now())
        st['fstate']    = STATE_DIPPING
        st['dip_price'] = price
        st['state_enter_time'] = _now()
        _log(f'[买回触发] 🔍 已跌到买回监控线! (卖出后{elapsed})')
        _log(f'  卖价 ¥{sp:.2f} → 现价 ¥{price:.2f} (-{drop_pct:.2f}%) ≤ 触发线 ¥{bt:.2f}')
        _log(f'  dip=¥{price:.2f} | 回升线=¥{price*(1+BOUNCE_PCT):.2f}(+{BOUNCE_PCT*100:.2f}%) | 需回升¥{price*BOUNCE_PCT:.2f}')


def _handle_dipping(ContextInfo, price):
    """
    ★ v5: DIPPING状态 — 等回升确认

    三个可能:
      1. 价格继续跌 → 更新最低价
      2. 从最低点回升 ≥ BOUNCE_PCT → 买回!
      3. 价格涨回买回触发线之上 → 假跌破
    """
    st = ContextInfo.st
    bt = st['buyback_target']
    sp = st['sell_fill_price']

    # 更新最低价
    if price < st['dip_price']:
        old_dip = st['dip_price']
        st['dip_price'] = price
        _log(f'  [新低] ¥{old_dip:.2f} → ¥{price:.2f}(-{(old_dip-price)/old_dip*100:.2f}%)')

    dip = st['dip_price']
    bounce = (price - dip) / dip

    # ★ 回升确认 → 买回
    if bounce >= BOUNCE_PCT:
        sell_p = st['sell_fill_price']
        gross = (sell_p - price) * TRADE_LOT_SIZE
        net_est = gross - 70  # 扣交易成本(约70元)
        elapsed = _elapsed(st.get('state_enter_time', ''), _now())
        drop_from_sell = (sell_p - price) / sell_p * 100

        _log(f'[买回] ✅ 下跌回升确认!')
        _log(f'  最低 ¥{dip:.2f} → 现价 ¥{price:.2f} (回升 {bounce*100:.2f}% = ¥{price-dip:.2f})')
        _log(f'  卖价 ¥{sell_p:.2f} → 买价 ¥{price:.2f} (跌 {drop_from_sell:.2f}% = ¥{sell_p-price:.2f}/股)')
        _log(f'  毛利: ({sell_p:.2f}-{price:.2f})×{TRADE_LOT_SIZE}股 = ¥{gross:.0f}')
        _log(f'  净利≈¥{net_est:.0f}(扣佣¥70) | 持仓{elapsed}')
        _log(f'  当日累计PnL≈¥{st["day_pnl"]:.0f}')

        _mini_buyback(ContextInfo, price, '正常')
        st['total_t_days'] += 1
        st['total_pnl']    += gross

    # 假跌破 → 回退
    elif price > bt:
        _log(f'[假跌破] ¥{price:.2f} 涨回触发线上 ¥{bt:.2f} | 最低触及 ¥{dip:.2f}')
        st['fstate']    = STATE_SOLD
        st['dip_price'] = 0.0
        st['state_enter_time'] = _now()


# ============================================================================
# 第七部分：下单 & 平仓
# ============================================================================

def _mini_sell(ContextInfo, price):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 下单卖出: ¥{price:.2f} × {TRADE_LOT_SIZE}股 = ¥{price*TRADE_LOT_SIZE:,.0f}')
    except Exception as e:
        _log(f'  >>> 卖出失败: {e}')
        st['fstate'] = STATE_IDLE


def _mini_buyback(ContextInfo, price, reason=''):
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)
    if avail < need:
        _log(f'  >>> 买回失败: 资金不足 需¥{need:,.0f} > 可用¥{avail:,.0f}(缺¥{need-avail:,.0f})')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 下单买回({reason}): ¥{price:.2f} × {TRADE_LOT_SIZE}股 = ¥{price*TRADE_LOT_SIZE:,.0f}')
        st['fstate'] = STATE_DONE
    except Exception as e:
        _log(f'  >>> 买回失败({reason}): {e}')


def _force_buyback(ContextInfo):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[尾盘买回] ✓ 对手价 × {TRADE_LOT_SIZE}股 — 底仓已恢复')
    except Exception as e:
        _log(f'[尾盘失败!!] {e} — 请手动买回{TRADE_LOT_SIZE}股!')
        st['fstate'] = STATE_FORCED


def _cash(ContextInfo):
    try:
        a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception:
        return 0.0


def _acc(ContextInfo):
    return ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else ACCOUNT


# ============================================================================
# 第八部分：回调 & 工具
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50:'已报', 52:'部成', 53:'全成', 54:'部撤', 55:'已撤', 56:'废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    d = '买入' if deal.m_nDirection == 1 else '卖出'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    st['day_pnl'] += (amt - fee) if deal.m_nDirection == 2 else -(amt + fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f} × {deal.m_nVolume}股 = ¥{amt:,.0f} | 佣金¥{fee:.2f} | PnL≈¥{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'\n{"="*55}')
        _log(f'  {STOCK_NAME} v5.0 策略已停止')
        _log(f'  累计反T: {st.get("total_t_days", 0)}天 | 盈亏: ¥{st.get("total_pnl", 0):,.0f}')
        if st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
            _log(f'  ⚠⚠ 未买回{TRADE_LOT_SIZE}股! 请手动补仓!')
        _log(f'{"="*55}')


def _now():
    import time as _t
    return _t.strftime('%H:%M:%S')


def _ts():
    import time as _t
    return _t.strftime('[%H:%M:%S]')


def _elapsed(start, end):
    """计算两个时间字符串之间的间隔(美化格式)"""
    if not start:
        return '?'
    try:
        h1, m1, s1 = map(int, start.split(':'))
        h2, m2, s2 = map(int, end.split(':'))
        sec = (h2 - h1) * 3600 + (m2 - m1) * 60 + (s2 - s1)
        if sec < 0:
            sec += 86400
        if sec < 60:
            return f'{sec}秒'
        elif sec < 3600:
            return f'{sec//60}分{sec%60}秒'
        else:
            return f'{sec//3600}时{(sec%3600)//60}分'
    except Exception:
        return '?'


def _log(*args, **kwargs):
    ts = _ts()
    if args:
        print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
