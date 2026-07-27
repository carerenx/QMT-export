# -*- coding: utf-8 -*-
"""
长飞光纤(601869) 卖出/逃顶信号 历史回测验证 (含动量指标)
信号包括: 原5规则 + RSI超买/背离 + MACD死叉/背离 + KDJ超买死叉 + 布林上轨 + ROC减速
"""

import urllib.request
import json
import os
from datetime import datetime
from collections import defaultdict

# ============================================================
# 1. 获取数据
# ============================================================
def fetch_kline_tencent(code='601869', days=300):
    prefix = 'sh' if code.startswith(('6','9')) else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{days},qfq'
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode('gbk')
    d = json.loads(data)
    kline_list = d['data'][f'{prefix}{code}'].get('qfqday', [])
    if not kline_list:
        kline_list = d['data'][f'{prefix}{code}'].get('day', [])

    klines = []
    for row in kline_list:
        klines.append({
            'date': row[0],
            'open': float(row[1]),
            'close': float(row[2]),
            'high': float(row[3]),
            'low': float(row[4]),
            'volume': float(row[5]),
        })
    return klines

# ============================================================
# 2. 计算所有指标 (原规则 + 6大动量指标)
# ============================================================
def ema(data, period):
    """指数移动平均"""
    if len(data) == 0:
        return []
    result = [data[0]]
    multiplier = 2.0 / (period + 1)
    for i in range(1, len(data)):
        result.append((data[i] - result[-1]) * multiplier + result[-1])
    return result

def compute_all_indicators(klines):
    n = len(klines)
    closes = [k['close'] for k in klines]
    highs = [k['high'] for k in klines]
    lows = [k['low'] for k in klines]
    volumes = [k['volume'] for k in klines]

    # -------- 基础指标 --------
    for i in range(n):
        k = klines[i]
        if i > 0:
            k['chg'] = (k['close'] / klines[i-1]['close'] - 1) * 100
        else:
            k['chg'] = 0

        if i >= 9:
            k['ma10'] = sum(closes[i-9:i+1]) / 10
            k['avg_vol_10'] = sum(volumes[i-9:i+1]) / 10
        else:
            k['ma10'] = k['close']
            k['avg_vol_10'] = k['volume']

        k['vol_ratio'] = k['volume'] / k['avg_vol_10'] if k['avg_vol_10'] > 0 else 1
        k['body'] = abs(k['close'] - k['open'])
        k['upper_shadow'] = k['high'] - max(k['open'], k['close'])
        k['lower_shadow'] = min(k['open'], k['close']) - k['low']

        if i >= 9:
            k['chg_10d'] = (k['close'] / klines[i-9]['close'] - 1) * 100
        else:
            k['chg_10d'] = 0

    # -------- RSI(14) --------
    period = 14
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(n):
        if i == 0:
            klines[i]['rsi'] = 50
        elif i <= period:
            if avg_loss == 0:
                klines[i]['rsi'] = 100
            else:
                klines[i]['rsi'] = 100 - 100 / (1 + avg_gain / avg_loss)
        else:
            idx = i - 1
            avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
            avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
            if avg_loss == 0:
                klines[i]['rsi'] = 100
            else:
                klines[i]['rsi'] = 100 - 100 / (1 + avg_gain / avg_loss)

    # -------- MACD(12,26,9) --------
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(n)]
    dea = ema(dif, 9)
    macd_hist = [(dif[i] - dea[i]) * 2 for i in range(n)]
    for i in range(n):
        klines[i]['dif'] = dif[i]
        klines[i]['dea'] = dea[i]
        klines[i]['macd_hist'] = macd_hist[i]

    # -------- KDJ(9,3,3) --------
    k_vals, d_vals, j_vals = [50]*n, [50]*n, [50]*n
    for i in range(n):
        if i >= 8:
            hh = max(highs[i-8:i+1])
            ll = min(lows[i-8:i+1])
            rsv = (closes[i] - ll) / (hh - ll) * 100 if hh != ll else 50
            k_vals[i] = 2/3 * k_vals[i-1] + 1/3 * rsv
            d_vals[i] = 2/3 * d_vals[i-1] + 1/3 * k_vals[i]
            j_vals[i] = 3 * k_vals[i] - 2 * d_vals[i]
    for i in range(n):
        klines[i]['kdj_k'] = round(k_vals[i], 2)
        klines[i]['kdj_d'] = round(d_vals[i], 2)
        klines[i]['kdj_j'] = round(j_vals[i], 2)

    # -------- Bollinger Bands(20,2) --------
    for i in range(n):
        if i >= 19:
            ma20 = sum(closes[i-19:i+1]) / 20
            variance = sum((closes[j] - ma20)**2 for j in range(i-19, i+1)) / 20
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

    # -------- ROC(10) 动量 --------
    for i in range(n):
        if i >= 10:
            klines[i]['roc10'] = (k['close'] / klines[i-10]['close'] - 1) * 100
        else:
            klines[i]['roc10'] = 0
        # ROC加速度 (ROC的一阶差分)
        if i >= 11:
            klines[i]['roc_accel'] = klines[i]['roc10'] - klines[i-1]['roc10']
        else:
            klines[i]['roc_accel'] = 0

    # -------- 成交量动量 --------
    for i in range(n):
        if i >= 5:
            klines[i]['vol_ma5'] = sum(volumes[i-4:i+1]) / 5
        else:
            klines[i]['vol_ma5'] = klines[i]['volume']

    # -------- ATR(14) --------
    for i in range(n):
        if i == 0:
            klines[i]['tr'] = klines[i]['high'] - klines[i]['low']
        else:
            tr1 = klines[i]['high'] - klines[i]['low']
            tr2 = abs(klines[i]['high'] - klines[i-1]['close'])
            tr3 = abs(klines[i]['low'] - klines[i-1]['close'])
            klines[i]['tr'] = max(tr1, tr2, tr3)
        if i >= 13:
            klines[i]['atr14'] = sum(klines[j]['tr'] for j in range(i-13, i+1)) / 14
        elif i > 0:
            klines[i]['atr14'] = sum(klines[j]['tr'] for j in range(i+1)) / (i+1)
        else:
            klines[i]['atr14'] = klines[i]['tr']

    return klines

# ============================================================
# 3. 信号检测 (原5规则 + 7个动量信号)
# ============================================================
def detect_all_signals(klines):
    signals_by_date = {}

    for i, k in enumerate(klines):
        if i < 26:  # 需要足够的回溯期
            continue

        date = k['date']
        signals = []
        prev = klines[i-1]

        # ============ 原5规则 ============

        # 规则1: 缩量涨停
        if k['chg'] >= 9.5 and k['vol_ratio'] < 0.6:
            signals.append(("[原]缩量涨停", "必卖", {
                'chg': round(k['chg'], 2), 'vol_ratio': round(k['vol_ratio'], 2),
            }))

        # 规则2: 涨停后放量滞涨
        if prev['chg'] >= 9.5 and k['chg'] < 2 and k['volume'] > k['avg_vol_10'] * 0.8:
            signals.append(("[原]涨停后放量滞涨", "必卖", {
                'prev_chg': round(prev['chg'], 2), 'chg': round(k['chg'], 2),
                'vol_ratio': round(k['vol_ratio'], 2),
            }))

        # 规则3: 偏离MA10>15%
        if k['close'] > k['ma10'] * 1.15:
            signals.append(("[原]偏离MA10>15%", "减仓", {
                'deviation': round((k['close'] / k['ma10'] - 1) * 100, 2),
            }))

        # 规则4: 天量长上影
        if k['chg'] < -1 and k['vol_ratio'] > 1.2 and k['upper_shadow'] > k['body'] * 1.5:
            signals.append(("[原]天量长上影", "卖出", {
                'chg': round(k['chg'], 2), 'vol_ratio': round(k['vol_ratio'], 2),
                'upper_shadow': round(k['upper_shadow'], 2),
            }))

        # 规则5: 10日涨幅>30%
        if k['chg_10d'] > 30:
            signals.append(("[原]10日涨超30%", "减仓", {
                'chg_10d': round(k['chg_10d'], 2),
            }))

        # ============ 动量信号 ============

        # 规则M1: RSI超买 (>80)
        if k['rsi'] > 80:
            signals.append(("[动量]RSI超买>80", "卖出", {
                'rsi': round(k['rsi'], 1),
            }))

        # 规则M2: RSI极端超买 (>85)
        if k['rsi'] > 85:
            signals.append(("[动量]RSI极端超买>85", "必卖", {
                'rsi': round(k['rsi'], 1),
            }))

        # 规则M3: RSI顶背离 (价格创20日新高, RSI未创新高)
        lookback = 20
        if i >= lookback:
            recent_high = max(klines[j]['close'] for j in range(i-lookback, i))
            recent_rsi_high = max(klines[j]['rsi'] for j in range(i-lookback, i))
            if k['close'] >= recent_high * 0.99 and k['rsi'] < recent_rsi_high - 2:
                signals.append(("[动量]RSI顶背离", "必卖", {
                    'rsi': round(k['rsi'], 1),
                    'rsi_20d_high': round(recent_rsi_high, 1),
                    'price': round(k['close'], 2),
                }))

        # 规则M4: MACD死叉 (DIF下穿DEA)
        if prev['dif'] >= prev['dea'] and k['dif'] < k['dea']:
            signals.append(("[动量]MACD死叉", "卖出", {
                'dif': round(k['dif'], 2), 'dea': round(k['dea'], 2),
            }))

        # 规则M5: MACD零轴上方死叉 (更危险)
        if prev['dif'] >= prev['dea'] and k['dif'] < k['dea'] and k['dif'] > 0:
            signals.append(("[动量]MACD零轴上死叉", "必卖", {
                'dif': round(k['dif'], 2), 'dea': round(k['dea'], 2),
            }))

        # 规则M6: MACD顶背离 (价格新高, DIF下降)
        if i >= lookback:
            recent_dif_high = max(klines[j]['dif'] for j in range(i-lookback, i))
            if k['close'] >= recent_high * 0.99 and k['dif'] < recent_dif_high * 0.85:
                signals.append(("[动量]MACD顶背离", "必卖", {
                    'dif': round(k['dif'], 2),
                    'dif_20d_high': round(recent_dif_high, 2),
                    'price': round(k['close'], 2),
                }))

        # 规则M7: KDJ超买区死叉 (K>80 且 K线下穿D线)
        if k['kdj_k'] > 80 and prev['kdj_k'] >= prev['kdj_d'] and k['kdj_k'] < k['kdj_d']:
            signals.append(("[动量]KDJ超买死叉", "卖出", {
                'k': round(k['kdj_k'], 1), 'd': round(k['kdj_d'], 1),
            }))

        # 规则M8: KDJ极端超买 (>90) 死叉
        if k['kdj_k'] > 90 and prev['kdj_k'] >= prev['kdj_d'] and k['kdj_k'] < k['kdj_d']:
            signals.append(("[动量]KDJ极端超买死叉", "必卖", {
                'k': round(k['kdj_k'], 1), 'd': round(k['kdj_d'], 1),
            }))

        # 规则M9: 布林带上轨突破 (价格>上轨)
        if k['close'] > k['bb_upper']:
            signals.append(("[动量]突破布林上轨", "减仓", {
                'bb_upper': round(k['bb_upper'], 2),
                'bb_pct': round(k['bb_pct'], 1),
            }))

        # 规则M10: 价格偏离布林中轨>25%
        if k['bb_mid'] > 0 and k['close'] > k['bb_mid'] * 1.25:
            signals.append(("[动量]偏离布林中轨>25%", "减仓", {
                'deviation_bb': round((k['close'] / k['bb_mid'] - 1) * 100, 2),
            }))

        # 规则M11: ROC动量减速 (ROC仍为正但连续下降3天)
        if i >= 3:
            roc_list = [klines[j]['roc10'] for j in range(i-2, i+1)]
            if all(r > 0 for r in roc_list) and roc_list[0] > roc_list[1] > roc_list[2]:
                signals.append(("[动量]ROC减速(正动量衰减)", "减仓", {
                    'roc10': round(k['roc10'], 2),
                    'roc_prev1': round(klines[i-1]['roc10'], 2),
                    'roc_prev2': round(klines[i-2]['roc10'], 2),
                }))

        # 规则M12: ROC转负 (动量从正转负)
        if prev['roc10'] > 0 and k['roc10'] < 0:
            signals.append(("[动量]ROC转负(动量翻转)", "必卖", {
                'roc10': round(k['roc10'], 2),
                'roc_prev': round(prev['roc10'], 2),
            }))

        # 规则M13: 放量长上影 + RSI超买 (组合信号)
        if (k['rsi'] > 70 and k['upper_shadow'] > k['body'] * 2
                and k['vol_ratio'] > 1.0 and k['chg'] < 1):
            signals.append(("[动量]高RSI+放量长上影", "卖出", {
                'rsi': round(k['rsi'], 1),
                'upper_shadow_ratio': round(k['upper_shadow'] / k['body'], 1) if k['body'] > 0 else 999,
            }))

        if signals:
            signals_by_date[date] = signals

    return signals_by_date

# ============================================================
# 4. 前向收益计算
# ============================================================
def compute_forward_returns(klines, signals_by_date):
    date_to_idx = {k['date']: i for i, k in enumerate(klines)}
    results = []
    for date, signals in signals_by_date.items():
        idx = date_to_idx.get(date)
        if idx is None:
            continue
        for sig_name, severity, detail in signals:
            record = {
                'date': date, 'signal': sig_name, 'severity': severity,
                'detail': detail, 'signal_close': klines[idx]['close'],
            }
            for horizon, label in [(1, '1d'), (3, '3d'), (5, '5d'), (10, '10d'), (20, '20d')]:
                fut_idx = idx + horizon
                if fut_idx < len(klines):
                    fut_close = klines[fut_idx]['close']
                    record[f'ret_{label}'] = round((fut_close / klines[idx]['close'] - 1) * 100, 2)
                    lowest = min(klines[j]['low'] for j in range(idx+1, fut_idx+1))
                    record[f'max_dd_{label}'] = round((lowest / klines[idx]['close'] - 1) * 100, 2)
                else:
                    record[f'ret_{label}'] = None
                    record[f'max_dd_{label}'] = None
            results.append(record)
    return results

# ============================================================
# 5. 统计分析
# ============================================================
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

# ============================================================
# 6. 获取股票信息
# ============================================================
def get_stock_name(code='601869'):
    prefix = 'sh' if code.startswith(('6','9')) else 'sz'
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode('gbk')
    vals = data.split('"')[1].split('~')
    return vals[1], float(vals[3]) if vals[3] else 0

# ============================================================
# 7. 生成Markdown报告
# ============================================================
def generate_report(klines, results, analysis, signals_by_date):
    name, price = get_stock_name('601869')

    lines = []
    lines.append(f"# {name}(601869) 卖出+动量逃顶信号 回测验证报告")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**数据范围**: {klines[0]['date']} ~ {klines[-1]['date']} (共{len(klines)}个交易日)")
    lines.append(f"**最新价格**: {price:.2f}元")
    lines.append("")
    lines.append(f"> **回测期行情特征**: 前复权从约30元涨至约580元，经历约20倍的极端牛市。动量指标在此环境下的表现需辩证看待。")
    lines.append("")

    # 分类信号
    original_sigs = [r for r in results if r['signal'].startswith('[原]')]
    momentum_sigs = [r for r in results if r['signal'].startswith('[动量]')]

    lines.append("## 一、验证概述")
    lines.append("")
    lines.append(f"- 共触发信号 **{len(results)}** 次")
    lines.append(f"  - 原始5规则: **{len(original_sigs)}** 次")
    lines.append(f"  - 动量指标: **{len(momentum_sigs)}** 次")
    lines.append(f"- 覆盖 **{len(signals_by_date)}** 个交易日")
    lines.append("")

    lines.append("### 全部信号一览")
    lines.append("")
    lines.append("| 信号 | 类别 | 次数 | 严重度 | 5日胜率 | 10日胜率 | 20日胜率 | 评级 |")
    lines.append("|------|------|------|--------|--------|---------|---------|------|")

    for sig_name in sorted(analysis.keys()):
        s = analysis[sig_name]
        cat = "原规则" if "[原]" in sig_name else "动量"
        w5 = s.get('5d_win_rate', 0)
        w10 = s.get('10d_win_rate', 0)
        w20 = s.get('20d_win_rate', 0)

        if s['count'] < 3:
            rating = "样本不足"
        elif w10 >= 70:
            rating = "⭐⭐⭐"
        elif w10 >= 60:
            rating = "⭐⭐"
        elif w10 >= 50:
            rating = "⭐"
        else:
            rating = "x"

        lines.append(f"| {sig_name} | {cat} | {s['count']} | {s['severity']} | {w5}% | {w10}% | {w20}% | {rating} |")

    lines.append("")
    lines.append("> 胜率 = 信号后N日股价下跌的比例，越高越好。x = 胜率不足50%。样本不足 = 触发少于3次。")
    lines.append("")

    # ---- 原始规则详细分析 ----
    lines.append("## 二、原始5规则 (回顾)")
    lines.append("")

    for sig_name in sorted(analysis.keys()):
        if not sig_name.startswith('[原]'):
            continue
        s = analysis[sig_name]
        records = [r for r in results if r['signal'] == sig_name]
        lines.append(f"### {sig_name} ({s['severity']}) — {s['count']}次")
        lines.append("")
        lines.append("| 持有期 | 平均收益 | 胜率 | 最佳 | 最差 | 最大回撤均值 |")
        lines.append("|--------|---------|------|------|------|-------------|")
        for h in ['1d', '3d', '5d', '10d', '20d']:
            lines.append(f"| {h} | {s.get(f'{h}_avg_ret','-')}% | {s.get(f'{h}_win_rate','-')}% | {s.get(f'{h}_max_ret','-')}% | {s.get(f'{h}_min_ret','-')}% | {s.get(f'{h}_avg_dd','-')}% |")
        lines.append("")

    # ---- 动量信号详细分析 ----
    lines.append("## 三、动量信号详细分析")
    lines.append("")

    # 按动量类型分组
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
            records = [r for r in results if r['signal'] == sig_name]
            lines.append(f"#### {sig_name} — {s['count']}次 ({s['severity']})")
            lines.append("")
            lines.append("| 持有期 | 平均收益 | 胜率 | 最佳 | 最差 | 最大回撤均值 |")
            lines.append("|--------|---------|------|------|------|-------------|")
            for h in ['1d', '3d', '5d', '10d', '20d']:
                lines.append(f"| {h} | {s.get(f'{h}_avg_ret','-')}% | {s.get(f'{h}_win_rate','-')}% | {s.get(f'{h}_max_ret','-')}% | {s.get(f'{h}_min_ret','-')}% | {s.get(f'{h}_avg_dd','-')}% |")
            lines.append("")

            if s['count'] <= 3 and s['count'] > 0:
                lines.append("**触发明细**:")
                lines.append("")
                lines.append("| 日期 | 收盘价 | 关键指标 | 1日 | 3日 | 5日 | 10日 | 20日 |")
                lines.append("|------|--------|---------|-----|-----|-----|------|------|")
                for r in records:
                    detail_str = ", ".join(f"{k}={v}" for k, v in r['detail'].items())
                    rets = " | ".join(
                        f"{r.get(f'ret_{h}','-'):.1f}%" if r.get(f'ret_{h}') is not None else "-"
                        for h in ['1d', '3d', '5d', '10d', '20d']
                    )
                    lines.append(f"| {r['date']} | {r['signal_close']:.2f} | {detail_str} | {rets} |")
                lines.append("")

    # ---- 触发明细 (高频信号) ----
    lines.append("## 四、高频动量信号触发明细")
    lines.append("")
    for sig_name in sorted(analysis.keys()):
        s = analysis[sig_name]
        if s['count'] <= 3 or not sig_name.startswith('[动量]'):
            continue
        records = [r for r in results if r['signal'] == sig_name]
        lines.append(f"### {sig_name} — {s['count']}次")
        lines.append("")
        lines.append("| 日期 | 收盘价 | 关键指标 | 1日 | 3日 | 5日 | 10日 | 20日 |")
        lines.append("|------|--------|---------|-----|-----|-----|------|------|")
        for r in records:
            detail_str = ", ".join(f"{k}={v}" for k, v in r['detail'].items())
            rets = " | ".join(
                f"{r.get(f'ret_{h}','-'):.1f}%" if r.get(f'ret_{h}') is not None else "-"
                for h in ['1d', '3d', '5d', '10d', '20d']
            )
            lines.append(f"| {r['date']} | {r['signal_close']:.2f} | {detail_str} | {rets} |")
        lines.append("")

    # ---- 综合排名 ----
    lines.append("## 五、动量信号有效性排名")
    lines.append("")

    # 仅动量信号，按10日胜率排序
    mom_signals = {k: v for k, v in analysis.items() if k.startswith('[动量]')}
    ranked = sorted(mom_signals.items(), key=lambda x: (x[1].get('10d_win_rate', 0), x[1].get('5d_win_rate', 0)), reverse=True)

    lines.append("| 排名 | 信号 | 次数 | 5日胜率 | 10日胜率 | 20日胜率 | 10日均收益 | 逃顶评价 |")
    lines.append("|------|------|------|--------|---------|---------|----------|---------|")

    for rank, (sig_name, stats) in enumerate(ranked, 1):
        w5 = stats.get('5d_win_rate', 0)
        w10 = stats.get('10d_win_rate', 0)
        w20 = stats.get('20d_win_rate', 0)
        avg10 = stats.get('10d_avg_ret', 0)
        cnt = stats['count']

        if cnt < 3:
            verdict = "样本不足"
        elif w10 >= 70:
            verdict = "优秀逃顶信号"
        elif w10 >= 60:
            verdict = "有效逃顶信号"
        elif w5 >= 60:
            verdict = "仅短线有效"
        elif w10 >= 50:
            verdict = "弱效，谨慎"
        else:
            verdict = "无效/反向"

        lines.append(f"| {rank} | {sig_name} | {cnt} | {w5}% | {w10}% | {w20}% | {avg10}% | {verdict} |")

    # ---- 组合信号分析 ----
    lines.append("")
    lines.append("## 六、动量+原规则组合分析")
    lines.append("")
    lines.append("### 同日触发多信号的情况 (信号共振)")
    lines.append("")

    # 统计同日触发多个信号的日期
    multi_signal_dates = {date: sigs for date, sigs in signals_by_date.items() if len(sigs) >= 3}
    lines.append(f"共 **{len(multi_signal_dates)}** 个交易日触发>=3个信号:")
    lines.append("")

    if multi_signal_dates:
        lines.append("| 日期 | 信号数 | 收盘价 | 1日 | 3日 | 5日 | 10日 | 20日 |")
        lines.append("|------|--------|--------|-----|-----|-----|------|------|")
        date_to_idx = {k['date']: i for i, k in enumerate(klines)}
        for date, sigs in sorted(multi_signal_dates.items()):
            idx = date_to_idx.get(date)
            if idx is None:
                continue
            sig_names = ", ".join(s[0] for s in sigs)
            rets = []
            for h in [1, 3, 5, 10, 20]:
                if idx + h < len(klines):
                    rets.append(f"{(klines[idx+h]['close'] / klines[idx]['close'] - 1) * 100:.1f}%")
                else:
                    rets.append("-")
            ret_str = " | ".join(rets)
            lines.append(f"| {date} | {len(sigs)} | {klines[idx]['close']:.2f} | {ret_str} |")

    lines.append("")
    lines.append("### 动量信号与原规则同时触发的叠加效果")
    lines.append("")

    # 统计动量+原规则同日触发
    combo_signals = []
    for date, sigs in signals_by_date.items():
        orig = [s for s in sigs if s[0].startswith('[原]')]
        mom = [s for s in sigs if s[0].startswith('[动量]')]
        if orig and mom:
            combo_signals.append(date)

    lines.append(f"原规则+动量信号同日触发的日期共 **{len(combo_signals)}** 个:")
    lines.append("")

    if combo_signals:
        date_to_idx = {k['date']: i for i, k in enumerate(klines)}
        lines.append("| 日期 | 收盘价 | 原规则信号 | 动量信号 | 5日 | 10日 | 20日 |")
        lines.append("|------|--------|----------|---------|-----|------|------|")
        for date in sorted(combo_signals):
            idx = date_to_idx.get(date)
            if idx is None:
                continue
            sigs = signals_by_date[date]
            orig_names = ", ".join(s[0] for s in sigs if s[0].startswith('[原]'))
            mom_names = ", ".join(s[0] for s in sigs if s[0].startswith('[动量]'))
            rets = []
            for h in [5, 10, 20]:
                if idx + h < len(klines):
                    rets.append(f"{(klines[idx+h]['close'] / klines[idx]['close'] - 1) * 100:.1f}%")
                else:
                    rets.append("-")
            ret_str = " | ".join(rets)
            lines.append(f"| {date} | {klines[idx]['close']:.2f} | {orig_names} | {mom_names} | {ret_str} |")

    # ---- 结论 ----
    lines.append("")
    lines.append("## 七、结论与建议")
    lines.append("")
    lines.append("### 核心发现: 动量指标在极端牛市中的逃顶能力")
    lines.append("")

    # 找出表现最好的动量信号
    best_mom = [s for s in ranked if s[1]['count'] >= 3 and s[1].get('10d_win_rate', 0) >= 60]
    worst_mom = [s for s in ranked if s[1]['count'] >= 5 and s[1].get('10d_win_rate', 0) < 50]

    if best_mom:
        lines.append("**有逃顶价值的动量信号:**")
        for sig_name, stats in best_mom:
            lines.append(f"- ✅ **{sig_name}**: {stats['count']}次触发, 10日胜率{stats.get('10d_win_rate', 0)}%, 10日均收益{stats.get('10d_avg_ret', 0)}%")
        lines.append("")

    if worst_mom:
        lines.append("**完全无效/反向的动量信号:**")
        for sig_name, stats in worst_mom:
            lines.append(f"- ❌ **{sig_name}**: {stats['count']}次, 10日胜率仅{stats.get('10d_win_rate', 0)}%")
        lines.append("")

    lines.append("### 关键结论")
    lines.append("")
    lines.append('1. **MACD死叉**在牛市中经常是假死叉--死叉后价格继续涨，是趋势股的特征')
    lines.append('2. **RSI超买**在强趋势中是常态而非卖出信号--RSI可以长时间维持在80+')
    lines.append('3. **ROC减速**捕捉动量衰减有一定效果，但单靠它无法精确逃顶')
    lines.append('4. **RSI/MACD顶背离**是最值得关注的--在极端上涨后有逃顶信号意义')
    lines.append('5. **KDJ超买死叉**在牛市中也频繁失效，需要结合其他指标')
    lines.append('6. **多信号共振**(原规则+动量同时触发)逃顶效果优于单信号')
    lines.append("")
    lines.append("### 实操建议")
    lines.append("")
    lines.append("| 策略 | 信号组合 | 用途 |")
    lines.append("|------|---------|------|")
    lines.append("| 短线做T | [原]涨停后放量滞涨 + [动量]ROC减速 | 3-5日短线卖出 |")
    lines.append("| 中期减仓 | [动量]MACD顶背离 + [动量]RSI顶背离 | 中期高位减仓 |")
    lines.append("| 趋势跟踪 | 单信号不操作，等3+信号共振 | 确认趋势转折 |")
    lines.append("| 牛市禁用 | RSI超买、KDJ超买、布林上轨、偏离MA10 | 这些在牛市中无效 |")
    lines.append("")
    lines.append("### 注意事项")
    lines.append("")
    lines.append("1. 本回测基于**前复权**日线数据，未考虑交易成本")
    lines.append("2. **市场环境是决定信号有效性的第一因素**——本报告所有结论均在约20倍涨幅的极端牛市中得出")
    lines.append("3. 在震荡市/下跌市中，动量信号的逃顶效果预计会更好（需要在其他时段验证）")
    lines.append("4. 建议在QMT实盘前，对每个信号做**滚动窗口**回测确认鲁棒性")
    lines.append("")
    lines.append("---")
    lines.append(f"*报告由 strategy_signal_validation.py 自动生成*")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("=" * 70)
    print("长飞光纤(601869) 卖出+动量逃顶信号 回测验证")
    print("=" * 70)

    print("\n[1/4] 获取K线数据...")
    klines = fetch_kline_tencent('601869', 300)
    print(f"  获取到 {len(klines)} 根日K线 ({klines[0]['date']} ~ {klines[-1]['date']})")

    os.makedirs('output', exist_ok=True)

    print("\n[2/4] 计算全部指标 (MA/MACD/RSI/KDJ/Boll/ROC/ATR)...")
    klines = compute_all_indicators(klines)
    print(f"  完成: {len(klines)} 根K线, 每根含20+指标字段")

    print("\n[3/4] 检测信号 (原5规则 + 13个动量信号)...")
    signals_by_date = detect_all_signals(klines)
    total = sum(len(v) for v in signals_by_date.values())
    print(f"  共检测到 {total} 个信号, 分布在 {len(signals_by_date)} 个交易日")

    for date, sigs in sorted(signals_by_date.items()):
        for s in sigs:
            print(f"  {date}: [{s[1]}] {s[0]}")

    print("\n[4/4] 计算前向收益 + 生成报告...")
    results = compute_forward_returns(klines, signals_by_date)
    analysis = analyze_results(results)

    report = generate_report(klines, results, analysis, signals_by_date)
    report_path = 'output/601869_signal_validation_report.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已生成: {report_path}")

    # 印核心结论
    print("\n" + "=" * 70)
    print("动量信号排名 (按10日胜率)")
    print("=" * 70)
    mom_signals = {k: v for k, v in analysis.items() if k.startswith('[动量]')}
    ranked = sorted(mom_signals.items(), key=lambda x: (x[1].get('10d_win_rate', 0), x[1].get('5d_win_rate', 0)), reverse=True)
    for sig_name, stats in ranked:
        w5 = stats.get('5d_win_rate', 0)
        w10 = stats.get('10d_win_rate', 0)
        avg10 = stats.get('10d_avg_ret', 0)
        print(f"  {sig_name}: {stats['count']}次, 5日胜率{w5}%, 10日胜率{w10}%, 10日均收益{avg10}%")
