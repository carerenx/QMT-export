# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你反T策略 v3.0 — 高价股适配版（股价 400+）
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()
 ================================================================================

 【v3.0 相对于 v2.0 的调整 — 针对464元高价股】
  ┌──────────────────────┬─────────────────┬─────────────────┐
  │ 参数                 │ v2.0(30元股)    │ v3.0(464元股)   │ 调整原因                │
  ├──────────────────────┼─────────────────┼─────────────────┼──────────────────────────┤
  │ PULLBACK_PCT(回落)   │ 0.30% ≈0.09元   │ 0.12% ≈0.56元   │ 高价股百分比要更小       │
  │ BOUNCE_PCT(回升)     │ 0.30% ≈0.09元   │ 0.12% ≈0.56元   │ 否则确认太迟钝           │
  │ EMERGENCY_BUYBACK    │ 1.50% ≈0.45元   │ 1.00% ≈4.64元   │ 1.5%损失太大             │
  │ STOP_LOSS_PCT        │ 2.0%            │ 1.5%            │ 稍收紧                  │
  │ 检测周期              │ 3秒             │ 3秒             │ 不变(高价股同样适合)     │
  │ SELL_TRIGGER_MULT     │ 1.0×ATR         │ 1.0×ATR         │ ATR百分比自适应,不变     │
  └──────────────────────┴─────────────────┴─────────────────┘

 【高价股交易成本重新计算（464元/股, 1手=100股=46,400元）】
   买入佣金: 46,400 × 0.025% = 11.6元
   卖出佣金: 46,400 × 0.025% = 11.6元
   印花税:   46,400 × 0.100% = 46.4元
   往返合计: ≈70元 (占交易金额0.15%)

   日均ATR ≈ 464 × 3% = 13.9元
   做T理论收益: 13.9 × 100股 = 1,390元(毛利)
   扣除成本: 1,390 - 70 = 1,320元(净利)

   → 高价股做T: 成本占比低(0.15%), 绝对收益高, 非常值得做

 【实盘注意事项】
   - 1手 = 46,400元, 确保卖出后现金足够买回
   - 高价股流动性可能相对差, 注意bid-ask spread
   - 建议在流动性好的时段操作(开盘30分钟、收盘前30分钟)

 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置（已针对464元股价优化）
# ============================================================================
ACCOUNT = '8890145315'                     # QMT账户名
# ---------- 标的 ----------
STOCK_CODE   = '601869'
STOCK_NAME   = '长飞光纤'
STOCK_QMT    = f'{STOCK_CODE}.SH'

# ---------- 仓位 ----------
TRADE_LOT_SIZE = 100                  # 每次做T 1手
MIN_LOT        = 100

# ---------- 检测周期 ----------
# QMT run_time支持: '1nSecond'~'60nSecond', '500nMilliSecond', 等
# 3秒对日内做T是很好的平衡 — 不会漏掉关键价位变化, 也不会过度消耗CPU
TIMER_INTERVAL = '1nSecond'           # 每3秒检测一次

# ---------- 反T触发线（ATR自适应, 无需针对股价调） ----------
ATR_PERIOD          = 14
SELL_TRIGGER_MULT   = 1.0             # 卖出触发线 = 开盘 + ATR×此值
BUYBACK_TRIGGER_MULT = 1.0            # 买回触发线 = 卖出价 × (1 - ATR%×此值)

# ---------- ★ 冲高回落 & 下跌回升 — 针对464元高价股优化 ★ ----------
# 核心原则: 高价股用更小的百分比, 因为绝对值已经足够大
#
# 464元 × 0.12% = 0.56元 (确认幅度)
#   vs 30元 × 0.30% = 0.09元
# → 绝对值0.56元 vs 0.09元, 前者实际空间更大, 所以百分比可以更小
#
# 调优指南:
#   股价100~200: PULLBACK≈0.15~0.25%
#   股价200~400: PULLBACK≈0.10~0.15%
#   股价400+:    PULLBACK≈0.08~0.12%
PULLBACK_PCT = 0.0012                 # 0.12% (464×0.12%=0.56元) — 回落确认
BOUNCE_PCT   = 0.0012                 # 0.12% — 回升确认

# ---------- 熔断 & 过滤 ----------
VOLUME_FILTER_RATIO = 0.5             # 量比<0.5不操作
RSI_OVERBOUGHT      = 75              # RSI>75不操作

# ---------- 紧急买回 & 止损 ----------
# 464 × 1.0% = 4.64元, 作为卖飞容忍上限是合理的
EMERGENCY_BUYBACK_PCT = 0.02          # 1.0% (vs v2.0的1.5%)
# 464 × 1.5% × 100股 = 696元, 作为单日亏损上限
STOP_LOSS_PCT         = 0.015         # 1.5% (vs v2.0的2.0%)

# ---------- 时间 ----------
FORCE_CLOSE_TIME = '14:57:00'         # 尾盘强制买回

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
    """计算当日反T信号（与v2.0相同逻辑, ATR已自适应股价）"""
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
    do_short, reason = True, ''
    if trend == 'bull':
        do_short, reason = False, '牛市禁反T'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short, reason = False, f'缩量(量比{curr_vr:.2f})'
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short, reason = False, f'RSI超买({curr_rsi:.0f})'

    sell_trigger    = round(co + curr_atr * SELL_TRIGGER_MULT, 2)
    buyback_trigger = round(sell_trigger * (1.0 - curr_atr_pct * BUYBACK_TRIGGER_MULT), 2)

    # 价差检查（464元股的0.15%≈0.70元, 刚好覆盖70元往返成本）
    spread = (sell_trigger - buyback_trigger) / buyback_trigger * 100 if buyback_trigger > 0 else 0
    if do_short and spread < 0.15:  # v3.0: 降低了最小价差阈值
        do_short, reason = False, f'价差不足({spread:.2f}% < 0.15%)'

    return {
        'do_short':        do_short,
        'blocked_reason':  reason,
        'trend':           trend,
        'sell_trigger':    sell_trigger,       # 越过此线→进入冲高监控
        'buyback_trigger': buyback_trigger,    # 跌破此线→进入下跌监控
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

# 状态常量
STATE_IDLE    = 'IDLE'     # 等待触发
STATE_SPIKING = 'SPIKING'  # 冲高监控: 价格越过卖出触发线, 等回落确认
STATE_DIPPING = 'DIPPING'  # 下跌监控: 价格跌破买回触发线, 等回升确认
STATE_SOLD    = 'SOLD'     # 已卖出, 等买回时机
STATE_DONE    = 'DONE'     # 今日完成
STATE_FORCED  = 'FORCED'   # 强制平仓


def init(ContextInfo):
    """策略初始化"""
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account(ACCOUNT)

    state = {
        'daily_signal':     None,
        'base_shares':      0,
        'base_cost':        0.0,
        # 状态机
        'fstate':           STATE_IDLE,
        'peak_price':       0.0,
        'dip_price':        0.0,
        'sell_fill_price':  0.0,
        # 风控
        'day_pnl':          0.0,
        'stop_loss_hit':    False,
    }
    ContextInfo.st = state

    # 启动定时器 — 检测周期3秒
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")

    _log(f'\n{"="*60}')
    _log(f'  QMT 迷你反T策略 v3.0 — 高价股适配版')
    _log(f'  标的: {STOCK_NAME}({STOCK_CODE})')
    _log(f'  检测周期: {TIMER_INTERVAL} (每3秒)')
    _log(f'  每次交易: {TRADE_LOT_SIZE}股 × ≈464元 = ≈46,400元')
    _log(f'  卖出触发: 开盘 + ATR×{SELL_TRIGGER_MULT} → 冲高回落{PULLBACK_PCT*100:.2f}%卖出')
    _log(f'  买回触发: 卖价 × (1-ATR%×{BUYBACK_TRIGGER_MULT}) → 下跌回升{BOUNCE_PCT*100:.2f}%买回')
    _log(f'  紧急买回: +{EMERGENCY_BUYBACK_PCT*100:.1f}% | 止损: {STOP_LOSS_PCT*100:.1f}%')
    _log(f'  尾盘: {FORCE_CLOSE_TIME}强制买回')
    _log(f'  往返成本≈70元(0.15%), 价差≥0.15%即可盈利')
    _log(f'{"="*60}\n')


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

    # 底仓
    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    if base_shares < TRADE_LOT_SIZE:
        _log(f'[警告] 底仓不足1手({base_shares}股)')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    # 账户简报
    acct = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
    if acct:
        avail = acct[0].m_dAvailable
        pos_val = base_shares * closes[STOCK_QMT][-1]
        _log(f'[账户] {base_shares}股 × ¥{closes[STOCK_QMT][-1]:.2f} '
              f'= ¥{pos_val:,.0f} | 现金¥{avail:,.0f} | '
              f'做T1手需¥{closes[STOCK_QMT][-1]*TRADE_LOT_SIZE:,.0f}')

    # 信号计算
    signal = compute_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    st['daily_signal'] = signal

    # 新交易日重置
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
               STATE_SOLD:'已卖出', STATE_DONE:'完成', STATE_FORCED:'强制平仓'}
    _log(f'\n--- {STOCK_NAME} v3.0 反T信号 ---')
    _log(f'  状态: {st_name[ContextInfo.st["fstate"]]}')
    _log(f'  开盘={s["open_price"]:.2f}  ATR={s["atr"]:.2f}元({s["atr_pct"]*100:.2f}%)')
    _log(f'  趋势={s["trend"]}  RSI={s["rsi"]:.1f}  量比={s["vol_ratio"]:.2f}')
    if s['do_short']:
        _log(f'  ✓ 允许反T | 预期价差{s["spread_pct"]:.2f}%')
        _log(f'  卖出触发线: {s["sell_trigger"]:.2f} (越过→冲高→回落{PULLBACK_PCT*100:.2f}%={s["sell_trigger"]*PULLBACK_PCT:.2f}元→卖出)')
        _log(f'  买回触发线: {s["buyback_trigger"]:.2f} (跌破→下跌→回升{BOUNCE_PCT*100:.2f}%={s["buyback_trigger"]*BOUNCE_PCT:.2f}元→买回)')
    else:
        _log(f'  ✗ 禁止 ({s["blocked_reason"]})')
    _log()


# ============================================================================
# 第五部分：ontimer — 状态机驱动
# ============================================================================

def ontimer(ContextInfo):
    """定时器 — 每3秒检测一次"""
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

    fstate  = st['fstate']
    trig_s  = signal['sell_trigger']
    trig_b  = signal['buyback_trigger']

    # ---- 状态路由 ----
    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price, trig_s)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price, trig_s)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price, trig_b)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price, trig_b)

    # ---- 全局安全检查 ----
    if now >= FORCE_CLOSE_TIME:
        if fstate in (STATE_SOLD, STATE_DIPPING):
            _force_buyback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_IDLE):
            st['fstate'] = STATE_DONE  # 今天没机会了

    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st['day_pnl'] < -loss_limit:
            _log(f'[止损] 亏损{st["day_pnl"]:.0f}触发止损({STOP_LOSS_PCT*100:.1f}%)')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 第六部分：状态处理
# ============================================================================

def _handle_idle(ContextInfo, price, sell_trigger):
    """IDLE → SPIKING: 价格越过卖出触发线"""
    st = ContextInfo.st
    if price >= sell_trigger:
        st['fstate']     = STATE_SPIKING
        st['peak_price'] = price
        _log(f'[冲高]  {price:.2f}越过{sell_trigger:.2f} → 等回落{PULLBACK_PCT*100:.2f}%')


def _handle_spiking(ContextInfo, price, sell_trigger):
    """SPIKING: 监控回落 → 确认卖出 或 假突破回退"""
    st = ContextInfo.st
    if price > st['peak_price']:
        st['peak_price'] = price

    peak = st['peak_price']
    pullback = (peak - price) / peak

    # 回落确认 → 卖出
    if pullback >= PULLBACK_PCT:
        _log(f'[卖出] ✅ 最高{peak:.2f}回落{pullback*100:.2f}%至{price:.2f} → 执行!')
        _mini_sell(ContextInfo, price)
        st['fstate']          = STATE_SOLD
        st['sell_fill_price'] = price
        # 动态更新买回触发线
        s = st['daily_signal']
        s['buyback_trigger'] = round(price * (1.0 - s['atr_pct'] * BUYBACK_TRIGGER_MULT), 2)
        _log(f'  买回触发线→{s["buyback_trigger"]:.2f}')

    # 假突破 → 回退
    elif price < sell_trigger:
        _log(f'[假突破] {price:.2f}跌回{sell_trigger:.2f}下 → 撤销')
        st['fstate']     = STATE_IDLE
        st['peak_price'] = 0.0


def _handle_sold(ContextInfo, price, buyback_trigger):
    """SOLD: 等买回时机 或 紧急买回"""
    st = ContextInfo.st
    sp = st['sell_fill_price']

    # 紧急买回（优先）
    if price >= sp * (1.0 + EMERGENCY_BUYBACK_PCT):
        _log(f'[紧急] 🔴 卖{sp:.2f} → 现{price:.2f}(+{(price-sp)/sp*100:.2f}%) 买回!')
        _mini_buyback(ContextInfo, price)
        return

    # 进入下跌监控
    if price <= buyback_trigger:
        st['fstate']    = STATE_DIPPING
        st['dip_price'] = price
        _log(f'[下跌] {price:.2f}跌破{buyback_trigger:.2f} → 等回升{BOUNCE_PCT*100:.2f}%')


def _handle_dipping(ContextInfo, price, buyback_trigger):
    """DIPPING: 监控回升 → 确认买回 或 假跌破回退"""
    st = ContextInfo.st
    if price < st['dip_price']:
        st['dip_price'] = price

    dip = st['dip_price']
    bounce = (price - dip) / dip

    # 回升确认 → 买回
    if bounce >= BOUNCE_PCT:
        _log(f'[买回] ✅ 最低{dip:.2f}回升{bounce*100:.2f}%至{price:.2f} → 执行!')
        _mini_buyback(ContextInfo, price)
        gross = (st['sell_fill_price'] - price) * TRADE_LOT_SIZE
        _log(f'  卖{st["sell_fill_price"]:.2f} 买{price:.2f} 毛利≈{gross:.0f}元 净利≈{gross-70:.0f}元')

    # 假跌破 → 回退
    elif price > buyback_trigger:
        _log(f'[假跌破] {price:.2f}涨回{buyback_trigger:.2f}上 → 撤销')
        st['fstate']    = STATE_SOLD
        st['dip_price'] = 0.0


# ============================================================================
# 第七部分：下单 & 平仓
# ============================================================================

def _mini_sell(ContextInfo, price):
    """卖出1手"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  [卖出] {price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  [卖出失败] {e}')
        st['fstate'] = STATE_IDLE


def _mini_buyback(ContextInfo, price):
    """买回1手"""
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    if _cash(ContextInfo) < need:
        _log(f'  [买回失败] 资金不足: 需{need:,.0f} | 可用{_cash(ContextInfo):,.0f}')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  [买回] {price:.2f} × {TRADE_LOT_SIZE}股')
        st['fstate'] = STATE_DONE
    except Exception as e:
        _log(f'  [买回失败] {e}')


def _force_buyback(ContextInfo):
    """尾盘强制买回"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[尾盘] 对手价买回{TRADE_LOT_SIZE}股 — 底仓恢复')
    except Exception as e:
        _log(f'[尾盘失败!!] {e} — 请手动买回!')
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
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} '
              f'{order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    d = '买入' if deal.m_nDirection == 1 else '卖出'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    st['day_pnl'] += (amt - fee) if deal.m_nDirection == 2 else -(amt + fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f} × {deal.m_nVolume}股 PnL≈{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        fs = st.get('fstate', '?')
        _log(f'\n[{STOCK_NAME}] v3.0 已停止 | 终态={fs}')
        if fs in (STATE_SOLD, STATE_DIPPING):
            _log(f'  ⚠⚠ 未买回{TRADE_LOT_SIZE}股! 请手动补仓!')


def _now():
    import time as _t
    return _t.strftime('%H:%M:%S')


def _ts():
    """返回当前时间戳 [HH:MM:SS]"""
    import time as _t
    return _t.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    """带时间戳的print — 所有日志统一入口"""
    ts = _ts()
    if args:
        print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
