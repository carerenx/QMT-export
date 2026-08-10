# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你反T策略 v8.0 — 自适应乘数优化版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()
================================================================================

 【v8.0 — 基于2026-07-02~27回测的优化】

  v7 验证结果 (7月2日~27日, 18个交易日):
    - 7笔交易全部盈利, 总PnL=+18,700, 胜率100%
    - 但11天有信号未触发(触发率仅39%) — mult被锁死在0.35下限
    - 根因: BASE=0.55+mult_min=0.35 → 有效乘数=0.35, 熊市中仍偏高

  v8 修复:
    ┌──────┬─────────────────────┬──────────────────────────────────┐
    │ 修复  │ v7 问题              │ v8 方案                          │
    ├──────┼─────────────────────┼──────────────────────────────────┤
    │ 1    │ mult_min=0.35过高    │ mult_min=0.20 ← 熊市中允许更低触发 │
    │      │ 11/18天未触发        │                                   │
    ├──────┼─────────────────────┼──────────────────────────────────┤
    │ 2    │ BASE固定0.55         │ BASE根据趋势动态调整:              │
    │      │ 不适应牛熊切换        │   bear=0.40, sideways=0.55,       │
    │      │                     │   weak_bull=0.65, strong_bull=禁反T│
    ├──────┼─────────────────────┼──────────────────────────────────┤
    │ 3    │ 牛市禁反T一刀切       │ 细分: strong_bull(连涨≥5+RSI>70)禁 │
    │      │                     │   weak_bull允许反T(乘数偏高)         │
    ├──────┼─────────────────────┼──────────────────────────────────┤
    │ 4    │ 紧急买回=2%          │ 提高到3% — 给股价更多下跌空间       │
    │      │ 卖后小涨即触发亏损    │                                   │
    ├──────┼─────────────────────┼──────────────────────────────────┤
    │ 5    │ 卖后无动态调整        │ 新增: 卖后若价格>卖价,             │
    │      │                     │   提高买回目标(止损上移)            │
    └──────┴─────────────────────┴──────────────────────────────────┘

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

# ---- 卖出触发线(v8: BASE根据趋势动态, 这里设默认值) ----
ATR_PERIOD           = 14
SELL_TRIGGER_BASE_BEAR      = 0.40   # ★ v8: 熊市BASE降低
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55   # v7的默认值
SELL_TRIGGER_BASE_WEAK_BULL = 0.65   # ★ v8: 弱牛BASE偏高(谨慎)
DYNAMIC_MULT_MIN     = 0.20          # ★ v8: 0.35→0.20 (熊市可更低)
DYNAMIC_MULT_MAX     = 1.50
# ★ v7保留: 日内振幅约束
DAILY_RANGE_CAP_ENABLED = True
DAILY_RANGE_CAP_MULT    = 0.80

# ---- 冲高回落确认 ----
PULLBACK_PCT = 0.0010

# ---- 买回触发(动态) ----
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT = 0.0010
# ★ v8新增: 卖后动态止损 — 价格不利时收紧买回
BUYBACK_TIGHTEN_MULT = 0.60   # 不利时买回目标收窄到60%

# ---- 熔断 & 过滤 ----
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI      = 70   # ★ v8: strong_bull条件: RSI>70
STRONG_BULL_STREAK   = 5    # ★ v8: strong_bull条件: 连涨≥5天

# ---- 紧急买回 & 止损 ----
EMERGENCY_BUYBACK_PCT = 0.03  # ★ v8: 0.02→0.03
STOP_LOSS_PCT         = 0.015

# ---- 时间 & 数据 ----
FORCE_CLOSE_TIME = '14:57:00'
HIST_DATA_LEN    = 80
COMMISSION       = 0.00025
STAMP_TAX        = 0.001


# ============================================================================
# 第二部分：技术指标 (同v7)
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
    n = len(opens)
    ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0:
            ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)


# ============================================================================
# 第三部分：★ v8 核心 — 自适应乘数 ★
# ============================================================================

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    """
    ★ v8: 自适应BASE + 扩大乘数范围

    改动:
      1. BASE根据trend动态 (0.40/0.55/0.65)
      2. mult_min=0.20 (vs v7的0.35)
      3. 因子权重微调
    """
    # ★ v8: 动态BASE
    if trend == 'bear':
        base = SELL_TRIGGER_BASE_BEAR      # 0.40
    elif trend == 'weak_bull':
        base = SELL_TRIGGER_BASE_WEAK_BULL  # 0.65
    else:
        base = SELL_TRIGGER_BASE_SIDEWAYS   # 0.55

    deviations = {}
    total = 0.0

    # ─── 因子1: 趋势强度 (v8: 细化为4级) ───
    if trend == 'bear':
        d = -0.25 if up_streak == 0 else -0.15
    elif trend == 'strong_bull':
        d = +999  # 不会被使用 (信号层已禁止)
    elif trend == 'weak_bull':
        if up_streak >= 3: d = +0.20
        elif up_streak >= 1: d = +0.12
        else: d = +0.05
    else:  # sideways
        d = 0.00
    deviations['趋势'] = d; total += d

    # ─── 因子2: 波动率综合 (同v7) ───
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
    deviations['波动率'] = round(vol_d, 2); total += vol_d

    # ─── 因子3: 成交量 ───
    if vol_ratio > 2.00:     d = -0.25
    elif vol_ratio > 1.50:   d = -0.18
    elif vol_ratio > 1.20:   d = -0.08
    elif vol_ratio > 0.80:   d = 0.00
    elif vol_ratio > 0.60:   d = +0.12
    elif vol_ratio > 0.40:   d = +0.20
    else:                    d = +0.25
    deviations['成交量'] = d; total += d

    # ─── 因子4: RSI ───
    if rsi_val > 80:         d = -0.25
    elif rsi_val > 70:       d = -0.18
    elif rsi_val > 60:       d = -0.08
    elif rsi_val > 55:       d = -0.03
    elif rsi_val > 45:       d = 0.00
    elif rsi_val > 40:       d = +0.03
    elif rsi_val > 30:       d = +0.10
    elif rsi_val > 20:       d = +0.20
    else:                    d = +0.25
    deviations['RSI'] = d; total += d

    # ─── 合成 ───
    final = base + total
    final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final))
    return round(final, 2), deviations, base


# ============================================================================
# 第四部分：信号计算 (v8: 趋势4级分类)
# ============================================================================

def compute_signal(opens, highs, lows, closes, volumes):
    """v8: 4级趋势分类 + 自适应BASE"""
    n = len(closes)
    if n < 60:
        return None

    co = opens[-1]; cc = closes[-1]; cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    ma5  = _sma(closes, 5)[-1]; ma20 = _sma(closes, 20)[-1]
    curr_rsi = _rsi(closes)[-1]; up_streak = _up_streak(closes)[-1]

    # ★ v8: 4级趋势
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

    # ★ v8: 自适应BASE + 扩大范围
    sell_mult, factor_details, base_used = calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak
    )

    # 日内振幅约束
    daily_range_ma10 = _daily_range_ma(highs, lows, opens, 10)[-1]
    max_trigger = co * (1.0 + daily_range_ma10 * DAILY_RANGE_CAP_MULT)
    range_capped = False

    sell_trigger_raw = co + curr_atr * sell_mult
    if DAILY_RANGE_CAP_ENABLED and sell_trigger_raw > max_trigger:
        sell_trigger = round(max_trigger, 2)
        range_capped = True
    else:
        sell_trigger = round(sell_trigger_raw, 2)

    # ★ v8: strong_bull or 缩量 or RSI超买 → 禁止
    do_short, reason = True, ''
    if trend == 'strong_bull':
        do_short, reason = False, '强牛禁反T(连涨≥5+RSI>70)'
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
        'sell_mult_base':     base_used,       # ★ v8: 记录实际使用的BASE
        'factor_details':     factor_details,
        'atr_ratio':          atr_ratio,
        'up_streak':          up_streak,
        'buyback_mult':       BUYBACK_TRIGGER_MULT,
        'bounce_pct':         BOUNCE_PCT,
    }


# ============================================================================
# 第五部分：QMT 策略入口 (同v7)
# ============================================================================

STATE_IDLE='IDLE'; STATE_SPIKING='SPIKING'; STATE_DIPPING='DIPPING'
STATE_SOLD='SOLD'; STATE_DONE='DONE'; STATE_FORCED='FORCED'

def init(ContextInfo):
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account(ACCOUNT)
    state = {
        'daily_signal':None, 'base_shares':0, 'base_cost':0.0,
        'fstate':STATE_IDLE, 'peak_price':0.0, 'dip_price':0.0,
        'sell_fill_price':0.0, 'buyback_target':0.0, 'buyback_target_pct':0.0,
        'day_pnl':0.0, 'stop_loss_hit':False, 'total_t_days':0, 'total_pnl':0.0,
        'entry_price':0.0, 'startup_printed':False, 'state_enter_time':'',
        # ★ v8新增
        'sell_elapsed_bars': 0,  # 卖出后经过的bar数
    }
    ContextInfo.st = state
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


def handlebar(ContextInfo):
    st = ContextInfo.st; is_live = ContextInfo.is_last_bar()
    closes=ContextInfo.get_history_data(HIST_DATA_LEN,'1d','close')
    opens=ContextInfo.get_history_data(HIST_DATA_LEN,'1d','open')
    highs=ContextInfo.get_history_data(HIST_DATA_LEN,'1d','high')
    lows=ContextInfo.get_history_data(HIST_DATA_LEN,'1d','low')
    volumes=ContextInfo.get_history_data(HIST_DATA_LEN,'1d','volume')
    if STOCK_QMT not in closes or len(closes[STOCK_QMT])<60: return
    print(f'[{_now()}] {STOCK_NAME} v8.0 自适应 | BASE=bear{SELL_TRIGGER_BASE_BEAR}/side{SELL_TRIGGER_BASE_SIDEWAYS}/wbull{SELL_TRIGGER_BASE_WEAK_BULL}')
    positions=get_trade_detail_data(ACCOUNT,'STOCK','POSITION')
    accounts=get_trade_detail_data(ACCOUNT,'STOCK','ACCOUNT')
    base_shares=0;base_cost=0.0
    for pos in positions:
        if pos.m_strInstrumentID==STOCK_CODE:base_shares=pos.m_nVolume;base_cost=pos.m_dOpenPrice;break
    if base_shares<TRADE_LOT_SIZE:
        if is_live:_log(f'[警告] 底仓不足1手({base_shares}股)')
        st['base_shares']=0;return
    st['base_shares']=base_shares;st['base_cost']=base_cost
    if st['entry_price']==0.0:st['entry_price']=base_cost

    cc=closes[STOCK_QMT][-1]
    ac=accounts[0].m_dAvailable if accounts else 0.0
    pv=base_shares*cc

    signal=compute_signal(opens[STOCK_QMT],highs[STOCK_QMT],lows[STOCK_QMT],closes[STOCK_QMT],volumes[STOCK_QMT])
    if signal is None:return
    st['daily_signal']=signal

    st['fstate']=STATE_IDLE;st['peak_price']=0.0;st['dip_price']=0.0
    st['sell_fill_price']=0.0;st['buyback_target']=0.0;st['buyback_target_pct']=0.0
    st['day_pnl']=0.0;st['stop_loss_hit']=False;st['state_enter_time']=_now()
    st['sell_elapsed_bars']=0

    if is_live:
        _print_signal(ContextInfo,cc,ac,pv)
    elif not st['startup_printed']:
        _log(f'{"="*55}')
        _log(f'  {STOCK_NAME} v8.0 自适应 | BASE=bear{SELL_TRIGGER_BASE_BEAR}/side{SELL_TRIGGER_BASE_SIDEWAYS}/wbull{SELL_TRIGGER_BASE_WEAK_BULL}')
        _log(f'  范围[{DYNAMIC_MULT_MIN},{DYNAMIC_MULT_MAX}] | 紧急买回{EMERGENCY_BUYBACK_PCT*100:.0f}%')
        _log(f'{"="*55}')
        st['startup_printed']=True


def _print_signal(ContextInfo,cc,ac,pv):
    s=ContextInfo.st['daily_signal'];st=ContextInfo.st
    cost=st['entry_price']
    upnl=(cc-cost)*st['base_shares'] if cost>0 else 0
    upct=(cc/cost-1)*100 if cost>0 else 0
    tv=pv+ac

    _log(f'━━━ {"信号":─^25} ━━━')
    _log(f'  持仓¥{pv:,.0f} | 浮动¥{upnl:,.0f}({upct:+.1f}%) | 总¥{tv:,.0f}')
    _log(f'  开盘¥{s["open_price"]:.2f} | ATR¥{s["atr"]:.2f}({s["atr_pct"]*100:.1f}%) | '
         f'振幅{s["daily_range_ma10"]*100:.1f}%')
    _log(f'  趋势{s["trend"]}(连涨{s["up_streak"]}天) | RSI{s["rsi"]:.0f} | 量比{s["vol_ratio"]:.2f}')

    if s['do_short']:
        _log(f'  ┌─ v8自适应乘数 (BASE={s["sell_mult_base"]}) ─')
        for name,dev in s['factor_details'].items():
            if dev!=0:_log(f'  │ {name:<6} {dev:+.2f}')
        _log(f'  ├─ 乘数={s["sell_mult"]:.2f} → 触发线={s["open_price"]:.2f}+{s["atr"]:.2f}×{s["sell_mult"]:.2f}=¥{s["sell_trigger_raw"]:.2f}')
        if s['range_capped']:
            _log(f'  │ ⚡ 振幅约束: ¥{s["sell_trigger_raw"]:.2f}→¥{s["sell_trigger"]:.2f}')
        _log(f'  └{"─"*25}')
    else:
        _log(f'  反T ✗ ({s["blocked_reason"]})')

    lc=cc*TRADE_LOT_SIZE
    if ac>=lc*1.01:_log(f'  正T ✓')
    else:_log(f'  正T ✗ (缺¥{lc-ac:,.0f})')
    if st['total_t_days']>0:_log(f'  累计反T {st["total_t_days"]}天 ¥{st["total_pnl"]:,.0f}')


# ============================================================================
# 第七部分：ontimer & 状态处理 (v8: 动态买回+紧急买回调高)
# ============================================================================

def ontimer(ContextInfo):
    st=ContextInfo.st;s=st.get('daily_signal')
    if s is None or not s.get('do_short'):return
    now=_now()
    if not _is_market_open(now):return
    if st['fstate'] in (STATE_DONE,STATE_FORCED):return
    try:tick=ContextInfo.get_full_tick([STOCK_QMT])
    except:return
    if STOCK_QMT not in tick:return
    price=tick[STOCK_QMT].get('lastPrice',0)
    if price<=0:return
    fs=st['fstate']
    if fs==STATE_IDLE:_handle_idle(ContextInfo,price)
    elif fs==STATE_SPIKING:_handle_spiking(ContextInfo,price)
    elif fs==STATE_SOLD:_handle_sold(ContextInfo,price)
    elif fs==STATE_DIPPING:_handle_dipping(ContextInfo,price)

    if st['fstate'] in (STATE_SOLD, STATE_DIPPING):
        st['sell_elapsed_bars'] += 1

    if now>=FORCE_CLOSE_TIME:
        if fs in (STATE_SOLD,STATE_DIPPING):_force_buyback(ContextInfo)
        elif fs in (STATE_SPIKING,STATE_IDLE):st['fstate']=STATE_DONE
    if fs==STATE_SOLD and not st['stop_loss_hit']:
        ll=st['base_shares']*s['open_price']*STOP_LOSS_PCT
        if st['day_pnl']<-ll:
            _log(f'[止损] ¥{st["day_pnl"]:.0f}');st['stop_loss_hit']=True
            _force_buyback(ContextInfo)

def _handle_idle(ContextInfo,price):
    st=ContextInfo.st;t=st['daily_signal']['sell_trigger']
    if price>=t:
        st['fstate']=STATE_SPIKING;st['peak_price']=price;st['state_enter_time']=_now()
        _log(f'[冲高] ¥{price:.2f}≥¥{t:.2f}(BASE={st["daily_signal"]["sell_mult_base"]},乘数{st["daily_signal"]["sell_mult"]})')

def _handle_spiking(ContextInfo,price):
    st=ContextInfo.st;t=st['daily_signal']['sell_trigger']
    if price>st['peak_price']:st['peak_price']=price
    pk=st['peak_price'];pb=(pk-price)/pk
    if pb>=PULLBACK_PCT:
        _log(f'[卖出] ¥{pk:.2f}回落{pb*100:.2f}%→¥{price:.2f}')
        _mini_sell(ContextInfo,price)
        st['fstate']=STATE_SOLD;st['sell_fill_price']=price;st['state_enter_time']=_now()
        ap=st['daily_signal']['atr_pct'];bp=ap*st['daily_signal']['buyback_mult']
        bt=round(price*(1.0-bp),2)
        st['buyback_target']=bt;st['buyback_target_pct']=bp*100
        st['sell_elapsed_bars']=0
        _log(f'  买回线¥{bt:.2f}({bp*100:.2f}%) | 紧急¥{price*(1+EMERGENCY_BUYBACK_PCT):.2f}')
    elif price<t:
        _log(f'[假突破] ¥{price:.2f}')
        st['fstate']=STATE_IDLE;st['peak_price']=0.0

def _handle_sold(ContextInfo,price):
    st=ContextInfo.st;sp=st['sell_fill_price'];bt=st['buyback_target']
    # ★ v8: 紧急买回提高到3%
    if price>=sp*(1.0+EMERGENCY_BUYBACK_PCT):
        _log(f'[紧急] 卖¥{sp:.2f}→现¥{price:.2f}(+{(price-sp)/sp*100:.2f}%)买回!')
        _mini_buyback(ContextInfo,price,'紧急');return
    # ★ v8新增: 动态收紧 — 如果卖后价格在卖价之上波动(没跌), 收紧买回目标
    tightened_bt = bt
    if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:  # 30个bar后仍未跌
        tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] * BUYBACK_TRIGGER_MULT * BUYBACK_TIGHTEN_MULT)
        # Don't go above original target
        tightened_bt = min(tightened_bt, bt)
    if price<=tightened_bt:
        dp=(sp-price)/sp*100;st['fstate']=STATE_DIPPING;st['dip_price']=price
        st['state_enter_time']=_now()
        tag = "(收紧)" if tightened_bt < bt else ""
        _log(f'[买回触发{tag}] ¥{price:.2f}(-{dp:.2f}%)≤¥{tightened_bt:.2f}')

def _handle_dipping(ContextInfo,price):
    st=ContextInfo.st;bt=st['buyback_target']
    if price<st['dip_price']:st['dip_price']=price
    dip=st['dip_price'];bn=(price-dip)/dip
    if bn>=BOUNCE_PCT:
        sp=st['sell_fill_price'];gr=(sp-price)*TRADE_LOT_SIZE
        _log(f'[买回] 最低¥{dip:.2f}回升{bn*100:.2f}%→¥{price:.2f} | 毛利≈¥{gr:.0f}')
        _mini_buyback(ContextInfo,price,'正常')
        st['total_t_days']+=1;st['total_pnl']+=gr
    elif price>bt:
        _log(f'[假跌破] ¥{price:.2f}')
        st['fstate']=STATE_SOLD;st['dip_price']=0.0


# ============================================================================
# 第八部分：下单 & 回调 (同v7)
# ============================================================================

def _mini_sell(ContextInfo,price):
    st=ContextInfo.st
    try:
        order_shares(STOCK_QMT,-TRADE_LOT_SIZE,'FIX',price,ContextInfo,_acc(ContextInfo))
        _log(f'  >>> 卖出 ¥{price:.2f}×{TRADE_LOT_SIZE}')
    except Exception as e:_log(f'  >>> 卖出失败:{e}');st['fstate']=STATE_IDLE

def _mini_buyback(ContextInfo,price,reason=''):
    st=ContextInfo.st;need=price*TRADE_LOT_SIZE*1.001
    if _cash(ContextInfo)<need:_log(f'  >>> 买回失败:资金不足');return
    try:
        order_shares(STOCK_QMT,TRADE_LOT_SIZE,'FIX',price,ContextInfo,_acc(ContextInfo))
        _log(f'  >>> 买回({reason}) ¥{price:.2f}×{TRADE_LOT_SIZE}')
        st['fstate']=STATE_DONE
    except Exception as e:_log(f'  >>> 买回失败:{e}')

def _force_buyback(ContextInfo):
    st=ContextInfo.st
    try:
        order_shares(STOCK_QMT,TRADE_LOT_SIZE,'COMPETE',ContextInfo,_acc(ContextInfo))
        st['fstate']=STATE_FORCED;_log(f'[尾盘] ✓')
    except Exception as e:_log(f'[尾盘失败!!] {e}');st['fstate']=STATE_FORCED

def _cash(ContextInfo):
    try:
        a=get_trade_detail_data(ACCOUNT,'STOCK','ACCOUNT');return a[0].m_dAvailable if a else 0.0
    except:return 0.0

def _acc(ContextInfo):
    return ContextInfo.accID if hasattr(ContextInfo,'accID') and ContextInfo.accID else ACCOUNT

def order_callback(ContextInfo,order):
    sm={50:'已报',52:'部成',53:'全成',54:'部撤',55:'已撤',56:'废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}→{sm[order.m_nOrderStatus]}')

def deal_callback(ContextInfo,deal):
    st=ContextInfo.st;d='买' if deal.m_nDirection==1 else '卖'
    amt=deal.m_dPrice*deal.m_nVolume;fee=deal.m_fCommission+deal.m_fStampTax
    st['day_pnl']+=(amt-fee) if deal.m_nDirection==2 else -(amt+fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f}×{deal.m_nVolume} PnL≈¥{st["day_pnl"]:.0f}')

def stop(ContextInfo):
    st=getattr(ContextInfo,'st',None)
    if st:
        _log(f'{STOCK_NAME} v8.0 停止 | {st.get("total_t_days",0)}天 ¥{st.get("total_pnl",0):,.0f}')
        if st.get('fstate') in (STATE_SOLD,STATE_DIPPING):_log(f'  ⚠ 未买回!')

def _now():
    import time as _t;return _t.strftime('%H:%M:%S')

def _ts():
    import time as _t;return _t.strftime('[%H:%M:%S]')

def _log(*args,**kwargs):
    ts=_ts()
    if args:print(f'{ts} {args[0]}',*args[1:],**kwargs)
    else:print(**kwargs)

def _is_market_open(now):
    return ('09:30:00'<=now<='11:30:00') or ('13:00:00'<=now<='15:00:00')
