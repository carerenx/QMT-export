# -*- coding: utf-8 -*-
"""601869 涨跌强弱分析 — 对比大盘 + 板块"""
import json, time, urllib.request

UA = "Mozilla/5.0"

def get_klines(code, prefix_override=None, n=60):
    """腾讯前复权日K线 -> {date: {o,c,h,l,v}}"""
    if prefix_override:
        prefix = prefix_override
    elif code.startswith(("6","9","0")):
        prefix = "sh"
    elif code.startswith(("3","0","2")):
        prefix = "sz"
    else:
        prefix = "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{n},qfq"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[]) or \
        data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[])
    return {r[0]: {"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k}

# ---- Fetch data ----
print("Fetching market data...")
symbols = {
    "601869":    ("长飞光纤", None),
    "000001":    ("上证指数", "sh"),
    "399006":    ("创业板指", "sz"),
    "BK0706":    ("光纤光缆板块", None),  # sector index - might need different approach
}

klines = {}
for code, (name, pfx) in symbols.items():
    try:
        klines[code] = get_klines(code, pfx)
        print(f"  {name} ({code}): {len(klines[code])} bars")
    except Exception as e:
        print(f"  {name} ({code}): FAILED - {e}")
    time.sleep(0.3)

# For BK indices, use a different Tencent API format
# Try fetching 光纤光缆 via Tencent board API
try:
    url = "https://web.ifzq.gtimg.cn/appstock/app/board/boardKline/get?param=BK0706,day,,,60,qfq"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    d = json.loads(resp.read().decode("utf-8"))
    k = d.get("data",{}).get("BK0706",{}).get("qfqday",[]) or \
        d.get("data",{}).get("BK0706",{}).get("day",[])
    klines["BK0706"] = {r[0]: {"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k}
    print(f"  光纤光缆 (BK0706): {len(klines['BK0706'])} bars")
except Exception as e:
    print(f"  光纤光缆 BK0706: FAILED - {e}")

# ---- Align dates ----
sets = [set(k.keys()) for k in klines.values()]
common = sorted(set.intersection(*sets))
print(f"\nCommon trading days: {len(common)}")
print(f"Range: {common[0]} ~ {common[-1]}")
today = common[-1]

def ret(kdata, start, end):
    if start in kdata and end in kdata:
        return (kdata[end]["c"] - kdata[start]["c"]) / kdata[start]["c"] * 100
    return None

# ===================================================================
# MAIN ANALYSIS
# ===================================================================
print()
print("=" * 90)
print("  601869 长飞光纤 — 涨跌强弱分析")
print("=" * 90)

# 1. TODAY (intraday)
print(f"\n{'='*90}")
print(f"  TODAY: {today}")
print(f"{'='*90}")

d = klines["601869"][today]
d_sh = klines["000001"][today]
d_cy = klines["399006"][today]
d_bk = klines.get("BK0706", {}).get(today)

intra_869 = (d["c"]-d["o"])/d["o"]*100
intra_sh = (d_sh["c"]-d_sh["o"])/d_sh["o"]*100
intra_cy = (d_cy["c"]-d_cy["o"])/d_cy["o"]*100

print(f"""
  {"":>12}  {"开盘":>8}  {"收盘":>8}  {"最高":>8}  {"最低":>8}  {"日内涨跌":>10}  {"振幅":>8}
  {"-"*70}
  {"长飞光纤":>12}  {d["o"]:>8.2f}  {d["c"]:>8.2f}  {d["h"]:>8.2f}  {d["l"]:>8.2f}  {intra_869:>+9.2f}%  {(d["h"]-d["l"])/d["o"]*100:>7.1f}%
  {"上证指数":>12}  {d_sh["o"]:>8.2f}  {d_sh["c"]:>8.2f}  {d_sh["h"]:>8.2f}  {d_sh["l"]:>8.2f}  {intra_sh:>+9.2f}%
  {"创业板指":>12}  {d_cy["o"]:>8.2f}  {d_cy["c"]:>8.2f}  {d_cy["h"]:>8.2f}  {d_cy["l"]:>8.2f}  {intra_cy:>+9.2f}%""")

if d_bk:
    intra_bk = (d_bk["c"]-d_bk["o"])/d_bk["o"]*100
    print(f"""  {"光纤光缆":>12}  {d_bk["o"]:>8.2f}  {d_bk["c"]:>8.2f}  {d_bk["h"]:>8.2f}  {d_bk["l"]:>8.2f}  {intra_bk:>+9.2f}%""")

vs_sh_today = intra_869 - intra_sh
vs_cy_today = intra_869 - intra_cy
print(f"""
  日内相对强度:
    vs 上证: {vs_sh_today:+.2f}%  -- {"强于大盘" if vs_sh_today>0 else "弱于大盘"}
    vs 创业板: {vs_cy_today:+.2f}%""")

# 2. 1 WEEK (5 trading days)
print(f"\n{'='*90}")
print(f"  PAST WEEK (5 trading days)")
print(f"{'='*90}")

d5_s = common[max(0, len(common)-6)]
d5_e = common[-1]
r5_869 = ret(klines["601869"], d5_s, d5_e) or 0
r5_sh  = ret(klines["000001"], d5_s, d5_e) or 0
r5_cy  = ret(klines["399006"], d5_s, d5_e) or 0
r5_bk  = ret(klines.get("BK0706",{}), d5_s, d5_e) if "BK0706" in klines else None

print(f"  Period: {d5_s} ~ {d5_e}")
print(f"  长飞光纤: {r5_869:+.2f}%  |  上证: {r5_sh:+.2f}%  |  创业板: {r5_cy:+.2f}%", end="")
if r5_bk is not None: print(f"  |  光纤光缆: {r5_bk:+.2f}%", end="")
print()

alpha5_sh = r5_869 - r5_sh
alpha5_cy = r5_869 - r5_cy
print(f"  超额(Alpha) vs 上证: {alpha5_sh:+.2f}%  |  vs 创业板: {alpha5_cy:+.2f}%")

# Strength rating
def strength_label(alpha):
    if alpha > 5: return "VERY STRONG (极强)"
    if alpha > 2: return "STRONG (偏强)"
    if alpha > 0: return "SLIGHTLY STRONG (略强)"
    if alpha > -2: return "SLIGHTLY WEAK (略弱)"
    if alpha > -5: return "WEAK (偏弱)"
    return "VERY WEAK (极弱)"

print(f"  近一周强弱: {strength_label(alpha5_sh)}")

# 3. 1 MONTH (20 trading days)
print(f"\n{'='*90}")
print(f"  PAST MONTH (20 trading days)")
print(f"{'='*90}")

d20_s = common[max(0, len(common)-21)]
d20_e = common[-1]
r20_869 = ret(klines["601869"], d20_s, d20_e) or 0
r20_sh  = ret(klines["000001"], d20_s, d20_e) or 0
r20_cy  = ret(klines["399006"], d20_s, d20_e) or 0
r20_bk  = ret(klines.get("BK0706",{}), d20_s, d20_e) if "BK0706" in klines else None

print(f"  Period: {d20_s} ~ {d20_e}")
print(f"  长飞光纤: {r20_869:+.2f}%  |  上证: {r20_sh:+.2f}%  |  创业板: {r20_cy:+.2f}%", end="")
if r20_bk is not None: print(f"  |  光纤光缆: {r20_bk:+.2f}%", end="")
print()

alpha20_sh = r20_869 - r20_sh
alpha20_cy = r20_869 - r20_cy
print(f"  超额(Alpha) vs 上证: {alpha20_sh:+.2f}%  |  vs 创业板: {alpha20_cy:+.2f}%")
print(f"  近一月强弱: {strength_label(alpha20_sh)}")

# 4. DAILY VOLATILITY
print(f"\n{'='*90}")
print(f"  VOLATILITY ANALYSIS (past 20 days)")
print(f"{'='*90}")

def daily_rets(kdata, dates):
    rets = []
    for i in range(1, len(dates)):
        if dates[i-1] in kdata and dates[i] in kdata:
            r = (kdata[dates[i]]["c"] - kdata[dates[i-1]]["c"]) / kdata[dates[i-1]]["c"]
            rets.append(r)
    return rets

r20_common = common[-21:]
rets_869 = daily_rets(klines["601869"], r20_common)
rets_sh  = daily_rets(klines["000001"], r20_common)

vol_869 = sum(abs(r) for r in rets_869)/len(rets_869)*100 if rets_869 else 0
vol_sh  = sum(abs(r) for r in rets_sh)/len(rets_sh)*100 if rets_sh else 0

up_869 = sum(1 for r in rets_869 if r>0)
dn_869 = sum(1 for r in rets_869 if r<0)
up_sh  = sum(1 for r in rets_sh if r>0)
dn_sh  = sum(1 for r in rets_sh if r<0)

print(f"""
  日均绝对波动:
    长飞光纤: {vol_869:.2f}%/day  |  上证: {vol_sh:.2f}%/day
    Beta (相对波动): {vol_869/vol_sh:.1f}x

  涨跌天数 (近20日):
    长飞光纤: {up_869}涨 / {dn_869}跌  |  上证: {up_sh}涨 / {dn_sh}跌
  最大单日涨幅: {max(rets_869)*100:+.2f}%  |  最大单日跌幅: {min(rets_869)*100:+.2f}%""")

# 5. ROLLING ALPHA (20-day rolling)
print(f"\n{'='*90}")
print(f"  ROLLING 5-DAY ALPHA (past month)")
print(f"{'='*90}")

print(f"  {'Period':<22}  {'601869':>8}  {'上证':>8}  {'Alpha':>8}  强弱")
for i in range(len(common)-25, len(common)-3):
    s = common[i]
    e = common[min(i+5, len(common)-1)]
    r869 = ret(klines["601869"], s, e)
    rsh  = ret(klines["000001"], s, e)
    if r869 is None or rsh is None: continue
    alpha = r869 - rsh
    bar = "+" * max(0, int(alpha/2)) if alpha>0 else "-" * min(10, int(abs(alpha)/2))
    print(f"  {s}~{e}  {r869:>+7.2f}%  {rsh:>+7.2f}%  {alpha:>+7.2f}%  {bar}")

# ===================================================================
# SUMMARY
# ===================================================================
print()
print("=" * 90)
print("  SUMMARY & RECOMMENDATION")
print("=" * 90)

price_now = klines["601869"][today]["c"]
price_5d_ago = klines["601869"][common[max(0,len(common)-6)]]["c"]
price_20d_ago = klines["601869"][common[max(0,len(common)-21)]]["c"]
price_high_20d = max(klines["601869"][d]["h"] for d in common[-21:])
price_low_20d = min(klines["601869"][d]["l"] for d in common[-21:])
drawdown_20d = (price_now - price_high_20d) / price_high_20d * 100

print(f"""
  当前价格: {price_now:.2f}
  近20日最高: {price_high_20d:.2f}  最低: {price_low_20d:.2f}
  从高点回撤: {drawdown_20d:.1f}%

  === 三周期强弱判断 ===
  今日日内:   vs大盘 {vs_sh_today:+.2f}%  |  {strength_label(vs_sh_today)}
  近一周(5d): vs大盘 {alpha5_sh:+.2f}%  |  {strength_label(alpha5_sh)}
  近一月(20d):vs大盘 {alpha20_sh:+.2f}%  |  {strength_label(alpha20_sh)}
""")

# Generate recommendation
recs = []
# Today analysis
if intra_869 > 1.5 and intra_869 > intra_sh + 0.5:
    recs.append("今日低开高走，日内走势独立于大盘，有资金在主动吸筹")
elif intra_869 < -1.5 and intra_869 < intra_sh - 0.5:
    recs.append("今日走势弱于大盘，卖压较大")
else:
    recs.append("今日走势与大盘基本同步")

# Weekly analysis
if alpha5_sh > 5:
    recs.append("近一周显著强于大盘(alpha=" + f"{alpha5_sh:+.1f}%)，属强势反弹阶段，短线可继续持有")
elif alpha5_sh > 0:
    recs.append("近一周略强于大盘，初步企稳，但反弹力度一般")
elif alpha5_sh > -5:
    recs.append("近一周弱于大盘，仍在调整趋势中，不宜追涨")
else:
    recs.append("近一周显著弱于大盘(alpha=" + f"{alpha5_sh:+.1f}%)，超跌严重，随时可能技术性反弹")

# Monthly analysis
if alpha20_sh > 5:
    recs.append("近一月大幅跑赢大盘，处于牛市主升浪后的高位震荡，注意获利了结风险")
elif alpha20_sh > 0:
    recs.append("近一月略强于大盘，但超额收益在收窄")
elif alpha20_sh > -10:
    recs.append("近一月弱于大盘，前期涨幅过大后的估值回归，短期观望为主")
else:
    recs.append("近一月大幅跑输大盘(alpha=" + f"{alpha20_sh:+.1f}%)，恐慌性杀跌，存在超跌反弹机会但风险极高")

# Volatility analysis
if vol_869 / vol_sh > 3:
    recs.append(f"极高波动(日均{vol_869:.1f}%, 大盘{vol_sh:.1f}%)，不适合重仓，严格止损")

print("  === 研判结论 ===")
for i, r in enumerate(recs, 1):
    print(f"  {i}. {r}")

if alpha5_sh > -3 and drawdown_20d < -15:
    short_advice = "超跌反弹可轻仓参与，止损" + str(int(price_low_20d))
elif alpha5_sh < 0:
    short_advice = "弱势调整，观望等企稳信号"
else:
    short_advice = "偏强，可持有但不宜追高"

if drawdown_20d < -10:
    mid_advice = "高位高波动，控制仓位，反弹减仓"
else:
    mid_advice = "趋势尚可，设好移动止盈"

print(f"""
  === 操作建议 ===
  - 短期: {short_advice}
  - 中期: {mid_advice}
  - 关键位: 支撑 {price_low_20d:.0f} / 压力 {price_high_20d:.0f}
  - 风险: 一年涨幅>1500%, PE>200, 高估值高波动, 融资余额高位回落中
""")

print("=" * 90)
