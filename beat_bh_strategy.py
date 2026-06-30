"""
长飞光纤(601869) - 跑赢Buy&Hold的策略设计
核心思路: 在+1300%的超级牛股中，关键是:
  1. 在趋势中充分参与
  2. 躲过大跌(-39.6%的3月暴跌)
  3. 在底部重新入场
  4. 利用资金流数据做领先指标
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

# ============ LOAD DATA ============
print('Loading data...')
# Re-fetch fresh data with fund flow for full 1-year range
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
    if wait > 0: time.sleep(wait + random.uniform(0.2, 0.6))
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
print(f'  K-line: {len(df)} bars, Fund flow: {len(df_f)} days')

# ============ FEATURES ============
df['returns'] = df['close'].pct_change()
for w in [3,5,10,20,60]:
    df[f'ma{w}'] = df['close'].rolling(w, min_periods=w//2).mean()
df['atr14'] = (df['high'] - df['low']).rolling(14, min_periods=5).mean()
df['atr_pct'] = df['atr14'] / df['close'] * 100
delta = df['close'].diff()
gain = delta.where(delta>0,0).rolling(14,min_periods=7).mean()
loss = (-delta.where(delta<0,0)).rolling(14,min_periods=7).mean()
rs = gain / loss.replace(0, np.nan)
df['rsi'] = 100 - 100/(1+rs)
df['ema12'] = df['close'].ewm(span=12,adjust=False,min_periods=6).mean()
df['ema26'] = df['close'].ewm(span=26,adjust=False,min_periods=13).mean()
df['macd'] = df['ema12'] - df['ema26']
df['macd_sig'] = df['macd'].ewm(span=9,adjust=False,min_periods=5).mean()
df['macd_h'] = df['macd'] - df['macd_sig']
df['vol_ma5'] = df['volume'].rolling(5,min_periods=2).mean()
df['vol_ma20'] = df['volume'].rolling(20,min_periods=5).mean()
df['vol_ratio'] = df['volume'] / df['vol_ma20'].replace(0,np.nan)
df['bb_mid'] = df['close'].rolling(20,min_periods=10).mean()
df['bb_std'] = df['close'].rolling(20,min_periods=10).std()
df['bb_lower'] = df['bb_mid'] - 2*df['bb_std']
df['bb_upper'] = df['bb_mid'] + 2*df['bb_std']

# Merge fund flow
df = df.merge(df_f[['date','main_net','super_net','large_net']], on='date', how='left')
for col in ['main_net','super_net','large_net']:
    df[col] = df[col].fillna(0)
df['flow_ma3'] = df['main_net'].rolling(3,min_periods=1).mean()
df['flow_ma5'] = df['main_net'].rolling(5,min_periods=2).mean()
df['flow_ma10'] = df['main_net'].rolling(10,min_periods=5).mean()
df['flow_cum3'] = df['main_net'].rolling(3,min_periods=1).sum()
df['flow_cum5'] = df['main_net'].rolling(5,min_periods=2).sum()

# Consecutive outflow days
df['outflow_streak'] = 0
streak = 0
for i in range(len(df)):
    if df['main_net'].iloc[i] < 0:
        streak += 1
    else:
        streak = 0
    df.loc[df.index[i], 'outflow_streak'] = streak

# Volume climax detection
df['vol_climax'] = df['vol_ratio'] > 3.0
df['vol_spike'] = df['vol_ratio'] > 2.0

# Clean
core_cols = ['close','ma5','ma10','ma20','atr14','rsi','macd_h','vol_ratio','flow_ma3','flow_cum3','outflow_streak']
df = df.dropna(subset=[c for c in core_cols if c in df.columns]).reset_index(drop=True)
print(f'  Clean data: {len(df)} bars, {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')

# ============ BACKTEST ENGINE ============
def backtest(name, df, signal_func, max_position=1.0, use_pyramid=False):
    """signal_func returns float: positive=buy signal strength, negative=sell urgency"""
    cash = CASH0
    shares = 0
    equity = [CASH0]
    trades = []
    in_pos = False
    entry_price = 0
    entry_i = 0

    for i in range(len(df)):
        row = df.iloc[i]
        price = row['close']
        sig = signal_func(df, i)
        if sig is None: sig = 0

        if not in_pos and sig > 0.3 and cash > price * 100:
            # Enter
            alloc = min(sig, max_position)
            trade_cash = cash * alloc * (1 - COMM)
            shares = trade_cash / price
            cash -= trade_cash
            in_pos = True
            entry_price = price
            entry_i = i
            trades.append({'type':'BUY','date':row['date'],'price':price,'alloc':alloc})
        elif in_pos and sig < -0.3:
            # Exit
            proceeds = shares * price * (1 - COMM)
            cash += proceeds
            ret = (price/entry_price - 1)*100
            trades.append({'type':'SELL','date':row['date'],'price':price,
                          'return_pct':ret,'hold_days':i-entry_i})
            shares = 0
            in_pos = False
        elif in_pos and use_pyramid and sig > 0.7:
            # Pyramid: add to position on strong signals
            add_cash = cash * 0.3 * (1 - COMM)
            if add_cash > price * 100:
                add_shares = add_cash / price
                shares += add_shares
                cash -= add_cash
                trades.append({'type':'ADD','date':row['date'],'price':price,'shares':add_shares})

        equity.append(cash + shares * price)

    if in_pos:
        last_p = df.iloc[-1]['close']
        cash += shares * last_p * (1 - COMM)
        trades.append({'type':'SELL(F)','date':df.iloc[-1]['date'],'price':last_p,
                      'return_pct':(last_p/entry_price-1)*100,'hold_days':len(df)-1-entry_i})
        equity[-1] = cash

    eq_s = pd.Series(equity)
    ret_s = eq_s.pct_change().dropna()
    tr = (equity[-1]/CASH0 - 1)*100
    nt = len([t for t in trades if 'SELL' in t['type']])
    wt = len([t for t in trades if t.get('return_pct',0)>0])
    wr = (wt/nt*100) if nt>0 else 0
    yrs = len(df)/252
    ar = ((1+tr/100)**(1/yrs)-1)*100 if yrs>0 else 0
    sr = (ret_s.mean()/ret_s.std()*np.sqrt(252)) if ret_s.std()>0 else 0
    cm = eq_s.cummax()
    mdd = ((eq_s-cm)/cm*100).min()

    return {'name':name,'equity':equity,'trades':trades,'total_return':tr,
            'annual':ar,'sharpe':sr,'max_dd':mdd,'n_trades':nt,'win_rate':wr}

# ============ BH BENCHMARK ============
bh_ret = (df['close'].iloc[-1]/df['close'].iloc[0]-1)*100
bh_s = df['close'].pct_change().dropna()
bh_sr = (bh_s.mean()/bh_s.std()*np.sqrt(252)) if bh_s.std()>0 else 0
bh_mdd = ((df['close']/df['close'].cummax()-1)*100).min()
print(f'\nBuy&Hold: Ret={bh_ret:.1f}%, Sharpe={bh_sr:.2f}, MaxDD={bh_mdd:.1f}%')
print(f'Target to beat: >{bh_ret:.1f}%')

# ============ STRATEGY A: Adaptive Fund Flow Escape ============
# Core idea: Trend follow normally, but use fund flow to ESCAPE before big crashes
# Then use fund flow reversal to RE-ENTER at the bottom
print('\n' + '='*60)
print('STRATEGY A: Adaptive Fund Flow Escape')
print('='*60)

def sA_flow_escape(df, i):
    """Use fund flow as crash early-warning system"""
    c = df.iloc[i]
    # STRONG BUY (1.0): Fund flow turning positive after being negative
    if (c['flow_cum3'] > 500_000_000 and  # 3-day inflow > 5亿
        c['flow_ma3'] > c['flow_ma5'] and  # Flow accelerating
        c['close'] > c['ma20'] and
        c['rsi'] < 65):  # Not overbought
        return 1.0

    # BUY (0.6): Normal trend entry
    if (c['close'] > c['ma20'] and c['ma5'] > c['ma10'] and
        c['macd_h'] > 0 and c['vol_ratio'] > 0.8):
        return 0.6

    # CRASH WARNING (-0.5): Fund flow massive outflow
    if (c['outflow_streak'] >= 3 and
        c['flow_cum3'] < -800_000_000 and  # 3-day outflow > 8亿
        c['close'] < c['ma10']):
        return -0.8

    # SELL (-0.5): Normal trend breakdown
    if i > 0:
        p = df.iloc[i-1]
        if p['ma5'] >= p['ma10'] and c['ma5'] < c['ma10']:
            return -0.5

    return 0

rA = backtest('A_FlowEscape', df, sA_flow_escape)
print(f"  Ret={rA['total_return']:.1f}%  Sharpe={rA['sharpe']:.2f}  MaxDD={rA['max_dd']:.1f}%  Trades={rA['n_trades']}  Win={rA['win_rate']:.0f}%")

# ============ STRATEGY B: Mean Reversion within Trend ============
# Buy dips in a bull trend, sell rips
print('\nSTRATEGY B: Mean Reversion within Trend')
def sB_mean_rev(df, i):
    c = df.iloc[i]
    in_bull = c['close'] > c['ma60']

    if in_bull:
        # BUY the dip: RSI oversold + near MA20 support
        if (c['rsi'] < 35 and
            c['close'] < c['ma20'] * 1.03 and
            c['close'] > c['ma60'] and
            c['vol_ratio'] > 0.7):
            return 1.0

        # SELL the rip: RSI overbought + far from MA20
        if (c['rsi'] > 75 and
            c['close'] > c['ma20'] * 1.15 and
            c['vol_ratio'] > 1.3):
            return -0.8

        # SELL: breakdown below MA60 (bull market over)
        if c['close'] < c['ma60'] * 0.97:
            return -0.7

    # Not in bull market - stay out (or go short if possible)
    return 0

rB = backtest('B_MeanRev', df, sB_mean_rev)
print(f"  Ret={rB['total_return']:.1f}%  Sharpe={rB['sharpe']:.2f}  MaxDD={rB['max_dd']:.1f}%  Trades={rB['n_trades']}  Win={rB['win_rate']:.0f}%")

# ============ STRATEGY C: Crash Dodger ============
# Specifically designed to dodge the March 2026 -39.6% crash
print('\nSTRATEGY C: Crash Dodger (Volatility + Flow)')
def sC_crash_dodge(df, i):
    c = df.iloc[i]
    # ENTRY conditions
    if (c['close'] > c['ma20'] and c['ma5'] > c['ma20'] and
        c['macd_h'] > 0 and c['rsi'] < 70):
        return 0.8

    # CRASH EXIT: Multiple red flags
    crash_signals = 0
    if c['outflow_streak'] >= 3: crash_signals += 1
    if c['close'] < c['ma10']: crash_signals += 1
    if c['close'] < c['ma20']: crash_signals += 1
    if c['vol_spike'] and c['close'] < c['open']: crash_signals += 1
    if c['rsi'] < 40: crash_signals += 1

    if crash_signals >= 3:
        return -0.9  # Strong sell

    # Normal exit
    if i > 0:
        p = df.iloc[i-1]
        if p['ma5'] >= p['ma20'] and c['ma5'] < c['ma20']:
            return -0.5

    return 0

rC = backtest('C_CrashDodge', df, sC_crash_dodge)
print(f"  Ret={rC['total_return']:.1f}%  Sharpe={rC['sharpe']:.2f}  MaxDD={rC['max_dd']:.1f}%  Trades={rC['n_trades']}  Win={rC['win_rate']:.0f}%")

# ============ STRATEGY D: Smart Money Tracker ============
# Track cumulative fund flow divergence
print('\nSTRATEGY D: Smart Money Tracker')
df['flow_cumsum'] = df['main_net'].cumsum()
df['flow_cumsum_ma20'] = df['flow_cumsum'].rolling(20, min_periods=10).mean()

def sD_smart_money(df, i):
    c = df.iloc[i]
    # ENTRY: Smart money accumulating (cumulative flow rising)
    if (c['close'] > c['ma20'] and
        c['flow_ma5'] > c['flow_ma10'] and  # Flow accelerating
        c['macd_h'] > 0 and
        c['rsi'] > 40 and c['rsi'] < 70 and
        c['vol_ratio'] > 0.6):
        return 0.7

    # STRONG ENTRY: Flow turning positive from deep negative
    if i >= 3:
        prev_flows = df['flow_cum3'].iloc[i-1]
        if (c['flow_cum3'] > 0 and prev_flows < -500_000_000 and
            c['close'] > c['ma10']):
            return 1.0  # Aggressive entry on flow reversal

    # EXIT: Smart money distributing heavily
    if (c['outflow_streak'] >= 4 and
        c['flow_cum5'] < -1_000_000_000):
        return -0.9

    # EXIT: Price + flow both weakening
    if (c['close'] < c['ma10'] and
        c['flow_ma3'] < c['flow_ma10'] and
        c['flow_ma3'] < 0):
        return -0.6

    return 0

rD = backtest('D_SmartMoney', df, sD_smart_money)
print(f"  Ret={rD['total_return']:.1f}%  Sharpe={rD['sharpe']:.2f}  MaxDD={rD['max_dd']:.1f}%  Trades={rD['n_trades']}  Win={rD['win_rate']:.0f}%")

# ============ STRATEGY E: Volatility Adaptive MA ============
# Use dynamic MA period based on volatility
print('\nSTRATEGY E: Volatility Adaptive MA')
def sE_vol_adaptive(df, i):
    c = df.iloc[i]
    # In high vol regime, use shorter MA; in low vol, use longer MA
    is_high_vol = c['atr_pct'] > 4.0  # Daily ATR > 4% = high vol

    if is_high_vol:
        # High vol: faster signals, tighter stops
        buy_sig = c['close'] > c['ma10'] and c['ma3'] > c['ma10'] and c['macd_h'] > 0
        sell_sig = c['close'] < c['ma5'] * 0.98  # 2% below MA5
    else:
        # Low vol: ride the trend
        buy_sig = c['close'] > c['ma20'] and c['ma5'] > c['ma20'] and c['macd_h'] > 0
        sell_sig = c['close'] < c['ma20'] * 0.98

    if buy_sig and c['rsi'] < 75: return 0.7
    if sell_sig: return -0.7
    return 0

rE = backtest('E_VolAdapt', df, sE_vol_adaptive)
print(f"  Ret={rE['total_return']:.1f}%  Sharpe={rE['sharpe']:.2f}  MaxDD={rE['max_dd']:.1f}%  Trades={rE['n_trades']}  Win={rE['win_rate']:.0f}%")

# ============ STRATEGY F: Triple Confirmation with Scaling ============
# Enter on 3 confirmations, scale in, scale out
print('\nSTRATEGY F: Triple Confirm + Scale')
def sF_triple(df, i):
    c = df.iloc[i]
    # Count bullish confirmations
    bulls = 0
    if c['close'] > c['ma20']: bulls += 1
    if c['ma5'] > c['ma10']: bulls += 1
    if c['macd_h'] > 0: bulls += 1
    if c['rsi'] > 50: bulls += 1
    if c['vol_ratio'] > 1.0: bulls += 1
    if c['flow_ma3'] > 0: bulls += 1

    bears = 0
    if c['close'] < c['ma10']: bears += 1
    if c['close'] < c['ma20']: bears += 1
    if c['macd_h'] < 0: bears += 1
    if c['rsi'] < 40: bears += 1
    if c['outflow_streak'] >= 2: bears += 1
    if c['vol_spike'] and c['close'] < c['open']: bears += 1

    if bulls >= 5: return 0.8  # Strong buy
    if bulls >= 4: return 0.5  # Moderate buy
    if bears >= 4: return -0.8  # Strong sell
    if bears >= 3: return -0.5  # Moderate sell
    return 0

rF = backtest('F_TripleConf', df, sF_triple)
print(f"  Ret={rF['total_return']:.1f}%  Sharpe={rF['sharpe']:.2f}  MaxDD={rF['max_dd']:.1f}%  Trades={rF['n_trades']}  Win={rF['win_rate']:.0f}%")

# ============ RESULTS SUMMARY ============
print('\n' + '='*70)
print('RESULTS: Beat Buy&Hold Strategies')
print('='*70)
print(f'\nBuy&Hold: {bh_ret:.1f}%  Sharpe={bh_sr:.2f}  MaxDD={bh_mdd:.1f}%')
print(f'\n{"Strategy":<25} {"Return":>10} {"Annual":>10} {"Sharpe":>8} {"MaxDD":>8} {"Trades":>7} {"WinRate":>8} {"BEAT?"}')
print('-'*90)

all_results = {'Buy&Hold': {'total_return': bh_ret, 'sharpe': bh_sr, 'max_dd': bh_mdd, 'annual': bh_ret, 'n_trades': 0, 'win_rate': 0}}
for r in [rA, rB, rC, rD, rE, rF]:
    all_results[r['name']] = r
    beat = 'YES!' if r['total_return'] > bh_ret else f'({bh_ret-r["total_return"]:.0f}% gap)'
    print(f'{r["name"]:<25} {r["total_return"]:>9.1f}% {r["annual"]:>9.1f}% {r["sharpe"]:>8.2f} {r["max_dd"]:>7.1f}% {r["n_trades"]:>7} {r["win_rate"]:>7.0f}% {beat:>10}')

# Find best
best_name = max([(n,r) for n,r in all_results.items() if n != 'Buy&Hold'], key=lambda x: x[1]['total_return'])[0]
print(f'\n>>> BEST: {best_name} (Return: {all_results[best_name]["total_return"]:.1f}%)')

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
    colors = ['#D62828','#004E89','#1B998B','#FF6B35','#6A4C93','#1982C4']

    # Chart 1: All strategies equity curves
    fig, axes = plt.subplots(2,1,figsize=(16,10),gridspec_kw={'height_ratios':[3,1]})
    ax = axes[0]
    for idx, (name, res) in enumerate(all_results.items()):
        eq = np.array(res['equity'])
        eqd = pd.to_datetime([df['date'].iloc[0]] + list(df['date']))
        if len(eq) == len(eqd):
            ls = '--' if name == 'Buy&Hold' else '-'
            lw = 2.5 if name == 'Buy&Hold' else 1.5
            ax.plot(eqd, eq/CASH0*100, color=colors[idx%6] if name!='Buy&Hold' else '#333',
                    linewidth=lw, linestyle=ls, alpha=0.85,
                    label=f'{name} ({res["total_return"]:.0f}%)')
    ax.axhline(y=100, color='gray', linewidth=0.5, linestyle=':')
    ax.set_title(f'{NAME}({CODE}) - Strategies to Beat Buy&Hold ({bh_ret:.0f}%)', fontsize=14, fontweight='bold')
    ax.set_ylabel('Equity (% of Initial)', fontsize=11)
    ax.legend(loc='upper left', fontsize=8, ncol=2)
    ax.grid(True, alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_: f'{x:.0f}%'))

    ax2 = axes[1]
    for idx, (name, res) in enumerate(all_results.items()):
        eq = np.array(res['equity'])
        eqd = pd.to_datetime([df['date'].iloc[0]] + list(df['date']))
        if len(eq) == len(eqd):
            cm = np.maximum.accumulate(eq)
            dd = (eq-cm)/cm*100
            ls = '--' if name=='Buy&Hold' else '-'
            ax2.plot(eqd, dd, color=colors[idx%6] if name!='Buy&Hold' else '#333',
                    linewidth=1.2 if name=='Buy&Hold' else 0.8, linestyle=ls, alpha=0.7, label=name)
    ax2.set_ylabel('Drawdown %', fontsize=10)
    ax2.set_xlabel('Date', fontsize=11)
    ax2.grid(True, alpha=0.25)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda x,_: f'{x:.0f}%'))
    plt.tight_layout()
    c1 = os.path.join(DATA_DIR, 'beat_bh_equity.png')
    plt.savefig(c1, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: beat_bh_equity.png')

    # Chart 2: Best strategy detail with trade annotations
    best = all_results[best_name]
    best_eq = np.array(best['equity'])
    best_dates = pd.to_datetime([df['date'].iloc[0]] + list(df['date']))
    assert len(best_eq) == len(best_dates) or len(best_eq) == len(best_dates)+1, f"Length mismatch: {len(best_eq)} vs {len(best_dates)}"

    fig, axes = plt.subplots(4,1,figsize=(16,14),gridspec_kw={'height_ratios':[2.5,1.5,1.5,1.5]})
    ax = axes[0]
    # Price with MA
    ax.plot(dates, df['close'], color='#333', linewidth=1, alpha=0.7, label='Close')
    ax.plot(dates, df['ma20'], color='#004E89', linewidth=0.8, alpha=0.5, label='MA20')
    ax.plot(dates, df['ma60'], color='#1B998B', linewidth=0.8, alpha=0.4, label='MA60')
    # Trade markers
    for t in best['trades']:
        idx = df[df['date']==t['date']].index
        if len(idx)==0: continue
        i = idx[0]
        if 'BUY' in t['type']:
            ax.scatter(dates.iloc[i], t['price'], color='#D62828', marker='^', s=80, zorder=5)
        elif 'ADD' in t['type']:
            ax.scatter(dates.iloc[i], t['price'], color='#FF6B35', marker='+', s=60, zorder=5)
        else:
            ax.scatter(dates.iloc[i], t['price'], color='#1B998B', marker='v', s=80, zorder=5)
            ret = t.get('return_pct',0)
            c = '#D62828' if ret>0 else '#1B998B'
            ax.annotate(f'{ret:+.1f}%', (dates.iloc[i], t['price']),
                       textcoords='offset points', xytext=(8,8), fontsize=7, color=c, fontweight='bold')
    ax.set_title(f'Best: {best_name}  |  Return={best["total_return"]:.0f}% vs B&H={bh_ret:.0f}%', fontsize=13, fontweight='bold')
    ax.set_ylabel('Price', fontsize=10)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.25)

    ax2 = axes[1]
    ax2.plot(best_dates, best_eq/CASH0*100, color='#004E89', linewidth=1.5)
    bh_eq = CASH0 * df['close']/df['close'].iloc[0]
    ax2.plot(dates, bh_eq/CASH0*100, '--', color='#333', linewidth=1, alpha=0.5, label=f'B&H ({bh_ret:.0f}%)')
    ax2.fill_between(best_dates, best_eq/CASH0*100, 100, alpha=0.12, color='#004E89')
    ax2.axhline(y=100, color='gray', linewidth=0.5, linestyle=':')
    ax2.set_ylabel('Equity %', fontsize=10)
    ax2.legend(loc='upper left', fontsize=9)
    ax2.grid(True, alpha=0.25)

    ax3 = axes[2]
    cm = np.maximum.accumulate(best_eq)
    dd = (best_eq-cm)/cm*100
    ax3.fill_between(best_dates, dd, 0, color='#D62828', alpha=0.4)
    ax3.set_ylabel('Drawdown %', fontsize=10)
    ax3.grid(True, alpha=0.25)

    ax4 = axes[3]
    # Show outflow streaks as red bars
    streaks = df['outflow_streak'].values
    ax4.bar(dates, streaks, color='#D62828', alpha=0.4, width=1, label='Outflow Streak')
    ax4_twin = ax4.twinx()
    ax4_twin.plot(dates, df['flow_cum3']/1e8, color='#004E89', linewidth=0.8, alpha=0.7, label='3-Day Flow')
    ax4_twin.axhline(y=0, color='#333', linewidth=0.5, alpha=0.4)
    ax4.set_ylabel('Outflow Days', fontsize=10)
    ax4_twin.set_ylabel('3-Day Flow (Yi)', fontsize=10)
    ax4.set_xlabel('Date', fontsize=11)
    ax4.grid(True, alpha=0.25)
    plt.tight_layout()
    c2 = os.path.join(DATA_DIR, 'beat_bh_best_detail.png')
    plt.savefig(c2, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: beat_bh_best_detail.png')

    # Chart 3: Bar comparison
    fig, ax = plt.subplots(figsize=(14,6))
    names_l = list(all_results.keys())
    rets_l = [all_results[n]['total_return'] for n in names_l]
    xp = range(len(names_l))
    bcolors = ['#D62828' if r > bh_ret else '#1B998B' for r in rets_l]
    bcolors[0] = '#333'  # B&H in black
    bars = ax.bar(xp, rets_l, color=bcolors, alpha=0.8, edgecolor='white')
    ax.axhline(y=bh_ret, color='#333', linewidth=1.5, linestyle='--', alpha=0.7, label=f'B&H Target: {bh_ret:.0f}%')
    ax.set_xticks(xp)
    ax.set_xticklabels(names_l, rotation=45, ha='right', fontsize=9)
    ax.set_title(f'Beat Buy&Hold Challenge - {NAME}({CODE})', fontsize=14, fontweight='bold')
    ax.set_ylabel('Total Return %', fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.25, axis='y')
    for bar, ret in zip(bars, rets_l):
        color = '#D62828' if ret > bh_ret else '#1B998B'
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+15,
                f'{ret:.0f}%', ha='center', fontsize=9, fontweight='bold', color=color)
    plt.tight_layout()
    c3 = os.path.join(DATA_DIR, 'beat_bh_comparison.png')
    plt.savefig(c3, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: beat_bh_comparison.png')
    CHARTS_OK = True
except Exception as e:
    print(f'Chart error: {e}')
    import traceback; traceback.print_exc()
    CHARTS_OK = False

# ============ SAVE TRADES ============
for name, res in all_results.items():
    if name == 'Buy&Hold' or 'trades' not in res: continue
    tp = os.path.join(DATA_DIR, f'trades_{name}.csv')
    with open(tp,'w',newline='',encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['type','date','price','return_pct','hold_days'])
        for t in res['trades']:
            w.writerow([t['type'],t['date'],f"{t['price']:.2f}",
                       f"{t.get('return_pct',0):.1f}" if t.get('return_pct') else '',
                       t.get('hold_days','')])

# Save equity curves
eq_df = pd.DataFrame({'date': list(df['date'])})
for name, res in all_results.items():
    eq = res['equity']
    eq_df[name] = eq[1:] if len(eq)==len(eq_df)+1 else eq[:len(eq_df)]
eq_df.to_csv(os.path.join(DATA_DIR, 'beat_bh_equity_curves.csv'), index=False, encoding='utf-8-sig')

print('\nDONE.')
