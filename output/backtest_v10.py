# -*- coding: utf-8 -*-
"""v10 5分钟K线回测验证 — 早盘结构过滤器效果"""
import json, urllib.request; from collections import defaultdict
UA = "Mozilla/5.0"; CODE = "601869"

def get_daily(code, n=150):
    prefix = "sh"; url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{n},qfq"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[]) or data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[])
    return [{"date":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

def get_5min(code):
    prefix = "sh"; url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},m5,,,500"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("m5",[])
    return [{"t":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

# Indicators
def _sma(v,p):
    n=len(v);r=[0.0]*n
    for i in range(p-1,n):r[i]=sum(v[i-p+1:i+1])/p
    return r
def _atr(h,l,c,p=14):
    n=len(c);tr=[0.0]*n
    for i in range(1,n):tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    r=[0.0]*n
    for i in range(p,n):r[i]=sum(tr[i-p+1:i+1])/p
    return r
def _rsi(c,p=14):
    n=len(c)
    if n<p+1:return[50.0]*n
    rsi=[50.0]*n;g,l=[],[]
    for i in range(1,n):
        d=c[i]-c[i-1];g.append(d if d>0 else 0);l.append(abs(d) if d<0 else 0)
    ag=sum(g[:p])/p;al=sum(l[:p])/p
    rsi[p]=100.0-100.0/(1+ag/al) if al>0 else 100.0
    for i in range(p,n-1):
        ag=(ag*(p-1)+g[i])/p;al=(al*(p-1)+l[i])/p
        rsi[i+1]=100.0-100.0/(1+ag/al) if al>0 else 100.0
    return rsi
def _us(c):
    n=len(c);s=[0]*n
    for i in range(1,n):s[i]=s[i-1]+1 if c[i]>c[i-1] else 0
    return s
def _drm(h,l,o,p=10):
    n=len(o);rng=[0.0]*n
    for i in range(n):
        if o[i]>0:rng[i]=(h[i]-l[i])/o[i]
    return _sma(rng,p)

# V8 signal
V8B,V8S,V8W,V8N=0.40,0.55,0.65,0.20;VMX=1.50;RCP=0.80
def v8m(tr,ap,ar,vr,rs,us_):
    if tr=="bear":base=V8B
    elif tr=="weak_bull":base=V8W
    else:base=V8S
    t=0.0
    if tr=="bear":d=-0.25 if us_==0 else -0.15
    elif tr=="weak_bull":d=0.20 if us_>=3 else (0.12 if us_>=1 else 0.05)
    else:d=0.00
    t+=d
    ad=-0.30 if ap>0.08 else (-0.22 if ap>0.07 else (-0.15 if ap>0.06 else (-0.08 if ap>0.05 else (0.05 if ap>0.03 else (0.15 if ap>0.02 else 0.25)))))
    ard=-0.25 if ar>1.50 else (-0.18 if ar>1.25 else (-0.10 if ar>1.10 else (0.00 if ar>0.90 else (0.12 if ar>0.70 else (0.20 if ar>0.50 else 0.25)))))
    vd=max(-0.35,min(0.30,ad*0.55+ard*0.45));t+=vd
    if vr>2.00:d=-0.25
    elif vr>1.50:d=-0.18
    elif vr>1.20:d=-0.08
    elif vr>0.80:d=0.00
    elif vr>0.60:d=0.12
    elif vr>0.40:d=0.20
    else:d=0.25;t+=d
    if rs>80:d=-0.25
    elif rs>70:d=-0.18
    elif rs>60:d=-0.08
    elif rs>55:d=-0.03
    elif rs>45:d=0.00
    elif rs>40:d=0.03
    elif rs>30:d=0.10
    elif rs>20:d=0.20
    else:d=0.25;t+=d
    return max(V8N,min(VMX,base+t)),base

def sig8(data,idx):
    if idx<60:return None
    o=[d["o"] for d in data[:idx+1]];h=[d["h"] for d in data[:idx+1]]
    l=[d["l"] for d in data[:idx+1]];c=[d["c"] for d in data[:idx+1]]
    v=[d["v"] for d in data[:idx+1]]
    dt=data[idx];co,cc,cv=dt["o"],dt["c"],dt["v"]
    aa=_atr(h,l,c,14);ca=aa[-1] or cc*0.03;cap=ca/cc if cc>0 else 0.03
    am20=_sma(aa,20)[-1] if idx>=20 else ca;ar=ca/am20 if am20>0 else 1.0
    ma5=_sma(c,5)[-1];ma20=_sma(c,20)[-1]
    cr=_rsi(c)[-1];us_=_us(c)[-1]
    ib=cc>ma20 and ma5>ma20;ibe=cc<ma20 and ma5<ma20
    if ib and cr>70 and us_>=5:tr="strong_bull"
    elif ib:tr="weak_bull"
    elif ibe:tr="bear"
    else:tr="sideways"
    mv=_sma(v,20);vr=cv/mv[-1] if mv[-1]>0 else 1.0
    sm,bu=v8m(tr,cap,ar,vr,cr,us_)
    drm10=_drm(h,l,o,10)[-1];mt=co*(1.0+drm10*RCP);rw=co+ca*sm
    stg=round(min(rw,mt) if rw>mt else rw,2)
    ds=True;reason=""
    if tr=="strong_bull":ds=False;reason="strong_bull"
    elif vr<0.4:ds=False;reason="low_vol"
    elif cr>75:ds=False;reason="rsi_high"
    return {"ds":ds,"tr":tr,"stg":stg,"sm":sm,"base":bu,"cap":cap,"cr":cr,"vr":vr,"o":co}

# Backtest engine
PB=0.001;BBM=0.15;BNC=0.001;EMERG=0.03;STOP=0.015
COMM=0.00025;TAX=0.001;LOT=100;FORCE="1457"

def sim_5min(signal, bars, morning_filter=True):
    """Run state machine on 5-min bars. If morning_filter=True, apply v10 filter."""
    if signal is None or not signal["ds"]:
        return {"state":"BLOCKED","pnl":0,"sell_p":0,"buy_p":0,"sell_tm":"","buy_tm":"","filtered":False}

    # ── v10 Morning Filter ──
    filtered = False
    if morning_filter and len(bars) >= 6:
        # First 6 bars (30 min) for observation
        morning = bars[:6]
        open_p = morning[0]["o"]  # opening price
        m_high = max(b["h"] for b in morning)
        m_low = min(b["l"] for b in morning)
        m_last = morning[-1]["c"]

        m_chg = (m_last - open_p) / open_p
        m_range = (m_high - m_low) / open_p
        m_dip = (m_low - open_p) / open_p  # negative = dropped

        # v10 filter rules
        if m_chg < -0.01:
            filtered = True  # 跌>1%, LOW→HIGH likely
        elif m_dip < -0.015:
            filtered = True  # 曾跌>1.5%, 结构风险
        elif m_range < 0.02:
            filtered = False  # 振幅<2%, 震荡豁免
        # else: filtered=False, normal

    if filtered:
        return {"state":"FILTERED","pnl":0,"sell_p":0,"buy_p":0,"sell_tm":"","buy_tm":"","filtered":True}

    # ── Only trade AFTER morning observation ──
    # In v10, trading only starts after 10:00 (bar index >= 6)
    trade_bars = [b for b in bars if b["t"][8:12] >= "1000"]
    if not trade_bars:
        return {"state":"NO_TRIG","pnl":0,"sell_p":0,"buy_p":0,"sell_tm":"","buy_tm":"","filtered":False}

    trade_bars = bars  # Actually, the state machine should also consider bars before 10:00 for trigger

    # Full state machine (same as before)
    state="IDLE";peak=0.0;sell_p=0.0;dip_p=0.0
    trigger=signal["stg"];bb_target=0.0;elapsed=0;sell_tm="";buy_tm=""

    for bar in bars:
        ph=bar["h"];pl=bar["l"];pc=bar["c"];tm=bar["t"][8:12]

        if tm >= FORCE:
            if state in ("SOLD","DIPPING"):
                buy_p=pc;buy_tm=tm
                gross=(sell_p-buy_p)*LOT;fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                return {"state":"FORCED","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"sell_tm":sell_tm,"buy_tm":buy_tm,"filtered":False}
            break

        # ★ v10: don't allow SELL before 10:00
        if tm < "1000" and state == "IDLE":
            continue  # 观察期内不触发

        # Stop loss
        if state=="SOLD":
            loss_limit = LOT * signal["o"] * STOP
            paper_loss = (sell_p - ph) * LOT
            if paper_loss < -loss_limit:
                buy_p=ph;buy_tm=tm
                gross=(sell_p-buy_p)*LOT;fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                return {"state":"STOP_LOSS","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"sell_tm":sell_tm,"buy_tm":buy_tm,"filtered":False}

        if state=="IDLE":
            if ph >= trigger:
                state="SPIKING";peak=ph
        elif state=="SPIKING":
            if ph>peak:peak=ph
            pb=(peak-pc)/peak
            if pb>=PB:
                sell_p=pc;state="SOLD";sell_tm=tm
                ap=signal["cap"];bb_target=sell_p*(1.0-ap*BBM);elapsed=0
            elif ph<trigger:
                state="IDLE";peak=0.0
        elif state=="SOLD":
            elapsed+=1
            emerg_line=sell_p*(1.0+EMERG)
            if ph>=emerg_line:
                buy_p=emerg_line;buy_tm=tm
                gross=(sell_p-buy_p)*LOT;fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                return {"state":"EMERGENCY","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"sell_tm":sell_tm,"buy_tm":buy_tm,"filtered":False}
            tbt=bb_target
            if elapsed>6 and pc>sell_p*0.995:
                tbt=max(bb_target,sell_p*(1.0-signal["cap"]*BBM*0.60))
            if pl<=tbt:
                dip_p=pl;state="DIPPING"
        elif state=="DIPPING":
            if pl<dip_p:dip_p=pl
            if dip_p>0:
                bn=(pc-dip_p)/dip_p
                if bn>=BNC:
                    buy_p=pc;buy_tm=tm
                    gross=(sell_p-buy_p)*LOT;fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                    return {"state":"DONE","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"sell_tm":sell_tm,"buy_tm":buy_tm,"filtered":False}
            if pc>bb_target:
                state="SOLD";dip_p=0.0

    if state in ("SOLD","DIPPING"):
        buy_p=pc;buy_tm=tm
        gross=(sell_p-buy_p)*LOT;fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
        return {"state":"FORCED","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,"sell_tm":sell_tm,"buy_tm":buy_tm,"filtered":False}

    return {"state":"NO_TRIG","pnl":0,"sell_p":0,"buy_p":0,"sell_tm":"","buy_tm":"","filtered":False}

# ===================================================================
# MAIN
# ===================================================================
print("Fetching...")
daily=get_daily(CODE,150);bars_all=get_5min(CODE)
m5=defaultdict(list)
for bar in bars_all:
    d=bar["t"][:4]+"-"+bar["t"][4:6]+"-"+bar["t"][6:8];m5[d].append(bar)
dates=sorted(m5.keys())

# Run V8 (no filter) vs V10 (with filter)
print(f"\n{'='*95}")
print(f"  V8 (无过滤) vs V10 (早盘结构过滤) 对比  |  {dates[0]} ~ {dates[-1]}")
print(f"{'='*95}")
print(f"  {'Date':<12} {'走势':<20} {'早盘涨跌':>8} {'V8状态':>12} {'V8盈亏':>8} | {'V10决策':>20} {'V10状态':>12} {'V10盈亏':>8}")
print(f"  {'-'*95}")

v8_total=0;v10_total=0;v8_n=0;v10_n=0
v10_blocked=0;v10_saved=0

for d in dates:
    di=None
    for i,dd in enumerate(daily):
        if dd["date"]==d:di=i;break
    if di is None or di<60:continue
    dd=daily[di];s=sig8(daily,di)
    if s is None:continue
    bars_today=m5[d]

    # V8 (no filter)
    r8=sim_5min(s,bars_today,morning_filter=False)

    # V10 (with filter)
    r10=sim_5min(s,bars_today,morning_filter=True)

    # Sequence info
    hi_bar=max(bars_today,key=lambda b:b["h"]);lo_bar=min(bars_today,key=lambda b:b["l"])
    hi_t=hi_bar["t"][8:12];lo_t=lo_bar["t"][8:12]
    seq="HIGH->LOW" if hi_t<lo_t else "LOW->HIGH"
    morning=bars_today[:6] if len(bars_today)>=6 else bars_today
    if len(morning)>=3: mchg=(morning[-1]["c"]-morning[0]["o"])/morning[0]["o"]*100
    else:mchg=0

    if r8["state"] not in ("BLOCKED","NO_TRIG","INCOMPLETE"):
        v8_total+=r8["pnl"];v8_n+=1
    if r10["state"] not in ("BLOCKED","NO_TRIG","INCOMPLETE","FILTERED"):
        v10_total+=r10["pnl"];v10_n+=1
    if r10["filtered"]:
        v10_blocked+=1
        # Check if we avoided a loss
        if r8["state"] not in ("BLOCKED","NO_TRIG") and r8["pnl"]<0:
            v10_saved+=abs(r8["pnl"])

    v10_decision="APPROVED" if not r10["filtered"] else "FILTERED"
    v8_state=r8["state"];v10_state=r10["state"]
    v8_pnl=f"{r8['pnl']:+,.0f}" if r8["state"] not in ("BLOCKED","NO_TRIG","INCOMPLETE") else "--"
    v10_pnl=f"{r10['pnl']:+,.0f}" if r10["state"] not in ("BLOCKED","NO_TRIG","INCOMPLETE","FILTERED") else "--"

    print(f"  {d:<12} {seq:<20} {mchg:>+7.1f}% {v8_state:>12} {v8_pnl:>8} | {v10_decision:>20} {v10_state:>12} {v10_pnl:>8}")

print(f"  {'-'*95}")
print(f"  {'SUMMARY':<12} {'':<20} {'':>8} {'V8 trades:'+str(v8_n):>12} {v8_total:>+8.0f} | {'FILTERED:'+str(v10_blocked):>20} {'V10 trades:'+str(v10_n):>12} {v10_total:>+8.0f}")
print(f"  {'':<12} {'':<20} {'':>8} {'':>12} {'':>8} | {'亏损规避:'+str(v10_saved):>20}")

improvement=v10_total-v8_total
print(f"\n  V10 vs V8: {improvement:+,.0f} RMB | 过滤 {v10_blocked} 天 | 规避亏损 {v10_saved:,.0f} RMB")
if v8_n>0:print(f"  V8 平均每笔: {v8_total/v8_n:+,.0f}")
if v10_n>0:print(f"  V10 平均每笔: {v10_total/v10_n:+,.0f}")
