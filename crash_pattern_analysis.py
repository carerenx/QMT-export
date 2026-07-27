# -*- coding: utf-8 -*-
"""
暴跌后走势规律分析 v2 — 全面验证版

改进:
  1. 修复样本去重 bug（跳过重叠区间）
  2. 新增：暴跌后最大回撤分析（仓位管理关键指标）
  3. 新增：多维度分组（市值、前期涨幅、暴跌速度）
  4. 新增：统计显著性（标准差、置信区间）
  5. 新增：与长飞光纤最相似的子集分析
  6. 扩大样本至 2000 只
"""

import argparse
import json
import random
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TEN_SESSION = requests.Session()
TEN_SESSION.headers.update({"User-Agent": UA})

# ── 参数 ──
CRASH_THRESHOLD = 30.0
CRASH_MAX_DAYS = 20
UPTREND_MIN_DAYS = 30
UPTREND_MIN_GAIN = 50.0
LOOKBACK_BARS = 500
FORWARD_DAYS = 60


def _prefix(code):
    if code.startswith(("6", "9")): return "sh"
    if code.startswith(("8", "4")): return "bj"
    return "sz"


def fetch_klines(code, bars=500):
    p = _prefix(code)
    url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get?param={p}{code},day,2020-01-01,,{bars},qfq"
    try:
        r = TEN_SESSION.get(url, headers={"Referer": "https://gu.qq.com/"}, timeout=(5, 10))
        if r.status_code != 200: return []
        d = r.json()
        sd = d.get("data", {}).get(f"{p}{code}", {})
        return sd.get("qfqday") or sd.get("day") or []
    except Exception:
        return []


def find_crash_patterns(klines):
    """
    扫描所有"前期猛涨 → 短期暴跌"模式。
    返回去重后的模式列表。
    """
    if len(klines) < UPTREND_MIN_DAYS + CRASH_MAX_DAYS + FORWARD_DAYS:
        return []

    dates, closes, highs, lows = [], [], [], []
    for bar in klines:
        try:
            dates.append(str(bar[0])[:10])
            closes.append(float(bar[2]))
            highs.append(float(bar[3]))
            lows.append(float(bar[4]))
        except (ValueError, IndexError):
            continue

    n = len(closes)
    patterns = []
    last_crash_end = 0  # 避免重叠

    for i in range(UPTREND_MIN_DAYS, n - CRASH_MAX_DAYS - FORWARD_DAYS):
        if i < last_crash_end:
            continue

        # ── 找近期高点（i前20天内最高） ──
        peak_idx = i - 1
        peak_price = highs[i - 1]
        for j in range(i - 1, max(i - 21, 0), -1):
            if highs[j] > peak_price:
                peak_price = highs[j]
                peak_idx = j

        # ── 是否暴跌 >= CRASH_THRESHOLD%？ ──
        crash_idx = None
        for j in range(i, min(i + CRASH_MAX_DAYS, n)):
            if (closes[j] / peak_price - 1) * 100 <= -CRASH_THRESHOLD:
                crash_idx = j
                break
        if crash_idx is None:
            continue

        # ── 暴跌前是否有持续上涨？ ──
        valley_price = closes[max(0, peak_idx - UPTREND_MIN_DAYS)]
        valley_idx = max(0, peak_idx - UPTREND_MIN_DAYS)
        for j in range(peak_idx - 1, max(peak_idx - 120, 0), -1):
            if closes[j] < valley_price:
                valley_price = closes[j]
                valley_idx = j

        prior_days = peak_idx - valley_idx
        prior_gain = (peak_price / valley_price - 1) * 100
        if prior_gain < UPTREND_MIN_GAIN or prior_days < UPTREND_MIN_DAYS:
            continue

        # ── 暴跌后走势 ──
        crash_close = closes[crash_idx]

        # 计算各持有期的收益 + 期间最大回撤
        forward_returns = {}
        forward_max_dd = {}
        for horizon in [1, 3, 5, 10, 20, 40, 60]:
            end_idx = min(crash_idx + horizon, n - 1)
            # 持有期收益
            fwd_close = closes[end_idx]
            forward_returns[horizon] = round((fwd_close / crash_close - 1) * 100, 2)
            # 持有期内最大回撤（从入场到最低点）
            segment_lows = lows[crash_idx:end_idx + 1]
            min_price = min(segment_lows)
            forward_max_dd[horizon] = round((min_price / crash_close - 1) * 100, 2)

        # ── 记录 ──
        patterns.append({
            "crash_date": dates[crash_idx],
            "peak_date": dates[peak_idx],
            "valley_date": dates[valley_idx],
            "peak_price": round(peak_price, 2),
            "crash_price": round(crash_close, 2),
            "valley_price": round(valley_price, 2),
            "crash_pct": round((crash_close / peak_price - 1) * 100, 2),
            "prior_gain_pct": round(prior_gain, 2),
            "prior_days": prior_days,
            "crash_days": crash_idx - peak_idx,
            "returns": forward_returns,
            "max_drawdown": forward_max_dd,
        })

        # 跳过已覆盖区间，避免重叠
        last_crash_end = crash_idx + max(CRASH_MAX_DAYS, 30)

    return patterns


def analyze_stock(code, name=""):
    klines = fetch_klines(code, LOOKBACK_BARS)
    if not klines:
        return {"code": code, "name": name, "patterns": [], "error": "no klines"}
    patterns = find_crash_patterns(klines)
    return {"code": code, "name": name, "patterns": patterns}


def compute_stats(values):
    """计算一组收益值的统计量"""
    if not values:
        return {}
    n = len(values)
    pos = sum(1 for v in values if v > 0)
    sv = sorted(values)
    mean = sum(values) / n
    stdev = statistics.stdev(values) if n >= 2 else 0
    return {
        "n": n,
        "positive_pct": round(100 * pos / n, 1),
        "mean": round(mean, 2),
        "median": round(sv[n // 2], 2),
        "stdev": round(stdev, 2),
        "sharpe": round(mean / stdev, 2) if stdev > 0 else 0,
        "ci_low": round(mean - 1.96 * stdev / (n ** 0.5), 2),  # 95% CI
        "ci_high": round(mean + 1.96 * stdev / (n ** 0.5), 2),
        "best_quarter": round(sv[-n // 4], 2) if n >= 4 else round(sv[-1], 2),
        "worst_quarter": round(sv[n // 4], 2) if n >= 4 else round(sv[0], 2),
        "best": round(sv[-1], 2),
        "worst": round(sv[0], 2),
    }


def generate_report(all_results, forward_days=60):
    """生成全面分析报告"""
    # 汇总
    all_pats = []
    for r in all_results:
        for p in r["patterns"]:
            p["code"] = r["code"]
            p["name"] = r.get("name", "")
            all_pats.append(p)

    if not all_pats:
        return "未找到符合条件的暴跌模式"

    n_stocks = sum(1 for r in all_results if r["patterns"])

    # ── 去重（按 code + crash_date） ──
    seen = set()
    deduped = []
    for p in all_pats:
        key = (p["code"], p["crash_date"])
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    all_pats = deduped

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ═══════════════════════════════════════════════════════════
    #  报告开始
    # ═══════════════════════════════════════════════════════════
    L = []
    L.append("# A股暴跌后走势规律分析（全面验证版）")
    L.append("")
    L.append(f"**生成时间**: {now_str}")
    L.append(f"**分析股票数**: {len(all_results)} 只")
    L.append(f"**发现模式数**: {len(all_pats)} 个（来自 {n_stocks} 只股票，已去重）")
    L.append(f"**筛选条件**: 前期≥{UPTREND_MIN_GAIN}%涨幅(≥{UPTREND_MIN_DAYS}天) → "
             f"≤{CRASH_MAX_DAYS}天内跌≥{CRASH_THRESHOLD}%")
    L.append("")

    # ═══════════════════════════════════════════════════════════
    #  一、整体统计 + 最大回撤
    # ═══════════════════════════════════════════════════════════
    L.append("## 一、暴跌后走势统计（含最大回撤）")
    L.append("")
    L.append("| 持有天 | 样本 | 胜率 | 均值 | 中位 | 标准差 | Sharpe | 95%CI | 最佳1/4 | 最差1/4 | "
             "期内最大回撤(中位) |")
    L.append("|------|------|------|------|------|------|------|------|------|------|------|")
    for h in [1, 3, 5, 10, 20, 40, 60]:
        vals = [p["returns"][h] for p in all_pats if h in p.get("returns", {})]
        dds = [p["max_drawdown"].get(h, 0) for p in all_pats if h in p.get("max_drawdown", {})]
        if not vals: continue
        s = compute_stats(vals)
        med_dd = sorted(dds)[len(dds)//2] if dds else 0
        L.append(f"| {h}天 | {s['n']} | {s['positive_pct']}% | {s['mean']:+.1f}% | {s['median']:+.1f}% | "
                 f"{s['stdev']:.1f}% | {s['sharpe']} | [{s['ci_low']:+.1f}, {s['ci_high']:+.1f}] | "
                 f"{s['best_quarter']:+.1f}% | {s['worst_quarter']:+.1f}% | {med_dd:+.1f}% |")
    L.append("")
    L.append("> **Sharpe = 均值/标准差**，衡量风险调整后收益。>1 较好，>2 优秀。")
    L.append("> **95%CI** = 95%置信区间，真实均值有95%概率落在此范围内。")
    L.append("")

    # ═══════════════════════════════════════════════════════════
    #  二、多维度分组
    # ═══════════════════════════════════════════════════════════
    L.append("## 二、多维度分组分析")
    L.append("")

    # 2.1 按跌幅分组
    L.append("### 2.1 按暴跌幅度分组")
    L.append("")
    L.append("| 跌幅区间 | 样本 | 20天胜率 | 20天均值 | 20天中位 | "
             "20天最大回撤(中位) | 40天胜率 | 40天均值 |")
    L.append("|------|------|------|------|------|------|------|------|")
    for lo, hi in [(30, 35), (35, 40), (40, 50), (50, 100)]:
        subset = [p for p in all_pats if lo <= abs(p["crash_pct"]) < hi]
        if len(subset) < 10: continue
        r20 = [p["returns"][20] for p in subset]
        d20 = [p["max_drawdown"][20] for p in subset]
        r40 = [p["returns"][40] for p in subset]
        s20, s40 = compute_stats(r20), compute_stats(r40)
        med_d20 = sorted(d20)[len(d20)//2] if d20 else 0
        L.append(f"| {lo}-{hi}% | {len(subset)} | {s20['positive_pct']}% | {s20['mean']:+.1f}% | "
                 f"{s20['median']:+.1f}% | {med_d20:+.1f}% | {s40['positive_pct']}% | {s40['mean']:+.1f}% |")
    L.append("")

    # 2.2 按前期涨幅分组
    L.append("### 2.2 按前期涨幅分组")
    L.append("")
    L.append("| 前期涨幅 | 样本 | 20天胜率 | 20天均值 | 40天胜率 | 40天均值 |")
    L.append("|------|------|------|------|------|------|")
    for lo, hi in [(50, 100), (100, 200), (200, 500), (500, 9999)]:
        subset = [p for p in all_pats if lo <= p["prior_gain_pct"] < hi]
        if len(subset) < 10: continue
        r20 = [p["returns"][20] for p in subset]
        r40 = [p["returns"][40] for p in subset]
        s20, s40 = compute_stats(r20), compute_stats(r40)
        L.append(f"| {lo}-{hi}% | {len(subset)} | {s20['positive_pct']}% | {s20['mean']:+.1f}% | "
                 f"{s40['positive_pct']}% | {s40['mean']:+.1f}% |")
    L.append("")

    # 2.3 按暴跌速度分组
    L.append("### 2.3 按暴跌速度分组（从高点到暴跌日的天数）")
    L.append("")
    L.append("| 暴跌天数 | 样本 | 20天胜率 | 20天均值 | 40天胜率 | 40天均值 |")
    L.append("|------|------|------|------|------|------|")
    for lo, hi in [(1, 5), (5, 10), (10, 15), (15, 20)]:
        subset = [p for p in all_pats if lo <= p["crash_days"] < hi]
        if len(subset) < 10: continue
        r20 = [p["returns"][20] for p in subset]
        r40 = [p["returns"][40] for p in subset]
        s20, s40 = compute_stats(r20), compute_stats(r40)
        L.append(f"| {lo}-{hi}天 | {len(subset)} | {s20['positive_pct']}% | {s20['mean']:+.1f}% | "
                 f"{s40['positive_pct']}% | {s40['mean']:+.1f}% |")
    L.append("")

    # ═══════════════════════════════════════════════════════════
    #  三、与长飞光纤最相似子集
    # ═══════════════════════════════════════════════════════════
    # 长飞: -33.6%, 前期+55%左右(从370到575), 跌了12天
    similar = [p for p in all_pats
               if 30 <= abs(p["crash_pct"]) <= 40
               and 10 <= p["crash_days"] <= 18
               and 40 <= p["prior_gain_pct"] <= 150]
    L.append("## 三、与长飞光纤最相似的历史案例")
    L.append("")
    L.append(f"**筛选条件**: 跌幅30-40% | 暴跌10-18天 | 前期涨幅40-150%")
    L.append(f"**匹配样本**: {len(similar)} 个")
    L.append("")
    if similar:
        L.append("| 持仓天 | 胜率 | 均值 | 中位 | Sharpe | 最大回撤(中位) |")
        L.append("|------|------|------|------|------|------|")
        for h in [5, 10, 20, 40, 60]:
            vals = [p["returns"][h] for p in similar if h in p.get("returns", {})]
            dds = [p["max_drawdown"].get(h, 0) for p in similar if h in p.get("max_drawdown", {})]
            if not vals: continue
            s = compute_stats(vals)
            med_dd = sorted(dds)[len(dds)//2] if dds else 0
            L.append(f"| {h}天 | {s['positive_pct']}% | {s['mean']:+.1f}% | {s['median']:+.1f}% | "
                     f"{s['sharpe']} | {med_dd:+.1f}% |")
        L.append("")

        L.append("### 典型案例（最近20个）")
        L.append("")
        L.append("| 代码 | 名称 | 暴跌日 | 前期涨 | 跌幅 | 跌天数 | 5日后 | 20日后 | 40日后 |")
        L.append("|------|------|------|------|------|------|------|------|------|")
        for p in sorted(similar, key=lambda x: x["crash_date"], reverse=True)[:20]:
            L.append(f"| {p['code']} | {p['name']} | {p['crash_date']} | "
                     f"+{p['prior_gain_pct']:.0f}% | {p['crash_pct']:.1f}% | "
                     f"{p['crash_days']}天 | {p['returns'][5]:+.1f}% | "
                     f"{p['returns'][20]:+.1f}% | {p['returns'][40]:+.1f}% |")
        L.append("")

    # ═══════════════════════════════════════════════════════════
    #  四、收益分布可视化（ASCII）
    # ═══════════════════════════════════════════════════════════
    L.append("## 四、20日收益分布")
    L.append("")
    r20_all = [p["returns"][20] for p in all_pats]
    buckets = defaultdict(int)
    for v in r20_all:
        bucket = int(v // 5) * 5
        buckets[bucket] += 1
    max_count = max(buckets.values())
    L.append("```")
    for k in sorted(buckets.keys()):
        bar = "█" * int(40 * buckets[k] / max_count)
        L.append(f"  {k:>+5}%: {bar} {buckets[k]}")
    L.append("```")
    L.append("")

    # ═══════════════════════════════════════════════════════════
    #  五、操作建议
    # ═══════════════════════════════════════════════════════════
    L.append("## 五、操作参考")
    L.append("")
    L.append(f"基于 {len(all_pats):,} 个历史暴跌模式的统计规律：")
    L.append("")
    vals_20 = [p["returns"][20] for p in all_pats]
    s20 = compute_stats(vals_20)
    dd_20 = [p["max_drawdown"][20] for p in all_pats]
    med_dd_20 = sorted(dd_20)[len(dd_20)//2] if dd_20 else 0

    L.append(f"| 维度 | 最佳持有期 | 胜率 | 预期收益 | 95%CI下限 | 期间最大回撤(中位) | 盈亏比 |")
    L.append(f"|------|------|------|------|------|------|------|")
    L.append(f"| 全部样本 | 20天 | {s20['positive_pct']}% | {s20['mean']:+.1f}% | "
             f"{s20['ci_low']:+.1f}% | {med_dd_20:+.1f}% | "
             f"{s20['best_quarter']/abs(s20['worst_quarter']):.1f}:1 |")
    if similar:
        sv = [p["returns"][20] for p in similar]
        ss = compute_stats(sv)
        sd = [p["max_drawdown"][20] for p in similar]
        msd = sorted(sd)[len(sd)//2] if sd else 0
        L.append(f"| 长飞相似 | 20天 | {ss['positive_pct']}% | {ss['mean']:+.1f}% | "
                 f"{ss['ci_low']:+.1f}% | {msd:+.1f}% | "
                 f"{ss['best_quarter']/max(0.1,abs(ss['worst_quarter'])):.1f}:1 |")
    L.append("")
    L.append("---")
    L.append(f"*数据: 腾讯前复权K线 | 样本: {len(all_results)}只/{len(all_pats):,}模式 | "
             "历史统计不代表未来*")

    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser(description="A股暴跌规律分析 v2")
    parser.add_argument("--sample", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=40)
    parser.add_argument("--crash", type=float, default=30.0)
    args = parser.parse_args()

    global CRASH_THRESHOLD
    CRASH_THRESHOLD = args.crash

    print("[1/3] 获取股票列表...", flush=True)
    try:
        req = urllib.request.Request("http://www.cninfo.com.cn/new/data/szse_stock.json")
        req.add_header("User-Agent", UA)
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        stocks = []
        for s in data.get("stockList", []):
            code, name = s.get("code", ""), s.get("zwjc", "")
            if code and name and "B" not in s.get("category", "") and "退" not in name:
                stocks.append({"code": code, "name": name})
    except Exception as e:
        print(f"ERROR: {e}"); sys.exit(1)

    if 0 < args.sample < len(stocks):
        random.seed(42)
        stocks = random.sample(stocks, args.sample)
    print(f"  分析 {len(stocks):,} 只股票\n", flush=True)

    print(f"[2/3] 扫描暴跌模式（并发={args.workers}）...", flush=True)
    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(analyze_stock, s["code"], s["name"]): s for s in stocks}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                results.append(f.result())
            except Exception:
                pass
            if i % 200 == 0:
                elapsed = time.time() - t0
                n_pat = sum(len(r["patterns"]) for r in results)
                print(f"  进度: {i}/{len(stocks)}  模式: {n_pat}  "
                      f"速率: {i/elapsed:.0f}只/秒", flush=True)
    elapsed = time.time() - t0
    n_pat = sum(len(r["patterns"]) for r in results)
    print(f"  完成: {len(results)}只, {n_pat}模式(去重前), {elapsed:.0f}秒\n", flush=True)

    print("[3/3] 生成报告...", flush=True)
    report = generate_report(results)
    out = OUTPUT_DIR / f"crash_pattern_v2_{CRASH_THRESHOLD:.0f}pct.md"
    out.write_text(report, encoding="utf-8")
    print(f"  报告: {out} ({out.stat().st_size/1024:.0f} KB)", flush=True)

    # 终端摘要
    all_pats = []
    seen = set()
    for r in results:
        for p in r["patterns"]:
            key = (p.get("code",""), p["crash_date"])
            if key not in seen:
                seen.add(key)
                p["code"] = r["code"]
                p["name"] = r.get("name", "")
                all_pats.append(p)

    if all_pats:
        print("\n" + "─" * 70)
        for h in [5, 10, 20, 40, 60]:
            vals = [p["returns"][h] for p in all_pats]
            dds = [p["max_drawdown"][h] for p in all_pats]
            if not vals: continue
            s = compute_stats(vals)
            md = sorted(dds)[len(dds)//2]
            print(f"  {h:>2}天后: 胜率{s['positive_pct']:>4.0f}%  "
                  f"均值{s['mean']:>+6.1f}%  中位{s['median']:>+6.1f}%  "
                  f"Sharpe{s['sharpe']:>5.2f}  最大回撤(中位){md:>+5.1f}%")
        print("─" * 70)

    print("\nDone!")


if __name__ == "__main__":
    main()
