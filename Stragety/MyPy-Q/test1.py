# -*- coding: utf-8 -*-
"""
打印 xtquant 能获取到的 601869 所有实时数据
"""
from xtquant import xtdata

CODE = '601869.SH'

# 隐藏 xtdata 连接成功消息
xtdata.enable_hello = False

# 连接 MiniQMT
xtdata.connect()

# 获取实时行情
tick = xtdata.get_full_tick([CODE])

print(f'{"="*60}')
print(f'  {CODE} 实时行情数据')
print(f'{"="*60}')

if CODE not in tick:
    print('无数据 — 请确认 MiniQMT 已启动且非交易时段也有盘口快照')
    exit()

t = tick[CODE]

# ── 基础价格 ──
print(f'\n【基础价格】')
price_fields = [
    ('lastPrice',             '最新价'),
    ('open',                  '今开'),
    ('high',                  '今日最高'),
    ('low',                   '今日最低'),
    ('lastClose',             '昨收'),
    ('lastSettlementPrice',   '昨结'),
    ('settlementPrice',       '今结'),
]
for key, label in price_fields:
    val = t.get(key, 'N/A')
    print(f'  {label:10s} = {val}')

# ── 涨跌 ──
print(f'\n【涨跌】')
if t.get('lastClose', 0) > 0:
    change = t['lastPrice'] - t['lastClose']
    change_pct = change / t['lastClose'] * 100
    print(f'  涨跌额       = {change:+.2f}')
    print(f'  涨跌幅       = {change_pct:+.2f}%')

# ── 成交量/额 ──
print(f'\n【成交】')
print(f'  成交量(手)   = {t.get("volume", "N/A"):,}')
print(f'  成交量(股)   = {t.get("pvolume", "N/A"):,}')
print(f'  成交额(元)   = {t.get("amount", "N/A"):,}')
print(f'  持仓量       = {t.get("openInt", "N/A")}')

# ── 五档盘口 ──
print(f'\n【卖盘 (ask)】')
ask_prices = t.get('askPrice', [])
ask_vols = t.get('askVol', [])
for i in range(len(ask_prices)):
    print(f'  卖{i+1}:  {ask_prices[i]:>10.2f}  x {ask_vols[i]:>6}手')

print(f'\n【买盘 (bid)】')
bid_prices = t.get('bidPrice', [])
bid_vols = t.get('bidVol', [])
for i in range(len(bid_prices)):
    print(f'  买{i+1}:  {bid_prices[i]:>10.2f}  x {bid_vols[i]:>6}手')

# ── 其他信息 ──
print(f'\n【其他】')
other_fields = [
    'stockStatus', 'time', 'timetag',
]
for key in other_fields:
    val = t.get(key, 'N/A')
    if key == 'timetag':
        import datetime
        try:
            val = datetime.datetime.fromtimestamp(val / 1000).strftime('%Y-%m-%d %H:%M:%S')
        except:
            pass
    print(f'  {key:15s} = {val}')

# ── 所有其他未展示的字段 ──
shown = {f[0] for f in price_fields} | {'lastPrice', 'open', 'high', 'low', 'lastClose',
    'lastSettlementPrice', 'settlementPrice', 'volume', 'pvolume', 'amount', 'openInt',
    'askPrice', 'askVol', 'bidPrice', 'bidVol', 'stockStatus', 'time', 'timetag'}
extra = {k: v for k, v in sorted(t.items()) if k not in shown}
if extra:
    print(f'\n【额外字段】')
    for k, v in extra.items():
        print(f'  {k:20s} = {v}')

print(f'\n{"="*60}')
print(f'  实时行情打印完毕')
print(f'{"="*60}')
