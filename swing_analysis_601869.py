"""
长飞光纤(601869) T+0/波段交易 买点卖点精准分析
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
tc = f'sh{CODE}'
url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
r = requests.get(url, params={'param': f'{tc},day,,,260,qfq'},
                 headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}, timeout=15)
d = r.json()
raw = d.get('data',{}).get(tc,{}).get('qfqday',[]) or d.get('data',{}).get(tc,{}).get('day',[])
klines = [{'date':k[0],'open':float(k[1]),'close':float(k[2]),
           'high':float(k[3]),'low':float(k[4]),'volume':float(k[5])} for k in raw]
df = pd.DataFrame(klines)

# Fund flow
EM_SESSION = requests.Session(); EM_SESSION.headers.update({'User-Agent':UA})
_eml=[0.0]
def em_get(url, params=None, headers=None, timeout=15):
    wait=1.5-(time.time()-_eml[0])
    if wait>0: time.sleep(wait+random.uniform(0.1,0.4))
    try: return EM_SESSION.get(url,params=params,headers=headers,timeout=timeout)
    finally: _eml[0]=time.time()

url2='https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
r2=em_get(url2,params={'secid':f'1.{CODE}','fields1':'f1,f2,f3,f7',
    'fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65','lmt':'250'})
d2=r2.json()
flines=d2.get('data',{}).get('klines',[])
flows=[]
for line in flines:
    p=line.split(',')
    if len(p)>=7: flows.append({'date':p[0],'main_net':float(p[1]) if p[1]!='-' else 0,
        'small_net':float(p[2]) if p[2]!='-' else 0,'mid_net':float(p[3]) if p[3]!='-' else 0,
        'large_net':float(p[4]) if p[4]!='-' else 0,'super_net':float(p[5]) if p[5]!='-' else 0})
df_f=pd.DataFrame(flows)

# ============ FEATURE ENGINEERING ============
df['returns']=df['close'].pct_change()
df['gap']=(df['open']-df['close'].shift(1))/df['close'].shift(1)*100  # 隔夜跳空
df['intraday_range']=(df['high']-df['low'])/df['open']*100  # 日内振幅%

for w in [3,5,10,20,60,120]:
    df[f'ma{w}']=df['close'].rolling(w,min_periods=max(1,w//3)).mean()

df['atr14']=(df['high']-df['low']).rolling(14,min_periods=5).mean()
df['atr_pct']=df['atr14']/df['close']*100

delta=df['close'].diff()
gain=delta.where(delta>0,0).rolling(14,min_periods=7).mean()
loss=(-delta.where(delta<0,0)).rolling(14,min_periods=7).mean()
df['rsi']=100-100/(1+gain/loss.replace(0,np.nan))

df['ema12']=df['close'].ewm(span=12,adjust=False,min_periods=6).mean()
df['ema26']=df['close'].ewm(span=26,adjust=False,min_periods=13).mean()
df['macd']=df['ema12']-df['ema26']
df['macd_sig']=df['macd'].ewm(span=9,adjust=False,min_periods=5).mean()
df['macd_h']=df['macd']-df['macd_sig']

df['vol_ma5']=df['volume'].rolling(5,min_periods=2).mean()
df['vol_ma20']=df['volume'].rolling(20,min_periods=5).mean()
df['vol_ratio']=df['volume']/df['vol_ma20'].replace(0,np.nan)

# Bollinger
df['bb_mid']=df['close'].rolling(20,min_periods=10).mean()
df['bb_std']=df['close'].rolling(20,min_periods=10).std()
df['bb_upper']=df['bb_mid']+2*df['bb_std']
df['bb_lower']=df['bb_mid']-2*df['bb_std']
df['bb_pos']=(df['close']-df['bb_lower'])/(df['bb_upper']-df['bb_lower'])*100  # 0=bottom,100=top

# Support/Resistance
df['high_20']=df['high'].rolling(20,min_periods=10).max()
df['low_20']=df['low'].rolling(20,min_periods=10).min()
df['pct_from_high20']=(df['close']-df['high_20'])/df['high_20']*100
df['pct_from_low20']=(df['close']-df['low_20'])/df['low_20']*100

# Price position in range
df['range_pos']=(df['close']-df['low_20'])/(df['high_20']-df['low_20'])*100

# Merge fund flow
df=df.merge(df_f[['date','main_net','super_net','large_net']],on='date',how='left')
for col in ['main_net','super_net','large_net']: df[col]=df[col].fillna(0)
df['f3']=df['main_net'].rolling(3,min_periods=1).sum()
df['f5']=df['main_net'].rolling(5,min_periods=2).sum()
df['flow_ratio']=df['main_net']/(df['close']*df['volume'])*10000  # normalized flow

# Clean
core=['close','ma5','ma10','ma20','ma60','atr14','rsi','macd_h','vol_ratio','bb_pos',
      'range_pos','pct_from_high20','pct_from_low20','f3','f5','flow_ratio','intraday_range']
df=df.dropna(subset=[c for c in core if c in df.columns]).reset_index(drop=True)
print(f'Data: {len(df)} bars, {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')

# ============ ANALYSIS 1: Optimal Entry/Exit Zones ============
print('\n'+'='*70)
print('ANALYSIS 1: Optimal Entry & Exit Zones (Historical Pattern)')
print('='*70)

# Study what happens after specific RSI/BB/MACD conditions
def study_outcome(df, condition, horizon=5):
    """What's the average return after `horizon` days when `condition` is true?"""
    idxs = []
    for i in range(len(df)-horizon):
        try:
            if condition(df, i):
                entry = df.iloc[i]['close']
                exit_p = df.iloc[i+horizon]['close']
                ret = (exit_p/entry-1)*100
                idxs.append({'i':i,'entry':entry,'ret':ret,'date':df.iloc[i]['date']})
        except: pass
    return idxs

# Entry conditions to test
entry_conditions = {
    'RSI<30': lambda df,i: df.iloc[i]['rsi']<30,
    'RSI<35': lambda df,i: df.iloc[i]['rsi']<35,
    'RSI<40': lambda df,i: df.iloc[i]['rsi']<40,
    'BB_bottom': lambda df,i: df.iloc[i]['bb_pos']<5,
    'BB_low': lambda df,i: df.iloc[i]['bb_pos']<15,
    'Near_MA20': lambda df,i: abs(df.iloc[i]['close']/df.iloc[i]['ma20']-1)*100<2,
    'MACD_golden': lambda df,i: i>0 and df.iloc[i-1]['macd_h']<=0 and df.iloc[i]['macd_h']>0,
    'Vol_spike_dip': lambda df,i: df.iloc[i]['vol_ratio']>2 and df.iloc[i]['close']<df.iloc[i]['open'],
    'Flow_turn_pos': lambda df,i: i>0 and df.iloc[i-1]['f3']<-500_000_000 and df.iloc[i]['f3']>0,
    'RSI<35+BB<15': lambda df,i: df.iloc[i]['rsi']<35 and df.iloc[i]['bb_pos']<15,
    'RSI<40+NearMA20': lambda df,i: df.iloc[i]['rsi']<40 and abs(df.iloc[i]['close']/df.iloc[i]['ma20']-1)*100<3,
}

print('\nEntry Signal Analysis (5-day forward return):')
print(f'{"Condition":<25} {"Occurrences":>12} {"Avg 5D Ret":>12} {"Win Rate":>10} {"Best":>10} {"Worst":>10}')
print('-'*85)
best_entry = None
best_avg = -999
for name, cond in entry_conditions.items():
    results = study_outcome(df, cond, horizon=5)
    if results:
        avg_ret = np.mean([r['ret'] for r in results])
        win_rate = len([r for r in results if r['ret']>0])/len(results)*100
        best_r = max(r['ret'] for r in results)
        worst_r = min(r['ret'] for r in results)
        print(f'{name:<25} {len(results):>12} {avg_ret:>+11.1f}% {win_rate:>9.0f}% {best_r:>+9.1f}% {worst_r:>+9.1f}%')
        if avg_ret > best_avg:
            best_avg = avg_ret
            best_entry = name

print(f'\nBest entry signal: {best_entry} (avg 5-day return: {best_avg:+.1f}%)')

# Exit conditions
exit_conditions = {
    'RSI>70': lambda df,i: df.iloc[i]['rsi']>70,
    'RSI>75': lambda df,i: df.iloc[i]['rsi']>75,
    'RSI>80': lambda df,i: df.iloc[i]['rsi']>80,
    'BB_top': lambda df,i: df.iloc[i]['bb_pos']>95,
    'BB_high': lambda df,i: df.iloc[i]['bb_pos']>85,
    'Far_from_MA20': lambda df,i: df.iloc[i]['close']/df.iloc[i]['ma20']>1.15,
    'MACD_death': lambda df,i: i>0 and df.iloc[i-1]['macd_h']>=0 and df.iloc[i]['macd_h']<0,
    'Vol_climax_up': lambda df,i: df.iloc[i]['vol_ratio']>3 and df.iloc[i]['close']>df.iloc[i]['open'],
    'Flow_dump': lambda df,i: df.iloc[i]['f3']<-1_000_000_000,
    'RSI>75+BB>85': lambda df,i: df.iloc[i]['rsi']>75 and df.iloc[i]['bb_pos']>85,
    'RSI>70+FarMA20': lambda df,i: df.iloc[i]['rsi']>70 and df.iloc[i]['close']/df.iloc[i]['ma20']>1.12,
}

print('\nExit Signal Analysis (5-day forward return after signal):')
print(f'{"Condition":<25} {"Occurrences":>12} {"Avg 5D Ret":>12} {"Win Rate":>10}')
print('-'*65)
for name, cond in exit_conditions.items():
    results = study_outcome(df, cond, horizon=5)
    if results:
        avg_ret = np.mean([r['ret'] for r in results])
        win_rate = len([r for r in results if r['ret']>0])/len(results)*100
        print(f'{name:<25} {len(results):>12} {avg_ret:>+11.1f}% {win_rate:>9.0f}%')

# ============ ANALYSIS 2: Swing Backtest with T+0 Style ============
print('\n'+'='*70)
print('ANALYSIS 2: Swing/T+0 Backtest')
print('='*70)

class SwingState:
    def __init__(self): self.reset()
    def reset(self): self.entry_price=0; self.entry_i=0; self.trail_high=0; self.shares=0

state = SwingState()

def swing_signal(df, i):
    """Swing trading: buy at support, sell at resistance"""
    c = df.iloc[i]
    state.trail_high = max(state.trail_high, c['high'])

    # === BUY SIGNALS (enter on pullback in uptrend) ===
    if state.shares == 0:
        # Signal A: RSI oversold in bull trend
        if (c['rsi'] < 38 and c['close'] > c['ma60'] and
            c['close'] < c['ma20'] and c['vol_ratio'] < 1.5 and
            c['f5'] > -500_000_000):  # Not heavy outflow
            return ('BUY', 'RSI超卖+趋势支撑', 1.0)

        # Signal B: BB lower band touch in trend
        if (c['bb_pos'] < 10 and c['close'] > c['ma60'] and
            c['rsi'] < 45 and c['vol_ratio'] > 0.5):
            return ('BUY', '布林下轨+趋势支撑', 0.9)

        # Signal C: Flow reversal at support
        if (c['f3'] > 0 and i > 0 and df.iloc[i-1]['f3'] < -300_000_000 and
            c['close'] > c['ma60'] and c['rsi'] < 50):
            return ('BUY', '资金流反转+支撑', 0.85)

    # === SELL SIGNALS (exit on overbought/breakdown) ===
    if state.shares > 0:
        profit_pct = (c['close'] / state.entry_price - 1) * 100

        # Signal X: RSI extreme overbought
        if c['rsi'] > 78 and profit_pct > 3:
            return ('SELL', f'RSI超买({c["rsi"]:.0f})+盈利{profit_pct:.0f}%', -1.0)

        # Signal Y: Hit BB upper band with volume
        if (c['bb_pos'] > 90 and c['vol_ratio'] > 1.5 and
            c['close'] < c['open'] and profit_pct > 0):
            return ('SELL', f'布林上轨放量滞涨', -0.9)

        # Signal Z: MACD death cross in profit
        if (i > 0 and df.iloc[i-1]['macd_h'] >= 0 and c['macd_h'] < 0 and
            profit_pct > 1):
            return ('SELL', f'MACD死叉+盈利{profit_pct:.0f}%', -0.8)

        # Signal W: Breakdown from MA20 with heavy outflow
        if (c['close'] < c['ma20'] and c['f5'] < -1_000_000_000 and
            i > 0 and df.iloc[i-1]['close'] > df.iloc[i-1]['ma20']):
            return ('SELL', f'破MA20+资金出逃', -1.0)

        # Signal V: Trailing stop (protect profits)
        stop_price = state.trail_high * 0.93
        if c['close'] < stop_price and profit_pct > 5:
            return ('SELL', f'移动止盈(-7% from high)', -0.9)

    return ('HOLD', '', 0)

# Run swing backtest
cash = CASH0; shares = 0; equity = [CASH0]
trades = []; state.reset()

for i in range(len(df)):
    row = df.iloc[i]; price = row['close']
    action, reason, strength = swing_signal(df, i)

    if action == 'BUY' and shares == 0 and cash > price * 100:
        buy_cash = cash * abs(strength) * (1 - COMM)
        shares = buy_cash / price
        cash -= buy_cash
        state.shares = shares; state.entry_price = price; state.entry_i = i
        state.trail_high = price
        trades.append({'type':'BUY','date':row['date'],'price':price,'reason':reason})

    elif action == 'SELL' and shares > 0:
        proceeds = shares * price * (1 - COMM)
        cash += proceeds
        ret = (price / state.entry_price - 1) * 100
        hold_days = i - state.entry_i
        trades.append({'type':'SELL','date':row['date'],'price':price,'reason':reason,
                      'return_pct':ret,'hold_days':hold_days})
        shares = 0; state.reset()

    equity.append(cash + shares * price)

if shares > 0:
    last_p = df.iloc[-1]['close']
    cash += shares * last_p * (1 - COMM)
    ret = (last_p / state.entry_price - 1) * 100
    trades.append({'type':'SELL(F)','date':df.iloc[-1]['date'],'price':last_p,
                  'reason':'期末强制','return_pct':ret,'hold_days':len(df)-1-state.entry_i})
    equity[-1] = cash

# Metrics
eq_s = pd.Series(equity); ret_s = eq_s.pct_change().dropna()
tr = (equity[-1]/CASH0-1)*100
nt = len([t for t in trades if 'SELL' in t['type']])
wt = len([t for t in trades if t.get('return_pct',0)>0])
wr = (wt/nt*100) if nt>0 else 0
yrs = len(df)/252
ar = ((1+tr/100)**(1/yrs)-1)*100 if yrs>0 else 0
sr = (ret_s.mean()/ret_s.std()*np.sqrt(252)) if ret_s.std()>0 else 0
cm = eq_s.cummax(); mdd = ((eq_s-cm)/cm*100).min()

bh_ret = (df['close'].iloc[-1]/df['close'].iloc[0]-1)*100
bh_sr = (df['close'].pct_change().dropna().mean()/df['close'].pct_change().dropna().std()*np.sqrt(252))
bh_mdd = ((df['close']/df['close'].cummax()-1)*100).min()

print(f'  Buy&Hold:      Ret={bh_ret:.1f}%, Sharpe={bh_sr:.2f}, MaxDD={bh_mdd:.1f}%')
print(f'  Swing/T+0:     Ret={tr:.1f}%, Sharpe={sr:.2f}, MaxDD={mdd:.1f}%, Trades={nt}, WinRate={wr:.0f}%')
print(f'  Avg trade ret: {np.mean([t["return_pct"] for t in trades if "return_pct" in t]):.1f}%')
print(f'  Avg hold days: {np.mean([t["hold_days"] for t in trades if "hold_days" in t]):.1f}')

# ============ ANALYSIS 3: Support/Resistance Levels ============
print('\n'+'='*70)
print('ANALYSIS 3: Key Support & Resistance Levels')
print('='*70)

current_price = df['close'].iloc[-1]
print(f'Current price: {current_price:.2f}')

# Fibonacci levels from recent swing
recent_low = df['low'].iloc[-60:].min()
recent_high = df['high'].iloc[-60:].max()
diff = recent_high - recent_low

print(f'\nRecent 60-day range: {recent_low:.0f} - {recent_high:.0f}')
print(f'\nFibonacci Retracement (from low {recent_low:.0f}):')
for level, pct in [('0.236',0.236),('0.382',0.382),('0.500',0.500),('0.618',0.618),('0.786',0.786)]:
    price_fib = recent_high - diff * pct
    dist = (current_price - price_fib) / current_price * 100
    print(f'  {level}: {price_fib:.0f}  ({"above" if dist>0 else "below"} current by {abs(dist):.1f}%)')

print(f'\nFibonacci Extension (upside targets):')
for level, pct in [('1.272',1.272),('1.414',1.414),('1.618',1.618),('2.000',2.000)]:
    price_ext = recent_low + diff * pct
    dist = (price_ext - current_price) / current_price * 100
    print(f'  {level}: {price_ext:.0f}  ({dist:+.1f}% from current)')

# Pivot points
print(f'\nPivot Points (based on last 20-day range):')
h20 = df['high'].iloc[-20:].max()
l20 = df['low'].iloc[-20:].min()
c20 = df['close'].iloc[-1]
pp = (h20 + l20 + c20) / 3
r1 = 2*pp - l20; r2 = pp + (h20 - l20); r3 = h20 + 2*(pp - l20)
s1 = 2*pp - h20; s2 = pp - (h20 - l20); s3 = l20 - 2*(h20 - pp)
print(f'  Pivot: {pp:.0f}')
print(f'  R3: {r3:.0f}  R2: {r2:.0f}  R1: {r1:.0f}')
print(f'  S1: {s1:.0f}  S2: {s2:.0f}  S3: {s3:.0f}')

# MA levels
print(f'\nMoving Average Levels:')
for w in [5,10,20,60,120]:
    ma_val = df[f'ma{w}'].iloc[-1]
    dist = (current_price - ma_val) / current_price * 100
    print(f'  MA{w}: {ma_val:.0f}  ({dist:+.1f}% from price)')

# ============ CHART ============
print('\nGenerating charts...')
try:
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    plt.rcParams['font.sans-serif']=['SimHei','Microsoft YaHei','DejaVu Sans']
    plt.rcParams['axes.unicode_minus']=False
    dates=pd.to_datetime(df['date'])

    # Chart 1: Entry/Exit Zones with indicators
    fig,axes=plt.subplots(4,1,figsize=(18,16),gridspec_kw={'height_ratios':[3,1.2,1.2,1.2]})
    ax=axes[0]

    # Price + MA + BB
    ax.plot(dates,df['close'],color='#333',linewidth=1.2,alpha=0.85,label='Close')
    ax.plot(dates,df['ma20'],color='#004E89',linewidth=0.8,alpha=0.5,label='MA20')
    ax.plot(dates,df['ma60'],color='#1B998B',linewidth=0.8,alpha=0.4,label='MA60')
    ax.fill_between(dates,df['bb_upper'],df['bb_lower'],alpha=0.06,color='#004E89')
    ax.plot(dates,df['bb_upper'],color='#004E89',linewidth=0.4,alpha=0.3,linestyle='--')
    ax.plot(dates,df['bb_lower'],color='#004E89',linewidth=0.4,alpha=0.3,linestyle='--')

    # Buy/Sell markers
    buy_d,buy_p,buy_r=[],[],[]
    sell_d,sell_p,sell_r=[],[],[]
    for t in trades:
        idx=df[df['date']==t['date']].index
        if len(idx)==0: continue
        i=idx[0]
        if 'BUY' in t['type']:
            buy_d.append(dates.iloc[i]); buy_p.append(t['price']); buy_r.append(t.get('reason',''))
        else:
            sell_d.append(dates.iloc[i]); sell_p.append(t['price']); sell_r.append(t.get('reason',''))

    ax.scatter(buy_d,buy_p,color='#D62828',marker='^',s=120,zorder=5,edgecolors='white',linewidth=0.8)
    ax.scatter(sell_d,sell_p,color='#1B998B',marker='v',s=120,zorder=5,edgecolors='white',linewidth=0.8)

    # Annotate recent buy zones
    for j,(d,p,r) in enumerate(zip(buy_d,buy_p,buy_r)):
        ax.annotate(r, (d,p), textcoords='offset points', xytext=(5,12),
                   fontsize=7, color='#D62828', fontweight='bold',
                   arrowprops=dict(arrowstyle='->',color='#D62828',alpha=0.5))

    # Current price line + support/resistance
    ax.axhline(y=current_price, color='#FF6B35', linewidth=1.5, linestyle='-', alpha=0.7,
              label=f'Current: {current_price:.0f}')
    ax.axhline(y=df['ma20'].iloc[-1], color='#004E89', linewidth=1, linestyle=':', alpha=0.6,
              label=f'MA20: {df["ma20"].iloc[-1]:.0f}')
    ax.axhline(y=df['ma60'].iloc[-1], color='#1B998B', linewidth=1, linestyle=':', alpha=0.6,
              label=f'MA60: {df["ma60"].iloc[-1]:.0f}')
    ax.axhline(y=s1, color='#D62828', linewidth=0.8, linestyle='--', alpha=0.4, label=f'S1: {s1:.0f}')
    ax.axhline(y=s2, color='#D62828', linewidth=0.8, linestyle='--', alpha=0.3, label=f'S2: {s2:.0f}')

    ax.set_title(f'{NAME}({CODE}) - T+0/Swing Buy & Sell Zones', fontsize=14, fontweight='bold')
    ax.set_ylabel('Price (CNY)', fontsize=11)
    ax.legend(loc='upper left', fontsize=8, ncol=2)
    ax.grid(True, alpha=0.2)

    # RSI with buy/sell zones
    ax2=axes[1]
    rsi_vals=df['rsi'].values
    ax2.fill_between(dates,30,70,color='green',alpha=0.05)
    ax2.fill_between(dates,70,100,color='red',alpha=0.08,label='Overbought(>70)')
    ax2.fill_between(dates,0,30,color='blue',alpha=0.08,label='Oversold(<30)')
    ax2.plot(dates,rsi_vals,color='#333',linewidth=1)
    ax2.axhline(y=70,color='#D62828',linewidth=0.5,linestyle='--',alpha=0.5)
    ax2.axhline(y=30,color='#1B998B',linewidth=0.5,linestyle='--',alpha=0.5)
    ax2.axhline(y=50,color='gray',linewidth=0.3,alpha=0.3)
    # Mark buy/sell on RSI
    for d in buy_d:
        idx=df[df['date']==d.strftime('%Y-%m-%d')].index
        if len(idx)>0: ax2.scatter(d,df['rsi'].iloc[idx[0]],color='#D62828',marker='^',s=40,zorder=5)
    for d in sell_d:
        idx=df[df['date']==d.strftime('%Y-%m-%d')].index
        if len(idx)>0: ax2.scatter(d,df['rsi'].iloc[idx[0]],color='#1B998B',marker='v',s=40,zorder=5)
    ax2.set_ylabel('RSI(14)',fontsize=10)
    ax2.set_ylim(0,100)
    ax2.legend(loc='upper left',fontsize=8)
    ax2.grid(True,alpha=0.2)

    # Fund flow
    ax3=axes[2]
    flow_colors=['#D62828' if v>=0 else '#1B998B' for v in df['f3']/1e8]
    ax3.bar(dates,df['f3']/1e8,color=flow_colors,alpha=0.5,width=1)
    ax3.axhline(y=0,color='#333',linewidth=0.5)
    ax3.plot(dates,df['f5'].rolling(3).mean()/1e8,color='#FF6B35',linewidth=1,alpha=0.7,label='5D Flow MA')
    ax3.set_ylabel('3-Day Flow (Yi)',fontsize=10)
    ax3.legend(loc='upper left',fontsize=8)
    ax3.grid(True,alpha=0.2)

    # Equity curve
    ax4=axes[3]
    eq_dates=pd.to_datetime([df['date'].iloc[0]]+list(df['date']))
    eq_arr=np.array(equity)
    if len(eq_arr)==len(eq_dates):
        ax4.plot(eq_dates,eq_arr/CASH0*100,color='#004E89',linewidth=1.5,label=f'Swing ({tr:.0f}%)')
    bh_eq=CASH0*df['close']/df['close'].iloc[0]
    ax4.plot(dates,bh_eq/CASH0*100,'--',color='#333',linewidth=1,alpha=0.6,label=f'B&H ({bh_ret:.0f}%)')
    ax4.fill_between(eq_dates,eq_arr/CASH0*100,100,alpha=0.1,color='#004E89')
    ax4.axhline(y=100,color='gray',linewidth=0.5,linestyle=':')
    ax4.set_ylabel('Equity %',fontsize=10)
    ax4.set_xlabel('Date',fontsize=11)
    ax4.legend(loc='upper left',fontsize=8)
    ax4.grid(True,alpha=0.2)

    plt.tight_layout()
    c1=os.path.join(DATA_DIR,'swing_buy_sell_zones.png')
    plt.savefig(c1,dpi=150,bbox_inches='tight')
    plt.close()
    print(f'Saved: swing_buy_sell_zones.png')

    # Chart 2: Detailed entry/exit zone map (last 60 days zoom)
    recent=df.iloc[-60:]
    recent_dates=pd.to_datetime(recent['date'])
    fig,ax=plt.subplots(figsize=(18,8))
    ax.plot(recent_dates,recent['close'],color='#333',linewidth=1.5,alpha=0.9)
    ax.plot(recent_dates,recent['ma20'],color='#004E89',linewidth=1,alpha=0.5,label='MA20')
    ax.plot(recent_dates,recent['bb_upper'],color='#D62828',linewidth=0.6,alpha=0.4,linestyle='--')
    ax.plot(recent_dates,recent['bb_lower'],color='#1B998B',linewidth=0.6,alpha=0.4,linestyle='--')
    ax.fill_between(recent_dates,recent['bb_upper'],recent['bb_lower'],alpha=0.06,color='#004E89')

    # Buy zone (BB lower to MA20)
    ax.fill_between(recent_dates,recent['bb_lower'],recent['ma20'],alpha=0.12,color='#1B998B',label='BUY ZONE')
    # Sell zone (BB upper area)
    ax.fill_between(recent_dates,recent['bb_upper'],recent['close'].max()*1.05,alpha=0.08,color='#D62828',label='SELL ZONE')

    # Recent trades on zoom
    for t in trades[-6:]:
        idx=df[df['date']==t['date']].index
        if len(idx)==0: continue
        i=idx[0]
        if i>=len(df)-60:
            rel_i=i-(len(df)-60)
            if 0<=rel_i<len(recent):
                if 'BUY' in t['type']:
                    ax.scatter(recent_dates.iloc[rel_i],t['price'],color='#D62828',marker='^',s=150,zorder=5,edgecolors='white',linewidth=1.5)
                    ax.annotate(f"BUY\n{t.get('reason','')[:15]}",(recent_dates.iloc[rel_i],t['price']),
                              textcoords='offset points',xytext=(8,15),fontsize=8,color='#D62828',fontweight='bold')
                else:
                    ax.scatter(recent_dates.iloc[rel_i],t['price'],color='#1B998B',marker='v',s=150,zorder=5,edgecolors='white',linewidth=1.5)
                    ret=t.get('return_pct',0)
                    ax.annotate(f"SELL {ret:+.1f}%\n{t.get('reason','')[:15]}",(recent_dates.iloc[rel_i],t['price']),
                              textcoords='offset points',xytext=(8,-20),fontsize=8,color='#1B998B',fontweight='bold')

    ax.set_title(f'{NAME}({CODE}) - Recent 60 Days: Entry/Exit Zones',fontsize=14,fontweight='bold')
    ax.set_ylabel('Price (CNY)',fontsize=11)
    ax.legend(loc='upper left',fontsize=9)
    ax.grid(True,alpha=0.2)
    plt.tight_layout()
    c2=os.path.join(DATA_DIR,'swing_zones_60d.png')
    plt.savefig(c2,dpi=150,bbox_inches='tight')
    plt.close()
    print(f'Saved: swing_zones_60d.png')

except Exception as e:
    print(f'Chart error: {e}')
    import traceback; traceback.print_exc()

# Save trades
tp=os.path.join(DATA_DIR,'trades_swing_T0.csv')
with open(tp,'w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f)
    w.writerow(['type','date','price','reason','return_pct','hold_days'])
    for t in trades:
        w.writerow([t['type'],t['date'],f"{t['price']:.2f}",t.get('reason',''),
                   f"{t.get('return_pct',0):.1f}" if t.get('return_pct') else '',
                   t.get('hold_days','')])

# Print all trades
print('\n'+'='*70)
print('ALL SWING TRADES')
print('='*70)
print(f'{"Type":<8} {"Date":<12} {"Price":>8} {"Return":>8} {"Days":>6} {"Reason"}')
print('-'*80)
for t in trades:
    ret_str=f'{t.get("return_pct",0):+.1f}%' if t.get('return_pct') else ''
    print(f'{t["type"]:<8} {t["date"]:<12} {t["price"]:>8.1f} {ret_str:>8} {t.get("hold_days",""):>6} {t.get("reason","")[:40]}')

print('\nDONE.')
