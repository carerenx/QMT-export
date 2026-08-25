# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 仓位管理 & 交易计划 — 独立评估工具
================================================================================
 读取 MiniQMT 实时持仓 + 资金 + 行情, 综合评估后输出交易计划。

 用法:
   python "MyPy-Q/QMT_仓位管理_交易计划.py"

 前置: MiniQMT 已启动, 账号已登录, 601869 历史数据已下载

 输出:
   1. 当前持仓状态 (总股数 / T+0可用 / T+1锁定 / 成本 / 浮盈)
   2. 资金状态 (可用 / 总资产)
   3. 市场评估 (趋势 / ATR / RSI / 量比 / 日内强度)
   4. 反T可行性 + 目标价位
   5. 正T可行性 + 目标价位
   6. 风险提示 & 仓位建议
================================================================================
"""
import time as _time
from datetime import datetime

import numpy as np
import pandas as pd

# ============================================================================
# 策略参数 (与 v12 一致)
# ============================================================================

ACCOUNT     = '8890145315'
STOCK_CODE  = '601869'
STOCK_NAME  = '长飞光纤'
STOCK_QMT   = '601869.SH'
TRADE_LOT   = 100

ATR_PERIOD  = 14
HIST_LEN    = 80

SELL_TRIGGER_BASE_BEAR      = 0.40
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55
SELL_TRIGGER_BASE_WEAK_BULL = 0.65
DYNAMIC_MULT_MIN = 0.20
DYNAMIC_MULT_MAX = 1.50

PULLBACK_PCT        = 0.0010
BUYBACK_TRIGGER_MULT = 0.15
BOUNCE_PCT          = 0.0010

BUY_TRIGGER_PCT   = 0.03
BUY_TRIGGER_TRAIL = 0.02
SELLBACK_RISE_PCT = 0.01

STOP_LOSS_PCT         = 0.015
EMERGENCY_BUYBACK_PCT = 0.03
FORCE_CLOSE_TIME      = '14:57:00'

VOLUME_FILTER_RATIO = 0.4
RSI_OVERBOUGHT      = 75
STRONG_BULL_RSI     = 70
STRONG_BULL_STREAK  = 5

# 仓位管理参数
MAX_POSITION_LOTS  = 5            # 最大持仓手数 (500股)
MIN_POSITION_LOTS  = 1            # 最低持仓手数 (100股)
MAX_DAILY_TRADES   = 3            # 单日最多交易次数
RISK_PER_TRADE_PCT = 0.015        # 单笔风险占持仓市值比例

# ============================================================================
# 技术指标 (精简版)
# ============================================================================

def _sma(v, p):
    r = [0.0] * len(v)
    for i in range(p - 1, len(v)):
        r[i] = sum(v[i - p + 1:i + 1]) / p
    return r

def _atr(h, l, c, p=14):
    n = len(c)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    r = [0.0] * n
    for i in range(p, n):
        r[i] = sum(tr[i - p + 1:i + 1]) / p
    return r

def _rsi(c, p=14):
    n = len(c)
    if n < p + 1: return [50.0] * n
    r, g, l = [50.0] * n, [], []
    for i in range(1, n):
        d = c[i] - c[i - 1]
        g.append(d if d > 0 else 0); l.append(abs(d) if d < 0 else 0)
    ag, al = sum(g[:p]) / p, sum(l[:p]) / p
    r[p] = 100 - 100 / (1 + ag / al) if al > 0 else 100
    for i in range(p, n - 1):
        ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + l[i]) / p
        r[i + 1] = 100 - 100 / (1 + ag / al) if al > 0 else 100
    return r

def _up_streak(c):
    s = [0] * len(c)
    for i in range(1, len(c)): s[i] = s[i - 1] + 1 if c[i] > c[i - 1] else 0
    return s

# ============================================================================
# 数据获取
# ============================================================================

def fetch_daily_data():
    """从 xtdata 获取日线"""
    from xtquant import xtdata
    xtdata.connect()
    data = xtdata.get_local_data(
        field_list=['open', 'high', 'low', 'close', 'volume'],
        stock_list=[STOCK_QMT], period='1d',
        start_time='20200101',
        end_time=datetime.now().strftime('%Y%m%d'),
        dividend_type='front', data_dir='C:/QMT/datadir',
    )
    df = data.get(STOCK_QMT)
    if df is None or len(df) < 60:
        raise RuntimeError('日线数据不足')
    return df

def fetch_tick():
    """获取实时 tick"""
    from xtquant import xtdata
    tick = xtdata.get_full_tick([STOCK_QMT])
    return tick.get(STOCK_QMT, {})

def fetch_position(account=ACCOUNT):
    """从 xttrader 获取持仓 (自动重试不同session)"""
    from xtquant import xttrader

    trader = None
    for session_id in [9, 8, 7, 5, 3, 0]:
        try:
            cb = xttrader.XtQuantTraderCallback()
            trader = xttrader.XtQuantTrader('C:/QMT/userdata_mini', session_id, cb)
            trader.start()
            ret = trader.connect()
            if ret == 0:
                break  # 连接成功
            trader.stop()
            trader = None
        except Exception:
            if trader:
                try: trader.stop()
                except: pass
            trader = None

    if trader is None:
        raise RuntimeError('MiniQMT交易连接失败(所有session被占用). 请关闭其他策略后重试.')

    accounts = trader.query_account_infos()
    if not accounts:
        raise RuntimeError('未找到资金账号')
    acc = None
    for a in accounts:
        if a.account_id == account:
            acc = a; break
    if acc is None:
        acc = accounts[0]
    trader.subscribe(acc)
    _time.sleep(0.3)

    asset = trader.query_stock_asset(acc)
    positions = trader.query_stock_positions(acc)

    trader.unsubscribe(acc)
    trader.stop()

    return asset, positions

# ============================================================================
# 市场评估
# ============================================================================

def assess_market(df, tick):
    """评估市场状态"""
    closes  = df['close'].values.tolist()
    opens   = df['open'].values.tolist()
    highs   = df['high'].values.tolist()
    lows    = df['low'].values.tolist()
    volumes = df['volume'].values.tolist()

    today_open = tick.get('open', opens[-1]) if tick else opens[-1]
    if today_open > 0 and len(opens) > 0:
        opens[-1] = today_open

    n = len(closes)
    cc = closes[-1]
    cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    atr_pct = curr_atr / cc if cc > 0 else 0.03

    ma5, ma20 = _sma(closes, 5)[-1], _sma(closes, 20)[-1]
    rsi_val = _rsi(closes)[-1]
    streak = _up_streak(closes)[-1]

    above_ma = (cc > ma20) and (ma5 > ma20)
    below_ma = (cc < ma20) and (ma5 < ma20)

    if above_ma and rsi_val > STRONG_BULL_RSI and streak >= STRONG_BULL_STREAK:
        trend = 'strong_bull'
    elif above_ma:
        trend = 'weak_bull'
    elif below_ma:
        trend = 'bear'
    else:
        trend = 'sideways'

    ma20v = _sma(volumes, 20)
    vol_r = cv / ma20v[-1] if ma20v[-1] > 0 else 1.0

    # 日内强度
    last_price = tick.get('lastPrice', cc) if tick else cc
    day_high = tick.get('high', last_price) if tick else last_price
    day_low = tick.get('low', last_price) if tick else last_price

    intraday_chg = (last_price - today_open) / today_open * 100 if today_open > 0 else 0
    day_range_pct = (day_high - day_low) / today_open * 100 if today_open > 0 else 0
    position_in_range = (last_price - day_low) / (day_high - day_low) * 100 if day_high > day_low else 50

    # 反T信号熔断检查
    short_blocked = False
    short_block_reason = ''
    if trend == 'strong_bull':
        short_blocked = True
        short_block_reason = '强牛禁反T'
    elif vol_r < VOLUME_FILTER_RATIO:
        short_blocked = True
        short_block_reason = '缩量(量比{:.2f})'.format(vol_r)
    elif rsi_val > RSI_OVERBOUGHT:
        short_blocked = True
        short_block_reason = 'RSI超买({:.0f})'.format(rsi_val)

    # 动态乘数
    base = SELL_TRIGGER_BASE_SIDEWAYS
    if trend == 'bear': base = SELL_TRIGGER_BASE_BEAR
    elif trend == 'weak_bull': base = SELL_TRIGGER_BASE_WEAK_BULL

    return {
        'trend': trend, 'atr': curr_atr, 'atr_pct': atr_pct,
        'rsi': rsi_val, 'vol_ratio': vol_r, 'streak': streak,
        'ma5': ma5, 'ma20': ma20, 'close_yday': cc,
        'open_today': today_open, 'last_price': last_price,
        'day_high': day_high, 'day_low': day_low,
        'intraday_chg_pct': intraday_chg,
        'day_range_pct': day_range_pct,
        'position_in_range_pct': position_in_range,
        'short_blocked': short_blocked,
        'short_block_reason': short_block_reason,
        'sell_base': base,
    }


# ============================================================================
# 交易计划生成
# ============================================================================

def make_plan(market, total_shares, avail_shares, locked_shares, cost, cash, total_asset):
    """根据仓位+市场生成交易计划"""

    price = market['last_price']
    open_p = market['open_today']
    atr = market['atr']
    atr_pct = market['atr_pct']
    trend = market['trend']

    # ── 仓位状态 ──
    pos_value = total_shares * price
    pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0
    unreal_pnl = (price - cost) * total_shares if cost > 0 else 0
    unreal_pnl_pct = (price / cost - 1) * 100 if cost > 0 else 0

    can_add_lots = int(avail_shares / TRADE_LOT) if avail_shares > 0 else 0
    max_buy_lots = int(cash / (price * TRADE_LOT * 1.005)) if price > 0 else 0

    # ── 反T 可行性 ──
    short_ok = avail_shares >= TRADE_LOT and not market['short_blocked']
    short_reasons = []
    if avail_shares < TRADE_LOT:
        short_reasons.append('可用{}股<1手'.format(avail_shares))
    if market['short_blocked']:
        short_reasons.append(market['short_block_reason'])
    short_reason = '; '.join(short_reasons) if short_reasons else 'OK'

    # 反T目标价
    if not market['short_blocked']:
        sell_mult = market['sell_base']  # 简化: 用BASE
        sell_trigger = round(open_p + atr * sell_mult, 2)
    else:
        sell_trigger = 0
        sell_mult = 0
    buyback_hint = round(sell_trigger * (1 - atr_pct * BUYBACK_TRIGGER_MULT), 2) if sell_trigger > 0 else 0

    # ── 正T 可行性 ──
    long_ok_cash = cash >= price * TRADE_LOT * 1.005
    long_ok_shares = avail_shares >= TRADE_LOT  # T+1: 卖腿需可用持仓
    long_ok = long_ok_cash and long_ok_shares
    long_reasons = []
    if not long_ok_cash:
        long_reasons.append('资金不足(需{:,.0f}>可用{:,.0f})'.format(price * TRADE_LOT * 1.005, cash))
    if not long_ok_shares:
        long_reasons.append('T+1:无可用持仓(可用{}股<1手)'.format(avail_shares))
    long_reason = '; '.join(long_reasons) if long_reasons else 'OK'

    # 正T目标价
    buy_trigger_floor = round(open_p * (1 - BUY_TRIGGER_PCT), 2)
    buy_trigger_trail = round(price * (1 - BUY_TRIGGER_TRAIL), 2)
    buy_trigger = max(buy_trigger_floor, buy_trigger_trail)
    sellback_target = round(buy_trigger * (1 + SELLBACK_RISE_PCT), 2)

    # ── 仓位建议 ──
    position_advice = ''
    if pos_pct > 80:
        position_advice = '仓位过重(>{:.0f}%), 不建议正T加仓, 可考虑反T减仓'.format(pos_pct)
    elif pos_pct < 20 and long_ok:
        position_advice = '仓位较轻(<{:.0f}%), 可考虑正T加仓'.format(pos_pct)
    elif not short_ok and not long_ok:
        position_advice = '两个方向都不可用, 观望'
    elif trend == 'strong_bull':
        position_advice = '强牛行情, 持有为主, 不反T, 正T等回调'
    elif trend == 'bear':
        position_advice = '熊市行情, 积极反T, 正T谨慎'
    else:
        parts = []
        if short_ok: parts.append('可做反T')
        if long_ok: parts.append('可做正T')
        position_advice = '; '.join(parts) if parts else '观望'

    # ── 风险参数 ──
    max_loss_per_trade = pos_value * RISK_PER_TRADE_PCT
    stop_loss_price_short = round(price * (1 + STOP_LOSS_PCT), 2)   # 反T止损价
    stop_loss_price_long  = round(price * (1 - STOP_LOSS_PCT), 2)   # 正T止损价

    return {
        # 仓位
        'total_shares': total_shares, 'avail_shares': avail_shares,
        'locked_shares': locked_shares, 'cost': cost,
        'pos_value': pos_value, 'pos_pct': pos_pct,
        'unreal_pnl': unreal_pnl, 'unreal_pnl_pct': unreal_pnl_pct,
        'cash': cash, 'total_asset': total_asset,

        # 反T
        'short_ok': short_ok, 'short_reason': short_reason,
        'sell_trigger': sell_trigger, 'sell_mult': sell_mult,
        'buyback_hint': buyback_hint,

        # 正T
        'long_ok': long_ok, 'long_reason': long_reason,
        'buy_trigger': buy_trigger, 'buy_trigger_floor': buy_trigger_floor,
        'sellback_target': sellback_target,

        # 建议
        'position_advice': position_advice,
        'can_add_lots': can_add_lots,
        'max_buy_lots': max_buy_lots,

        # 风险
        'max_loss_per_trade': max_loss_per_trade,
        'stop_loss_price_short': stop_loss_price_short,
        'stop_loss_price_long': stop_loss_price_long,
    }


# ============================================================================
# 报告输出
# ============================================================================

def print_report(market, plan):
    """打印仓位管理和交易计划报告"""

    trend_labels = {
        'strong_bull': '强牛(持有为主)',
        'weak_bull': '弱牛(谨慎反T)',
        'bear': '熊市(积极反T)',
        'sideways': '震荡(双向可做)',
    }

    print()
    print('╔' + '═' * 60 + '╗')
    print('║  {:^56s}  ║'.format('{} 仓位管理 & 交易计划'.format(STOCK_NAME)))
    print('║  {:^56s}  ║'.format(datetime.now().strftime('%Y-%m-%d %H:%M')))
    print('╠' + '═' * 60 + '╣')

    # ── 市场概况 ──
    print('║  {:^56s}  ║'.format('【市场评估】'))
    print('║  趋势: {:<16s}  ATR: {:.1f}%  RSI: {:.0f}  量比: {:.2f}  ║'.format(
        trend_labels.get(market['trend'], market['trend']),
        market['atr_pct'] * 100, market['rsi'], market['vol_ratio']))
    print('║  昨收: Y{:<10.2f}  今开: Y{:<10.2f}  现价: Y{:<10.2f}  ║'.format(
        market['close_yday'], market['open_today'], market['last_price']))
    print('║  日内涨跌: {:+.2f}%  日内振幅: {:.2f}%  价格位置: {:.0f}%  ║'.format(
        market['intraday_chg_pct'], market['day_range_pct'], market['position_in_range_pct']))
    print('╠' + '═' * 60 + '╣')

    # ── 仓位状态 ──
    print('║  {:^56s}  ║'.format('【仓位状态】'))
    print('║  总持仓: {}股  Y{:,.0f} ({:.0f}%仓位)  ║'.format(
        plan['total_shares'], plan['pos_value'], plan['pos_pct']))
    print('║  T+0可用: {}股  |  T+1锁定: {}股  |  成本: Y{:.2f}  ║'.format(
        plan['avail_shares'], plan['locked_shares'], plan['cost']))
    print('║  浮动盈亏: Y{:+,.0f} ({:+.1f}%)  ║'.format(
        plan['unreal_pnl'], plan['unreal_pnl_pct']))
    print('║  可用资金: Y{:,.0f}  |  总资产: Y{:,.0f}  ║'.format(
        plan['cash'], plan['total_asset']))
    print('╠' + '═' * 60 + '╣')

    # ── 交易计划 ──
    print('║  {:^56s}  ║'.format('【交易计划】'))

    # 反T
    status_short = '✓ 可用' if plan['short_ok'] else '✗ 不可用'
    print('║  反T(先卖后买): {:s}  ║'.format(status_short))
    if plan['short_ok']:
        print('║    卖出触发: Y{:.2f}  买回预估: Y{:.2f}  ║'.format(
            plan['sell_trigger'], plan['buyback_hint']))
    else:
        print('║    原因: {:s}  ║'.format(plan['short_reason'][:50]))

    # 正T
    status_long = '✓ 可用' if plan['long_ok'] else '✗ 不可用'
    print('║  正T(先买后卖): {:s}  ║'.format(status_long))
    if plan['long_ok']:
        print('║    买入触发: Y{:.2f} (底线Y{:.2f})  卖出预估: Y{:.2f}  ║'.format(
            plan['buy_trigger'], plan['buy_trigger_floor'], plan['sellback_target']))
    else:
        print('║    原因: {:s}  ║'.format(plan['long_reason'][:50]))

    print('╠' + '═' * 60 + '╣')

    # ── 建议 ──
    print('║  {:^56s}  ║'.format('【操作建议】'))
    print('║  {:s}  ║'.format(plan['position_advice'][:54]))
    print('║  最大加仓: {}手(正T)  可用T+0: {}手  ║'.format(
        plan['max_buy_lots'], plan['can_add_lots']))
    print('║  单笔最大亏损: Y{:,.0f}  ║'.format(plan['max_loss_per_trade']))
    print('║  反T止损价: Y{:.2f}  正T止损价: Y{:.2f}  ║'.format(
        plan['stop_loss_price_short'], plan['stop_loss_price_long']))
    print('╚' + '═' * 60 + '╝')
    print()


# ============================================================================
# 主入口
# ============================================================================

def main():
    print('[仓位管理] 连接 MiniQMT ...')

    # 1. 获取行情数据
    try:
        df = fetch_daily_data()
        tick = fetch_tick()
        print('[数据] 日线 {} 条 | tick Y{:.2f}'.format(
            len(df), tick.get('lastPrice', 0)))
    except Exception as e:
        print('[错误] 行情获取失败: {}'.format(e))
        print('[提示] 请先启动 MiniQMT (极简模式)')
        return

    # 2. 获取持仓
    try:
        asset, positions = fetch_position()
        print('[账户] 可用资金 Y{:,.0f} | 总资产 Y{:,.0f}'.format(
            asset.cash, asset.total_asset))
    except Exception as e:
        print('[错误] 持仓获取失败: {}'.format(e))
        return

    # 3. 解析 601869 持仓
    total_shares = 0
    avail_shares = 0
    cost = 0.0
    for pos in positions:
        code = pos.stock_code.split('.')[0] if hasattr(pos, 'stock_code') else ''
        if code == STOCK_CODE:
            total_shares = pos.volume
            avail_shares = getattr(pos, 'can_use_volume', pos.volume)
            cost = pos.open_price
            break

    locked_shares = total_shares - avail_shares
    cash = asset.cash
    total_asset = asset.total_asset

    # 4. 市场评估
    market = assess_market(df, tick)

    # 5. 生成计划
    plan = make_plan(market, total_shares, avail_shares, locked_shares,
                     cost, cash, total_asset)

    # 6. 输出报告
    print_report(market, plan)


if __name__ == '__main__':
    main()
