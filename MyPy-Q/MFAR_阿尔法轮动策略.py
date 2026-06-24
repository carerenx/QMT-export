#coding:gbk
"""
增强版多因子趋势跟踪策略 (Enhanced Multi-Factor Trend v3.0)
=============================================================

基于原始"低回撤多因子趋势跟踪策略 v2.0"（2020-2025: +77.3%, 10.4%年化）的增强版。

保留原策略的所有核心要素:
  1. 三因子评分: 动量(40%) + 低波(30%) + 趋势强度(30%)
  2. ATR自适应止损/止盈
  3. 市场过滤器: HS300 > MA50
  4. 冷却期 + 最低持有天
  5. 等风险仓位管理
  6. 弱市减半仓防御

增强改进 (vs 原始 v2.0):
  1. 牛市风险: 2.0% (原1.5%) — 牛市更激进
  2. 牛市持仓: 8只 (原6只) — 更多分散
  3. 冷却期缩短: 10天 (原20天) — 更快回归
  4. 最低持有: 5天 (原10天) — 更灵活
  5. 移动止盈: 3.0×ATR (原2.5×) — 更宽, 让利润奔跑
  6. 扩展股票池: 70只 (原55只) — 更多中盘alpha
  7. 弱市减速平缓: 平1/3仓位 (vs 平一半)

2020-2025 回测目标:
  - 年化收益: 12-18% (在10.4%基础上优化)
  - 最大回撤: <25%
  - 夏普: >0.5

重要说明:
  该回测使用2020-2025数据。在此期间上证指数/CIS300回报率接近零。
  策略在如此困难的环境中仍实现了正向回报。
  在趋势性更强的市场（如2014-2015, 2019）中，预期回报将大幅提高。
  年化50%+的实现需要杠杆或趋势性牛市环境，而非策略本身的问题。
"""

import numpy as np

# ============================================================
# 策略参数 (增强版)
# ============================================================
RISK_PER_TRADE = 0.020          # 牛市单笔风险 (2%, 原1.5%)
RISK_PER_TRADE_WEAK = 0.010     # 弱市单笔风险 (1%)
MAX_POSITIONS = 8               # 牛市最大持仓 (原6只)
STOP_ATR_MULT = 3.0             # 初始止损 ATR (同原版)
TRAIL_ATR_MULT = 3.0            # 移动止盈 ATR (原2.5x, 放宽)
MA_SHORT = 20
MA_LONG = 60
MA_MARKET = 50
CANDIDATE_N = 20                # 候选数 (原15, 扩大)
ATR_PERIOD = 14
MIN_HOLD_BARS = 5               # 最低持有 (原10天, 缩短)
COOLDOWN_BARS = 10              # 冷却期 (原20天, 缩短)
MAX_POSITION_PCT = 0.22         # 单票上限 (原20%)


class State:
    stock_pool = []              # 全量股票池
    filtered_pool = []           # 过滤后候选池
    positions = {}               # {code: {shares, entry_price, bars_held, highest, atr}}
    cash = 0
    total_assets = 0
    market_ok = True
    acc_id = 'testS'
    capital = 10000000
    last_barpos = -1
    cooldown = {}                # {code: bars_remaining}
    peak_assets = 0


def init(ContextInfo):
    """策略初始化 — 70只精选蓝筹+中盘成长"""
    stocks = [
        # === 大盘蓝筹 ===
        '600519.SH', '000858.SZ', '600809.SH', '000568.SZ',
        '601318.SH', '600036.SH', '601166.SH', '600030.SH', '601398.SH',
        '601328.SH', '601288.SH', '600016.SH', '600000.SH', '600837.SH',
        '000001.SZ', '002142.SZ',
        '000333.SZ', '600690.SH', '000651.SZ',
        '300750.SZ', '002594.SZ', '601012.SH', '600104.SH',
        '600276.SH', '300760.SZ', '000538.SZ',
        '002415.SZ', '688981.SH', '603986.SH',
        '601857.SH', '600028.SH', '600585.SH', '601088.SH',
        '601668.SH', '000002.SZ', '600031.SH',
        '600887.SH', '600900.SH', '601888.SH', '002714.SZ',

        # === 中盘成长 (弹性alpha源) ===
        '002475.SZ', '002049.SZ', '002230.SZ', '000725.SZ', '300433.SZ',
        '300274.SZ', '300014.SZ', '002460.SZ',
        '300122.SZ', '002007.SZ', '000963.SZ',
        '300124.SZ', '000625.SZ', '002050.SZ',
        '600111.SH', '600188.SH',
        '002013.SZ',
        '600025.SH', '003816.SZ',
        '002304.SZ', '000895.SZ', '300498.SZ',
        '300413.SZ', '002555.SZ', '300418.SZ',
        '600048.SH',
        # 额外中盘alpha
        '601615.SH', '601238.SH', '600733.SH',
    ]

    valid = []
    for c in stocks:
        try:
            n = ContextInfo.get_stock_name(c)
            if n and len(n) > 0 and 'ST' not in n and '*' not in n:
                valid.append(c)
        except Exception:
            pass
    State.stock_pool = valid
    State.filtered_pool = valid[:]
    ContextInfo.set_universe(valid)

    for attr, val in [('capital', State.capital), ('benchmark', '000300.SH'),
                      ('start', '2020-01-01 09:30:00'), ('end', '2025-12-31 15:00:00')]:
        try:
            setattr(ContextInfo, attr, val)
        except (AttributeError, TypeError):
            pass
    ContextInfo.set_slippage(1, 0.001)
    ContextInfo.set_commission(0, [0.00025, 0.00025, 0.001, 0.0, 0.0, 0.0])
    ContextInfo.set_account(State.acc_id)

    print("[增强 v3 init] 股票池 %d 只" % len(stocks))


def handlebar(ContextInfo):
    bar = ContextInfo.barpos

    if bar < MA_LONG + 10:
        return

    if bar == State.last_barpos:
        return
    State.last_barpos = bar

    # ========== 1. 行情数据 ==========
    hist_c = ContextInfo.get_history_data(MA_LONG + 10, '1d', 'close')
    hist_h = ContextInfo.get_history_data(ATR_PERIOD + 10, '1d', 'high')
    hist_l = ContextInfo.get_history_data(ATR_PERIOD + 10, '1d', 'low')

    # ========== 2. 冷却期更新 ==========
    for code in list(State.cooldown.keys()):
        State.cooldown[code] -= 1
        if State.cooldown[code] <= 0:
            del State.cooldown[code]

    # ========== 3. 账户信息 ==========
    _update_account(ContextInfo)
    if State.total_assets > State.peak_assets:
        State.peak_assets = State.total_assets

    # ========== 4. 市场过滤器 ==========
    State.market_ok = _market_ok(hist_c)
    effective_max = MAX_POSITIONS if State.market_ok else max(2, MAX_POSITIONS // 2)
    risk_pct = RISK_PER_TRADE if State.market_ok else RISK_PER_TRADE_WEAK

    if not State.market_ok:
        print("[市场] HS300 < MA50, 防御模式 (仓位%d/%d)" % (effective_max, MAX_POSITIONS))

    # ========== 5. 同步持仓 ==========
    _sync_positions(ContextInfo)

    # ========== 6. 退出检查 ==========
    _check_exits(ContextInfo, hist_c)

    # ========== 7. 弱市减仓 (温和: 1/3) ==========
    if not State.market_ok:
        _reduce_third(ContextInfo)

    # ========== 8. 三因子选股 ==========
    candidates = _rank_stocks(ContextInfo, hist_c)
    print("[选股] 候选 %d 只" % len(candidates))

    # ========== 9. 开新仓 ==========
    if len(State.positions) < effective_max and candidates:
        _open_positions(ContextInfo, candidates, hist_c, hist_h, hist_l, risk_pct)

    # ========== 10. 更新持仓状态 ==========
    for code, pos in list(State.positions.items()):
        pos['bars_held'] += 1
        px = _get_price(ContextInfo, code, hist_c)
        if px > 0 and px > pos['highest']:
            pos['highest'] = px

    print("[汇总] 持仓=%d/%d 资产=%.0f 市场=%s" % (
        len(State.positions), effective_max, State.total_assets,
        "可交易" if State.market_ok else "防御"))


# ============================================================
#                    辅助函数
# ============================================================

def _update_account(ContextInfo):
    try:
        a = get_trade_detail_data(State.acc_id, 'stock', 'account')
        if a:
            State.cash = a[0].m_dAvailable
            State.total_assets = a[0].m_dBalance
            return
    except Exception:
        pass
    State.cash = State.capital
    State.total_assets = State.capital


def _sync_positions(ContextInfo):
    try:
        ps = get_trade_detail_data(State.acc_id, 'stock', 'position')
        new = {}
        for p in ps:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            vol = p.m_nVolume
            if vol <= 0:
                continue
            if code in State.positions:
                old = State.positions[code]
                old['shares'] = vol
                new[code] = old
            else:
                new[code] = {
                    'shares': vol,
                    'entry_price': p.m_dOpenPrice,
                    'bars_held': 0,
                    'highest': p.m_dLastPrice,
                    'atr': 0,
                }
        State.positions = new
    except Exception:
        pass


def _market_ok(hist_c):
    """HS300 > MA50"""
    if '000300.SH' not in hist_c:
        return True
    arr = np.array(hist_c['000300.SH'], dtype=float)
    if len(arr) < MA_MARKET + 1:
        return True
    return arr[-1] > np.mean(arr[-MA_MARKET:])


def _check_exits(ContextInfo, hist_c):
    """三重退出: 硬止损 + ATR止盈 + MA20趋势退出"""
    to_close = []

    for code, pos in State.positions.items():
        if pos['shares'] <= 0:
            continue
        px = _get_price(ContextInfo, code, hist_c)
        if px <= 0:
            continue

        entry = pos['entry_price']
        highest = pos['highest']
        atr_val = pos['atr'] if pos['atr'] > 0 else entry * 0.02

        # (a) 初始止损
        if px <= entry - STOP_ATR_MULT * atr_val:
            print("[退出] %s 止损: %.2f <= %.2f" % (code, px, entry - STOP_ATR_MULT * atr_val))
            to_close.append(code)
            continue

        # (b) 移动止盈
        if px <= highest - TRAIL_ATR_MULT * atr_val:
            print("[退出] %s 止盈: %.2f→%.2f" % (code, highest, px))
            to_close.append(code)
            continue

        # (c) 趋势平仓 (仅持有>=MIN_HOLD_BARS天)
        if pos.get('bars_held', 0) >= MIN_HOLD_BARS:
            if code in hist_c and len(hist_c[code]) >= MA_SHORT:
                c_arr = np.array(hist_c[code], dtype=float)
                ma20 = np.mean(c_arr[-MA_SHORT:])
                if px < ma20 * 0.97:
                    print("[退出] %s 破MA20: %.2f < %.2f" % (code, px, ma20 * 0.97))
                    to_close.append(code)

    for code in to_close:
        _close_position(ContextInfo, code)


def _close_position(ContextInfo, code):
    if code not in State.positions:
        return
    shares = State.positions[code]['shares']
    if shares <= 0:
        return
    try:
        order_shares(code, -shares, 'COMPETE', ContextInfo, State.acc_id)
        print("[平仓] %s %d股" % (code, shares))
    except Exception:
        pass
    State.positions.pop(code, None)
    State.cooldown[code] = COOLDOWN_BARS


def _reduce_third(ContextInfo):
    """弱市减1/3仓位 (温和, 非激进)"""
    n = len(State.positions)
    if n == 0:
        return
    n_close = max(1, n // 3)
    perf = []
    for code, pos in State.positions.items():
        entry = pos['entry_price']
        ratio = pos['highest'] / max(entry, 0.01)
        perf.append((code, ratio))
    perf.sort(key=lambda x: x[1])
    for i in range(n_close):
        if i < len(perf):
            _close_position(ContextInfo, perf[i][0])


def _rank_stocks(ContextInfo, hist_c):
    """三因子评分: 动量(40%) + 低波(30%) + 趋势强度(30%)"""
    pool = State.filtered_pool
    scores = {}

    for code in pool:
        if code in State.cooldown:
            continue
        try:
            if code not in hist_c or len(hist_c[code]) < MA_SHORT:
                continue
            arr = np.array(hist_c[code], dtype=float)

            ma20 = np.mean(arr[-MA_SHORT:])
            if arr[-1] < ma20:
                continue

            mom = (arr[-1] - arr[-20]) / max(arr[-20], 0.01)
            rets = (arr[1:] - arr[:-1]) / np.maximum(arr[:-1], 0.01)
            vol = np.std(rets[-20:]) if len(rets) >= 20 else 0.5
            lv = 1.0 / max(vol, 0.01)
            ts = arr[-1] / max(ma20, 0.01)

            scores[code] = (mom, lv, ts)
        except Exception:
            continue

    if not scores:
        return []

    codes = list(scores.keys())
    mom_a = np.array([scores[c][0] for c in codes])
    lv_a = np.array([scores[c][1] for c in codes])
    ts_a = np.array([scores[c][2] for c in codes])

    def norm(x):
        mn, mx = x.min(), x.max()
        return (x - mn) / (mx - mn) if mx > mn else np.ones_like(x) * 0.5

    total = 0.40 * norm(mom_a) + 0.30 * norm(lv_a) + 0.30 * norm(ts_a)
    ranked = sorted(zip(codes, total), key=lambda x: -x[1])
    return ranked[:CANDIDATE_N]


def _open_positions(ContextInfo, candidates, hist_c, hist_h, hist_l, risk_pct):
    """等风险开仓"""
    held = set(State.positions.keys())
    slots = MAX_POSITIONS - len(State.positions)
    if slots <= 0:
        return

    opened = 0
    for code, score in candidates:
        if opened >= slots:
            break
        if code in held:
            continue

        px = _get_price(ContextInfo, code, hist_c)
        if px <= 0:
            continue

        atr_val = _calc_atr(code, hist_h, hist_l, hist_c)
        if atr_val <= 0:
            continue

        risk_budget = State.total_assets * risk_pct
        risk_per_share = atr_val * STOP_ATR_MULT
        if risk_per_share <= 0:
            continue

        shares = int(risk_budget / risk_per_share)
        shares = (shares // 100) * 100
        if shares < 100:
            shares = 100

        max_shares = int(State.total_assets * MAX_POSITION_PCT / px / 100) * 100
        shares = min(shares, max_shares)

        if shares * px > State.cash * 0.95:
            shares = int(State.cash * 0.95 / px / 100) * 100
        if shares < 100:
            continue

        try:
            order_shares(code, shares, 'COMPETE', ContextInfo, State.acc_id)
            print("[开仓] %s %d股 @%.2f ATR=%.2f score=%.3f" % (
                code, shares, px, atr_val, score))
            State.positions[code] = {
                'shares': shares,
                'entry_price': px,
                'bars_held': 0,
                'highest': px,
                'atr': atr_val,
            }
            opened += 1
        except Exception:
            pass


def _calc_atr(code, hist_h, hist_l, hist_c):
    if code not in hist_h or code not in hist_l or code not in hist_c:
        return 0
    h = np.array(hist_h[code], dtype=float)
    l = np.array(hist_l[code], dtype=float)
    c_all = np.array(hist_c[code], dtype=float)
    if len(h) < ATR_PERIOD + 2:
        return 0
    c = c_all[-len(h):]
    prev_c = c[:-1]
    tr = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c))
    )
    return float(np.mean(tr[-ATR_PERIOD:]))


def _get_price(ContextInfo, code, hist_c):
    try:
        t = ContextInfo.get_full_tick([code])
        if code in t:
            return t[code].get('lastPrice', 0)
    except Exception:
        pass
    if code in hist_c and len(hist_c[code]) > 0:
        return hist_c[code][-1]
    return 0
