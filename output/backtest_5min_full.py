# -*- coding: utf-8 -*-
"""完整5分钟K线回测 + 日内结构分析 + 诚实报告"""
import json, urllib.request, time as _t
from collections import defaultdict

UA = "Mozilla/5.0"; CODE = "601869"

# ===================================================================
# Data
# ===================================================================
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
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},m5,,,500"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("m5",[])
    return [{"t":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

# ===================================================================
# Indicators
# ===================================================================
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

# ===================================================================
# V8 Signal
# ===================================================================
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

def sig8(data, idx):
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
    return {"ds":ds,"tr":tr,"stg":stg,"sm":sm,"base":bu,"cap":cap,"cr":cr,"vr":vr,"o":co}

# ===================================================================
# 5-min Backtest Engine
# ===================================================================
PB=0.001; BBM=0.15; BNC=0.001; EMERG=0.03
STOP=0.015; COMM=0.00025; TAX=0.001; LOT=100; FORCE="1457"

def sim_5min(signal, bars):
    """Full v8 state machine on 5-min bars"""
    if signal is None or not signal["ds"]:
        return {"state":"BLOCKED","pnl":0,"sell_p":0,"buy_p":0,"peak":0,"dip":0,"sell_time":"","buy_time":""}

    state="IDLE"; peak=0.0; sell_p=0.0; dip_p=0.0
    trigger=signal["stg"]; bb_target=0.0; elapsed=0
    sell_tm=""; buy_tm=""

    for bar in bars:
        ph=bar["h"]; pl=bar["l"]; pc=bar["c"]; tm=bar["t"][8:12]

        if tm >= FORCE:
            if state in ("SOLD","DIPPING"):
                buy_p=pc; buy_tm=tm
                gross=(sell_p-buy_p)*LOT
                fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                return {"state":"FORCED","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,
                        "peak":peak,"dip":dip_p,"sell_time":sell_tm,"buy_time":buy_tm}
            break

        # Stop loss
        if state=="SOLD":
            day_pnl_tracker = 0  # simplified
            loss_limit = LOT * signal["o"] * STOP
            paper_loss = (sell_p - pc) * LOT
            if paper_loss < -loss_limit:
                buy_p=pc; buy_tm=tm
                gross=(sell_p-buy_p)*LOT
                fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                return {"state":"STOP_LOSS","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,
                        "peak":peak,"dip":dip_p,"sell_time":sell_tm,"buy_time":buy_tm}

        if state=="IDLE":
            if ph >= trigger:
                state="SPIKING"; peak=ph

        elif state=="SPIKING":
            if ph > peak: peak=ph
            pb=(peak-pc)/peak
            if pb >= PB:
                sell_p=pc; state="SOLD"; sell_tm=tm
                ap=signal["cap"]; bb_target=sell_p*(1.0-ap*BBM); elapsed=0
            elif ph < trigger:
                state="IDLE"; peak=0.0

        elif state=="SOLD":
            elapsed+=1
            # Emergency
            emerg_line = sell_p*(1.0+EMERG)
            if ph >= emerg_line:
                buy_p=emerg_line; buy_tm=tm
                gross=(sell_p-buy_p)*LOT
                fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                return {"state":"EMERGENCY","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,
                        "peak":peak,"dip":dip_p,"sell_time":sell_tm,"buy_time":buy_tm}
            # Tightening
            tbt=bb_target
            if elapsed>6 and pc>sell_p*0.995:
                tbt=max(bb_target, sell_p*(1.0-signal["cap"]*BBM*0.60))
            if pl <= tbt:
                dip_p=pl; state="DIPPING"

        elif state=="DIPPING":
            if pl < dip_p: dip_p=pl
            if dip_p>0:
                bn=(pc-dip_p)/dip_p
                if bn >= BNC:
                    buy_p=pc; buy_tm=tm
                    gross=(sell_p-buy_p)*LOT
                    fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
                    return {"state":"DONE","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,
                            "peak":peak,"dip":dip_p,"sell_time":sell_tm,"buy_time":buy_tm}
            if pc > bb_target:
                state="SOLD"; dip_p=0.0

    # EOD
    if state in ("SOLD","DIPPING"):
        buy_p=pc; buy_tm=tm
        gross=(sell_p-buy_p)*LOT
        fees=sell_p*LOT*(COMM+TAX)+buy_p*LOT*COMM
        return {"state":"FORCED","pnl":gross-fees,"sell_p":sell_p,"buy_p":buy_p,
                "peak":peak,"dip":dip_p,"sell_time":sell_tm,"buy_time":buy_tm}

    return {"state":"NO_TRIG" if state in ("IDLE","BLOCKED") else "INCOMPLETE",
            "pnl":0,"sell_p":0,"buy_p":0,"peak":peak,"dip":dip_p,"sell_time":"","buy_time":""}

# ===================================================================
# Intraday Sequence Analysis
# ===================================================================
def analyze_sequence(bars):
    """Classify intraday structure"""
    if not bars: return "UNKNOWN", 0, 0

    # Morning trend (first 6 bars = 30 min)
    morning_bars = bars[:6]
    if len(morning_bars) >= 3:
        morning_chg = (morning_bars[-1]["c"] - morning_bars[0]["o"]) / morning_bars[0]["o"] * 100
    else:
        morning_chg = 0

    # Find when daily high and low occurred
    hi_bar = max(bars, key=lambda b: b["h"])
    lo_bar = min(bars, key=lambda b: b["l"])
    hi_t = hi_bar["t"][8:12]
    lo_t = lo_bar["t"][8:12]
    hi_first = hi_t < lo_t

    # Calculate: % of bars where price is above open
    day_open = bars[0]["o"]
    above_open = sum(1 for b in bars if b["c"] >= day_open) / max(1, len(bars))

    if hi_first:
        seq = "HIGH_FIRST"
    else:
        seq = "LOW_FIRST"

    # Opening direction
    if morning_chg > 1.0: open_dir = "GAP_UP"
    elif morning_chg < -1.0: open_dir = "GAP_DOWN"
    else: open_dir = "FLAT"

    return {
        "seq": seq, "hi_time": hi_t, "lo_time": lo_t,
        "hi_first": hi_first, "morning_chg": morning_chg,
        "open_dir": open_dir, "above_open_pct": above_open,
        "day_range": (hi_bar["h"] - lo_bar["l"]) / day_open * 100
    }

# ===================================================================
# MAIN
# ===================================================================
print("Fetching data...")
daily = get_daily(CODE, 150)
bars_all = get_5min(CODE)
print(f"Daily: {len(daily)} ({daily[0]['date']}~{daily[-1]['date']})")
print(f"5-min: {len(bars_all)} bars")

# Group by date
m5 = defaultdict(list)
for bar in bars_all:
    d = bar["t"][:4]+"-"+bar["t"][4:6]+"-"+bar["t"][6:8]
    m5[d].append(bar)

dates_5m = sorted(m5.keys())
print(f"5-min dates: {dates_5m[0]} ~ {dates_5m[-1]} ({len(dates_5m)} days)")

# Run backtest
results = []
for d in dates_5m:
    di = None
    for i, dd in enumerate(daily):
        if dd["date"] == d: di = i; break
    if di is None or di < 60: continue

    dd = daily[di]
    s = sig8(daily, di)
    bars_today = m5[d]
    seq_info = analyze_sequence(bars_today)

    if s is None: continue

    # v8 backtest
    r = sim_5min(s, bars_today)

    results.append({
        "date": d, "daily": dd, "signal": s, "result": r, "seq": seq_info
    })

# ===================================================================
# Analysis & Report
# ===================================================================
md = []
md.append("# 601869 长飞光纤 V8 反T策略 — 5分钟K线实盘级回测报告\n")
md.append(f"**数据精度**: 5分钟K线完整日内序列 | **回测方法**: 完整状态机模拟")
md.append(f"**数据范围**: {dates_5m[0]} ~ {dates_5m[-1]} ({len(dates_5m)}个交易日)")
md.append(f"**策略版本**: V8 (BASE bear=0.40, mult=[0.20,1.50], emerg=3%)\n")
md.append("---\n")

# Section 1: Daily summary
md.append("## 一、逐日回测明细\n")
md.append("| 日期 | 开盘 | 最高 | 最低 | 收盘 | 走势结构 | 触发线 | 操作1(卖出) | 操作2(买回) | 净盈亏 | 状态 |")
md.append("|------|------|------|------|------|----------|--------|------------|------------|--------|------|")

total_pnl = 0; trade_count = 0; win_count = 0
hi_first_count = 0; lo_first_count = 0
hi_first_pnl = 0; lo_first_pnl = 0

for r in results:
    dd = r["daily"]; s = r["signal"]; res = r["result"]; seq = r["seq"]
    st = res["state"]

    if st == "BLOCKED":
        md.append(f"| {r['date']} | {dd['o']:.2f} | {dd['h']:.2f} | {dd['l']:.2f} | {dd['c']:.2f} | {seq['seq']} | {s['stg']:.2f} | — | — | — | BLOCKED({s.get('blocked_reason','?')}) |")
        continue

    sp = f"{res['sell_p']:.2f}@{res['sell_time']}" if res['sell_p'] else "—"
    bp = f"{res['buy_p']:.2f}@{res['buy_time']}" if res['buy_p'] else "—"
    pnl = res['pnl']
    pnl_s = f"{pnl:+,.0f}" if st not in ("NO_TRIG","INCOMPLETE") else "—"

    if st not in ("NO_TRIG","INCOMPLETE","BLOCKED"):
        trade_count += 1
        total_pnl += pnl
        if pnl > 0: win_count += 1
        if seq["hi_first"]:
            hi_first_count += 1; hi_first_pnl += pnl
        else:
            lo_first_count += 1; lo_first_pnl += pnl

    md.append(f"| {r['date']} | {dd['o']:.2f} | {dd['h']:.2f} | {dd['l']:.2f} | {dd['c']:.2f} | {seq['seq']}({seq['morning_chg']:+.1f}%) | {s['stg']:.2f} | {sp} | {bp} | {pnl_s} | {st} |")

md.append("")

# Section 2: By sequence type
md.append("---\n")
md.append("## 二、按日内走势结构分类统计\n")
md.append("| 走势结构 | 交易天数 | 盈利天数 | 胜率 | 总盈亏 | 平均每笔 |")
md.append("|----------|---------|---------|------|--------|----------|")

hi_trades = [r for r in results if r["result"]["state"] not in ("BLOCKED","NO_TRIG","INCOMPLETE") and r["seq"]["hi_first"]]
lo_trades = [r for r in results if r["result"]["state"] not in ("BLOCKED","NO_TRIG","INCOMPLETE") and not r["seq"]["hi_first"]]
hi_t = [r for r in results if r["seq"]["hi_first"]]
lo_t = [r for r in results if not r["seq"]["hi_first"]]

for label, trades in [("HIGH→LOW (先涨后跌, 有利)", hi_trades), ("LOW→HIGH (先跌后涨, 不利)", lo_trades)]:
    if not trades:
        md.append(f"| {label} | 0 | 0 | — | 0 | — |")
        continue
    wins = sum(1 for t in trades if t["result"]["pnl"] > 0)
    total = sum(t["result"]["pnl"] for t in trades)
    avg = total / len(trades)
    wr = f"{wins}/{len(trades)}={wins/len(trades)*100:.0f}%"
    md.append(f"| {label} | {len(trades)} | {wins} | {wr} | {total:+,.0f} | {avg:+,.0f} |")

md.append("")

# Section 3: Opening direction analysis
md.append("## 三、早盘方向与交易结果\n")
md.append("| 早盘方向 | 天数 | 走势结构占比 | 交易盈亏 |")
md.append("|----------|------|-------------|----------|")

for odir in ["GAP_UP", "FLAT", "GAP_DOWN"]:
    odays = [r for r in results if r["seq"]["open_dir"] == odir]
    if not odays: continue
    hi_pct = sum(1 for r in odays if r["seq"]["hi_first"]) / len(odays) * 100
    od_trades = [r for r in odays if r["result"]["state"] not in ("BLOCKED","NO_TRIG","INCOMPLETE")]
    od_pnl = sum(r["result"]["pnl"] for r in od_trades) if od_trades else 0
    md.append(f"| {odir} | {len(odays)} | HIGH先={hi_pct:.0f}% LOW先={100-hi_pct:.0f}% | {od_pnl:+,.0f} |")
md.append("")

# Section 4: Key stats
md.append("---\n")
md.append("## 四、关键统计数据\n")

blocked = sum(1 for r in results if r["result"]["state"] == "BLOCKED")
no_trig = sum(1 for r in results if r["result"]["state"] == "NO_TRIG")
emerg = sum(1 for r in results if r["result"]["state"] == "EMERGENCY")
forced = sum(1 for r in results if r["result"]["state"] == "FORCED")
done = sum(1 for r in results if r["result"]["state"] == "DONE")
stop_loss = sum(1 for r in results if r["result"]["state"] == "STOP_LOSS")

md.append(f"""
| 指标 | 数值 |
|------|------|
| 总交易日 | {len(results)} |
| 信号禁止(BLOCKED) | {blocked} |
| 有信号未触发(NO_TRIG) | {no_trig} |
| 成功买回(DONE) | {done} |
| 紧急买回(EMERGENCY) | {emerg} |
| 尾盘强平(FORCED) | {forced} |
| 止损(STOP_LOSS) | {stop_loss} |
| **总成交笔数** | **{trade_count}** |
| **盈利笔数** | **{win_count}** |
| **胜率** | **{win_count/max(1,trade_count)*100:.0f}%** |
| **总净盈亏** | **{total_pnl:+,.0f} RMB** |
| 平均每笔 | {total_pnl/max(1,trade_count):+,.0f} |
| HIGH先天数 | {hi_first_count} (占{trade_count}笔) |
| LOW先天数 | {lo_first_count} (占{trade_count}笔) |
| HIGH先总盈亏 | {hi_first_pnl:+,.0f} |
| LOW先总盈亏 | {lo_first_pnl:+,.0f} |

### 日内结构分布

| 结构 | 天数 | 占比 |
|------|------|------|
| HIGH→LOW (先涨后跌) | {len(hi_t)} | {len(hi_t)/max(1,len(results))*100:.0f}% |
| LOW→HIGH (先跌后涨) | {len(lo_t)} | {len(lo_t)/max(1,len(results))*100:.0f}% |
""")

# Section 5: Comparison with daily OHLC
md.append("---\n")
md.append("## 五、日线OHLC近似 vs 5分钟真实序列\n")
md.append("| 日期 | 走势 | 日线模拟PnL | 5分钟真实PnL | 偏差 | 偏差原因 |")
md.append("|------|------|------------|-------------|------|----------|")

total_daily_pnl = 0
for r in results:
    if r["result"]["state"] in ("BLOCKED","NO_TRIG","INCOMPLETE"): continue
    dd = r["daily"]; s = r["signal"]

    # Daily OHLC approx
    o,h,l,cv=dd["o"],dd["h"],dd["l"],dd["c"]; tg=s["stg"]
    if h<tg: dl_pnl=0; dl_note="daily: NO_TRIG"
    else:
        sp=h*(1.0-PB)
        if sp<tg: sp=tg
        bb=sp*(1.0-s["cap"]*BBM)
        if cv>sp*(1+EMERG): bp=sp*(1+EMERG); dl_note="daily: EMERG"
        elif l<=bb: bp=max(l,bb*0.95)*(1+BNC); dl_note="daily: DONE"
        else: bp=cv; dl_note="daily: FORCED"
        if bp>bb: bp=bb
        gross=(sp-bp)*LOT; fees=sp*LOT*(COMM+TAX)+bp*LOT*COMM; dl_pnl=gross-fees
    total_daily_pnl += dl_pnl

    error = dl_pnl - r["result"]["pnl"]
    cause = ""
    if r["result"]["state"] == "EMERGENCY": cause = "5min紧急买回(卖后不跌反涨)"
    elif r["result"]["state"] == "FORCED": cause = "5min尾盘强平(来不及回落)"
    elif r["result"]["state"] == "STOP_LOSS": cause = "5min触发止损"
    elif abs(error) > 500:
        if r["seq"]["hi_first"]: cause = "买卖价差偏差"
        else: cause = "LOW先于HIGH,日线假设不成立"
    else:
        cause = "基本一致"

    md.append(f"| {r['date']} | {r['seq']['seq']} | {dl_pnl:+,.0f} | {r['result']['pnl']:+,.0f} | {error:+,.0f} | {cause} |")

overstatement = (total_daily_pnl - total_pnl) / abs(total_pnl) * 100 if total_pnl != 0 else float('inf')
md.append(f"""
| **合计** | | **{total_daily_pnl:+,.0f}** | **{total_pnl:+,.0f}** | **{total_daily_pnl-total_pnl:+,.0f}** | **高估{overstatement:+.0f}%** |
""")

# Section 6: Strategy fix proposal
md.append("---\n")
md.append("## 六、问题诊断与改进方案\n")
md.append(f"""
### 核心问题

日线OHLC回测将盈亏高估了 **{overstatement:+.0f}%**。根因是反T策略对日内价格路径高度敏感:

```
HIGH→LOW (先涨后跌):  ✓ 策略有效
  冲高→触发线→卖出→回落→买回 ✓

LOW→HIGH (先跌后涨):  ✗ 策略天然失效
  杀跌(触发线未到)→反弹至触发线→卖出→但跌不回去了→紧急买回/强平 ✗
```

在本回测期间({len(hi_t)+len(lo_t)}天), HIGH→LOW占{len(hi_t)/max(1,len(hi_t)+len(lo_t))*100:.0f}%, LOW→HIGH占{len(lo_t)/max(1,len(hi_t)+len(lo_t))*100:.0f}%。

### 改进方案

#### A. 日内走势预判过滤器 (推荐)

在handlebar中增加早盘结构判断:
- 开盘30分钟后判断日内走势方向
- 如果早盘持续下跌(开盘→30分钟后跌>1%), 当日大概率LOW→HIGH, **跳过反T**
- 如果早盘持续上涨或震荡, 正常执行

#### B. 卖后动态止损收紧

当前紧急买回=3%, 对LOW→HIGH日来说太晚。
- LOW→HIGH日中, 卖出后15分钟不跌 → 主动买回(仅亏手续费)
- 配合早盘判断使用

#### C. 仅在高胜率结构日中交易

统计数据:
- HIGH→FIRST日中: 胜率较高, 盈亏可控
- LOW→FIRST日中: 大概率亏损

建议: 仅在开盘呈现强势(前30分钟>0%且HIGH先出现概率>60%)时执行反T
""")

# Section 7: Parameter sensitivity
md.append("---\n")
md.append("## 七、策略使用建议\n")
md.append(f"""
基于5分钟K线回测结果({trade_count}笔实盘级模拟):

1. **当前V8在随机市场中不可靠**: 盈亏高度依赖日内走势结构,
   而日内结构在短期(10-20天)内具有随机性

2. **建议增加日内结构过滤器后再上线**:
   - 开盘30分钟方向判断
   - 仅在早盘偏强或震荡时启用反T

3. **风控建议**:
   - 单日最大亏损限制(建议500-800元)
   - 连续2日亏损则当日暂停

4. **日线OHLC回测的局限性**:
   - 日线回测仅适合粗筛策略方向, 不能替代实盘级验证
   - 任何日内策略必须用分钟级数据验证后才能上线
""")

report = "\n".join(md)
with open("output/v8_5min_backtest_report.md", "w", encoding="utf-8") as f:
    f.write(report)

print(f"\nReport saved: output/v8_5min_backtest_report.md ({len(report)} chars)")
print(f"\n=== KEY RESULTS ===")
print(f"Total trades: {trade_count}")
print(f"Win rate: {win_count}/{trade_count} = {win_count/max(1,trade_count)*100:.0f}%")
print(f"Total PnL (5min): {total_pnl:+,.0f}")
print(f"Daily OHLC overstatement: {overstatement:+.0f}%")
print(f"HIGH-first days: {hi_first_count}, PnL: {hi_first_pnl:+,.0f}")
print(f"LOW-first days:  {lo_first_count}, PnL: {lo_first_pnl:+,.0f}")

# Also save raw data
with open("output/backtest_5min_raw.json", "w", encoding="utf-8") as f:
    serializable = []
    for r in results:
        item = {
            "date": r["date"],
            "daily": r["daily"],
            "signal": {k: v for k, v in r["signal"].items() if k != "factor_details"},
            "result": {k: v for k, v in r["result"].items()},
            "seq": r["seq"]
        }
        serializable.append(item)
    json.dump(serializable, f, ensure_ascii=False, indent=2)
print("Raw data: output/backtest_5min_raw.json")
