#coding:gbk
"""
亨通光电(600487) 动量趋势跟踪策略 v2
==========================================
基于 Alphalens 因子分析报告 — 2025.06~2026.06 通信设备/光通信板块 73只A股

══════════════════════════════════════════════════════════════
策略设计
══════════════════════════════════════════════════════════════
核心: "买入持有 + 移动止盈" — 利用5因子动量延续，配合ATR动态止损。

入场条件 (全部满足):
  (1) 5日动量 > 3% AND 20日动量 > 5% (因子动量确认)
  (2) Close > MA10 (短期趋势确认)
  (3) Close > MA60 (中期趋势确认)
  (4) 当日涨幅 < +6% (不追涨停)
  (5) 距上次卖出 > 10 天 (冷却)

出场条件 (任一触发):
  (1) Close < MA60 (中期趋势破坏)
  (2) Close < 入场价 - 2.5×ATR (硬止损)
  (3) Close < 最高价 - 1.5×ATR (移动止盈)

仓位管理:
  - 风险预算: 1.5% 总资产
  - 单票上限: 25%
"""

import numpy as np

# ╔════════════════════════════════════════════════════════════╗
# ║              用户可调参数                                  ║
# ╚════════════════════════════════════════════════════════════╝

TARGET_STOCK = '600487.SH'

# ── 因子阈值 (原始动量, 非 z-score) ──
MIN_MOM_5D = 0.03     # 5日动量 > 3%
MIN_MOM_20D = 0.05    # 20日动量 > 5%

# ── 趋势过滤 ──
MA_FAST = 10          # 快速均线 (短期趋势)
MA_SLOW = 60          # 慢速均线 (中期趋势)

# ── ATR 止损/止盈 ──
ATR_PERIOD = 14
STOP_ATR_MULT = 2.5   # 硬止损: entry - 2.5×ATR
TRAIL_ATR_MULT = 1.5  # 移动止盈: highest - 1.5×ATR

# ── 仓位管理 ──
RISK_PER_TRADE = 0.015    # 单笔风险敞口 (1.5%)
MAX_POSITION_PCT = 0.25   # 单票仓位上限 (25%)

# ── 其他 ──
COOLDOWN_BARS = 10        # 卖出后冷却天数
MIN_HISTORY_BARS = 130    # 最少需要的历史K线数
MAX_DAILY_GAIN = 0.06     # 当日涨幅超此值不追


# ╔════════════════════════════════════════════════════════════╗
# ║              全局状态                                      ║
# ╚════════════════════════════════════════════════════════════╝

class State:
    acc_id = 'testS'
    capital = 10000000
    position = None         # {shares, entry_price, highest, bars_held}
    cash = 0
    total_assets = 0
    last_barpos = -1
    cooldown_left = 0


# ╔════════════════════════════════════════════════════════════╗
# ║              主入口                                        ║
# ╚════════════════════════════════════════════════════════════╝

def init(ContextInfo):
    ContextInfo.set_universe([TARGET_STOCK, '000300.SH'])

    for attr, val in [
        ('capital', State.capital),
        ('benchmark', '000300.SH'),
        ('start', '2025-06-01 09:30:00'),
        ('end', '2026-06-17 15:00:00'),
    ]:
        try:
            setattr(ContextInfo, attr, val)
        except (AttributeError, TypeError):
            pass

    ContextInfo.set_slippage(1, 0.001)
    ContextInfo.set_commission(0, [0.00025, 0.00025, 0.001, 0.0, 0.0, 5.0])
    ContextInfo.set_account(State.acc_id)

    print("[init] 亨通光电动量趋势策略 v2")
    print("[init] 标的: %s | 初始资金: %.0f万" % (TARGET_STOCK, State.capital / 10000))


def handlebar(ContextInfo):
    bar = ContextInfo.barpos

    if bar < MIN_HISTORY_BARS:
        return
    if bar == State.last_barpos:
        return
    State.last_barpos = bar

    # ── 获取行情 ──
    hist_needed = max(MA_SLOW, 130) + 30
    close_dict = ContextInfo.get_history_data(hist_needed, '1d', 'close')
    high_dict = ContextInfo.get_history_data(ATR_PERIOD + 10, '1d', 'high')
    low_dict = ContextInfo.get_history_data(ATR_PERIOD + 10, '1d', 'low')

    close_arr = close_dict.get(TARGET_STOCK, [])
    high_arr = high_dict.get(TARGET_STOCK, [])
    low_arr = low_dict.get(TARGET_STOCK, [])

    if len(close_arr) < MIN_HISTORY_BARS:
        return

    current_price = close_arr[-1]
    if current_price <= 0 or np.isnan(current_price):
        return

    # ── 更新账户 ──
    _update_account(ContextInfo)

    # ── 更新冷却 ──
    if State.cooldown_left > 0:
        State.cooldown_left -= 1

    # ── 同步持仓 ──
    _sync_position(ContextInfo, close_arr)

    # ── 计算指标 ──
    arr = np.array(close_arr, dtype=float)
    arr = arr[~np.isnan(arr)]

    if len(arr) < MA_SLOW + 1:
        return

    # 5日动量 (注: reversal_5d 实际上是 -5日收益率, IC为负所以动量信号)
    mom_5d = arr[-1] / arr[-5] - 1 if len(arr) >= 6 else 0
    mom_20d = arr[-1] / arr[-20] - 1 if len(arr) >= 21 else 0
    mom_60d = arr[-1] / arr[-60] - 1 if len(arr) >= 61 else 0

    # 均线
    ma_fast = float(np.mean(arr[-MA_FAST:]))
    ma_slow = float(np.mean(arr[-MA_SLOW:]))

    # ATR
    atr = _compute_atr(high_arr, low_arr, close_arr, ATR_PERIOD)
    atr_pct = atr / current_price if current_price > 0 else 0.02

    daily_ret = arr[-1] / arr[-2] - 1 if len(arr) >= 2 else 0

    # ── 日志 ──
    has_pos = State.position is not None and State.position.get('shares', 0) > 0
    pos_info = ""
    if has_pos:
        p = State.position
        pnl_pct = (current_price / p['entry_price'] - 1) * 100
        pos_info = " | 盈亏: %+.1f%% 持有%d天" % (pnl_pct, p.get('bars_held', 0))

    print("[%d] 价格=%.2f | 5d动%.1f%% 20d动%.1f%% 60d动%.1f%% | MA%d=%.2f MA%d=%.2f | ATR=%.2f(%.1f%%)%s" % (
        bar, current_price, mom_5d * 100, mom_20d * 100, mom_60d * 100,
        MA_FAST, ma_fast, MA_SLOW, ma_slow, atr, atr_pct * 100, pos_info))

    # ═══════════════════════════════════════════════════════════
    # 有持仓 → 检查出场
    # ═══════════════════════════════════════════════════════════
    if has_pos:
        exit_reason = _check_exit(current_price, ma_slow, atr)
        if exit_reason:
            _sell_all(ContextInfo, TARGET_STOCK, current_price, exit_reason)
        else:
            if current_price > State.position['highest']:
                State.position['highest'] = current_price
            State.position['bars_held'] += 1
    else:
        # ═══════════════════════════════════════════════════════
        # 无持仓 → 检查入场
        # ═══════════════════════════════════════════════════════
        entry_signal = _check_entry(mom_5d, mom_20d, current_price, ma_fast, ma_slow, daily_ret)
        if entry_signal:
            _buy_all(ContextInfo, TARGET_STOCK, current_price, atr, entry_signal)

    has_pos_after = State.position is not None and State.position.get('shares', 0) > 0
    print("[摘要] 持仓=%s 冷却=%d天 资产=%.0f万" % (
        "是" if has_pos_after else "否", State.cooldown_left,
        State.total_assets / 10000))


# ╔════════════════════════════════════════════════════════════╗
# ║              入场判断                                      ║
# ╚════════════════════════════════════════════════════════════╝

def _check_entry(mom_5d, mom_20d, price, ma_fast, ma_slow, daily_ret):
    """检查入场条件"""
    # (1) 因子动量确认
    if mom_5d <= MIN_MOM_5D:
        return None
    if mom_20d <= MIN_MOM_20D:
        return None

    # (2) 趋势确认: price > MA_FAST AND price > MA_SLOW
    if price <= ma_fast or price <= ma_slow:
        return None

    # (3) 不追涨
    if daily_ret >= MAX_DAILY_GAIN:
        return None

    # (4) 冷却期
    if State.cooldown_left > 0:
        return None

    return "5d动%.1f%% 20d动%.1f%% | MA%d>MA%d | 日涨%.1f%%" % (
        mom_5d * 100, mom_20d * 100, MA_FAST, MA_SLOW, daily_ret * 100)


# ╔════════════════════════════════════════════════════════════╗
# ║              出场判断                                      ║
# ╚════════════════════════════════════════════════════════════╝

def _check_exit(price, ma_slow, atr):
    """检查出场条件"""
    if State.position is None:
        return None

    entry = State.position['entry_price']
    highest = State.position['highest']

    # (1) 中期趋势破坏
    if price < ma_slow:
        return "趋势破坏: %.2f < MA%d(%.2f)" % (price, MA_SLOW, ma_slow)

    # (2) 硬止损
    stop_price = entry - STOP_ATR_MULT * atr
    if price <= stop_price:
        return "硬止损: %.2f <= %.2f-%.1f×ATR" % (price, entry, STOP_ATR_MULT)

    # (3) 移动止盈 (仅在盈利时)
    if highest > entry * 1.05:
        trail_price = highest - TRAIL_ATR_MULT * atr
        if price <= trail_price:
            return "移动止盈: %.2f <= %.2f-%.1f×ATR" % (price, highest, TRAIL_ATR_MULT)

    return None


# ╔════════════════════════════════════════════════════════════╗
# ║              交易执行                                      ║
# ╚════════════════════════════════════════════════════════════╝

def _buy_all(ContextInfo, code, price, atr, reason):
    if price <= 0 or np.isnan(price):
        return
    if np.isnan(atr) or atr <= 0:
        atr = price * 0.02

    capital = max(State.cash, State.total_assets * 0.5) if State.total_assets > 0 else State.cash
    if capital <= 0 or np.isnan(capital):
        return

    risk_amount = capital * RISK_PER_TRADE
    stop_dist = STOP_ATR_MULT * atr
    if stop_dist <= 0 or np.isnan(stop_dist):
        stop_dist = price * 0.03

    target_shares = int(risk_amount / stop_dist / 100) * 100
    max_shares = int(capital * MAX_POSITION_PCT / price / 100) * 100
    shares = min(target_shares, max_shares)

    if shares < 100:
        return

    need_cash = shares * price * 1.002
    if need_cash > State.cash:
        shares = int(State.cash * 0.98 / price / 100) * 100
        if shares < 100:
            return

    passorder(23, 1101, State.acc_id, code, 5, -1, shares,
              '亨通动量v2', 1, '', ContextInfo)

    State.position = {
        'shares': shares,
        'entry_price': price,
        'highest': price,
        'bars_held': 0,
    }
    State.cooldown_left = 0

    print(">>> [买入] %s × %d股 @ %.2f | 金额 %.0f | %s" % (
        code, shares, price, shares * price, reason))


def _sell_all(ContextInfo, code, price, reason):
    if State.position is None:
        return
    if price <= 0 or np.isnan(price):
        return

    shares = State.position.get('shares', 0)
    if shares <= 0:
        State.position = None
        return

    passorder(24, 1101, State.acc_id, code, 5, -1, shares,
              '亨通动量v2', 1, '', ContextInfo)

    entry = State.position['entry_price']
    pnl_pct = (price / entry - 1) * 100
    bars = State.position.get('bars_held', 0)

    print("<<< [卖出] %s × %d股 @ %.2f | 盈亏 %+.1f%% | 持有%d天 | %s" % (
        code, shares, price, pnl_pct, bars, reason))

    State.position = None
    State.cooldown_left = COOLDOWN_BARS


# ╔════════════════════════════════════════════════════════════╗
# ║              辅助函数                                      ║
# ╚════════════════════════════════════════════════════════════╝

def _compute_atr(high_arr, low_arr, close_arr, period):
    if len(close_arr) < period + 1:
        px = close_arr[-1] if len(close_arr) > 0 and close_arr[-1] > 0 else 100
        return px * 0.02

    tr_list = []
    for i in range(len(close_arr) - period, len(close_arr)):
        h = high_arr[i] if i < len(high_arr) else close_arr[i]
        l = low_arr[i] if i < len(low_arr) else close_arr[i]
        pc = close_arr[i - 1] if i > 0 else close_arr[i]
        if np.isnan(h) or np.isnan(l) or np.isnan(pc):
            continue
        tr = max(h - l, abs(h - pc), abs(l - pc))
        if not np.isnan(tr):
            tr_list.append(tr)

    if tr_list:
        return float(np.mean(tr_list))
    px = close_arr[-1] if len(close_arr) > 0 and close_arr[-1] > 0 and not np.isnan(close_arr[-1]) else 100
    return px * 0.02


def _update_account(ContextInfo):
    try:
        a = get_trade_detail_data(State.acc_id, 'stock', 'account')
        if a:
            State.cash = a[0].m_dAvailable
            State.total_assets = a[0].m_dBalance
            return
    except Exception:
        pass
    try:
        State.cash = ContextInfo.cash
        State.total_assets = ContextInfo.capital
    except Exception:
        pass


def _sync_position(ContextInfo, close_arr):
    current_price = close_arr[-1] if len(close_arr) > 0 else 0
    try:
        ps = get_trade_detail_data(State.acc_id, 'stock', 'position')
        has_real_pos = False
        for p in ps:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            if code == TARGET_STOCK and p.m_nVolume > 0:
                has_real_pos = True
                if State.position is None:
                    State.position = {
                        'shares': p.m_nVolume,
                        'entry_price': p.m_dOpenPrice,
                        'highest': max(p.m_dLastPrice, current_price),
                        'bars_held': 0,
                    }
                else:
                    State.position['shares'] = p.m_nVolume
                break
        if not has_real_pos and State.position is not None:
            State.position = None
    except Exception:
        pass


def _log_time(ContextInfo):
    return str(ContextInfo.barpos)
