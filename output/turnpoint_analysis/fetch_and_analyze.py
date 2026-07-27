# -*- coding: utf-8 -*-
"""Fetch K-line, compute indicators, money flow for 601869 & 600487"""
import urllib.request
import json
import pandas as pd
import numpy as np
import os
import time
import random
import requests

os.makedirs('output/turnpoint_analysis', exist_ok=True)

# ============ Tencent daily K-line ============
def tencent_kline(code, period='day', count=200):
    prefix = 'sh' if code.startswith(('6','9')) else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_{period}&param={prefix}{code},{period},,,{count},qfq'
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode('gbk')
    json_str = data.split('=', 1)[1].strip() if '=' in data else data
    d = json.loads(json_str)
    stock_key = f'{prefix}{code}'
    stock_data = d.get('data', {}).get(stock_key, {})
    klines = stock_data.get(f'qfq{period}', [])
    if not klines:
        for k in stock_data:
            if isinstance(stock_data[k], list) and len(stock_data[k]) > 0:
                klines = stock_data[k]
                break
    rows = []
    for item in klines:
        if len(item) >= 6:
            rows.append({'date': item[0], 'open': float(item[1]), 'close': float(item[2]),
                         'high': float(item[3]), 'low': float(item[4]), 'vol': float(item[5])})
    return pd.DataFrame(rows)

codes = ['601869', '600487']
names = {'601869': '长飞光纤', '600487': '亨通光电'}

all_data = {}
for code in codes:
    df = tencent_kline(code, 'day', 200)
    df = df[df['date'] >= '2026-04-01'].copy()
    all_data[code] = df
    print(f'{code}: {len(df)} rows from {df["date"].min()} to {df["date"].max()}')

# ============ Technical Indicators ============
def compute_indicators(df):
    df = df.copy()
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    vol = df['vol'].values

    # Returns
    df['ret'] = df['close'].pct_change() * 100

    # Volume indicators
    df['vol_ma5'] = df['vol'].rolling(5).mean()
    df['vol_ma20'] = df['vol'].rolling(20).mean()
    df['vol_ratio'] = df['vol'] / df['vol_ma20']
    df['vol_ratio_vs5'] = df['vol'] / df['vol_ma5']

    # Moving averages
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    df['pct_ma5'] = (df['close'] - df['ma5']) / df['ma5'] * 100
    df['pct_ma20'] = (df['close'] - df['ma20']) / df['ma20'] * 100

    # MA slope (acceleration)
    df['ma5_slope'] = df['ma5'].diff()
    df['ma10_slope'] = df['ma10'].diff()

    # ATR (14-day)
    tr = np.maximum(high - low, np.abs(high - np.roll(close, 1)))
    tr = np.maximum(tr, np.abs(low - np.roll(close, 1)))
    df['atr'] = pd.Series(tr).rolling(14).mean()
    df['atr_pct'] = df['atr'] / df['close'] * 100

    # RSI (14)
    delta = pd.Series(close).diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    df['macd_hist_change'] = df['macd_hist'].diff()

    # KDJ
    n = 9
    low_n = pd.Series(low).rolling(n).min()
    high_n = pd.Series(high).rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    df['k'] = rsv.ewm(com=2, adjust=False).mean()
    df['d'] = df['k'].ewm(com=2, adjust=False).mean()
    df['j'] = 3 * df['k'] - 2 * df['d']

    # Bollinger Bands
    df['bb_mid'] = df['ma20']
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid'] * 100
    df['bb_pos'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

    # OBV
    obv = [0]
    for i in range(1, len(close)):
        if close[i] > close[i-1]:
            obv.append(obv[-1] + vol[i])
        elif close[i] < close[i-1]:
            obv.append(obv[-1] - vol[i])
        else:
            obv.append(obv[-1])
    df['obv'] = obv

    # Money Flow Index (MFI, 14-day)
    typical = (high + low + close) / 3
    raw_mf = typical * vol
    pos_mf = np.where(typical > np.roll(typical, 1), raw_mf, 0)
    neg_mf = np.where(typical < np.roll(typical, 1), raw_mf, 0)
    pos_mf_sum = pd.Series(pos_mf).rolling(14).sum()
    neg_mf_sum = pd.Series(neg_mf).rolling(14).sum()
    mf_ratio = pos_mf_sum / neg_mf_sum
    df['mfi'] = 100 - (100 / (1 + mf_ratio))

    # High-low spread
    df['hl_spread'] = (df['high'] - df['low']) / df['close'] * 100

    # Upper/lower shadows
    body_high = np.maximum(df['open'], df['close'])
    body_low = np.minimum(df['open'], df['close'])
    df['upper_shadow'] = (df['high'] - body_high) / df['close'] * 100
    df['lower_shadow'] = (body_low - df['low']) / df['close'] * 100
    df['body_pct'] = (body_high - body_low) / df['close'] * 100

    # Amplitude
    df['amplitude'] = (df['high'] - df['low']) / df['open'] * 100

    # Cumulative returns
    df['cumret_5d'] = df['ret'].rolling(5).sum()
    df['cumret_10d'] = df['ret'].rolling(10).sum()

    # Consecutive changes
    df['up_day'] = (df['ret'] > 0).astype(int)
    df['consec_up'] = df['up_day'].groupby((df['up_day'] != df['up_day'].shift()).cumsum()).cumcount() + 1
    df['consec_up'] = df['consec_up'] * df['up_day']
    df['down_day'] = (df['ret'] < 0).astype(int)
    df['consec_down'] = df['down_day'].groupby((df['down_day'] != df['down_day'].shift()).cumsum()).cumcount() + 1
    df['consec_down'] = df['consec_down'] * df['down_day']

    # Price acceleration
    df['ret_accel'] = df['ret'].diff()

    # Turnover ratio: vol/shares approximation - use vol directly

    return df

for code in codes:
    all_data[code] = compute_indicators(all_data[code])
    print(f'{code}: indicators computed')

# ============ Money Flow (eastmoney push2his) ============
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

def stock_fund_flow_120d(code):
    market_code = 1 if code.startswith('6') else 0
    url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {
        'secid': f'{market_code}.{code}',
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
        'lmt': '120',
    }
    r = requests.get(url, params=params,
                     headers={'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'},
                     timeout=15)
    d = r.json()
    klines = d.get('data', {}).get('klines', [])
    rows = []
    for line in klines:
        parts = line.split(',')
        if len(parts) >= 7:
            rows.append({
                'date': parts[0],
                'main_net': float(parts[1]) if parts[1] != '-' else 0,
                'small_net': float(parts[2]) if parts[2] != '-' else 0,
                'mid_net': float(parts[3]) if parts[3] != '-' else 0,
                'large_net': float(parts[4]) if parts[4] != '-' else 0,
                'super_net': float(parts[5]) if parts[5] != '-' else 0,
            })
    return pd.DataFrame(rows)

for code in codes:
    try:
        df_flow = stock_fund_flow_120d(code)
        if not df_flow.empty:
            all_data[code] = all_data[code].merge(df_flow, on='date', how='left')
            print(f'{code} money flow: {len(df_flow)} rows merged')
        else:
            print(f'{code} money flow: EMPTY')
    except Exception as e:
        print(f'{code} money flow: ERROR - {e}')
    time.sleep(1.5)

# ============ Save full data ============
for code in codes:
    all_data[code].to_csv(f'output/turnpoint_analysis/{code}_full.csv', index=False, encoding='utf-8-sig')

# ============ Print key stats around inflection point ============
key_cols = ['date','open','close','high','low','vol','vol_ratio','ret','rsi',
            'macd','macd_hist','k','d','j','bb_pos','atr_pct','amplitude',
            'upper_shadow','lower_shadow','body_pct','hl_spread','mfi',
            'pct_ma5','pct_ma20','ma5_slope','cumret_5d','ret_accel']

if 'main_net' in all_data['601869'].columns:
    key_cols.extend(['main_net','super_net'])

for code in codes:
    df = all_data[code]
    available = [c for c in key_cols if c in df.columns]

    # Find index of June 24
    idx_24 = df[df['date'] == '2026-06-24'].index
    if len(idx_24) > 0:
        idx = idx_24[0]
        context = df.iloc[max(0,idx-15):min(len(df),idx+20)]
        print(f'\n===== {names[code]} ({code}) CONTEXT =====')
        print(context[available].to_string())
    else:
        print(f'June 24 not found for {code}')

print('\nAll data saved to output/turnpoint_analysis/')
