# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你做T策略 v7.3 — 双方向版 (修复分钟bar重复 & 手续费)
================================================================================
 QMT运行时注入: get_trade_detail_data / order_shares / ContextInfo.*

 【v7.3 改动】
   ★ 日级去重: ContextInfo.get_trade_date() 判重, 同一天只交易一次
   ★ 手续费: deal_callback 正确扣佣
   ★ 回测下单: 改用 THIS_CLOSE (当日收盘价), 更接近真实模拟
   ★ 精简日志: 不逐笔打印
================================================================================
"""

# ============================================================================
# 第一部分：全局配置
# ============================================================================
ACCOUNT = '8890145315'

STOCK_CODE      = '601869'
STOCK_NAME      = '长飞光纤'
STOCK_QMT       = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE  = 200
MIN_LOT         = 100
TIMER_INTERVAL  = '1nSecond'

# ---- 技术指标 ----
ATR_PERIOD = 14

# ---- 反T: 动态乘数 ----
SELL_TRIGGER_BASE_BEAR      = 0.40
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55
SELL_TRIGGER_BASE_WEAK_BULL = 0.65
DYNAMIC_MULT_MIN = 0.20
DYNAMIC_MULT_MAX = 1.50
DAILY_RANGE_CAP_ENABLED = True
DAILY_RANGE_CAP_MULT    = 0.80

# ---- 回落/回升 ----
PULLBACK_PCT        = 0.0010
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT           = 0.0010
BUYBACK_TIGHTEN_MULT = 0.60

# ---- 熔断 ----
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI     = 70
STRONG_BULL_STREAK  = 5

# ---- 紧急 & 止损 ----
EMERGENCY_BUYBACK_PCT = 0.03
STOP_LOSS_PCT         = 0.015

# ---- 正T ----
BUY_TRIGGER_PCT   = 0.030
BUY_TRIGGER_TRAIL = 0.020
SELLBACK_RISE_PCT = 0.012

# ---- 锁仓 ----
LOCK_PRICE_RATIO  = 0.015
LOCK_MOMENTUM_PCT = 0.005
LOCK_DRAWDOWN_PCT = 0.005
LOCK_LOOKBACK_SEC = 300
LOCK_COOLDOWN_SEC = 120

# ---- 仓位 ----
MAX_POSITION_LOTS = 5
MIN_POSITION_LOTS = 1
MAX_DAILY_TRADES  = 3
BT_INIT_CASH      = 500000
BT_POSITION_PCT   = 0.20

# ---- 时间 ----
FORCE_CLOSE_TIME   = '14:57:00'
ENABLE_FORCE_CLOSE = True

# ---- 数据 ----
HIST_DATA_LEN = 80
COMMISSION    = 0.00025
STAMP_TAX     = 0.001

# ---- 状态机 ----
STATE_IDLE       = 'IDLE'
STATE_SPIKING    = 'SPIKING'
STATE_SOLD       = 'SOLD'
STATE_DIPPING    = 'DIPPING'
STATE_DONE       = 'DONE'
STATE_FORCED     = 'FORCED'
STATE_BT_DIPPING = 'BT_DIPPING'
STATE_BT_BOUGHT  = 'BT_BOUGHT'
STATE_BT_SPIKING = 'BT_SPIKING'


# ============================================================================
# 第二部分：技术指标
# ============================================================================

def _sma(values, period):
    n = len(values); r = [0.0] * n
    for i in range(period - 1, n):
        r[i] = sum(values[i - period + 1 : i + 1]) / period
    return r


def _atr(highs, lows, closes, period=14):
    n = len(closes); tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
    result = [0.0] * n
    for i in range(period, n): result[i] = sum(tr[i - period + 1 : i + 1]) / period
    return result


def _rsi(closes, period=14):
    n = len(closes)
    if n < period + 1: return [50.0] * n
    rsi_vals = [50.0] * n; g, l = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        g.append(d if d > 0 else 0); l.append(abs(d) if d < 0 else 0)
    ag = sum(g[:period]) / period; al = sum(l[:period]) / period
    rsi_vals[period] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    for i in range(period, n - 1):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        rsi_vals[i + 1] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    return rsi_vals


def _up_streak(closes):
    n = len(closes); streak = [0] * n
    for i in range(1, n): streak[i] = streak[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return streak


def _daily_range_ma(highs, lows, opens, period=10):
    n = len(opens); ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0: ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)


# ============================================================================
# 第三部分：信号计算
# ============================================================================

def _calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak_val):
    if trend == 'bear': base = SELL_TRIGGER_BASE_BEAR
    elif trend == 'weak_bull': base = SELL_TRIGGER_BASE_WEAK_BULL
    else: base = SELL_TRIGGER_BASE_SIDEWAYS

    deviations = {}; total_dev = 0.0

    if trend == 'bear': d = -0.25 if up_streak_val == 0 else -0.15
    elif trend == 'strong_bull': d = +999
    elif trend == 'weak_bull':
        if up_streak_val >= 3: d = +0.20
        elif up_streak_val >= 1: d = +0.12
        else: d = +0.05
    else: d = 0.00
    deviations['T'] = d; total_dev += d

    if atr_pct > 0.08: atr_d = -0.30
    elif atr_pct > 0.07: atr_d = -0.22
    elif atr_pct > 0.06: atr_d = -0.15
    elif atr_pct > 0.05: atr_d = -0.08
    elif atr_pct > 0.03: atr_d = +0.05
    elif atr_pct > 0.02: atr_d = +0.15
    else: atr_d = +0.25
    if atr_ratio > 1.50: atrd_d = -0.25
    elif atr_ratio > 1.25: atrd_d = -0.18
    elif atr_ratio > 1.10: atrd_d = -0.10
    elif atr_ratio > 0.90: atrd_d = 0.00
    elif atr_ratio > 0.70: atrd_d = +0.12
    elif atr_ratio > 0.50: atrd_d = +0.20
    else: atrd_d = +0.25
    vol_d = atr_d * 0.55 + atrd_d * 0.45; vol_d = max(-0.35, min(0.30, vol_d))
    deviations['V'] = round(vol_d, 2); total_dev += vol_d

    if vol_ratio > 2.00: d = -0.25
    elif vol_ratio > 1.50: d = -0.18
    elif vol_ratio > 1.20: d = -0.08
    elif vol_ratio > 0.80: d = 0.00
    elif vol_ratio > 0.60: d = +0.12
    elif vol_ratio > 0.40: d = +0.20
    else: d = +0.25
    deviations['Q'] = d; total_dev += d

    if rsi_val > 80: d = -0.25
    elif rsi_val > 70: d = -0.18
    elif rsi_val > 60: d = -0.08
    elif rsi_val > 55: d = -0.03
    elif rsi_val > 45: d = 0.00
    elif rsi_val > 40: d = +0.03
    elif rsi_val > 30: d = +0.10
    elif rsi_val > 20: d = +0.20
    else: d = +0.25
    deviations['R'] = d; total_dev += d

    final = base + total_dev; final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final))
    return round(final, 2), deviations, base


def compute_signal(opens, highs, lows, closes, volumes):
    n = len(closes)
    if n < 60: return None
    co = opens[-1]; cc = closes[-1]; cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03
    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    ma5 = _sma(closes, 5)[-1]; ma20 = _sma(closes, 20)[-1]
    curr_rsi = _rsi(closes)[-1]; up_streak_val = _up_streak(closes)[-1]

    price_above_ma = (cc > ma20) and (ma5 > ma20)
    price_below_ma = (cc < ma20) and (ma5 < ma20)
    if price_above_ma and curr_rsi > STRONG_BULL_RSI and up_streak_val >= STRONG_BULL_STREAK:
        trend = 'strong_bull'
    elif price_above_ma: trend = 'weak_bull'
    elif price_below_ma: trend = 'bear'
    else: trend = 'sideways'

    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0
    sell_mult, factor_details, base_used = _calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak_val)

    daily_range_ma10 = _daily_range_ma(highs, lows, opens, 10)[-1]
    max_trigger_by_range = co * (1.0 + daily_range_ma10 * DAILY_RANGE_CAP_MULT)
    range_capped = False; sell_trigger_raw = co + curr_atr * sell_mult
    if DAILY_RANGE_CAP_ENABLED and sell_trigger_raw > max_trigger_by_range:
        sell_trigger = round(max_trigger_by_range, 2); range_capped = True
    else: sell_trigger = round(sell_trigger_raw, 2)

    do_short = True; reason = ''
    if trend == 'strong_bull': do_short = False; reason = '强牛'
    elif curr_vr < VOLUME_FILTER_RATIO: do_short = False; reason = '缩量'
    elif curr_rsi > RSI_OVERBOUGHT: do_short = False; reason = 'RSI'

    buy_trigger_floor = round(co * (1.0 - BUY_TRIGGER_PCT), 2)
    buy_trigger_trail = round(cc * (1.0 - BUY_TRIGGER_TRAIL), 2)
    buy_trigger = max(buy_trigger_floor, buy_trigger_trail)
    sellback_target_hint = round(buy_trigger * (1.0 + SELLBACK_RISE_PCT), 2)

    return {
        'do_short': do_short, 'blocked_reason': reason, 'trend': trend,
        'sell_trigger': sell_trigger, 'sell_trigger_raw': round(sell_trigger_raw, 2),
        'range_capped': range_capped, 'open_price': co, 'close_yday': cc,
        'atr': curr_atr, 'atr_pct': curr_atr_pct, 'rsi': curr_rsi,
        'vol_ratio': curr_vr, 'sell_mult': sell_mult, 'sell_mult_base': base_used,
        'factor_details': factor_details, 'atr_ratio': atr_ratio,
        'up_streak': up_streak_val, 'buyback_mult': BUYBACK_TRIGGER_MULT,
        'bounce_pct': BOUNCE_PCT,
        'buy_trigger': buy_trigger, 'buy_trigger_floor': buy_trigger_floor,
        'buy_trigger_trail': buy_trigger_trail,
        'sellback_target_hint': sellback_target_hint,
    }


# ============================================================================
# 第四部分：QMT 入口
# ============================================================================

def init(ContextInfo):
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account(ACCOUNT)
    ContextInfo.st = {
        'daily_signal': None, 'base_shares': 0, 'base_can_use': 0,
        'base_cost': 0.0, 'entry_price': 0.0, 'fstate': STATE_IDLE,
        'peak_price': 0.0, 'dip_price': 0.0, 'sell_fill_price': 0.0,
        'buyback_target': 0.0, 'buyback_target_pct': 0.0, 'day_pnl': 0.0,
        'stop_loss_hit': False, 'total_t_days': 0, 'total_pnl': 0.0,
        'total_commission': 0.0,    # 累计手续费
        'state_enter_time': '', 'sell_elapsed_bars': 0,
        'do_short': False, 'do_long': False, 'long_reason': '',
        'short_lots': 0, 'long_lots': 0, 'pos_value': 0.0, 'pos_pct': 0.0,
        'avail_cash': 0.0, 'trade_count_short': 0, 'trade_count_long': 0,
        'bt_dip_price': 0.0, 'bt_buy_trigger': 0.0, 'bt_buy_fill_price': 0.0,
        'bt_sellback_target': 0.0, 'bt_max_trail': 0.0, 'bt_sell_peak_price': 0.0,
        'locked': False, 'lock_reason': '', 'lock_since': '',
        'lock_cooldown_until': 0.0, 'price_history': [],
        '_bt_inited': False,
        '_last_trade_date': '',      # 日级去重
    }
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


# ============================================================================
# 第五部分：handlebar
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

    base_shares = 0; base_can_use = 0; base_cost = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
            base_cost = pos.m_dOpenPrice
            break

    # ---- 回测初始持仓注入 (仅首日首bar) ----
    if not is_live and base_shares < TRADE_LOT_SIZE and not st.get('_bt_inited'):
        bar_close_0 = closes[STOCK_QMT][-1]
        avail_0 = accounts[0].m_dAvailable if accounts else float(BT_INIT_CASH)
        if avail_0 >= BT_INIT_CASH: avail_0 = BT_INIT_CASH
        buy_cash = avail_0 * BT_POSITION_PCT
        shares_0 = int(buy_cash / (bar_close_0 * 1.001) / TRADE_LOT_SIZE) * TRADE_LOT_SIZE
        if shares_0 < TRADE_LOT_SIZE: shares_0 = TRADE_LOT_SIZE
        try: order_shares(STOCK_QMT, shares_0, 'THIS_CLOSE', 0, ContextInfo, ACCOUNT)
        except Exception: pass
        _log('[回测] 初始买入 {}股 @{:.2f} 总资金{:,.0f}'.format(shares_0, bar_close_0, BT_INIT_CASH))
        # 读取回结果
        positions2 = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions2:
            if pos.m_strInstrumentID == STOCK_CODE:
                base_shares = pos.m_nVolume; base_can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                base_cost = pos.m_dOpenPrice; break
        st['_bt_inited'] = True

    if base_shares < TRADE_LOT_SIZE:
        if is_live: _log('[警告] 底仓不足({}股)'.format(base_shares))
        return

    st['base_shares'] = base_shares; st['base_can_use'] = base_can_use
    st['base_cost'] = base_cost
    if st['entry_price'] == 0.0: st['entry_price'] = base_cost

    bar_open = opens[STOCK_QMT][-1]; bar_high = highs[STOCK_QMT][-1]
    bar_low = lows[STOCK_QMT][-1]; bar_close = closes[STOCK_QMT][-1]

    # ----- 日级去重 (★核心修复) -----
    if not is_live:
        try: trade_date = ContextInfo.get_trade_date()
        except Exception: trade_date = str(bar_close)  # fallback
        if trade_date == st.get('_last_trade_date', ''):
            return  # 同一日已处理过, 跳过
        st['_last_trade_date'] = trade_date

    # ----- 信号计算 -----
    signal = compute_signal(opens[STOCK_QMT], highs[STOCK_QMT], lows[STOCK_QMT],
                            closes[STOCK_QMT], volumes[STOCK_QMT])
    if signal is None: return

    if is_live:
        try:
            tick = ContextInfo.get_full_tick([STOCK_QMT])
            curr_price = tick.get(STOCK_QMT, {}).get('lastPrice', bar_close)
        except Exception: curr_price = bar_close
    else:
        curr_price = bar_close

    avail_cash = accounts[0].m_dAvailable if accounts else 0.0
    pos_value = base_shares * curr_price; total_val = pos_value + avail_cash

    short_lots = min(base_can_use // TRADE_LOT_SIZE, MAX_DAILY_TRADES)
    long_lots_cash = int(avail_cash / (bar_close * TRADE_LOT_SIZE * 1.01))
    long_lots_sell = base_can_use // TRADE_LOT_SIZE
    long_lots = min(long_lots_cash, long_lots_sell, MAX_DAILY_TRADES)

    do_short = signal['do_short'] and (short_lots >= MIN_POSITION_LOTS)
    do_long = long_lots >= MIN_POSITION_LOTS

    short_reason = ''
    if not signal['do_short']: short_reason = signal['blocked_reason']
    elif short_lots < MIN_POSITION_LOTS: short_reason = '仓位不足'

    long_reason = ''
    if not do_long:
        r = []
        if long_lots_cash < MIN_POSITION_LOTS: r.append('资金')
        if long_lots_sell < MIN_POSITION_LOTS: r.append('T+1')
        long_reason = ';'.join(r) if r else '?'

    signal['do_short'] = do_short; signal['short_reason'] = short_reason
    st['daily_signal'] = signal
    st['do_short'] = do_short; st['do_long'] = do_long
    st['long_reason'] = long_reason
    st['short_lots'] = short_lots; st['long_lots'] = long_lots
    st['pos_value'] = pos_value; st['pos_pct'] = pos_value / total_val * 100 if total_val > 0 else 0
    st['avail_cash'] = avail_cash
    st['trade_count_short'] = 0; st['trade_count_long'] = 0

    # ---- 回测: OHLC交易判定 ----
    if not is_live:
        # 获取当前bar时间用于日志
        bar_time = ''
        try: bar_time = ContextInfo.get_bar_time()
        except Exception: pass
        _bt_trade_from_ohlc(ContextInfo, signal, bar_open, bar_high, bar_low, bar_close,
                            do_short, do_long, bar_time)
        return

    # 实盘
    _print_status(ContextInfo, curr_price, avail_cash, pos_value, total_val)
    _print_signal(ContextInfo)


# ============================================================================
# 第六部分：回测 OHLC 交易判定
# ============================================================================

def _bt_trade_from_ohlc(ContextInfo, signal, open_p, high_p, low_p, close_p,
                        do_short, do_long, bar_time=''):
    """用日线OHLC判断当日是否可成交。每天最多一笔双向交易。"""
    st = ContextInfo.st
    atr_pct = signal['atr_pct']; traded = False
    bt = bar_time if bar_time else ''  # bar时间戳用于日志

    # --- 反T ---
    if do_short:
        sell_trig = signal['sell_trigger']
        if high_p >= sell_trig:
            sell_price = max(open_p, sell_trig)
            buyback_pct = atr_pct * BUYBACK_TRIGGER_MULT
            buyback_target = round(sell_price * (1.0 - buyback_pct), 2)

            if low_p <= buyback_target:       buy_price = buyback_target
            elif close_p <= sell_price * (1.0 + EMERGENCY_BUYBACK_PCT): buy_price = close_p
            else:                             buy_price = close_p

            gross = (sell_price - buy_price) * TRADE_LOT_SIZE
            _log('[{}] [反T] S{:.2f} B{:.2f} {:+,.0f} | O{:.2f} H{:.2f} L{:.2f} C{:.2f}'.format(
                bt, sell_price, buy_price, gross, open_p, high_p, low_p, close_p))

            _bt_order(ContextInfo, -TRADE_LOT_SIZE)
            _bt_order(ContextInfo, TRADE_LOT_SIZE)
            st['total_t_days'] += 1; st['total_pnl'] += gross
            traded = True

    # --- 正T ---
    if do_long and not traded:
        buy_trig = signal['buy_trigger']
        if low_p <= buy_trig:
            buy_price = buy_trig
            sell_target = round(buy_price * (1.0 + SELLBACK_RISE_PCT), 2)

            if high_p >= sell_target:                     sell_price = sell_target
            elif close_p <= buy_price * (1.0 - STOP_LOSS_PCT): sell_price = close_p
            else:                                         sell_price = close_p

            gross = (sell_price - buy_price) * TRADE_LOT_SIZE
            _log('[{}] [正T] B{:.2f} S{:.2f} {:+,.0f} | O{:.2f} H{:.2f} L{:.2f} C{:.2f}'.format(
                bt, buy_price, sell_price, gross, open_p, high_p, low_p, close_p))

            _bt_order(ContextInfo, TRADE_LOT_SIZE)
            _bt_order(ContextInfo, -TRADE_LOT_SIZE)
            st['total_t_days'] += 1; st['total_pnl'] += gross


def _bt_order(ContextInfo, shares):
    """回测下单: THIS_CLOSE 以当日收盘价成交, 确保价在bar范围内"""
    try:
        order_shares(STOCK_QMT, shares, 'THIS_CLOSE', 0, ContextInfo, ACCOUNT)
    except Exception: pass


# ============================================================================
# 第七部分：ontimer (实盘)
# ============================================================================

def ontimer(ContextInfo):
    if not ContextInfo.is_last_bar(): return
    st = ContextInfo.st
    signal = st.get('daily_signal')
    if signal is None: return
    do_short = st.get('do_short', False); do_long = st.get('do_long', False)
    if not do_short and not do_long: return
    now = _now()
    if not _is_market_open(now): return

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        if now < '14:57:00':
            tc_s = st.get('trade_count_short', 0); tc_l = st.get('trade_count_long', 0)
            if ((do_short and tc_s < MAX_DAILY_TRADES) or
                (do_long and tc_l < MAX_DAILY_TRADES)):
                _maybe_resume_trading(ContextInfo)
        return

    try: tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception: return
    if STOCK_QMT not in tick: return
    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0: return

    fstate = st['fstate']; now_ts = _time()
    if fstate == STATE_IDLE: _assess_strength(ContextInfo, price, now_ts)
    if fstate == STATE_IDLE: _handle_idle(ContextInfo, price)
    elif fstate == STATE_SPIKING: _handle_spiking(ContextInfo, price)
    elif fstate == STATE_SOLD: _handle_sold(ContextInfo, price)
    elif fstate == STATE_DIPPING: _handle_dipping(ContextInfo, price)
    elif fstate == STATE_BT_DIPPING: _handle_bt_dipping(ContextInfo, price)
    elif fstate == STATE_BT_BOUGHT: _handle_bt_bought(ContextInfo, price)
    elif fstate == STATE_BT_SPIKING: _handle_bt_spiking(ContextInfo, price)

    if st['fstate'] in (STATE_SOLD, STATE_DIPPING):
        st['sell_elapsed_bars'] = st.get('sell_elapsed_bars', 0) + 1

    if ENABLE_FORCE_CLOSE and now >= FORCE_CLOSE_TIME:
        f = st['fstate']
        if f in (STATE_SOLD, STATE_DIPPING): _force_buyback(ContextInfo)
        elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING): _do_bt_force_sell(ContextInfo)
        elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE): st['fstate'] = STATE_DONE

    if fstate == STATE_SOLD and not st.get('stop_loss_hit', False):
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st.get('day_pnl', 0) < -loss_limit:
            st['stop_loss_hit'] = True; _force_buyback(ContextInfo)


# ============================================================================
# 第八部分：状态处理 (实盘)
# ============================================================================

def _handle_idle(ContextInfo, price):
    st = ContextInfo.st; signal = st.get('daily_signal', {})
    if st.get('do_short', False) and not st.get('locked', False):
        trigger = signal.get('sell_trigger', 999999)
        if price >= trigger:
            can_use = st.get('base_can_use', st['base_shares'])
            if can_use < TRADE_LOT_SIZE: return
            tc = st.get('trade_count_short', 0)
            if tc >= MAX_DAILY_TRADES: return
            st['trade_count_short'] = tc + 1
            st['fstate'] = STATE_SPIKING; st['peak_price'] = price; st['state_enter_time'] = _now()
            return
    if st.get('do_long', False):
        buy_trigger = signal.get('buy_trigger', 0)
        if price <= buy_trigger:
            tc = st.get('trade_count_long', 0)
            if tc >= MAX_DAILY_TRADES: return
            st['trade_count_long'] = tc + 1
            st['fstate'] = STATE_BT_DIPPING; st['bt_dip_price'] = price
            st['bt_buy_trigger'] = buy_trigger; st['state_enter_time'] = _now()


def _handle_spiking(ContextInfo, price):
    st = ContextInfo.st
    if price > st['peak_price']: st['peak_price'] = price
    peak = st['peak_price']; pullback = (peak - price) / peak if peak > 0 else 0
    if pullback >= PULLBACK_PCT:
        atr_pct = st['daily_signal']['atr_pct']; buyback_pct = atr_pct * BUYBACK_TRIGGER_MULT
        st['sell_fill_price'] = price
        st['buyback_target'] = round(price * (1.0 - buyback_pct), 2)
        st['buyback_target_pct'] = buyback_pct * 100
        st['sell_elapsed_bars'] = 0; st['state_enter_time'] = _now()
        _mini_sell(ContextInfo, price); st['fstate'] = STATE_SOLD
    elif price < st['daily_signal'].get('sell_trigger', 999999):
        st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0; st['state_enter_time'] = _now()


def _handle_sold(ContextInfo, price):
    st = ContextInfo.st; sp = st['sell_fill_price']; bt = st['buyback_target']
    if price >= sp * (1.0 + EMERGENCY_BUYBACK_PCT): _mini_buyback(ContextInfo, price, '紧急'); return
    tightened_bt = bt
    if st.get('sell_elapsed_bars', 0) > 30 and price > sp * 0.995:
        tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] *
                             BUYBACK_TRIGGER_MULT * BUYBACK_TIGHTEN_MULT)
        tightened_bt = round(max(tightened_bt, bt), 2)
    if price <= tightened_bt:
        st['fstate'] = STATE_DIPPING; st['dip_price'] = price; st['state_enter_time'] = _now()


def _handle_dipping(ContextInfo, price):
    st = ContextInfo.st
    if price < st['dip_price']: st['dip_price'] = price
    dip = st['dip_price'] or price; bounce = (price - dip) / dip if dip > 0 else 0
    if bounce >= BOUNCE_PCT:
        sp = st['sell_fill_price']; gross = (sp - price) * TRADE_LOT_SIZE
        _mini_buyback(ContextInfo, price, '正常'); st['total_t_days'] += 1; st['total_pnl'] += gross
    elif price > st['buyback_target']:
        st['fstate'] = STATE_SOLD; st['dip_price'] = 0.0; st['state_enter_time'] = _now()


def _handle_bt_dipping(ContextInfo, price):
    st = ContextInfo.st
    if price < st.get('bt_dip_price', price): st['bt_dip_price'] = price
    dip = st.get('bt_dip_price', price) or price; bounce = (price - dip) / dip if dip > 0 else 0
    if bounce >= BOUNCE_PCT:
        need = price * TRADE_LOT_SIZE * 1.001; avail = _cash(ContextInfo)
        if avail < need: st['fstate'] = STATE_IDLE; return
        _mini_buy(ContextInfo, price); st['fstate'] = STATE_BT_BOUGHT
        st['bt_buy_fill_price'] = price
        st['bt_sellback_target'] = round(price * (1.0 + SELLBACK_RISE_PCT), 2)


def _handle_bt_bought(ContextInfo, price):
    st = ContextInfo.st; target = st.get('bt_sellback_target', 999999); bp = st.get('bt_buy_fill_price', 0)
    if bp > 0 and price <= bp * (1.0 - STOP_LOSS_PCT): _do_bt_force_sell(ContextInfo); return
    if price >= target: st['fstate'] = STATE_BT_SPIKING; st['bt_sell_peak_price'] = price


def _handle_bt_spiking(ContextInfo, price):
    st = ContextInfo.st
    if price > st.get('bt_sell_peak_price', price): st['bt_sell_peak_price'] = price
    peak = st.get('bt_sell_peak_price', price); pullback = (peak - price) / peak if peak > 0 else 0
    if pullback >= PULLBACK_PCT:
        bp = st['bt_buy_fill_price']; gross = (price - bp) * TRADE_LOT_SIZE
        _mini_sell_bt(ContextInfo, price); st['fstate'] = STATE_DONE
        st['total_t_days'] += 1; st['total_pnl'] += gross


# ============================================================================
# 第九部分：下单
# ============================================================================

def _mini_sell(ContextInfo, price):
    try: order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
    except Exception: ContextInfo.st['fstate'] = STATE_IDLE


def _mini_buyback(ContextInfo, price, reason=''):
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001; avail = _cash(ContextInfo)
    if avail < need: return
    try: order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
    except Exception: pass
    st['fstate'] = STATE_DONE


def _force_buyback(ContextInfo):
    try: order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', 0, ContextInfo, _acc(ContextInfo))
    except Exception: pass
    ContextInfo.st['fstate'] = STATE_FORCED


def _mini_buy(ContextInfo, price):
    try: order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
    except Exception: ContextInfo.st['fstate'] = STATE_IDLE


def _mini_sell_bt(ContextInfo, price):
    try: order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
    except Exception: pass


def _do_bt_force_sell(ContextInfo):
    try: order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, ContextInfo, _acc(ContextInfo))
    except Exception: pass
    ContextInfo.st['fstate'] = STATE_FORCED


# ============================================================================
# 第十部分：锁仓 & 恢复
# ============================================================================

def _assess_strength(ContextInfo, price, now_ts):
    st = ContextInfo.st; sig = st.get('daily_signal', {})
    open_price = sig.get('open_price', 0)
    if open_price <= 0: return
    hist = st.get('price_history', [])
    hist.append((now_ts, price)); cutoff = now_ts - LOCK_LOOKBACK_SEC
    hist = [(t, p) for t, p in hist if t >= cutoff]; st['price_history'] = hist
    if len(hist) < 10: return
    prices = [p for _, p in hist]; p5 = prices[0]; pn = prices[-1]; dh = max(prices)
    cond1 = pn > open_price * (1.0 + LOCK_PRICE_RATIO)
    cond2 = (pn - p5) / p5 > LOCK_MOMENTUM_PCT if p5 > 0 else False
    cond3 = (dh - pn) / dh < LOCK_DRAWDOWN_PCT if dh > 0 else False
    should_lock = cond1 and cond2 and cond3
    cool_ok = now_ts >= st.get('lock_cooldown_until', 0)
    if should_lock and not st.get('locked'):
        st['locked'] = True; st['lock_since'] = _now()
    elif not should_lock and st.get('locked') and cool_ok:
        st['locked'] = False; st['lock_reason'] = ''; st['lock_since'] = ''
        st['lock_cooldown_until'] = 0.0
    elif should_lock: st['lock_cooldown_until'] = 0.0
    elif st.get('locked') and st.get('lock_cooldown_until', 0) == 0.0:
        st['lock_cooldown_until'] = now_ts + LOCK_COOLDOWN_SEC


def _maybe_resume_trading(ContextInfo):
    st = ContextInfo.st
    tc_s = st.get('trade_count_short', 0); tc_l = st.get('trade_count_long', 0)
    can_s = st.get('do_short', False) and tc_s < MAX_DAILY_TRADES
    can_l = st.get('do_long', False) and tc_l < MAX_DAILY_TRADES
    if can_s or can_l:
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                st['base_shares'] = pos.m_nVolume
                st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                st['base_cost'] = pos.m_dOpenPrice; break
        st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0; st['dip_price'] = 0.0
        st['sell_fill_price'] = 0.0; st['buyback_target'] = 0.0
        st['state_enter_time'] = _now(); st['stop_loss_hit'] = False


# ============================================================================
# 第十一部分：回调 & 工具
# ============================================================================

def order_callback(ContextInfo, order): pass


def deal_callback(ContextInfo, deal):
    """成交回调: 累加手续费"""
    st = ContextInfo.st
    fee = deal.m_fCommission + deal.m_fStampTax
    st['total_commission'] = st.get('total_commission', 0.0) + fee


def _print_status(ContextInfo, curr_price, avail_cash, pos_value, total_val):
    st = ContextInfo.st; cost = st['entry_price']
    unreal_pnl = (curr_price - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_price/cost-1)*100 if cost > 0 else 0
    _log('持仓:{}股×{:.2f}={:,.0f} 浮{:+,.0f}({:+.1f}%) 现金{:,.0f}'.format(
        st['base_shares'], curr_price, pos_value, unreal_pnl, unreal_pct, avail_cash))
    if st['total_t_days'] > 0: _log('累计:{}笔 毛利{:+,.0f} 佣金{:,.0f}'.format(
        st['total_t_days'], st['total_pnl'], st.get('total_commission', 0)))


def _print_signal(ContextInfo):
    st = ContextInfo.st; s = st['daily_signal']
    _log('Trend:{} O{:.2f} ATR{:.1f}% RSI{:.0f} Mult{:.2f} Short:{} Long:{}'.format(
        s['trend'], s['open_price'], s['atr_pct']*100, s['rsi'], s['sell_mult'],
        'Y' if st['do_short'] else 'N', 'Y' if st['do_long'] else 'N'))


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st and st.get('total_t_days', 0) > 0:
        gross = st.get('total_pnl', 0)
        comm = st.get('total_commission', 0)
        _log('=' * 55)
        _log('  {} v7.3  交易日:{}笔  毛利{:+,.0f}  佣金{:,.0f}  净利{:+,.0f}'.format(
            STOCK_NAME, st['total_t_days'], gross, comm, gross - comm))
        _log('=' * 55)


def _cash(ContextInfo):
    try: a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT'); return a[0].m_dAvailable if a else 0.0
    except Exception: return 0.0


def _acc(ContextInfo):
    return ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else ACCOUNT


def _now():
    import time as _t; return _t.strftime('%H:%M:%S')


def _time():
    import time as _t; return _t.time()


def _ts():
    import time as _t; return _t.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    ts = _ts()
    if args: print('{} {}'.format(ts, args[0]), *args[1:], **kwargs)
    else: print(**kwargs)


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
