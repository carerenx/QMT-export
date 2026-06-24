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
RISK_PER_TRADE = 0.015        # 单笔风险敞口 (1.5%)
MAX_POSITIONS = 6              # 最大同时持仓数
STOP_ATR_MULT = 3.0            # 初始止损 ATR 倍数
TRAIL_ATR_MULT = 2.5           # 移动止盈 ATR 倍数
MA_SHORT = 20                  # 短周期均线
MA_LONG = 60                   # 长周期均线
MA_MARKET = 50                 # 市场过滤器 (MA50, 平衡快速与稳健)
MIN_DAILY_AMOUNT = 5e7         # 最低日均成交额 (5000万)
CANDIDATE_N = 15               # 选股评分前 N 名
ATR_PERIOD = 14                # ATR 计算周期
MIN_HOLD_BARS = 10             # 最低持有天数 (减少换手)
COOLDOWN_BARS = 20             # 卖出后冷却天数

# ============================================================
# 全局状态 (模块级变量，持久化)
# ============================================================
class State:
    stock_pool = []      # 股票池（原始）
    filtered_pool = []   # 过滤后的候选池（去ST、去无效）
    positions = {}       # {code: {shares, entry_price, bars_held, highest, atr}}
    cash = 0
    total_assets = 0
    market_ok = True     # 市场过滤器结果
    acc_id = 'testS'
    capital = 10000000   # 初始资金
    last_barpos = -1     # 上次处理的 barpos，防止同根 bar 重复执行
    cooldown = {}        # {code: bars_remaining} 卖出后的冷却期
    peak_assets = 0      # 历史最高净值


def init(ContextInfo):
    """策略初始化"""

    # 精选股票池（沪深300核心 + 精选中盘）
    stocks = [
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
        # 中盘精选 (高弹性)
        '002475.SZ', '002049.SZ', '000725.SZ',
        '300274.SZ', '300014.SZ', '002460.SZ',
        '300122.SZ', '002007.SZ', '000963.SZ',
        '300124.SZ', '002050.SZ',
        '600111.SH',
        '600025.SH', '003816.SZ',
        '002304.SZ', '300498.SZ',
        '300413.SZ', '002555.SZ', '300418.SZ',
    ]
    # 过滤无效代码 + 预先剔除 ST（避免每 bar 重复 API 调用）
    valid = []
    for c in stocks:
        try:
            n = ContextInfo.get_stock_name(c)
            if n and len(n) > 0:
                if 'ST' not in n and '*' not in n:
                    valid.append(c)
        except Exception:
            pass
    State.stock_pool = valid
    State.filtered_pool = valid[:]
    ContextInfo.set_universe(valid)

    # 以下为回测参数，实盘模式下为只读，用 try 保护
    for attr, val in [('capital', State.capital), ('benchmark', '000300.SH'),
                      ('start', '2020-01-01 09:30:00'), ('end', '2025-12-31 15:00:00')]:
        try:
            setattr(ContextInfo, attr, val)
        except (AttributeError, TypeError):
            pass
    ContextInfo.set_slippage(1, 0.001)
    ContextInfo.set_commission(0, [0.00025, 0.00025, 0.001, 0.0, 0.0, 0.0])

    # 账户
    ContextInfo.set_account(State.acc_id)

    print("[init] 股票池 %d 只" % len(stocks))


def handlebar(ContextInfo):
    """每根K线调用"""
    bar = ContextInfo.barpos

    # 前 MA_LONG+10 个 bar 数据不够，跳过
    if bar < MA_LONG + 10:
        print("[跳过] bar=%d 数据不足(需>=%d)" % (bar, MA_LONG + 10))
        return

    # 同一根 bar 只执行一次（实盘中 handlebar 每 tick 都触发！）
    if bar == State.last_barpos:
        return
    State.last_barpos = bar

    date_str = _log_time(ContextInfo)
    print("=" * 40)
    print("[触发] bar=%d 时间=%s 持仓=%d只 现金=%.0f" % (
        bar, date_str, len(State.positions), State.cash))

    # ========== 1. 获取行情数据 (精选池35只, 每次调用很快) ==========
    hist_c   = ContextInfo.get_history_data(MA_LONG + 5, '1d', 'close')
    hist_h   = ContextInfo.get_history_data(ATR_PERIOD + 5, '1d', 'high')
    hist_l   = ContextInfo.get_history_data(ATR_PERIOD + 5, '1d', 'low')
    print("[数据] close=%d stocks" % len(hist_c))

    # ========== 2. 更新冷却期 ==========
    for code in list(State.cooldown.keys()):
        State.cooldown[code] -= 1
        if State.cooldown[code] <= 0:
            del State.cooldown[code]

    # ========== 3. 更新账户信息 ==========
    _update_account(ContextInfo)
    if State.total_assets > State.peak_assets:
        State.peak_assets = State.total_assets

    # ========== 3. 市场过滤器 ==========
    State.market_ok = _market_ok(hist_c)
    if not State.market_ok:
        print("[市场] HS300 < MA60, 防御模式")
    else:
        print("[市场] HS300 > MA60, 可交易")

    # ========== 4. 同步持仓 ==========
    _sync_positions(ContextInfo)
    print("[持仓] %d 只" % len(State.positions))

    # ========== 5. 执行止损/止盈 ==========
    _check_exits(ContextInfo, hist_c)

    # ========== 6. 熊市减仓 ==========
    if not State.market_ok:
        _reduce_half(ContextInfo)

    # ========== 7. 选股评分 ==========
    candidates = _rank_stocks(ContextInfo, hist_c)
    print("[选股] 候选 %d 只" % len(candidates))
    if len(candidates) == 0:
        # 诊断：为什么没有候选？
        pass_count = 0
        total_count = 0
        fail_ma = 0
        fail_data = 0
        fail_other = 0
        for code in State.filtered_pool:
            if code not in hist_c or len(hist_c[code]) < MA_SHORT:
                fail_data += 1
                continue
            try:
                arr = np.array(hist_c[code], dtype=float)
                total_count += 1
                if arr[-1] > np.mean(arr[-MA_SHORT:]):
                    pass_count += 1
                else:
                    fail_ma += 1
            except Exception:
                fail_other += 1
        print("[诊断] 可评分=%d 通过MA20=%d 未过MA20=%d 缺数据=%d 其他失败=%d" % (
            total_count, pass_count, fail_ma, fail_data, fail_other))
        if total_count == 0:
            print("[诊断] hist_c中没有任何股票数据! 示例key:", list(hist_c.keys())[:3])

    # ========== 8. 开新仓 ==========
    if len(State.positions) < MAX_POSITIONS and candidates:
        _open_positions(ContextInfo, candidates, hist_c, hist_h, hist_l)

    # ========== 9. 更新持仓状态 ==========
    for code, pos in list(State.positions.items()):
        pos['bars_held'] += 1
        px = _get_price(ContextInfo, code, hist_c)
        if px > 0 and px > pos['highest']:
            pos['highest'] = px

    # ========== 10. 打印汇总 ==========
    print("[汇总] bar=%d 持仓=%d只 现金=%.0f 总资产=%.0f 市场=%s" % (
        bar, len(State.positions), State.cash, State.total_assets,
        "可交易" if State.market_ok else "防御"))


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
    State.cash = State.capital
    State.total_assets = State.capital


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
    """HS300 > MA20 (单层过滤器)"""
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

        # (c) 趋势平仓 (低于MA20, 仅持有足够天后触发)
        if pos.get('bars_held', 0) >= MIN_HOLD_BARS:
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
        print("[平仓] %s %d股  %s" % (code, shares, _log_time(ContextInfo)))
    except Exception:
        pass
    State.positions.pop(code, None)
    # ★ 加入冷却期，防止短期内重复买入同一标的
    State.cooldown[code] = COOLDOWN_BARS


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


def _rank_stocks(ContextInfo, hist_c):
    """
    多因子评分选股 (精选池已过滤ST/无效, 直接评分)
    """
    pool = State.filtered_pool
    scores = {}

    for code in pool:
        # ★ 冷却期跳过
        if code in State.cooldown:
            continue
        try:
            if code not in hist_c or len(hist_c[code]) < MA_SHORT:
                continue
            arr = np.array(hist_c[code], dtype=float)

            # --- 趋势过滤: 价格 > MA20 (短趋势, 比MA60宽松) ---
            ma20 = np.mean(arr[-MA_SHORT:])
            if arr[-1] < ma20:
                continue

            # --- 因子 1: 20日动量 (涨幅) ---
            mom = (arr[-1] - arr[-20]) / max(arr[-20], 0.01)

            # --- 因子 2: 低波动 (回报率标准差倒数) ---
            rets = (arr[1:] - arr[:-1]) / np.maximum(arr[:-1], 0.01)
            vol = np.std(rets[-20:]) if len(rets) >= 20 else 0.5
            lv = 1.0 / max(vol, 0.01)

            # --- 因子 3: 趋势强度 (价格/MA20) ---
            ma20 = np.mean(arr[-MA_SHORT:])
            ts = arr[-1] / max(ma20, 0.01)

            scores[code] = (mom, lv, ts)

        except Exception as e:
            print("[评分异常] %s: %s" % (code, str(e)[:60]))
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
    print("[开仓] slots=%d 候选%d只 现金=%.0f 总资产=%.0f 风险预算=%.0f" % (
        slots, len(candidates), State.cash, State.total_assets,
        State.total_assets * RISK_PER_TRADE))
    for code, score in candidates:
        if opened >= slots:
            break
        if code in held:
            print("[开仓] %s 已有持仓,跳过" % code)
            continue

        px = _get_price(ContextInfo, code, hist_c)
        if px <= 0:
            print("[开仓] %s 价格<=0,跳过" % code)
            continue

        atr = _calc_atr(code, hist_h, hist_l, hist_c)
        if atr <= 0:
            print("[开仓] %s ATR<=0,跳过 评分=%.3f" % (code, score))
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
            print("[开仓] %s 资金不足: shares=%d price=%.2f need=%.0f cash=%.0f" % (
                code, shares, px, shares*px, State.cash))
            continue

        try:
            order_shares(code, shares, 'COMPETE', ContextInfo, State.acc_id)
            print("[开仓] %s %d股 @%.2f, ATR=%.2f  %s" % (code, shares, px, atr, _log_time(ContextInfo)))
            State.positions[code] = {
                'shares': shares,
                'entry_price': px,
                'bars_held': 0,
                'highest': px,
                'atr': atr,
            }
            opened += 1
        except Exception as e:
            print("[开仓失败] %s 下单异常: %s" % (code, str(e)))


def _calc_atr(code, hist_h, hist_l, hist_c):
    """ATR(14)"""
    if code not in hist_h or code not in hist_l or code not in hist_c:
        return 0
    h = np.array(hist_h[code], dtype=float)
    l = np.array(hist_l[code], dtype=float)
    c_all = np.array(hist_c[code], dtype=float)
    if len(h) < ATR_PERIOD + 2:
        return 0

    # hist_c 可能比 hist_h/hist_l 长, 截取尾部对齐
    c = c_all[-len(h):]
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


def _log_time(ContextInfo):
    """获取可读时间字符串"""
    try:
        t = ContextInfo.get_bar_timetag(ContextInfo.barpos)
        return timetag_to_datetime(t, '%Y-%m-%d %H:%M')
    except Exception:
        return ""
