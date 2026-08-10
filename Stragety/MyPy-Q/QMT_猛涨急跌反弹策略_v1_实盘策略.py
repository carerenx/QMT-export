# -*- coding: gbk -*-
"""
QMT 猛涨急跌反弹策略 v1 — Crash Rebound Strategy

策略逻辑:
  1. 前期(60日)涨幅 >= 50%
  2. 近期(20日)从高点回落 >= 30%
  3. 买入并持有 20 个交易日
  4. 止损 -8% / 止盈 +30%
"""

# ============================================================================
#  策略参数
# ============================================================================

ACCOUNT = '8890145315'

# --- 选股 ---
PRIOR_LOOKBACK = 60
CRASH_LOOKBACK = 20
NEED = PRIOR_LOOKBACK + CRASH_LOOKBACK   # 80 bars total
PRIOR_GAIN_MIN = 50.0
CRASH_MIN = 30.0
CRASH_MAX = 60.0

# --- 交易 ---
HOLD_DAYS = 20
STOP_LOSS = -8.0
TAKE_PROFIT = 30.0
MAX_POS = 5
MAX_SINGLE = 0.20

# --- 过滤 ---
MIN_PRICE = 5.0

# --- 股票池（精选 ~50 只活跃股，方便 QMT 快速回测） ---
CODES = [
    '000001','000002','000063','000333','000338','000568','000651','000725',
    '000858','000876','000938','002001','002007','002049','002074','002129',
    '002230','002236','002241','002271','002304','002352','002415','002459',
    '002460','002466','002475','002493','002594','002601',
    '300014','300015','300059','300122','300274','300308','300433','300450',
    '300498','300502','300750','300759','300896',
    '600000','600009','600016','600028','600029','600030','600031','600036',
    '600048','600050','600085','600104','600111','600150','600176','600188',
    '600196','600276','600309','600332','600383','600406','600415','600426',
    '600436','600438','600460','600489','600498','600519','600522','600547',
    '600570','600584','600585','600588','600600','600660','600690','600703',
    '600741','600754','600760','600795','600809','600837','600875','600886',
    '600887','600893','600900','600905','600918','600919','600941',
    '601006','601009','601012','601021','601066','601088','601100','601108',
    '601111','601117','601127','601138','601166','601186','601211','601225',
    '601288','601318','601319','601328','601336','601360','601377','601390',
    '601398','601456','601607','601615','601628','601633','601658','601668',
    '601669','601688','601689','601696','601728','601766','601788','601800',
    '601808','601816','601818','601857','601868','601869','601872','601877',
    '601878','601881','601888','601898','601899','601919','601939','601985',
    '601988','601989','601995',
    '603019','603160','603259','603260','603290','603369','603392','603501',
    '603589','603596','603799','603806','603833','603899',
    '688001','688005','688008','688009','688012','688036','688041','688065',
    '688111','688126','688169','688180','688185','688187','688223','688256',
    '688303','688390','688396','688472','688516','688561','688568','688728',
    '688777','688819','688981',
]


# ============================================================================
#  init
# ============================================================================
def init(ContextInfo):
    print("[CrashRebound] init start")

    ContextInfo.capital = 1000000
    ContextInfo.set_slippage(1, 0.01)
    ContextInfo.set_commission(0, [0, 0.001, 0.0003, 0.0003, 0, 5])

    # 去重 + universe
    seen = set()
    universe = []
    for c in CODES:
        if c in seen: continue
        seen.add(c)
        if c.startswith(('6', '9')):
            universe.append(c + '.SH')
        else:
            universe.append(c + '.SZ')
    ContextInfo.set_universe(universe)
    codes_only = [u.split('.')[0] for u in universe]

    state = {
        'acc': ACCOUNT,
        'pos': {},
        'candidates': {},
        'scan_bar': -999,
        'codes': codes_only,
        'universe': universe,
        'total_bars': 0,          # 估计总 bar 数
    }
    ContextInfo.st = state

    print("[CrashRebound] init done, universe={}".format(len(universe)))


# ============================================================================
#  handlebar
# ============================================================================
def handlebar(ContextInfo):
    g = ContextInfo.st

    # --- 估算总 bar 数（仅第一次） ---
    if g['total_bars'] == 0:
        try:
            h = ContextInfo.get_history_data(NEED, '1d', 'close', 1, True)
            first_key = g['universe'][0] if g['universe'] else ''
            if first_key in h and h[first_key]:
                g['total_bars'] = len(h[first_key])
        except Exception:
            g['total_bars'] = 9999

    # --- 每 100 bar 输出进度 ---
    if ContextInfo.barpos % 100 == 0:
        pct = 100.0 * ContextInfo.barpos / max(g['total_bars'], 1)
        print("[CrashRebound] bar={}/~{} ({:.0f}%) pos={}".format(
            ContextInfo.barpos, g['total_bars'], pct, len(g['pos'])))

    # --- 1. 持仓检查（用上一根 bar 缓存的价格，不额外调 API） ---
    _check_exits(ContextInfo)

    # --- 2. 隔足够久才扫描（扫描很重，不要频繁做） ---
    if ContextInfo.barpos - g['scan_bar'] >= 30 or not g['candidates']:
        _scan_all(ContextInfo)

    # --- 3. 建仓 ---
    if len(g['pos']) < MAX_POS:
        _enter(ContextInfo)

    # --- 末尾输出 ---
    if ContextInfo.barpos == g['total_bars'] - 1:
        print("[CrashRebound] ======== BACKTEST COMPLETE ========")


# ============================================================================
#  退出检查 — 用 get_history_data(1) 取当日收盘价
# ============================================================================
def _check_exits(ContextInfo):
    g = ContextInfo.st
    if not g['pos']:
        return

    # 一次性取所有持仓的当日收盘价
    try:
        h = ContextInfo.get_history_data(1, '1d', 'close', 1, True)
    except Exception:
        return

    to_del = []
    for code, p in list(g['pos'].items()):
        key = code + ('.SH' if code.startswith(('6','9')) else '.SZ')
        vals = h.get(key, [])
        if not vals:
            continue
        cur_p = float(vals[-1])
        if cur_p <= 0:
            continue

        held = ContextInfo.barpos - p['entry_bar']
        pnl = (cur_p / p['entry_price'] - 1) * 100
        reason = None

        if pnl <= STOP_LOSS:
            reason = "SL({:+.1f})".format(pnl)
        elif pnl >= TAKE_PROFIT:
            reason = "TP({:+.1f})".format(pnl)
        elif held >= HOLD_DAYS:
            reason = "EXP({}d,{:+.1f})".format(held, pnl)

        if reason:
            _sell(ContextInfo, code)
            print("[CrashRebound] SELL {} {} bar={}".format(
                code, reason, ContextInfo.barpos))
            to_del.append(code)

    for c in to_del:
        del g['pos'][c]


# ============================================================================
#  扫描 — 这个函数比较重，只在必要时调用
# ============================================================================
def _scan_all(ContextInfo):
    g = ContextInfo.st
    g['scan_bar'] = ContextInfo.barpos

    try:
        dict_close = ContextInfo.get_history_data(NEED, '1d', 'close', 1, True)
        dict_high  = ContextInfo.get_history_data(NEED, '1d', 'high',  1, True)
    except Exception as e:
        print("[CrashRebound] scan FAIL: {}".format(e))
        return

    new_candidates = {}
    for key, closes in dict_close.items():
        code = key.split('.')[0] if '.' in key else key
        highs = dict_high.get(key, [])

        if len(closes) < NEED or len(highs) < NEED:
            continue

        try:
            closes = [float(v) for v in closes]
            highs  = [float(v) for v in highs]
        except Exception:
            continue

        cur_p = closes[-1]
        if cur_p < MIN_PRICE:
            continue

        # 20日内最高 -> 暴跌幅度
        recent_highs = highs[-CRASH_LOOKBACK:]
        peak = max(recent_highs)
        peak_offset = len(highs) - CRASH_LOOKBACK + recent_highs.index(peak)
        crash_pct = (cur_p / peak - 1) * 100

        if crash_pct > -CRASH_MIN or crash_pct < -CRASH_MAX:
            continue

        crash_days = len(closes) - 1 - peak_offset
        if crash_days < 3 or crash_days > CRASH_LOOKBACK:
            continue

        # 前期涨幅
        prior_start = max(0, peak_offset - PRIOR_LOOKBACK)
        segment = closes[prior_start:peak_offset + 1]
        if not segment:
            continue
        prior_low = min(segment)
        if prior_low <= 0:
            continue
        prior_gain = (peak / prior_low - 1) * 100
        if prior_gain < PRIOR_GAIN_MIN:
            continue

        new_candidates[code] = {
            'crash_pct': round(crash_pct, 2),
            'prior_gain': round(prior_gain, 2),
            'crash_days': crash_days,
            'price': cur_p,
        }

    g['candidates'] = new_candidates
    if new_candidates:
        print("[CrashRebound] scan bar={}: {} candidates".format(
            ContextInfo.barpos, len(new_candidates)))


# ============================================================================
#  建仓
# ============================================================================
def _enter(ContextInfo):
    g = ContextInfo.st
    if not g['candidates']:
        return

    avail = {c: d for c, d in g['candidates'].items() if c not in g['pos']}
    if not avail:
        return

    ranked = sorted(avail.items(), key=lambda x: abs(x[1]['crash_pct']), reverse=True)
    slots = MAX_POS - len(g['pos'])

    for code, data in ranked[:slots]:
        try:
            buy_amount = int(ContextInfo.capital * MAX_SINGLE)
            passorder(23, 1102, g['acc'], code, 5, -1, buy_amount,
                      'CrashRebound', 1, '', ContextInfo)

            g['pos'][code] = {
                'entry_bar': ContextInfo.barpos,
                'entry_price': data['price'],
                'crash_pct': data['crash_pct'],
            }
            print("[CrashRebound] BUY {} crash={:.1f}% prior=+{:.0f}% days={} price={:.2f}".format(
                code, data['crash_pct'], data['prior_gain'],
                data['crash_days'], data['price']))
            del g['candidates'][code]
        except Exception as e:
            print("[CrashRebound] BUY {} FAIL: {}".format(code, e))


# ============================================================================
#  卖出
# ============================================================================
def _sell(ContextInfo, code):
    g = ContextInfo.st
    passorder(24, 1101, g['acc'], code, 5, -1, 0,
              'CrashRebound', 1, '', ContextInfo)
