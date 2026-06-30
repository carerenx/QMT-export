"""
长飞光纤(601869) 量化策略设计与回测
===================================
策略：
  1. 双均线交叉 (MA5/MA20)
  2. 布林带突破 (Bollinger Band)
  3. 量价共振 (Volume-Price Breakout)
  4. 资金流趋势 (Fund Flow Momentum)
  5. 综合策略 (Multi-Signal Fusion)
"""
import sys, os, time, random, json, csv, math, warnings
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')

# ========== CONFIG ==========
CODE = '601869'
NAME = '长飞光纤'
DATA_DIR = r'd:\02Project\QMT-export\data\601869_backtest'
os.makedirs(DATA_DIR, exist_ok=True)
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
INITIAL_CAPITAL = 1_000_000  # 100万初始资金
COMMISSION = 0.0003  # 万三佣金
SLIPPAGE = 0.001  # 0.1% 滑点

# ========== STEP 1: DATA COLLECTION ==========
print('='*60)
print(f'STEP 1: Downloading 1-year data for {NAME}({CODE})')
print('='*60)

# 1a. Daily K-line from Tencent (250 trading days)
tc = f'sh{CODE}'
url_kline = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
params = {'param': f'{tc},day,,,260,qfq'}
r = requests.get(url_kline, params=params, headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}, timeout=15)
d = r.json()
klines_raw = d.get('data', {}).get(tc, {}).get('qfqday', []) or d.get('data', {}).get(tc, {}).get('day', [])

klines = []
for k in klines_raw:
    klines.append({
        'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
        'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])
    })
df_kline = pd.DataFrame(klines)
print(f'  K-line: {len(df_kline)} bars, {df_kline["date"].iloc[0]} ~ {df_kline["date"].iloc[-1]}')

# 1b. Fund flow data
EM_SESSION = requests.Session()
EM_SESSION.headers.update({'User-Agent': UA})
_em_last = [0.0]

def em_get(url, params=None, headers=None, timeout=15):
    wait = 1.5 - (time.time() - _em_last[0])
    if wait > 0: time.sleep(wait + random.uniform(0.2, 0.6))
    try: return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout)
    finally: _em_last[0] = time.time()

url_flow = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
params_flow = {
    'secid': f'1.{CODE}', 'fields1':'f1,f2,f3,f7',
    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
    'lmt': '250',
}
r_flow = em_get(url_flow, params=params_flow)
d_flow = r_flow.json()
flow_lines = d_flow.get('data', {}).get('klines', [])
flows = []
for line in flow_lines:
    parts = line.split(',')
    if len(parts) >= 7:
        flows.append({
            'date': parts[0],
            'main_net': float(parts[1]) if parts[1] != '-' else 0,
            'small_net': float(parts[2]) if parts[2] != '-' else 0,
            'mid_net': float(parts[3]) if parts[3] != '-' else 0,
            'large_net': float(parts[4]) if parts[4] != '-' else 0,
            'super_net': float(parts[5]) if parts[5] != '-' else 0,
        })
df_flow = pd.DataFrame(flows)
print(f'  Fund flow: {len(df_flow)} days')

# Merge data
df = df_kline.merge(df_flow, on='date', how='inner')
df = df.sort_values('date').reset_index(drop=True)
print(f'  Merged: {len(df)} rows')

# Save raw data
df.to_csv(os.path.join(DATA_DIR, 'raw_data.csv'), index=False, encoding='utf-8-sig')
print(f'  Saved: raw_data.csv')

# ========== STEP 2: FEATURE ENGINEERING ==========
print('\n' + '='*60)
print('STEP 2: Feature Engineering')
print('='*60)

# Price features
df['returns'] = df['close'].pct_change()
df['log_returns'] = np.log(df['close'] / df['close'].shift(1))
df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()
df['ma20'] = df['close'].rolling(20).mean()
df['ma60'] = df['close'].rolling(60).mean()
df['ma120'] = df['close'].rolling(120).mean()

# Volatility
df['atr'] = (df['high'] - df['low']).rolling(14).mean()
df['volatility_20'] = df['returns'].rolling(20).std()

# Volume features
df['volume_ma5'] = df['volume'].rolling(5).mean()
df['volume_ma20'] = df['volume'].rolling(20).mean()
df['volume_ratio'] = df['volume'] / df['volume_ma20']

# Bollinger Bands
df['bb_mid'] = df['close'].rolling(20).mean()
df['bb_std'] = df['close'].rolling(20).std()
df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']

# RSI
delta = df['close'].diff()
gain = delta.where(delta > 0, 0).rolling(14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
rs = gain / loss
df['rsi'] = 100 - (100 / (1 + rs))

# MACD
df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()
df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()
df['macd'] = df['ema12'] - df['ema26']
df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
df['macd_hist'] = df['macd'] - df['macd_signal']

# Fund flow features
df['main_net_cum'] = df['main_net'].cumsum()
df['main_net_ma5'] = df['main_net'].rolling(5).mean()
df['main_net_ma20'] = df['main_net'].rolling(20).mean()
df['super_net_cum'] = df['super_net'].cumsum()

# Price position
df['pct_from_ma20'] = (df['close'] - df['ma20']) / df['ma20'] * 100
df['pct_from_ma60'] = (df['close'] - df['ma60']) / df['ma60'] * 100

# High-low range
df['hl_ratio'] = (df['high'] - df['low']) / df['close']

print(f'  Features added: {len(df.columns)} columns')
print(f'  Date range: {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')

# ========== STEP 3: STRATEGY DEFINITIONS ==========
print('\n' + '='*60)
print('STEP 3: Strategy Definitions')
print('='*60)

# Drop NaN rows (from rolling calculations)
df_clean = df.dropna().reset_index(drop=True)
print(f'  Clean data: {len(df_clean)} rows (dropped NaN)')

class BacktestResult:
    def __init__(self, name, signals, df):
        self.name = name
        self.df = df
        self.signals = signals
        self.trades = []
        self.equity_curve = []
        self.metrics = {}

def run_backtest(name, df, signal_func):
    """Run a backtest given a signal function that returns 1(buy)/0(hold)/-1(sell)"""
    signals = []
    positions = []
    equity = [INITIAL_CAPITAL]
    cash = INITIAL_CAPITAL
    shares = 0
    trades = []
    in_position = False
    entry_price = 0
    entry_idx = 0

    for i in range(len(df)):
        row = df.iloc[i]
        price = row['close']
        sig = signal_func(df, i)

        if sig is None:
            sig = 0
        signals.append(sig)

        # Execute trades
        if sig == 1 and not in_position and cash > price * 100:
            # Buy
            trade_cash = cash * 0.95  # use 95% of cash
            cost = trade_cash * (1 + COMMISSION + SLIPPAGE)
            shares = cost / price
            cash -= cost
            in_position = True
            entry_price = price
            entry_idx = i
            trades.append({'type': 'BUY', 'date': row['date'], 'price': price, 'shares': shares})

        elif sig == -1 and in_position:
            # Sell
            proceeds = shares * price * (1 - COMMISSION - SLIPPAGE)
            cash += proceeds
            return_pct = (price / entry_price - 1) * 100
            trades.append({'type': 'SELL', 'date': row['date'], 'price': price,
                          'shares': shares, 'return_pct': return_pct,
                          'hold_days': i - entry_idx})
            shares = 0
            in_position = False

        # Calculate equity
        mark_to_market = shares * price if in_position else 0
        total_equity = cash + mark_to_market
        equity.append(total_equity)
        positions.append(1 if in_position else 0)

    # Force close at end
    if in_position:
        proceeds = shares * df.iloc[-1]['close'] * (1 - COMMISSION - SLIPPAGE)
        cash += proceeds
        trades.append({'type': 'SELL(FINAL)', 'date': df.iloc[-1]['date'],
                      'price': df.iloc[-1]['close'], 'shares': shares,
                      'return_pct': (df.iloc[-1]['close'] / entry_price - 1) * 100,
                      'hold_days': len(df) - 1 - entry_idx})
        equity[-1] = cash
        positions[-1] = 0

    # Compute metrics
    equity_series = pd.Series(equity)
    returns_series = equity_series.pct_change().dropna()

    total_return = (equity[-1] / INITIAL_CAPITAL - 1) * 100
    n_trades = len([t for t in trades if 'SELL' in t['type']])
    win_trades = len([t for t in trades if t.get('return_pct', 0) > 0])
    win_rate = (win_trades / n_trades * 100) if n_trades > 0 else 0

    # Annualized metrics
    trading_days = len(df)
    years = trading_days / 252
    annual_return = ((1 + total_return/100) ** (1/years) - 1) * 100 if years > 0 else 0
    sharpe = (returns_series.mean() / returns_series.std() * np.sqrt(252)) if returns_series.std() > 0 else 0

    # Max drawdown
    cummax = equity_series.cummax()
    drawdowns = (equity_series - cummax) / cummax * 100
    max_dd = drawdowns.min()

    # Calmar ratio
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0

    metrics = {
        'Total Return': f'{total_return:.1f}%',
        'Annual Return': f'{annual_return:.1f}%',
        'Sharpe Ratio': f'{sharpe:.2f}',
        'Max Drawdown': f'{max_dd:.1f}%',
        'Calmar Ratio': f'{calmar:.2f}',
        'Total Trades': n_trades,
        'Win Rate': f'{win_rate:.1f}%',
        'Final Equity': f'{equity[-1]:,.0f}',
    }

    return {
        'name': name,
        'signals': signals,
        'positions': positions,
        'equity': equity,
        'trades': trades,
        'metrics': metrics,
        'total_return': total_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
    }

# ========== STRATEGY 1: MA Crossover ==========
def strategy_ma_cross(df, i):
    """MA5/MA20 golden cross"""
    if i < 1: return 0
    prev = df.iloc[i-1]
    curr = df.iloc[i]
    # Golden cross: MA5 crosses above MA20
    if prev['ma5'] <= prev['ma20'] and curr['ma5'] > curr['ma20']:
        return 1
    # Death cross: MA5 crosses below MA20
    elif prev['ma5'] >= prev['ma20'] and curr['ma5'] < curr['ma20']:
        return -1
    return 0

# ========== STRATEGY 2: Bollinger Band Mean Reversion ==========
def strategy_bb(df, i):
    """Buy at lower band, sell at upper band"""
    if i < 1: return 0
    curr = df.iloc[i]
    # Oversold: price hits lower band + RSI < 30
    if curr['close'] <= curr['bb_lower'] * 1.02 and curr['rsi'] < 35:
        return 1
    # Overbought: price hits upper band + RSI > 70
    elif curr['close'] >= curr['bb_upper'] * 0.98 and curr['rsi'] > 65:
        return -1
    return 0

# ========== STRATEGY 3: Volume-Price Breakout ==========
def strategy_volume_breakout(df, i):
    """Volume expansion + price breaking MA20"""
    if i < 1: return 0
    curr = df.iloc[i]
    # Breakout: volume > 1.5x avg AND price > MA20 AND close > open
    if (curr['volume_ratio'] > 1.5 and
        curr['close'] > curr['ma20'] and
        curr['close'] > curr['open'] and
        curr['rsi'] > 40 and curr['rsi'] < 70):
        return 1
    # Exit: volume spikes but price drops below MA10
    elif (curr['volume_ratio'] > 2.0 and
          curr['close'] < curr['ma10'] and
          curr['close'] < curr['open']):
        return -1
    return 0

# ========== STRATEGY 4: Fund Flow Momentum ==========
def strategy_fund_flow(df, i):
    """Follow the smart money"""
    if i < 5: return 0
    curr = df.iloc[i]
    # Entry: 5-day main net inflow accelerating + price above MA20
    recent_flow = df['main_net'].iloc[max(0,i-5):i+1].sum()
    prev_flow = df['main_net'].iloc[max(0,i-10):max(0,i-5)].sum()
    flow_accelerating = recent_flow > prev_flow

    if (curr['main_net_ma5'] > 0 and
        flow_accelerating and
        curr['close'] > curr['ma20'] and
        curr['main_net'] > 0):
        return 1
    # Exit: main net outflow > 1亿 for 3 consecutive days
    elif (df['main_net'].iloc[max(0,i-2):i+1].sum() < -200_000_000 and
          curr['close'] < curr['ma10']):
        return -1
    return 0

# ========== STRATEGY 5: Composite (Weighted Multi-Signal) ==========
def strategy_composite(df, i):
    """Combine all signals with weights"""
    s1 = strategy_ma_cross(df, i)
    s2 = strategy_bb(df, i)
    s3 = strategy_volume_breakout(df, i)
    s4 = strategy_fund_flow(df, i)

    # Score: buy signals positive, sell signals negative
    score = s1 * 0.25 + s2 * 0.15 + s3 * 0.35 + s4 * 0.25

    if score >= 0.5: return 1
    elif score <= -0.5: return -1
    return 0

# ========== STRATEGY 6: Trend Following with Trailing Stop ==========
class TrailState:
    def __init__(self): self.high = 0
_trail_state = TrailState()

def strategy_trend_trail(df, i):
    """Trend following with ATR-based trailing stop"""
    state = _trail_state
    if i < 20: return 0
    curr = df.iloc[i]

    # Entry: Price > MA20 + MA60 + MA5 > MA20 (strong trend)
    if (curr['close'] > curr['ma20'] > curr['ma60'] and
        curr['ma5'] > curr['ma20'] and
        curr['volume_ratio'] > 1.2 and
        curr['macd_hist'] > 0):
        state.high = max(state.high, curr['close'])
        return 1
    # Exit: trail stop (close below highest high - 2*ATR)
    elif state.high > 0 and curr['close'] < state.high - 2 * curr['atr']:
        state.high = 0
        return -1
    # Exit: MA5 crosses below MA20
    elif i > 0:
        prev = df.iloc[i-1]
        if prev['ma5'] >= prev['ma20'] and curr['ma5'] < curr['ma20']:
            state.high = 0
            return -1
    return 0

# ========== STEP 4: RUN BACKTESTS ==========
print('\n' + '='*60)
print('STEP 4: Running Backtests')
print('='*60)

strategies = [
    ('S1_MA_Cross', strategy_ma_cross),
    ('S2_Bollinger', strategy_bb),
    ('S3_Vol_Breakout', strategy_volume_breakout),
    ('S4_Fund_Flow', strategy_fund_flow),
    ('S5_Composite', strategy_composite),
    ('S6_Trend_Trail', strategy_trend_trail),
]

results = {}
for name, func in strategies:
    _trail_state.high = 0
    res = run_backtest(name, df_clean, func)
    results[name] = res
    print(f'  {name}: Return={res["metrics"]["Total Return"]}, Sharpe={res["metrics"]["Sharpe Ratio"]}, MaxDD={res["metrics"]["Max Drawdown"]}, Trades={res["metrics"]["Total Trades"]}, WinRate={res["metrics"]["Win Rate"]}')

# Buy & Hold benchmark
bh_return = (df_clean['close'].iloc[-1] / df_clean['close'].iloc[0] - 1) * 100
bh_returns = df_clean['close'].pct_change().dropna()
bh_sharpe = (bh_returns.mean() / bh_returns.std() * np.sqrt(252)) if bh_returns.std() > 0 else 0
bh_equity = [INITIAL_CAPITAL * (1 + bh_return/100)]
bh_maxdd = ((df_clean['close'] / df_clean['close'].cummax() - 1) * 100).min()
print(f'\n  Buy&Hold: Return={bh_return:.1f}%, Sharpe={bh_sharpe:.2f}, MaxDD={bh_maxdd:.1f}%')

# ========== STEP 5: GENERATE CHARTS ==========
print('\n' + '='*60)
print('STEP 5: Generating Charts')
print('='*60)

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    # Set Chinese font
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    dates = pd.to_datetime(df_clean['date'])
    n = len(df_clean)

    # ---- Chart 1: Price + MA + Signals (Best Strategy) ----
    fig, axes = plt.subplots(3, 1, figsize=(16, 12), gridspec_kw={'height_ratios': [3, 1, 1]})

    best = results['S5_Composite']
    ax = axes[0]
    ax.plot(dates, df_clean['close'], color='#333333', linewidth=1.2, alpha=0.8, label='Close')
    ax.plot(dates, df_clean['ma5'], color='#FF6B35', linewidth=0.8, alpha=0.6, label='MA5')
    ax.plot(dates, df_clean['ma20'], color='#004E89', linewidth=0.8, alpha=0.6, label='MA20')
    ax.plot(dates, df_clean['ma60'], color='#1B998B', linewidth=0.8, alpha=0.5, label='MA60')

    # Mark buy/sell signals
    buy_dates, buy_prices = [], []
    sell_dates, sell_prices = [], []
    for t in best['trades']:
        idx = df_clean[df_clean['date'] == t['date']].index
        if len(idx) == 0: continue
        i = idx[0]
        if 'BUY' in t['type']:
            buy_dates.append(dates.iloc[i])
            buy_prices.append(t['price'])
        else:
            sell_dates.append(dates.iloc[i])
            sell_prices.append(t['price'])

    ax.scatter(buy_dates, buy_prices, color='#D62828', marker='^', s=80, zorder=5, label=f'Buy({len(buy_dates)})')
    ax.scatter(sell_dates, sell_prices, color='#1B998B', marker='v', s=80, zorder=5, label=f'Sell({len(sell_dates)})')

    ax.set_title(f'{NAME}({CODE}) - S5 Composite Strategy Signals', fontsize=14, fontweight='bold')
    ax.set_ylabel('Price (CNY)', fontsize=11)
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)

    # Volume subplot
    ax2 = axes[1]
    colors = ['#D62828' if df_clean['close'].iloc[i] >= df_clean['open'].iloc[i] else '#1B998B' for i in range(n)]
    ax2.bar(dates, df_clean['volume'] / 1e6, color=colors, alpha=0.6, width=1)
    ax2.set_ylabel('Volume (M)', fontsize=10)
    ax2.grid(True, alpha=0.3)

    # Fund flow subplot
    ax3 = axes[2]
    ax3.fill_between(dates, df_clean['main_net'] / 1e8, 0,
                     where=df_clean['main_net'] >= 0, color='#D62828', alpha=0.5, label='Inflow')
    ax3.fill_between(dates, df_clean['main_net'] / 1e8, 0,
                     where=df_clean['main_net'] < 0, color='#1B998B', alpha=0.5, label='Outflow')
    ax3.plot(dates, df_clean['main_net_ma5'] / 1e8, color='#FF6B35', linewidth=0.8, label='MA5 Flow')
    ax3.axhline(y=0, color='#333333', linewidth=0.5)
    ax3.set_ylabel('Main Net (Yi CNY)', fontsize=10)
    ax3.set_xlabel('Date', fontsize=11)
    ax3.legend(loc='upper left', fontsize=9)
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    chart1_path = os.path.join(DATA_DIR, 'chart1_price_signals.png')
    plt.savefig(chart1_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart1_price_signals.png')

    # ---- Chart 2: Equity Curves Comparison ----
    fig, axes = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={'height_ratios': [3, 1]})

    ax = axes[0]
    colors = ['#D62828', '#004E89', '#1B998B', '#FF6B35', '#6A4C93', '#1982C4']
    for idx, (name, res) in enumerate(results.items()):
        equity_arr = np.array(res['equity'])
        eq_dates = pd.to_datetime([df_clean['date'].iloc[0]] + list(df_clean['date']))
        if len(equity_arr) == len(eq_dates):
            ax.plot(eq_dates, equity_arr / INITIAL_CAPITAL * 100, color=colors[idx],
                    linewidth=1.5, alpha=0.85, label=f'{name} ({res["metrics"]["Total Return"]})')

    # Buy & Hold
    bh_eq = INITIAL_CAPITAL * df_clean['close'] / df_clean['close'].iloc[0]
    ax.plot(dates, bh_eq / INITIAL_CAPITAL * 100, '--', color='#333333', linewidth=2, alpha=0.7, label=f'Buy&Hold ({bh_return:.1f}%)')

    ax.axhline(y=100, color='gray', linewidth=0.5, linestyle=':')
    ax.set_title(f'{NAME}({CODE}) - Strategy Equity Curves Comparison', fontsize=14, fontweight='bold')
    ax.set_ylabel('Equity (% of Initial)', fontsize=11)
    ax.legend(loc='upper left', fontsize=8, ncol=2)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.0f}%'))

    # Drawdown subplot
    ax2 = axes[1]
    for idx, (name, res) in enumerate(results.items()):
        equity_arr = np.array(res['equity'])
        eq_dates = pd.to_datetime([df_clean['date'].iloc[0]] + list(df_clean['date']))
        if len(equity_arr) == len(eq_dates):
            cummax = np.maximum.accumulate(equity_arr)
            dd = (equity_arr - cummax) / cummax * 100
            ax2.fill_between(eq_dates, dd, 0, color=colors[idx], alpha=0.3, label=name)

    ax2.set_ylabel('Drawdown %', fontsize=10)
    ax2.set_xlabel('Date', fontsize=11)
    ax2.grid(True, alpha=0.3)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.0f}%'))

    plt.tight_layout()
    chart2_path = os.path.join(DATA_DIR, 'chart2_equity_curves.png')
    plt.savefig(chart2_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart2_equity_curves.png')

    # ---- Chart 3: Best Strategy Full Analysis ----
    fig, axes = plt.subplots(4, 1, figsize=(16, 14), gridspec_kw={'height_ratios': [2.5, 1.5, 1.5, 1.5]})

    best = results['S5_Composite']
    best_eq = np.array(best['equity'])
    best_dates = pd.to_datetime([df_clean['date'].iloc[0]] + list(df_clean['date']))

    # Price + entry/exit
    ax = axes[0]
    ax.plot(dates, df_clean['close'], color='#333333', linewidth=1, alpha=0.7)
    for t in best['trades']:
        idx = df_clean[df_clean['date'] == t['date']].index
        if len(idx) == 0: continue
        i = idx[0]
        if 'BUY' in t['type']:
            ax.axvline(x=dates.iloc[i], color='#D62828', alpha=0.3, linewidth=0.8)
        else:
            ax.axvline(x=dates.iloc[i], color='#1B998B', alpha=0.3, linewidth=0.8)
            ret = t.get('return_pct', 0)
            color = '#D62828' if ret > 0 else '#1B998B'
            ax.annotate(f'{ret:+.1f}%', (dates.iloc[i], df_clean['close'].iloc[i]),
                       textcoords='offset points', xytext=(5, 10), fontsize=7, color=color)

    ax.set_title(f'Best Strategy: S5_Composite - Trade Analysis', fontsize=13, fontweight='bold')
    ax.set_ylabel('Price', fontsize=10)
    ax.grid(True, alpha=0.3)

    # Equity
    ax2 = axes[1]
    ax2.plot(best_dates, best_eq / INITIAL_CAPITAL * 100, color='#004E89', linewidth=1.5)
    ax2.fill_between(best_dates, best_eq / INITIAL_CAPITAL * 100, 100, alpha=0.15, color='#004E89')
    ax2.axhline(y=100, color='gray', linewidth=0.5, linestyle=':')
    ax2.set_ylabel('Equity %', fontsize=10)
    ax2.grid(True, alpha=0.3)

    # Drawdown
    ax3 = axes[2]
    cummax = np.maximum.accumulate(best_eq)
    dd = (best_eq - cummax) / cummax * 100
    ax3.fill_between(best_dates, dd, 0, color='#D62828', alpha=0.4)
    ax3.set_ylabel('Drawdown %', fontsize=10)
    ax3.grid(True, alpha=0.3)

    # Position + RSI
    ax4 = axes[3]
    ax4.fill_between(dates, best['positions'][:len(dates)], 0, color='#004E89', alpha=0.4, label='Position')
    ax4_2 = ax4.twinx()
    ax4_2.plot(dates, df_clean['rsi'], color='#FF6B35', linewidth=0.8, alpha=0.6, label='RSI')
    ax4_2.axhline(y=70, color='#D62828', linewidth=0.5, linestyle='--', alpha=0.5)
    ax4_2.axhline(y=30, color='#1B998B', linewidth=0.5, linestyle='--', alpha=0.5)
    ax4.set_ylabel('Position', fontsize=10)
    ax4_2.set_ylabel('RSI', fontsize=10)
    ax4.set_xlabel('Date', fontsize=11)
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    chart3_path = os.path.join(DATA_DIR, 'chart3_best_strategy_detail.png')
    plt.savefig(chart3_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart3_best_strategy_detail.png')

    # ---- Chart 4: Trade Distribution ----
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Trade returns histogram
    trade_returns = [t.get('return_pct', 0) for t in best['trades'] if 'SELL' in t['type']]
    ax = axes[0]
    if trade_returns:
        colors_bar = ['#D62828' if r > 0 else '#1B998B' for r in trade_returns]
        bars = ax.bar(range(len(trade_returns)), trade_returns, color=colors_bar, alpha=0.7)
        ax.axhline(y=0, color='#333333', linewidth=0.5)
        ax.set_title(f'Trade Returns Distribution (n={len(trade_returns)})', fontsize=12, fontweight='bold')
        ax.set_ylabel('Return %', fontsize=10)
        ax.set_xlabel('Trade #', fontsize=10)
        ax.grid(True, alpha=0.3, axis='y')
        # Add avg line
        avg_ret = np.mean(trade_returns)
        ax.axhline(y=avg_ret, color='#FF6B35', linewidth=1, linestyle='--', label=f'Avg: {avg_ret:+.1f}%')
        ax.legend(fontsize=9)

    # Strategy comparison bar chart
    ax2 = axes[1]
    names_list = []
    returns_list = []
    sharpes_list = []
    for name, res in results.items():
        names_list.append(name)
        returns_list.append(res['total_return'])
        sharpes_list.append(res['sharpe'])
    names_list.append('Buy&Hold')
    returns_list.append(bh_return)
    sharpes_list.append(bh_sharpe)

    x_pos = range(len(names_list))
    bar_colors = ['#D62828' if r > 0 else '#1B998B' for r in returns_list]
    bars = ax2.bar(x_pos, returns_list, color=bar_colors, alpha=0.75, edgecolor='white')
    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(names_list, rotation=45, ha='right', fontsize=8)
    ax2.set_title('Strategy Total Return Comparison', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Total Return %', fontsize=10)
    ax2.axhline(y=bh_return, color='#333333', linewidth=1, linestyle='--', alpha=0.5, label=f'B&H: {bh_return:.1f}%')
    ax2.grid(True, alpha=0.3, axis='y')
    # Add value labels
    for bar, ret in zip(bars, returns_list):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2,
                f'{ret:.1f}%', ha='center', fontsize=8, fontweight='bold')

    plt.tight_layout()
    chart4_path = os.path.join(DATA_DIR, 'chart4_trade_analysis.png')
    plt.savefig(chart4_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'  Saved: chart4_trade_analysis.png')

    CHARTS_OK = True
except Exception as e:
    print(f'  Chart generation error: {e}')
    CHARTS_OK = False

# ========== STEP 6: SAVE BACKTEST DATA ==========
print('\n' + '='*60)
print('STEP 6: Saving Backtest Results')
print('='*60)

# Save trades
for name, res in results.items():
    trades_path = os.path.join(DATA_DIR, f'trades_{name}.csv')
    with open(trades_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['type', 'date', 'price', 'shares', 'return_pct', 'hold_days'])
        for t in res['trades']:
            writer.writerow([t['type'], t['date'], f"{t['price']:.2f}",
                           f"{t.get('shares', 0):.0f}",
                           f"{t.get('return_pct', 0):.1f}" if t.get('return_pct') else '',
                           t.get('hold_days', '')])
    print(f'  Saved: trades_{name}.csv')

# Save equity curves
equity_df = pd.DataFrame({'date': list(df_clean['date'])})
for name, res in results.items():
    eq = res['equity']
    equity_df[name] = eq[:len(equity_df)] if len(eq) != len(equity_df) + 1 else eq[1:]
equity_df['Buy_Hold'] = INITIAL_CAPITAL * df_clean['close'] / df_clean['close'].iloc[0]
equity_df.to_csv(os.path.join(DATA_DIR, 'equity_curves.csv'), index=False, encoding='utf-8-sig')
print(f'  Saved: equity_curves.csv')

# ========== STEP 7: OUTPUT SUMMARY JSON ==========
summary = {
    'stock': {'code': CODE, 'name': NAME},
    'data_period': {'start': df_clean['date'].iloc[0], 'end': df_clean['date'].iloc[-1], 'bars': len(df_clean)},
    'benchmark': {'buy_hold_return': f'{bh_return:.1f}%', 'buy_hold_sharpe': f'{bh_sharpe:.2f}', 'buy_hold_maxdd': f'{bh_maxdd:.1f}%'},
    'strategies': {}
}
for name, res in results.items():
    summary['strategies'][name] = res['metrics']

summary_path = os.path.join(DATA_DIR, 'backtest_summary.json')
with open(summary_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f'  Saved: backtest_summary.json')

# ========== PRINT FINAL SUMMARY ==========
print('\n' + '='*60)
print('BACKTEST RESULTS SUMMARY')
print('='*60)
print(f'\nStock: {NAME}({CODE})')
print(f'Period: {df_clean["date"].iloc[0]} ~ {df_clean["date"].iloc[-1]} ({len(df_clean)} trading days)')
print(f'Initial Capital: {INITIAL_CAPITAL:,.0f} CNY')
print(f'\n{"Strategy":<20} {"Return":>10} {"Annual":>10} {"Sharpe":>8} {"MaxDD":>8} {"Trades":>7} {"WinRate":>8}')
print('-' * 75)
for name, res in results.items():
    m = res['metrics']
    print(f'{name:<20} {m["Total Return"]:>10} {m["Annual Return"]:>10} {m["Sharpe Ratio"]:>8} {m["Max Drawdown"]:>8} {m["Total Trades"]:>7} {m["Win Rate"]:>8}')
print(f'{"Buy & Hold":<20} {bh_return:>9.1f}% {"N/A":>10} {bh_sharpe:>8.2f} {bh_maxdd:>7.1f}% {"N/A":>7} {"N/A":>8}')

best_strategy = max(results.items(), key=lambda x: x[1]['total_return'])
print(f'\n>>> BEST STRATEGY: {best_strategy[0]} (Return: {best_strategy[1]["metrics"]["Total Return"]})')

print(f'\nAll outputs saved to: {DATA_DIR}')
for f in sorted(os.listdir(DATA_DIR)):
    size = os.path.getsize(os.path.join(DATA_DIR, f))
    print(f'  {f} ({size:,} bytes)')

print('\nDONE.')
