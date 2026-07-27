# -*- coding: utf-8 -*-
"""
Signal Validation Backtest: Test the 5 top signals discovered from June 24-25 analysis
against 3+ years of historical data for 601869 & 600487.
"""
import pandas as pd
import numpy as np
import os

OUT = 'output/turnpoint_analysis'
os.makedirs(OUT, exist_ok=True)

# ============ 1. Load & prepare ============
codes = ['601869', '600487']
names = {'601869': '长飞光纤', '600487': '亨通光电'}
all_data = {}

for code in codes:
    df = pd.read_csv(f'{OUT}/{code}_long.csv')
    df['date'] = pd.to_datetime(df['date'])
    all_data[code] = df
    print(f'{code} ({names[code]}): {len(df)} rows, {df["date"].min().date()} ~ {df["date"].max().date()}')

# ============ 2. Compute all indicators ============
def add_all_indicators(df):
    d = df.copy()
    c, h, l, o, v = d['close'], d['high'], d['low'], d['open'], d['vol']

    d['ret'] = c.pct_change() * 100

    # MAs
    d['ma5'] = c.rolling(5).mean()
    d['ma10'] = c.rolling(10).mean()
    d['ma20'] = c.rolling(20).mean()
    d['ma60'] = c.rolling(60).mean()
    d['pct_ma20'] = (c - d['ma20']) / d['ma20'] * 100
    d['pct_ma60'] = (c - d['ma60']) / d['ma60'] * 100

    # Volume
    d['vol_ma5'] = v.rolling(5).mean()
    d['vol_ma20'] = v.rolling(20).mean()
    d['vol_ratio'] = v / d['vol_ma20']

    # ATR(14)
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    d['atr'] = tr.rolling(14).mean()
    d['atr_pct'] = d['atr'] / c * 100

    # RSI(14)
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    d['rsi'] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    d['macd'] = ema12 - ema26
    d['macd_signal'] = d['macd'].ewm(span=9, adjust=False).mean()
    d['macd_hist'] = d['macd'] - d['macd_signal']
    d['macd_hist_chg'] = d['macd_hist'].diff()

    # KDJ
    n = 9
    low_n = l.rolling(n).min()
    high_n = h.rolling(n).max()
    rsv = (c - low_n) / (high_n - low_n) * 100
    d['k'] = rsv.ewm(com=2, adjust=False).mean()
    d['d'] = d['k'].ewm(com=2, adjust=False).mean()
    d['j'] = 3 * d['k'] - 2 * d['d']

    # Bollinger
    d['bb_mid'] = d['ma20']
    d['bb_std'] = c.rolling(20).std()
    d['bb_upper'] = d['bb_mid'] + 2 * d['bb_std']
    d['bb_lower'] = d['bb_mid'] - 2 * d['bb_std']
    d['bb_pos'] = (c - d['bb_lower']) / (d['bb_upper'] - d['bb_lower'])

    # Cumulative returns
    d['cumret_5d'] = d['ret'].rolling(5).sum()
    d['cumret_10d'] = d['ret'].rolling(10).sum()
    d['cumret_20d'] = d['ret'].rolling(20).sum()

    # Ret acceleration
    d['ret_accel'] = d['ret'].diff()

    # Up days count (recent 10)
    d['up_count_10d'] = (d['ret'] > 0).rolling(10).sum()

    # RSI peak tracking for divergence detection
    d['rsi_peak_5d'] = d['rsi'].rolling(5).max()
    d['close_peak_5d'] = d['close'].rolling(5).max()

    # Volume trend (5d vs 20d ago)
    d['vol_trend'] = d['vol_ma5'] / d['vol_ma20']

    # MFI
    tp = (h + l + c) / 3
    rmf = tp * v
    pos_mf = rmf.where(tp > tp.shift(1), 0)
    neg_mf = rmf.where(tp < tp.shift(1), 0)
    mfr = pos_mf.rolling(14).sum() / neg_mf.rolling(14).sum()
    d['mfi'] = 100 - (100 / (1 + mfr))

    return d

for code in codes:
    all_data[code] = add_all_indicators(all_data[code])
    print(f'{code}: indicators computed')

# ============ 3. Define Signals ============

def detect_signals(df):
    """
    Detect the 5 top signals. Each signal returns a boolean Series.
    We require price to be in an uptrend context (above MA60).
    """
    d = df.copy()
    n = len(d)

    # Trend context: price must be above MA60 (bull market)
    uptrend = d['close'] > d['ma60']

    # Cumulative return over past 20 days must be strongly positive (>15%)
    strong_uptrend = d['cumret_20d'] > 15

    # === Signal 1: Price-MA20 extreme deviation (>25%) ===
    d['sig1_ma20_extreme'] = (d['pct_ma20'] > 25) & uptrend

    # === Signal 2: RSI bearish divergence ===
    # Price makes a 5-day high BUT RSI is lower than its 5-day peak by >=3 points
    price_new_high = d['close'] >= d['close_peak_5d'].shift(1)
    rsi_diverging = d['rsi'] < (d['rsi_peak_5d'].shift(1) - 3)
    d['sig2_rsi_divergence'] = price_new_high & rsi_diverging & uptrend & (d['rsi'] > 60)

    # === Signal 3: Volume contraction while price rising ===
    # Price up, vol_ratio < 0.8, and vol is declining
    price_up = d['ret'] > 0
    vol_low = d['vol_ratio'] < 0.8
    vol_declining = d['vol_ma5'] < d['vol_ma20']
    d['sig3_vol_contraction'] = price_up & vol_low & vol_declining & uptrend

    # === Signal 4: MACD histogram stalling ===
    # MACD_hist change near zero (< 0.1% of price) while price is rising
    macd_stalling = (d['macd_hist_chg'].abs() < d['close'] * 0.001)
    d['sig4_macd_stall'] = macd_stalling & price_up & uptrend & (d['rsi'] > 55)

    # === Signal 5: Return acceleration negative while price still up ===
    # ret > 0 but ret_accel < -3
    d['sig5_ret_decel'] = (d['ret'] > 0) & (d['ret_accel'] < -3) & uptrend

    # === Composite Signal: at least 3 of 5 signals fire ===
    sig_cols = ['sig1_ma20_extreme', 'sig2_rsi_divergence', 'sig3_vol_contraction',
                'sig4_macd_stall', 'sig5_ret_decel']
    d['sig_composite'] = d[sig_cols].sum(axis=1) >= 3
    d['sig_count'] = d[sig_cols].sum(axis=1)

    return d

for code in codes:
    all_data[code] = detect_signals(all_data[code])
    print(f'{code}: signals detected')

# ============ 4. Forward Returns Analysis ============

def analyze_signals(df, code, name):
    """For each signal, compute forward returns at multiple horizons."""
    horizons = [1, 3, 5, 10, 20]
    signal_names = {
        'sig1_ma20_extreme': '信号1: 价格-MA20极端乖离(>25%)',
        'sig2_rsi_divergence': '信号2: RSI顶背离',
        'sig3_vol_contraction': '信号3: 缩量上涨(量价背离)',
        'sig4_macd_stall': '信号4: MACD柱停滞',
        'sig5_ret_decel': '信号5: 涨幅加速度转负',
        'sig_composite': '综合信号(≥3个同时触发)',
    }

    results = []

    for sig_col, sig_name in signal_names.items():
        sig_dates = df[df[sig_col] == True]

        if len(sig_dates) == 0:
            results.append({'股票': name, '信号': sig_name, '触发次数': 0,
                           '备注': '历史无触发'})
            continue

        row = {'股票': name, '信号': sig_name, '触发次数': len(sig_dates)}

        for h in horizons:
            fwd_ret_col = f'fwd_{h}d'
            # Compute forward returns for signal days
            fwd_rets = []
            for idx in sig_dates.index:
                if idx + h < len(df):
                    ret = (df.loc[idx + h, 'close'] / df.loc[idx, 'close'] - 1) * 100
                    fwd_rets.append(ret)

            if fwd_rets:
                fwd_rets = np.array(fwd_rets)
                row[f'{h}日_平均收益%'] = round(np.mean(fwd_rets), 2)
                row[f'{h}日_收益中位数%'] = round(np.median(fwd_rets), 2)
                row[f'{h}日_下跌概率%'] = round((fwd_rets < 0).mean() * 100, 1)
                row[f'{h}日_最大跌幅%'] = round(np.min(fwd_rets), 2)
                row[f'{h}日_最大涨幅%'] = round(np.max(fwd_rets), 2)
                row[f'{h}日_收益标准差'] = round(np.std(fwd_rets), 2)

        results.append(row)

    return pd.DataFrame(results)

# ============ 5. Baseline: random days in uptrend ============

def analyze_baseline(df, code, name):
    """Random sampling of days in similar uptrend context as control."""
    horizons = [1, 3, 5, 10, 20]
    uptrend = df['close'] > df['ma60']
    eligible = df[uptrend].copy()

    if len(eligible) < 50:
        return pd.DataFrame()

    # Random sample 1000 times (or all eligible days if fewer)
    np.random.seed(42)
    n_samples = min(1000, len(eligible))
    sampled_indices = np.random.choice(eligible.index, size=n_samples, replace=False)

    row = {'股票': name, '信号': '随机对照(上升趋势中随机买入)', '触发次数': n_samples}
    for h in horizons:
        fwd_rets = []
        for idx in sampled_indices:
            if idx + h < len(df):
                ret = (df.loc[idx + h, 'close'] / df.loc[idx, 'close'] - 1) * 100
                fwd_rets.append(ret)
        if fwd_rets:
            fwd_rets = np.array(fwd_rets)
            row[f'{h}日_平均收益%'] = round(np.mean(fwd_rets), 2)
            row[f'{h}日_收益中位数%'] = round(np.median(fwd_rets), 2)
            row[f'{h}日_下跌概率%'] = round((fwd_rets < 0).mean() * 100, 1)
            row[f'{h}日_最大跌幅%'] = round(np.min(fwd_rets), 2)
            row[f'{h}日_最大涨幅%'] = round(np.max(fwd_rets), 2)
            row[f'{h}日_收益标准差'] = round(np.std(fwd_rets), 2)

    return pd.DataFrame([row])

# ============ 6. Run Analysis ============
all_results = []

for code in codes:
    df = all_data[code]
    name = names[code]

    # Signal analysis
    signal_results = analyze_signals(df, code, name)
    all_results.append(signal_results)

    # Baseline
    baseline = analyze_baseline(df, code, name)
    if not baseline.empty:
        all_results.append(baseline)

    # Print signal trigger dates
    print(f'\n===== {name} ({code}) Signal Trigger Dates =====')
    for sig_col, sig_name in [
        ('sig1_ma20_extreme', '信号1: MA20乖离>25%'),
        ('sig2_rsi_divergence', '信号2: RSI顶背离'),
        ('sig3_vol_contraction', '信号3: 缩量上涨'),
        ('sig4_macd_stall', '信号4: MACD柱停滞'),
        ('sig5_ret_decel', '信号5: 涨幅加速度转负'),
        ('sig_composite', '综合信号(≥3)'),
    ]:
        sig_df = df[df[sig_col] == True]
        if len(sig_df) > 0:
            dates_str = ', '.join([d.strftime('%Y-%m-%d') for d in sig_df['date']])
            print(f'  {sig_name}: {len(sig_df)}次 → {dates_str[:300]}')
        else:
            print(f'  {sig_name}: 0次')

# ============ 7. Combine & Save ============
final = pd.concat(all_results, ignore_index=True)

# Print nice table
print('\n\n' + '='*120)
print('信号有效性验证结果汇总')
print('='*120)

# Print by stock
for code in codes:
    name = names[code]
    stock_results = final[final['股票'] == name]
    print(f'\n### {name} ({code})')
    display_cols = [c for c in stock_results.columns if c != '股票']
    pd.set_option('display.max_columns', 30)
    pd.set_option('display.width', 300)
    pd.set_option('display.max_rows', 50)
    print(stock_results[display_cols].to_string(index=False))

# ============ 8. Detailed event log ============
print('\n\n' + '='*120)
print('每次信号触发的详细收益')
print('='*120)

for code in codes:
    df = all_data[code]
    name = names[code]
    print(f'\n### {name} ({code}) — 综合信号(≥3)触发详情')

    sig_df = df[df['sig_composite'] == True]
    if len(sig_df) == 0:
        print('  无触发')
        continue

    for _, row in sig_df.iterrows():
        d = row['date'].strftime('%Y-%m-%d')
        price = row['close']
        sig_count = int(row['sig_count'])
        # Which signals fired
        fired = []
        if row['sig1_ma20_extreme']: fired.append('MA20乖离')
        if row['sig2_rsi_divergence']: fired.append('RSI背离')
        if row['sig3_vol_contraction']: fired.append('缩量上涨')
        if row['sig4_macd_stall']: fired.append('MACD停滞')
        if row['sig5_ret_decel']: fired.append('涨幅减速')

        # Forward returns
        fwd_str = ''
        for h in [1, 3, 5, 10, 20]:
            idx = row.name
            if idx + h < len(df):
                fwd_ret = (df.loc[idx + h, 'close'] / price - 1) * 100
                fwd_str += f'{h}d:{fwd_ret:+.1f}%  '
        print(f'  {d} | 价格:{price:.1f} | 触发{int(sig_count)}个({",".join(fired)}) | 后续: {fwd_str}')

# Save
final.to_csv(f'{OUT}/signal_validation_results.csv', index=False, encoding='utf-8-sig')
print(f'\n\nResults saved to {OUT}/signal_validation_results.csv')
