# -*- coding: utf-8 -*-
"""Analyze v10 backtest trade data for optimization insights"""
import pandas as pd, numpy as np, os, glob, sys

output_dir = os.path.join(os.path.dirname(__file__), 'output')
files = glob.glob(os.path.join(output_dir, 'v10_backtest_trades_*.csv'))
if not files:
    print('No trade files found!')
    sys.exit(1)

latest = max(files, key=os.path.getmtime)
print('File:', os.path.basename(latest))
df = pd.read_csv(latest)
print('Total trades:', len(df))

SEP = '=' * 60

# ============================================================
# 1. STOP LOSS DEEP DIVE
# ============================================================
stops = df[df['reason'] == '止损']
normal = df[df['reason'] == '正常']
emergency = df[df['reason'] == '紧急']

print('\n' + SEP)
print('1. STOP LOSS DEEP DIVE')
print(SEP)
print('Stop losses: %d/%d (%.0f%%)' % (len(stops), len(df), len(stops)/len(df)*100))
print('Stop loss total loss: %.0f RMB' % stops['net_profit'].sum())
print('Stop loss avg: %.0f RMB/trade' % stops['net_profit'].mean())

# Price change from sell to buyback for stops
stops_copy = stops.copy()
stops_copy['pct_chg'] = (stops_copy['buyback_price'] - stops_copy['sell_price']) / stops_copy['sell_price'] * 100
print('Avg sell->buy move: %.2f%%' % stops_copy['pct_chg'].mean())
print('Min/Max move: %.2f%% ~ %.2f%%' % (stops_copy['pct_chg'].min(), stops_copy['pct_chg'].max()))

print('\nIndividual stop losses:')
for _, t in stops.iterrows():
    print('  %s trend=%-10s mult=%.2f ATR=%.1f%% sell=%.0f buy=%.0f net=%.0f' % (
        t['date'], t['trend'], t['sell_mult'], t['atr_pct']*100,
        t['sell_price'], t['buyback_price'], t['net_profit']))

print('\nStop loss by trend:')
for trend in ['bear', 'sideways', 'weak_bull']:
    cnt = len(stops[stops['trend'] == trend])
    if cnt > 0:
        print('  %s: %d trades, net=%.0f' % (trend, cnt, stops[stops['trend']==trend]['net_profit'].sum()))

# ============================================================
# 2. PROFIT DISTRIBUTION
# ============================================================
net = df['net_profit'].values
wins_arr = net[net > 0]
loss_arr = net[net <= 0]

print('\n' + SEP)
print('2. PROFIT DISTRIBUTION')
print(SEP)
print('Wins: %d (%.0f%%)' % (len(wins_arr), len(wins_arr)/len(df)*100))
print('Losses: %d (%.0f%%)' % (len(loss_arr), len(loss_arr)/len(df)*100))
print('Win distribution:  p25=%.0f  p50=%.0f  p75=%.0f  max=%.0f' % (
    np.percentile(wins_arr,25), np.median(wins_arr), np.percentile(wins_arr,75), wins_arr.max()))
print('Loss distribution: p25=%.0f  p50=%.0f  p75=%.0f  worst=%.0f' % (
    np.percentile(loss_arr,25), np.median(loss_arr), np.percentile(loss_arr,75), loss_arr.min()))

# Normal trades that lost money (should NOT happen ideally)
normal_loss = df[(df['reason'] == '正常') & (df['net_profit'] <= 0)]
print('\nNormal trades with LOSS (unexpected!): %d' % len(normal_loss))
for _, t in normal_loss.iterrows():
    print('  %s trend=%-10s ATR=%.1f%% mult=%.2f sell=%.0f buy=%.0f net=%.0f' % (
        t['date'], t['trend'], t['atr_pct']*100, t['sell_mult'],
        t['sell_price'], t['buyback_price'], t['net_profit']))

# Big losses
big = df[df['net_profit'] <= -500]
print('\nBig losses (<= -500 RMB): %d trades, total=%.0f' % (len(big), big['net_profit'].sum()))
for _, t in big.iterrows():
    print('  %s %-4s trend=%-10s ATR=%.1f%% mult=%.2f net=%.0f' % (
        t['date'], t['reason'], t['trend'], t['atr_pct']*100, t['sell_mult'], t['net_profit']))

# ============================================================
# 3. MULTIPLIER vs OUTCOME
# ============================================================
print('\n' + SEP)
print('3. SELL MULTIPLIER vs OUTCOME')
print(SEP)
df_copy = df.copy()
bins = [0, 0.30, 0.50, 0.70, 0.90, 1.50]
labels = ['<0.30', '0.30-0.50', '0.50-0.70', '0.70-0.90', '>0.90']
df_copy['mult_bin'] = pd.cut(df_copy['sell_mult'], bins=bins, labels=labels)
for grp, grp_df in df_copy.groupby('mult_bin', observed=False):
    if len(grp_df) > 0:
        wr = len(grp_df[grp_df['net_profit']>0])/len(grp_df)*100
        stops_n = len(grp_df[grp_df['reason']=='止损'])
        print('  mult %8s: %3d trades  wr=%.0f%%  avg=%.0f  stops=%d' % (
            grp, len(grp_df), wr, grp_df['net_profit'].mean(), stops_n))

# ============================================================
# 4. ATR vs OUTCOME
# ============================================================
print('\n' + SEP)
print('4. ATR %% vs OUTCOME')
print(SEP)
df_copy['atr_bin'] = pd.cut(df_copy['atr_pct']*100,
    bins=[0, 3, 5, 7, 10, 20],
    labels=['<3%', '3-5%', '5-7%', '7-10%', '>10%'])
for grp, grp_df in df_copy.groupby('atr_bin', observed=False):
    if len(grp_df) > 0:
        wr = len(grp_df[grp_df['net_profit']>0])/len(grp_df)*100
        stops_n = len(grp_df[grp_df['reason']=='止损'])
        print('  ATR %6s: %3d trades  wr=%.0f%%  avg=%.0f  stops=%d' % (
            grp, len(grp_df), wr, grp_df['net_profit'].mean(), stops_n))

# ============================================================
# 5. TREND x REASON CROSS
# ============================================================
print('\n' + SEP)
print('5. TREND x REASON CROSS ANALYSIS')
print(SEP)
for trend in ['bear', 'sideways', 'weak_bull']:
    td = df[df['trend'] == trend]
    if len(td) == 0:
        continue
    wr = len(td[td['net_profit']>0])/len(td)*100
    print('%s: %d trades, wr=%.0f%%, total_net=%.0f, avg=%.0f' % (
        trend, len(td), wr, td['net_profit'].sum(), td['net_profit'].mean()))
    for reason in ['正常', '止损', '紧急', '尾盘']:
        rd = td[td['reason'] == reason]
        if len(rd) > 0:
            print('  %4s: %2d trades  net=%7.0f  avg=%6.0f' % (
                reason, len(rd), rd['net_profit'].sum(), rd['net_profit'].mean()))

# ============================================================
# 6. CONSECUTIVE LOSSES
# ============================================================
print('\n' + SEP)
print('6. CONSECUTIVE LOSSES')
print(SEP)
cons = 0
max_cons = 0
cons_start = ''
for i, (_, t) in enumerate(df.iterrows()):
    if t['net_profit'] <= 0:
        if cons == 0:
            cons_start = t['date']
        cons += 1
        max_cons = max(max_cons, cons)
    else:
        if cons >= 2:
            segment = df.iloc[i-cons:i]
            print('  %s ~ %s: %d losses, total=%.0f' % (
                cons_start, df.iloc[i-1]['date'], cons, segment['net_profit'].sum()))
        cons = 0
print('Max consecutive losses: %d' % max_cons)

# ============================================================
# 7. WEAK BULL PROBLEM
# ============================================================
print('\n' + SEP)
print('7. WEAK BULL SPECIFIC ANALYSIS')
print(SEP)
wb = df[df['trend'] == 'weak_bull']
wb_stops = wb[wb['reason'] == '止损']
wb_normal = wb[wb['reason'] == '正常']
print('Weak bull trades: %d total' % len(wb))
print('  Normal: %d trades, net=%.0f, avg=%.0f' % (len(wb_normal), wb_normal['net_profit'].sum(), wb_normal['net_profit'].mean()))
print('  Stops:  %d trades, net=%.0f, avg=%.0f' % (len(wb_stops), wb_stops['net_profit'].sum(), wb_stops['net_profit'].mean()))
print('  Stop ratio: %.0f%% (vs overall %.0f%%)' % (
    len(wb_stops)/len(wb)*100 if len(wb) > 0 else 0,
    len(stops)/len(df)*100))
# Why do weak bull stops happen?
print('\nWeak bull stop details:')
for _, t in wb_stops.iterrows():
    chg = (t['buyback_price'] - t['sell_price']) / t['sell_price'] * 100
    print('  %s mult=%.2f ATR=%.1f%% sell=%.0f buy=%.0f (+%.1f%%) net=%.0f' % (
        t['date'], t['sell_mult'], t['atr_pct']*100,
        t['sell_price'], t['buyback_price'], chg, t['net_profit']))

# ============================================================
# 8. EMERGENCY BUYBACK ANALYSIS
# ============================================================
print('\n' + SEP)
print('8. EMERGENCY BUYBACK ANALYSIS')
print(SEP)
print('Emergency buybacks: %d' % len(emergency))
for _, t in emergency.iterrows():
    print('  %s trend=%s ATR=%.1f%% mult=%.2f sell=%.0f buy=%.0f net=%.0f' % (
        t['date'], t['trend'], t['atr_pct']*100, t['sell_mult'],
        t['sell_price'], t['buyback_price'], t['net_profit']))

# ============================================================
# 9. GROSS MARGIN DECOMPOSITION
# ============================================================
print('\n' + SEP)
print('9. TRADE ECONOMICS')
print(SEP)
avg_sell = df['sell_price'].mean()
avg_buy = df['buyback_price'].mean()
avg_gross = df['gross_profit'].mean()
avg_net = df['net_profit'].mean()
avg_fee = avg_gross - avg_net
print('Avg sell: %.2f  |  avg buyback: %.2f' % (avg_sell, avg_buy))
print('Avg spread: %.2f (%.3f%% of sell)' % (avg_sell - avg_buy, (avg_sell-avg_buy)/avg_sell*100))
print('Avg gross: %.2f  |  avg fee: %.2f  |  avg net: %.2f' % (avg_gross, avg_fee, avg_net))
print('Fee as %% of gross: %.1f%%' % (avg_fee/avg_gross*100 if avg_gross > 0 else 0))

# ============================================================
# 10. TRIGGER PRICE vs ACTUAL SELL
# ============================================================
print('\n' + SEP)
print('10. TRIGGER vs ACTUAL SELL PRICE')
print(SEP)
df_copy2 = df.copy()
df_copy2['overshoot'] = (df_copy2['sell_price'] - df_copy2['sell_trigger']) / df_copy2['sell_trigger'] * 100
print('Avg trigger overshoot: %.2f%%' % df_copy2['overshoot'].mean())
print('Min/Max overshoot: %.2f%% ~ %.2f%%' % (df_copy2['overshoot'].min(), df_copy2['overshoot'].max()))

# Does more overshoot = worse outcome?
df_copy2['overshoot_bin'] = pd.cut(df_copy2['overshoot'], bins=[-10, 0, 2, 5, 50], labels=['<0%', '0-2%', '2-5%', '>5%'])
print('\nOvershoot vs outcome:')
for grp, grp_df in df_copy2.groupby('overshoot_bin', observed=False):
    if len(grp_df) > 0:
        wr = len(grp_df[grp_df['net_profit']>0])/len(grp_df)*100
        print('  overshoot %5s: %3d trades  wr=%.0f%%  avg_net=%.0f' % (
            grp, len(grp_df), wr, grp_df['net_profit'].mean()))

print('\n' + SEP)
print('ANALYSIS COMPLETE')
print(SEP)
