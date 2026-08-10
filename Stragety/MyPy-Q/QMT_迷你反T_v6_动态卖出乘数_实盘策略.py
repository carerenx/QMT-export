# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你反T策略 v6.0 — 动态卖出乘数版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()
 ================================================================================

 【v6.0 核心改动 — SELL_TRIGGER_MULT 从固定值变为5因子动态模型】

  v4/v5 做法:
   SELL_TRIGGER_MULT = 1.0
   if ATR% > 7%: MULT = 0.5     ← 只有一维(ATR水平)
   elif ATR% > 5%: MULT = 0.7
   else: MULT = 1.0

  v6 做法:
   SELL_TRIGGER_MULT = f(趋势, ATR水平, 波动率变化, 成交量, RSI)
                      = 基准1.0 + 5个修正项 → clamp到[0.3, 2.0]

   每个因子贡献一个修正量(可正可负), 最终累加到基准值上:

   ┌──────────────┬──────────────────────┬──────────────────────────────┐
   │ 因子          │ 信号                  │ 修正量(示例)                  │
   ├──────────────┼──────────────────────┼──────────────────────────────┤
   │ 趋势强度      │ 量化的趋势方向和力度   │ 牛市+0.30 / 强熊-0.40         │
   │ ATR水平       │ ATR%相对20日均值      │ ATR扩大→-0.20 / 收缩→+0.25   │
   │ ATR变化方向   │ 当前ATR vs 20日均ATR  │ 扩大中→-0.15 / 收缩中→+0.20   │
   │ 成交量        │ 量比                  │ 放量→-0.20 / 缩量→+0.25       │
   │ RSI           │ 超买超卖程度          │ 超买→-0.25 / 超卖→+0.30       │
   ├──────────────┼──────────────────────┼──────────────────────────────┤
   │ 最终乘数      │ clamp(1.0+Σ修正,     │                              │
   │              │       0.30, 2.00)     │                              │
   └──────────────┴──────────────────────┴──────────────────────────────┘

   为什么乘数越低=越激进(更早卖出)?
     SELL_TRIGGER = 开盘价 + ATR × MULT
     MULT=0.5 → 开盘+ATR×0.5 → 涨3.5%(ATR=7%)就触发 → 激进,不放过小反弹
     MULT=1.5 → 开盘+ATR×1.5 → 涨10.5%才触发 → 保守,只做确定性大机会

   什么时候该激进(低乘数)?
     - 熊市/下跌趋势 → 抓紧每个反弹卖出
     - ATR扩大中 → 波动在加剧,快进快出
     - 放量 → 流动性好,容易成交
     - RSI超买 → 随时可能回调,赶紧卖

   什么时候该保守(高乘数)?
     - 牛市/上涨趋势 → 耐心等大冲高,别卖太早
     - ATR收缩中 → 波动在减小,交易成本占比高
     - 缩量 → 流动性差,需更大价差覆盖滑点
     - RSI超卖 → 可能继续反弹,等更高的卖点
 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置
# ============================================================================
ACCOUNT = '8890145315'

STOCK_CODE      = '601869'
STOCK_NAME      = '长飞光纤'
STOCK_QMT       = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE  = 100
MIN_LOT         = 100
TIMER_INTERVAL  = '1nSecond'

# ---- 卖出触发线 ----
ATR_PERIOD           = 14
SELL_TRIGGER_BASE    = 1.0              # ★ v6: 重命名, 这是基准乘数(不再是最终值)
# ★ v6: 动态乘数范围
DYNAMIC_MULT_MIN     = 0.30             # 最激进(ATR↑+熊市+放量+超买 → 0.3)
DYNAMIC_MULT_MAX     = 2.00             # 最保守(ATR↓+牛市+缩量+超卖 → 2.0)

# ---- 冲高回落确认 ----
PULLBACK_PCT = 0.0010

# ---- 买回触发(动态, 基于卖出价) ----
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT = 0.0010

# ---- 熔断 & 过滤 ----
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75

# ---- 紧急买回 & 止损 ----
EMERGENCY_BUYBACK_PCT = 0.02
STOP_LOSS_PCT         = 0.015

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


def _up_streak(closes):
    """连涨天数"""
    n = len(closes)
    streak = [0] * n
    for i in range(1, n):
        streak[i] = streak[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return streak


# ============================================================================
# 第三部分：★ v6 核心 — 5因子动态乘数模型 ★
# ============================================================================

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    """
    ★ v6 核心引擎: 根据5个市场因子动态计算 SELL_TRIGGER_MULT

    设计原则:
      - 每个因子独立贡献一个修正量(deviation), 范围[-0.5, +0.5]
      - 修正量相加 → 最终乘数 = clamp(基准 + Σ修正, 0.3, 2.0)
      - 正修正 = 增加乘数(更保守, 等更大涨幅才卖)
      - 负修正 = 减少乘数(更激进, 小反弹就卖)

    参数:
      trend:      'bull' | 'bear' | 'sideways'
      atr_pct:    当前ATR%(如0.07=7%)
      atr_ratio:  当前ATR/20日均ATR (>1=波动扩大中, <1=收缩中)
      vol_ratio:  量比(当前成交量/20日均量)
      rsi_val:    RSI值(0-100)
      up_streak:  连涨天数

    返回:
      (final_mult, factors_detail_dict)
    """
    base = SELL_TRIGGER_BASE  # 1.0
    deviations = {}
    deviation_total = 0.0

    # ─── 因子1: 趋势强度 (±0.40) ───
    # 熊市→激进(跌势中抓紧每个反弹卖), 牛市→保守(耐心等大冲高)
    if trend == 'bear':
        # 区分温和熊市 vs 剧烈熊市
        if up_streak == 0:
            d = -0.40   # 连跌中 → 最激进, 小反弹就卖
        else:
            d = -0.25   # 熊市但今天涨了 → 适度激进
    elif trend == 'bull':
        # 区分温和牛市 vs 强势牛市
        if up_streak >= 3:
            d = +0.40   # 连涨强势 → 最保守, 别卖飞
        elif up_streak >= 1:
            d = +0.25   # 温和牛市
        else:
            d = +0.10   # 牛市但今天调整 → 微保守
    else:  # sideways
        d = 0.00
    deviations['趋势'] = d
    deviation_total += d

    # ─── 因子2: ATR绝对水平 (±0.30) ───
    # ATR越高 → 波动越大 → 更有机会 → 更激进(低乘数)
    if atr_pct > 0.08:
        d = -0.30       # ATR>8% 极端波动 → 快速出手
    elif atr_pct > 0.07:
        d = -0.25
    elif atr_pct > 0.06:
        d = -0.18
    elif atr_pct > 0.05:
        d = -0.10
    elif atr_pct > 0.03:
        d = 0.00        # ATR 3-5% 正常水平
    elif atr_pct > 0.02:
        d = +0.15       # ATR较低 → 等大点的波动
    else:
        d = +0.25       # ATR<2% 极低波动 → 保守
    deviations['ATR水平'] = d
    deviation_total += d

    # ─── 因子3: ATR变化方向(波动率扩张/收缩) (±0.20) ───
    # ATR扩大中 → 波动在加剧 → 更激进
    # ATR收缩中 → 波动在收敛 → 更要等
    # atr_ratio = 当前ATR / 20日均ATR
    if atr_ratio > 1.40:
        d = -0.20       # 波动率急剧扩大
    elif atr_ratio > 1.20:
        d = -0.12       # 波动率明显扩大
    elif atr_ratio > 1.05:
        d = -0.05       # 微扩大
    elif atr_ratio > 0.95:
        d = 0.00        # 稳定
    elif atr_ratio > 0.80:
        d = +0.10       # 微收缩
    elif atr_ratio > 0.60:
        d = +0.18       # 明显收缩
    else:
        d = +0.25       # 急剧收缩 → 大保守
    deviations['波动率Δ'] = d
    deviation_total += d

    # ─── 因子4: 成交量(流动性) (±0.25) ───
    # 放量→流动性好→激进, 缩量→滑点大→保守
    if vol_ratio > 2.00:
        d = -0.25       # 巨量 → 流动性极好
    elif vol_ratio > 1.50:
        d = -0.18       # 明显放量
    elif vol_ratio > 1.20:
        d = -0.08       # 温和放量
    elif vol_ratio > 0.80:
        d = 0.00        # 正常
    elif vol_ratio > 0.60:
        d = +0.12       # 温和缩量
    elif vol_ratio > 0.40:
        d = +0.20       # 明显缩量
    else:
        d = +0.25       # 极致缩量
    deviations['成交量'] = d
    deviation_total += d

    # ─── 因子5: RSI(超买超卖) (±0.30) ───
    # 超买→随时可能回调→抓紧卖, 超卖→可能继续反弹→耐心等
    if rsi_val > 80:
        d = -0.30       # 极度超买 → 赶紧卖
    elif rsi_val > 70:
        d = -0.20
    elif rsi_val > 60:
        d = -0.08
    elif rsi_val > 40:
        d = 0.00        # 中性区
    elif rsi_val > 30:
        d = +0.12
    elif rsi_val > 20:
        d = +0.22
    else:
        d = +0.30       # 极度超卖 → 耐心等反弹
    deviations['RSI'] = d
    deviation_total += d

    # ─── 合成最终乘数 ───
    final_mult = base + deviation_total
    final_mult = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final_mult))
    final_mult = round(final_mult, 2)

    return final_mult, deviations


# ============================================================================
# 第四部分：信号计算
# ============================================================================

def compute_signal(opens, highs, lows, closes, volumes):
    """计算当日反T信号 — v6加入5因子动态乘数"""
    n = len(closes)
    if n < 60:
        return None

    co = opens[-1]
    cc = closes[-1]
    cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    # ★ v6: ATR变化方向 (当前ATR vs 20日均ATR)
    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]
    trend = 'bull' if (cc > ma20 and ma5 > ma20) else \
            ('bear' if (cc < ma20 and ma5 < ma20) else 'sideways')

    curr_rsi = _rsi(closes)[-1]
    up_streak = _up_streak(closes)[-1]

    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # ★★★ v6核心: 5因子动态乘数 ★★★
    sell_mult, factor_details = calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak
    )

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
        'do_short':          do_short,
        'blocked_reason':    reason,
        'trend':             trend,
        'sell_trigger':      sell_trigger,
        'open_price':        co,
        'close_yday':        cc,
        'atr':               curr_atr,
        'atr_pct':           curr_atr_pct,
        'rsi':               curr_rsi,
        'vol_ratio':         curr_vr,
        'sell_mult':         sell_mult,        # ★ v6: 最终动态乘数
        'sell_mult_base':    SELL_TRIGGER_BASE, # 基准乘数(参考)
        'factor_details':    factor_details,    # ★ v6: 各因子贡献明细
        'atr_ratio':         atr_ratio,         # ATR变化比
        'up_streak':         up_streak,
        'buyback_mult':      BUYBACK_TRIGGER_MULT,
        'bounce_pct':        BOUNCE_PCT,
    }


# ============================================================================
# 第五部分：QMT 策略入口
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
        'buyback_target':   0.0,
        'buyback_target_pct': 0.0,
        'day_pnl':          0.0,
        'stop_loss_hit':    False,
        'total_t_days':     0,
        'total_pnl':        0.0,
        'entry_price':      0.0,
        'startup_printed':  False,
        'state_enter_time': '',
    }
    ContextInfo.st = state
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


# ============================================================================
# 第六部分：handlebar — 日线触发
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
            _log(f'[警告] 底仓不足1手({base_shares}股)')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost
    if st['entry_price'] == 0.0:
        st['entry_price'] = base_cost

    curr_close = closes[STOCK_QMT][-1]
    avail_cash = accounts[0].m_dAvailable if accounts else 0.0
    pos_value  = base_shares * curr_close

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
        _print_signal(ContextInfo, curr_close, avail_cash, pos_value)
    else:
        if not st['startup_printed']:
            _log(f'{"="*60}')
            _log(f'  {STOCK_NAME} v6.0 动态卖出乘数版 — 已加载')
            _log(f'  现价 ¥{curr_close:.2f} | 基准乘数 {SELL_TRIGGER_BASE}')
            _log(f'  5因子模型: 趋势+ATR水平+ATRΔ+成交量+RSI')
            _log(f'  乘数范围 [{DYNAMIC_MULT_MIN}, {DYNAMIC_MULT_MAX}]')
            _log(f'{"="*60}')
            st['startup_printed'] = True


def _print_signal(ContextInfo, curr_close, avail_cash, pos_value):
    s = ContextInfo.st['daily_signal']
    st = ContextInfo.st
    cost = st['entry_price']
    unreal_pnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_close / cost - 1) * 100 if cost > 0 else 0
    total_val = pos_value + avail_cash

    _log(f'━━━ {"账户 & 信号":─^30} ━━━')
    _log(f'  持仓 ¥{pos_value:,.0f} | 浮动 ¥{unreal_pnl:,.0f}({unreal_pct:+.1f}%) | 总资产 ¥{total_val:,.0f}')
    _log(f'  开盘 ¥{s["open_price"]:.2f} | ATR ¥{s["atr"]:.2f}({s["atr_pct"]*100:.2f}%) | '
         f'ATRΔ {s["atr_ratio"]:.2f} | RSI {s["rsi"]:.1f} | 量比 {s["vol_ratio"]:.2f}')
    _log(f'  趋势 {s["trend"]}(连涨{s["up_streak"]}天)')

    if s['do_short']:
        # ★ v6: 展示动态乘数的各因子贡献
        _log(f'  ┌─ 动态乘数分解 (基准={s["sell_mult_base"]}) ─')
        fd = s['factor_details']
        bar_max = 15
        for name, dev in fd.items():
            if dev == 0:
                continue
            direction = '◀' if dev < 0 else '▶'
            bar_len = min(int(abs(dev) / 0.05), bar_max)
            bar = '█' * bar_len
            _log(f'  │ {name:<6} {dev:+.2f} {direction}{bar}')
        _log(f'  ├─ 最终乘数: {s["sell_mult"]:.2f}')
        _log(f'  │  触发线: {s["open_price"]:.2f} + {s["atr"]:.2f}×{s["sell_mult"]:.2f}'
             f' = ¥{s["sell_trigger"]:.2f}')
        _log(f'  │  卖出后 跌≈{s["atr_pct"]*s["buyback_mult"]*100:.2f}%'
             f'(ATR%×{s["buyback_mult"]}) → 买回监控')
        _log(f'  │  回升 +{s["bounce_pct"]*100:.2f}% → 买回')
        _log(f'  └{"─"*30}')
    else:
        reason = s['blocked_reason']
        if reason == '牛市禁反T':
            _log(f'  反T ✗ 牛市禁反T(持仓+{unreal_pct:.0f}% — 持有待涨)')
        else:
            _log(f'  反T ✗ ({reason})')

    # 正T可行性
    lot_cost = curr_close * TRADE_LOT_SIZE
    if avail_cash >= lot_cost * 1.01:
        _log(f'  正T ✓ 可用(1手需¥{lot_cost:,.0f})')
    else:
        _log(f'  正T ✗ (缺口¥{lot_cost-avail_cash:,.0f})')
    if st['total_t_days'] > 0:
        _log(f'  累计反T {st["total_t_days"]}天 盈亏¥{st["total_pnl"]:,.0f}')


# ============================================================================
# 第七部分：ontimer — 状态机驱动
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

    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price)

    if now >= FORCE_CLOSE_TIME:
        if fstate in (STATE_SOLD, STATE_DIPPING):
            _log(f'[尾盘] {now} 强制买回')
            _force_buyback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_IDLE):
            st['fstate'] = STATE_DONE

    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st['day_pnl'] < -loss_limit:
            _log(f'[止损] ¥{st["day_pnl"]:.0f} > ¥{loss_limit:.0f}')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 第八部分：状态处理（同v5）
# ============================================================================

def _handle_idle(ContextInfo, price):
    st = ContextInfo.st
    s  = st['daily_signal']
    trigger = s['sell_trigger']
    if price >= trigger:
        over_pct = (price - trigger) / trigger * 100
        st['fstate']            = STATE_SPIKING
        st['peak_price']        = price
        st['state_enter_time']  = _now()
        _log(f'[冲高] 🔍 ¥{price:.2f} ≥ ¥{trigger:.2f}(乘数{s["sell_mult"]}) → 等回落{PULLBACK_PCT*100:.2f}%')


def _handle_spiking(ContextInfo, price):
    st = ContextInfo.st
    s  = st['daily_signal']
    trigger = s['sell_trigger']
    if price > st['peak_price']:
        st['peak_price'] = price

    peak = st['peak_price']
    pullback = (peak - price) / peak
    if pullback >= PULLBACK_PCT:
        _log(f'[卖出] ✅ 最高¥{peak:.2f}回落{pullback*100:.2f}%→¥{price:.2f}')
        _mini_sell(ContextInfo, price)
        st['fstate']           = STATE_SOLD
        st['sell_fill_price']  = price
        st['state_enter_time'] = _now()

        atr_pct = s['atr_pct']
        buyback_pct   = atr_pct * s['buyback_mult']
        buyback_target = round(price * (1.0 - buyback_pct), 2)
        st['buyback_target']     = buyback_target
        st['buyback_target_pct'] = buyback_pct * 100
        _log(f'  买回线=¥{buyback_target:.2f}({buyback_pct*100:.2f}%) | 紧急=¥{price*(1+EMERGENCY_BUYBACK_PCT):.2f}')
    elif price < trigger:
        _log(f'[假突破] ¥{price:.2f} < ¥{trigger:.2f}')
        st['fstate']     = STATE_IDLE
        st['peak_price'] = 0.0


def _handle_sold(ContextInfo, price):
    st = ContextInfo.st
    sp = st['sell_fill_price']
    bt = st['buyback_target']
    if price >= sp * (1.0 + EMERGENCY_BUYBACK_PCT):
        _log(f'[紧急] 🔴 卖¥{sp:.2f}→现¥{price:.2f}(+{(price-sp)/sp*100:.2f}%)买回!')
        _mini_buyback(ContextInfo, price, '紧急')
        return
    if price <= bt:
        drop_pct = (sp - price) / sp * 100
        st['fstate']    = STATE_DIPPING
        st['dip_price'] = price
        st['state_enter_time'] = _now()
        _log(f'[买回触发] ¥{price:.2f}(-{drop_pct:.2f}%) ≤ ¥{bt:.2f}')


def _handle_dipping(ContextInfo, price):
    st = ContextInfo.st
    bt = st['buyback_target']
    if price < st['dip_price']:
        st['dip_price'] = price
    dip = st['dip_price']
    bounce = (price - dip) / dip
    if bounce >= BOUNCE_PCT:
        sell_p = st['sell_fill_price']
        gross = (sell_p - price) * TRADE_LOT_SIZE
        _log(f'[买回] ✅ 最低¥{dip:.2f}回升{bounce*100:.2f}%→¥{price:.2f} | 毛利≈¥{gross:.0f}')
        _mini_buyback(ContextInfo, price, '正常')
        st['total_t_days'] += 1
        st['total_pnl']    += gross
    elif price > bt:
        _log(f'[假跌破] ¥{price:.2f} > ¥{bt:.2f}')
        st['fstate']    = STATE_SOLD
        st['dip_price'] = 0.0


# ============================================================================
# 第九部分：下单 & 平仓
# ============================================================================

def _mini_sell(ContextInfo, price):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 卖出 ¥{price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  >>> 卖出失败: {e}')
        st['fstate'] = STATE_IDLE


def _mini_buyback(ContextInfo, price, reason=''):
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    if _cash(ContextInfo) < need:
        _log(f'  >>> 买回失败: 资金不足(需¥{need:,.0f})')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 买回({reason}) ¥{price:.2f} × {TRADE_LOT_SIZE}股')
        st['fstate'] = STATE_DONE
    except Exception as e:
        _log(f'  >>> 买回失败: {e}')


def _force_buyback(ContextInfo):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[尾盘] ✓ 对手价买回')
    except Exception as e:
        _log(f'[尾盘失败!!] {e}')
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
# 第十部分：回调 & 工具
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50:'已报', 52:'部成', 53:'全成', 54:'部撤', 55:'已撤', 56:'废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    d = '买' if deal.m_nDirection == 1 else '卖'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    st['day_pnl'] += (amt - fee) if deal.m_nDirection == 2 else -(amt + fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f} × {deal.m_nVolume} PnL≈¥{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'{STOCK_NAME} v6.0 已停止 | 累计{st.get("total_t_days",0)}天 ¥{st.get("total_pnl",0):,.0f}')
        if st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
            _log(f'  ⚠ 未买回!')


def _now():
    import time as _t
    return _t.strftime('%H:%M:%S')


def _ts():
    import time as _t
    return _t.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    ts = _ts()
    if args:
        print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
