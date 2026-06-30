"""
长飞光纤(601869) 量化策略设计与回测 V2
"""
import sys, os, time, random, json, csv, math, warnings
import requests
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')

CODE, NAME = '601869', '长飞光纤'
DATA_DIR = r'd:\02Project\QMT-export\data\601869_backtest'
os.makedirs(DATA_DIR, exist_ok=True)
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
INITIAL_CAPITAL = 1_000_000
COMMISSION = 0.0003

# ============ DATA COLLECTION ============
print('='*60)
print(f'STEP 1: Downloading data for {NAME}({CODE})')
print('='*60)

# K-line (use full 1-year, no fund-flow merge constraint for baseline)
tc = f'sh{CODE}'
url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
params = {'param': f'{tc},day,,,260,qfq'}
r = requests.get(url, params=params, headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}, timeout=15)
d = r.json()
raw = d.get('data', {}).get(tc, {}).get('qfqday', []) or d.get('data', {}).get(tc, {}).get('day', [])
klines = [{'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
           'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])} for k in raw]
df = pd.DataFrame(klines)
print(f'  K-line: {len(df)} bars, {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')

# Fund flow
EM_SESSION = requests.Session()
EM_SESSION.headers.update({'User-Agent': UA})
_em_last = [0.0]
def em_get(url, params=None, headers=None, timeout=15):
    wait = 1.5 - (time.time() - _em_last[0])
    if wait > 0: time.sleep(wait + random.uniform(0.2, 0.6))
    try: return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout)
    finally: _em_last[0] = time.time()

url2 = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
params2 = {'secid': f'1.{CODE}', 'fields1':'f1,f2,f3,f7',
           'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65', 'lmt': '250'}
r2 = em_get(url2, params=params2)
d2 = r2.json()
flow_lines = d2.get('data', {}).get('klines', [])
flows = []
for line in flow_lines:
    parts = line.split(',')
    if len(parts) >= 7:
        flows.append({'date': parts[0], 'main_net': float(parts[1]) if parts[1] != '-' else 0,
                      'small_net': float(parts[2]) if parts[2] != '-' else 0,
                      'mid_net': float(parts[3]) if parts[3] != '-' else 0,
                      'large_net': float(parts[4]) if parts[4] != '-' else 0,
                      'super_net': float(parts[5]) if parts[5] != '-' else 0})
df_flow = pd.DataFrame(flows)
print(f'  Fund flow: {len(df_flow)} days')

# Core analysis on K-line only (full 260 bars)
df['returns'] = df['close'].pct_change()
df['log_returns'] = np.log(df['close'] / df['close'].shift(1))

# MA with min_periods for early entries
for w in [5, 10, 20, 60]:
    df[f'ma{w}'] = df['close'].rolling(w, min_periods=w//2).mean()

# ATR
df['atr'] = (df['high'] - df['low']).rolling(14, min_periods=5).mean()
df['volatility_20'] = df['returns'].rolling(20, min_periods=5).std()

# Volume
df['volume_ma5'] = df['volume'].rolling(5, min_periods=2).mean()
df['volume_ma20'] = df['volume'].rolling(20, min_periods=5).mean()
df['volume_ratio'] = df['volume'] / df['volume_ma20'].replace(0, np.nan)

# Bollinger
df['bb_mid'] = df['close'].rolling(20, min_periods=10).mean()
df['bb_std'] = df['close'].rolling(20, min_periods=10).std()
df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']

# RSI
delta = df['close'].diff()
gain = delta.where(delta > 0, 0).rolling(14, min_periods=7).mean()
loss = (-delta.where(delta < 0, 0)).rolling(14, min_periods=7).mean()
rs = gain / loss.replace(0, np.nan)
df['rsi'] = 100 - (100 / (1 + rs))

# MACD
df['ema12'] = df['close'].ewm(span=12, adjust=False, min_periods=6).mean()
df['ema26'] = df['close'].ewm(span=26, adjust=False, min_periods=13).mean()
df['macd'] = df['ema12'] - df['ema26']
df['macd_signal'] = df['macd'].ewm(span=9, adjust=False, min_periods=5).mean()
df['macd_hist'] = df['macd'] - df['macd_signal']

# Merge fund flow for fund-specific strategies
df_m = df.merge(df_flow[['date', 'main_net', 'super_net', 'small_net', 'mid_net', 'large_net']], on='date', how='left')
df_m['main_net'] = df_m['main_net'].fillna(0)
df_m['super_net'] = df_m['super_net'].fillna(0)
df_m['main_net_ma5'] = df_m['main_net'].rolling(5, min_periods=2).mean()
df_m['main_net_cum'] = df_m['main_net'].cumsum()

# Use full K-line data (drop only NaN in core indicators)
core_cols = ['close', 'ma5', 'ma10', 'ma20', 'atr', 'rsi', 'volume_ratio', 'macd_hist', 'bb_upper', 'bb_lower']
df_core = df.dropna(subset=[c for c in core_cols if c in df.columns]).reset_index(drop=True)
df_full = df_m.dropna(subset=[c for c in core_cols if c in df_m.columns]).reset_index(drop=True)

print(f'  Core data (K-line only): {len(df_core)} bars')
print(f'  Full data (+fund flow): {len(df_full)} bars')

# Save
df_core.to_csv(os.path.join(DATA_DIR, 'raw_data_kline.csv'), index=False, encoding='utf-8-sig')
df_full.to_csv(os.path.join(DATA_DIR, 'raw_data_full.csv'), index=False, encoding='utf-8-sig')
print('  Data saved.')

# ============ STRATEGIES ============
print('\n' + '='*60)
print('STEP 2: Strategy Backtesting')
print('='*60)

class TrailState:
    def __init__(self): self.high = 0; self.stop = 0

def run_backtest(name, df, signal_func, use_close_only=True):
    signals, positions, equity = [], [], [INITIAL_CAPITAL]
    cash = INITIAL_CAPITAL
    shares = 0
    in_position = False
    entry_price = 0
    entry_idx = 0
    trades = []

    for i in range(len(df)):
        row = df.iloc[i]
        price = row['close']
        sig = signal_func(df, i)
        if sig is None: sig = 0
        signals.append(sig)

        if sig == 1 and not in_position and cash > price * 100:
            trade_cash = cash * 0.95
            shares = trade_cash / price * (1 - COMMISSION)
            cash -= trade_cash
            in_position = True
            entry_price = price
            entry_idx = i
            trades.append({'type': 'BUY', 'date': row['date'], 'price': price})

        elif sig == -1 and in_position:
            proceeds = shares * price * (1 - COMMISSION)
            cash += proceeds
            ret = (price / entry_price - 1) * 100
            trades.append({'type': 'SELL', 'date': row['date'], 'price': price,
                          'return_pct': ret, 'hold_days': i - entry_idx})
            shares = 0
            in_position = False

        equity.append(cash + shares * price)
        positions.append(1 if in_position else 0)

    if in_position:
        last_p = df.iloc[-1]['close']
        proceeds = shares * last_p * (1 - COMMISSION)
        cash += proceeds
        trades.append({'type': 'SELL(F)', 'date': df.iloc[-1]['date'], 'price': last_p,
                      'return_pct': (last_p/entry_price-1)*100, 'hold_days': len(df)-1-entry_idx})
        equity[-1] = cash
        positions[-1] = 0

    eq_s = pd.Series(equity)
    ret_s = eq_s.pct_change().dropna()
    total_ret = (equity[-1] / INITIAL_CAPITAL - 1) * 100
    n_trades = len([t for t in trades if 'SELL' in t['type']])
    win_trades = len([t for t in trades if t.get('return_pct', 0) > 0])
    win_rate = (win_trades / n_trades * 100) if n_trades > 0 else 0
    years = len(df) / 252
    ann_ret = ((1 + total_ret/100) ** (1/years) - 1) * 100 if years > 0 else 0
    sharpe = (ret_s.mean() / ret_s.std() * np.sqrt(252)) if ret_s.std() > 0 else 0
    cummax = eq_s.cummax()
    max_dd = ((eq_s - cummax) / cummax * 100).min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    return {
        'name': name, 'signals': signals, 'positions': positions, 'equity': equity,
        'trades': trades,
        'metrics': {'Total Return': f'{total_ret:.1f}%', 'Annual Return': f'{ann_ret:.1f}%',
                    'Sharpe Ratio': f'{sharpe:.2f}', 'Max Drawdown': f'{max_dd:.1f}%',
                    'Calmar Ratio': f'{calmar:.2f}', 'Total Trades': n_trades,
                    'Win Rate': f'{win_rate:.1f}%', 'Final Equity': f'{equity[-1]:,.0f}'},
        'total_return': total_ret, 'sharpe': sharpe, 'max_dd': max_dd,
    }

# --- S1: MA Golden/Death Cross ---
_trail1 = TrailState()
def s1_ma_cross(df, i):
    if i < 1: return 0
    p, c = df.iloc[i-1], df.iloc[i]
    if p['ma5'] <= p['ma20'] and c['ma5'] > c['ma20']: return 1
    if p['ma5'] >= p['ma20'] and c['ma5'] < c['ma20']: return -1
    return 0

# --- S2: Bollinger Band + RSI ---
def s2_bb(df, i):
    c = df.iloc[i]
    if c['close'] <= c['bb_lower'] * 1.03 and c['rsi'] < 40: return 1
    if c['close'] >= c['bb_upper'] * 0.97 and c['rsi'] > 60: return -1
    return 0

# --- S3: Volume-Price Breakout ---
def s3_vol_break(df, i):
    c = df.iloc[i]
    if (c['volume_ratio'] > 1.5 and c['close'] > c['ma20'] and
        c['close'] > c['open'] and 40 < c['rsi'] < 75): return 1
    if c['volume_ratio'] > 2.2 and c['close'] < c['ma10'] and c['close'] < c['open']: return -1
    return 0

# --- S4: Trend Following + Trailing Stop ---
_trail4 = TrailState()
def s4_trend_trail(df, i):
    if i < 5: return 0
    c = df.iloc[i]
    # Entry
    if (c['close'] > c['ma20'] > c['ma60'] and c['ma5'] > c['ma20'] and
        c['volume_ratio'] > 1.1 and c['macd_hist'] > 0):
        _trail4.high = max(_trail4.high, c['close'])
        _trail4.stop = _trail4.high - 2.5 * c['atr']
        return 1
    # Trailing stop exit
    if _trail4.stop > 0 and c['close'] < _trail4.stop:
        _trail4.high = 0; _trail4.stop = 0
        return -1
    # Update trail
    if _trail4.high > 0:
        _trail4.high = max(_trail4.high, c['high'])
        _trail4.stop = _trail4.high - 2.5 * c['atr']
    # MA death cross exit
    if i > 0:
        p = df.iloc[i-1]
        if p['ma5'] >= p['ma10'] and c['ma5'] < c['ma10']:
            _trail4.high = 0; _trail4.stop = 0
            return -1
    return 0

# --- S5: MACD Momentum ---
def s5_macd(df, i):
    if i < 1: return 0
    p, c = df.iloc[i-1], df.iloc[i]
    # MACD golden cross
    if p['macd_hist'] <= 0 and c['macd_hist'] > 0 and c['close'] > c['ma20']: return 1
    # MACD death cross
    if p['macd_hist'] >= 0 and c['macd_hist'] < 0: return -1
    return 0

# --- S6: Composite (weighted fusion) ---
def s6_composite(df, i):
    s1 = s1_ma_cross(df, i)
    s2 = s2_bb(df, i)
    s3 = s3_vol_break(df, i)
    s5 = s5_macd(df, i)
    score = s1 * 0.25 + s2 * 0.15 + s3 * 0.35 + s5 * 0.25
    if score >= 0.5: return 1
    if score <= -0.5: return -1
    return 0

# --- S7: Fund Flow Following (uses full data) ---
def s7_fund_flow(df, i):
    if i < 10: return 0
    c = df.iloc[i]
    recent5 = df['main_net'].iloc[max(0,i-4):i+1].sum()
    if (c['main_net_ma5'] > 0 and recent5 > 0 and
        c['close'] > c['ma20'] and c['rsi'] < 70): return 1
    recent3 = df['main_net'].iloc[max(0,i-2):i+1].sum()
    if recent3 < -300_000_000 and c['close'] < c['ma10']: return -1
    return 0

strategies = [
    ('S1_MA_Cross', s1_ma_cross, df_core),
    ('S2_Bollinger', s2_bb, df_core),
    ('S3_Vol_Break', s3_vol_break, df_core),
    ('S4_TrendTrail', s4_trend_trail, df_core),
    ('S5_MACD', s5_macd, df_core),
    ('S6_Composite', s6_composite, df_core),
    ('S7_FundFlow', s7_fund_flow, df_full),
]

results = {}
for name, func, ddf in strategies:
    _trail4.high = 0; _trail4.stop = 0
    res = run_backtest(name, ddf, func)
    results[name] = res
    m = res['metrics']
    print(f'  {name}: Ret={m["Total Return"]}, Sharpe={m["Sharpe Ratio"]}, MaxDD={m["Max Drawdown"]}, Trades={m["Total Trades"]}, Win={m["Win Rate"]}')

# Benchmark
bh_ret = (df_core['close'].iloc[-1] / df_core['close'].iloc[0] - 1) * 100
bh_ret_s = df_core['close'].pct_change().dropna()
bh_sr = (bh_ret_s.mean() / bh_ret_s.std() * np.sqrt(252)) if bh_ret_s.std() > 0 else 0
bh_mdd = ((df_core['close'] / df_core['close'].cummax() - 1) * 100).min()
print(f'\n  Buy&Hold: Ret={bh_ret:.1f}%, Sharpe={bh_sr:.2f}, MaxDD={bh_mdd:.1f}%')

# ============ CHARTS ============
print('\n' + '='*60)
print('STEP 3: Generating Charts')
print('='*60)

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    dates = pd.to_datetime(df_core['date'])
    n = len(df_core)
    colors6 = ['#D62828', '#004E89', '#1B998B', '#FF6B35', '#6A4C93', '#1982C4', '#F4A261']

    # ---- Chart 1: Price + MA + Best Strategy Signals ----
    best = results['S6_Composite']
    fig, axes = plt.subplots(3, 1, figsize=(16, 12), gridspec_kw={'height_ratios': [3, 1, 1]})
    ax = axes[0]
    ax.plot(dates, df_core['close'], color='#333', linewidth=1.2, alpha=0.85, label='Close')
    ax.plot(dates, df_core['ma5'], color='#FF6B35', linewidth=0.8, alpha=0.5, label='MA5')
    ax.plot(dates, df_core['ma20'], color='#004E89', linewidth=0.8, alpha=0.5, label='MA20')
    ax.plot(dates, df_core['ma60'], color='#1B998B', linewidth=0.8, alpha=0.4, label='MA60')
    # Fill between MA20
    ax.fill_between(dates, df_core['bb_upper'], df_core['bb_lower'], alpha=0.08, color='#004E89', label='BB(2σ)')

    buy_d, buy_p, sell_d, sell_p = [], [], [], []
    for t in best['trades']:
        idx = df_core[df_core['date'] == t['date']].index
        if len(idx) == 0: continue
        i = idx[0]
        if 'BUY' in t['type']:
            buy_d.append(dates.iloc[i]); buy_p.append(t['price'])
        else:
            sell_d.append(dates.iloc[i]); sell_p.append(t['price'])
    ax.scatter(buy_d, buy_p, color='#D62828', marker='^', s=100, zorder=5, label=f'Buy({len(buy_d)})', edgecolors='white', linewidth=0.5)
    ax.scatter(sell_d, sell_p, color='#1B998B', marker='v', s=100, zorder=5, label=f'Sell({len(sell_d)})', edgecolors='white', linewidth=0.5)
    ax.set_title(f'{NAME}({CODE}) - S6 Composite Strategy Signals  |  B&H Return: {bh_ret:.1f}%', fontsize=14, fontweight='bold')
    ax.set_ylabel('Price (CNY)', fontsize=11)
    ax.legend(loc='upper left', fontsize=9, ncol=2)
    ax.grid(True, alpha=0.25)

    ax2 = axes[1]
    colors_bar = ['#D62828' if df_core['close'].iloc[i] >= df_core['open'].iloc[i] else '#1B998B' for i in range(n)]
    ax2.bar(dates, df_core['volume'] / 1e6, color=colors_bar, alpha=0.5, width=1)
    ax2.set_ylabel('Volume (M)', fontsize=10)
    ax2.grid(True, alpha=0.25)

    ax3 = axes[2]
    rsi_vals = df_core['rsi'].values
    ax3.fill_between(dates, rsi_vals, 50, where=rsi_vals >= 50, color='#D62828', alpha=0.3, label='RSI>50')
    ax3.fill_between(dates, rsi_vals, 50, where=rsi_vals < 50, color='#1B998B', alpha=0.3, label='RSI<50')
    ax3.plot(dates, rsi_vals, color='#333', linewidth=1)
    ax3.axhline(y=70, color='#D62828', linewidth=0.5, linestyle='--', alpha=0.5)
    ax3.axhline(y=30, color='#1B998B', linewidth=0.5, linestyle='--', alpha=0.5)
    ax3.set_ylabel('RSI(14)', fontsize=10)
    ax3.set_xlabel('Date', fontsize=11)
    ax3.set_ylim(0, 100)
    ax3.legend(loc='upper left', fontsize=9)
    ax3.grid(True, alpha=0.25)
    plt.tight_layout()
    c1 = os.path.join(DATA_DIR, 'chart1_price_signals.png')
    plt.savefig(c1, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart1_price_signals.png')

    # ---- Chart 2: Equity Curves All Strategies ----
    fig, axes = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={'height_ratios': [3, 1]})
    ax = axes[0]
    for idx, (name, res) in enumerate(results.items()):
        eq_dates = pd.to_datetime([df_core['date'].iloc[0]] + list(df_core['date']))
        eq = np.array(res['equity'])
        if len(eq) == len(eq_dates):
            ax.plot(eq_dates, eq / INITIAL_CAPITAL * 100, color=colors6[idx], linewidth=1.5, alpha=0.85,
                    label=f'{name} ({res["metrics"]["Total Return"]})')
    bh_eq = INITIAL_CAPITAL * df_core['close'] / df_core['close'].iloc[0]
    ax.plot(dates, bh_eq / INITIAL_CAPITAL * 100, '--', color='#333', linewidth=2, alpha=0.7, label=f'Buy&Hold ({bh_ret:.1f}%)')
    ax.axhline(y=100, color='gray', linewidth=0.5, linestyle=':')
    ax.set_title(f'{NAME}({CODE}) - All Strategy Equity Curves', fontsize=14, fontweight='bold')
    ax.set_ylabel('Equity (% of Initial)', fontsize=11)
    ax.legend(loc='upper left', fontsize=8, ncol=2)
    ax.grid(True, alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.0f}%'))

    ax2 = axes[1]
    for idx, (name, res) in enumerate(results.items()):
        eq_dates = pd.to_datetime([df_core['date'].iloc[0]] + list(df_core['date']))
        eq = np.array(res['equity'])
        if len(eq) == len(eq_dates):
            cm = np.maximum.accumulate(eq)
            dd = (eq - cm) / cm * 100
            ax2.fill_between(eq_dates, dd, 0, color=colors6[idx], alpha=0.25, label=name)
    ax2.set_ylabel('Drawdown %', fontsize=10)
    ax2.set_xlabel('Date', fontsize=11)
    ax2.grid(True, alpha=0.25)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.0f}%'))
    plt.tight_layout()
    c2 = os.path.join(DATA_DIR, 'chart2_equity_curves.png')
    plt.savefig(c2, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart2_equity_curves.png')

    # ---- Chart 3: Best Strategy Deep Dive ----
    fig, axes = plt.subplots(4, 1, figsize=(16, 14), gridspec_kw={'height_ratios': [2.5, 1.5, 1.5, 1.5]})
    best_eq = np.array(best['equity'])
    best_dates = pd.to_datetime([df_core['date'].iloc[0]] + list(df_core['date']))

    ax = axes[0]
    ax.plot(dates, df_core['close'], color='#333', linewidth=1, alpha=0.7)
    for t in best['trades']:
        idx = df_core[df_core['date'] == t['date']].index
        if len(idx) == 0: continue
        i = idx[0]
        if 'BUY' in t['type']:
            ax.axvline(x=dates.iloc[i], color='#D62828', alpha=0.25, linewidth=0.8)
        else:
            ax.axvline(x=dates.iloc[i], color='#1B998B', alpha=0.25, linewidth=0.8)
            ret = t.get('return_pct', 0)
            color = '#D62828' if ret > 0 else '#1B998B'
            ax.annotate(f'{ret:+.1f}%', (dates.iloc[i], df_core['close'].iloc[i]),
                       textcoords='offset points', xytext=(8, 8), fontsize=7.5, color=color, fontweight='bold')
    ax.set_title(f'Best Strategy: S6_Composite - Trade-by-Trade Analysis', fontsize=13, fontweight='bold')
    ax.set_ylabel('Price', fontsize=10)
    ax.grid(True, alpha=0.25)

    ax2 = axes[1]
    ax2.plot(best_dates, best_eq / INITIAL_CAPITAL * 100, color='#004E89', linewidth=1.5)
    ax2.fill_between(best_dates, best_eq/INITIAL_CAPITAL*100, 100, alpha=0.12, color='#004E89')
    ax2.axhline(y=100, color='gray', linewidth=0.5, linestyle=':')
    ax2.set_ylabel('Equity %', fontsize=10)
    ax2.grid(True, alpha=0.25)

    ax3 = axes[2]
    cm = np.maximum.accumulate(best_eq)
    dd = (best_eq - cm) / cm * 100
    ax3.fill_between(best_dates, dd, 0, color='#D62828', alpha=0.4)
    ax3.set_ylabel('Drawdown %', fontsize=10)
    ax3.grid(True, alpha=0.25)

    ax4 = axes[3]
    pos_arr = np.array(best['positions'][:len(dates)])
    ax4.fill_between(dates, pos_arr, 0, color='#004E89', alpha=0.35, label='Position')
    ax4_twin = ax4.twinx()
    ax4_twin.plot(dates, df_core['macd_hist'].values, color='#FF6B35', linewidth=0.8, alpha=0.6, label='MACD Hist')
    ax4_twin.axhline(y=0, color='#333', linewidth=0.5, alpha=0.4)
    ax4.set_ylabel('Position', fontsize=10)
    ax4_twin.set_ylabel('MACD Hist', fontsize=10)
    ax4.set_xlabel('Date', fontsize=11)
    ax4.set_ylim(-0.1, 1.3)
    ax4.grid(True, alpha=0.25)
    plt.tight_layout()
    c3 = os.path.join(DATA_DIR, 'chart3_best_strategy_detail.png')
    plt.savefig(c3, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart3_best_strategy_detail.png')

    # ---- Chart 4: Strategy Comparison + Trade Distribution ----
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Returns bar chart
    names_l = list(results.keys()) + ['Buy&Hold']
    rets_l = [results[n]['total_return'] for n in results] + [bh_ret]
    sharpes_l = [results[n]['sharpe'] for n in results] + [bh_sr]
    xp = range(len(names_l))
    bcolors = ['#D62828' if r > 0 else '#1B998B' for r in rets_l]
    bars = axes[0].bar(xp, rets_l, color=bcolors, alpha=0.75, edgecolor='white')
    axes[0].set_xticks(xp)
    axes[0].set_xticklabels(names_l, rotation=45, ha='right', fontsize=8)
    axes[0].set_title('Total Return Comparison', fontsize=12, fontweight='bold')
    axes[0].set_ylabel('Return %', fontsize=10)
    axes[0].axhline(y=bh_ret, color='#333', linewidth=1, linestyle='--', alpha=0.5, label=f'B&H:{bh_ret:.1f}%')
    axes[0].grid(True, alpha=0.25, axis='y')
    for bar, ret in zip(bars, rets_l):
        axes[0].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2 if ret >= 0 else bar.get_height() - 8,
                    f'{ret:.1f}%', ha='center', fontsize=7.5, fontweight='bold')
    axes[0].legend(fontsize=8)

    # Sharpe comparison
    bars2 = axes[1].bar(xp, sharpes_l, color=['#004E89' if s > 0 else '#D62828' for s in sharpes_l], alpha=0.75, edgecolor='white')
    axes[1].set_xticks(xp)
    axes[1].set_xticklabels(names_l, rotation=45, ha='right', fontsize=8)
    axes[1].set_title('Sharpe Ratio Comparison', fontsize=12, fontweight='bold')
    axes[1].set_ylabel('Sharpe', fontsize=10)
    axes[1].axhline(y=0, color='#333', linewidth=0.5)
    axes[1].grid(True, alpha=0.25, axis='y')
    for bar, sr in zip(bars2, sharpes_l):
        axes[1].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                    f'{sr:.2f}', ha='center', fontsize=7.5, fontweight='bold')
    plt.tight_layout()
    c4 = os.path.join(DATA_DIR, 'chart4_comparison.png')
    plt.savefig(c4, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart4_comparison.png')

    CHARTS_OK = True
except Exception as e:
    print(f'  Chart error: {e}')
    import traceback; traceback.print_exc()
    CHARTS_OK = False

# ============ SAVE RESULTS ============
print('\n' + '='*60)
print('STEP 4: Saving Results')
print('='*60)

for name, res in results.items():
    tp = os.path.join(DATA_DIR, f'trades_{name}.csv')
    with open(tp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['type', 'date', 'price', 'return_pct', 'hold_days'])
        for t in res['trades']:
            w.writerow([t['type'], t['date'], f"{t['price']:.2f}",
                       f"{t.get('return_pct', 0):.1f}" if t.get('return_pct') else '',
                       t.get('hold_days', '')])
    print(f'  Saved: trades_{name}.csv')

# Equity curves
eq_df = pd.DataFrame({'date': list(df_core['date'])})
for name, res in results.items():
    eq = res['equity']
    eq_df[name] = eq[1:] if len(eq) == len(eq_df) + 1 else eq[:len(eq_df)]
eq_df['Buy_Hold'] = INITIAL_CAPITAL * df_core['close'] / df_core['close'].iloc[0]
eq_df.to_csv(os.path.join(DATA_DIR, 'equity_curves.csv'), index=False, encoding='utf-8-sig')

# Summary JSON
summary = {
    'stock': {'code': CODE, 'name': NAME},
    'period': {'start': df_core['date'].iloc[0], 'end': df_core['date'].iloc[-1], 'bars': len(df_core)},
    'initial_capital': INITIAL_CAPITAL,
    'benchmark': {'buy_hold_return': f'{bh_ret:.1f}%', 'sharpe': f'{bh_sr:.2f}', 'max_drawdown': f'{bh_mdd:.1f}%'},
    'strategies': {name: res['metrics'] for name, res in results.items()}
}
with open(os.path.join(DATA_DIR, 'backtest_summary.json'), 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

# ============ FINAL SUMMARY ============
print('\n' + '='*70)
print('FINAL BACKTEST RESULTS')
print('='*70)
print(f'\nStock: {NAME}({CODE})  |  Period: {df_core["date"].iloc[0]} ~ {df_core["date"].iloc[-1]}  |  Bars: {len(df_core)}')
print(f'Initial: {INITIAL_CAPITAL:,.0f} CNY  |  B&H Return: {bh_ret:.1f}%  |  B&H Sharpe: {bh_sr:.2f}  |  B&H MaxDD: {bh_mdd:.1f}%')
print(f'\n{"Strategy":<20} {"Return":>10} {"Annual":>10} {"Sharpe":>8} {"MaxDD":>8} {"Calmar":>8} {"Trades":>7} {"WinRate":>8}')
print('-' * 85)
for name, res in results.items():
    m = res['metrics']
    print(f'{name:<20} {m["Total Return"]:>10} {m["Annual Return"]:>10} {m["Sharpe Ratio"]:>8} {m["Max Drawdown"]:>8} {m["Calmar Ratio"]:>8} {m["Total Trades"]:>7} {m["Win Rate"]:>8}')

best_name = max(results.items(), key=lambda x: x[1]['total_return'])[0]
print(f'\n>>> BEST: {best_name}  |  Return: {results[best_name]["metrics"]["Total Return"]}  |  Sharpe: {results[best_name]["metrics"]["Sharpe Ratio"]}')
print(f'\nAll outputs: {DATA_DIR}')
for f in sorted(os.listdir(DATA_DIR)):
    print(f'  {f} ({os.path.getsize(os.path.join(DATA_DIR, f)):,} bytes)')
print('\nDONE.')
