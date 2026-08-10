# -*- coding: utf-8 -*-
"""6/25至今 不同持股策略收益对比"""
import json, urllib.request

UA = "Mozilla/5.0"

def get_daily(code, n=300):
    prefix = "sh"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{n},qfq"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[]) or \
        data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[])
    return [{"date":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

daily = get_daily("601869", 200)
# Include days before 6/25 for indicator calculation
period = [d for d in daily if d["date"] >= "2026-06-25"]
full_idx_start = next(i for i,d in enumerate(daily) if d["date"] >= "2026-06-25")

print(f"Period: {period[0]['date']} ~ {period[-1]['date']}, {len(period)} trading days")
print(f"Need {full_idx_start} prior bars for indicators")

# ===================================================================
# Indicators (need full history, not just period)
# ===================================================================
def _sma(v,p):
    n=len(v);r=[0.0]*n
    for i in range(p-1,n):r[i]=sum(v[i-p+1:i+1])/p
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

full_c = [d["c"] for d in daily]; full_h = [d["h"] for d in daily]; full_l = [d["l"] for d in daily]
full_o = [d["o"] for d in daily]; full_v = [d["v"] for d in daily]
full_ma5 = _sma(full_c, 5); full_ma20 = _sma(full_c, 20)
full_rsi = _rsi(full_c, 14)

# ===================================================================
# Initial state: 200 shares, cost basis
# ===================================================================
INIT_SHARES = 200
INIT_CASH = 11638
start_price = period[0]["c"]  # 6/25 close = 575
initial_value = INIT_SHARES * start_price + INIT_CASH

LOT = 100; COMM = 0.00025; TAX = 0.001

print(f"\nInitial: {INIT_SHARES} shares @ {start_price:.2f} + {INIT_CASH} cash = {initial_value:,.0f} RMB")
print(f"{'='*95}")

# ===================================================================
# STRATEGY 1: Buy & Hold
# ===================================================================
end_price = period[-1]["c"]
s1_end_value = INIT_SHARES * end_price + INIT_CASH
s1_return = (s1_end_value - initial_value) / initial_value * 100
s1_max_value = INIT_SHARES * max(d["h"] for d in period) + INIT_CASH
s1_max_dd = (s1_end_value - s1_max_value) / s1_max_value * 100

# ===================================================================
# STRATEGY 2: Sell ALL on 6/25 peak, buy back at bottom (hindsight optimal)
# ===================================================================
# This is impossible in practice but serves as theoretical max
peak_d = max(period, key=lambda d: d["h"])
trough_d = min((d for d in period if d["date"] >= peak_d["date"]), key=lambda d: d["l"], default=period[-1])
# Sell all at peak high, buy back at trough low
s2_sell_val = INIT_SHARES * peak_d["h"] * (1 - COMM - TAX)
s2_buy_shares = int(s2_sell_val / (trough_d["l"] * (1 + COMM)))
s2_cash_remain = s2_sell_val - s2_buy_shares * trough_d["l"] * (1 + COMM)
s2_end_value = s2_buy_shares * end_price + s2_cash_remain + INIT_CASH
s2_return = (s2_end_value - initial_value) / initial_value * 100

# ===================================================================
# STRATEGY 3: MA Crossover — sell when close < MA20, buy when close > MA5
# ===================================================================
shares3 = INIT_SHARES; cash3 = INIT_CASH; in_market3 = True
trades3 = []
for i, d in enumerate(period):
    idx = full_idx_start + i
    if idx < 20: continue  # MA20 needs 20 bars
    ma5 = full_ma5[idx]; ma20 = full_ma20[idx]; price = d["c"]

    if in_market3 and price < ma20 and shares3 >= LOT:
        # SELL 1 lot
        shares3 -= LOT; cash3 += price * LOT * (1 - COMM - TAX)
        in_market3 = False
        trades3.append((d["date"], "SELL", price, shares3, cash3))
    elif not in_market3 and price > ma5 and cash3 >= price * LOT * 1.01:
        # BUY 1 lot
        shares3 += LOT; cash3 -= price * LOT * (1 + COMM)
        in_market3 = True
        trades3.append((d["date"], "BUY", price, shares3, cash3))

s3_end_value = shares3 * end_price + cash3
s3_return = (s3_end_value - initial_value) / initial_value * 100

# ===================================================================
# STRATEGY 4: Trailing Stop — sell 50% when drawdown > 10% from peak
# ===================================================================
shares4 = INIT_SHARES; cash4 = INIT_CASH
peak_value4 = start_price; trades4 = []
for d in period:
    price = d["c"]
    if price > peak_value4: peak_value4 = price
    dd = (price - peak_value4) / peak_value4
    if dd < -0.10 and shares4 >= LOT:
        # Sell 1 lot at market
        shares4 -= LOT; cash4 += price * LOT * (1 - COMM - TAX)
        peak_value4 = price  # reset peak
        trades4.append((d["date"], "STOP_SELL", price, shares4, cash4, f"dd={dd*100:.1f}%"))

s4_end_value = shares4 * end_price + cash4
s4_return = (s4_end_value - initial_value) / initial_value * 100

# ===================================================================
# STRATEGY 5: RSI-based position sizing
# ===================================================================
# RSI > 70: sell 1 lot (overbought → reduce)
# RSI < 30: buy 1 lot (oversold → add)
shares5 = INIT_SHARES; cash5 = INIT_CASH; trades5 = []
for i, d in enumerate(period):
    idx = full_idx_start + i
    if idx < 14: continue
    rsi = full_rsi[idx]; price = d["c"]
    if rsi > 70 and shares5 >= LOT:
        shares5 -= LOT; cash5 += price * LOT * (1 - COMM - TAX)
        trades5.append((d["date"], "SELL", price, rsi, shares5))
    elif rsi < 30 and cash5 >= price * LOT * 1.01:
        shares5 += LOT; cash5 -= price * LOT * (1 + COMM)
        trades5.append((d["date"], "BUY", price, rsi, shares5))

s5_end_value = shares5 * end_price + cash5
s5_return = (s5_end_value - initial_value) / initial_value * 100

# ===================================================================
# STRATEGY 6: Sell all at open 6/26, stay in cash
# ===================================================================
sell_all_price = period[1]["o"]  # 6/26 open
s6_cash = INIT_SHARES * sell_all_price * (1 - COMM - TAX) + INIT_CASH
s6_end_value = s6_cash  # all cash
s6_return = (s6_end_value - initial_value) / initial_value * 100

# ===================================================================
# STRATEGY 7: Progressive Scale-out (分批减仓)
# ===================================================================
# Sell 1 lot each time RSI > 65, buy back if RSI < 35
shares7 = INIT_SHARES; cash7 = INIT_CASH; trades7 = []
for i, d in enumerate(period):
    idx = full_idx_start + i
    if idx < 14: continue
    rsi = full_rsi[idx]; price = d["c"]
    if rsi > 65 and shares7 >= LOT:
        shares7 -= LOT; cash7 += price * LOT * (1 - COMM - TAX)
        trades7.append((d["date"], "SELL", price, f"RSI={rsi:.0f}"))
    elif rsi < 35 and cash7 >= price * LOT * 2 and shares7 < INIT_SHARES:
        shares7 += LOT; cash7 -= price * LOT * (1 + COMM)
        trades7.append((d["date"], "BUY", price, f"RSI={rsi:.0f}"))

s7_end_value = shares7 * end_price + cash7
s7_return = (s7_end_value - initial_value) / initial_value * 100

# ===================================================================
# STRATEGY 8: Day 1 sell 1 lot (take profit at peak), hold 1 lot
# ===================================================================
shares8 = INIT_SHARES; cash8 = INIT_CASH
# Sell 1 lot at 6/25 close (575), hold 1 lot
sell_price8 = start_price
shares8 -= LOT; cash8 += sell_price8 * LOT * (1 - COMM - TAX)
s8_end_value = shares8 * end_price + cash8
s8_return = (s8_end_value - initial_value) / initial_value * 100

# ===================================================================
# PRINT RESULTS
# ===================================================================
print(f"\n{'='*95}")
print(f"  STRATEGY COMPARISON: 6/25 ~ {period[-1]['date']} ({len(period)} trading days)")
print(f"{'='*95}")
print(f"  Start: {INIT_SHARES} shares @ {start_price:.2f} + {INIT_CASH:,} cash = {initial_value:,.0f} RMB")
print(f"  End:   price = {end_price:.2f}  (change: {(end_price-start_price)/start_price*100:+.1f}%)")
print(f"  Peak in period: {max(d['h'] for d in period):.2f}  Trough: {min(d['l'] for d in period):.2f}")
print(f"")

results = [
    ("S1: Buy & Hold (不动)", s1_end_value, s1_return, s1_max_dd,
     f"持有{INIT_SHARES}股不变", f"{INIT_SHARES}股"),
    ("S2: 完美逃顶抄底(后见之明)", s2_end_value, s2_return, 0,
     f"6/25 Peak@{peak_d['h']:.2f}全卖 → Trough@{trough_d['l']:.2f}全买回", f"{s2_buy_shares}股"),
    ("S3: MA20/MA5交叉", s3_end_value, s3_return, 0,
     f"price<MA20卖1手, price>MA5买1手 | {len(trades3)}笔交易", f"{shares3}股 + {cash3:,.0f}现金"),
    ("S4: 回撤10%止损减仓", s4_end_value, s4_return, 0,
     f"从高点回撤>10%卖1手 | {len(trades4)}笔", f"{shares4}股 + {cash4:,.0f}现金"),
    ("S5: RSI超买>70卖, 超卖<30买", s5_end_value, s5_return, 0,
     f"{len(trades5)}笔交易", f"{shares5}股 + {cash5:,.0f}现金"),
    ("S6: 6/26开盘全卖, 持币至今", s6_end_value, s6_return, 0,
     f"开盘{sell_all_price:.2f}清仓", f"全现金{cash8+sell_all_price*LOT*(1-COMM-TAX):,.0f}"),
    ("S7: 分批减仓 RSI>65卖 RSI<35买", s7_end_value, s7_return, 0,
     f"{len(trades7)}笔交易", f"{shares7}股 + {cash7:,.0f}现金"),
    ("S8: 6/25卖1手, 持1手不动", s8_end_value, s8_return, 0,
     f"卖1手@{start_price:.2f}, 留1手", f"{shares8}股 + {cash8:,.0f}现金"),
]

print(f"  {'Strategy':<35} {'End Value':>12} {'Return':>8} {'Max DD':>8}  Notes")
print(f"  {'-'*90}")
for name, val, ret, mdd, note, holding in results:
    print(f"  {name:<35} {val:>12,.0f} {ret:>+7.1f}% {mdd:>+7.1f}%  {holding}")

# Best strategy
best = max(results, key=lambda x: x[1])
print(f"\n  *** BEST: {best[0]} → {best[1]:,.0f} RMB ({best[2]:+.1f}%) ***")

# Detailed trade log for the best realistic strategy
print(f"\n{'='*95}")
print(f"  DETAIL: S7 分批减仓交易记录")
print(f"{'='*95}")
for t in trades7:
    print(f"  {t[0]} {t[1]:>6} @ {t[2]:.2f}  {t[3]}")

print(f"\n{'='*95}")
print(f"  DETAIL: S4 回撤止损交易记录")
print(f"{'='*95}")
for t in trades4:
    print(f"  {t[0]} {t[1]} @ {t[2]:.2f}  shares={t[3]} cash={t[4]:,.0f}  {t[5]}")
