# -*- coding: utf-8 -*-
"""
风险机会矩阵 (Risk-Opportunity Matrix) — R/O 指标

基于前导天数比 + 多维技术指标，将任意股票的当前状态映射到
"风险 × 机会" 二维平面上，为操作决策提供量化参考。

核心理念:
  - 风险轴: 当前行情有多"异常"？(加速比 + 偏离度 + 量能异常)
  - 机会轴: 当前位置有多"划算"？(超卖程度 + 抛压衰竭 + 历史对称性)

四个象限:
  ┌────────────────┬────────────────┐
  │ 高机会·低风险   │ 高机会·高风险   │
  │ 🟢 最佳入场区  │ 🟡 博弈区       │
  │ (超跌+对称+缩量)│ (恐慌超跌+加速) │
  ├────────────────┼────────────────┤
  │ 低机会·低风险   │ 低机会·高风险   │
  │ ⚪ 观望区       │ 🔴 危险区       │
  │ (窄幅震荡)     │ (加速赶顶/崩跌) │
  └────────────────┴────────────────┘

用法:
    from risk_opportunity import compute_ro_scores

    scores = compute_ro_scores(klines, ref_date, cur_price)
    # → {risk_score, opportunity_score, quadrant, signals, ...}
"""

import statistics
from datetime import datetime, timedelta


def _trading_days_between(dates: list[str], start: str, end: str) -> int:
    """计算两个日期之间的交易天数"""
    return sum(1 for d in dates if start <= d <= end)


def compute_ro_scores(
    klines: list,
    ref_date: str,
    cur_price: float = None,
    ma_windows: tuple = (5, 10, 20, 60),
) -> dict:
    """
    计算单只股票的风险机会评分。

    Args:
        klines: K线数据，每条为 [date_str, open, close, high, low, volume]
                或 dict: {date, open, close, high, low, volume}
                按时间升序排列。
        ref_date: 基准日期 (YYYY-MM-DD)，从此日开始计算"当前行情"
        cur_price: 当前价格，为 None 则取最后一条 K线的收盘价

    Returns:
        {
            # ── 核心评分 ──
            risk_score:       float (0-100), 风险评分，越高越危险
            opportunity_score: float (0-100), 机会评分，越高机会越好
            quadrant:         str,  四象限标签
            quadrant_cn:      str,  中文解读
            recommendation:   str,  操作建议

            # ── 单项因子 ──
            factors: {
                'ratio_factor':        前导比因子 (0-100)
                'magnitude_factor':    涨跌幅因子 (0-100)
                'mean_reversion':      均值回归因子 (0-100)
                'volume_exhaustion':   量能衰竭因子 (0-100)
                'symmetry_factor':     对称性因子 (0-100)
                'consecutive_factor':  连续趋势因子 (0-100)
            }

            # ── 原始指标 ──
            metrics: {
                'change_pct':      涨跌幅 %
                'before_days':     前导天数
                'after_days':      后导天数
                'ratio':           前后天数比
                'dist_ma5':        距MA5 %
                'dist_ma10':       距MA10 %
                'dist_ma20':       距MA20 %
                'dist_ma60':       距MA60 %
                'vol_trend':       量能趋势 (-1~+1, 负=缩量)
                'consecutive_bars': 连续同向K线数
                'amplitude_5d':    5日平均振幅 %
            }
        }
    """
    # ── 归一化K线数据 ──────────────────────────────────────────────
    parsed = []
    for bar in klines:
        if isinstance(bar, (list, tuple)):
            date, o, c, h, l, v = (str(bar[0])[:10], float(bar[1]),
                                   float(bar[2]), float(bar[3]),
                                   float(bar[4]), float(bar[5]))
        elif isinstance(bar, dict):
            date = str(bar.get("date", ""))[:10]
            o, c = float(bar.get("open", 0)), float(bar.get("close", 0))
            h, l = float(bar.get("high", 0)), float(bar.get("low", 0))
            v = float(bar.get("volume", 0))
        else:
            continue
        if date and c > 0:
            parsed.append({"date": date, "open": o, "close": c,
                           "high": h, "low": l, "volume": v})

    if len(parsed) < 20:
        return {"risk_score": 50, "opportunity_score": 50,
                "quadrant": "insufficient_data", "quadrant_cn": "数据不足",
                "recommendation": "需要至少20根K线"}

    # ── 基础数据 ──────────────────────────────────────────────────
    dates = [b["date"] for b in parsed]
    closes = [b["close"] for b in parsed]
    volumes = [b["volume"] for b in parsed]

    if cur_price is None:
        cur_price = closes[-1]

    # 找到 ref_date 之后的起始索引
    ref_idx = None
    for i, d in enumerate(dates):
        if d >= ref_date:
            ref_idx = i
            break
    if ref_idx is None:
        ref_idx = len(parsed) - 10  # fallback

    ref_price = closes[ref_idx] if ref_idx < len(closes) else closes[-1]
    ref_date_actual = dates[ref_idx]

    # ═══════════════════════════════════════════════════════════════
    #  因子1: 前导比 (Ratio Factor) — 核心创新指标
    # ═══════════════════════════════════════════════════════════════
    after_change_pct = (cur_price / ref_price - 1) * 100
    after_days = len(dates) - ref_idx - 1
    if after_days < 1:
        after_days = 1

    target = abs(after_change_pct)
    direction = 1 if after_change_pct > 0 else -1

    before_days = -ref_idx  # 默认未找到
    for i in range(ref_idx - 1, -1, -1):
        prior_price = closes[i]
        if prior_price <= 0:
            continue
        prior_change = abs((ref_price / prior_price - 1) * 100)
        if prior_change >= target:
            before_days = ref_idx - i
            break

    # 改为绝对幅度匹配
    if before_days <= 0:
        before_days_closest = ref_idx
        for i in range(ref_idx - 1, -1, -1):
            prior_price = closes[i]
            if prior_price <= 0:
                continue
            prior_abs = abs((ref_price / prior_price - 1) * 100)
            if prior_abs >= target:
                before_days = ref_idx - i
                break
        if before_days <= 0:
            before_days = -ref_idx  # 未找到

    ratio = abs(before_days) / after_days if after_days > 0 else 0
    if before_days < 0:
        # 未找到：历史从未有如此大幅度 → 极端异常
        ratio_factor = min(ratio / 2, 1) * 100  # 直接拉满风险
        ratio_label = "极端异常"
    elif ratio <= 0.5:
        ratio_factor = 15  # 当前比历史慢很多 → 低风险
        ratio_label = "比历史慢"
    elif ratio <= 1.0:
        ratio_factor = 30  # 与历史持平
        ratio_label = "与历史持平"
    elif ratio <= 2.0:
        ratio_factor = 55  # 加速1-2倍
        ratio_label = "轻度加速"
    elif ratio <= 4.0:
        ratio_factor = 75  # 加速2-4倍
        ratio_label = "显著加速"
    else:
        ratio_factor = 95  # 加速4倍以上
        ratio_label = "极端加速"

    # ═══════════════════════════════════════════════════════════════
    #  因子2: 涨跌幅因子 (Magnitude Factor) — 幅度本身的风险
    # ═══════════════════════════════════════════════════════════════
    abs_change = abs(after_change_pct)
    if abs_change < 3:
        magnitude_factor = 10
    elif abs_change < 8:
        magnitude_factor = 25
    elif abs_change < 15:
        magnitude_factor = 45
    elif abs_change < 25:
        magnitude_factor = 65
    elif abs_change < 40:
        magnitude_factor = 80
    else:
        magnitude_factor = 95

    # ═══════════════════════════════════════════════════════════════
    #  因子3: 均值回归 (Mean Reversion) — 机会指标
    # ═══════════════════════════════════════════════════════════════
    dist_ma = {}
    for w in ma_windows:
        if len(closes) >= w:
            ma = sum(closes[-w:]) / w
            dist_ma[f"dist_ma{w}"] = round((cur_price / ma - 1) * 100, 2)

    dist_ma20 = dist_ma.get("dist_ma20", 0)

    # 超卖 = 机会（跌太远了会反弹）
    if dist_ma20 <= -20:
        mean_reversion = 90  # 极度超卖
    elif dist_ma20 <= -15:
        mean_reversion = 75
    elif dist_ma20 <= -10:
        mean_reversion = 60
    elif dist_ma20 <= -5:
        mean_reversion = 45
    elif dist_ma20 <= 0:
        mean_reversion = 30
    elif dist_ma20 <= 5:
        mean_reversion = 20
    elif dist_ma20 <= 10:
        mean_reversion = 12
    elif dist_ma20 <= 15:
        mean_reversion = 6
    else:
        mean_reversion = 3  # 极度超买，无机会

    # ═══════════════════════════════════════════════════════════════
    #  因子4: 量能衰竭 (Volume Exhaustion) — 机会指标
    # ═══════════════════════════════════════════════════════════════
    if len(volumes) >= 10:
        vol_recent_5 = statistics.mean(volumes[-5:])
        vol_prior_5 = statistics.mean(volumes[-10:-5])
        if vol_prior_5 > 0:
            vol_trend = (vol_recent_5 / vol_prior_5 - 1)
        else:
            vol_trend = 0
    else:
        vol_trend = 0

    # 缩量下跌 = 抛压衰竭 = 机会
    if after_change_pct < 0 and vol_trend < -0.15:
        volume_exhaustion = 80  # 明显缩量下跌
    elif after_change_pct < 0 and vol_trend < -0.05:
        volume_exhaustion = 55  # 轻微缩量
    elif after_change_pct < 0 and vol_trend > 0.15:
        volume_exhaustion = 15  # 放量下跌 = 危险
    elif after_change_pct > 0 and vol_trend > 0.15:
        volume_exhaustion = 30  # 放量上涨 = 正常
    elif after_change_pct > 0 and vol_trend < -0.15:
        volume_exhaustion = 10  # 缩量上涨 = 危险(量价背离)
    else:
        volume_exhaustion = 35  # 量能平稳

    # ═══════════════════════════════════════════════════════════════
    #  因子5: 对称性 (Symmetry Factor) — 机会指标
    # ═══════════════════════════════════════════════════════════════
    if before_days > 0 and after_days > 0:
        raw_ratio = before_days / after_days
        # 对称性最好在 0.7-1.5 之间（跌速≈涨速）
        if 0.7 <= raw_ratio <= 1.5:
            symmetry_factor = 85  # 高度对称 → 很可能反转
        elif 0.5 <= raw_ratio <= 2.0:
            symmetry_factor = 60
        elif 0.3 <= raw_ratio <= 3.0:
            symmetry_factor = 35
        else:
            symmetry_factor = 10
    else:
        symmetry_factor = 20  # 无历史参照

    # ═══════════════════════════════════════════════════════════════
    #  因子6: 连续趋势 (Consecutive Factor) — 风险指标
    # ═══════════════════════════════════════════════════════════════
    consecutive_bars = 0
    ref_close = closes[ref_idx]
    for i in range(ref_idx + 1, len(closes)):
        if (closes[i] - closes[i-1]) * (closes[ref_idx+1] - ref_close) > 0:
            consecutive_bars += 1
        else:
            break

    if consecutive_bars >= 10:
        consecutive_factor = 90
    elif consecutive_bars >= 7:
        consecutive_factor = 70
    elif consecutive_bars >= 5:
        consecutive_factor = 50
    elif consecutive_bars >= 3:
        consecutive_factor = 35
    else:
        consecutive_factor = 20

    # ═══════════════════════════════════════════════════════════════
    #  综合评分 (Composite)
    # ═══════════════════════════════════════════════════════════════

    # 风险分 = 加权平均（加速比权重最高，因其是最具区分度的指标）
    risk_score = round(
        ratio_factor * 0.35 +
        magnitude_factor * 0.25 +
        consecutive_factor * 0.20 +
        (70 if vol_trend > 0.15 and after_change_pct < 0 else 30) * 0.10 +
        (85 if before_days < 0 else 40) * 0.10
    )

    # 机会分 = 加权平均（均值回归 + 量能衰竭 权重最高）
    opportunity_score = round(
        mean_reversion * 0.35 +
        volume_exhaustion * 0.25 +
        symmetry_factor * 0.20 +
        (90 - magnitude_factor) * 0.10 +  # 幅度小 = 低风险 ≠ 高机会
        (80 if abs(ratio - 1.0) < 0.5 else 20) * 0.10
    )

    # 限制在 0-100
    risk_score = max(0, min(100, risk_score))
    opportunity_score = max(0, min(100, opportunity_score))

    # ═══════════════════════════════════════════════════════════════
    #  象限判定
    # ═══════════════════════════════════════════════════════════════
    if opportunity_score >= 50 and risk_score <= 50:
        quadrant = "green"
        quadrant_cn = "🟢 最佳入场区 (高机会·低风险)"
        recommendation = "积极关注，可考虑分批建仓"
    elif opportunity_score >= 50 and risk_score > 50:
        quadrant = "yellow"
        quadrant_cn = "🟡 博弈区 (高机会·高风险)"
        recommendation = "轻仓试探，严格止损，快进快出"
    elif opportunity_score < 50 and risk_score <= 50:
        quadrant = "white"
        quadrant_cn = "⚪ 观望区 (低机会·低风险)"
        recommendation = "持币观望，或持有底仓不动"
    else:
        quadrant = "red"
        quadrant_cn = "🔴 危险区 (低机会·高风险)"
        recommendation = "减仓或清仓，回避为主"

    # ═══════════════════════════════════════════════════════════════
    #  特殊信号
    # ═══════════════════════════════════════════════════════════════
    signals = []
    if before_days < 0:
        signals.append("⚠️ 历史未见如此大幅度变动")
    if vol_trend < -0.15 and after_change_pct < -8:
        signals.append("✅ 缩量急跌—抛压衰竭")
    if vol_trend > 0.15 and after_change_pct < -8:
        signals.append("⚠️ 放量急跌—恐慌未止")
    if consecutive_bars >= 8:
        signals.append("⚠️ 连续单向走势—趋势极端")
    if 0.8 <= (before_days / after_days if after_days > 0 and before_days > 0 else 0) <= 1.5:
        signals.append("✅ 涨跌对称—反转概率高")
    if dist_ma20 <= -15:
        signals.append("✅ 极度超卖—技术反弹需求强")
    if dist_ma20 >= 15:
        signals.append("⚠️ 极度超买—回调风险大")

    return {
        "risk_score": risk_score,
        "opportunity_score": opportunity_score,
        "quadrant": quadrant,
        "quadrant_cn": quadrant_cn,
        "recommendation": recommendation,
        "signals": signals,
        "factors": {
            "ratio_factor": ratio_factor,
            "ratio_label": ratio_label,
            "magnitude_factor": magnitude_factor,
            "mean_reversion": mean_reversion,
            "volume_exhaustion": volume_exhaustion,
            "symmetry_factor": symmetry_factor,
            "consecutive_factor": consecutive_factor,
        },
        "metrics": {
            "change_pct": round(after_change_pct, 2),
            "before_days": before_days,
            "after_days": after_days,
            "ratio": round(ratio, 2),
            "dist_ma5": dist_ma.get("dist_ma5", 0),
            "dist_ma10": dist_ma.get("dist_ma10", 0),
            "dist_ma20": dist_ma20,
            "dist_ma60": dist_ma.get("dist_ma60", 0),
            "vol_trend": round(vol_trend, 3),
            "consecutive_bars": consecutive_bars,
            "ref_date_actual": ref_date_actual,
            "ref_price": round(ref_price, 2),
            "cur_price": round(cur_price, 2),
        },
    }


# ═══════════════════════════════════════════════════════════════════
#  批量分析 & 报告生成
# ═══════════════════════════════════════════════════════════════════

def batch_analyze(stocks_data: list[dict], klines_fetcher, ref_date: str,
                  workers: int = 20) -> list[dict]:
    """
    对批量股票进行 R/O 分析。

    Args:
        stocks_data: 股票列表，每个元素含 code, name, cur_price
        klines_fetcher: 函数 (code, ref_date) → klines列表
        ref_date: 基准日期

    Returns:
        [{code, name, ...metrics, ...scores, quadrant, recommendation}, ...]
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time

    results = []
    total = len(stocks_data)
    t0 = time.time()

    def _analyze_one(s):
        try:
            klines = klines_fetcher(s["code"], ref_date)
            if not klines:
                return None
            ro = compute_ro_scores(klines, ref_date, s.get("cur_price"))
            ro["code"] = s["code"]
            ro["name"] = s.get("name", "")
            return ro
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_analyze_one, s): s for s in stocks_data}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                results.append(result)
            if i % 200 == 0:
                print(f"  RO分析进度: {i}/{total}", flush=True)

    elapsed = time.time() - t0
    print(f"  RO分析完成: {len(results)}/{total} 只, 耗时 {elapsed:.0f}s")
    return results


def ro_summary_report(results: list[dict]) -> str:
    """生成 R/O 分析汇总报告 (Markdown)"""
    if not results:
        return "无数据"

    green = [r for r in results if r["quadrant"] == "green"]
    yellow = [r for r in results if r["quadrant"] == "yellow"]
    white = [r for r in results if r["quadrant"] == "white"]
    red = [r for r in results if r["quadrant"] == "red"]

    lines = [
        "# 风险机会矩阵 (R/O) 分析报告",
        "",
        f"**分析股票数**: {len(results)} 只",
        "",
        "## 象限分布",
        "",
        f"| 象限 | 数量 | 占比 | 含义 |",
        f"|------|------|------|------|",
        f"| 🟢 高机会·低风险 | {len(green)} | {100*len(green)/len(results):.1f}% | 最佳入场区 |",
        f"| 🟡 高机会·高风险 | {len(yellow)} | {100*len(yellow)/len(results):.1f}% | 博弈区 |",
        f"| ⚪ 低机会·低风险 | {len(white)} | {100*len(white)/len(results):.1f}% | 观望区 |",
        f"| 🔴 低机会·高风险 | {len(red)} | {100*len(red)/len(results):.1f}% | 危险区 |",
        "",
    ]

    # 各象限代表股票
    for label, subset, n in [("🟢 高机会·低风险", green, 15),
                               ("🟡 高机会·高风险", yellow, 10),
                               ("🔴 低机会·高风险", red, 10)]:
        if not subset:
            continue
        sorted_subset = sorted(subset, key=lambda x: x["opportunity_score"], reverse=True)
        lines.append(f"## {label} (TOP {min(n, len(subset))})")
        lines.append("")
        lines.append("| 代码 | 名称 | 涨跌幅 | 风险分 | 机会分 | 前导比 | 信号 |")
        lines.append("|------|------|--------|--------|--------|--------|------|")
        for r in sorted_subset[:n]:
            signals = ", ".join(r.get("signals", [])[:2])
            lines.append(
                f"| {r['code']} | {r['name']} | "
                f"{r['metrics']['change_pct']:+.1f}% | "
                f"{r['risk_score']} | {r['opportunity_score']} | "
                f"{r['metrics']['ratio']:.1f}x | {signals} |"
            )
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  自测
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys, json, urllib.request
    sys.stdout.reconfigure(encoding="utf-8")

    # 测试: 长飞光纤
    code = "601869"
    url = (f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"
           f"?param=sh{code},day,2026-01-01,,150,qfq")
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Referer", "https://gu.qq.com/")
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    klines = data["data"][f"sh{code}"]["qfqday"]

    result = compute_ro_scores(klines, "2026-06-25")

    print("=" * 60)
    print(f"  风险机会矩阵 — {code} 长飞光纤")
    print("=" * 60)
    print(f"  风险分: {result['risk_score']}/100")
    print(f"  机会分: {result['opportunity_score']}/100")
    print(f"  象限:   {result['quadrant_cn']}")
    print(f"  建议:   {result['recommendation']}")
    print()
    print("  因子详情:")
    for k, v in result["factors"].items():
        print(f"    {k}: {v}")
    print()
    print("  关键指标:")
    for k, v in result["metrics"].items():
        print(f"    {k}: {v}")
    print()
    print("  信号:")
    for s in result.get("signals", []):
        print(f"    {s}")
    print()
    print("  R/O 矩阵位置:")
    opp = result["opportunity_score"]
    risk = result["risk_score"]
    grid = [["·"] * 20 for _ in range(20)]
    x = min(int(risk / 5), 19)
    y = 19 - min(int(opp / 5), 19)
    grid[y][x] = "●"
    # 画象限线
    for i in range(20):
        grid[10][i] = "─"
        grid[i][10] = "│"
    grid[10][10] = "┼"
    print("   机会 ↑")
    for row in grid:
        print("   " + " ".join(row))
    print("   风险 →")
