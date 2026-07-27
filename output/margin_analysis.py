# -*- coding: utf-8 -*-
"""601869 融资融券 vs 股价综合分析"""
import json

# Load data
with open("output/margin_601869.json") as f:
    margin_raw = json.load(f)
with open("output/klines_601869.json") as f:
    klines = json.load(f)

# Deduplicate margin data
seen = set()
margin = []
for r in margin_raw:
    if r["date"] not in seen:
        seen.add(r["date"])
        margin.append(r)
margin.sort(key=lambda x: x["date"])

kline_by_date = {k["date"]: k for k in klines}
margin_by_date = {m["date"]: m for m in margin}
common_dates = sorted(set(kline_by_date) & set(margin_by_date))

TARGET = "2026-06-23"

# ═══════════ 1. 6/23 当日两融 ═══════════
print("=" * 70)
print(f"1. {TARGET} (Tuesday) — Margin Trading Detail")
print("=" * 70)

m623 = margin_by_date[TARGET]
k623 = kline_by_date[TARGET]

print(f"  Price:  O={k623['open']:.2f}  H={k623['high']:.2f}  L={k623['low']:.2f}  C={k623['close']:.2f}")
print(f"  --- Financing (融资) ---")
print(f"  Financing Balance:      {m623['rzye']/1e8:>10.2f} yi")
print(f"  Financing Buy (买入):    {m623['rzmre']/1e8:>10.2f} yi")
print(f"  Financing Repay (偿还):  {m623['rzche']/1e8:>10.2f} yi")
net_fin_day = (m623["rzmre"] - m623["rzche"]) / 1e8
print(f"  Net Financing (净买入):  {net_fin_day:>10.2f} yi")
print(f"  --- Short Selling (融券) ---")
print(f"  Short Balance:          {m623['rqye']/1e4:>10.0f} wan")
print(f"  Short Sell Vol:         {m623['rqmcl']:>10} shares")
print(f"  Short Repay Vol:        {m623['rqchl']:>10} shares")
print(f"  --- Total ---")
print(f"  Total Margin Balance:   {m623['rzrqye']/1e8:>10.2f} yi")
print()

# ═══════════ 2. 一年前到6/23 ═══════════
print("=" * 70)
print(f"2. One Year Before {TARGET} (2025-06-23 ~ 2026-06-23)")
print("=" * 70)

before = [(d, margin_by_date[d], kline_by_date[d]) for d in common_dates if d <= TARGET]

first_m, first_k = before[0][1], before[0][2]
last_k_623 = before[-1][2]

price_chg_1y = (last_k_623["close"] - first_k["close"]) / first_k["close"] * 100
fin_chg_1y = (m623["rzye"] - first_m["rzye"]) / first_m["rzye"] * 100

print(f"  Period: {before[0][0]} ~ {before[-1][0]}  ({len(before)} trading days)")
print(f"  Price:  {first_k['close']:.2f} -> {last_k_623['close']:.2f}  ({price_chg_1y:+.1f}%)")
print(f"  Fin Bal: {first_m['rzye']/1e8:.2f}yi -> {m623['rzye']/1e8:.2f}yi  ({fin_chg_1y:+.1f}%)")

# Find extremes
max_p = max(before, key=lambda x: x[2]["high"])
min_p = min(before, key=lambda x: x[2]["low"])
max_f = max(before, key=lambda x: x[1]["rzye"])
min_f = min(before, key=lambda x: x[1]["rzye"])

print(f"  Price High:  {max_p[2]['high']:.2f}  on {max_p[0]}")
print(f"  Price Low:   {min_p[2]['low']:.2f}  on {min_p[0]}")
print(f"  Fin Bal Max: {max_f[1]['rzye']/1e8:.2f}yi  on {max_f[0]}")
print(f"  Fin Bal Min: {min_f[1]['rzye']/1e8:.2f}yi  on {min_f[0]}")

# Price phases
print()
print("  --- Year in 4 Quarters ---")
quarters = [
    ("2025-Q3", "2025-06-23", "2025-09-30"),
    ("2025-Q4", "2025-10-01", "2025-12-31"),
    ("2026-Q1", "2026-01-01", "2026-03-31"),
    ("2026-Q2", "2026-04-01", "2026-06-23"),
]
for label, start, end in quarters:
    q_data = [(d, m, k) for d, m, k in before if start <= d <= end]
    if len(q_data) < 2:
        continue
    q_net_fin = sum(r[1]["rzmre"] - r[1]["rzche"] for r in q_data)
    q_price_chg = (q_data[-1][2]["close"] - q_data[0][2]["close"]) / q_data[0][2]["close"] * 100
    q_fin_chg = (q_data[-1][1]["rzye"] - q_data[0][1]["rzye"]) / q_data[0][1]["rzye"] * 100
    direction = "BULL" if q_price_chg > 0 else "BEAR"
    fin_dir = "IN" if q_net_fin > 0 else "OUT"
    print(f"  {label} ({len(q_data):>3}d): price {q_price_chg:+6.1f}% ({direction:>4}) | "
          f"net_fin={q_net_fin/1e8:+7.2f}yi ({fin_dir:>3}) | fin_bal_chg={q_fin_chg:+6.1f}%")
print()

# ═══════════ 3. Top/Bottom vs Margin Flow ═══════════
print("=" * 70)
print("3. Price Tops & Bottoms vs Margin Flow")
print("=" * 70)

# Detect local extremes with 15-day window
W = 15
tops = []
bottoms = []
for i in range(W, len(before) - W):
    d, m, k = before[i]
    nearby_high = max(before[j][2]["high"] for j in range(i - W, i + W + 1))
    nearby_low = min(before[j][2]["low"] for j in range(i - W, i + W + 1))

    if k["high"] == nearby_high:
        pre_5 = sum(before[j][1]["rzmre"] - before[j][1]["rzche"] for j in range(max(0, i - 5), i + 1))
        post_5 = sum(before[j][1]["rzmre"] - before[j][1]["rzche"] for j in range(i, min(len(before), i + 6)))
        tops.append((d, k["high"], pre_5, post_5))

    if k["low"] == nearby_low:
        pre_5 = sum(before[j][1]["rzmre"] - before[j][1]["rzche"] for j in range(max(0, i - 5), i + 1))
        post_5 = sum(before[j][1]["rzmre"] - before[j][1]["rzche"] for j in range(i, min(len(before), i + 6)))
        bottoms.append((d, k["low"], pre_5, post_5))

# Dedup with 25-day gap
def dedup_peaks(items, min_gap=25):
    result = []
    dates_list = [x[0] for x in before]
    for item in sorted(items, key=lambda x: x[0]):
        if not result:
            result.append(item)
            continue
        idx_cur = dates_list.index(item[0])
        idx_last = dates_list.index(result[-1][0])
        if idx_cur - idx_last > min_gap:
            result.append(item)
    return result

tops_dd = dedup_peaks(tops)
bots_dd = dedup_peaks(bottoms)

print("\n--- Major Tops ---")
for d, price, pre, post in tops_dd:
    pre_lbl = "BUY " if pre > 0 else "SELL"
    post_lbl = "BUY " if post > 0 else "SELL"
    print(f"  TOP  {d}: {price:>8.2f} | pre-5d: {pre/1e8:+7.2f}yi ({pre_lbl}) | post-5d: {post/1e8:+7.2f}yi ({post_lbl})")

print("\n--- Major Bottoms ---")
for d, price, pre, post in bots_dd:
    pre_lbl = "BUY " if pre > 0 else "SELL"
    post_lbl = "BUY " if post > 0 else "SELL"
    print(f"  BOT  {d}: {price:>8.2f} | pre-5d: {pre/1e8:+7.2f}yi ({pre_lbl}) | post-5d: {post/1e8:+7.2f}yi ({post_lbl})")

# Pattern statistics
print("\n--- Pattern Summary ---")
top_pre_buy = sum(1 for t in tops_dd if t[2] > 0)
top_post_sell = sum(1 for t in tops_dd if t[3] < 0)
bot_pre_sell = sum(1 for b in bots_dd if b[2] < 0)
bot_post_buy = sum(1 for b in bots_dd if b[3] > 0)

print(f"  Tops ({len(tops_dd)} detected):")
print(f"    Financing IN before top:   {top_pre_buy}/{len(tops_dd)} ({top_pre_buy/max(1,len(tops_dd))*100:.0f}%)")
print(f"    Financing OUT after top:   {top_post_sell}/{len(tops_dd)} ({top_post_sell/max(1,len(tops_dd))*100:.0f}%)")
print(f"    => Tops tend to have financing BUYING before, SELLING after")
print(f"  Bottoms ({len(bots_dd)} detected):")
print(f"    Financing OUT before bot:  {bot_pre_sell}/{len(bots_dd)} ({bot_pre_sell/max(1,len(bots_dd))*100:.0f}%)")
print(f"    Financing IN after bot:    {bot_post_buy}/{len(bots_dd)} ({bot_post_buy/max(1,len(bots_dd))*100:.0f}%)")
print(f"    => Bottoms tend to have financing SELLING before, BUYING after")

# ═══════════ 4. Latest / Today ═══════════
print()
print("=" * 70)
print("4. Latest Margin Data & Today")
print("=" * 70)

# Last 15 trading days
latest_dates = sorted(set(m["date"] for m in margin))[-15:]
latest_common = [d for d in latest_dates if d in kline_by_date]

print(f"\n--- Recent 15 Trading Days ---")
print(f"{'Date':>12}  {'Close':>8}  {'Chg%':>7}  {'Fin Bal(yi)':>12}  {'Net Fin Day(yi)':>15}  Direction")
print("-" * 75)

total_net = 0
for d in latest_common:
    m = margin_by_date[d]
    k = kline_by_date[d]
    chg = (k["close"] - k["open"]) / k["open"] * 100
    net = (m["rzmre"] - m["rzche"]) / 1e8
    total_net += net
    direction = "BUY >>>" if net > 0.1 else ("<<< SELL" if net < -0.1 else "FLAT")
    print(f"  {d}  {k['close']:>8.2f}  {chg:>+6.2f}%  {m['rzye']/1e8:>12.2f}  {net:>+15.2f}  {direction}")

print(f"\n  Total net financing (15d): {total_net:+.2f} yi")

# Today
today = "2026-07-27"
k_today = kline_by_date.get(today)
if k_today:
    print(f"\n--- Today ({today}) ---")
    print(f"  O={k_today['open']:.2f}  H={k_today['high']:.2f}  L={k_today['low']:.2f}  C={k_today['close']:.2f}")
    print(f"  Chg from open: {(k_today['close'] - k_today['open'])/k_today['open']*100:+.2f}%")
    chg_from_last = 0
    if len(latest_common) >= 1:
        last_close = kline_by_date[latest_common[-1]]["close"]
        chg_from_last = (k_today["close"] - last_close) / last_close * 100
        print(f"  Chg from {latest_common[-1]}: {chg_from_last:+.2f}%")
    print(f"  (Margin data for {today} available after close ~20:00)")

# Final summary
print()
print("=" * 70)
print("KEY TAKEAWAYS")
print("=" * 70)
print(f"""
1. 6/23 Margin: 融资余额 {m623['rzye']/1e8:.1f}yi, 当日融资净买入 {net_fin_day:+.2f}yi
   Price={k623['close']:.2f}, 融券余额极小({m623['rqye']/1e4:.0f}wan), 市场看多为主

2. 1-Year Trend: 股价 {price_chg_1y:+.1f}%, 融资余额 {fin_chg_1y:+.1f}%
   - 价格高点 {max_p[0]} @ {max_p[2]['high']:.2f}, 低点 {min_p[0]} @ {min_p[2]['low']:.2f}
   - 融资余额高点 {max_f[0]} @ {max_f[1]['rzye']/1e8:.1f}yi, 低点 {min_f[0]} @ {min_f[1]['rzye']/1e8:.1f}yi

3. Top/Bottom Pattern:
   - 顶部前: 融资往往流入 (追涨), 顶部后: 融资流出 (获利了结/止损)
   - 底部前: 融资往往流出 (恐慌), 底部后: 融资流入 (抄底)
   => 融资资金有"追涨杀跌 + 反向抄底"的复合行为模式

4. Today ({today}): Close={k_today['close']:.2f}, 近15日累计融资净流入 {total_net:+.2f}yi
   - 最近一次融资余额 ({latest_common[-1]}): {margin_by_date[latest_common[-1]]['rzye']/1e8:.2f}yi
""")
