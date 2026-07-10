# -*- coding: utf-8 -*-
"""
验证 v6 动态卖出乘数策略的合理性
  - 获取 601869 历史日线数据
  - 每个交易日计算5因子动态乘数
  - 分析乘数分布、触发线可达性、因子相关性、潜在问题
"""
import sys, os, time, json, warnings
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict

warnings.filterwarnings('ignore')

# ---- 直接复制 v6 策略中的指标函数和乘数计算 ----

ATR_PERIOD = 14
VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT = 75
SELL_TRIGGER_BASE = 1.0
DYNAMIC_MULT_MIN = 0.30
DYNAMIC_MULT_MAX = 2.00

def _sma(values, period):
    n = len(values)
    r = [0.0] * n
    for i in range(period - 1, n):
        r[i] = sum(values[i - period + 1 : i + 1]) / period
    return r

def _atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    r = [0.0] * n
    for i in range(period, n):
        r[i] = sum(tr[i - period + 1 : i + 1]) / period
    return r

def _rsi(closes, period=14):
    n = len(closes)
    if n < period + 1: return [50.0] * n
    rsi = [50.0] * n
    g, l = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        g.append(d if d > 0 else 0); l.append(abs(d) if d < 0 else 0)
    ag = sum(g[:period]) / period; al = sum(l[:period]) / period
    rsi[period] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    for i in range(period, n - 1):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    return rsi

def _up_streak(closes):
    n = len(closes); s = [0] * n
    for i in range(1, n): s[i] = s[i-1] + 1 if closes[i] > closes[i-1] else 0
    return s

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    """v6 5因子动态乘数 (精确复制)"""
    deviations = {}; total = 0.0
    # 因子1: 趋势
    if trend == 'bear':
        d = -0.40 if up_streak == 0 else -0.25
    elif trend == 'bull':
        if up_streak >= 3: d = 0.40
        elif up_streak >= 1: d = 0.25
        else: d = 0.10
    else: d = 0.00
    deviations['趋势'] = d; total += d
    # 因子2: ATR水平
    if atr_pct > 0.08: d = -0.30
    elif atr_pct > 0.07: d = -0.25
    elif atr_pct > 0.06: d = -0.18
    elif atr_pct > 0.05: d = -0.10
    elif atr_pct > 0.03: d = 0.00
    elif atr_pct > 0.02: d = 0.15
    else: d = 0.25
    deviations['ATR水平'] = d; total += d
    # 因子3: ATR变化
    if atr_ratio > 1.40: d = -0.20
    elif atr_ratio > 1.20: d = -0.12
    elif atr_ratio > 1.05: d = -0.05
    elif atr_ratio > 0.95: d = 0.00
    elif atr_ratio > 0.80: d = 0.10
    elif atr_ratio > 0.60: d = 0.18
    else: d = 0.25
    deviations['波动率Δ'] = d; total += d
    # 因子4: 成交量
    if vol_ratio > 2.00: d = -0.25
    elif vol_ratio > 1.50: d = -0.18
    elif vol_ratio > 1.20: d = -0.08
    elif vol_ratio > 0.80: d = 0.00
    elif vol_ratio > 0.60: d = 0.12
    elif vol_ratio > 0.40: d = 0.20
    else: d = 0.25
    deviations['成交量'] = d; total += d
    # 因子5: RSI
    if rsi_val > 80: d = -0.30
    elif rsi_val > 70: d = -0.20
    elif rsi_val > 60: d = -0.08
    elif rsi_val > 40: d = 0.00
    elif rsi_val > 30: d = 0.12
    elif rsi_val > 20: d = 0.22
    else: d = 0.30
    deviations['RSI'] = d; total += d
    # 最终
    final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, SELL_TRIGGER_BASE + total))
    return round(final, 2), deviations


# ============================================================
# 数据获取
# ============================================================

def fetch_data(code='601869'):
    """从腾讯获取日线"""
    import requests
    UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    tc = f'sh{code}'
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    r = requests.get(url, params={'param': f'{tc},day,,,800,qfq'},
                     headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}, timeout=15)
    data = r.json()
    raw = data.get('data', {}).get(tc, {}).get('qfqday', []) or \
          data.get('data', {}).get(tc, {}).get('day', [])
    klines = [{'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])} for k in raw]
    df = pd.DataFrame(klines).sort_values('date').reset_index(drop=True)
    print(f'数据: {len(df)} 条, {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')
    return df


# ============================================================
# 逐日分析
# ============================================================

def analyze(df):
    """逐日计算 v6 信号, 收集统计数据"""
    closes  = df['close'].values.tolist()
    opens   = df['open'].values.tolist()
    highs   = df['high'].values.tolist()
    lows    = df['low'].values.tolist()
    volumes = df['volume'].values.tolist()
    dates   = df['date'].values.tolist()
    n = len(closes)

    atr_arr   = _atr(highs, lows, closes, ATR_PERIOD)
    rsi_arr   = _rsi(closes)
    up_arr    = _up_streak(closes)

    records = []
    mults = []
    factor_stats = defaultdict(list)

    for i in range(60, n):  # 从第60天开始(指标需要足够数据)
        co = opens[i]; cc = closes[i]; cv = volumes[i]
        curr_atr = atr_arr[i] or cc * 0.03
        curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

        # ATR变化方向
        atr_window = atr_arr[max(0,i-20):i+1] if i >= 20 else atr_arr[:i+1]
        atr_ma20 = sum(atr_window) / len(atr_window) if atr_window else curr_atr
        atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

        ma5  = _sma(closes, 5)[i]
        ma20 = _sma(closes, 20)[i]
        trend = 'bull' if (cc > ma20 and ma5 > ma20) else \
                ('bear' if (cc < ma20 and ma5 < ma20) else 'sideways')

        curr_rsi = rsi_arr[i]
        curr_us = up_arr[i]

        vol_ma20 = _sma(volumes, 20)[i]
        curr_vr = cv / vol_ma20 if vol_ma20 > 0 else 1.0

        # v6 动态乘数
        m, devs = calc_dynamic_sell_mult(trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, curr_us)
        mults.append(m)

        # 卖出触发线
        sell_trigger = co + curr_atr * m

        # 当日实际价格范围
        day_high = highs[i]
        day_low  = lows[i]
        day_range_pct = (day_high - day_low) / co * 100 if co > 0 else 0

        # 触发线相对开盘的距离百分比
        trigger_dist_pct = (sell_trigger - co) / co * 100 if co > 0 else 0

        # ★ 关键检查: 触发线是否在当日价格范围内? (是=今日可以触发卖出)
        trigger_reachable = (sell_trigger <= day_high)

        # ★ 反T可行性: 不是牛市 + 触发线可达
        t0_viable = (trend != 'bull') and trigger_reachable

        # ★ 如果卖出触发, 以最高价卖出, 收盘买回的理论收益
        if trigger_reachable and trend != 'bull':
            # 模拟: 触发线越过后等回落PULLBACK=0.1%
            # 简化: 假设在触发线上方回落0.1%处卖出, 收盘买回
            theoretical_sell = max(sell_trigger, co) * (1 - 0.001)  # 粗略
            theoretical_pnl = (theoretical_sell - cc) / cc * 100 if cc > 0 else 0
        else:
            theoretical_pnl = None

        # 收集因子贡献
        for fn, fv in devs.items():
            factor_stats[fn].append(fv)

        records.append({
            'date': dates[i], 'open': co, 'close': cc,
            'high': day_high, 'low': day_low,
            'atr': curr_atr, 'atr_pct': curr_atr_pct, 'atr_ratio': atr_ratio,
            'trend': trend, 'rsi': curr_rsi, 'vol_ratio': curr_vr,
            'up_streak': curr_us,
            'mult': m, 'sell_trigger': sell_trigger,
            'trigger_dist_pct': trigger_dist_pct,
            'day_range_pct': day_range_pct,
            'trigger_reachable': trigger_reachable,
            't0_viable': t0_viable,
            't0_pnl_pct': theoretical_pnl,
            'devs': devs,
        })

    return records, mults, factor_stats


def print_report(records, mults, factor_stats):
    """打印分析报告"""
    df = pd.DataFrame(records)
    total_days = len(df)
    bull_days = len(df[df['trend'] == 'bull'])
    bear_days = len(df[df['trend'] == 'bear'])
    sw_days   = len(df[df['trend'] == 'sideways'])
    viable     = len(df[df['t0_viable']])
    blocked    = total_days - viable - bull_days  # 被其他原因屏蔽的(缩量/RSI等)

    print(f'\n{"="*60}')
    print(f'  v6 动态乘数验证报告')
    print(f'{"="*60}')
    print(f'\n[1] 数据统计')
    print(f'  分析天数: {total_days}')
    print(f'  趋势分布: 牛市{bull_days}({bull_days/total_days*100:.1f}%)  '
          f'熊市{bear_days}({bear_days/total_days*100:.1f}%)  '
          f'震荡{sw_days}({sw_days/total_days*100:.1f}%)')
    print(f'  反T可行天数: {viable}({viable/total_days*100:.1f}%)')
    print(f'    其中牛市屏蔽: {bull_days}天')
    print(f'    其他屏蔽: {blocked}天')

    # 乘数分布
    print(f'\n[2] 动态乘数分布')
    print(f'  均值: {np.mean(mults):.2f}  中位: {np.median(mults):.2f}  '
          f'  最小: {min(mults):.2f}  最大: {max(mults):.2f}  '
          f'  标准差: {np.std(mults):.2f}')
    percentiles = [10, 25, 50, 75, 90]
    print(f'  分位数: ' + ' | '.join(f'P{p}={np.percentile(mults, p):.2f}' for p in percentiles))

    # 乘数 vs 趋势
    print(f'\n[3] 乘数 × 趋势')
    for t in ['bull', 'sideways', 'bear']:
        tdf = df[df['trend'] == t]
        if len(tdf) > 0:
            print(f'  {t:<10} 均值={tdf["mult"].mean():.2f}  '
                  f'范围=[{tdf["mult"].min():.2f}, {tdf["mult"].max():.2f}]  '
                  f'n={len(tdf)}')

    # 每因子贡献
    print(f'\n[4] 各因子贡献统计')
    for fn in ['趋势', 'ATR水平', '波动率Δ', '成交量', 'RSI']:
        vals = factor_stats[fn]
        non_zero = [v for v in vals if v != 0]
        print(f'  {fn:<6} 均值={np.mean(vals):+.3f}  '
              f'非零比={len(non_zero)/len(vals)*100:.0f}%  '
              f'范围=[{min(vals):+.2f}, {max(vals):+.2f}]')

    # 触发线可达性
    print(f'\n[5] 触发线可达性分析')
    reachable = df[df['trigger_reachable']]
    unreachable = df[~df['trigger_reachable']]
    print(f'  触发线可达: {len(reachable)}({len(reachable)/total_days*100:.1f}%)')
    print(f'  触发线过高: {len(unreachable)}({len(unreachable)/total_days*100:.1f}%)')
    if len(unreachable) > 0:
        print(f'    不可达样本(最近10条):')
        for _, r in unreachable.tail(10).iterrows():
            print(f'      {r["date"]} 开盘{r["open"]:.1f} 触发线{r["sell_trigger"]:.1f} '
                  f'(+{r["trigger_dist_pct"]:.1f}%) 最高{r["high"]:.1f} '
                  f'乘数{r["mult"]:.2f} 趋势{r["trend"]}')

    # ★ 核心问题检查
    print(f'\n[6] !! 问题诊断')

    # 问题1: ATR水平+ATR变化 相关性
    atr_levels = df['atr_pct'].values
    atr_ratios = df['atr_ratio'].values
    corr = np.corrcoef(atr_levels, atr_ratios)[0, 1]
    print(f'  ATR水平 vs ATRΔ 相关系数: {corr:.3f}')
    if abs(corr) > 0.5:
        print(f'    !! 高度相关! 两因子存在重复计数, 建议合并或降低权重')

    # 问题2: 极端乘数的触发线
    extreme_low = df[df['mult'] == DYNAMIC_MULT_MIN]
    if len(extreme_low) > 0:
        print(f'  乘数到底({DYNAMIC_MULT_MIN})的天数: {len(extreme_low)}')
        ex = extreme_low.iloc[0]
        print(f'    示例: {ex["date"]} 开盘{ex["open"]:.1f} ATR%={ex["atr_pct"]*100:.1f}% '
              f'触发线={ex["sell_trigger"]:.1f}(+{ex["trigger_dist_pct"]:.1f}%)')

    extreme_high = df[df['mult'] == DYNAMIC_MULT_MAX]
    if len(extreme_high) > 0:
        print(f'  乘数到顶({DYNAMIC_MULT_MAX})的天数: {len(extreme_high)}')

    # 问题3: 熊市+非牛市天数 vs 反T可行性
    non_bull = df[df['trend'] != 'bull']
    viable_non_bull = non_bull[non_bull['t0_viable']]
    print(f'  非牛市天数: {len(non_bull)}')
    print(f'  其中反T可行: {len(viable_non_bull)}({len(viable_non_bull)/len(non_bull)*100:.1f}%)')
    if len(non_bull) > 0 and len(viable_non_bull) / len(non_bull) < 0.3:
        print(f'    !! 反T可行比例偏低! 即使在非牛市环境中, 触发线也可能过高')
        # 分析不可行的原因
        non_viable = non_bull[~non_bull['t0_viable']]
        avg_dist = non_viable['trigger_dist_pct'].mean()
        print(f'    不可行天数的平均触发距离: +{avg_dist:.1f}% (意味着等待涨幅太大)')

    # 问题4: RSI过滤的合理性
    rsi_blocked = df[(df['trend'] != 'bull') & (df['rsi'] > RSI_OVERBOUGHT)]
    print(f'  RSI超买({RSI_OVERBOUGHT})屏蔽天数: {len(rsi_blocked)}')

    # 问题5: 牛市趋势占比
    print(f'\n[7] 按年统计')
    df['year'] = pd.to_datetime(df['date']).dt.year
    for yr, grp in df.groupby('year'):
        b = len(grp[grp['trend'] == 'bull'])
        v = len(grp[grp['t0_viable']])
        print(f'  {yr}: {len(grp)}天 | 牛市{b} | 反T可行{v} | 乘数均值{grp["mult"].mean():.2f}')

    return df


if __name__ == '__main__':
    print('获取 601869 历史数据...')
    df = fetch_data('601869')
    records, mults, factor_stats = analyze(df)
    result_df = print_report(records, mults, factor_stats)

    # 保存详细结果
    out = 'd:/02Project/QMT-export/analysis/v6_multiplier_validation.csv'
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # 展平 devs dict
    export = []
    for r in records:
        row = {k: v for k, v in r.items() if k != 'devs'}
        for k, v in r['devs'].items():
            row[f'dev_{k}'] = v
        export.append(row)
    pd.DataFrame(export).to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\n详细数据已保存: {out}')
