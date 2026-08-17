# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你做T策略 v6.0 — 双方向版 (反T+正T + 动态乘数 + 锁仓)
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【v6.0 核心特性】
   ★ 反T(short): 动态乘数模型 — 趋势+波动率+成交量+RSI 四因子 → 自适应卖出触发线
   ★ 正T(long):  下跌买入 → 回升卖出 — 动态追踪买回线
   ★ 锁仓机制:    强牛市中检测动量+回撤 → 自动锁定反T, 避免卖飞
   ★ 振幅约束:    日内振幅MA限制卖出触发线, 防止极端行情假突破
   ★ 买回触发线:  基于实际卖出价动态计算 (继承v5)

 【状态机 — 反T】
  IDLE → SPIKING(冲高监控) → SOLD(已卖出) → DIPPING(下跌监控) → 买回 → DONE
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
               跌到买回线       涨超紧急线      14:57尾盘
                    │               │               │
                    ▼               ▼               ▼
               DIPPING         立即买回         强制买回

 【状态机 — 正T】
  IDLE → BT_DIPPING(下跌监控) → BT_BOUGHT(已买入) → BT_SPIKING(冲高监控) → 卖出 → DONE

 【来源】适配自 MiniQMT DayTradeing v20 → QMT内部单文件运行
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
TRADE_LOT_SIZE  = 200
MIN_LOT         = 100

# 检测周期 (ontimer 间隔)
TIMER_INTERVAL  = '1nSecond'

# ---- 技术指标参数 ----
ATR_PERIOD = 14

# ---- 反T (short): 动态乘数 ----
SELL_TRIGGER_BASE_BEAR      = 0.40
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55
SELL_TRIGGER_BASE_WEAK_BULL = 0.65

DYNAMIC_MULT_MIN = 0.20
DYNAMIC_MULT_MAX = 1.50

DAILY_RANGE_CAP_ENABLED = True
DAILY_RANGE_CAP_MULT    = 0.80

# ---- 冲高回落确认（卖出用） ----
PULLBACK_PCT = 0.0010

# ---- 买回触发（基于实际卖出价动态计算） ----
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT           = 0.0010
BUYBACK_TIGHTEN_MULT = 0.60   # 卖出后超30秒未触发, 收紧买回线

# ---- 熔断 & 过滤 ----
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI     = 70
STRONG_BULL_STREAK  = 5

# ---- 紧急买回 & 止损 ----
EMERGENCY_BUYBACK_PCT = 0.03
STOP_LOSS_PCT         = 0.015

# ---- 正T (long) 参数 ----
BUY_TRIGGER_PCT   = 0.030    # 买入触发 = 开盘价 × (1 - 此值), 作为保底
BUY_TRIGGER_TRAIL = 0.020    # 动态追踪 = 当前价 × (1 - 此值)
SELLBACK_RISE_PCT = 0.012    # 正T卖出目标 = 买入价 × (1 + 此值)

# ---- 锁仓参数 ----
LOCK_PRICE_RATIO  = 0.015    # 现价高于开盘此比例
LOCK_MOMENTUM_PCT = 0.005    # 近期动量超此值
LOCK_DRAWDOWN_PCT = 0.005    # 从高点回撤小于此值
LOCK_LOOKBACK_SEC = 300      # 回看窗口(秒)
LOCK_COOLDOWN_SEC = 120      # 解锁冷却(秒)

# ---- 仓位管理 ----
MAX_POSITION_LOTS = 5
MIN_POSITION_LOTS = 1
MAX_DAILY_TRADES  = 3
BT_INIT_SHARES    = 1000           # 回测模式: 初始持仓股数 (实盘时由get_trade_detail_data获取, 此值仅在回测底仓为0时生效)

# ---- 时间 ----
FORCE_CLOSE_TIME   = '14:57:00'
ENABLE_FORCE_CLOSE = True

# ---- 数据 & 费率 ----
HIST_DATA_LEN = 80
COMMISSION    = 0.00025
STAMP_TAX     = 0.001

# ---- 状态机常量 ----
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
# 第二部分：技术指标 (纯函数)
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
    rsi_vals = [50.0] * n
    g, l = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        g.append(d if d > 0 else 0)
        l.append(abs(d) if d < 0 else 0)
    ag = sum(g[:period]) / period
    al = sum(l[:period]) / period
    rsi_vals[period] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    for i in range(period, n - 1):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        rsi_vals[i + 1] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    return rsi_vals


def _up_streak(closes):
    n = len(closes)
    streak = [0] * n
    for i in range(1, n):
        streak[i] = streak[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return streak


def _daily_range_ma(highs, lows, opens, period=10):
    n = len(opens)
    ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0:
            ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)


# ============================================================================
# 第三部分：动态乘数 & 信号计算
# ============================================================================

def _calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak_val):
    """多因子动态乘数模型。返回 (final_mult, factor_details, base_mult)"""
    if trend == 'bear':
        base = SELL_TRIGGER_BASE_BEAR
    elif trend == 'weak_bull':
        base = SELL_TRIGGER_BASE_WEAK_BULL
    else:
        base = SELL_TRIGGER_BASE_SIDEWAYS

    deviations = {}
    total_dev = 0.0

    # 因子1: 趋势
    if trend == 'bear':
        d = -0.25 if up_streak_val == 0 else -0.15
    elif trend == 'strong_bull':
        d = +999
    elif trend == 'weak_bull':
        if up_streak_val >= 3:     d = +0.20
        elif up_streak_val >= 1:   d = +0.12
        else:                       d = +0.05
    else:
        d = 0.00
    deviations['趋势'] = d
    total_dev += d

    # 因子2: 波动率
    if atr_pct > 0.08:        atr_d = -0.30
    elif atr_pct > 0.07:      atr_d = -0.22
    elif atr_pct > 0.06:      atr_d = -0.15
    elif atr_pct > 0.05:      atr_d = -0.08
    elif atr_pct > 0.03:      atr_d = +0.05
    elif atr_pct > 0.02:      atr_d = +0.15
    else:                     atr_d = +0.25

    if atr_ratio > 1.50:         atrd_d = -0.25
    elif atr_ratio > 1.25:       atrd_d = -0.18
    elif atr_ratio > 1.10:       atrd_d = -0.10
    elif atr_ratio > 0.90:       atrd_d = 0.00
    elif atr_ratio > 0.70:       atrd_d = +0.12
    elif atr_ratio > 0.50:       atrd_d = +0.20
    else:                        atrd_d = +0.25

    vol_d = atr_d * 0.55 + atrd_d * 0.45
    vol_d = max(-0.35, min(0.30, vol_d))
    deviations['波动率'] = round(vol_d, 2)
    total_dev += vol_d

    # 因子3: 成交量
    if vol_ratio > 2.00:         d = -0.25
    elif vol_ratio > 1.50:       d = -0.18
    elif vol_ratio > 1.20:       d = -0.08
    elif vol_ratio > 0.80:       d = 0.00
    elif vol_ratio > 0.60:       d = +0.12
    elif vol_ratio > 0.40:       d = +0.20
    else:                        d = +0.25
    deviations['成交量'] = d
    total_dev += d

    # 因子4: RSI
    if rsi_val > 80:           d = -0.25
    elif rsi_val > 70:         d = -0.18
    elif rsi_val > 60:         d = -0.08
    elif rsi_val > 55:         d = -0.03
    elif rsi_val > 45:         d = 0.00
    elif rsi_val > 40:         d = +0.03
    elif rsi_val > 30:         d = +0.10
    elif rsi_val > 20:         d = +0.20
    else:                      d = +0.25
    deviations['RSI'] = d
    total_dev += d

    final = base + total_dev
    final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final))
    return round(final, 2), deviations, base


def compute_signal(opens, highs, lows, closes, volumes):
    """计算当日完整信号 (反T + 正T)"""
    n = len(closes)
    if n < 60:
        return None

    co = opens[-1]
    cc = closes[-1]
    cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]
    curr_rsi = _rsi(closes)[-1]
    up_streak_val = _up_streak(closes)[-1]

    price_above_ma = (cc > ma20) and (ma5 > ma20)
    price_below_ma = (cc < ma20) and (ma5 < ma20)

    if price_above_ma and curr_rsi > STRONG_BULL_RSI and up_streak_val >= STRONG_BULL_STREAK:
        trend = 'strong_bull'
    elif price_above_ma:
        trend = 'weak_bull'
    elif price_below_ma:
        trend = 'bear'
    else:
        trend = 'sideways'

    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    sell_mult, factor_details, base_used = _calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak_val
    )

    daily_range_ma10 = _daily_range_ma(highs, lows, opens, 10)[-1]
    max_trigger_by_range = co * (1.0 + daily_range_ma10 * DAILY_RANGE_CAP_MULT)
    range_capped = False

    sell_trigger_raw = co + curr_atr * sell_mult

    if DAILY_RANGE_CAP_ENABLED and sell_trigger_raw > max_trigger_by_range:
        sell_trigger = round(max_trigger_by_range, 2)
        range_capped = True
    else:
        sell_trigger = round(sell_trigger_raw, 2)

    # 反T方向决策
    do_short = True
    reason = ''

    if trend == 'strong_bull':
        do_short = False
        reason = '强牛禁反T(连涨>=5+RSI>70)'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short = False
        reason = '缩量(量比{:.2f})'.format(curr_vr)
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short = False
        reason = 'RSI超买({:.0f})'.format(curr_rsi)

    # 正T买入触发线
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
        # 正T相关
        'buy_trigger': buy_trigger, 'buy_trigger_floor': buy_trigger_floor,
        'buy_trigger_trail': buy_trigger_trail,
        'sellback_target_hint': sellback_target_hint,
    }


# ============================================================================
# 第四部分：QMT 策略入口
# ============================================================================

def init(ContextInfo):
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account(ACCOUNT)

    state = {
        'daily_signal':     None,
        'base_shares':      0,
        'base_can_use':     0,
        'base_cost':        0.0,
        'entry_price':      0.0,
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
        'startup_printed':  False,
        'state_enter_time': '',
        'sell_elapsed_bars': 0,

        # 正T
        'do_short':         False,
        'do_long':          False,
        'long_reason':      '',
        'short_lots':       0,
        'long_lots':        0,
        'pos_value':        0.0,
        'pos_pct':          0.0,
        'avail_cash':       0.0,
        'trade_count_short': 0,
        'trade_count_long':  0,
        'bt_dip_price':     0.0,
        'bt_buy_trigger':   0.0,
        'bt_buy_fill_price': 0.0,
        'bt_sellback_target': 0.0,
        'bt_max_trail':     0.0,
        'bt_sell_peak_price': 0.0,

        # 锁仓
        'locked':           False,
        'lock_reason':      '',
        'lock_since':       '',
        'lock_cooldown_until': 0.0,
        'price_history':    [],
        '_pre_market_done': '',
        '_guard_date':      '',
        '_bt_inited':       False,   # 回测: 初始持仓仅注入一次
        '_bt_spam_guard':   False,   # 回测: 防止底仓不足日志刷屏
    }
    ContextInfo.st = state
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")
    is_live = ContextInfo.is_last_bar()
    if not is_live:
        _log('[回测] 建议在回测面板配置初始持仓: {} {}股 (否则自动注入{}股仅用于信号展示)'.format(
            STOCK_CODE, TRADE_LOT_SIZE, BT_INIT_SHARES))
    _log('[启动] {} v6.0 完整版 反T+正T {}'.format(STOCK_NAME, STOCK_QMT))


def _do_backtest_init_buy(ContextInfo, closes, accounts):
    """回测首个bar: 以开盘价买入底仓(留足正T资金), 让QMT模拟账户有真实持仓"""
    open_price = closes[STOCK_QMT][-1] if closes[STOCK_QMT] else 100.0

    # 计算可用初始资金
    if accounts and len(accounts) > 0:
        total_cash = max(accounts[0].m_dAvailable, 500000)
    else:
        total_cash = 500000

    # 初始买入 = 总量的 20%, 留 80% 现金做正T
    buy_cash = total_cash * 0.20
    shares = int(buy_cash / (open_price * 1.001) / TRADE_LOT_SIZE) * TRADE_LOT_SIZE
    if shares < TRADE_LOT_SIZE:
        shares = TRADE_LOT_SIZE

    _log('[回测初始化] 买入底仓 {}股 @Y{:.2f} = Y{:,.0f} (20%仓位, 留Y{:,.0f}做正T)'.format(
        shares, open_price, shares * open_price, total_cash - shares * open_price))
    try:
        order_shares(STOCK_QMT, shares, 'FIX', open_price, ContextInfo, ACCOUNT)
    except Exception as e:
        _log('[回测初始化] 下单失败: {}'.format(e))


# ============================================================================
# 第五部分：handlebar — 日线触发 (计算信号)
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

    # 刷新持仓
    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    accounts  = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')

    base_shares = 0
    base_can_use = 0
    base_cost = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
            base_cost = pos.m_dOpenPrice
            break

    if base_shares < TRADE_LOT_SIZE:
        if is_live:
            _log('[警告] 底仓不足1手({}股) - 策略等待中'.format(base_shares))
            st['base_shares'] = 0
            return
        else:
            # 回测模式: 第一个bar自动买入半仓
            if not st.get('_bt_inited'):
                _do_backtest_init_buy(ContextInfo, closes, accounts)
                st['_bt_inited'] = True
                # 重新读取持仓(买入后)
                positions2 = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
                for pos in positions2:
                    if pos.m_strInstrumentID == STOCK_CODE:
                        base_shares = pos.m_nVolume
                        base_can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                        base_cost = pos.m_dOpenPrice
                        break
                if base_shares < TRADE_LOT_SIZE:
                    st['base_shares'] = 0
                    return
            else:
                # 后续bar: 持仓仍为0 → 跳过当日
                st['base_shares'] = 0
                return

    st['base_shares'] = base_shares
    st['base_can_use'] = base_can_use
    st['base_cost'] = base_cost

    if st['entry_price'] == 0.0:
        st['entry_price'] = base_cost

    # 获取实时价格
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
        tick_data = tick.get(STOCK_QMT, {})
        curr_price = tick_data.get('lastPrice', 0)
        today_open = tick_data.get('open', 0)
    except Exception:
        curr_price = closes[STOCK_QMT][-1]
        today_open = 0

    if curr_price <= 0:
        curr_price = closes[STOCK_QMT][-1]

    avail_cash = accounts[0].m_dAvailable if accounts else 0.0
    pos_value = base_shares * curr_price
    total_val = pos_value + avail_cash

    # 用实时开盘价更新历史数据最后一个开盘价
    opens_list = list(opens[STOCK_QMT])
    if today_open > 0 and len(opens_list) > 0:
        opens_list[-1] = today_open

    signal = compute_signal(
        opens_list, highs[STOCK_QMT], lows[STOCK_QMT],
        closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    # 正T可行性判断
    pos_pct = pos_value / total_val * 100 if total_val > 0 else 0
    short_lots = min(base_can_use // TRADE_LOT_SIZE, MAX_DAILY_TRADES)
    long_lots_cash = int(avail_cash / (curr_price * TRADE_LOT_SIZE * 1.01))
    long_lots_sell = base_can_use // TRADE_LOT_SIZE
    long_lots = min(long_lots_cash, long_lots_sell, MAX_DAILY_TRADES)

    do_short = signal['do_short'] and (short_lots >= MIN_POSITION_LOTS)
    short_reason = ''
    if not signal['do_short']:
        short_reason = signal.get('blocked_reason', '信号禁止')
    elif short_lots < MIN_POSITION_LOTS:
        short_reason = '可用{}股<{}手'.format(base_can_use, MIN_POSITION_LOTS)

    do_long = long_lots >= MIN_POSITION_LOTS
    long_reason = ''
    if not do_long:
        reasons = []
        if long_lots_cash < MIN_POSITION_LOTS: reasons.append('资金不足')
        if long_lots_sell < MIN_POSITION_LOTS: reasons.append('T+1:无可卖持仓')
        long_reason = '; '.join(reasons) if reasons else '未知'

    signal['do_short'] = do_short
    signal['short_reason'] = short_reason
    # compute_signal() 已返回了 buy_trigger/buy_trigger_floor 等正T字段, 这里确保存在即可
    signal.setdefault('buy_trigger', signal.get('buy_trigger', 0))
    signal.setdefault('buy_trigger_floor', signal.get('buy_trigger_floor', 0))
    signal.setdefault('buy_trigger_trail', signal.get('buy_trigger_trail', 0))
    signal.setdefault('sellback_target_hint', signal.get('sellback_target_hint', 0))

    # 写入状态
    st['daily_signal'] = signal
    st['do_short'] = do_short
    st['do_long'] = do_long
    st['long_reason'] = long_reason
    st['short_lots'] = short_lots
    st['long_lots'] = long_lots
    st['pos_value'] = pos_value
    st['pos_pct'] = pos_pct
    st['avail_cash'] = avail_cash
    st['trade_count_short'] = 0
    st['trade_count_long'] = 0

    # 重置状态机
    st['fstate'] = STATE_IDLE
    st['peak_price'] = 0.0
    st['dip_price'] = 0.0
    st['sell_fill_price'] = 0.0
    st['buyback_target'] = 0.0
    st['buyback_target_pct'] = 0.0
    st['day_pnl'] = 0.0
    st['stop_loss_hit'] = False
    st['state_enter_time'] = _now()
    st['sell_elapsed_bars'] = 0
    for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
               'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
        st[k] = 0.0
    st['locked'] = False
    st['lock_reason'] = ''
    st['lock_since'] = ''
    st['lock_cooldown_until'] = 0.0
    st['price_history'] = []
    st['_pre_market_done'] = ''

    if is_live:
        _print_status(ContextInfo, curr_price, avail_cash, pos_value, total_val)
        _print_signal(ContextInfo)
    else:
        if not st['startup_printed']:
            _log('=' * 55)
            _log('  {} v6.0 完整版 — 已加载'.format(STOCK_NAME))
            _log('  现价 ¥{:.2f} | ATR ¥{:.2f}({:.1f}%)'.format(
                curr_price, signal['atr'], signal['atr_pct'] * 100))
            _log('  卖出乘数 {}(基准{}) | 买回乘数 {}'.format(
                signal['sell_mult'], signal['sell_mult_base'], signal['buyback_mult']))
            _log('  振幅约束: {}'.format('启用' if DAILY_RANGE_CAP_ENABLED else '关闭'))
            _log('=' * 55)
            st['startup_printed'] = True
        # 回测: 用当日OHLC模拟日内价格路径, 驱动状态机
        _bt_simulate_intraday(
            ContextInfo,
            opens[STOCK_QMT][-1], highs[STOCK_QMT][-1],
            lows[STOCK_QMT][-1], closes[STOCK_QMT][-1]
        )


def _print_status(ContextInfo, curr_price, avail_cash, pos_value, total_val):
    st = ContextInfo.st
    cost = st['entry_price']
    unreal_pnl = (curr_price - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_price / cost - 1) * 100 if cost > 0 else 0

    _log('━━━ 账户状态 ━━━')
    _log('  持仓: {}股 × ¥{:.2f} = ¥{:,.0f}'.format(st['base_shares'], curr_price, pos_value))
    _log('  浮动盈亏: ¥{:,.0f}({:+.1f}%) | 现金: ¥{:,.0f} | 总资产: ¥{:,.0f}'.format(
        unreal_pnl, unreal_pct, avail_cash, total_val))
    lot_cost = curr_price * TRADE_LOT_SIZE
    if avail_cash >= lot_cost * 1.01:
        _log('  正T: ✓ 可用(1手需¥{:,.0f})'.format(lot_cost))
    else:
        _log('  正T: ✗ 不可用(1手需¥{:,.0f} > 现金¥{:,.0f})'.format(lot_cost, avail_cash))
    if st['total_t_days'] > 0:
        _log('  累计做T: {}笔 | 盈亏: ¥{:,.0f}'.format(st['total_t_days'], st['total_pnl']))


def _print_signal(ContextInfo):
    st = ContextInfo.st
    s = st['daily_signal']

    trend_labels = {'strong_bull': '强牛', 'weak_bull': '弱牛', 'sideways': '震荡', 'bear': '熊市'}
    trend_cn = trend_labels.get(s['trend'], s['trend'])

    _log('━━━ 做T信号 ━━━')
    _log('  趋势: {} | 开盘 ¥{:.2f} | ATR ¥{:.2f}({:.1f}%) | RSI {:.0f} | 量比 {:.2f}'.format(
        trend_cn, s['open_price'], s['atr'], s['atr_pct'] * 100, s['rsi'], s['vol_ratio']))
    _log('  乘数 {:.2f}(基准{}) | 触发线 ¥{:.2f}{}'.format(
        s['sell_mult'], s['sell_mult_base'], s['sell_trigger'],
        '(振幅约束)' if s.get('range_capped') else ''))

    # 因子详情
    fd = s.get('factor_details', {})
    if fd:
        _log('  因子: {}'.format(' '.join('{}{:+.2f}'.format(k, v) for k, v in fd.items())))

    # 反T
    if st.get('do_short'):
        _log('  反T: ✅ {}手 触发Y{:.2f} 买回=卖价×(1-ATR%×{:.2f}) 紧急+{:.0f}%'.format(
            st['short_lots'], s['sell_trigger'], BUYBACK_TRIGGER_MULT, EMERGENCY_BUYBACK_PCT * 100))
    else:
        reason = st.get('do_short', False) is False and s.get('short_reason', s.get('blocked_reason', '未知')) or '未知'
        _log('  反T: ❌ {}'.format(reason))

    # 正T
    if st.get('do_long'):
        _log('  正T: ✅ {}手 买入Y{:.2f} 卖出Y{:.2f}(买价+{:.1f}%)'.format(
            st['long_lots'], s.get('buy_trigger', 0),
            s.get('sellback_target_hint', 0), SELLBACK_RISE_PCT * 100))
    else:
        _log('  正T: ❌ {}'.format(st.get('long_reason', '未知')))


def _bt_simulate_intraday(ContextInfo, open_p, high_p, low_p, close_p):
    """回测: 用当日OHLC模拟日内路径 → 驱动状态机

    run_time()在回测中不工作, ontimer永远不会触发。
    因此用 handlebar 中的 OHLC 数据模拟一条简化日内价格路径:
      open → high → low → close
    这个顺序确保: 先见峰值(SPIKING→SELL) / 先见低谷(BT_DIPPING→BUY)
    """
    st = ContextInfo.st
    do_short = st.get('do_short', False)
    do_long = st.get('do_long', False)
    if not do_short and not do_long:
        return

    # 统一路径: open→high→low→close (先极端后回归)
    path = [('开', open_p), ('高', high_p), ('低', low_p), ('收', close_p)]

    traded = False
    for label, price in path:
        fstate = st['fstate']

        if fstate in (STATE_DONE, STATE_FORCED):
            tc_s = st.get('trade_count_short', 0)
            tc_l = st.get('trade_count_long', 0)
            if ((do_short and tc_s < MAX_DAILY_TRADES) or
                (do_long and tc_l < MAX_DAILY_TRADES)):
                _maybe_resume_trading(ContextInfo)
                fstate = st['fstate']
            else:
                break

        fstate = st['fstate']
        if fstate == STATE_IDLE:
            _handle_idle(ContextInfo, price)
        elif fstate == STATE_SPIKING:
            _handle_spiking(ContextInfo, price)
        elif fstate == STATE_SOLD:
            _handle_sold(ContextInfo, price)
        elif fstate == STATE_DIPPING:
            _handle_dipping(ContextInfo, price)
        elif fstate == STATE_BT_DIPPING:
            _handle_bt_dipping(ContextInfo, price)
        elif fstate == STATE_BT_BOUGHT:
            _handle_bt_bought(ContextInfo, price)
        elif fstate == STATE_BT_SPIKING:
            _handle_bt_spiking(ContextInfo, price)

        if st.get('fstate') != fstate:
            traded = True

    # 尾盘强制平仓
    if ENABLE_FORCE_CLOSE:
        f = st['fstate']
        if f in (STATE_SOLD, STATE_DIPPING):
            _force_buyback(ContextInfo)
            traded = True
        elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
            _do_bt_force_sell(ContextInfo)
            traded = True

    if traded and st.get('total_t_days', 0) > 0:
        _log('[日末] {}笔累计 毛利~¥{:,.0f}'.format(st['total_t_days'], st['total_pnl']))


# ============================================================================
# 第六部分：ontimer — 状态机驱动 (盘中每秒触发)
# ============================================================================

def ontimer(ContextInfo):
    """盘中状态机 (仅实盘运行; 回测由 _bt_simulate_intraday 接管)"""
    st = ContextInfo.st
    signal = st.get('daily_signal')
    if signal is None:
        return

    do_short = st.get('do_short', False)
    do_long = st.get('do_long', False)
    if not do_short and not do_long:
        return

    is_live = ContextInfo.is_last_bar()
    # 回测: ontimer不跑, handlebar中的 _bt_simulate_intraday 接管全部日内逻辑
    if not is_live:
        return

    now = _now()
    if not _is_market_open(now):
        return

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        # DONE状态但还有剩余交易次数 → 恢复
        if now < '14:57:00':
            tc_s = st.get('trade_count_short', 0)
            tc_l = st.get('trade_count_long', 0)
            if ((do_short and tc_s < MAX_DAILY_TRADES) or
                (do_long and tc_l < MAX_DAILY_TRADES)):
                _maybe_resume_trading(ContextInfo)
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
    now_ts = _time()

    # 锁仓评估 (仅在IDLE状态)
    if fstate == STATE_IDLE:
        _assess_strength(ContextInfo, price, now_ts)

    # 状态路由
    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price)
    elif fstate == STATE_BT_DIPPING:
        _handle_bt_dipping(ContextInfo, price)
    elif fstate == STATE_BT_BOUGHT:
        _handle_bt_bought(ContextInfo, price)
    elif fstate == STATE_BT_SPIKING:
        _handle_bt_spiking(ContextInfo, price)

    # 卖出后计时
    if st['fstate'] in (STATE_SOLD, STATE_DIPPING):
        st['sell_elapsed_bars'] = st.get('sell_elapsed_bars', 0) + 1

    # 尾盘强制平仓
    if ENABLE_FORCE_CLOSE and now >= FORCE_CLOSE_TIME:
        f = st['fstate']
        if f in (STATE_SOLD, STATE_DIPPING):
            _log('[尾盘] {} 强制买回'.format(now))
            _force_buyback(ContextInfo)
        elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
            _log('[尾盘] {} 强制卖出正T'.format(now))
            _do_bt_force_sell(ContextInfo)
        elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE):
            st['fstate'] = STATE_DONE

    # 止损检查
    if fstate == STATE_SOLD and not st.get('stop_loss_hit', False):
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st.get('day_pnl', 0) < -loss_limit:
            _log('[止损] 反T亏损 ¥{:.0f} > ¥{:.0f}({:.1f}%) 强制买回'.format(
                st['day_pnl'], loss_limit, STOP_LOSS_PCT * 100))
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 第七部分：状态处理 — 反T
# ============================================================================

def _handle_idle(ContextInfo, price):
    st = ContextInfo.st
    signal = st.get('daily_signal', {})

    # 反T: 检查是否触发卖出
    if st.get('do_short', False) and not st.get('locked', False):
        trigger = signal.get('sell_trigger', 999999)
        if price >= trigger:
            can_use = st.get('base_can_use', st['base_shares'])
            if can_use < TRADE_LOT_SIZE:
                return
            tc = st.get('trade_count_short', 0)
            if tc >= MAX_DAILY_TRADES:
                return
            st['trade_count_short'] = tc + 1
            st['fstate'] = STATE_SPIKING
            st['peak_price'] = price
            st['state_enter_time'] = _now()
            _log('[反T冲高 #{}/{}] ¥{:.2f} >= ¥{:.2f}(+{:.2f}%)'.format(
                tc + 1, MAX_DAILY_TRADES, price, trigger,
                (price - trigger) / trigger * 100))
            return

    # 正T: 检查是否触发买入
    if st.get('do_long', False):
        buy_trigger = signal.get('buy_trigger', 0)
        if price <= buy_trigger:
            tc = st.get('trade_count_long', 0)
            if tc >= MAX_DAILY_TRADES:
                return
            st['trade_count_long'] = tc + 1
            st['fstate'] = STATE_BT_DIPPING
            st['bt_dip_price'] = price
            st['bt_buy_trigger'] = buy_trigger
            st['state_enter_time'] = _now()
            _log('[正T探底 #{}/{}] ¥{:.2f} <= ¥{:.2f}(-{:.2f}%)'.format(
                tc + 1, MAX_DAILY_TRADES, price, buy_trigger,
                (buy_trigger - price) / buy_trigger * 100))


def _handle_spiking(ContextInfo, price):
    st = ContextInfo.st
    if price > st['peak_price']:
        st['peak_price'] = price

    peak = st['peak_price']
    pullback = (peak - price) / peak if peak > 0 else 0

    if pullback >= PULLBACK_PCT:
        _log('[反T卖出] 峰值 ¥{:.2f} 回落 {:.2f}% → ¥{:.2f}'.format(peak, pullback * 100, price))

        atr_pct = st['daily_signal']['atr_pct']
        buyback_pct = atr_pct * BUYBACK_TRIGGER_MULT
        buyback_target = round(price * (1.0 - buyback_pct), 2)

        st['sell_fill_price'] = price
        st['buyback_target'] = buyback_target
        st['buyback_target_pct'] = buyback_pct * 100
        st['sell_elapsed_bars'] = 0
        st['state_enter_time'] = _now()

        _mini_sell(ContextInfo, price)
        st['fstate'] = STATE_SOLD

        _log('  ┌─ 动态买回参数 ─────────────────────')
        _log('  │ 卖出成交价: ¥{:.2f}'.format(price))
        _log('  │ ATR%={:.2f}% × 乘数{} = 回撤阈值{:.2f}%'.format(
            atr_pct * 100, BUYBACK_TRIGGER_MULT, buyback_pct * 100))
        _log('  │ 买回触发线: ¥{:.2f} (比卖价跌¥{:.2f})'.format(buyback_target, price - buyback_target))
        _log('  │ 回升确认线: 最低点 +{:.2f}% → 执行买回'.format(BOUNCE_PCT * 100))
        _log('  │ 紧急买回线: ¥{:.2f} (卖价+{:.1f}%)'.format(
            round(price * (1 + EMERGENCY_BUYBACK_PCT), 2), EMERGENCY_BUYBACK_PCT * 100))
        _log('  └─────────────────────────────────────')

    # 假突破回退
    elif price < st['daily_signal'].get('sell_trigger', 999999):
        _log('[假突破] ¥{:.2f} 跌回触发线下 | 最高触及 ¥{:.2f}'.format(price, peak))
        st['fstate'] = STATE_IDLE
        st['peak_price'] = 0.0
        st['state_enter_time'] = _now()


def _handle_sold(ContextInfo, price):
    st = ContextInfo.st
    sp = st['sell_fill_price']
    bt = st['buyback_target']

    # 条件1: 紧急买回
    emergency_line = sp * (1.0 + EMERGENCY_BUYBACK_PCT)
    if price >= emergency_line:
        rise_pct = (price - sp) / sp * 100
        _log('[紧急买回] 卖飞防护! 卖价¥{:.2f} → 现价¥{:.2f}(+{:.2f}%)'.format(sp, price, rise_pct))
        _mini_buyback(ContextInfo, price, '紧急')
        return

    # 条件2: 买回线动态收紧 (卖出后超30秒未触发)
    tightened_bt = bt
    if st.get('sell_elapsed_bars', 0) > 30 and price > sp * 0.995:
        tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] *
                             BUYBACK_TRIGGER_MULT * BUYBACK_TIGHTEN_MULT)
        tightened_bt = round(max(tightened_bt, bt), 2)

    # 条件3: 跌到买回触发线
    if price <= tightened_bt:
        drop_pct = (sp - price) / sp * 100
        st['fstate'] = STATE_DIPPING
        st['dip_price'] = price
        st['state_enter_time'] = _now()
        tag = '(收紧)' if tightened_bt > bt else ''
        _log('[买回触发{}] ¥{:.2f}(-{:.2f}%) ≤ ¥{:.2f}'.format(tag, price, drop_pct, tightened_bt))


def _handle_dipping(ContextInfo, price):
    st = ContextInfo.st
    if price < st['dip_price']:
        st['dip_price'] = price

    dip = st['dip_price'] or price
    bounce = (price - dip) / dip if dip > 0 else 0

    if bounce >= BOUNCE_PCT:
        sp = st['sell_fill_price']
        gross = (sp - price) * TRADE_LOT_SIZE
        net_est = gross - 70
        _log('[反T买回] 低¥{:.2f} 回{:.2f}% → ¥{:.2f} 毛利~¥{:,.0f} 净利≈¥{:,.0f}'.format(
            dip, bounce * 100, price, gross, net_est))
        _mini_buyback(ContextInfo, price, '正常')
        st['total_t_days'] += 1
        st['total_pnl'] += gross

    # 假跌破回退
    elif price > st['buyback_target']:
        _log('[假跌破] ¥{:.2f} 涨回触发线上 ¥{:.2f} | 最低触及 ¥{:.2f}'.format(
            price, st['buyback_target'], dip))
        st['fstate'] = STATE_SOLD
        st['dip_price'] = 0.0
        st['state_enter_time'] = _now()


# ============================================================================
# 第八部分：状态处理 — 正T
# ============================================================================

def _handle_bt_dipping(ContextInfo, price):
    st = ContextInfo.st
    if price < st.get('bt_dip_price', price):
        st['bt_dip_price'] = price

    dip = st.get('bt_dip_price', price) or price
    bounce = (price - dip) / dip if dip > 0 else 0

    if bounce >= BOUNCE_PCT:
        _log('[正T买入] 低¥{:.2f} 回{:.2f}% → ¥{:.2f}'.format(dip, bounce * 100, price))
        need = price * TRADE_LOT_SIZE * 1.001
        avail = _cash(ContextInfo)
        if avail < need:
            _log('[正T买入失败] 资金不足 需¥{:,.0f}>可用¥{:,.0f}'.format(need, avail))
            st['fstate'] = STATE_IDLE
            return
        _mini_buy(ContextInfo, price)
        st['fstate'] = STATE_BT_BOUGHT
        st['bt_buy_fill_price'] = price
        st['bt_sellback_target'] = round(price * (1.0 + SELLBACK_RISE_PCT), 2)
        _log('  卖出目标: ¥{:.2f} (+{:.2f}%)'.format(
            st['bt_sellback_target'], SELLBACK_RISE_PCT * 100))


def _handle_bt_bought(ContextInfo, price):
    st = ContextInfo.st
    target = st.get('bt_sellback_target', 999999)
    bp = st.get('bt_buy_fill_price', 0)

    # 止损
    if bp > 0 and price <= bp * (1.0 - STOP_LOSS_PCT):
        _log('[正T止损] 买¥{:.2f} 现¥{:.2f}({:.1f}%)'.format(bp, price, (price - bp) / bp * 100))
        _do_bt_force_sell(ContextInfo)
        return

    # 动态更新买入追踪线 (用于IDLE恢复后的触发)
    bt_floor = st['daily_signal'].get('buy_trigger_floor', 0)
    bt_trail = round(price * (1.0 - BUY_TRIGGER_TRAIL), 2)
    st['bt_max_trail'] = max(st.get('bt_max_trail', 0), bt_trail)
    st['daily_signal']['buy_trigger'] = max(bt_floor, st['bt_max_trail'])

    if price >= target:
        st['fstate'] = STATE_BT_SPIKING
        st['bt_sell_peak_price'] = price
        _log('[正T卖回监控] +{:.2f}% → ¥{:.2f}'.format((price - bp) / bp * 100, price))


def _handle_bt_spiking(ContextInfo, price):
    st = ContextInfo.st
    if price > st.get('bt_sell_peak_price', price):
        st['bt_sell_peak_price'] = price

    peak = st.get('bt_sell_peak_price', price)
    pullback = (peak - price) / peak if peak > 0 else 0

    if pullback >= PULLBACK_PCT:
        bp = st['bt_buy_fill_price']
        gross = (price - bp) * TRADE_LOT_SIZE
        net_est = gross - 70
        _log('[正T卖出] 峰值¥{:.2f} 回落{:.2f}% → ¥{:.2f} 毛利~¥{:,.0f} 净利≈¥{:,.0f}'.format(
            peak, pullback * 100, price, gross, net_est))
        _mini_sell_bt(ContextInfo, price)
        st['fstate'] = STATE_DONE
        st['total_t_days'] += 1
        st['total_pnl'] += gross
        _maybe_resume_trading(ContextInfo)


# ============================================================================
# 第九部分：下单函数
# ============================================================================

def _mini_sell(ContextInfo, price):
    """反T卖出"""
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log('  >>> 下单卖出: ¥{:.2f} × {}股 = ¥{:,.0f}'.format(price, TRADE_LOT_SIZE, price * TRADE_LOT_SIZE))
    except Exception as e:
        _log('  >>> 卖出失败: {}'.format(e))
        ContextInfo.st['fstate'] = STATE_IDLE


def _mini_buyback(ContextInfo, price, reason=''):
    """反T买回"""
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)
    if avail < need:
        _log('  >>> 买回失败({}): 资金不足 需¥{:,.0f}>可用¥{:,.0f}'.format(reason, need, avail))
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log('  >>> 下单买回({}): ¥{:.2f} × {}股 = ¥{:,.0f}'.format(reason, price, TRADE_LOT_SIZE, price * TRADE_LOT_SIZE))
        st['fstate'] = STATE_DONE
    except Exception as e:
        _log('  >>> 买回失败({}): {}'.format(reason, e))


def _force_buyback(ContextInfo):
    """尾盘强制买回 (对手价)"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', 0, ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log('[尾盘买回] 对手价 × {}股 — 底仓已恢复'.format(TRADE_LOT_SIZE))
    except Exception as e:
        _log('[尾盘失败!!] {} — 请手动买回{}股!'.format(e, TRADE_LOT_SIZE))
        st['fstate'] = STATE_FORCED


def _mini_buy(ContextInfo, price):
    """正T买入"""
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log('  >>> 下单正T买入: ¥{:.2f} × {}股 = ¥{:,.0f}'.format(price, TRADE_LOT_SIZE, price * TRADE_LOT_SIZE))
    except Exception as e:
        _log('  >>> 正T买入失败: {}'.format(e))
        ContextInfo.st['fstate'] = STATE_IDLE


def _mini_sell_bt(ContextInfo, price):
    """正T卖出"""
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log('  >>> 下单正T卖出: ¥{:.2f} × {}股 = ¥{:,.0f}'.format(price, TRADE_LOT_SIZE, price * TRADE_LOT_SIZE))
    except Exception as e:
        _log('  >>> 正T卖出失败: {}'.format(e))


def _do_bt_force_sell(ContextInfo):
    """正T强制卖出"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', 0, ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log('[正T强制卖出] 对手价 × {}股'.format(TRADE_LOT_SIZE))
    except Exception as e:
        _log('[正T强制卖出失败!!] {} — 请手动卖出{}股!'.format(e, TRADE_LOT_SIZE))
        st['fstate'] = STATE_FORCED


# ============================================================================
# 第十部分：锁仓 & 恢复
# ============================================================================

def _assess_strength(ContextInfo, price, now_ts):
    """评估是否应该锁仓 (强牛市中禁止反T)"""
    st = ContextInfo.st
    sig = st.get('daily_signal', {})
    open_price = sig.get('open_price', 0)
    if open_price <= 0:
        return

    # 维护价格历史
    hist = st.get('price_history', [])
    hist.append((now_ts, price))
    cutoff = now_ts - LOCK_LOOKBACK_SEC
    hist = [(t, p) for t, p in hist if t >= cutoff]
    st['price_history'] = hist

    if len(hist) < 10:
        return

    prices = [p for _, p in hist]
    p5 = prices[0]
    pn = prices[-1]
    dh = max(prices)

    cond1 = pn > open_price * (1.0 + LOCK_PRICE_RATIO)
    cond2 = (pn - p5) / p5 > LOCK_MOMENTUM_PCT if p5 > 0 else False
    cond3 = (dh - pn) / dh < LOCK_DRAWDOWN_PCT if dh > 0 else False
    should_lock = cond1 and cond2 and cond3

    cool_ok = now_ts >= st.get('lock_cooldown_until', 0)

    if should_lock and not st.get('locked'):
        st['locked'] = True
        st['lock_since'] = _now()
        st['lock_reason'] = '+{:.1f}% M{:.2f}% D{:.2f}%'.format(
            (pn / open_price - 1) * 100,
            (pn - p5) / p5 * 100,
            (dh - pn) / dh * 100 if dh > 0 else 0)
        _log('[锁仓] {} 强牛动量 → 暂停反T'.format(st['lock_reason']))
    elif not should_lock and st.get('locked') and cool_ok:
        st['locked'] = False
        st['lock_reason'] = ''
        st['lock_since'] = ''
        st['lock_cooldown_until'] = 0.0
        _log('[解锁] 锁仓条件解除 → 恢复反T')
    elif should_lock:
        st['lock_cooldown_until'] = 0.0
    elif st.get('locked') and st.get('lock_cooldown_until', 0) == 0.0:
        st['lock_cooldown_until'] = now_ts + LOCK_COOLDOWN_SEC


def _maybe_resume_trading(ContextInfo):
    """一笔交易完成后, 检查是否可以继续"""
    st = ContextInfo.st
    tc_s = st.get('trade_count_short', 0)
    tc_l = st.get('trade_count_long', 0)
    do_short = st.get('do_short', False)
    do_long = st.get('do_long', False)

    can_s = do_short and tc_s < MAX_DAILY_TRADES
    can_l = do_long and tc_l < MAX_DAILY_TRADES

    if can_s or can_l:
        # 刷新持仓
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                st['base_shares'] = pos.m_nVolume
                st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                st['base_cost'] = pos.m_dOpenPrice
                break

        st['fstate'] = STATE_IDLE
        st['peak_price'] = 0.0
        st['dip_price'] = 0.0
        st['sell_fill_price'] = 0.0
        st['buyback_target'] = 0.0
        st['state_enter_time'] = _now()
        st['stop_loss_hit'] = False

        parts = []
        if can_s: parts.append('反T{}/{}'.format(tc_s, MAX_DAILY_TRADES))
        if can_l: parts.append('正T{}/{}'.format(tc_l, MAX_DAILY_TRADES))
        _log('[恢复] → IDLE ({})'.format(', '.join(parts)))
    else:
        _log('[完成] {}/{}笔已达上限'.format(tc_s + tc_l, MAX_DAILY_TRADES * 2))


# ============================================================================
# 第十一部分：回调 & 工具
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    if order.m_nOrderStatus in sm:
        _log('[委托] ¥{:.2f} {}/{}股 → {}'.format(
            order.m_dOrderPrice, order.m_nVolumeTraded, order.m_nVolumeTotal,
            sm[order.m_nOrderStatus]))


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    d = '买入' if deal.m_nDirection == 1 else '卖出'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    st['day_pnl'] += (amt - fee) if deal.m_nDirection == 2 else -(amt + fee)
    _log('[成交] {} ¥{:.2f} × {}股 = ¥{:,.0f} | 佣金¥{:.2f} | PnL≈¥{:,.0f}'.format(
        d, deal.m_dPrice, deal.m_nVolume, amt, fee, st['day_pnl']))


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log('')
        _log('=' * 55)
        _log('  {} v6.0 完整版 策略已停止'.format(STOCK_NAME))
        _log('  累计做T: {}笔 | 盈亏: ¥{:,.0f}'.format(
            st.get('total_t_days', 0), st.get('total_pnl', 0)))
        if st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
            _log('  ⚠⚠ 反T未买回{}股! 请手动补仓!'.format(TRADE_LOT_SIZE))
        if st.get('fstate') in (STATE_BT_BOUGHT, STATE_BT_SPIKING):
            _log('  ⚠⚠ 正T未卖出{}股! 请手动处理!'.format(TRADE_LOT_SIZE))
        _log('=' * 55)


def _cash(ContextInfo):
    try:
        a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception:
        return 0.0


def _acc(ContextInfo):
    return ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else ACCOUNT


def _now():
    import time as _t
    return _t.strftime('%H:%M:%S')


def _time():
    import time as _t
    return _t.time()


def _ts():
    import time as _t
    return _t.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    ts = _ts()
    if args:
        print('{} {}'.format(ts, args[0]), *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
