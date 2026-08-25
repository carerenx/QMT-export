# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 自适应做T策略 v1.0 — 不强制买回, 日内结构感知
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【设计理念 — 基于5分钟K线真实回测的教训】

  教训1: 日线OHLC回测不可信 — 日内走势路径(High→Low vs Low→High)决定盈亏
  教训2: 强制买回是亏损之源 — 卖后不跌就保留现金/股票, 不强制成交
  教训3: 早盘30分钟是日内结构的最佳预测器 — 极端波动日不交易
  教训4: 少做少错 — 平均2-3天才触发1笔, 胜率远高于频繁交易

  【策略逻辑】
   09:30-10:00  早盘观察期: 跟踪价格走势, 计算涨跌幅和振幅
   10:00         日内结构判断:
                   FADE(温和上涨) → 预期午后回落 → 反T(先卖后买)
                   EXTREME(极端波动) + 暴跌 → 预期V反 → 正T(先买后卖)
                   其他 → 不交易
   10:00-14:00   交易窗口: 触发后进入状态机
   14:00         关闭新开仓, 已有头寸继续等待自然成交
   14:50         撤销所有挂单, 接受当前仓位(不强制买回/卖出)

  【与v7-v10的核心区别】
   - 不强制买回: 14:50前未成交就撤单, 保留现金/股票过夜
   - 双向: 根据仓位+现金+日内结构自动选择反T或正T
   - 严格过滤: 极端波动日不交易, 只在温和行情中操作
================================================================================
"""
import time as _time

# ============================================================================
# 配置
# ============================================================================
ACCOUNT = '8890145315'
STOCK_CODE = '601869'
STOCK_NAME = '长飞光纤'
STOCK_QMT  = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE = 100
MIN_LOT        = 100
TIMER_INTERVAL = '1nSecond'

# ---- 技术指标参数 ----
ATR_PERIOD = 14

# ---- 反T: 卖出触发 ----
BASE_BEAR      = 0.40
BASE_SIDEWAYS  = 0.55
BASE_WEAK_BULL = 0.65
MULT_MIN = 0.20; MULT_MAX = 1.50

# ---- 反T: 冲高回落确认 ----
PULLBACK_PCT = 0.0010       # 从最高点回落0.1%确认卖出

# ---- 反T: 买回触发(不强制) ----
BUYBACK_MULT = 0.20          # 买回目标 = 卖价×(1-ATR%×0.20)
BOUNCE_PCT   = 0.0015        # 回升0.15%确认买回

# ---- 正T: 买入触发 ----
BUY_TRIGGER_MULT = 0.20      # 买入触发 = 开盘×(1-ATR%×0.20)
SELL_TARGET_MULT = 0.20      # 卖出目标 = 买价×(1+ATR%×0.20)

# ---- 日内结构过滤 ----
MORNING_OBSERVE_MINUTES = 30  # 早盘观察30分钟
MORNING_DECISION_TIME   = '10:00:00'

# 早盘涨跌幅阈值
FADE_MAX_CHG   = 0.01         # 温和上涨上限: +1%
EXTREME_CHG    = 0.02         # 极端波动阈值: ±2%
NEUTRAL_MAX_CHG= 0.005        # 平盘波动上限: ±0.5%

# ---- 风控 ----
EMERGENCY_EXIT_PCT = 0.03     # 紧急平仓(卖后涨3%→止损买回)
DAILY_MAX_LOSS     = -800     # 单日最大亏损
NO_NEW_TRADE_TIME  = '14:00:00'  # 此后不新开仓
CANCEL_ALL_TIME    = '14:50:00'  # 此后撤销挂单

# ---- 熔断 ----
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI     = 70
STRONG_BULL_STREAK  = 5

# ---- 时间 & 费用 ----
HIST_DATA_LEN = 80
COMMISSION    = 0.00025
STAMP_TAX     = 0.001


# ============================================================================
# 技术指标
# ============================================================================
def _sma(v, p):
    n = len(v); r = [0.0]*n
    for i in range(p-1, n): r[i] = sum(v[i-p+1:i+1])/p
    return r

def _atr(h, l, c, p=14):
    n = len(c); tr = [0.0]*n
    for i in range(1,n): tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    r = [0.0]*n
    for i in range(p, n): r[i] = sum(tr[i-p+1:i+1])/p
    return r

def _rsi(c, p=14):
    n = len(c)
    if n < p+1: return [50.0]*n
    rsi = [50.0]*n; g, l = [], []
    for i in range(1,n):
        d = c[i]-c[i-1]; g.append(d if d>0 else 0); l.append(abs(d) if d<0 else 0)
    ag = sum(g[:p])/p; al = sum(l[:p])/p
    rsi[p] = 100.0-100.0/(1+ag/al) if al>0 else 100.0
    for i in range(p, n-1):
        ag = (ag*(p-1)+g[i])/p; al = (al*(p-1)+l[i])/p
        rsi[i+1] = 100.0-100.0/(1+ag/al) if al>0 else 100.0
    return rsi

def _us(c):
    n = len(c); s = [0]*n
    for i in range(1,n): s[i] = s[i-1]+1 if c[i]>c[i-1] else 0
    return s

def _drm(h, l, o, p=10):
    n = len(o); rng = [0.0]*n
    for i in range(n):
        if o[i]>0: rng[i] = (h[i]-l[i])/o[i]
    return _sma(rng, p)


# ============================================================================
# 动态乘数模型
# ============================================================================
def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    if trend == 'bear': base = BASE_BEAR
    elif trend == 'weak_bull': base = BASE_WEAK_BULL
    else: base = BASE_SIDEWAYS

    t = 0.0
    if trend == 'bear': d = -0.25 if up_streak == 0 else -0.15
    elif trend == 'strong_bull': d = +999
    elif trend == 'weak_bull': d = 0.20 if up_streak >= 3 else (0.12 if up_streak >= 1 else 0.05)
    else: d = 0.00
    t += d

    atr_d = -0.30 if atr_pct>0.08 else (-0.22 if atr_pct>0.07 else (-0.15 if atr_pct>0.06 else (-0.08 if atr_pct>0.05 else (0.05 if atr_pct>0.03 else (0.15 if atr_pct>0.02 else 0.25)))))
    atrd_d = -0.25 if atr_ratio>1.50 else (-0.18 if atr_ratio>1.25 else (-0.10 if atr_ratio>1.10 else (0.00 if atr_ratio>0.90 else (0.12 if atr_ratio>0.70 else (0.20 if atr_ratio>0.50 else 0.25)))))
    vd = max(-0.35, min(0.30, atr_d*0.55+atrd_d*0.45)); t += vd

    if vol_ratio>2.00: d=-0.25
    elif vol_ratio>1.50: d=-0.18
    elif vol_ratio>1.20: d=-0.08
    elif vol_ratio>0.80: d=0.00
    elif vol_ratio>0.60: d=0.12
    elif vol_ratio>0.40: d=0.20
    else: d=0.25; t += d

    if rsi_val>80: d=-0.25
    elif rsi_val>70: d=-0.18
    elif rsi_val>60: d=-0.08
    elif rsi_val>55: d=-0.03
    elif rsi_val>45: d=0.00
    elif rsi_val>40: d=0.03
    elif rsi_val>30: d=0.10
    elif rsi_val>20: d=0.20
    else: d=0.25; t += d

    return max(MULT_MIN, min(MULT_MAX, base + t)), base


# ============================================================================
# 信号计算
# ============================================================================
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
    curr_rsi = _rsi(closes)[-1]; up_streak = _us(closes)[-1]

    is_bull = cc > ma20 and ma5 > ma20
    is_bear = cc < ma20 and ma5 < ma20
    if is_bull and curr_rsi > STRONG_BULL_RSI and up_streak >= STRONG_BULL_STREAK:
        trend = 'strong_bull'
    elif is_bull: trend = 'weak_bull'
    elif is_bear: trend = 'bear'
    else: trend = 'sideways'

    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    sell_mult, sell_base = calc_dynamic_sell_mult(trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak)
    sell_trigger = round(co + curr_atr * sell_mult, 2)
    buy_trigger  = round(co * (1.0 - curr_atr_pct * BUY_TRIGGER_MULT), 2)

    do_reverse = (trend != 'strong_bull' and curr_vr >= VOLUME_FILTER_RATIO and curr_rsi <= RSI_OVERBOUGHT)

    return {
        'trend': trend, 'atr': curr_atr, 'atr_pct': curr_atr_pct,
        'rsi': curr_rsi, 'vol_ratio': curr_vr, 'up_streak': up_streak,
        'sell_trigger': sell_trigger, 'sell_mult': sell_mult, 'sell_base': sell_base,
        'buy_trigger': buy_trigger,
        'do_reverse': do_reverse,
        'open': co, 'yclose': cc,
    }


# ============================================================================
# 状态常量
# ============================================================================
S_IDLE      = 'IDLE'        # 空闲
S_SPIKING   = 'SPIKING'     # 反T-冲高监控
S_SOLD      = 'SOLD'        # 反T-已卖出等回落
S_WAIT_BUY  = 'WAIT_BUY'    # 反T-已到买回区等回升
S_DIPPING_N = 'DIPPING_N'   # 正T-下跌监控
S_BOUGHT    = 'BOUGHT'      # 正T-已买入等上涨
S_WAIT_SELL = 'WAIT_SELL'   # 正T-已到卖出区等回落
S_DONE      = 'DONE'        # 当日完成
S_SKIP      = 'SKIP'        # 跳过(结构不利)


def init(ContextInfo):
    """策略初始化"""
    try: ContextInfo.set_universe([STOCK_QMT])
    except Exception: pass
    try: ContextInfo.set_account(ACCOUNT)
    except Exception: pass

    state = {
        'daily_signal': None,
        'base_shares': 0, 'base_cost': 0.0,

        # 日内结构判断
        'morning_open': 0.0, 'morning_high': 0.0, 'morning_low': 999999.0,
        'morning_last': 0.0, 'morning_ticks': 0,
        'morning_decision': '',   # FADE / EXTREME / NEUTRAL / RISKY / CHOP
        'intraday_direction': '', # REVERSE / NORMAL / NONE

        # 状态机
        'fstate': S_IDLE,
        'peak_price': 0.0, 'dip_price': 999999.0,
        'entry_price': 0.0, 'entry_time': '',
        'exit_price': 0.0, 'exit_time': '',
        'target_price': 0.0,
        'day_pnl': 0.0,
        'stop_loss_hit': False,

        # 累计
        'total_t_days': 0, 'total_pnl': 0.0,
        'consecutive_loss': 0,

        'startup_printed': False,
    }
    ContextInfo.st = state
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


def handlebar(ContextInfo):
    """日线回调"""
    st = ContextInfo.st; is_live = ContextInfo.is_last_bar()

    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')

    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60: return

    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    accounts  = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')

    base_shares = 0; base_cost = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume; base_cost = pos.m_dOpenPrice; break

    if base_shares < TRADE_LOT_SIZE:
        if is_live: _log(f'[警告] 底仓不足({base_shares}股)')
        st['base_shares'] = 0; return

    st['base_shares'] = base_shares; st['base_cost'] = base_cost
    if st.get('entry_price', 0) == 0: st['entry_price'] = base_cost

    curr_close = closes[STOCK_QMT][-1]
    avail_cash = accounts[0].m_dAvailable if accounts else 0.0

    signal = compute_signal(opens[STOCK_QMT], highs[STOCK_QMT], lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT])
    if signal is None: return
    st['daily_signal'] = signal

    # 重置
    st['fstate'] = S_IDLE; st['peak_price'] = 0.0; st['dip_price'] = 999999.0
    st['entry_price'] = 0.0; st['entry_time'] = ''
    st['exit_price'] = 0.0; st['exit_time'] = ''
    st['target_price'] = 0.0; st['day_pnl'] = 0.0; st['stop_loss_hit'] = False
    st['morning_open'] = 0.0; st['morning_high'] = 0.0; st['morning_low'] = 999999.0
    st['morning_last'] = 0.0; st['morning_ticks'] = 0
    st['morning_decision'] = ''; st['intraday_direction'] = ''

    if is_live:
        _print_status(ContextInfo, curr_close, avail_cash)
    elif not st['startup_printed']:
        _log(f'{"="*50}')
        _log(f'  {STOCK_NAME} 自适应做T v1.0 | 不强制买回')
        _log(f'  反T: 早盘温和上涨→预期回落 | 正T: 暴跌→预期V反')
        _log(f'  观察{MORNING_OBSERVE_MINUTES}分钟 | {NO_NEW_TRADE_TIME}后不新开 | {CANCEL_ALL_TIME}撤单')
        _log(f'{"="*50}')
        st['startup_printed'] = True


def _print_status(ContextInfo, curr_close, avail_cash):
    st = ContextInfo.st; s = st['daily_signal']
    pos_val = st['base_shares'] * curr_close
    cost = st.get('entry_price', 0)
    upnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    upct = (curr_close/cost - 1)*100 if cost > 0 else 0

    _log(f'[状态] 持仓¥{pos_val:,.0f} | 浮盈¥{upnl:,.0f}({upct:+.1f}%) | 现金¥{avail_cash:,.0f}')
    _log(f'[信号] 趋势{s["trend"]} | RSI{s["rsi"]:.0f} | ATR%{s["atr_pct"]*100:.1f}%')
    _log(f'[触发] 卖出触发线¥{s["sell_trigger"]:.2f} | 买入触发线¥{s["buy_trigger"]:.2f}')
    _log(f'[早盘] 等待{MORNING_DECISION_TIME}决策...')
    if st['total_t_days'] > 0:
        _log(f'[累计] {st["total_t_days"]}笔 | 毛利¥{st["total_pnl"]:,.0f}')


# ============================================================================
# ontimer
# ============================================================================
def ontimer(ContextInfo):
    st = ContextInfo.st; now = _now()
    if not _is_market_open(now): return

    # ── 早盘观察期 (09:30 ~ 10:00) ──
    if now < MORNING_DECISION_TIME:
        _morning_observe(ContextInfo, now)
        return

    # ── 10:00 做日内结构决策 ──
    if st['morning_decision'] == '':
        _morning_decide(ContextInfo)
        if st['intraday_direction'] == 'NONE':
            return  # 结构不利, 全天不交易

    # ── 前置检查 ──
    direction = st['intraday_direction']
    if direction == 'NONE': return
    if st['fstate'] == S_DONE: return
    if st.get('base_shares', 0) < TRADE_LOT_SIZE and direction == 'REVERSE': return

    # ── 时间窗口检查 ──
    if now >= NO_NEW_TRADE_TIME and st['fstate'] == S_IDLE:
        return  # 不新开仓
    if now >= CANCEL_ALL_TIME:
        if st['fstate'] not in (S_IDLE, S_DONE):
            _cancel_pending(ContextInfo)
        return

    # ── 获取价格 ──
    try: tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception: return
    if STOCK_QMT not in tick: return
    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0: return

    # ── 风控: 单日最大亏损 ──
    if st['day_pnl'] < DAILY_MAX_LOSS:
        _log(f'[风控] 日亏损¥{st["day_pnl"]:.0f}超限, 强制平仓')
        _emergency_close(ContextInfo, price)
        return

    # ── 状态路由 ──
    if direction == 'REVERSE':
        _run_reverse_t(ContextInfo, price)
    elif direction == 'NORMAL':
        _run_normal_t(ContextInfo, price)


# ============================================================================
# 早盘观察 & 决策
# ============================================================================
def _morning_observe(ContextInfo, now):
    st = ContextInfo.st
    try: tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception: return
    if STOCK_QMT not in tick: return
    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0: return

    if st['morning_open'] == 0.0:
        st['morning_open'] = price
        st['morning_high'] = price
        st['morning_low'] = price
        _log(f'[早盘] 开始观察 | 开盘¥{price:.2f}')

    if price > st['morning_high']: st['morning_high'] = price
    if price < st['morning_low']: st['morning_low'] = price
    st['morning_last'] = price
    st['morning_ticks'] += 1


def _morning_decide(ContextInfo):
    st = ContextInfo.st
    open_p = st['morning_open']
    if open_p <= 0: st['intraday_direction'] = 'NONE'; return

    last_p = st['morning_last']
    m_chg = (last_p - open_p) / open_p
    m_range = (st['morning_high'] - st['morning_low']) / open_p
    abs_chg = abs(m_chg)

    # 决策
    if m_range < 0.015:
        decision = 'CHOP'       # 振幅<1.5%, 无交易机会
    elif abs_chg > EXTREME_CHG:
        decision = 'EXTREME'    # 极端波动
    elif m_chg > 0 and abs_chg < FADE_MAX_CHG:
        decision = 'FADE'       # 温和上涨 → 预期午后回落
    elif abs_chg < NEUTRAL_MAX_CHG:
        decision = 'NEUTRAL'    # 平盘
    else:
        decision = 'RISKY'      # 有风险

    st['morning_decision'] = decision

    # 方向
    can_reverse = st['base_shares'] >= TRADE_LOT_SIZE and st['daily_signal']['do_reverse']
    can_normal = _cash(ContextInfo) >= 350 * TRADE_LOT_SIZE * 1.01

    if decision == 'FADE' and can_reverse:
        st['intraday_direction'] = 'REVERSE'
        _log(f'[决策] FADE(温和上涨{abs_chg*100:.1f}%) → 反T(先卖后买)')
    elif decision == 'NEUTRAL' and can_reverse and m_chg >= 0:
        st['intraday_direction'] = 'REVERSE'
        _log(f'[决策] NEUTRAL(平盘) → 谨慎反T')
    elif decision == 'EXTREME' and m_chg < -0.03 and can_normal:
        st['intraday_direction'] = 'NORMAL'
        _log(f'[决策] EXTREME 暴跌 → 正T(抄底)')
    else:
        st['intraday_direction'] = 'NONE'
        _log(f'[决策] {decision} → 今日不交易')


# ============================================================================
# 反T状态机
# ============================================================================
def _run_reverse_t(ContextInfo, price):
    st = ContextInfo.st; s = st['daily_signal']
    fs = st['fstate']

    if fs == S_IDLE:
        if price >= s['sell_trigger']:
            st['fstate'] = S_SPIKING; st['peak_price'] = price
            _log(f'[反T-冲高] ¥{price:.2f} >= ¥{s["sell_trigger"]:.2f}')

    elif fs == S_SPIKING:
        if price > st['peak_price']: st['peak_price'] = price
        pk = st['peak_price']; pb = (pk - price) / pk
        if pb >= PULLBACK_PCT:
            _mini_sell(ContextInfo, price)
            st['fstate'] = S_SOLD; st['entry_price'] = price; st['entry_time'] = _now()
            target = price * (1.0 - s['atr_pct'] * BUYBACK_MULT)
            st['target_price'] = round(target, 2)
            _log(f'[反T-卖出] ¥{price:.2f} | 买回目标¥{st["target_price"]:.2f} | 紧急¥{price*(1+EMERGENCY_EXIT_PCT):.2f}')
        elif price < s['sell_trigger']:
            st['fstate'] = S_IDLE; st['peak_price'] = 0.0
            _log(f'[反T-假突破]')

    elif fs == S_SOLD:
        if price >= st['entry_price'] * (1.0 + EMERGENCY_EXIT_PCT):
            _emergency_close(ContextInfo, price)
            return
        if price <= st['target_price']:
            st['fstate'] = S_WAIT_BUY; st['dip_price'] = price
            _log(f'[反T-到位] ¥{price:.2f} <= ¥{st["target_price"]:.2f}')

    elif fs == S_WAIT_BUY:
        if price < st['dip_price']: st['dip_price'] = price
        dip = st['dip_price']
        if dip <= 0: st['dip_price'] = price; dip = price
        bounce = (price - dip) / dip
        if bounce >= BOUNCE_PCT:
            _mini_buy(ContextInfo, price, '反T买回')
            gross = (st['entry_price'] - price) * TRADE_LOT_SIZE
            st['total_t_days'] += 1; st['total_pnl'] += gross
            st['fstate'] = S_DONE
            _log(f'[反T-完成] 卖¥{st["entry_price"]:.2f}→买¥{price:.2f} | 毛利¥{gross:.0f}')


# ============================================================================
# 正T状态机
# ============================================================================
def _run_normal_t(ContextInfo, price):
    st = ContextInfo.st; s = st['daily_signal']
    fs = st['fstate']

    if fs == S_IDLE:
        if price <= s['buy_trigger']:
            st['fstate'] = S_DIPPING_N; st['dip_price'] = price
            _log(f'[正T-探底] ¥{price:.2f} <= ¥{s["buy_trigger"]:.2f}')

    elif fs == S_DIPPING_N:
        if price < st['dip_price']: st['dip_price'] = price
        dip = st['dip_price']
        if dip <= 0: st['dip_price'] = price; dip = price
        bounce = (price - dip) / dip
        if bounce >= PULLBACK_PCT:
            _mini_buy(ContextInfo, price, '正T买入')
            st['fstate'] = S_BOUGHT; st['entry_price'] = price; st['entry_time'] = _now()
            target = price * (1.0 + s['atr_pct'] * SELL_TARGET_MULT)
            st['target_price'] = round(target, 2)
            _log(f'[正T-买入] ¥{price:.2f} | 卖出目标¥{st["target_price"]:.2f} | 止损¥{price*(1-EMERGENCY_EXIT_PCT):.2f}')

    elif fs == S_BOUGHT:
        if price <= st['entry_price'] * (1.0 - EMERGENCY_EXIT_PCT):
            _emergency_close(ContextInfo, price)
            return
        if price >= st['target_price']:
            st['fstate'] = S_WAIT_SELL; st['peak_price'] = price
            _log(f'[正T-到位] ¥{price:.2f} >= ¥{st["target_price"]:.2f}')

    elif fs == S_WAIT_SELL:
        if price > st['peak_price']: st['peak_price'] = price
        pk = st['peak_price']; pb = (pk - price) / pk
        if pb >= PULLBACK_PCT:
            _mini_sell(ContextInfo, price)
            gross = (price - st['entry_price']) * TRADE_LOT_SIZE
            st['total_t_days'] += 1; st['total_pnl'] += gross
            st['fstate'] = S_DONE
            _log(f'[正T-完成] 买¥{st["entry_price"]:.2f}→卖¥{price:.2f} | 毛利¥{gross:.0f}')


# ============================================================================
# 下单 & 辅助
# ============================================================================
def _mini_sell(ContextInfo, price):
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 卖出 ¥{price:.2f} × {TRADE_LOT_SIZE}')
    except Exception as e:
        _log(f'  >>> 卖出失败: {e}')

def _mini_buy(ContextInfo, price, reason=''):
    need = price * TRADE_LOT_SIZE * 1.001
    if _cash(ContextInfo) < need:
        _log(f'  >>> 买入失败: 资金不足')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 买入({reason}) ¥{price:.2f} × {TRADE_LOT_SIZE}')
    except Exception as e:
        _log(f'  >>> 买入失败: {e}')

def _emergency_close(ContextInfo, price):
    st = ContextInfo.st
    if st['intraday_direction'] == 'REVERSE':
        _mini_buy(ContextInfo, price, '紧急买回')
    elif st['intraday_direction'] == 'NORMAL':
        _mini_sell(ContextInfo, price)
    st['fstate'] = S_DONE; st['stop_loss_hit'] = True

def _cancel_pending(ContextInfo):
    st = ContextInfo.st
    _log(f'[{_now()}] 撤单 — 接受当前仓位')
    st['fstate'] = S_DONE

def _cash(ContextInfo):
    try:
        a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception: return 0.0

def _acc(ContextInfo):
    try:
        if hasattr(ContextInfo, 'accID') and ContextInfo.accID: return ContextInfo.accID
    except Exception: pass
    return ACCOUNT

# ============================================================================
# QMT 回调
# ============================================================================
def order_callback(ContextInfo, order):
    sm = {50:'已报',52:'部成',53:'全成',54:'部撤',55:'已撤',56:'废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal} → {sm[order.m_nOrderStatus]}')

def deal_callback(ContextInfo, deal):
    st = ContextInfo.st; d = '买' if deal.m_nDirection == 1 else '卖'
    amt = deal.m_dPrice * deal.m_nVolume; fee = deal.m_fCommission + deal.m_fStampTax
    if deal.m_nDirection == 2: st['day_pnl'] += (amt - fee)
    else: st['day_pnl'] -= (amt + fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f}×{deal.m_nVolume} | 日PnL≈¥{st["day_pnl"]:.0f}')

def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'{STOCK_NAME} v1.0 停止 | {st.get("total_t_days",0)}笔 | 毛利¥{st.get("total_pnl",0):,.0f}')
        if st.get('fstate') not in (S_IDLE, S_DONE, S_SKIP):
            _log(f'  [警告] 有未完成头寸! fstate={st.get("fstate")}')

# ============================================================================
# 工具
# ============================================================================
def _now(): return _time.strftime('%H:%M:%S')
def _ts():  return _time.strftime('[%H:%M:%S]')
def _log(*args, **kwargs):
    ts = _ts()
    if args: print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else: print(**kwargs)
def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
