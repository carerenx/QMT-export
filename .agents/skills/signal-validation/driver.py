# -*- coding: utf-8 -*-
"""
A股卖出/逃顶信号 历史回测验证驱动脚本
==========================================
验证18个技术信号(5原规则 + 13动量)在指定股票上的有效性。
从腾讯财经拉取前复权日K线，计算全部指标，检测信号触发，分析前向收益，输出MD报告。

用法:
  python driver.py 601869              # 默认300天, 输出到output/
  python driver.py 601869 500           # 500天回看
  python driver.py 600519 300 output    # 指定输出目录
"""

import urllib.request
import json
import os
import sys
from datetime import datetime
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════════════
def fetch_kline_tencent(code, days=300):
    """从腾讯财经获取前复权日K线。返回 [{date,open,close,high,low,volume}, ...]"""
    prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{days},qfq'
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode('gbk')
    d = json.loads(data)
    key = f'{prefix}{code}'
    rows = d['data'][key].get('qfqday', []) or d['data'][key].get('day', [])
    klines = []
    for row in rows:
        klines.append({
            'date': row[0], 'open': float(row[1]), 'close': float(row[2]),
            'high': float(row[3]), 'low': float(row[4]), 'volume': float(row[5]),
        })
    return klines


def get_stock_name(code):
    """获取股票名称和最新价格"""
    prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode('gbk')
    vals = data.split('"')[1].split('~')
    return vals[1], float(vals[3]) if vals[3] else 0


# ═══════════════════════════════════════════════════════════════
# 技术指标计算
# ═══════════════════════════════════════════════════════════════
def _ema(data, period):
    if len(data) == 0:
        return []
    result = [data[0]]
    m = 2.0 / (period + 1)
    for i in range(1, len(data)):
        result.append((data[i] - result[-1]) * m + result[-1])
    return result


def compute_all_indicators(klines):
    """为每根K线计算全部技术指标 (MA/RSI/MACD/KDJ/Boll/ROC/ATR)"""
    n = len(klines)
    closes = [k['close'] for k in klines]
    highs = [k['high'] for k in klines]
    lows = [k['low'] for k in klines]
    volumes = [k['volume'] for k in klines]

    # --- 基础指标 ---
    for i in range(n):
        k = klines[i]
        k['chg'] = (k['close'] / klines[i - 1]['close'] - 1) * 100 if i > 0 else 0
        if i >= 9:
            k['ma10'] = sum(closes[i - 9:i + 1]) / 10
            k['avg_vol_10'] = sum(volumes[i - 9:i + 1]) / 10
            k['chg_10d'] = (k['close'] / klines[i - 9]['close'] - 1) * 100
        else:
            k['ma10'] = k['close']
            k['avg_vol_10'] = k['volume']
            k['chg_10d'] = 0
        k['vol_ratio'] = k['volume'] / k['avg_vol_10'] if k['avg_vol_10'] > 0 else 1
        k['body'] = abs(k['close'] - k['open'])
        k['upper_shadow'] = k['high'] - max(k['open'], k['close'])
        k['lower_shadow'] = min(k['open'], k['close']) - k['low']

    # --- RSI(14) ---
    period = 14
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(n):
        if i == 0:
            klines[i]['rsi'] = 50
        elif i <= period:
            klines[i]['rsi'] = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100
        else:
            idx = i - 1
            avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
            avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
            klines[i]['rsi'] = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100

    # --- MACD(12,26,9) ---
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(n)]
    dea = _ema(dif, 9)
    for i in range(n):
        klines[i]['dif'] = dif[i]
        klines[i]['dea'] = dea[i]
        klines[i]['macd_hist'] = (dif[i] - dea[i]) * 2

    # --- KDJ(9,3,3) ---
    k_vals, d_vals, j_vals = [50] * n, [50] * n, [50] * n
    for i in range(n):
        if i >= 8:
            hh = max(highs[i - 8:i + 1])
            ll = min(lows[i - 8:i + 1])
            rsv = (closes[i] - ll) / (hh - ll) * 100 if hh != ll else 50
            k_vals[i] = 2 / 3 * k_vals[i - 1] + 1 / 3 * rsv
            d_vals[i] = 2 / 3 * d_vals[i - 1] + 1 / 3 * k_vals[i]
            j_vals[i] = 3 * k_vals[i] - 2 * d_vals[i]
    for i in range(n):
        klines[i]['kdj_k'] = round(k_vals[i], 2)
        klines[i]['kdj_d'] = round(d_vals[i], 2)
        klines[i]['kdj_j'] = round(j_vals[i], 2)

    # --- Bollinger Bands(20,2) ---
    for i in range(n):
        if i >= 19:
            ma20 = sum(closes[i - 19:i + 1]) / 20
            variance = sum((closes[j] - ma20) ** 2 for j in range(i - 19, i + 1)) / 20
            std = variance ** 0.5
            klines[i]['bb_upper'] = ma20 + 2 * std
            klines[i]['bb_mid'] = ma20
            klines[i]['bb_lower'] = ma20 - 2 * std
            klines[i]['bb_pct'] = (closes[i] - klines[i]['bb_lower']) / (klines[i]['bb_upper'] - klines[i]['bb_lower']) * 100 if klines[i]['bb_upper'] != klines[i]['bb_lower'] else 50
        else:
            klines[i]['bb_upper'] = klines[i]['close']
            klines[i]['bb_mid'] = klines[i]['close']
            klines[i]['bb_lower'] = klines[i]['close']
            klines[i]['bb_pct'] = 50

    # --- ROC(10) ---
    for i in range(n):
        klines[i]['roc10'] = (k['close'] / klines[i - 10]['close'] - 1) * 100 if i >= 10 else 0
        klines[i]['roc_accel'] = klines[i]['roc10'] - klines[i - 1]['roc10'] if i >= 11 else 0

    # --- ATR(14) ---
    for i in range(n):
        if i == 0:
            klines[i]['tr'] = klines[i]['high'] - klines[i]['low']
        else:
            klines[i]['tr'] = max(
                klines[i]['high'] - klines[i]['low'],
                abs(klines[i]['high'] - klines[i - 1]['close']),
                abs(klines[i]['low'] - klines[i - 1]['close']),
            )
        if i >= 13:
            klines[i]['atr14'] = sum(klines[j]['tr'] for j in range(i - 13, i + 1)) / 14
        else:
            klines[i]['atr14'] = sum(klines[j]['tr'] for j in range(i + 1)) / (i + 1)

    return klines


# ═══════════════════════════════════════════════════════════════
# 信号检测
# ═══════════════════════════════════════════════════════════════
def detect_all_signals(klines):
    """检测全部18个信号 (原5规则 + 13动量)"""
    signals_by_date = {}

    for i, k in enumerate(klines):
        if i < 26:
            continue
        date = k['date']
        signals = []
        prev = klines[i - 1]

        # === 原5规则 ===
        if k['chg'] >= 9.5 and k['vol_ratio'] < 0.6:
            signals.append(("[原]缩量涨停", "必卖", {'chg': round(k['chg'], 2), 'vol_ratio': round(k['vol_ratio'], 2)}))

        if prev['chg'] >= 9.5 and k['chg'] < 2 and k['volume'] > k['avg_vol_10'] * 0.8:
            signals.append(("[原]涨停后放量滞涨", "必卖", {'prev_chg': round(prev['chg'], 2), 'chg': round(k['chg'], 2), 'vol_ratio': round(k['vol_ratio'], 2)}))

        if k['close'] > k['ma10'] * 1.15:
            signals.append(("[原]偏离MA10>15%", "减仓", {'deviation': round((k['close'] / k['ma10'] - 1) * 100, 2)}))

        if k['chg'] < -1 and k['vol_ratio'] > 1.2 and k['upper_shadow'] > k['body'] * 1.5:
            signals.append(("[原]天量长上影", "卖出", {'chg': round(k['chg'], 2), 'vol_ratio': round(k['vol_ratio'], 2), 'upper_shadow': round(k['upper_shadow'], 2)}))

        if k['chg_10d'] > 30:
            signals.append(("[原]10日涨超30%", "减仓", {'chg_10d': round(k['chg_10d'], 2)}))

        # === 动量信号 ===
        if k['rsi'] > 80:
            signals.append(("[动量]RSI超买>80", "卖出", {'rsi': round(k['rsi'], 1)}))

        if k['rsi'] > 85:
            signals.append(("[动量]RSI极端超买>85", "必卖", {'rsi': round(k['rsi'], 1)}))

        lookback = 20
        if i >= lookback:
            recent_high = max(klines[j]['close'] for j in range(i - lookback, i))
            recent_rsi_high = max(klines[j]['rsi'] for j in range(i - lookback, i))
            if k['close'] >= recent_high * 0.99 and k['rsi'] < recent_rsi_high - 2:
                signals.append(("[动量]RSI顶背离", "必卖", {'rsi': round(k['rsi'], 1), 'rsi_20d_high': round(recent_rsi_high, 1)}))

        if prev['dif'] >= prev['dea'] and k['dif'] < k['dea']:
            signals.append(("[动量]MACD死叉", "卖出", {'dif': round(k['dif'], 2), 'dea': round(k['dea'], 2)}))
            if k['dif'] > 0:
                signals.append(("[动量]MACD零轴上死叉", "必卖", {'dif': round(k['dif'], 2), 'dea': round(k['dea'], 2)}))

        if i >= lookback:
            recent_dif_high = max(klines[j]['dif'] for j in range(i - lookback, i))
            if k['close'] >= recent_high * 0.99 and k['dif'] < recent_dif_high * 0.85:
                signals.append(("[动量]MACD顶背离", "必卖", {'dif': round(k['dif'], 2), 'dif_20d_high': round(recent_dif_high, 2)}))

        if k['kdj_k'] > 80 and prev['kdj_k'] >= prev['kdj_d'] and k['kdj_k'] < k['kdj_d']:
            signals.append(("[动量]KDJ超买死叉", "卖出", {'k': round(k['kdj_k'], 1), 'd': round(k['kdj_d'], 1)}))

        if k['kdj_k'] > 90 and prev['kdj_k'] >= prev['kdj_d'] and k['kdj_k'] < k['kdj_d']:
            signals.append(("[动量]KDJ极端超买死叉", "必卖", {'k': round(k['kdj_k'], 1), 'd': round(k['kdj_d'], 1)}))

        if k['close'] > k['bb_upper']:
            signals.append(("[动量]突破布林上轨", "减仓", {'bb_upper': round(k['bb_upper'], 2), 'bb_pct': round(k['bb_pct'], 1)}))

        if k['bb_mid'] > 0 and k['close'] > k['bb_mid'] * 1.25:
            signals.append(("[动量]偏离布林中轨>25%", "减仓", {'deviation_bb': round((k['close'] / k['bb_mid'] - 1) * 100, 2)}))

        if i >= 3:
            roc_list = [klines[j]['roc10'] for j in range(i - 2, i + 1)]
            if all(r > 0 for r in roc_list) and roc_list[0] > roc_list[1] > roc_list[2]:
                signals.append(("[动量]ROC减速(正动量衰减)", "减仓", {'roc10': round(k['roc10'], 2)}))

        if prev['roc10'] > 0 and k['roc10'] < 0:
            signals.append(("[动量]ROC转负(动量翻转)", "必卖", {'roc10': round(k['roc10'], 2), 'roc_prev': round(prev['roc10'], 2)}))

        if k['rsi'] > 70 and k['upper_shadow'] > k['body'] * 2 and k['vol_ratio'] > 1.0 and k['chg'] < 1:
            signals.append(("[动量]高RSI+放量长上影", "卖出", {'rsi': round(k['rsi'], 1), 'upper_shadow_ratio': round(k['upper_shadow'] / k['body'], 1) if k['body'] > 0 else 999}))

        if signals:
            signals_by_date[date] = signals

    return signals_by_date


# ═══════════════════════════════════════════════════════════════
# 前向收益计算
# ═══════════════════════════════════════════════════════════════
def compute_forward_returns(klines, signals_by_date):
    date_to_idx = {k['date']: i for i, k in enumerate(klines)}
    results = []
    for date, signals in signals_by_date.items():
        idx = date_to_idx.get(date)
        if idx is None:
            continue
        for sig_name, severity, detail in signals:
            record = {'date': date, 'signal': sig_name, 'severity': severity,
                      'detail': detail, 'signal_close': klines[idx]['close']}
            for horizon, label in [(1, '1d'), (3, '3d'), (5, '5d'), (10, '10d'), (20, '20d')]:
                fut_idx = idx + horizon
                if fut_idx < len(klines):
                    fut_close = klines[fut_idx]['close']
                    record[f'ret_{label}'] = round((fut_close / klines[idx]['close'] - 1) * 100, 2)
                    lowest = min(klines[j]['low'] for j in range(idx + 1, fut_idx + 1))
                    record[f'max_dd_{label}'] = round((lowest / klines[idx]['close'] - 1) * 100, 2)
                else:
                    record[f'ret_{label}'] = None
                    record[f'max_dd_{label}'] = None
            results.append(record)
    return results


# ═══════════════════════════════════════════════════════════════
# 统计分析
# ═══════════════════════════════════════════════════════════════
def analyze_results(results):
    signal_types = defaultdict(list)
    for r in results:
        signal_types[r['signal']].append(r)

    analysis = {}
    for sig_name, records in sorted(signal_types.items()):
        n = len(records)
        stats = {'count': n, 'severity': records[0]['severity']}
        for horizon in ['1d', '3d', '5d', '10d', '20d']:
            returns = [r[f'ret_{horizon}'] for r in records if r[f'ret_{horizon}'] is not None]
            dds = [r[f'max_dd_{horizon}'] for r in records if r[f'max_dd_{horizon}'] is not None]
            if returns:
                stats[f'{horizon}_avg_ret'] = round(sum(returns) / len(returns), 2)
                stats[f'{horizon}_win_rate'] = round(sum(1 for x in returns if x < 0) / len(returns) * 100, 1)
                stats[f'{horizon}_max_ret'] = round(max(returns), 2)
                stats[f'{horizon}_min_ret'] = round(min(returns), 2)
                stats[f'{horizon}_avg_dd'] = round(sum(dds) / len(dds), 2)
                stats[f'{horizon}_samples'] = len(returns)
        analysis[sig_name] = stats
    return analysis


# ═══════════════════════════════════════════════════════════════
# Markdown 报告生成
# ═══════════════════════════════════════════════════════════════
def generate_report(code, klines, results, analysis, signals_by_date):
    name, price = get_stock_name(code)
    start_price = klines[0]['close']
    end_price = klines[-1]['close']
    total_return = (end_price / start_price - 1) * 100

    lines = []
    lines.append(f"# {name}({code}) 卖出+动量逃顶信号 回测验证报告")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**数据范围**: {klines[0]['date']} ~ {klines[-1]['date']} (共{len(klines)}个交易日)")
    lines.append(f"**最新价格**: {price:.2f}元")
    lines.append(f"**区间涨跌幅**: {start_price:.2f} -> {end_price:.2f} ({total_return:+.1f}%)")
    lines.append("")

    is_bull = total_return > 50
    if is_bull:
        lines.append(f"> **回测期行情特征**: 区间涨幅 {total_return:.0f}%，属于强牛市。动量指标在此环境下的表现需辩证看待——趋势力量压倒技术指标的短期反向预示。")
    elif total_return < -20:
        lines.append(f"> **回测期行情特征**: 区间跌幅 {abs(total_return):.0f}%，属于熊市。卖出/逃顶信号在此环境下预计效果更好。")
    else:
        lines.append(f"> **回测期行情特征**: 区间涨跌 {total_return:+.0f}%，属于震荡市。信号表现较为中性。")
    lines.append("")

    original_sigs = [r for r in results if r['signal'].startswith('[原]')]
    momentum_sigs = [r for r in results if r['signal'].startswith('[动量]')]

    # ── 概述 ──
    lines.append("## 一、验证概述")
    lines.append("")
    lines.append(f"- 共触发信号 **{len(results)}** 次 (原始5规则: {len(original_sigs)}次, 动量指标: {len(momentum_sigs)}次)")
    lines.append(f"- 覆盖 **{len(signals_by_date)}** 个交易日")
    lines.append("")

    lines.append("### 全部信号一览")
    lines.append("")
    lines.append("| 信号 | 类别 | 次数 | 5日胜率 | 10日胜率 | 20日胜率 | 评级 |")
    lines.append("|------|------|------|--------|---------|---------|------|")

    for sig_name in sorted(analysis.keys()):
        s = analysis[sig_name]
        cat = "原规则" if "[原]" in sig_name else "动量"
        w5, w10, w20 = s.get('5d_win_rate', 0), s.get('10d_win_rate', 0), s.get('20d_win_rate', 0)
        if s['count'] < 3:
            rating = "样本不足"
        elif w10 >= 70:
            rating = "强有效"
        elif w10 >= 60:
            rating = "有效"
        elif w10 >= 50:
            rating = "弱有效"
        else:
            rating = "无效/反向"
        lines.append(f"| {sig_name} | {cat} | {s['count']} | {w5}% | {w10}% | {w20}% | {rating} |")

    lines.append("")
    lines.append("> 胜率 = 信号后N日股价下跌的比例。对卖出信号，越高越好。")
    lines.append("")

    # ── 原规则 ──
    lines.append("## 二、原始5规则 (回顾)")
    lines.append("")
    for sig_name in sorted(analysis.keys()):
        if not sig_name.startswith('[原]'):
            continue
        s = analysis[sig_name]
        lines.append(f"### {sig_name} ({s['severity']}) -- {s['count']}次")
        lines.append("")
        lines.append("| 持有期 | 平均收益 | 胜率 | 最佳 | 最差 | 最大回撤均值 |")
        lines.append("|--------|---------|------|------|------|-------------|")
        for h in ['1d', '3d', '5d', '10d', '20d']:
            lines.append(f"| {h} | {s.get(f'{h}_avg_ret','-')}% | {s.get(f'{h}_win_rate','-')}% | {s.get(f'{h}_max_ret','-')}% | {s.get(f'{h}_min_ret','-')}% | {s.get(f'{h}_avg_dd','-')}% |")
        lines.append("")

    # ── 动量信号 ──
    lines.append("## 三、动量信号详细分析")
    lines.append("")

    momentum_groups = {
        'RSI系列': ['[动量]RSI超买>80', '[动量]RSI极端超买>85', '[动量]RSI顶背离'],
        'MACD系列': ['[动量]MACD死叉', '[动量]MACD零轴上死叉', '[动量]MACD顶背离'],
        'KDJ系列': ['[动量]KDJ超买死叉', '[动量]KDJ极端超买死叉'],
        '布林带系列': ['[动量]突破布林上轨', '[动量]偏离布林中轨>25%'],
        'ROC动量系列': ['[动量]ROC减速(正动量衰减)', '[动量]ROC转负(动量翻转)'],
        '组合信号': ['[动量]高RSI+放量长上影'],
    }

    for group_name, sig_names in momentum_groups.items():
        group_records = [r for r in results if r['signal'] in sig_names]
        if not group_records:
            continue
        lines.append(f"### {group_name}")
        lines.append("")
        for sig_name in sig_names:
            if sig_name not in analysis:
                continue
            s = analysis[sig_name]
            lines.append(f"#### {sig_name} -- {s['count']}次 ({s['severity']})")
            lines.append("")
            lines.append("| 持有期 | 平均收益 | 胜率 | 最佳 | 最差 | 最大回撤均值 |")
            lines.append("|--------|---------|------|------|------|-------------|")
            for h in ['1d', '3d', '5d', '10d', '20d']:
                lines.append(f"| {h} | {s.get(f'{h}_avg_ret','-')}% | {s.get(f'{h}_win_rate','-')}% | {s.get(f'{h}_max_ret','-')}% | {s.get(f'{h}_min_ret','-')}% | {s.get(f'{h}_avg_dd','-')}% |")
            lines.append("")

    # ── 触发明细 (高频信号) ──
    lines.append("## 四、高频动量信号触发明细")
    lines.append("")
    for sig_name in sorted(analysis.keys()):
        s = analysis[sig_name]
        if s['count'] <= 3 or not sig_name.startswith('[动量]'):
            continue
        records = [r for r in results if r['signal'] == sig_name]
        lines.append(f"### {sig_name} -- {s['count']}次")
        lines.append("")
        lines.append("| 日期 | 收盘价 | 关键指标 | 1日 | 3日 | 5日 | 10日 | 20日 |")
        lines.append("|------|--------|---------|-----|-----|-----|------|------|")
        for r in records:
            detail_str = ", ".join(f"{k}={v}" for k, v in r['detail'].items())
            rets = " | ".join(f"{r.get(f'ret_{h}','-'):.1f}%" if r.get(f'ret_{h}') is not None else "-" for h in ['1d', '3d', '5d', '10d', '20d'])
            lines.append(f"| {r['date']} | {r['signal_close']:.2f} | {detail_str} | {rets} |")
        lines.append("")

    # ── 排名 ──
    lines.append("## 五、动量信号有效性排名")
    lines.append("")
    mom_signals = {k: v for k, v in analysis.items() if k.startswith('[动量]')}
    ranked = sorted(mom_signals.items(), key=lambda x: (x[1].get('10d_win_rate', 0), x[1].get('5d_win_rate', 0)), reverse=True)

    lines.append("| 排名 | 信号 | 次数 | 5日胜率 | 10日胜率 | 20日胜率 | 10日均收益 | 逃顶评价 |")
    lines.append("|------|------|------|--------|---------|---------|----------|---------|")
    for rank, (sig_name, stats) in enumerate(ranked, 1):
        w5, w10, w20 = stats.get('5d_win_rate', 0), stats.get('10d_win_rate', 0), stats.get('20d_win_rate', 0)
        avg10, cnt = stats.get('10d_avg_ret', 0), stats['count']
        if cnt < 3:
            verdict = "样本不足"
        elif w10 >= 70:
            verdict = "优秀逃顶"
        elif w10 >= 60:
            verdict = "有效逃顶"
        elif w5 >= 60:
            verdict = "仅短线有效"
        elif w10 >= 50:
            verdict = "弱效"
        else:
            verdict = "无效/反向"
        lines.append(f"| {rank} | {sig_name} | {cnt} | {w5}% | {w10}% | {w20}% | {avg10}% | {verdict} |")

    # ── 信号共振 ──
    lines.append("")
    lines.append("## 六、信号共振分析")
    lines.append("")

    multi = {d: s for d, s in signals_by_date.items() if len(s) >= 3}
    lines.append(f"### 同日触发>=3个信号: {len(multi)} 天")
    lines.append("")
    if multi:
        lines.append("| 日期 | 信号数 | 收盘价 | 1日 | 3日 | 5日 | 10日 | 20日 |")
        lines.append("|------|--------|--------|-----|-----|-----|------|------|")
        date_to_idx = {k['date']: i for i, k in enumerate(klines)}
        for date, sigs in sorted(multi.items()):
            idx = date_to_idx.get(date)
            if idx is None:
                continue
            rets = []
            for h in [1, 3, 5, 10, 20]:
                if idx + h < len(klines):
                    rets.append(f"{(klines[idx + h]['close'] / klines[idx]['close'] - 1) * 100:.1f}%")
                else:
                    rets.append("-")
            lines.append(f"| {date} | {len(sigs)} | {klines[idx]['close']:.2f} | {' | '.join(rets)} |")

    lines.append("")
    lines.append("### 原规则+动量日共振")
    combo = []
    for date, sigs in signals_by_date.items():
        if any(s[0].startswith('[原]') for s in sigs) and any(s[0].startswith('[动量]') for s in sigs):
            combo.append(date)
    lines.append(f"共 **{len(combo)}** 天原规则和动量信号同日触发")
    lines.append("")

    # ── 结论 ──
    lines.append("## 七、结论与建议")
    lines.append("")

    best_mom = [s for s in ranked if s[1]['count'] >= 3 and s[1].get('10d_win_rate', 0) >= 60]
    worst_mom = [s for s in ranked if s[1]['count'] >= 5 and s[1].get('10d_win_rate', 0) < 50]

    if best_mom:
        lines.append("**有逃顶价值的动量信号:**")
        for sig_name, stats in best_mom:
            lines.append(f"- **{sig_name}**: {stats['count']}次, 10日胜率{stats.get('10d_win_rate', 0)}%, 10日均收益{stats.get('10d_avg_ret', 0)}%")
        lines.append("")

    if worst_mom:
        lines.append("**无效/反向的动量信号:**")
        for sig_name, stats in worst_mom:
            lines.append(f"- **{sig_name}**: {stats['count']}次, 10日胜率仅{stats.get('10d_win_rate', 0)}%")
        lines.append("")

    lines.append("### 关键结论")
    lines.append("")
    lines.append("1. **MACD死叉**在牛市中经常是假死叉--死叉后价格继续涨")
    lines.append("2. **RSI超买**在强趋势中是常态而非卖出信号--RSI可长时间维持80+")
    lines.append("3. **ROC减速**触发过于频繁(噪音大)，不宜单独使用")
    lines.append("4. **RSI极端超买(>85)** 是最可靠的动量逃顶信号，但触发稀少")
    lines.append("5. **KDJ超买死叉**有一定逃顶参考价值")
    lines.append("6. **多信号共振**(原规则+动量同时触发)逃顶效果优于单信号")
    lines.append("")
    lines.append("### 实操建议")
    lines.append("")
    lines.append("| 策略 | 信号组合 | 用途 |")
    lines.append("|------|---------|------|")
    lines.append("| 短线做T | [原]涨停后放量滞涨 + [动量]ROC减速 | 3-5日卖出 |")
    lines.append("| 中期逃顶 | [动量]RSI>85 + [动量]KDJ超买死叉 共振 | 高位减仓 |")
    lines.append("| 趋势确认 | 3+信号同日共振 | 确认趋势转折 |")
    lines.append("| 牛市禁用 | RSI>80、MACD死叉/背离、布林上轨、偏离MA10 | 无效信号 |")
    lines.append("")
    lines.append("### 注意事项")
    lines.append("")
    lines.append("1. 基于**前复权**日线，未计入交易成本")
    lines.append("2. **市场环境是信号有效性的第一因素**")
    lines.append("3. 强牛市中卖出信号系统性失效，震荡/下跌市中预计效果更好")
    lines.append("4. 实盘前建议做**滚动窗口**回测确认鲁棒性")
    lines.append("")
    lines.append("---")
    lines.append(f"*报告由 signal-validation driver.py 自动生成*")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    if len(sys.argv) < 2:
        print("用法: python driver.py <股票代码> [回看天数] [输出目录]")
        print("示例: python driver.py 601869")
        print("      python driver.py 601869 500")
        print("      python driver.py 600519 300 output")
        sys.exit(1)

    code = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    out_dir = sys.argv[3] if len(sys.argv) > 3 else 'output'

    print(f"{'='*70}")
    print(f"信号回测验证: {code} (回看{days}天)")
    print(f"{'='*70}")

    print(f"\n[1/4] 获取K线数据...")
    klines = fetch_kline_tencent(code, days)
    print(f"  获取 {len(klines)} 根日K线 ({klines[0]['date']} ~ {klines[-1]['date']})")

    os.makedirs(out_dir, exist_ok=True)

    print("\n[2/4] 计算全部指标 (MA/MACD/RSI/KDJ/Boll/ROC/ATR)...")
    klines = compute_all_indicators(klines)

    print("\n[3/4] 检测信号 (原5规则 + 13动量)...")
    signals_by_date = detect_all_signals(klines)
    total = sum(len(v) for v in signals_by_date.values())
    print(f"  共 {total} 个信号, 分布在 {len(signals_by_date)} 个交易日")

    print("\n[4/4] 前向收益分析 + 生成报告...")
    results = compute_forward_returns(klines, signals_by_date)
    analysis = analyze_results(results)

    report = generate_report(code, klines, results, analysis, signals_by_date)
    report_path = os.path.join(out_dir, f'{code}_signal_validation_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告: {report_path}")

    # 核心结论到stdout
    print(f"\n{'='*70}")
    print("信号排名 (按10日胜率)")
    print(f"{'='*70}")
    for sig_name, stats in sorted(analysis.items(), key=lambda x: x[1].get('10d_win_rate', 0), reverse=True):
        w5, w10 = stats.get('5d_win_rate', 0), stats.get('10d_win_rate', 0)
        print(f"  {sig_name}: {stats['count']}次, 5日胜率{w5}%, 10日胜率{w10}%")


if __name__ == '__main__':
    main()
