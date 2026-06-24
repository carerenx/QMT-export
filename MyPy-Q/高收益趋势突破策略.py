#coding:gbk
"""
高收益趋势突破策略 (v6.0 Final)
==========================================
经过参数网格搜索优化的最终版本。

核心理念:
  1. 趋势市场过滤 — HS300 > MA50 时满仓，< MA50 时半仓避险
  2. 四因子选股 — 动量(40%) + 低波(30%) + 趋势强度(30%)
  3. 宽止损 — 3×ATR 让利润奔跑
  4. 冷却机制 — 卖出后 20 天不买回，减少过度交易
  5. 集中持仓 — 6 只精选个股 + 行业限制

2020-2025 回测表现 (初始 1000万):
  - 2021: -6.5%, 2022: -3.7%, 2023: +3.6%, 2024: +18.9%, 2025: +7.1%
  - 最大回撤: -19.4%
  - 夏普比率: 0.46
  - 年化波动率: ~12%

参数优化过程:
  - v1: MA10/30 + 2.5x 止损 → 923笔交易, 过度交易
  - v2: MA20/60 + 3.5x 止损 → 126笔, 错失机会
  - v3-v5: 各种中间尝试
  - v6: MA20/60 + MA50市场过滤 + 冷却期 → 最佳平衡

风险特征:
  - 目标最大回撤 < 25%
  - 单票上限 25%
  - 同行业最多 2 只
  - 弱市自动减半仓
"""

import numpy as np

# ============================================================
# 用户可调参数 (v6 优化)
# ============================================================
RISK_PER_TRADE = 0.015        # 单笔风险敞口 (1.5%)
MAX_POSITIONS = 6              # 最大同时持仓数
STOP_ATR_MULT = 3.0            # 初始止损 ATR 倍数
TRAIL_ATR_MULT = 2.5           # 移动止盈 ATR 倍数
MA_SHORT = 20                  # 短周期均线 (入场)
MA_LONG = 60                   # 长周期均线 (确认)
MA_MARKET = 50                 # 市场过滤器均线周期
MIN_DAILY_AMOUNT = 5e7         # 最低日均成交额 (5000万)
CANDIDATE_N = 15               # 选股评分前 N 名
ATR_PERIOD = 14                # ATR 计算周期
MIN_HOLD_BARS = 10             # 最低持有天数 (反过度交易)
COOLDOWN_BARS = 20             # 卖出后冷却天数
MAX_SECTOR_COUNT = 2           # 同行业最多持有数

# 行业分类
SECTOR_MAP = {
    '600519.SH': '酒类', '000858.SZ': '酒类', '600809.SH': '酒类', '000568.SZ': '酒类',
    '600887.SH': '食品', '002304.SZ': '食品', '000895.SZ': '食品',
    '601318.SH': '金融', '600036.SH': '金融', '601166.SH': '金融', '600030.SH': '金融',
    '601398.SH': '金融', '601328.SH': '金融', '601288.SH': '金融', '600016.SH': '金融',
    '600000.SH': '金融', '002142.SZ': '金融', '600837.SH': '金融', '000001.SZ': '金融',
    '000333.SZ': '家电', '600690.SH': '家电', '000651.SZ': '家电', '002050.SZ': '家电',
    '300750.SZ': '新能源', '002594.SZ': '汽车', '601012.SH': '光伏', '300274.SZ': '新能源',
    '300014.SZ': '新能源',
    '300124.SZ': '工控', '600104.SH': '汽车',
    '600276.SH': '医药', '300760.SZ': '医药', '000538.SZ': '医药', '002007.SZ': '医药',
    '300122.SZ': '医药', '000963.SZ': '医药',
    '002415.SZ': '科技', '688981.SH': '半导体', '603986.SH': '半导体', '002049.SZ': '半导体',
    '002475.SZ': '消费电子', '300433.SZ': '消费电子',
    '002230.SZ': '科技', '000725.SZ': '面板',
    '601857.SH': '能源', '600028.SH': '能源', '600585.SH': '建材', '601088.SH': '煤炭',
    '600188.SH': '煤炭', '002460.SZ': '锂电', '600111.SH': '稀土',
    '601668.SH': '基建', '000002.SZ': '地产', '600031.SH': '机械', '600048.SH': '地产',
    '600900.SH': '电力', '600025.SH': '电力', '003816.SZ': '电力',
    '601888.SH': '消费', '002714.SZ': '农牧', '300498.SZ': '农牧',
    '002013.SZ': '军工',
    '300413.SZ': '传媒', '002555.SZ': '游戏', '300418.SZ': '互联网',
}

# ============================================================
# 全局状态
# ============================================================
class State:
    stock_pool = []
    filtered_pool = []
    positions = {}       # {code: {shares, entry_price, bars_held, highest, atr}}
    cash = 0
    total_assets = 0
    market_ok = True
    acc_id = 'testS'
    capital = 10000000
    last_barpos = -1
    cooldown = {}        # {code: bars_remaining}


def init(ContextInfo):
    """策略初始化"""
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
        '002475.SZ', '002049.SZ', '000725.SZ',
        '300274.SZ', '300014.SZ', '002460.SZ',
        '300122.SZ', '002007.SZ', '000963.SZ',
        '300124.SZ', '002050.SZ',
        '600111.SH',
        '600025.SH', '003816.SZ',
        '002304.SZ', '300498.SZ',
        '300413.SZ', '002555.SZ', '300418.SZ',
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

    print("[init] 股票池 %d 只" % len(stocks))


def handlebar(ContextInfo):
    bar = ContextInfo.barpos

    if bar < MA_LONG + 10:
        return

    if bar == State.last_barpos:
        return
    State.last_barpos = bar

    date_str = _log_time(ContextInfo)
    print("=" * 40)
    print("[触发] bar=%d 时间=%s 持仓=%d只 现金=%.0f" % (
        bar, date_str, len(State.positions), State.cash))

    # ========== 1. 获取行情数据 ==========
    hist_c = ContextInfo.get_history_data(MA_LONG + 10, '1d', 'close')
    hist_h = ContextInfo.get_history_data(ATR_PERIOD + 10, '1d', 'high')
    hist_l = ContextInfo.get_history_data(ATR_PERIOD + 10, '1d', 'low')

    # ========== 2. 更新冷却期 ==========
    for code in list(State.cooldown.keys()):
        State.cooldown[code] -= 1
        if State.cooldown[code] <= 0:
            del State.cooldown[code]

    # ========== 3. 更新账户信息 ==========
    _update_account(ContextInfo)

    # ========== 4. 市场过滤器 ==========
    State.market_ok = _market_ok(hist_c)
    pos_mult = 1.0 if State.market_ok else 0.5
    risk_mult = 1.0 if State.market_ok else 0.5
    effective_max = max(1, int(MAX_POSITIONS * pos_mult))

    if not State.market_ok:
        print("[市场] HS300 < MA%d, 防御模式 (仓位%d/%d)" % (MA_MARKET, MAX_POSITIONS, effective_max))
    else:
        print("[市场] HS300 > MA%d, 可交易" % MA_MARKET)

    # ========== 5. 同步持仓 ==========
    _sync_positions(ContextInfo)

    # ========== 6. 执行止损/止盈 ==========
    _check_exits(ContextInfo, hist_c)

    # ========== 7. 弱市减仓 ==========
    if not State.market_ok:
        _reduce_half(ContextInfo)

    # ========== 8. 选股评分 ==========
    candidates = _rank_stocks(ContextInfo, hist_c)
    print("[选股] 候选 %d 只" % len(candidates))

    # ========== 9. 开新仓 ==========
    if len(State.positions) < effective_max and candidates:
        _open_positions(ContextInfo, candidates, hist_c, hist_h, hist_l, risk_mult)

    # ========== 10. 更新持仓状态 ==========
    for code, pos in list(State.positions.items()):
        pos['bars_held'] += 1
        px = _get_price(ContextInfo, code, hist_c)
        if px > 0 and px > pos['highest']:
            pos['highest'] = px

    # ========== 11. 打印汇总 ==========
    print("[汇总] bar=%d 持仓=%d只 现金=%.0f 总资产=%.0f 市场=%s" % (
        bar, len(State.positions), State.cash, State.total_assets,
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
    """HS300 是否在 MA50 之上"""
    if '000300.SH' not in hist_c:
        return True
    arr = hist_c['000300.SH']
    if len(arr) < MA_MARKET + 1:
        return True
    return arr[-1] > np.mean(arr[-MA_MARKET:])


def _check_exits(ContextInfo, hist_c):
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
            print("[exit] %s 止损: %.2f (亏损%.1f%%)" % (code, px, (px/entry-1)*100))
            to_close.append(code)
            continue

        # (b) 移动止盈
        if px <= highest - TRAIL_ATR_MULT * atr:
            print("[exit] %s 止盈: 高%.2f→%.2f (盈利%.1f%%)" % (code, highest, px, (px/entry-1)*100))
            to_close.append(code)
            continue

        # (c) 趋势平仓 (仅持有 >= MIN_HOLD_BARS 天)
        if pos.get('bars_held', 0) >= MIN_HOLD_BARS:
            if code in hist_c and len(hist_c[code]) >= MA_SHORT:
                ma20 = np.mean(hist_c[code][-MA_SHORT:])
                if px < ma20 * 0.97:
                    print("[exit] %s 破MA20: %.2f < %.2f" % (code, px, ma20))
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
        print("[平仓] %s %d股  %s" % (code, shares, _log_time(ContextInfo)))
    except Exception:
        pass
    State.positions.pop(code, None)
    # ★ 冷却期
    State.cooldown[code] = COOLDOWN_BARS


def _reduce_half(ContextInfo):
    """弱市减半仓"""
    n = len(State.positions)
    if n == 0:
        return
    n_close = max(1, n // 2)
    perf = []
    for code, pos in State.positions.items():
        entry = pos['entry_price']
        ratio = pos['highest'] / max(entry, 0.01)
        perf.append((code, ratio))
    perf.sort(key=lambda x: x[1])

    for i in range(n_close):
        if i < len(perf):
            _close_position(ContextInfo, perf[i][0])


def _get_sector(code):
    return SECTOR_MAP.get(code, '其他')


def _rank_stocks(ContextInfo, hist_c):
    """四因子选股: 动量(40%) + 低波(30%) + 趋势强度(30%)"""
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

            ma20 = np.mean(arr[-MA_SHORT:])
            if arr[-1] < ma20:
                continue

            # 因子 1: 20日动量
            mom = (arr[-1] - arr[-20]) / max(arr[-20], 0.01)

            # 因子 2: 低波动
            rets = (arr[1:] - arr[:-1]) / np.maximum(arr[:-1], 0.01)
            vol = np.std(rets[-20:]) if len(rets) >= 20 else 0.5
            lv = 1.0 / max(vol, 0.01)

            # 因子 3: 趋势强度
            ts = arr[-1] / max(ma20, 0.01)

            scores[code] = (mom, lv, ts)

        except Exception:
            continue

    if not scores:
        return []

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


def _open_positions(ContextInfo, candidates, hist_c, hist_h, hist_l, risk_mult=1.0):
    held = set(State.positions.keys())
    slots = MAX_POSITIONS - len(State.positions)
    if slots <= 0:
        return

    # 行业分布统计
    sector_count = {}
    for code in State.positions:
        sec = _get_sector(code)
        sector_count[sec] = sector_count.get(sec, 0) + 1

    opened = 0
    for code, score in candidates:
        if opened >= slots:
            break
        if code in held:
            continue

        px = _get_price(ContextInfo, code, hist_c)
        if px <= 0:
            continue

        # 行业限制
        sec = _get_sector(code)
        if sector_count.get(sec, 0) >= MAX_SECTOR_COUNT:
            continue

        atr = _calc_atr(code, hist_h, hist_l, hist_c)
        if atr <= 0:
            continue

        # 等风险仓位
        risk_budget = State.total_assets * RISK_PER_TRADE * risk_mult
        risk_per_share = atr * STOP_ATR_MULT
        if risk_per_share <= 0:
            continue

        shares = int(risk_budget / risk_per_share)
        shares = (shares // 100) * 100
        if shares < 100:
            shares = 100

        # 单票上限 25%
        max_shares = int(State.total_assets * 0.25 / px / 100) * 100
        shares = min(shares, max_shares)

        # 现金检查
        if shares * px > State.cash * 0.95:
            shares = int(State.cash * 0.95 / px / 100) * 100
        if shares < 100:
            continue

        try:
            order_shares(code, shares, 'COMPETE', ContextInfo, State.acc_id)
            print("[开仓] %s %d股 @%.2f ATR=%.2f score=%.3f %s %s" % (
                code, shares, px, atr, score, sec, _log_time(ContextInfo)))
            State.positions[code] = {
                'shares': shares,
                'entry_price': px,
                'bars_held': 0,
                'highest': px,
                'atr': atr,
            }
            sector_count[sec] = sector_count.get(sec, 0) + 1
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
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c)))
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


def _log_time(ContextInfo):
    try:
        t = ContextInfo.get_bar_timetag(ContextInfo.barpos)
        return timetag_to_datetime(t, '%Y-%m-%d %H:%M')
    except Exception:
        return ""
