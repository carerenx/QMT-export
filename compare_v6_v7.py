# -*- coding: utf-8 -*-
"""对比 v6 vs v7 的改善效果"""
import sys, os, time, warnings
import numpy as np
import pandas as pd
from validate_v6_multiplier import fetch_data, _sma, _atr, _rsi, _up_streak

warnings.filterwarnings('ignore')

# ---- v7 配置 ----
SELL_TRIGGER_BASE = 0.55
DYNAMIC_MULT_MIN = 0.35
DYNAMIC_MULT_MAX = 1.50
DAILY_RANGE_CAP_ENABLED = True
DAILY_RANGE_CAP_MULT = 0.80

def _daily_range_ma(highs, lows, opens, period=10):
    n = len(opens)
    ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0: ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)

def calc_dynamic_sell_mult_v7(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    """v7 4因子动态乘数"""
    base = SELL_TRIGGER_BASE
    deviations = {}; total = 0.0

    # 趋势
    if trend == 'bear': d = -0.25 if up_streak == 0 else -0.15
    elif trend == 'bull':
        if up_streak >= 3: d = 0.25
        elif up_streak >= 1: d = 0.15
        else: d = 0.05
    else: d = 0.00
    deviations['趋势'] = d; total += d

    # 波动率综合
    if atr_pct > 0.08: atr_d = -0.30
    elif atr_pct > 0.07: atr_d = -0.22
    elif atr_pct > 0.06: atr_d = -0.15
    elif atr_pct > 0.05: atr_d = -0.08
    elif atr_pct > 0.03: atr_d = +0.05
    elif atr_pct > 0.02: atr_d = +0.15
    else: atr_d = +0.25

    if atr_ratio > 1.50: atrd_d = -0.25
    elif atr_ratio > 1.25: atrd_d = -0.18
    elif atr_ratio > 1.10: atrd_d = -0.10
    elif atr_ratio > 0.90: atrd_d = 0.00
    elif atr_ratio > 0.70: atrd_d = +0.12
    elif atr_ratio > 0.50: atrd_d = +0.20
    else: atrd_d = +0.25

    vol_d = atr_d * 0.55 + atrd_d * 0.45
    vol_d = max(-0.35, min(0.30, vol_d))
    deviations['波动率'] = round(vol_d, 2); total += vol_d

    # 成交量
    if vol_ratio > 2.00: d = -0.25
    elif vol_ratio > 1.50: d = -0.18
    elif vol_ratio > 1.20: d = -0.08
    elif vol_ratio > 0.80: d = 0.00
    elif vol_ratio > 0.60: d = +0.12
    elif vol_ratio > 0.40: d = +0.20
    else: d = +0.25
    deviations['成交量'] = d; total += d

    # RSI
    if rsi_val > 80: d = -0.25
    elif rsi_val > 70: d = -0.18
    elif rsi_val > 60: d = -0.08
    elif rsi_val > 55: d = -0.03
    elif rsi_val > 45: d = 0.00
    elif rsi_val > 40: d = +0.03
    elif rsi_val > 30: d = +0.10
    elif rsi_val > 20: d = +0.20
    else: d = +0.25
    deviations['RSI'] = d; total += d

    final = base + total
    final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final))
    return round(final, 2), deviations


def analyze_v7(df):
    """v7 逐日信号计算"""
    closes=df['close'].values.tolist();opens=df['open'].values.tolist()
    highs=df['high'].values.tolist();lows=df['low'].values.tolist()
    volumes=df['volume'].values.tolist();dates=df['date'].values.tolist()
    n=len(closes)
    atr_arr=_atr(highs,lows,closes,14);rsi_arr=_rsi(closes);up_arr=_up_streak(closes)

    records=[];mults=[]
    for i in range(60,n):
        co=opens[i];cc=closes[i];cv=volumes[i]
        curr_atr=atr_arr[i] or cc*0.03
        curr_atr_pct=curr_atr/cc if cc>0 else 0.03
        atr_w=atr_arr[max(0,i-20):i+1] if i>=20 else atr_arr[:i+1]
        atr_ma20=sum(atr_w)/len(atr_w) if atr_w else curr_atr
        atr_ratio=curr_atr/atr_ma20 if atr_ma20>0 else 1.0
        ma5=_sma(closes,5)[i];ma20=_sma(closes,20)[i]
        trend='bull' if (cc>ma20 and ma5>ma20) else ('bear' if (cc<ma20 and ma5<ma20) else 'sideways')
        curr_rsi=rsi_arr[i];curr_us=up_arr[i]
        vm20=_sma(volumes,20)[i];curr_vr=cv/vm20 if vm20>0 else 1.0
        m,devs=calc_dynamic_sell_mult_v7(trend,curr_atr_pct,atr_ratio,curr_vr,curr_rsi,curr_us)
        mults.append(m)
        trigger_raw=co+curr_atr*m
        drm10=_daily_range_ma(highs,lows,opens,10)[i]
        max_tr=co*(1.0+drm10*DAILY_RANGE_CAP_MULT)
        if DAILY_RANGE_CAP_ENABLED and trigger_raw>max_tr:
            trigger=round(max_tr,2);capped=True
        else:
            trigger=round(trigger_raw,2);capped=False
        dh=highs[i];dl=lows[i]
        reachable=(trigger<=dh)
        viable=(trend!='bull') and reachable
        records.append({'date':dates[i],'open':co,'close':cc,'high':dh,'low':dl,
            'atr':curr_atr,'atr_pct':curr_atr_pct,'trend':trend,
            'rsi':curr_rsi,'vol_ratio':curr_vr,'up_streak':curr_us,
            'mult':m,'trigger':trigger,'trigger_raw':trigger_raw,
            'capped':capped,'trigger_dist_pct':(trigger-co)/co*100 if co>0 else 0,
            'reachable':reachable,'viable':viable})
    return records,mults


def compare(df_v6, df_v7):
    """v6 vs v7 对比"""
    total=len(df_v6)
    v6_viable=len(df_v6[df_v6['t0_viable']])
    v7_viable=len(df_v7[df_v7['viable']])
    v6_reach=len(df_v6[df_v6['trigger_reachable']])
    v7_reach=len(df_v7[df_v7['reachable']])
    v6_avg_mult=df_v6['mult'].mean()
    v7_avg_mult=df_v7['mult'].mean()
    v6_avg_dist=df_v6['trigger_dist_pct'].mean()
    v7_avg_dist=df_v7['trigger_dist_pct'].mean()
    v7_capped=len(df_v7[df_v7['capped']])

    print(f'\n{"="*60}')
    print(f'  v6 vs v7 对比报告')
    print(f'{"="*60}')
    print(f'\n{"指标":<25} {"v6":>15} {"v7":>15} {"改善":>15}')
    print('-'*70)
    print(f'{"反T可行天数":<25} {v6_viable:>15} {v7_viable:>15} {v7_viable-v6_viable:>+15}')
    print(f'{"反T可行率":<25} {v6_viable/total*100:>14.1f}% {v7_viable/total*100:>14.1f}% {(v7_viable-v6_viable)/total*100:>+14.1f}%')
    print(f'{"触发线可达天数":<25} {v6_reach:>15} {v7_reach:>15} {v7_reach-v6_reach:>+15}')
    print(f'{"触发线可达率":<25} {v6_reach/total*100:>14.1f}% {v7_reach/total*100:>14.1f}% {(v7_reach-v6_reach)/total*100:>+14.1f}%')
    print(f'{"平均乘数":<25} {v6_avg_mult:>15.2f} {v7_avg_mult:>15.2f} {v7_avg_mult-v6_avg_mult:>+15.2f}')
    print(f'{"平均触发距离":<25} {v6_avg_dist:>14.2f}% {v7_avg_dist:>14.2f}% {v7_avg_dist-v6_avg_dist:>+14.2f}%')
    print(f'{"振幅约束触发":<25} {"N/A":>15} {v7_capped:>15} {"":>15}')

    # 按趋势分
    print(f'\n--- 按趋势分 ---')
    for t in ['bull','sideways','bear']:
        v6t=df_v6[df_v6['trend']==t];v7t=df_v7[df_v7['trend']==t]
        if len(v6t)>0:
            v6v=len(v6t[v6t['t0_viable']]);v7v=len(v7t[v7t['viable']])
            print(f'  {t:<10} 反T可行: v6={v6v}({v6v/len(v6t)*100:.0f}%)  v7={v7v}({v7v/len(v7t)*100:.0f}%)  '
                  f'改善={v7v-v6v:+d}')


if __name__ == '__main__':
    print('加载数据...')
    df = fetch_data()
    print('运行 v7 分析...')
    recs7, m7 = analyze_v7(df)
    df7 = pd.DataFrame(recs7)
    print('运行 v6 分析...')
    from validate_v6_multiplier import analyze as analyze_v6
    recs6, m6, _ = analyze_v6(df)
    df6 = pd.DataFrame(recs6)
    compare(df6, df7)
