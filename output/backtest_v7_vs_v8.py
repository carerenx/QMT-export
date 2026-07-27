# -*- coding: utf-8 -*-
"""v7 vs v8 对比回测 + 多周期"""
import json, time, urllib.request

UA = "Mozilla/5.0"

# ===================================================================
# Data
# ===================================================================
def get_daily_klines(code, n=300):
    prefix = "sh" if code.startswith(("6","9","0")) else "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{n},qfq"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[]) or \
        data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[])
    return [{"date":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

# ===================================================================
# Indicators
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
# V7 Strategy
# ===================================================================
V7_BASE = 0.55; V7_MIN = 0.35; V7_MAX = 1.50; V7_RANGE_CAP = 0.80
V7_PB = 0.001; V7_BB_MULT = 0.15; V7_BOUNCE = 0.001; V7_EMERG = 0.02

def v7_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    total = 0.0
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
    return max(V7_MIN, min(V7_MAX, V7_BASE + total))

def v7_signal(data, idx):
    if idx < 60: return None
    o=[d['o'] for d in data[:idx+1]]; h=[d['h'] for d in data[:idx+1]]
    l=[d['l'] for d in data[:idx+1]]; c=[d['c'] for d in data[:idx+1]]
    v=[d['v'] for d in data[:idx+1]]
    d_t = data[idx]; co, cc, cv = d_t['o'], d_t['c'], d_t['v']
    atr_arr = _atr(h,l,c,14); curr_atr = atr_arr[-1] or cc*0.03
    curr_atr_pct = curr_atr/cc if cc>0 else 0.03
    atr_ma20 = _sma(atr_arr,20)[-1] if idx>=20 else curr_atr
    atr_ratio = curr_atr/atr_ma20 if atr_ma20>0 else 1.0
    ma5=_sma(c,5)[-1]; ma20=_sma(c,20)[-1]
    trend = 'bull' if (cc>ma20 and ma5>ma20) else ('bear' if (cc<ma20 and ma5<ma20) else 'sideways')
    curr_rsi=_rsi(c)[-1]; up_streak=_up_streak(c)[-1]
    ma20_vol=_sma(v,20); curr_vr=cv/ma20_vol[-1] if ma20_vol[-1]>0 else 1.0
    sell_mult = v7_mult(trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak)
    daily_range_ma10=_daily_range_ma(h,l,o,10)[-1]
    max_trig=co*(1.0+daily_range_ma10*V7_RANGE_CAP)
    raw=co+curr_atr*sell_mult
    sell_trigger=round(min(raw,max_trig) if raw>max_trig else raw,2)
    do_short,reason=True,''
    if trend=='bull': do_short,reason=False,'牛市'
    elif curr_vr<0.4: do_short,reason=False,'缩量'
    elif curr_rsi>75: do_short,reason=False,'RSI'
    return {'do_short':do_short,'trend':trend,'trigger':sell_trigger,'mult':sell_mult,'atr_pct':curr_atr_pct,'rsi':curr_rsi,'vr':curr_vr,'blocked':reason}

# ===================================================================
# V8 Strategy
# ===================================================================
V8_BASE_BEAR=0.40; V8_BASE_SIDE=0.55; V8_BASE_WBULL=0.65
V8_MIN=0.20; V8_EMERG=0.03

def v8_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    if trend == 'bear': base = V8_BASE_BEAR
    elif trend == 'weak_bull': base = V8_BASE_WBULL
    else: base = V8_BASE_SIDE
    total = 0.0
    if trend=='bear': d=-0.25 if up_streak==0 else -0.15
    elif trend=='strong_bull': d=999
    elif trend=='weak_bull': d=0.20 if up_streak>=3 else (0.12 if up_streak>=1 else 0.05)
    else: d=0.00
    total+=d
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
    vol_d=max(-0.35,min(0.30,atr_d*0.55+atrd_d*0.45)); total+=vol_d
    if vol_ratio>2.00: d=-0.25
    elif vol_ratio>1.50: d=-0.18
    elif vol_ratio>1.20: d=-0.08
    elif vol_ratio>0.80: d=0.00
    elif vol_ratio>0.60: d=0.12
    elif vol_ratio>0.40: d=0.20
    else: d=0.25
    total+=d
    if rsi_val>80: d=-0.25
    elif rsi_val>70: d=-0.18
    elif rsi_val>60: d=-0.08
    elif rsi_val>55: d=-0.03
    elif rsi_val>45: d=0.00
    elif rsi_val>40: d=0.03
    elif rsi_val>30: d=0.10
    elif rsi_val>20: d=0.20
    else: d=0.25
    total+=d
    return max(V8_MIN, min(V7_MAX, base+total)), base

def v8_signal(data, idx):
    if idx < 60: return None
    o=[d['o'] for d in data[:idx+1]]; h=[d['h'] for d in data[:idx+1]]
    l=[d['l'] for d in data[:idx+1]]; c=[d['c'] for d in data[:idx+1]]
    v=[d['v'] for d in data[:idx+1]]
    d_t=data[idx]; co,cc,cv=d_t['o'],d_t['c'],d_t['v']
    atr_arr=_atr(h,l,c,14); curr_atr=atr_arr[-1] or cc*0.03
    curr_atr_pct=curr_atr/cc if cc>0 else 0.03
    atr_ma20=_sma(atr_arr,20)[-1] if idx>=20 else curr_atr
    atr_ratio=curr_atr/atr_ma20 if atr_ma20>0 else 1.0
    ma5=_sma(c,5)[-1]; ma20=_sma(c,20)[-1]
    curr_rsi=_rsi(c)[-1]; up_streak=_up_streak(c)[-1]
    is_bull=cc>ma20 and ma5>ma20; is_bear=cc<ma20 and ma5<ma20
    if is_bull and curr_rsi>70 and up_streak>=5: trend='strong_bull'
    elif is_bull: trend='weak_bull'
    elif is_bear: trend='bear'
    else: trend='sideways'
    ma20_vol=_sma(v,20); curr_vr=cv/ma20_vol[-1] if ma20_vol[-1]>0 else 1.0
    sell_mult,base_used=v8_mult(trend,curr_atr_pct,atr_ratio,curr_vr,curr_rsi,up_streak)
    daily_range_ma10=_daily_range_ma(h,l,o,10)[-1]
    max_trig=co*(1.0+daily_range_ma10*V7_RANGE_CAP)
    raw=co+curr_atr*sell_mult
    sell_trigger=round(min(raw,max_trig) if raw>max_trig else raw,2)
    do_short,reason=True,''
    if trend=='strong_bull': do_short,reason=False,'强牛'
    elif curr_vr<0.4: do_short,reason=False,'缩量'
    elif curr_rsi>75: do_short,reason=False,'RSI'
    return {'do_short':do_short,'trend':trend,'trigger':sell_trigger,'mult':sell_mult,'base':base_used,'atr_pct':curr_atr_pct,'rsi':curr_rsi,'vr':curr_vr,'blocked':reason}

# ===================================================================
# Backtest Simulator (daily OHLC approximation)
# ===================================================================
COMM=0.00025; TAX=0.001; LOT=100

def sim_day(d, signal, emergency_pct):
    """日线OHLC近似模拟"""
    if signal is None or not signal['do_short']:
        return {'state':'BLOCKED','pnl':0,'sell_p':0,'buy_p':0}
    o,h,l,c_val=d['o'],d['h'],d['l'],d['c']
    trigger=signal['trigger']
    if h<trigger: return {'state':'NO_TRIG','pnl':0,'sell_p':0,'buy_p':0}
    peak=h; sell_p=h*(1.0-V7_PB)
    if sell_p<trigger: sell_p=trigger
    atr_pct=signal['atr_pct']
    bb_target=sell_p*(1.0-atr_pct*V7_BB_MULT)
    emerg_trig=sell_p*(1.0+emergency_pct)
    if c_val>emerg_trig and h>emerg_trig:
        buy_p=emerg_trig; state='EMERGENCY'
    elif l<=bb_target:
        buy_dip=max(l,bb_target*0.95)
        buy_p=buy_dip*(1.0+V7_BOUNCE)
        if buy_p>bb_target: buy_p=bb_target
        state='DONE'
    else:
        buy_p=c_val; state='FORCED'
    gross=(sell_p-buy_p)*LOT
    fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
    pnl=gross-fees
    return {'state':state,'pnl':pnl,'sell_p':sell_p,'buy_p':buy_p}

# ===================================================================
# Run Backtest
# ===================================================================
print("Fetching data...")
daily=get_daily_klines("601869", n=300)
print(f"{len(daily)} bars: {daily[0]['date']} ~ {daily[-1]['date']}")

test_periods = [
    ("7/2-7/27(近一月)", lambda d: '2026-07-02' <= d['date'] <= '2026-07-27'),
    ("6/23-7/27(6/23至今)", lambda d: '2026-06-23' <= d['date'] <= '2026-07-27'),
    ("6/1-7/27(近两月)",   lambda d: '2026-06-01' <= d['date'] <= '2026-07-27'),
    ("5/1-7/27(近三月)",   lambda d: '2026-05-01' <= d['date'] <= '2026-07-27'),
]

for period_name, period_filter in test_periods:
    period_data = [d for d in daily if period_filter(d)]
    if len(period_data) < 60: continue

    start_idx_in_full = daily.index(period_data[0])
    end_idx_in_full = daily.index(period_data[-1])

    v7_results = []; v8_results = []
    for i in range(start_idx_in_full, end_idx_in_full+1):
        d = daily[i]
        s7 = v7_signal(daily, i)
        s8 = v8_signal(daily, i)
        v7_results.append({'date':d['date'],'d':d,'signal':s7,'r':sim_day(d,s7,V7_EMERG)})
        v8_results.append({'date':d['date'],'d':d,'signal':s8,'r':sim_day(d,s8,V8_EMERG)})

    v7_total = sum(r['r']['pnl'] for r in v7_results)
    v8_total = sum(r['r']['pnl'] for r in v8_results)
    v7_trig = [r for r in v7_results if r['r']['state'] not in ('BLOCKED','NO_TRIG')]
    v8_trig = [r for r in v8_results if r['r']['state'] not in ('BLOCKED','NO_TRIG')]
    v7_blocked = sum(1 for r in v7_results if not r['signal']['do_short'])
    v8_blocked = sum(1 for r in v8_results if not r['signal']['do_short'])
    v7_win = sum(1 for r in v7_trig if r['r']['pnl']>0)
    v8_win = sum(1 for r in v8_trig if r['r']['pnl']>0)

    print(f"\n{'='*90}")
    print(f"  PERIOD: {period_name} ({period_data[0]['date']} ~ {period_data[-1]['date']}, {len(period_data)} days)")
    print(f"{'='*90}")
    print(f"  {'':<20} {'V7':^20} {'V8':^20} {'V8 vs V7':^15}")
    print(f"  {'-'*75}")
    print(f"  {'Trading Days':<20} {len(period_data):>20} {len(period_data):>20}")
    print(f"  {'Blocked Days':<20} {v7_blocked:>20} {v8_blocked:>20}")
    print(f"  {'Signal Days':<20} {len(period_data)-v7_blocked:>20} {len(period_data)-v8_blocked:>20}")
    print(f"  {'Days Triggered':<20} {len(v7_trig):>20} {len(v8_trig):>20} {len(v8_trig)-len(v7_trig):>+15}")
    print(f"  {'Win Rate':<20} {f'{v7_win}/{len(v7_trig)}={v7_win/max(1,len(v7_trig))*100:.0f}%' if v7_trig else 'N/A':>20} {f'{v8_win}/{len(v8_trig)}={v8_win/max(1,len(v8_trig))*100:.0f}%' if v8_trig else 'N/A':>20}")
    print(f"  {'TOTAL P&L (RMB)':<20} {v7_total:>+20.0f} {v8_total:>+20.0f} {v8_total-v7_total:>+15.0f}")
    print(f"  {'Avg P&L/Trade':<20} {f'{v7_total/max(1,len(v7_trig)):+.0f}' if v7_trig else 'N/A':>20} {f'{v8_total/max(1,len(v8_trig)):+.0f}' if v8_trig else 'N/A':>20}")
    if v7_trig:
        print(f"  {'Max Profit':<20} {max(r['r']['pnl'] for r in v7_trig):>+20.0f} {max(r['r']['pnl'] for r in v8_trig) if v8_trig else 'N/A':>20}")
        print(f"  {'Max Loss':<20} {min(r['r']['pnl'] for r in v7_trig):>+20.0f} {min(r['r']['pnl'] for r in v8_trig) if v8_trig else 'N/A':>20}")

# ===================================================================
# Detailed day-by-day comparison for 7/2-7/27
# ===================================================================
print(f"\n\n{'='*100}")
print(f"  V7 vs V8 逐日对比 (7月2日 ~ 7月27日)")
print(f"{'='*100}")

july_data = [d for d in daily if '2026-07-02' <= d['date'] <= '2026-07-27']
july_start = daily.index(july_data[0])
july_end = daily.index(july_data[-1])

print(f"  {'Date':<12} {'Trend':>10} {'V7-Mult':>7} {'V7-Trig':>8} {'V7-PnL':>8} {'V8-Trend':>10} {'V8-Base':>7} {'V8-Mult':>7} {'V8-Trig':>8} {'V8-PnL':>8} {'Delta':>8}")
print(f"  {'-'*100}")

v7_total=0; v8_total=0; v7_trig_cnt=0; v8_trig_cnt=0
for i in range(july_start, july_end+1):
    d = daily[i]
    s7 = v7_signal(daily, i); s8 = v8_signal(daily, i)
    r7 = sim_day(d, s7, V7_EMERG); r8 = sim_day(d, s8, V8_EMERG)
    v7_total+=r7['pnl']; v8_total+=r8['pnl']
    if r7['state'] not in ('BLOCKED','NO_TRIG'): v7_trig_cnt+=1
    if r8['state'] not in ('BLOCKED','NO_TRIG'): v8_trig_cnt+=1

    s7_do = 'YES' if s7['do_short'] else 'NO'; s8_do = 'YES' if s8['do_short'] else 'NO'
    s7_trend = s7['trend'] if s7 else '?'; s8_trend = s8['trend'] if s8 else '?'

    # Only print days where v7 or v8 triggered or had a change
    v7_trig_mark = '*' if r7['state'] not in ('BLOCKED','NO_TRIG') else ' '
    v8_trig_mark = '*' if r8['state'] not in ('BLOCKED','NO_TRIG') else ' '

    print(f"  {d['date']:<12} {s7_trend:>10} {s7['mult']:>6.3f} {s7['trigger']:>8.2f} {v7_trig_mark}{r7['pnl']:>+7.0f} "
          f"{s8_trend:>10} {s8['base'] if s8 else 0:>6.2f} {s8['mult']:>6.3f} {s8['trigger']:>8.2f} {v8_trig_mark}{r8['pnl']:>+7.0f} "
          f"{r8['pnl']-r7['pnl']:>+8.0f}")

print(f"  {'-'*100}")
print(f"  {'TOTAL':<12} {'':>10} {'':>7} {'':>8} {v7_total:>+8.0f} {'':>10} {'':>7} {'':>7} {'':>8} {v8_total:>+8.0f} {v8_total-v7_total:>+8.0f}")
print(f"  {'Triggers':<12} {'':>10} {'':>7} {'':>8} {v7_trig_cnt:>8} {'':>10} {'':>7} {'':>7} {'':>8} {v8_trig_cnt:>8}")

print(f"\n  *=triggered (sold & bought back)")

# ===================================================================
# Multi-period summary table
# ===================================================================
print(f"\n\n{'='*90}")
print(f"  多周期回测汇总")
print(f"{'='*90}")
print(f"  {'Period':<25} {'V7 P&L':>10} {'V7#Tr':>6} {'V7%Win':>7} {'V8 P&L':>10} {'V8#Tr':>6} {'V8%Win':>7} {'Delta':>10}")
print(f"  {'-'*75}")

# Calculate for additional periods
extra_periods = [
    ("2026-03-01~04-30(上升期)", lambda d: '2026-03-01'<=d['date']<='2026-04-30'),
    ("2026-05-01~06-30(冲顶期)", lambda d: '2026-05-01'<=d['date']<='2026-06-30'),
]

for pname, pfilter in extra_periods:
    pd_data = [d for d in daily if pfilter(d)]
    if len(pd_data)<60: continue
    si = daily.index(pd_data[0]); ei = daily.index(pd_data[-1])
    v7r=[]; v8r=[]
    for i in range(si, ei+1):
        d=daily[i]; s7=v7_signal(daily,i); s8=v8_signal(daily,i)
        v7r.append(sim_day(d,s7,V7_EMERG)); v8r.append(sim_day(d,s8,V8_EMERG))
    v7t=[r for r in v7r if r['state'] not in ('BLOCKED','NO_TRIG')]
    v8t=[r for r in v8r if r['state'] not in ('BLOCKED','NO_TRIG')]
    v7p=sum(r['pnl'] for r in v7r); v8p=sum(r['pnl'] for r in v8r)
    v7w=sum(1 for r in v7t if r['pnl']>0); v8w=sum(1 for r in v8t if r['pnl']>0)
    print(f"  {pname:<25} {v7p:>+10.0f} {len(v7t):>6} {f'{v7w/max(1,len(v7t))*100:.0f}%' if v7t else 'N/A':>7} {v8p:>+10.0f} {len(v8t):>6} {f'{v8w/max(1,len(v8t))*100:.0f}%' if v8t else 'N/A':>7} {v8p-v7p:>+10.0f}")

print()
print("  V8 IMPROVEMENTS OVER V7:")
print("    1. Lower mult_min (0.35->0.20): captures more trades in bear markets")
print("    2. Adaptive BASE (bear=0.40/side=0.55/wbull=0.65): fits market regime")
print("    3. 4-level trend (strong/weak bull): allows 反T in moderate uptrends")
print("    4. Emergency buyback raised (2%->3%): gives more room for price to dip")
