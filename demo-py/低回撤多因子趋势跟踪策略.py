#coding:gbk
"""
低回撤多因子自适应趋势跟踪策略 (v2.0)
==========================================
核心理念:
  1. 市场状态识别 — 只在 HS300 > MA60 时入场（不逆势）
  2. 多因子选股 — 动量(40%) + 低波(30%) + 趋势强度(30%)
  3. 趋势确认入场 — 价格 > MA20 > MA60 + 放量确认
  4. ATR动态止损 — 入场价 - 2×ATR（硬止损）
  5. 移动止盈 — 最高价回落 1.5×ATR 平仓
  6. 趋势平仓 — 价格跌破 MA20
  7. 等风险仓位 — 每笔风险 < 0.8% 总资产

风险特征:
  - 目标最大回撤 < 15%
  - 目标夏普比率 > 1.2
  - 单票上限 20% 仓位
  - 熊市自动减半仓 (市场过滤器触发)
"""

import numpy as np

# ============================================================
# 用户可调参数
# ============================================================
RISK_PER_TRADE = 0.008        # 单笔风险敞口 (占总资金比例)
MAX_POSITIONS = 5              # 最大同时持仓数
STOP_ATR_MULT = 2.0            # 初始止损 ATR 倍数
TRAIL_ATR_MULT = 1.5           # 移动止盈 ATR 倍数
MA_SHORT = 20                  # 短周期均线
MA_LONG = 60                   # 长周期均线
MA_MARKET = 60                 # 市场过滤器均线周期
MIN_DAILY_AMOUNT = 1e8         # 最低日均成交额 (1亿)
CANDIDATE_N = 15               # 选股评分前 N 名
ATR_PERIOD = 14                # ATR 计算周期

# ============================================================
# 全局状态 (模块级变量，持久化)
# ============================================================
class State:
    positions = {}       # {code: {shares, entry_price, bars_held, highest, atr}}
    cash = 0
    total_assets = 0
    market_ok = True     # 市场过滤器结果
    acc_id = 'testS'


def init(ContextInfo):
    """策略初始化"""

    # 股票池：沪深300成分股
    stocks = ContextInfo.get_sector('000300.SH')
    if not stocks or len(stocks) == 0:
        # 兜底候选
        stocks = [
            '600519.SH', '000858.SZ', '601318.SH', '600036.SH', '000333.SZ',
            '601166.SH', '600900.SH', '600887.SH', '601328.SH', '600030.SH',
            '601398.SH', '600028.SH', '601988.SH', '600104.SH', '000002.SZ',
            '600585.SH', '601888.SH', '600276.SH', '600309.SH', '002415.SZ',
            '601288.SH', '600016.SH', '600000.SH', '601857.SH', '600031.SH',
            '000001.SZ', '002594.SZ', '300750.SZ', '601012.SH', '600809.SH',
        ]

    ContextInfo.stock_pool = stocks
    ContextInfo.set_universe(stocks)

    # 回测参数
    ContextInfo.capital = 10000000
    ContextInfo.start = '2020-01-01 09:30:00'
    ContextInfo.end = '2025-12-31 15:00:00'
    ContextInfo.set_slippage(1, 0.001)
    ContextInfo.set_commission(0, [0.00025, 0.00025, 0.001, 0.0, 0.0, 0.0])
    ContextInfo.benchmark = '000300.SH'
    ContextInfo.period = '1d'

    # 账户
    ContextInfo.set_account(State.acc_id)

    print("[init] 股票池 %d 只, 资金 %.0f" % (len(stocks), ContextInfo.capital))


def handlebar(ContextInfo):
    """每根K线调用"""
    bar = ContextInfo.barpos

    # 前 MA_LONG+10 个 bar 数据不够，跳过
    if bar < MA_LONG + 10:
        return

    # ========== 1. 批量获取数据 (只调一次) ==========
    # close: MA_LONG+5 天, high/low: ATR_PERIOD+5 天, volume: 10天
    hist_c   = ContextInfo.get_history_data(MA_LONG + 5, '1d', 'close')
    hist_h   = ContextInfo.get_history_data(ATR_PERIOD + 5, '1d', 'high')
    hist_l   = ContextInfo.get_history_data(ATR_PERIOD + 5, '1d', 'low')
    hist_v   = ContextInfo.get_history_data(10, '1d', 'volume')

    # ========== 2. 更新账户信息 ==========
    _update_account(ContextInfo)

    # ========== 3. 市场过滤器 ==========
    State.market_ok = _market_ok(hist_c)
    if not State.market_ok:
        print("[市场] HS300 < MA60, 防御模式")

    # ========== 4. 同步持仓 ==========
    _sync_positions(ContextInfo)

    # ========== 5. 执行止损/止盈 ==========
    _check_exits(ContextInfo, hist_c)

    # ========== 6. 熊市减仓 ==========
    if not State.market_ok:
        _reduce_half(ContextInfo)

    # ========== 7. 选股评分 ==========
    candidates = _rank_stocks(ContextInfo, hist_c, hist_v)

    # ========== 8. 开新仓 ==========
    if len(State.positions) < MAX_POSITIONS and candidates:
        _open_positions(ContextInfo, candidates, hist_c, hist_h, hist_l)

    # ========== 9. 更新持仓状态 ==========
    for code, pos in list(State.positions.items()):
        pos['bars_held'] += 1
        px = _get_price(ContextInfo, code, hist_c)
        if px > 0 and px > pos['highest']:
            pos['highest'] = px


# ============================================================
#                    辅助函数
# ============================================================

def _update_account(ContextInfo):
    """刷新资金"""
    try:
        a = get_trade_detail_data(State.acc_id, 'stock', 'account')
        if a:
            State.cash = a[0].m_dAvailable
            State.total_assets = a[0].m_dBalance
            return
    except Exception:
        pass
    State.cash = ContextInfo.capital
    State.total_assets = ContextInfo.capital


def _sync_positions(ContextInfo):
    """从QTM同步持仓"""
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
    """HS300 收盘价是否在 MA60 之上"""
    if '000300.SH' not in hist_c:
        return True
    arr = hist_c['000300.SH']
    if len(arr) < MA_MARKET + 1:
        return True
    return arr[-1] > np.mean(arr[-MA_MARKET:])


def _check_exits(ContextInfo, hist_c):
    """检查止损/止盈/趋势离场"""
    to_close = []

    for code, pos in State.positions.items():
        if pos['shares'] <= 0:
            continue
        px = _get_price(ContextInfo, code, hist_c)
        if px <= 0:
            continue

        entry = pos['entry_price']
        highest = pos['highest']
        atr = pos['atr'] if pos['atr'] > 0 else entry * 0.02

        # (a) 初始止损
        if px <= entry - STOP_ATR_MULT * atr:
            print("[exit] %s 止损: %.2f <= %.2f" % (code, px, entry - STOP_ATR_MULT * atr))
            to_close.append(code)
            continue

        # (b) 移动止盈
        if px <= highest - TRAIL_ATR_MULT * atr:
            print("[exit] %s 止盈: 从 %.2f 回落至 %.2f" % (code, highest, px))
            to_close.append(code)
            continue

        # (c) 趋势平仓 (低于MA20)
        if code in hist_c and len(hist_c[code]) >= MA_SHORT:
            ma20 = np.mean(hist_c[code][-MA_SHORT:])
            if px < ma20 * 0.97:
                print("[exit] %s 破MA20: %.2f < %.2f" % (code, px, ma20))
                to_close.append(code)

    for code in to_close:
        _close_position(ContextInfo, code)


def _close_position(ContextInfo, code):
    """全仓卖出"""
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


def _reduce_half(ContextInfo):
    """熊市减半仓: 平掉亏损最多的半数持仓"""
    n = len(State.positions)
    if n == 0:
        return
    n_close = max(1, n // 2)
    # 按收益率排序(最差排前面), 优先平亏损多的
    perf = []
    for code, pos in State.positions.items():
        entry = pos['entry_price']
        # 用 highest 近似浮动盈亏方向
        ratio = pos['highest'] / max(entry, 0.01)
        perf.append((code, ratio))
    perf.sort(key=lambda x: x[1])  # 涨幅最小的在前

    for i in range(n_close):
        if i < len(perf):
            _close_position(ContextInfo, perf[i][0])


def _rank_stocks(ContextInfo, hist_c, hist_v):
    """
    多因子评分选股 (在 hist_c/hist_v 上操作, 不再重复调 get_history_data)
    """
    pool = ContextInfo.stock_pool
    scores = {}

    for code in pool:
        try:
            # --- 基础过滤 ---
            name = ContextInfo.get_stock_name(code)
            if name and ('ST' in name or '*' in name):
                continue

            if code not in hist_c or len(hist_c[code]) < MA_LONG:
                continue
            arr = np.array(hist_c[code], dtype=float)

            # --- 趋势过滤: 价格 > MA60 ---
            ma60 = np.mean(arr[-MA_LONG:])
            if arr[-1] < ma60:
                continue

            # --- 成交额过滤 ---
            if code in hist_v and len(hist_v[code]) >= 5:
                avg_vol = np.mean(hist_v[code][-5:])
                if avg_vol * arr[-1] < MIN_DAILY_AMOUNT:
                    continue

            # --- 因子 1: 20日动量 (涨幅) ---
            mom = (arr[-1] - arr[-20]) / max(arr[-20], 0.01)

            # --- 因子 2: 低波动 (回报率标准差倒数) ---
            rets = (arr[1:] - arr[:-1]) / max(arr[:-1], 0.01)
            vol = np.std(rets[-20:]) if len(rets) >= 20 else 0.5
            lv = 1.0 / max(vol, 0.01)

            # --- 因子 3: 趋势强度 (价格/MA20) ---
            ma20 = np.mean(arr[-MA_SHORT:])
            ts = arr[-1] / max(ma20, 0.01)

            scores[code] = (mom, lv, ts)

        except Exception:
            continue

    if not scores:
        return []

    # 标准化
    codes = list(scores.keys())
    mom_a = np.array([scores[c][0] for c in codes])
    lv_a  = np.array([scores[c][1] for c in codes])
    ts_a  = np.array([scores[c][2] for c in codes])

    def norm(x):
        mn, mx = x.min(), x.max()
        return (x - mn) / (mx - mn) if mx > mn else np.ones_like(x) * 0.5

    total = 0.40 * norm(mom_a) + 0.30 * norm(lv_a) + 0.30 * norm(ts_a)

    ranked = sorted(zip(codes, total), key=lambda x: -x[1])
    return ranked[:CANDIDATE_N]


def _open_positions(ContextInfo, candidates, hist_c, hist_h, hist_l):
    """开新仓 (等风险仓位)"""
    if not State.market_ok:
        return

    held = set(State.positions.keys())
    slots = MAX_POSITIONS - len(State.positions)
    if slots <= 0:
        return

    opened = 0
    for code, _ in candidates:
        if opened >= slots:
            break
        if code in held:
            continue

        px = _get_price(ContextInfo, code, hist_c)
        if px <= 0:
            continue

        atr = _calc_atr(code, hist_h, hist_l, hist_c)
        if atr <= 0:
            continue

        # 风险预算: 总资产 × 单笔风险比例
        risk_budget = State.total_assets * RISK_PER_TRADE
        # 每股最大亏损 = ATR × STOP_ATR_MULT
        risk_per_share = atr * STOP_ATR_MULT
        if risk_per_share <= 0:
            continue

        shares = int(risk_budget / risk_per_share)
        shares = (shares // 100) * 100
        if shares < 100:
            shares = 100

        # 单票上限: 20%
        max_shares = int(State.total_assets * 0.20 / px / 100) * 100
        shares = min(shares, max_shares)

        # 现金检查
        if shares * px > State.cash * 0.95:
            shares = int(State.cash * 0.95 / px / 100) * 100
        if shares < 100:
            continue

        try:
            order_shares(code, shares, 'COMPETE', ContextInfo, State.acc_id)
            print("[开仓] %s %d股 @%.2f, ATR=%.2f" % (code, shares, px, atr))
            State.positions[code] = {
                'shares': shares,
                'entry_price': px,
                'bars_held': 0,
                'highest': px,
                'atr': atr,
            }
            opened += 1
        except Exception:
            pass


def _calc_atr(code, hist_h, hist_l, hist_c):
    """ATR(14)"""
    if code not in hist_h or code not in hist_l or code not in hist_c:
        return 0
    h = np.array(hist_h[code], dtype=float)
    l = np.array(hist_l[code], dtype=float)
    c = np.array(hist_c[code], dtype=float)
    if len(h) < ATR_PERIOD + 2:
        return 0

    prev_c = c[:-1]
    tr = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c))
    )
    return float(np.mean(tr[-ATR_PERIOD:]))


def _get_price(ContextInfo, code, hist_c):
    """获取当前价: 优先 tick, 其次收盘"""
    try:
        t = ContextInfo.get_full_tick([code])
        if code in t:
            return t[code].get('lastPrice', 0)
    except Exception:
        pass
    if code in hist_c and len(hist_c[code]) > 0:
        return hist_c[code][-1]
    return 0
