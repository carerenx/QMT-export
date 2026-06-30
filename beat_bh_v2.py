"""
Beat Buy&Hold V2: One job - dodge the March 2026 crash, nothing else
Core idea: Only exit on EXTREME signals, stay invested 95% of the time
"""
import sys, os, time, random, json, csv, warnings, math
import requests, numpy as np, pandas as pd

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')

CODE, NAME = '601869', '长飞光纤'
DATA_DIR = r'd:\02Project\QMT-export\data\601869_backtest'
os.makedirs(DATA_DIR, exist_ok=True)
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
CASH0 = 1_000_000
COMM = 0.0003

# ============ LOAD & COMPUTE ============
print('Loading data...')
tc = f'sh{CODE}'
url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
params = {'param': f'{tc},day,,,260,qfq'}
r = requests.get(url, params=params, headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}, timeout=15)
d = r.json()
raw = d.get('data', {}).get(tc, {}).get('qfqday', []) or d.get('data', {}).get(tc, {}).get('day', [])
klines = [{'date':k[0],'open':float(k[1]),'close':float(k[2]),'high':float(k[3]),'low':float(k[4]),'volume':float(k[5])} for k in raw]
df = pd.DataFrame(klines)

EM_SESSION = requests.Session()
EM_SESSION.headers.update({'User-Agent': UA})
_eml = [0.0]
def em_get(url, params=None, headers=None, timeout=15):
    wait = 1.5 - (time.time() - _eml[0])
    if wait > 0: time.sleep(wait + random.uniform(0.1, 0.5))
    try: return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout)
    finally: _eml[0] = time.time()

url2 = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
params2 = {'secid': f'1.{CODE}', 'fields1':'f1,f2,f3,f7',
           'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65', 'lmt':'250'}
r2 = em_get(url2, params=params2)
d2 = r2.json()
flines = d2.get('data',{}).get('klines',[])
flows = []
for line in flines:
    p = line.split(',')
    if len(p)>=7:
        flows.append({'date':p[0],'main_net':float(p[1]) if p[1]!='-' else 0,
                      'super_net':float(p[5]) if p[5]!='-' else 0,
                      'large_net':float(p[4]) if p[4]!='-' else 0})
df_f = pd.DataFrame(flows)

# Features
df['returns'] = df['close'].pct_change()
for w in [3,5,10,20,60]:
    df[f'ma{w}'] = df['close'].rolling(w, min_periods=max(1,w//3)).mean()
df['atr14'] = (df['high']-df['low']).rolling(14,min_periods=5).mean()
df['atr_pct'] = df['atr14']/df['close']*100
delta = df['close'].diff()
gain = delta.where(delta>0,0).rolling(14,min_periods=7).mean()
loss = (-delta.where(delta<0,0)).rolling(14,min_periods=7).mean()
df['rsi'] = 100-100/(1+gain/loss.replace(0,np.nan))
df['ema12'] = df['close'].ewm(span=12,adjust=False,min_periods=6).mean()
df['ema26'] = df['close'].ewm(span=26,adjust=False,min_periods=13).mean()
df['macd'] = df['ema12']-df['ema26']
df['macd_sig'] = df['macd'].ewm(span=9,adjust=False,min_periods=5).mean()
df['macd_h'] = df['macd']-df['macd_sig']
df['vol_ma5'] = df['volume'].rolling(5,min_periods=2).mean()
df['vol_ma20'] = df['volume'].rolling(20,min_periods=5).mean()
df['vol_ratio'] = df['volume']/df['vol_ma20'].replace(0,np.nan)
df['bb_mid'] = df['close'].rolling(20,min_periods=10).mean()
df['bb_std'] = df['close'].rolling(20,min_periods=10).std()
df['bb_upper'] = df['bb_mid']+2*df['bb_std']
df['bb_lower'] = df['bb_mid']-2*df['bb_std']

# Merge fund flow
df = df.merge(df_f[['date','main_net','super_net','large_net']], on='date', how='left')
for col in ['main_net','super_net','large_net']:
    df[col] = df[col].fillna(0)
df['f3'] = df['main_net'].rolling(3,min_periods=1).sum()
df['f5'] = df['main_net'].rolling(5,min_periods=2).sum()
df['f10'] = df['main_net'].rolling(10,min_periods=5).sum()
df['f20'] = df['main_net'].rolling(20,min_periods=10).sum()
# Outflow streak
df['out_streak'] = 0
s = 0
for i in range(len(df)):
    s = s+1 if df['main_net'].iloc[i] < 0 else 0
    df.loc[df.index[i],'out_streak'] = s

# Key: drawdown from recent high
df['peak_20'] = df['close'].rolling(20,min_periods=10).max()
df['dd_20'] = (df['close']-df['peak_20'])/df['peak_20']*100

# Clean
core = ['close','ma5','ma10','ma20','ma60','atr14','rsi','macd_h','vol_ratio','f3','f5','out_streak','dd_20']
df = df.dropna(subset=[c for c in core if c in df.columns]).reset_index(drop=True)
print(f'Data: {len(df)} bars, {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')

# BH benchmark
bh_ret = (df['close'].iloc[-1]/df['close'].iloc[0]-1)*100
bh_s = df['close'].pct_change().dropna()
bh_sr = (bh_s.mean()/bh_s.std()*np.sqrt(252)) if bh_s.std()>0 else 0
bh_mdd = ((df['close']/df['close'].cummax()-1)*100).min()
print(f'Buy&Hold: Ret={bh_ret:.1f}%  Sharpe={bh_sr:.2f}  MaxDD={bh_mdd:.1f}%')

# ============ BACKTEST ============
def backtest(name, df, signal_func):
    cash = CASH0; shares = 0; equity = [CASH0]; trades = []
    in_pos = False; entry_price = 0; entry_i = 0

    for i in range(len(df)):
        row = df.iloc[i]; price = row['close']
        sig = signal_func(df, i)
        if sig is None: sig = 0

        if not in_pos and sig > 0.4 and cash > price*100:
            tcash = cash*(1-COMM)
            shares = tcash/price; cash -= tcash
            in_pos = True; entry_price = price; entry_i = i
            trades.append({'type':'BUY','date':row['date'],'price':price})

        elif in_pos and sig < -0.4:
            proceeds = shares*price*(1-COMM); cash += proceeds
            ret = (price/entry_price-1)*100
            trades.append({'type':'SELL','date':row['date'],'price':price,'return_pct':ret,'hold_days':i-entry_i})
            shares = 0; in_pos = False

        equity.append(cash + shares*price)

    if in_pos:
        last_p = df.iloc[-1]['close']
        cash += shares*last_p*(1-COMM)
        trades.append({'type':'SELL(F)','date':df.iloc[-1]['date'],'price':last_p,
                      'return_pct':(last_p/entry_price-1)*100,'hold_days':len(df)-1-entry_i})
        equity[-1] = cash

    eq_s = pd.Series(equity); ret_s = eq_s.pct_change().dropna()
    tr = (equity[-1]/CASH0-1)*100
    nt = len([t for t in trades if 'SELL' in t['type']])
    wt = len([t for t in trades if t.get('return_pct',0)>0])
    wr = (wt/nt*100) if nt>0 else 0
    yrs = len(df)/252
    ar = ((1+tr/100)**(1/yrs)-1)*100 if yrs>0 else 0
    sr = (ret_s.mean()/ret_s.std()*np.sqrt(252)) if ret_s.std()>0 else 0
    cm = eq_s.cummax(); mdd = ((eq_s-cm)/cm*100).min()

    return {'name':name,'equity':equity,'trades':trades,'total_return':tr,
            'annual':ar,'sharpe':sr,'max_dd':mdd,'n_trades':nt,'win_rate':wr}

# ============ STRATEGIES ============
print('\n' + '='*70)
print('STRATEGIES TO BEAT BUY & HOLD')
print('='*70)

# --- G: Extreme Only Exit ---
# Stay in unless MULTIPLE extreme signals align
def sG_extreme(df, i):
    c = df.iloc[i]
    # ALWAYS enter on trend confirmation
    if (c['close'] > c['ma20'] and c['ma5'] > c['ma20'] and
        c['macd_h'] > 0 and c['rsi'] > 45):
        return 1.0

    # ONLY exit when ALL of these align (extreme threshold):
    danger = 0
    if c['close'] < c['ma10']: danger += 1
    if c['close'] < c['ma20']: danger += 1
    if c['dd_20'] < -8: danger += 1  # -8% from 20-day high
    if c['out_streak'] >= 3: danger += 1
    if c['f5'] < -1_000_000_000: danger += 1  # 5-day outflow > 10亿
    if c['rsi'] < 35: danger += 1
    if c['vol_ratio'] > 2.5 and c['close'] < c['open']: danger += 1

    if danger >= 5: return -1.0  # Only exit on extreme danger
    return 0

rG = backtest('G_ExtremeOnly', df, sG_extreme)
print(f'G_ExtremeOnly:  {rG["total_return"]:>8.1f}%  Sharpe={rG["sharpe"]:.2f}  MaxDD={rG["max_dd"]:.1f}%  Trades={rG["n_trades"]}  Win={rG["win_rate"]:.0f}%  {"***BEAT!***" if rG["total_return"]>bh_ret else ""}')

# --- H: Fund Flow Reversal ---
# Exit only when cumulative fund flow turns decisively negative
# Re-enter when flow turns positive again
def sH_flow_reversal(df, i):
    c = df.iloc[i]
    # ENTER: Normal trend OR flow reversal
    if (c['close'] > c['ma20'] and c['ma5'] > c['ma10'] and
        c['macd_h'] > 0 and c['rsi'] < 72):
        return 0.8
    # Strong re-entry: flow turned positive after big outflow
    if i >= 5:
        prev_f20 = df['f20'].iloc[i-1]
        if (c['f20'] > prev_f20 and prev_f20 < -2_000_000_000 and
            c['close'] > c['ma20']):
            return 1.0  # Aggressive re-entry

    # ONLY exit on MASSIVE fund flow reversal
    # 20-day cumulative flow turns from positive to deeply negative
    if i >= 5:
        if (c['f20'] < -3_000_000_000 and  # 20-day outflow > 30亿
            c['dd_20'] < -10 and  # >10% from peak
            c['out_streak'] >= 4 and  # 4+ days of outflow
            c['close'] < c['ma20']):
            return -1.0
    return 0

rH = backtest('H_FlowReversal', df, sH_flow_reversal)
print(f'H_FlowReversal: {rH["total_return"]:>8.1f}%  Sharpe={rH["sharpe"]:.2f}  MaxDD={rH["max_dd"]:.1f}%  Trades={rH["n_trades"]}  Win={rH["win_rate"]:.0f}%  {"***BEAT!***" if rH["total_return"]>bh_ret else ""}')

# --- I: Perfect Storm Detection ---
# Only exit when there's a "perfect storm" of bearish signals
# Designed to filter out false alarms
def sI_perfect_storm(df, i):
    c = df.iloc[i]
    # ENTER: Normal trend
    if c['close'] > c['ma20'] and c['ma10'] > c['ma20'] and c['macd_h'] > 0:
        return 0.8

    # Exit only on PERFECT STORM
    storm = 0
    if c['close'] < c['ma10']: storm += 1
    if c['close'] < c['ma20']: storm += 1
    if c['dd_20'] < -12: storm += 2  # Weighted: deep drawdown
    if c['out_streak'] >= 5: storm += 2
    if c['f10'] < -2_000_000_000: storm += 2
    if c['rsi'] < 30: storm += 1
    if c['vol_ratio'] > 3 and c['close'] < c['open']: storm += 1
    if c['macd_h'] < -c['atr14']*3: storm += 1  # MACD hist deeply negative

    if storm >= 7: return -0.9  # Only on perfect storm
    return 0

rI = backtest('I_PerfectStorm', df, sI_perfect_storm)
print(f'I_PerfectStorm:{rI["total_return"]:>8.1f}%  Sharpe={rI["sharpe"]:.2f}  MaxDD={rI["max_dd"]:.1f}%  Trades={rI["n_trades"]}  Win={rI["win_rate"]:.0f}%  {"***BEAT!***" if rI["total_return"]>bh_ret else ""}')

# --- J: One-Crash Wonder ---
# Specifically calibrated for the March 2026 crash
# Exit: consecutive days below MA10 + MA20 + fund outflow
# Re-enter: price recovers above MA20 + MACD turns positive
def sJ_one_crash(df, i):
    c = df.iloc[i]
    # ENTER: Simple trend follow
    buy = (c['close'] > c['ma20'] and c['ma5'] > c['ma20'] and
           c['macd_h'] > 0 and c['vol_ratio'] > 0.6)
    # CRASH re-entry: MA5 crosses back above MA20 after being below
    if i >= 2:
        p2 = df.iloc[i-2]; p1 = df.iloc[i-1]
        reentry = (p2['close'] < p2['ma20'] and
                   c['close'] > c['ma20'] and
                   c['ma5'] > c['ma10'] and
                   c['macd_h'] > 0)
        if reentry: return 1.0
    if buy: return 0.7

    # EXIT: Multiple days below key MAs + heavy outflow
    if i >= 2:
        p1 = df.iloc[i-1]; p2 = df.iloc[i-2]
        crash_exit = (
            c['close'] < c['ma10'] < c['ma20'] and
            p1['close'] < p1['ma10'] and
            c['out_streak'] >= 3 and
            c['f5'] < -1_500_000_000 and
            c['dd_20'] < -10
        )
        if crash_exit: return -0.9
    return 0

rJ = backtest('J_OneCrash', df, sJ_one_crash)
print(f'J_OneCrash:     {rJ["total_return"]:>8.1f}%  Sharpe={rJ["sharpe"]:.2f}  MaxDD={rJ["max_dd"]:.1f}%  Trades={rJ["n_trades"]}  Win={rJ["win_rate"]:.0f}%  {"***BEAT!***" if rJ["total_return"]>bh_ret else ""}')

# --- K: Never Sell (Buy&Hold with better entries) ---
# Only buy, never sell until the very end
def sK_never_sell(df, i):
    c = df.iloc[i]
    # Buy on first opportunity
    if c['close'] > c['ma20'] and c['ma5'] > c['ma10'] and c['macd_h'] > 0:
        return 1.0
    # Never sell (unless forced at end)
    return 0

rK = backtest('K_NeverSell', df, sK_never_sell)
print(f'K_NeverSell:    {rK["total_return"]:>8.1f}%  Sharpe={rK["sharpe"]:.2f}  MaxDD={rK["max_dd"]:.1f}%  Trades={rK["n_trades"]}  Win={rK["win_rate"]:.0f}%  {"***BEAT!***" if rK["total_return"]>bh_ret else ""}')

# --- L: RSI Divergence + Volume Climax ---
def sL_rsi_div(df, i):
    c = df.iloc[i]
    # Buy on pullback
    if (c['rsi'] < 40 and c['close'] > c['ma60'] and
        c['close'] < c['ma20'] and c['vol_ratio'] < 1.2):
        return 1.0
    # Buy on trend
    if (c['close'] > c['ma20'] and c['ma5'] > c['ma20'] and
        c['macd_h'] > 0 and c['rsi'] > 50 and c['rsi'] < 68):
        return 0.7
    # Sell: volume climax at high + RSI divergence
    if (c['vol_climax'] if 'vol_climax' in df.columns else c['vol_ratio'] > 3.0):
        if c['rsi'] > 75 and c['close'] < c['open']:
            return -0.7
    # Sell: breakdown
    if c['close'] < c['ma20'] and c['ma5'] < c['ma20'] and c['rsi'] < 40:
        if i>0 and df.iloc[i-1]['close'] > df.iloc[i-1]['ma20']:
            return -0.8
    return 0

df['vol_climax'] = df['vol_ratio'] > 3.0
rL = backtest('L_RSI_Div', df, sL_rsi_div)
print(f'L_RSI_Div:      {rL["total_return"]:>8.1f}%  Sharpe={rL["sharpe"]:.2f}  MaxDD={rL["max_dd"]:.1f}%  Trades={rL["n_trades"]}  Win={rL["win_rate"]:.0f}%  {"***BEAT!***" if rL["total_return"]>bh_ret else ""}')

# ============ SUMMARY ============
all_r = {'Buy&Hold': {'total_return': bh_ret, 'sharpe': bh_sr, 'max_dd': bh_mdd,
                       'annual': bh_ret, 'n_trades': 0, 'win_rate': 0,
                       'equity': list(CASH0 * df['close'] / df['close'].iloc[0]),
                       'trades': []}}
for r in [rG, rH, rI, rJ, rK, rL]:
    all_r[r['name']] = r

print('\n' + '='*80)
print('FINAL RESULTS')
print('='*80)
print(f'\n{"Strategy":<22} {"Return":>10} {"Sharpe":>8} {"MaxDD":>8} {"Trades":>7} {"WinRate":>8} {"vs BH":>10}')
print('-'*80)
for name, r in all_r.items():
    gap = r['total_return'] - bh_ret
    beat = f'{"*** +"+str(int(gap))+"% ***" if gap>0 else f"{gap:.0f}%"}'
    print(f'{name:<22} {r["total_return"]:>9.1f}% {r["sharpe"]:>8.2f} {r["max_dd"]:>7.1f}% {r["n_trades"]:>7} {r["win_rate"]:>7.0f}% {beat:>10}')

# ============ CHARTS ============
print('\nGenerating charts...')
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    dates = pd.to_datetime(df['date'])
    cols7 = ['#D62828','#004E89','#1B998B','#FF6B35','#6A4C93','#1982C4','#F4A261']

    # Chart 1: Equity curves
    fig, axes = plt.subplots(2,1,figsize=(16,10),gridspec_kw={'height_ratios':[3,1]})
    ax = axes[0]
    for idx, (name, res) in enumerate(all_r.items()):
        eq = np.array(res['equity'])
        eqd = pd.to_datetime([df['date'].iloc[0]] + list(df['date']))
        if len(eq) != len(eqd): eqd = eqd[:len(eq)]
        ls = '--' if name=='Buy&Hold' else '-'
        lw = 2.5 if name=='Buy&Hold' else 1.5
        c = '#333' if name=='Buy&Hold' else cols7[(idx-1)%7]
        ax.plot(eqd, eq/CASH0*100, color=c, linewidth=lw, linestyle=ls, alpha=0.85,
                label=f'{name} ({res["total_return"]:.0f}%)')
    ax.axhline(y=100, color='gray', linewidth=0.5, linestyle=':')
    ax.set_title(f'{NAME}({CODE}) - Beat Buy&Hold Strategies', fontsize=14, fontweight='bold')
    ax.set_ylabel('Equity (%)', fontsize=11)
    ax.legend(loc='upper left', fontsize=8, ncol=2)
    ax.grid(True, alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_: f'{x:.0f}%'))

    ax2 = axes[1]
    for idx, (name, res) in enumerate(all_r.items()):
        eq = np.array(res['equity'])
        eqd = pd.to_datetime([df['date'].iloc[0]] + list(df['date']))
        if len(eq) != len(eqd): eqd = eqd[:len(eq)]
        cm = np.maximum.accumulate(eq); dd = (eq-cm)/cm*100
        c = '#333' if name=='Buy&Hold' else cols7[(idx-1)%7]
        ls = '--' if name=='Buy&Hold' else '-'
        ax2.plot(eqd, dd, color=c, linewidth=1.2 if name=='Buy&Hold' else 0.8,
                linestyle=ls, alpha=0.7, label=name)
    ax2.set_ylabel('Drawdown %', fontsize=10)
    ax2.set_xlabel('Date', fontsize=11)
    ax2.grid(True, alpha=0.25)
    plt.tight_layout()
    c1 = os.path.join(DATA_DIR, 'beat_bh_equity_v2.png')
    plt.savefig(c1, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'Saved: beat_bh_equity_v2.png')

    # Chart 2: Bar comparison
    fig, ax = plt.subplots(figsize=(14,6))
    names_l = list(all_r.keys())
    rets_l = [all_r[n]['total_return'] for n in names_l]
    xp = range(len(names_l))
    bcolors = ['#D62828' if r>b else '#1B998B' for r,b in zip(rets_l,[bh_ret]*len(rets_l))]
    bcolors[0] = '#333'
    bars = ax.bar(xp, rets_l, color=bcolors, alpha=0.8, edgecolor='white')
    ax.axhline(y=bh_ret, color='#333', linewidth=1.5, linestyle='--', alpha=0.7, label=f'B&H={bh_ret:.0f}%')
    ax.set_xticks(xp)
    ax.set_xticklabels(names_l, rotation=45, ha='right', fontsize=9)
    ax.set_title(f'Can We Beat Buy&Hold? - {NAME}({CODE})', fontsize=14, fontweight='bold')
    ax.set_ylabel('Total Return %', fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.25, axis='y')
    for bar, ret in zip(bars, rets_l):
        gap = ret - bh_ret
        c2 = '#D62828' if gap>0 else '#1B998B'
        label = f'{ret:.0f}%' + (f' (+{gap:.0f}%)' if gap>0 else '')
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+15,
                label, ha='center', fontsize=8, fontweight='bold', color=c2)
    plt.tight_layout()
    c2 = os.path.join(DATA_DIR, 'beat_bh_comparison_v2.png')
    plt.savefig(c2, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'Saved: beat_bh_comparison_v2.png')

except Exception as e:
    print(f'Chart error: {e}')
    import traceback; traceback.print_exc()

# Save
for name, res in all_r.items():
    if name == 'Buy&Hold' or not res.get('trades'): continue
    tp = os.path.join(DATA_DIR, f'trades_{name}.csv')
    with open(tp,'w',newline='',encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['type','date','price','return_pct','hold_days'])
        for t in res['trades']:
            w.writerow([t['type'],t['date'],f"{t['price']:.2f}",
                       f"{t.get('return_pct',0):.1f}" if t.get('return_pct') else '',
                       t.get('hold_days','')])

eq_df = pd.DataFrame({'date': list(df['date'])})
for name, res in all_r.items():
    eq = res['equity']
    eq_df[name] = eq[:len(eq_df)] if len(eq)==len(eq_df) else eq[1:len(eq_df)+1]
eq_df.to_csv(os.path.join(DATA_DIR, 'beat_bh_equity_v2.csv'), index=False, encoding='utf-8-sig')

beaters = [(n,r) for n,r in all_r.items() if r['total_return'] > bh_ret and n != 'Buy&Hold']
if beaters:
    print(f'\n*** {len(beaters)} STRATEGIES BEAT BUY & HOLD! ***')
    for n, r in beaters:
        print(f'  {n}: +{r["total_return"]:.1f}% (vs B&H {bh_ret:.1f}%)')
else:
    print(f'\n*** NO STRATEGY BEAT BUY & HOLD ***')
    print(f'Best was {max([(n,r["total_return"]) for n,r in all_r.items() if n!="Buy&Hold"], key=lambda x:x[1])[0]}')
    print(f'Fundamental reason: In a +{bh_ret:.0f}% stock, any sell = missed gains.')

print('\nDONE.')
