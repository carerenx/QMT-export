# -*- coding: utf-8 -*-
"""验证日线OHLC回测 vs 5分钟K线回测的偏差"""
import json, urllib.request, sys

UA = "Mozilla/5.0"
CODE = "601869"
PB = 0.001; BBM = 0.15; BNC = 0.001
EMERG = 0.03; COMM = 0.00025; TAX = 0.001; LOT = 100

# ── Indicators ──
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

# V8 signal on daily data
V8B, V8S, V8W, V8N = 0.40, 0.55, 0.65, 0.20; VMX = 1.50; RCP = 0.80
def v8m(tr, ap, ar, vr, rs, us_):
    if tr == "bear": base = V8B
    elif tr == "weak_bull": base = V8W
    else: base = V8S
    t = 0.0
    if tr == "bear": d = -0.25 if us_ == 0 else -0.15
    elif tr == "weak_bull": d = 0.20 if us_ >= 3 else (0.12 if us_ >= 1 else 0.05)
    else: d = 0.00
    t += d
    ad  = -0.30 if ap>0.08 else (-0.22 if ap>0.07 else (-0.15 if ap>0.06 else (-0.08 if ap>0.05 else (0.05 if ap>0.03 else (0.15 if ap>0.02 else 0.25)))))
    ard = -0.25 if ar>1.50 else (-0.18 if ar>1.25 else (-0.10 if ar>1.10 else (0.00 if ar>0.90 else (0.12 if ar>0.70 else (0.20 if ar>0.50 else 0.25)))))
    vd = max(-0.35, min(0.30, ad*0.55+ard*0.45)); t += vd
    if vr>2.00: d=-0.25
    elif vr>1.50: d=-0.18
    elif vr>1.20: d=-0.08
    elif vr>0.80: d=0.00
    elif vr>0.60: d=0.12
    elif vr>0.40: d=0.20
    else: d=0.25
    t += d
    if rs>80: d=-0.25
    elif rs>70: d=-0.18
    elif rs>60: d=-0.08
    elif rs>55: d=-0.03
    elif rs>45: d=0.00
    elif rs>40: d=0.03
    elif rs>30: d=0.10
    elif rs>20: d=0.20
    else: d=0.25
    t += d
    return max(V8N, min(VMX, base+t)), base

def signal_v8(data, idx):
    if idx < 60: return None
    o=[d["o"] for d in data[:idx+1]]; h=[d["h"] for d in data[:idx+1]]
    l=[d["l"] for d in data[:idx+1]]; c=[d["c"] for d in data[:idx+1]]
    v=[d["v"] for d in data[:idx+1]]
    dt=data[idx]; co,cc,cv=dt["o"],dt["c"],dt["v"]
    aa=_atr(h,l,c,14); ca=aa[-1] or cc*0.03; cap=ca/cc if cc>0 else 0.03
    am20=_sma(aa,20)[-1] if idx>=20 else ca; ar=ca/am20 if am20>0 else 1.0
    ma5=_sma(c,5)[-1]; ma20=_sma(c,20)[-1]
    cr=_rsi(c)[-1]; us_=_us(c)[-1]
    ib=cc>ma20 and ma5>ma20; ibe=cc<ma20 and ma5<ma20
    if ib and cr>70 and us_>=5: tr="strong_bull"
    elif ib: tr="weak_bull"
    elif ibe: tr="bear"
    else: tr="sideways"
    mv=_sma(v,20); vr=cv/mv[-1] if mv[-1]>0 else 1.0
    sm,bu=v8m(tr,cap,ar,vr,cr,us_)
    drm10=_drm(h,l,o,10)[-1]; mt=co*(1.0+drm10*RCP); rw=co+ca*sm
    stg=round(min(rw,mt) if rw>mt else rw,2)
    ds=True; reason=""
    if tr=="strong_bull": ds=False; reason="strong_bull"
    elif vr<0.4: ds=False; reason="low_vol"
    elif cr>75: ds=False; reason="rsi_high"
    return {"ds":ds,"tr":tr,"stg":stg,"sm":sm,"base":bu,"cap":cap,"cr":cr,"vr":vr}

# ── Fetch data ──
def get_daily(code, n=150):
    prefix = "sh" if code.startswith(("6","9","0")) else "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{n},qfq"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[]) or \
        data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[])
    return [{"date":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

def get_5min(code):
    prefix = "sh" if code.startswith(("6","9","0")) else "sz"
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},m5,,,48"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("m5",[])
    return [{"t":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

# ── Sim on 5-min bars (real intraday sequence) ──
def sim_5min(signal, bars_5min):
    """Exactly the v8 state machine on 5-min bars — preserves REAL intraday sequence"""
    if signal is None or not signal["ds"]:
        return {"state":"BLOCKED","pnl":0,"sell_p":0,"buy_p":0,"peak":0,"dip":0}

    state = "IDLE"; peak = 0.0; sell_p = 0.0; dip_p = 0.0; buy_p = 0.0
    trigger = signal["stg"]; bb_target = 0.0; elapsed = 0

    for bar in bars_5min:
        # Use bar high as "price touched during bar", bar close as "sustained price"
        # Conservative approach: check trigger against high, execute against close
        price_hi = bar["h"]  # highest price in this 5-min window
        price_lo = bar["l"]  # lowest price in this 5-min window
        price = bar["c"]     # closing price of this 5-min window
        tm = bar["t"][8:12]  # HHMM

        if tm >= "1457":  # force close
            if state in ("SOLD","DIPPING"):
                buy_p = price
                gross = (sell_p - buy_p) * LOT
                fees = sell_p*LOT*(COMM+TAX) + buy_p*LOT*COMM
                return {"state":"FORCED","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"peak":peak,"dip":dip_p}
            break

        if state == "IDLE":
            if price_hi >= trigger:
                state = "SPIKING"
                peak = price_hi

        elif state == "SPIKING":
            if price_hi > peak:
                peak = price_hi
            pb = (peak - price) / peak
            if pb >= PB:
                sell_p = price
                state = "SOLD"
                ap = signal["cap"]
                bb_target = sell_p * (1.0 - ap * BBM)
                elapsed = 0
            elif price_hi < trigger:
                state = "IDLE"; peak = 0.0

        elif state == "SOLD":
            elapsed += 1
            # Emergency
            if price_hi >= sell_p * (1.0 + EMERG):
                buy_p = sell_p * (1.0 + EMERG)
                gross = (sell_p - buy_p) * LOT
                fees = sell_p*LOT*(COMM+TAX) + buy_p*LOT*COMM
                return {"state":"EMERGENCY","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"peak":peak,"dip":dip_p}
            # Tightening
            tbt = bb_target
            if elapsed > 6 and price > sell_p * 0.995:  # 6 bars = 30 min
                tbt = max(bb_target, sell_p * (1.0 - signal["cap"] * BBM * 0.60))
            if price_lo <= tbt:
                dip_p = price_lo
                state = "DIPPING"

        elif state == "DIPPING":
            if price_lo < dip_p:
                dip_p = price_lo
            bn = (price - dip_p) / dip_p if dip_p > 0 else 0
            if bn >= BNC:
                buy_p = price
                gross = (sell_p - buy_p) * LOT
                fees = sell_p*LOT*(COMM+TAX) + buy_p*LOT*COMM
                return {"state":"DONE","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"peak":peak,"dip":dip_p}
            elif price > bb_target:
                state = "SOLD"; dip_p = 0.0

    # End of day
    if state in ("SOLD","DIPPING"):
        buy_p = price
        gross = (sell_p - buy_p) * LOT
        fees = sell_p*LOT*(COMM+TAX) + buy_p*LOT*COMM
        return {"state":"FORCED","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"peak":peak,"dip":dip_p}

    return {"state":"NO_TRIG" if state=="IDLE" else "INCOMPLETE","pnl":0,"sell_p":sell_p,"buy_p":0,"peak":peak,"dip":dip_p}

# Sim on DAILY bars (the approximation used in report)
def sim_daily(d, signal):
    if signal is None or not signal["ds"]:
        return {"state":"BLOCKED","pnl":0,"sell_p":0,"buy_p":0}
    o,h,l,cv=d["o"],d["h"],d["l"],d["c"]; tg=signal["stg"]
    if h<tg: return {"state":"NO_TRIG","pnl":0,"sell_p":0,"buy_p":0}
    sp=h*(1.0-PB)
    if sp<tg: sp=tg
    bb=sp*(1.0-signal["cap"]*BBM); emg=sp*(1.0+EMERG)
    if cv>emg and h>emg: bp=emg; st="EMERGENCY"
    elif l<=bb: bd=max(l,bb*0.95); bp=bd*(1.0+BNC)
    if bp>bb: bp=bb
    st="DONE"
    gross=(sp-bp)*LOT; fees=sp*LOT*(COMM+TAX)+bp*LOT*COMM
    return {"state":st,"pnl":gross-fees,"sell_p":sp,"buy_p":bp}

# ── MAIN ──
print("Fetching data...")
daily = get_daily(CODE, 150)
bars_5min_all = get_5min(CODE)
print(f"Daily: {len(daily)} bars ({daily[0]['date']}~{daily[-1]['date']})")
print(f"5-min: {len(bars_5min_all)} bars total")

# Group 5-min bars by date
from collections import defaultdict
min5_by_date = defaultdict(list)
for bar in bars_5min_all:
    d = bar["t"][:4] + "-" + bar["t"][4:6] + "-" + bar["t"][6:8]
    min5_by_date[d].append(bar)

# Find dates with 5-min data
dates_with_5min = sorted(min5_by_date.keys())
print(f"Dates with 5-min data: {dates_with_5min[0]} ~ {dates_with_5min[-1]} ({len(dates_with_5min)} days)")

# Find overlapping dates with daily signal
overlap = [d for d in dates_with_5min if any(dd["date"]==d for dd in daily)]
print(f"Overlapping with daily: {len(overlap)} days")

# Run comparison
print(f"\n{'='*100}")
print(f"  日线OHLC近似 vs 5分钟K线真实序列 对比")
print(f"{'='*100}")
print(f"  {'Date':<12} {'Daily-H':>8} {'Daily-L':>8} {'Daily-Sim':>12} {'5min-Sim':>12} {'Error':>10} {'Sequence':>18} {'Note'}")
print(f"  {'-'*100}")

total_daily = 0; total_5min = 0; count = 0
mismatch = []

for d in overlap:
    # Find daily bar for signal
    daily_idx = None
    for i, dd in enumerate(daily):
        if dd["date"] == d:
            daily_idx = i
            break
    if daily_idx is None or daily_idx < 60: continue

    dd = daily[daily_idx]
    s = signal_v8(daily, daily_idx)
    if s is None or not s["ds"]: continue

    # Get 5-min bars for this day
    bars_today = min5_by_date[d]
    if len(bars_today) < 10: continue

    # Run both simulations
    r_daily = sim_daily(dd, s)
    r_5min = sim_5min(s, bars_today)

    # Analyze intraday sequence
    # Find when high and low occurred
    hi_time = max(bars_today, key=lambda b: b["h"])["t"][8:12]
    lo_time = min(bars_today, key=lambda b: b["l"])["t"][8:12]
    hi_first = hi_time < lo_time
    seq = "HIGH→LOW (有利)" if hi_first else "LOW→HIGH (不利)"

    # Determine if daily sim is valid
    if r_5min["state"] in ("DONE","FORCED","EMERGENCY"):
        count += 1
        total_daily += r_daily["pnl"]
        total_5min += r_5min["pnl"]
        error = r_daily["pnl"] - r_5min["pnl"]
        err_pct = error / abs(r_5min["pnl"]) * 100 if r_5min["pnl"] != 0 else 0

        note = ""
        if r_daily["state"] == "DONE" and r_5min["state"] == "FORCED":
            note = "日线模拟过度乐观(5min尾盘强平)"
            mismatch.append(("FORCED", d, error))
        elif r_daily["state"] == "DONE" and r_5min["state"] == "EMERGENCY":
            note = "日线模拟过度乐观(5min紧急买回)"
            mismatch.append(("EMERG", d, error))
        elif r_daily["state"] == "DONE" and r_5min["state"] == "DONE":
            if abs(error) > 500:
                note = "日线有偏差"
                mismatch.append(("BIAS", d, error))

        print(f"  {d:<12} {dd['h']:>8.2f} {dd['l']:>8.2f} {r_daily['pnl']:>+12.0f} {r_5min['pnl']:>+12.0f} {error:>+10.0f}  {seq:<18} {note}")
    else:
        print(f"  {d:<12} {dd['h']:>8.2f} {dd['l']:>8.2f} {'NO_TRIG':>12} {r_5min['state']:>12} {'--':>10}  {seq:<18} 5min未触发")

print(f"\n  {'='*100}")
if count > 0:
    print(f"  对比天数: {count}")
    print(f"  日线模拟总盈亏: {total_daily:+,.0f}")
    print(f"  5分钟模拟总盈亏: {total_5min:+,.0f}")
    print(f"  偏差总额: {total_daily - total_5min:+,.0f} ({(total_daily-total_5min)/abs(total_5min)*100:+.1f}%)")
    print(f"")
    print(f"  偏差来源分析:")
    for reason, dt, err in mismatch:
        print(f"    {dt}: {err:+,.0f} ({reason})")

    print(f"")
    print(f"  结论:")
    overstatement = (total_daily - total_5min) / abs(total_5min) * 100 if total_5min != 0 else 0
    print(f"    日线OHLC近似 ≈ 5分钟真实序列 × {(1+overstatement/100):.2f}")
    print(f"    即: 日线模拟将盈亏高估了约 {overstatement:+.0f}%")
    print(f"    主要原因: 日线假设High总是先于Low, 而实际有{sum(1 for m in mismatch if m[0]=='FORCED')}天是Low先于High(被迫尾盘强平)")
else:
    print(f"  (无重叠触发日)")
