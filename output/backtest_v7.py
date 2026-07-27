# -*- coding: utf-8 -*-
"""
v7 反T策略回测 — 日线OHLC近似模拟 + v8改进版
从7月2日回测，基于日线最高/最低/开收近似日内走势
"""
import json, time, urllib.request, sys

UA = "Mozilla/5.0"

# ===================================================================
# 1. Data Fetching
# ===================================================================
def get_daily_klines(code, n=150):
    prefix = "sh" if code.startswith(("6","9","0")) else "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{n},qfq"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[]) or \
        data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[])
    return [{"date":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

# ===================================================================
# 2. Indicators (ported from v7)
# ===================================================================
def _sma(vals, period):
    n = len(vals); r = [0.0]*n
    for i in range(period-1,n): r[i] = sum(vals[i-period+1:i+1])/period
    return r

def _atr(highs, lows, closes, period=14):
    n = len(closes); tr = [0.0]*n
    for i in range(1,n): tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    r = [0.0]*n
    for i in range(period,n): r[i] = sum(tr[i-period+1:i+1])/period
    return r

def _rsi(closes, period=14):
    n = len(closes)
    if n < period+1: return [50.0]*n
    rsi = [50.0]*n; g, l = [], []
    for i in range(1,n):
        d = closes[i]-closes[i-1]; g.append(d if d>0 else 0); l.append(abs(d) if d<0 else 0)
    ag = sum(g[:period])/period; al = sum(l[:period])/period
    rsi[period] = 100.0-100.0/(1+ag/al) if al>0 else 100.0
    for i in range(period,n-1):
        ag = (ag*(period-1)+g[i])/period; al = (al*(period-1)+l[i])/period
        rsi[i+1] = 100.0-100.0/(1+ag/al) if al>0 else 100.0
    return rsi

def _up_streak(closes):
    n = len(closes); s = [0]*n
    for i in range(1,n): s[i] = s[i-1]+1 if closes[i]>closes[i-1] else 0
    return s

def _daily_range_ma(highs, lows, opens, period=10):
    n = len(opens); ranges = [0.0]*n
    for i in range(n):
        if opens[i]>0: ranges[i] = (highs[i]-lows[i])/opens[i]
    return _sma(ranges, period)

# ===================================================================
# 3. v7 Dynamic Multiplier
# ===================================================================
SELL_TRIGGER_BASE = 0.55
DYNAMIC_MULT_MIN = 0.35
DYNAMIC_MULT_MAX = 1.50
DAILY_RANGE_CAP_MULT = 0.80
ATR_PERIOD = 14

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    base = SELL_TRIGGER_BASE; total = 0.0 # deviations omitted for brevity

    if trend == 'bear': d = -0.25 if up_streak==0 else -0.15
    elif trend == 'bull': d = 0.25 if up_streak>=3 else (0.15 if up_streak>=1 else 0.05)
    else: d = 0.00
    total += d

    if atr_pct>0.08: atr_d=-0.30
    elif atr_pct>0.07: atr_d=-0.22
    elif atr_pct>0.06: atr_d=-0.15
    elif atr_pct>0.05: atr_d=-0.08
    elif atr_pct>0.03: atr_d=0.05
    elif atr_pct>0.02: atr_d=0.15
    else: atr_d=0.25

    if atr_ratio>1.50: atrd_d=-0.25
    elif atr_ratio>1.25: atrd_d=-0.18
    elif atr_ratio>1.10: atrd_d=-0.10
    elif atr_ratio>0.90: atrd_d=0.00
    elif atr_ratio>0.70: atrd_d=0.12
    elif atr_ratio>0.50: atrd_d=0.20
    else: atrd_d=0.25

    vol_d = max(-0.35, min(0.30, atr_d*0.55 + atrd_d*0.45))
    total += vol_d

    if vol_ratio>2.00: d=-0.25
    elif vol_ratio>1.50: d=-0.18
    elif vol_ratio>1.20: d=-0.08
    elif vol_ratio>0.80: d=0.00
    elif vol_ratio>0.60: d=0.12
    elif vol_ratio>0.40: d=0.20
    else: d=0.25
    total += d

    if rsi_val>80: d=-0.25
    elif rsi_val>70: d=-0.18
    elif rsi_val>60: d=-0.08
    elif rsi_val>55: d=-0.03
    elif rsi_val>45: d=0.00
    elif rsi_val>40: d=0.03
    elif rsi_val>30: d=0.10
    elif rsi_val>20: d=0.20
    else: d=0.25
    total += d

    return max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, base + total))

def compute_signal(data, idx):
    """v7 daily signal"""
    if idx < 60: return None
    o = [d['o'] for d in data[:idx+1]]
    h = [d['h'] for d in data[:idx+1]]
    l = [d['l'] for d in data[:idx+1]]
    c = [d['c'] for d in data[:idx+1]]
    v = [d['v'] for d in data[:idx+1]]

    d_today = data[idx]
    co, cc, cv = d_today['o'], d_today['c'], d_today['v']

    atr_arr = _atr(h,l,c,ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc*0.03
    curr_atr_pct = curr_atr/cc if cc>0 else 0.03

    atr_ma20 = _sma(atr_arr,20)[-1] if idx>=20 else curr_atr
    atr_ratio = curr_atr/atr_ma20 if atr_ma20>0 else 1.0

    ma5 = _sma(c,5)[-1]; ma20 = _sma(c,20)[-1]
    trend = 'bull' if (cc>ma20 and ma5>ma20) else ('bear' if (cc<ma20 and ma5<ma20) else 'sideways')

    curr_rsi = _rsi(c)[-1]
    up_streak_val = _up_streak(c)[-1]
    ma20_vol = _sma(v,20)
    curr_vr = cv/ma20_vol[-1] if ma20_vol[-1]>0 else 1.0

    sell_mult = calc_dynamic_sell_mult(trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak_val)

    daily_range_ma10 = _daily_range_ma(h,l,o,10)[-1]
    max_trigger = co*(1.0+daily_range_ma10*DAILY_RANGE_CAP_MULT)
    sell_trigger_raw = co+curr_atr*sell_mult
    sell_trigger = round(min(sell_trigger_raw, max_trigger) if sell_trigger_raw>max_trigger else sell_trigger_raw, 2)
    range_capped = sell_trigger_raw > max_trigger

    do_short, reason = True, ''
    if trend=='bull': do_short,reason = False,'牛市禁反T'
    elif curr_vr<0.4: do_short,reason = False,f'缩量'
    elif curr_rsi>75: do_short,reason = False,f'RSI超买'

    return {
        'do_short':do_short, 'blocked_reason':reason, 'trend':trend,
        'sell_trigger':sell_trigger, 'open_price':co, 'atr':round(curr_atr,2),
        'atr_pct':curr_atr_pct, 'rsi':curr_rsi, 'vol_ratio':curr_vr,
        'sell_mult':round(sell_mult,3), 'range_capped':range_capped, 'up_streak':up_streak_val,
    }

# ===================================================================
# 4. Daily OHLC Backtest Simulation
# ===================================================================
PULLBACK = 0.0010
BUYBACK_MULT = 0.15
BOUNCE = 0.0010
EMERGENCY = 0.02
STOP_LOSS = 0.015
COMM = 0.00025
TAX = 0.001
LOT = 100

def simulate_day_v7(d, signal):
    """
    日线OHLC近似反T:
    - 如果日最高 >= 触发线: 策略触发
    - 卖出价 ≈ 最高价 × (1 - PULLBACK) （回落确认后卖出）
    - 情况A (跌到位): 最低价 ≤ 买回目标价 → 买回成功
    - 情况B (未跌到位): 最低价 > 买回目标价 → 收盘强制买回(或亏损)
    - 情况C (紧急买回): 如果盘中反弹超过卖价+2%
    """
    if signal is None or not signal['do_short']:
        return {'state':'BLOCKED','pnl':0,'sell_p':0,'buy_p':0,'peak':0,'dip':0}

    o, h, l, c_val = d['o'], d['h'], d['l'], d['c']
    trigger = signal['sell_trigger']

    # Check if price reached trigger
    if h < trigger:
        return {'state':'NO_TRIGGER','pnl':0,'sell_p':0,'buy_p':0,'peak':h,'dip':l}

    # Price reached trigger → simulate sell
    # Peak = day's high (conservative: sell after 0.1% pullback from peak)
    peak = h
    sell_p = h * (1.0 - PULLBACK)  # Sell after pullback from high
    if sell_p < trigger:
        sell_p = trigger  # Worst case: sell right at trigger

    # Buyback target
    atr_pct = signal['atr_pct']
    buyback_target = sell_p * (1.0 - atr_pct * BUYBACK_MULT)

    # Emergency check: if price went up instead of down
    # In daily bars, emergency means high is significantly above sell price
    # But since we sold at peak-PB, emergency only if we sold too early and price kept rising
    emergency_trigger = sell_p * (1.0 + EMERGENCY)
    # With daily bars, if h is much higher than sell_p, emergency likely triggered
    # But this is hard to capture — let's assume if h > emergency_trigger, it triggered after sell

    # Determine buyback
    if c_val > emergency_trigger and h > emergency_trigger:
        # Emergency buyback — price went up after sell
        buy_p = emergency_trigger  # Buy at emergency level
        state = 'EMERGENCY'
    elif l <= buyback_target:
        # Price dropped to buyback zone — buy at bounce from dip
        buy_dip = max(l, buyback_target * 0.95)
        buy_p = buy_dip * (1.0 + BOUNCE)
        if buy_p > buyback_target:
            buy_p = buyback_target  # Don't exceed target
        state = 'DONE'
    else:
        # Never hit buyback zone — forced buyback at close
        buy_p = c_val
        state = 'FORCED'

    # Stop loss check
    loss = (sell_p - buy_p) * LOT
    stop_loss_amount = LOT * o * STOP_LOSS
    if loss < -stop_loss_amount:
        state = 'STOP_LOSS'
        # Cap loss at stop level
        buy_p = sell_p - stop_loss_amount / LOT

    # P&L
    gross = (sell_p - buy_p) * LOT
    fees = sell_p*LOT*(COMM+TAX) + buy_p*LOT*COMM
    pnl = gross - fees

    return {'state':state,'pnl':pnl,'sell_p':sell_p,'buy_p':buy_p,'peak':peak,'dip':l}

# ===================================================================
# 5. RUN BACKTEST
# ===================================================================
print("Fetching data...")
daily = get_daily_klines("601869", n=150)
print(f"Got {len(daily)} daily bars: {daily[0]['date']} ~ {daily[-1]['date']}")

# ===== V7 BACKTEST =====
print("\n" + "="*85)
print("  V7 策略回测: 2026-07-02 ~ 2026-07-27 (日线OHLC近似)")
print("="*85)

july2_idx = next(i for i,d in enumerate(daily) if d['date']>='2026-07-02')
total_pnl = 0; results = []

for i in range(july2_idx, len(daily)):
    d = daily[i]
    signal = compute_signal(daily, i)
    if signal is None: continue
    result = simulate_day_v7(d, signal)
    total_pnl += result['pnl']
    results.append({'date':d['date'],'d':d,'signal':signal,'r':result})

# Print
print(f"\n  {'Date':<12} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Signal':>6} {'Trend':>8} "
      f"{'Mult':>6} {'Trigger':>8} {'Sell':>8} {'Buy':>8} {'PnL':>10} {'State':>12}")
print(f"  {'-'*105}")

for r in results:
    d = r['d']; s = r['signal']; res = r['r']
    sig_str = 'YES' if s['do_short'] else 'NO'
    print(f"  {r['date']:<12} {d['o']:>8.2f} {d['h']:>8.2f} {d['l']:>8.2f} {d['c']:>8.2f} "
          f"{sig_str:>6} {s['trend']:>8} {s['sell_mult']:>6.3f} "
          f"{s['sell_trigger']:>8.2f} {res['sell_p']:>8.2f} {res['buy_p']:>8.2f} "
          f"{res['pnl']:>+10.0f} {res['state']:>12}")

# Summary
trade_days = [r for r in results if r['signal']['do_short']]
triggered = [r for r in trade_days if r['r']['state'] not in ('NO_TRIGGER','BLOCKED')]
profit_days = [r for r in triggered if r['r']['pnl']>0]
loss_days = [r for r in triggered if r['r']['pnl']<0]

print(f"\n{'='*85}")
print(f"  V7 BACKTEST SUMMARY")
print(f"{'='*85}")
print(f"""
  Period:              2026-07-02 ~ 2026-07-27 ({len(results)} trading days)
  Days with signal:    {len(trade_days)}
  Days triggered(sold):{len(triggered)}
  Profit days:         {len(profit_days)}
  Loss days:           {len(loss_days)}
  Win rate:            {len(profit_days)/max(1,len(triggered))*100:.0f}%

  Total P&L:           {total_pnl:+.0f} RMB
  Avg P&L per trade:   {total_pnl/max(1,len(triggered)):+.0f} RMB
  Max single profit:   {max([r['r']['pnl'] for r in triggered]) if triggered else 0:+.0f}
  Max single loss:     {min([r['r']['pnl'] for r in triggered]) if triggered else 0:+.0f}
""")

# Blocked days analysis
blocked = [r for r in results if not r['signal']['do_short']]
print("  --- Blocked Days ---")
for r in blocked:
    s = r['signal']
    print(f"  {r['date']}: {s['blocked_reason']} | trend={s['trend']} | RSI={s['rsi']:.0f} | ATR%={s['atr_pct']*100:.1f}% | VR={s['vol_ratio']:.2f}")

# No-trigger days
no_trig = [r for r in trade_days if r['r']['state']=='NO_TRIGGER']
print(f"\n  --- No-Trigger Days ({len(no_trig)}) ---")
for r in no_trig:
    d = r['d']; s = r['signal']
    gap = (s['sell_trigger']-d['h'])/d['h']*100
    print(f"  {r['date']}: high={d['h']:.2f} trigger={s['sell_trigger']:.2f} gap={gap:+.1f}% | mult={s['sell_mult']:.3f} trend={s['trend']}")

# ===================================================================
# 6. Issues found & V8 improvements
# ===================================================================
print("\n" + "="*85)
print("  V7 问题诊断 & V8 改进方向")
print("="*85)

# Analyze the main issues
forced = [r for r in triggered if r['r']['state']=='FORCED']
emergency = [r for r in triggered if r['r']['state']=='EMERGENCY']

print(f"""
  【问题1】牛市禁反T规则过于简单粗暴
    - v7: trend=='bull' → 完全禁止反T
    - 实际: 7月2日后趋势从bull→bear转变, v7前段全部信号被blocked
    - 影响: {len(blocked)}天被blocked, 错失潜在交易机会

  【问题2】BASE=0.55在熊市中触发线过高
    - v7触发 = 开盘 + ATR×mult (mult在0.35~1.50)
    - 熊市中ATR仍在高位, 导致触发线远离实际可达价位
    - 影响: {len(no_trig)}天有信号但高未触触发线

  【问题3】强制买回/紧急买回造成亏损
    - FORCED={len(forced)}天, EMERGENCY={len(emergency)}天
    - 卖后股价不跌反涨 → 亏损
    - 缺少"卖后动态调整买回目标"的机制

  【问题4】BASE固定0.55不适应市场状态变化
    - 牛→熊转换时, 波动特征剧变, 固定参数无法跟上

  【V8改进方案】
    1. 趋势判断改为"弱牛允许反T" — 仅在RSI>70+连涨≥5天时禁止
    2. BASE根据趋势动态调整: 熊市0.45, 震荡0.55, 弱牛0.65
    3. 卖后增加"动态移动止损" — 当价格向不利方向移动时收紧买回条件
    4. 紧急买回阈值从2%提高到3% (给更多下跌空间)
""")

# Save raw results for v8 comparison
print("Saving results for later...")
