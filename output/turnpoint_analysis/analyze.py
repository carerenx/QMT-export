# -*- coding: utf-8 -*-
"""Comprehensive analysis: 601869 & 600487 inflection point June 24-25, 2026"""
import urllib.request, json, os, time, requests
import pandas as pd, numpy as np

OUT = 'output/turnpoint_analysis'
os.makedirs(OUT, exist_ok=True)
UA = 'Mozilla/5.0'

# ============ 1. Fetch K-line ============
def tencent_kline(code, count=200):
    prefix = 'sh' if code.startswith(('6','9')) else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_day&param={prefix}{code},day,,,{count},qfq'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode('gbk')
    json_str = data.split('=', 1)[1].strip() if '=' in data else data
    d = json.loads(json_str)
    stock_key = f'{prefix}{code}'
    stock_data = d.get('data', {}).get(stock_key, {})
    klines = stock_data.get('qfqday', [])
    if not klines:
        for k in stock_data:
            if isinstance(stock_data[k], list) and len(stock_data[k]) > 0:
                klines = stock_data[k]; break
    rows = [{'date': it[0], 'open': float(it[1]), 'close': float(it[2]),
             'high': float(it[3]), 'low': float(it[4]), 'vol': float(it[5])}
            for it in klines if len(it) >= 6]
    return pd.DataFrame(rows)

codes = ['601869', '600487']
names = {'601869': '长飞光纤', '600487': '亨通光电'}
all_data = {}

for code in codes:
    df = tencent_kline(code, 200)
    df = df[df['date'] >= '2026-04-01'].reset_index(drop=True)
    all_data[code] = df
    print(f'{code} ({names[code]}): {len(df)} rows, {df["date"].min()} ~ {df["date"].max()}')

# ============ 2. Compute technical indicators (all pandas ops) ============
def add_indicators(df):
    d = df.copy()
    c, h, l, o, v = d['close'], d['high'], d['low'], d['open'], d['vol']

    # Daily return
    d['ret'] = c.pct_change() * 100
    d['ret_1d'] = d['ret'].shift(-1)

    # Volume
    d['vol_ma5'] = v.rolling(5).mean()
    d['vol_ma20'] = v.rolling(20).mean()
    d['vol_ratio'] = v / d['vol_ma20']
    d['vol_ratio_5'] = v / d['vol_ma5']

    # MA
    d['ma5'] = c.rolling(5).mean()
    d['ma10'] = c.rolling(10).mean()
    d['ma20'] = c.rolling(20).mean()
    d['ma60'] = c.rolling(60).mean()
    d['pct_ma5'] = (c - d['ma5']) / d['ma5'] * 100
    d['pct_ma20'] = (c - d['ma20']) / d['ma20'] * 100
    d['ma5_slope'] = d['ma5'].diff()
    d['ma10_slope'] = d['ma10'].diff()
    d['ma20_slope'] = d['ma20'].diff()

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

    # MACD(12,26,9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    d['macd'] = ema12 - ema26
    d['macd_signal'] = d['macd'].ewm(span=9, adjust=False).mean()
    d['macd_hist'] = d['macd'] - d['macd_signal']
    d['macd_hist_chg'] = d['macd_hist'].diff()

    # KDJ(9,3,3)
    n = 9
    low_n = l.rolling(n).min()
    high_n = h.rolling(n).max()
    rsv = (c - low_n) / (high_n - low_n) * 100
    d['k'] = rsv.ewm(com=2, adjust=False).mean()
    d['d'] = d['k'].ewm(com=2, adjust=False).mean()
    d['j'] = 3 * d['k'] - 2 * d['d']

    # Bollinger(20,2)
    d['bb_mid'] = d['ma20']
    d['bb_std'] = c.rolling(20).std()
    d['bb_upper'] = d['bb_mid'] + 2 * d['bb_std']
    d['bb_lower'] = d['bb_mid'] - 2 * d['bb_std']
    d['bb_width'] = (d['bb_upper'] - d['bb_lower']) / d['bb_mid'] * 100
    d['bb_pos'] = (c - d['bb_lower']) / (d['bb_upper'] - d['bb_lower'])

    # OBV
    obv = [0]
    cv = c.values
    vv = v.values
    for i in range(1, len(cv)):
        if cv[i] > cv[i-1]: obv.append(obv[-1] + vv[i])
        elif cv[i] < cv[i-1]: obv.append(obv[-1] - vv[i])
        else: obv.append(obv[-1])
    d['obv'] = obv

    # MFI(14)
    tp = (h + l + c) / 3
    rmf = tp * v
    pos_mf = rmf.where(tp > tp.shift(1), 0)
    neg_mf = rmf.where(tp < tp.shift(1), 0)
    mfr = pos_mf.rolling(14).sum() / neg_mf.rolling(14).sum()
    d['mfi'] = 100 - (100 / (1 + mfr))

    # Candlestick features
    body_high = pd.concat([o, c], axis=1).max(axis=1)
    body_low = pd.concat([o, c], axis=1).min(axis=1)
    d['upper_shadow'] = (h - body_high) / c * 100
    d['lower_shadow'] = (body_low - l) / c * 100
    d['body_pct'] = (body_high - body_low) / c * 100
    d['amplitude'] = (h - l) / o * 100
    d['hl_spread'] = (h - l) / c * 100

    # Cumulative returns
    d['cumret_5d'] = d['ret'].rolling(5).sum()
    d['cumret_10d'] = d['ret'].rolling(10).sum()
    d['cumret_20d'] = d['ret'].rolling(20).sum()

    # Ret acceleration
    d['ret_accel'] = d['ret'].diff()

    # Consecutive up/down
    up = (d['ret'] > 0).astype(int)
    d['consec_up'] = up.groupby((up != up.shift()).cumsum()).cumcount() + 1
    d.loc[up == 0, 'consec_up'] = 0
    down = (d['ret'] < 0).astype(int)
    d['consec_down'] = down.groupby((down != down.shift()).cumsum()).cumcount() + 1
    d.loc[down == 0, 'consec_down'] = 0

    # Gap (open vs previous close)
    d['gap'] = (o - c.shift(1)) / c.shift(1) * 100

    # Intraday reversal
    d['intraday_rev'] = (c - o) / o * 100

    return d

for code in codes:
    all_data[code] = add_indicators(all_data[code])
    print(f'{code}: indicators OK')

# ============ 3. Money flow ============
def fund_flow(code):
    mc = 1 if code.startswith('6') else 0
    url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {'secid': f'{mc}.{code}',
              'fields1': 'f1,f2,f3,f7',
              'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
              'lmt': '120'}
    headers = {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    d = r.json()
    klines = d.get('data', {}).get('klines', [])
    rows = []
    for line in klines:
        parts = line.split(',')
        if len(parts) >= 7:
            rows.append({'date': parts[0],
                         'main_net': float(parts[1]) if parts[1] != '-' else 0,
                         'small_net': float(parts[2]) if parts[2] != '-' else 0,
                         'mid_net': float(parts[3]) if parts[3] != '-' else 0,
                         'large_net': float(parts[4]) if parts[4] != '-' else 0,
                         'super_net': float(parts[5]) if parts[5] != '-' else 0})
    return pd.DataFrame(rows)

for code in codes:
    for attempt in range(3):
        try:
            df_flow = fund_flow(code)
            if not df_flow.empty:
                all_data[code] = all_data[code].merge(df_flow, on='date', how='left')
                if 'main_net' in all_data[code].columns:
                    all_data[code]['main_net_wan'] = all_data[code]['main_net'] / 1e4
                    all_data[code]['super_net_wan'] = all_data[code]['super_net'] / 1e4
                print(f'{code} money flow: {len(df_flow)} rows')
                break
        except Exception as e:
            print(f'{code} attempt {attempt+1}: {e}')
            time.sleep(3)

# ============ 4. Save ============
for code in codes:
    all_data[code].to_csv(f'{OUT}/{code}_full.csv', index=False, encoding='utf-8-sig')
    print(f'{code} saved: {len(all_data[code].columns)} cols')

# ============ 5. Verify indicators ============
for code in codes:
    df = all_data[code]
    print(f'\n{code} indicator check:')
    for col in ['rsi','macd','macd_hist','k','d','j','bb_pos','mfi']:
        if col in df.columns:
            print(f'  {col}: {df[col].notna().sum()}/{len(df)} valid')

# ============ 6. Key data around inflection ============
display_cols = ['date','open','close','high','low','vol','vol_ratio','ret',
    'rsi','macd','macd_hist','k','d','j','bb_pos','atr_pct','amplitude',
    'upper_shadow','lower_shadow','body_pct','hl_spread','mfi',
    'pct_ma5','pct_ma20','ma5_slope','cumret_5d','ret_accel','gap','intraday_rev']
if 'main_net_wan' in all_data['601869'].columns:
    display_cols.extend(['main_net_wan','super_net_wan'])

for code in codes:
    df = all_data[code]
    avail = [c for c in display_cols if c in df.columns]
    idx = df[df['date'] == '2026-06-24'].index
    if len(idx) == 0:
        print(f'\n{code}: June 24 NOT FOUND')
        continue
    i = idx[0]
    ctx = df.iloc[max(0,i-15):min(len(df),i+20)].copy()
    print(f'\n===== {names[code]} ({code}) =====')
    pd.set_option('display.max_columns', 50)
    pd.set_option('display.width', 300)
    pd.set_option('display.max_rows', 60)
    print(ctx[avail].to_string())
