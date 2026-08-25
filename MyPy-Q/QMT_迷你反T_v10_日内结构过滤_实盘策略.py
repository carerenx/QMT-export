# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你反T策略 v10.0 — 日内结构过滤器版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【v10.0 — 基于5分钟K线回测的日内结构过滤器】
  5分钟K线回测(7/13~27)发现:
    - HIGH→LOW(先涨后跌)日: 胜率67%, 盈亏+104  → 策略有效
    - LOW→HIGH(先跌后涨)日: 胜率40%, 盈亏-1,431 → 策略天然失效
    - 日线OHLC回测将盈亏高估了1,555%

  v10方案: 开盘30分钟观察期 → 判断日内走势结构 → 仅在有利结构中执行反T

  新增功能:
  ┌────────┬──────────────────────────────────────┬──────────────────────────┐
  │ 功能    │ 说明                                  │ 效果                     │
  ├────────┼──────────────────────────────────────┼──────────────────────────┤
  │ 早盘观察│ 开盘后30分钟(09:30-10:00)观察走势      │ 判断HIGH先还是LOW先      │
  │ GAP过滤 │ 开盘跳空下跌>1% → 当日大概率LOW→HIGH   │ 直接跳过, 0交易          │
  │ 方向过滤│ 30分钟后仍跌>1% → 结构不利            │ 跳过反T                  │
  │ 震荡豁免│ 30分钟内振幅<2% → 无法判断, 允许交易   │ 不误杀震荡市             │
  │ 单日限额│ 单日最大亏损800元                      │ 防止极端行情             │
  │ 连亏熔断│ 连续2日亏损 → 暂停1天                 │ 避免情绪化连续亏损       │
  └────────┴──────────────────────────────────────┴──────────────────────────┘

  继承v9所有修复: 动态收紧max修复, dip==0防御, 模块级time import等
================================================================================
"""
import time as _time

# ============================================================================
# 第一部分：全局配置
# ============================================================================
ACCOUNT = '8890145315'

STOCK_CODE = '601869'
STOCK_NAME = '长飞光纤'
STOCK_QMT  = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE = 100
MIN_LOT        = 100
TIMER_INTERVAL = '1nSecond'

# ---- 卖出触发线 ----
ATR_PERIOD = 14

SELL_TRIGGER_BASE_BEAR      = 0.40
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55
SELL_TRIGGER_BASE_WEAK_BULL = 0.65

DYNAMIC_MULT_MIN = 0.20
DYNAMIC_MULT_MAX = 1.50

DAILY_RANGE_CAP_ENABLED = True
DAILY_RANGE_CAP_MULT    = 0.80

# ---- 冲高回落确认 ----
PULLBACK_PCT = 0.0010

# ---- 买回触发 ----
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT           = 0.0010
BUYBACK_TIGHTEN_MULT = 0.60

# ---- 熔断 & 过滤 ----
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI     = 70
STRONG_BULL_STREAK  = 5

# ---- 紧急买回 & 止损 ----
EMERGENCY_BUYBACK_PCT = 0.03
STOP_LOSS_PCT         = 0.015

# ---- 时间 & 数据 & 费用 ----
FORCE_CLOSE_TIME = '14:57:00'
HIST_DATA_LEN    = 80
COMMISSION       = 0.00025
STAMP_TAX        = 0.001

# ============================================================================
# ★ v10新增: 日内结构过滤器参数
# ============================================================================
# 原理: 开盘后前30分钟观察价格走势, 判断日内结构是HIGH→LOW还是LOW→HIGH
# 仅在有利结构(HIGH→LOW概率高)中执行反T

MORNING_OBSERVE_MINUTES = 30          # 早盘观察时长(分钟), 默认30分钟
MORNING_OBSERVE_BARS    = 6           # 对应的5分钟K线数(30÷5=6)
MORNING_DECISION_TIME   = '10:00:00'  # 观察期结束时间(在此之后才允许交易)

# 过滤条件:
MORNING_GAP_DOWN_PCT = -0.01          # 开盘30分钟后跌超1% → 当日大概率LOW→HIGH, 跳过
MORNING_RANGE_MIN_PCT = 0.02          # 30分钟内振幅<2% → 震荡市, 无法判断结构, 允许交易
MORNING_DIP_THRESHOLD = 0.015         # 30分钟内曾跌超1.5%(从开盘算) → 结构风险, 跳过

# 风控:
DAILY_MAX_LOSS = -800                 # 单日最大亏损限额(元)
CONSECUTIVE_LOSS_LIMIT = 2            # 连续亏损天数上限, 超过则暂停1天


# ============================================================================
# 第二部分：技术指标 (同v9)
# ============================================================================

def _sma(values, period):
    n = len(values); r = [0.0] * n
    for i in range(period - 1, n):
        r[i] = sum(values[i - period + 1 : i + 1]) / period
    return r

def _atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    r = [0.0] * n
    for i in range(period, n): r[i] = sum(tr[i - period + 1 : i + 1]) / period
    return r

def _rsi(closes, period=14):
    n = len(closes)
    if n < period + 1: return [50.0] * n
    rsi = [50.0] * n; g, l = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]; g.append(d if d > 0 else 0); l.append(abs(d) if d < 0 else 0)
    ag = sum(g[:period]) / period; al = sum(l[:period]) / period
    rsi[period] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    for i in range(period, n - 1):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    return rsi

def _up_streak(closes):
    n = len(closes); s = [0] * n
    for i in range(1, n): s[i] = s[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return s

def _daily_range_ma(highs, lows, opens, period=10):
    n = len(opens); ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0: ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)


# ============================================================================
# 第三部分：动态乘数模型 (同v8/v9)
# ============================================================================

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    """计算动态卖出乘数 — 4因子模型 + 自适应BASE"""
    if trend == 'bear':
        base = SELL_TRIGGER_BASE_BEAR
    elif trend == 'weak_bull':
        base = SELL_TRIGGER_BASE_WEAK_BULL
    else:
        base = SELL_TRIGGER_BASE_SIDEWAYS

    deviations = {}
    total_deviation = 0.0

    # 因子1: 趋势
    if trend == 'bear':
        d = -0.25 if up_streak == 0 else -0.15
    elif trend == 'strong_bull':
        d = +999
    elif trend == 'weak_bull':
        if up_streak >= 3: d = +0.20
        elif up_streak >= 1: d = +0.12
        else: d = +0.05
    else:
        d = 0.00
    deviations['趋势'] = d; total_deviation += d

    # 因子2: 波动率
    if atr_pct > 0.08:     atr_d = -0.30
    elif atr_pct > 0.07:   atr_d = -0.22
    elif atr_pct > 0.06:   atr_d = -0.15
    elif atr_pct > 0.05:   atr_d = -0.08
    elif atr_pct > 0.03:   atr_d = +0.05
    elif atr_pct > 0.02:   atr_d = +0.15
    else:                  atr_d = +0.25

    if atr_ratio > 1.50:     atrd_d = -0.25
    elif atr_ratio > 1.25:   atrd_d = -0.18
    elif atr_ratio > 1.10:   atrd_d = -0.10
    elif atr_ratio > 0.90:   atrd_d = 0.00
    elif atr_ratio > 0.70:   atrd_d = +0.12
    elif atr_ratio > 0.50:   atrd_d = +0.20
    else:                    atrd_d = +0.25

    vol_d = atr_d * 0.55 + atrd_d * 0.45
    vol_d = max(-0.35, min(0.30, vol_d))
    deviations['波动率'] = round(vol_d, 2); total_deviation += vol_d

    # 因子3: 成交量
    if vol_ratio > 2.00:     d = -0.25
    elif vol_ratio > 1.50:   d = -0.18
    elif vol_ratio > 1.20:   d = -0.08
    elif vol_ratio > 0.80:   d = 0.00
    elif vol_ratio > 0.60:   d = +0.12
    elif vol_ratio > 0.40:   d = +0.20
    else:                    d = +0.25
    deviations['成交量'] = d; total_deviation += d

    # 因子4: RSI
    if rsi_val > 80:         d = -0.25
    elif rsi_val > 70:       d = -0.18
    elif rsi_val > 60:       d = -0.08
    elif rsi_val > 55:       d = -0.03
    elif rsi_val > 45:       d = 0.00
    elif rsi_val > 40:       d = +0.03
    elif rsi_val > 30:       d = +0.10
    elif rsi_val > 20:       d = +0.20
    else:                    d = +0.25
    deviations['RSI'] = d; total_deviation += d

    final = base + total_deviation
    final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final))
    return round(final, 2), deviations, base


# ============================================================================
# 第四部分：信号计算 (同v9)
# ============================================================================

def compute_signal(opens, highs, lows, closes, volumes):
    """计算当日反T信号"""
    n = len(closes)
    if n < 60: return None

    co = opens[-1]; cc = closes[-1]; cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    ma5  = _sma(closes, 5)[-1]; ma20 = _sma(closes, 20)[-1]
    curr_rsi = _rsi(closes)[-1]; up_streak = _up_streak(closes)[-1]

    is_bull = cc > ma20 and ma5 > ma20
    is_bear = cc < ma20 and ma5 < ma20

    if is_bull and curr_rsi > STRONG_BULL_RSI and up_streak >= STRONG_BULL_STREAK:
        trend = 'strong_bull'
    elif is_bull:
        trend = 'weak_bull'
    elif is_bear:
        trend = 'bear'
    else:
        trend = 'sideways'

    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    sell_mult, factor_details, base_used = calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak
    )

    daily_range_ma10 = _daily_range_ma(highs, lows, opens, 10)[-1]
    max_trigger = co * (1.0 + daily_range_ma10 * DAILY_RANGE_CAP_MULT)
    range_capped = False

    sell_trigger_raw = co + curr_atr * sell_mult
    if DAILY_RANGE_CAP_ENABLED and sell_trigger_raw > max_trigger:
        sell_trigger = round(max_trigger, 2)
        range_capped = True
    else:
        sell_trigger = round(sell_trigger_raw, 2)

    do_short, reason = True, ''
    if trend == 'strong_bull':
        do_short, reason = False, '强牛禁反T(连涨>=5+RSI>70)'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short, reason = False, f'缩量(量比{curr_vr:.2f})'
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short, reason = False, f'RSI超买({curr_rsi:.0f})'

    return {
        'do_short':           do_short,
        'blocked_reason':     reason,
        'trend':              trend,
        'sell_trigger':       sell_trigger,
        'sell_trigger_raw':   round(sell_trigger_raw, 2),
        'range_capped':       range_capped,
        'daily_range_ma10':   daily_range_ma10,
        'open_price':         co,
        'close_yday':         cc,
        'atr':                curr_atr,
        'atr_pct':            curr_atr_pct,
        'rsi':                curr_rsi,
        'vol_ratio':          curr_vr,
        'sell_mult':          sell_mult,
        'sell_mult_base':     base_used,
        'factor_details':     factor_details,
        'atr_ratio':          atr_ratio,
        'up_streak':          up_streak,
        'buyback_mult':       BUYBACK_TRIGGER_MULT,
        'bounce_pct':         BOUNCE_PCT,
    }


# ============================================================================
# 第五部分：QMT 策略入口
# ============================================================================

STATE_IDLE    = 'IDLE'
STATE_SPIKING = 'SPIKING'
STATE_SOLD    = 'SOLD'
STATE_DIPPING = 'DIPPING'
STATE_DONE    = 'DONE'
STATE_FORCED  = 'FORCED'
STATE_WAITING = 'WAITING'     # ★ v10新增: 等待早盘观察期结束


def init(ContextInfo):
    """QMT策略初始化"""
    try:
        ContextInfo.set_universe([STOCK_QMT])
    except Exception:
        pass
    try:
        ContextInfo.set_account(ACCOUNT)
    except Exception:
        pass

    state = {
        'daily_signal': None,
        'base_shares': 0, 'base_cost': 0.0,
        'fstate': STATE_IDLE,
        'peak_price': 0.0, 'dip_price': 0.0,
        'sell_fill_price': 0.0, 'buyback_target': 0.0, 'buyback_target_pct': 0.0,
        'day_pnl': 0.0, 'stop_loss_hit': False,
        'total_t_days': 0, 'total_pnl': 0.0,
        'entry_price': 0.0,
        'startup_printed': False,
        'state_enter_time': '',
        'sell_elapsed_bars': 0,

        # ★ v10新增: 日内结构过滤
        'morning_open_price': 0.0,          # 今日开盘价(用于早盘涨跌计算)
        'morning_lowest': 0.0,              # 早盘观察期内最低价
        'morning_highest': 0.0,             # 早盘观察期内最高价
        'morning_bars_count': 0,            # 早盘观察期已过bar数
        'morning_approved': False,          # 早盘观察通过? True=结构有利,允许交易
        'morning_blocked_reason': '',       # 如果不通过, 原因是什么

        # ★ v10新增: 风控
        'consecutive_loss_days': 0,         # 连续亏损天数
        'skip_today': False,                # 今日是否跳过交易(熔断用)
    }
    ContextInfo.st = state
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


def handlebar(ContextInfo):
    """日线回调 — 计算信号 + 重置状态 + ★v10早盘观察初始化"""
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

    base_shares = 0; base_cost = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume; base_cost = pos.m_dOpenPrice; break

    if base_shares < TRADE_LOT_SIZE:
        if is_live: _log(f'[警告] 底仓不足1手({base_shares}股)')
        st['base_shares'] = 0; return

    st['base_shares'] = base_shares; st['base_cost'] = base_cost
    if st['entry_price'] == 0.0: st['entry_price'] = base_cost

    curr_close  = closes[STOCK_QMT][-1]
    avail_cash  = accounts[0].m_dAvailable if accounts else 0.0
    pos_value   = base_shares * curr_close

    signal = compute_signal(
        opens[STOCK_QMT], highs[STOCK_QMT], lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None: return
    st['daily_signal'] = signal

    # ── 重置状态机 ──
    st['fstate']             = STATE_IDLE
    st['peak_price']         = 0.0
    st['dip_price']          = 0.0
    st['sell_fill_price']    = 0.0
    st['buyback_target']     = 0.0
    st['buyback_target_pct'] = 0.0
    st['day_pnl']            = 0.0
    st['stop_loss_hit']      = False
    st['state_enter_time']   = _now()
    st['sell_elapsed_bars']  = 0

    # ★ v10: 早盘观察期初始化
    st['morning_open_price'] = 0.0
    st['morning_lowest']     = 999999.0
    st['morning_highest']    = 0.0
    st['morning_bars_count'] = 0
    st['morning_approved']   = False
    st['morning_blocked_reason'] = ''

    # ★ v10: 连亏熔断检查
    if st['consecutive_loss_days'] >= CONSECUTIVE_LOSS_LIMIT:
        st['skip_today'] = True
        st['consecutive_loss_days'] = 0  # 休息一天, 重置计数
    else:
        st['skip_today'] = False

    # ── 日志 ──
    if is_live:
        _print_signal(ContextInfo, curr_close, avail_cash, pos_value)
    elif not st['startup_printed']:
        _log(f'{"="*55}')
        _log(f'  {STOCK_NAME} v10.0 日内结构过滤器 — 已加载')
        _log(f'  早盘观察: {MORNING_OBSERVE_MINUTES}分钟 | 决策时间: {MORNING_DECISION_TIME}')
        _log(f'  过滤条件: 跌>{abs(MORNING_GAP_DOWN_PCT)*100:.0f}%跳过 | 振幅<{MORNING_RANGE_MIN_PCT*100:.0f}%放行')
        _log(f'  风控: 单日限亏{DAILY_MAX_LOSS}元 | 连亏{CONSECUTIVE_LOSS_LIMIT}天熔断')
        _log(f'{"="*55}')
        st['startup_printed'] = True


def _print_signal(ContextInfo, curr_close, avail_cash, pos_value):
    """打印信号和状态"""
    s  = ContextInfo.st['daily_signal']
    st = ContextInfo.st
    cost = st['entry_price']

    unreal_pnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_close / cost - 1) * 100 if cost > 0 else 0
    total_val  = pos_value + avail_cash

    _log(f'━━━ {"账户 & 信号":─^30} ━━━')
    _log(f'  持仓: ¥{pos_value:,.0f} | 浮动: ¥{unreal_pnl:,.0f}({unreal_pct:+.1f}%)')
    _log(f'  开盘: ¥{s["open_price"]:.2f} | 趋势: {s["trend"]} | RSI: {s["rsi"]:.0f}')

    if st['skip_today']:
        _log(f'  [熔断] 今日跳过交易 (连亏{CONSECUTIVE_LOSS_LIMIT}天后休息1天)')
    elif s['do_short']:
        _log(f'  反T: [待早盘确认] 触发线 ¥{s["sell_trigger"]:.2f} | 等待{MORNING_DECISION_TIME}后决策')
    else:
        _log(f'  反T: [禁止] {s["blocked_reason"]}')

    if st['total_t_days'] > 0:
        _log(f'  累计反T: {st["total_t_days"]}天 | 毛利≈¥{st["total_pnl"]:,.0f}')
    if st['consecutive_loss_days'] > 0:
        _log(f'  连亏: {st["consecutive_loss_days"]}天')


# ============================================================================
# 第六部分：ontimer — ★ v10核心: 早盘观察 + 状态机
# ============================================================================

def ontimer(ContextInfo):
    """定时器回调 — ★v10: 早盘观察模式 + 正常交易模式"""
    st = ContextInfo.st
    now = _now()

    if not _is_market_open(now):
        return

    signal = st.get('daily_signal')
    if signal is None:
        return

    # ── ★ v10新增: 早盘观察模式 ──
    # 在 MORNING_DECISION_TIME 之前, 只做观察不做交易
    if now < MORNING_DECISION_TIME and not st['morning_approved']:
        _morning_observe(ContextInfo, now)
        return  # 观察期内不执行交易逻辑

    # ── 观察期结束后, 检查是否通过 ──
    if not st['morning_approved'] and st['morning_blocked_reason'] == '':
        # 观察期结束但还没做决策 → 现在决策
        _morning_decide(ContextInfo)
        if not st['morning_approved']:
            return  # 结构不利, 全天不交易

    # ── 熔断检查 ──
    if st.get('skip_today', False):
        return

    # ── 信号检查 ──
    if not signal.get('do_short'):
        return

    if st.get('base_shares', 0) < TRADE_LOT_SIZE:
        return

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return

    # ── ★ v10新增: 单日最大亏损限制 ──
    if st['day_pnl'] < DAILY_MAX_LOSS:
        if st['fstate'] in (STATE_SOLD, STATE_DIPPING):
            _log(f'[风控] 单日亏损¥{st["day_pnl"]:.0f} 超过限制¥{DAILY_MAX_LOSS}, 强制买回')
            _force_buyback(ContextInfo)
        return

    # ── 获取实时价格 ──
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

    # ── 状态路由 ──
    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price)

    # ── 卖后计时 ──
    if st['fstate'] in (STATE_SOLD, STATE_DIPPING):
        st['sell_elapsed_bars'] += 1

    # ── 尾盘 ──
    if now >= FORCE_CLOSE_TIME:
        if fstate in (STATE_SOLD, STATE_DIPPING):
            _log(f'[尾盘] {now} 强制买回')
            _force_buyback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_IDLE):
            st['fstate'] = STATE_DONE

    # ── 止损 ──
    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st['day_pnl'] < -loss_limit:
            _log(f'[止损] 亏损¥{st["day_pnl"]:.0f} > ¥{loss_limit:.0f}')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# ★ v10 核心新增: 早盘观察 & 决策
# ============================================================================

def _morning_observe(ContextInfo, now):
    """
    早盘观察模式: 跟踪开盘后前30分钟的价格走势

    在每个ontimer tick中:
      1. 记录今日开盘价(第一个tick的价格)
      2. 更新观察期内的最高/最低价
      3. 累计观察bar数

    不执行任何交易操作
    """
    st = ContextInfo.st

    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return

    if STOCK_QMT not in tick:
        return

    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0:
        return

    # 记录开盘价
    if st['morning_open_price'] == 0.0:
        st['morning_open_price'] = price
        st['morning_lowest'] = price
        st['morning_highest'] = price
        _log(f'[早盘] 开始观察 | 开盘价 ¥{price:.2f} | 决策时间 {MORNING_DECISION_TIME}')

    # 更新最高/最低
    if price < st['morning_lowest']:
        st['morning_lowest'] = price
    if price > st['morning_highest']:
        st['morning_highest'] = price

    st['morning_bars_count'] += 1

    # 每5分钟打印一次观察状态
    if st['morning_bars_count'] % 5 == 0:
        chg = (price - st['morning_open_price']) / st['morning_open_price'] * 100
        _log(f'[早盘] {now} | ¥{price:.2f}({chg:+.2f}%) | H={st["morning_highest"]:.2f} L={st["morning_lowest"]:.2f}')


def _morning_decide(ContextInfo):
    """
    早盘观察期结束, 做出日内结构判断

    决策逻辑:
      1. 开盘跳空跌>1% → 当日大概率LOW→HIGH → BLOCK
      2. 观察期内曾跌超1.5% → 有结构风险 → BLOCK
      3. 观察期内呈下跌趋势(现价<开盘-1%) → BLOCK
      4. 观察期内振幅极小(<2%) → 无法判断 → ALLOW (震荡市反T有效)
      5. 其他情况 → ALLOW

    结果写入 st['morning_approved'] 和 st['morning_blocked_reason']
    """
    st = ContextInfo.st
    open_p = st['morning_open_price']

    if open_p <= 0:
        st['morning_approved'] = True  # 无法获取开盘价, 放行
        return

    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
        current_p = tick[STOCK_QMT].get('lastPrice', open_p) if STOCK_QMT in tick else open_p
    except Exception:
        current_p = open_p

    morning_chg = (current_p - open_p) / open_p
    morning_range = (st['morning_highest'] - st['morning_lowest']) / open_p
    morning_dip = (st['morning_lowest'] - open_p) / open_p  # 负值=跌了多少

    # ── 决策 ──
    blocked = False
    reason = ''

    if morning_chg < MORNING_GAP_DOWN_PCT:
        # 条件1: 30分钟后仍在开盘价下方>1%
        blocked = True
        reason = f'早盘跌{morning_chg*100:.1f}% > {abs(MORNING_GAP_DOWN_PCT)*100:.0f}%'
    elif morning_dip < -MORNING_DIP_THRESHOLD:
        # 条件2: 曾跌超1.5%
        blocked = True
        reason = f'曾跌至¥{st["morning_lowest"]:.2f}({morning_dip*100:.1f}%) > {MORNING_DIP_THRESHOLD*100:.0f}%'
    elif morning_range < MORNING_RANGE_MIN_PCT:
        # 豁免: 振幅极小, 震荡市
        blocked = False
        reason = f'震荡豁免(振幅{morning_range*100:.1f}% < {MORNING_RANGE_MIN_PCT*100:.0f}%)'
    else:
        blocked = False
        reason = f'结构有利(涨{morning_chg*100:+.1f}% 振幅{morning_range*100:.1f}%)'

    st['morning_approved'] = not blocked
    st['morning_blocked_reason'] = reason

    if blocked:
        _log(f'[早盘决策] BLOCKED — {reason} → 今日不执行反T')
        # 如果信号允许但早盘不允许, 更新信号状态
        if st.get('daily_signal'):
            st['daily_signal']['do_short'] = False
            st['daily_signal']['blocked_reason'] = f'早盘过滤: {reason}'
    else:
        _log(f'[早盘决策] APPROVED — {reason} → 正常执行反T')


# ============================================================================
# 第七部分：状态处理函数 (同v9, 略作精简)
# ============================================================================

def _handle_idle(ContextInfo, price):
    st = ContextInfo.st; trigger = st['daily_signal']['sell_trigger']
    if price >= trigger:
        st['fstate'] = STATE_SPIKING; st['peak_price'] = price
        st['state_enter_time'] = _now()
        _log(f'[冲高] ¥{price:.2f} >= ¥{trigger:.2f} (BASE={st["daily_signal"]["sell_mult_base"]}, 乘数={st["daily_signal"]["sell_mult"]})')

def _handle_spiking(ContextInfo, price):
    st = ContextInfo.st; trigger = st['daily_signal']['sell_trigger']
    if price > st['peak_price']: st['peak_price'] = price
    peak = st['peak_price']; pullback = (peak - price) / peak
    if pullback >= PULLBACK_PCT:
        _log(f'[卖出] 最高¥{peak:.2f} 回落{pullback*100:.2f}% → ¥{price:.2f}')
        _mini_sell(ContextInfo, price)
        st['fstate'] = STATE_SOLD; st['sell_fill_price'] = price
        st['state_enter_time'] = _now()
        ap = st['daily_signal']['atr_pct']; bp = ap * st['daily_signal']['buyback_mult']
        bt = round(price * (1.0 - bp), 2)
        st['buyback_target'] = bt; st['buyback_target_pct'] = bp * 100
        st['sell_elapsed_bars'] = 0
        _log(f'  买回线: ¥{bt:.2f}({bp*100:.2f}%) | 紧急: ¥{price*(1+EMERGENCY_BUYBACK_PCT):.2f}')
    elif price < trigger:
        _log(f'[假突破] ¥{price:.2f} < ¥{trigger:.2f}')
        st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0

def _handle_sold(ContextInfo, price):
    st = ContextInfo.st; sp = st['sell_fill_price']; bt = st['buyback_target']
    # 紧急买回
    emerg_line = sp * (1.0 + EMERGENCY_BUYBACK_PCT)
    if price >= emerg_line:
        _log(f'[紧急买回] 卖¥{sp:.2f} → 现¥{price:.2f}(+{(price-sp)/sp*100:.2f}%)')
        _mini_buyback(ContextInfo, price, '紧急')
        return
    # 动态收紧 (v9修复: max)
    tightened_bt = bt
    if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
        tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] * BUYBACK_TRIGGER_MULT * BUYBACK_TIGHTEN_MULT)
        tightened_bt = round(max(tightened_bt, bt), 2)  # ★ v9修复
    if price <= tightened_bt:
        drop_pct = (sp - price) / sp * 100
        st['fstate'] = STATE_DIPPING; st['dip_price'] = price
        st['state_enter_time'] = _now()
        tag = '(收紧)' if tightened_bt > bt else ''
        _log(f'[买回触发{tag}] ¥{price:.2f}(-{drop_pct:.2f}%) <= ¥{tightened_bt:.2f}')

def _handle_dipping(ContextInfo, price):
    st = ContextInfo.st; bt = st['buyback_target']
    if price < st['dip_price']: st['dip_price'] = price
    dip = st['dip_price']
    if dip <= 0: st['dip_price'] = price; dip = price  # ★ v9修复
    bounce = (price - dip) / dip
    if bounce >= BOUNCE_PCT:
        sell_p = st['sell_fill_price']; gross = (sell_p - price) * TRADE_LOT_SIZE
        _log(f'[买回] 最低¥{dip:.2f} 回升{bounce*100:.2f}% → ¥{price:.2f} | 毛利≈¥{gross:.0f}')
        _mini_buyback(ContextInfo, price, '正常')
        st['total_t_days'] += 1; st['total_pnl'] += gross
    elif price > bt:
        _log(f'[假跌破] ¥{price:.2f} > ¥{bt:.2f}')
        st['fstate'] = STATE_SOLD; st['dip_price'] = 0.0


# ============================================================================
# 第八部分：下单 & 辅助函数
# ============================================================================

def _mini_sell(ContextInfo, price):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 卖出 ¥{price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  >>> 卖出失败: {e}'); st['fstate'] = STATE_IDLE

def _mini_buyback(ContextInfo, price, reason=''):
    st = ContextInfo.st; need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)
    if avail < need:
        _log(f'  >>> 买回失败: 资金不足 (需¥{need:,.0f} > ¥{avail:,.0f})'); return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 买回({reason}) ¥{price:.2f} × {TRADE_LOT_SIZE}股')
        st['fstate'] = STATE_DONE
    except Exception as e:
        _log(f'  >>> 买回失败({reason}): {e}')

def _force_buyback(ContextInfo):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED; _log(f'[强制买回] 已下单(对手价)')
    except Exception as e:
        _log(f'[强制买回失败!!] {e}'); st['fstate'] = STATE_FORCED

def _cash(ContextInfo):
    try:
        a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception:
        return 0.0

def _acc(ContextInfo):
    try:
        if hasattr(ContextInfo, 'accID') and ContextInfo.accID:
            return ContextInfo.accID
    except Exception:
        pass
    return ACCOUNT

# ============================================================================
# 第九部分：QMT回调
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')

def deal_callback(ContextInfo, deal):
    st = ContextInfo.st; d = '买' if deal.m_nDirection == 1 else '卖'
    amt = deal.m_dPrice * deal.m_nVolume; fee = deal.m_fCommission + deal.m_fStampTax
    if deal.m_nDirection == 2:  # 卖出
        st['day_pnl'] += (amt - fee)
    else:                        # 买入
        st['day_pnl'] -= (amt + fee)

    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f}×{deal.m_nVolume} | 当日PnL≈¥{st["day_pnl"]:.0f}')

    # ★ v10: 买回成交后更新连亏计数
    if deal.m_nDirection == 1 and st['fstate'] == STATE_DONE:
        if st['day_pnl'] < 0:
            st['consecutive_loss_days'] += 1
            _log(f'  [风控] 本日亏损, 连亏{st["consecutive_loss_days"]}天')
        else:
            st['consecutive_loss_days'] = 0

def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'{STOCK_NAME} v10.0 停止 | {st.get("total_t_days",0)}天 | 毛利≈¥{st.get("total_pnl",0):,.0f}')
        if st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
            _log(f'  [警告] 未买回头寸! 请手动检查!')

# ============================================================================
# 第十部分：工具函数
# ============================================================================

def _now():
    return _time.strftime('%H:%M:%S')

def _ts():
    return _time.strftime('[%H:%M:%S]')

def _log(*args, **kwargs):
    ts = _ts()
    if args: print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else: print(**kwargs)

def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
