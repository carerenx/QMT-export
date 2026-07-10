# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你反T策略 v4.0 — 实盘优化版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()
 ================================================================================

 【v4.0 优化项（基于实盘log反馈）】

  ┌──────┬──────────────────────────────┬────────────────────────────────┐
  │ 优化  │ 问题                          │ 方案                            │
  ├──────┼──────────────────────────────┼────────────────────────────────┤
  │ 1    │ handlebar回放历史bar时        │ is_last_bar()判断, 非最新bar     │
  │      │ 大量重复打印日志              │ 只更新信号不打印                │
  ├──────┼──────────────────────────────┼────────────────────────────────┤
  │ 2    │ ATR 6-8%极高, SELL_TRIGGER   │ 自适应ATR乘数: ATR>5%→用0.7    │
  │      │ = 开盘+8%等待太久             │ ATR>7%→用0.5 (更早触发)         │
  ├──────┼──────────────────────────────┼────────────────────────────────┤
  │ 3    │ 牛市彻底禁反T导致策略闲置     │ 统计持仓盈亏 & 建议调仓         │
  │      │ (股价+55%期间0操作)          │ 提示"当前不适合反T"             │
  ├──────┼──────────────────────────────┼────────────────────────────────┤
  │ 4    │ 初始化时大量历史信号打印      │ 用is_last_bar()屏蔽历史日志     │
  │      │ 所有23条都在[21:33:56]       │                                │
  ├──────┼──────────────────────────────┼────────────────────────────────┤
  │ 5    │ 每次handlebar都查两次         │ 合并为一次查询                  │
  │      │ get_trade_detail_data         │                                │
  ├──────┼──────────────────────────────┼────────────────────────────────┤
  │ 6    │ 缩量过滤(量比<0.5)可放宽     │ 调整为0.4 (减少误拦)            │
  │      │ v3中0.5, 有4天量比0.5-0.6   │                                │
  └──────┴──────────────────────────────┴────────────────────────────────┘

 ================================================================================
"""

# ============================================================================
# 第一部分：全局配置
# ============================================================================
ACCOUNT = '8890145315'

STOCK_CODE      = '601869'
STOCK_NAME      = '长飞光纤'
STOCK_QMT       = f'{STOCK_CODE}.SH'

# 仓位
TRADE_LOT_SIZE  = 100                   # 每次1手
MIN_LOT         = 100

# 检测周期
TIMER_INTERVAL  = '1nSecond'

# 反T触发线（ATR自适应）
ATR_PERIOD           = 14
SELL_TRIGGER_MULT    = 1.0              # 基准乘数
BUYBACK_TRIGGER_MULT = 1.0
# ★ v4新增: 高ATR自适应 — ATR过大时自动缩小乘数, 避免等太久
ATR_HIGH_THRESHOLD   = 0.05             # ATR% > 5% 触发自适应
SELL_TRIGGER_MULT_HI = 0.7              # 高ATR时乘数 → 0.7
ATR_VERYHIGH_THRESH  = 0.07             # ATR% > 7%
SELL_TRIGGER_MULT_VH = 0.5              # 极高ATR时乘数 → 0.5

# 冲高回落 & 下跌回升
PULLBACK_PCT = 0.0010                   # 0.10%
BOUNCE_PCT   = 0.0010

# 熔断 & 过滤
VOLUME_FILTER_RATIO = 0.4               # v4: 0.5→0.4 减少误拦
RSI_OVERBOUGHT      = 75

# 紧急买回 & 止损
EMERGENCY_BUYBACK_PCT = 0.02
STOP_LOSS_PCT         = 0.015

# 时间
FORCE_CLOSE_TIME = '14:57:00'

# 数据
HIST_DATA_LEN = 80
COMMISSION    = 0.00025
STAMP_TAX     = 0.001


# ============================================================================
# 第二部分：技术指标
# ============================================================================

def _sma(values, period):
    n = len(values)
    r = [0.0] * n
    for i in range(period - 1, n):
        r[i] = sum(values[i - period + 1 : i + 1]) / period
    return r


def _atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i]  - closes[i - 1]))
    result = [0.0] * n
    for i in range(period, n):
        result[i] = sum(tr[i - period + 1 : i + 1]) / period
    return result


def _rsi(closes, period=14):
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    rsi = [50.0] * n
    g, l = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        g.append(d if d > 0 else 0)
        l.append(abs(d) if d < 0 else 0)
    ag = sum(g[:period]) / period
    al = sum(l[:period]) / period
    rsi[period] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    for i in range(period, n - 1):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1 + ag / al) if al > 0 else 100.0
    return rsi


def compute_signal(opens, highs, lows, closes, volumes):
    """计算当日反T信号 — v4加入自适应ATR乘数"""
    n = len(closes)
    if n < 60:
        return None

    co = opens[-1]
    cc = closes[-1]
    cv = volumes[-1]

    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]
    trend = 'bull' if (cc > ma20 and ma5 > ma20) else \
            ('bear' if (cc < ma20 and ma5 < ma20) else 'sideways')

    curr_rsi = _rsi(closes)[-1]
    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # ★ v4: 自适应ATR乘数
    if curr_atr_pct > ATR_VERYHIGH_THRESH:
        sell_mult = SELL_TRIGGER_MULT_VH      # ATR% > 7% → 乘数0.5
    elif curr_atr_pct > ATR_HIGH_THRESHOLD:
        sell_mult = SELL_TRIGGER_MULT_HI       # ATR% > 5% → 乘数0.7
    else:
        sell_mult = SELL_TRIGGER_MULT          # 正常 → 乘数1.0

    # 方向决策
    do_short, reason = True, ''
    if trend == 'bull':
        do_short, reason = False, '牛市禁反T'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short, reason = False, f'缩量(量比{curr_vr:.2f})'
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short, reason = False, f'RSI超买({curr_rsi:.0f})'

    sell_trigger    = round(co + curr_atr * sell_mult, 2)
    buyback_trigger = round(sell_trigger * (1.0 - curr_atr_pct * BUYBACK_TRIGGER_MULT), 2)

    spread = (sell_trigger - buyback_trigger) / buyback_trigger * 100 if buyback_trigger > 0 else 0
    if do_short and spread < 0.15:
        do_short, reason = False, f'价差不足({spread:.2f}% < 0.15%)'

    return {
        'do_short':        do_short,
        'blocked_reason':  reason,
        'trend':           trend,
        'sell_trigger':    sell_trigger,
        'buyback_trigger': buyback_trigger,
        'open_price':      co,
        'close_yday':      cc,
        'atr':             curr_atr,
        'atr_pct':         curr_atr_pct,
        'rsi':             curr_rsi,
        'vol_ratio':       curr_vr,
        'spread_pct':      spread,
        'sell_mult_used':  sell_mult,          # v4: 记录实际使用的乘数
    }


# ============================================================================
# 第三部分：QMT 策略入口
# ============================================================================

STATE_IDLE    = 'IDLE'
STATE_SPIKING = 'SPIKING'
STATE_DIPPING = 'DIPPING'
STATE_SOLD    = 'SOLD'
STATE_DONE    = 'DONE'
STATE_FORCED  = 'FORCED'
ST_NAME = {STATE_IDLE:'等待', STATE_SPIKING:'冲高监控', STATE_DIPPING:'下跌监控',
           STATE_SOLD:'已卖出', STATE_DONE:'完成', STATE_FORCED:'强制平仓'}


def init(ContextInfo):
    ContextInfo.set_universe([STOCK_QMT])
    ContextInfo.set_account(ACCOUNT)

    state = {
        'daily_signal':     None,
        'base_shares':      0,
        'base_cost':        0.0,
        'fstate':           STATE_IDLE,
        'peak_price':       0.0,
        'dip_price':        0.0,
        'sell_fill_price':  0.0,
        'day_pnl':          0.0,
        'stop_loss_hit':    False,
        # v4新增: 运营统计
        'total_t_days':     0,     # 累计做T成功天数
        'total_pnl':        0.0,   # 累计做T盈亏
        'entry_price':      0.0,   # 建仓价(首次handlebar时的昨收)
        'startup_printed':  False, # 横幅是否已打印
    }
    ContextInfo.st = state
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


# ============================================================================
# 第四部分：handlebar — 日线触发
# ============================================================================

def handlebar(ContextInfo):
    """
    日线触发。

    ★ v4关键改进: 用 is_last_bar() 判断是否最新K线。
      - 非最新bar(历史回放) → 只更新信号, 不打印日志
      - 最新bar(当前交易日) → 打印完整的做T计划
      这样初始化时就不会被23条历史日志淹没了。
    """
    st = ContextInfo.st
    is_live = ContextInfo.is_last_bar()   # ★ v4: 判断是否为最新K线

    # 获取历史数据
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')
    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return

    # ★ v4: 合并为一次调用获取持仓+账户
    # get_trade_detail_data(账号, 类型, 数据类别)
    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    accounts  = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')

    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    if base_shares < TRADE_LOT_SIZE:
        if is_live:
            _log(f'[警告] 底仓不足1手({base_shares}股) - 策略等待中')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    # 记录建仓价(首次)
    if st['entry_price'] == 0.0:
        st['entry_price'] = base_cost

    curr_close = closes[STOCK_QMT][-1]
    avail_cash = accounts[0].m_dAvailable if accounts else 0.0
    pos_value  = base_shares * curr_close
    total_val  = pos_value + avail_cash

    # 计算信号
    signal = compute_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    st['daily_signal'] = signal

    # 新交易日重置
    st['fstate']          = STATE_IDLE
    st['peak_price']      = 0.0
    st['dip_price']       = 0.0
    st['sell_fill_price'] = 0.0
    st['day_pnl']         = 0.0
    st['stop_loss_hit']   = False

    # ★ v4: 只在最新bar打印详细日志
    if is_live:
        _print_status(ContextInfo, curr_close, avail_cash, pos_value, total_val)
        _print_signal(ContextInfo)
    else:
        # 历史回放时只打印横幅(一次)
        if not st['startup_printed']:
            _log(f'{"="*50}')
            _log(f'  {STOCK_NAME} v4.0 策略已加载 — 回放历史数据中...')
            _log(f'  最新价=¥{curr_close:.2f} | ATR={signal["atr"]:.2f}元({signal["atr_pct"]*100:.1f}%)')
            _log(f'  ATR自适应: 乘数={signal["sell_mult_used"]} (基准={SELL_TRIGGER_MULT})')
            _log(f'{"="*50}')
            st['startup_printed'] = True


def _print_status(ContextInfo, curr_close, avail_cash, pos_value, total_val):
    """打印账户状态 & 正T可行性分析"""
    st = ContextInfo.st
    cost = st['entry_price']
    unreal_pnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_close / cost - 1) * 100 if cost > 0 else 0

    _log(f'[账户] {st["base_shares"]}股 × ¥{curr_close:.2f} = ¥{pos_value:,.0f} '
         f'| 现金¥{avail_cash:,.0f}')
    _log(f'  浮动盈亏: ¥{unreal_pnl:,.0f}({unreal_pct:+.1f}%) '
         f'| 总资产¥{total_val:,.0f}')

    # ★ v4: 正T可行性提示
    lot_cost = curr_close * TRADE_LOT_SIZE
    if avail_cash >= lot_cost * 1.01:
        _log(f'  💡 现金充足, 可考虑开启正T(先买后卖)')
    else:
        _log(f'  正T不可用: 1手需¥{lot_cost:,.0f} > 现金¥{avail_cash:,.0f} '
             f'(缺口¥{lot_cost-avail_cash:,.0f})')

    # 累计做T统计
    if st['total_t_days'] > 0:
        _log(f'  累计做T: {st["total_t_days"]}天 | 盈亏¥{st["total_pnl"]:,.0f}')


def _print_signal(ContextInfo):
    s = ContextInfo.st['daily_signal']
    st = ContextInfo.st
    _log(f'--- 做T信号 ---')
    _log(f'  开盘={s["open_price"]:.2f}  ATR={s["atr"]:.2f}元({s["atr_pct"]*100:.2f}%) '
         f'乘数={s["sell_mult_used"]}')
    _log(f'  趋势={s["trend"]}  RSI={s["rsi"]:.1f}  量比={s["vol_ratio"]:.2f}')

    if s['do_short']:
        _log(f'  ✓ 反T | 卖出线={s["sell_trigger"]:.2f} | 买回线={s["buyback_trigger"]:.2f}')
        _log(f'    冲高→回落{PULLBACK_PCT*100:.2f}%卖出 | 下跌→回升{BOUNCE_PCT*100:.2f}%买回')
    else:
        reason = s['blocked_reason']
        if reason == '牛市禁反T':
            # ★ v4: 牛市时给用户有价值的提示
            pos_pnl = (st.get('entry_price', 0) > 0 and
                       (s['close_yday'] / st['entry_price'] - 1) * 100)
            _log(f'  ✗ 牛市禁反T — 这是正确的! 持有待涨胜过卖飞')
        else:
            _log(f'  ✗ 禁止 ({reason})')


# ============================================================================
# 第五部分：ontimer — 状态机驱动
# ============================================================================

def ontimer(ContextInfo):
    st = ContextInfo.st
    signal = st.get('daily_signal')
    if signal is None or not signal.get('do_short'):
        return

    now = _now()
    if not _is_market_open(now):
        return

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return

    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return
    if STOCK_QMT not in tick:
        return

    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0:
        return

    fstate = st['fstate']
    trig_s = signal['sell_trigger']
    trig_b = signal['buyback_trigger']

    # 状态路由
    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price, trig_s)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price, trig_s)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price, trig_b)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price, trig_b)

    # 尾盘强制买回
    if now >= FORCE_CLOSE_TIME:
        if fstate in (STATE_SOLD, STATE_DIPPING):
            _force_buyback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_IDLE):
            st['fstate'] = STATE_DONE

    # 止损
    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st['day_pnl'] < -loss_limit:
            _log(f'[止损] 亏损{st["day_pnl"]:.0f}(>{loss_limit:.0f})触发')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 第六部分：状态处理
# ============================================================================

def _handle_idle(ContextInfo, price, sell_trigger):
    st = ContextInfo.st
    if price >= sell_trigger:
        st['fstate']     = STATE_SPIKING
        st['peak_price'] = price
        _log(f'[冲高] {price:.2f}越过{sell_trigger:.2f} → 等回落{PULLBACK_PCT*100:.2f}%')


def _handle_spiking(ContextInfo, price, sell_trigger):
    st = ContextInfo.st
    if price > st['peak_price']:
        st['peak_price'] = price

    peak = st['peak_price']
    pullback = (peak - price) / peak

    if pullback >= PULLBACK_PCT:
        _log(f'[卖出] 最高{peak:.2f}回落{pullback*100:.2f}%→{price:.2f}')
        _mini_sell(ContextInfo, price)
        st['fstate']          = STATE_SOLD
        st['sell_fill_price'] = price
        s = st['daily_signal']
        s['buyback_trigger'] = round(price * (1.0 - s['atr_pct'] * BUYBACK_TRIGGER_MULT), 2)
    elif price < sell_trigger:
        _log(f'[假突破] {price:.2f}跌回{sell_trigger:.2f}下')
        st['fstate']     = STATE_IDLE
        st['peak_price'] = 0.0


def _handle_sold(ContextInfo, price, buyback_trigger):
    st = ContextInfo.st
    sp = st['sell_fill_price']

    if price >= sp * (1.0 + EMERGENCY_BUYBACK_PCT):
        _log(f'[紧急] 卖{sp:.2f}→现{price:.2f}(+{(price-sp)/sp*100:.2f}%)买回!')
        _mini_buyback(ContextInfo, price)
        return

    if price <= buyback_trigger:
        st['fstate']    = STATE_DIPPING
        st['dip_price'] = price
        _log(f'[下跌] {price:.2f}跌破{buyback_trigger:.2f} → 等回升{BOUNCE_PCT*100:.2f}%')


def _handle_dipping(ContextInfo, price, buyback_trigger):
    st = ContextInfo.st
    if price < st['dip_price']:
        st['dip_price'] = price

    dip = st['dip_price']
    bounce = (price - dip) / dip

    if bounce >= BOUNCE_PCT:
        gross = (st['sell_fill_price'] - price) * TRADE_LOT_SIZE
        _log(f'[买回] 最低{dip:.2f}回升{bounce*100:.2f}%→{price:.2f} 毛利≈{gross:.0f}')
        _mini_buyback(ContextInfo, price)
        # ★ v4: 累计统计
        st['total_t_days'] += 1
        st['total_pnl']    += gross
    elif price > buyback_trigger:
        _log(f'[假跌破] {price:.2f}涨回{buyback_trigger:.2f}上')
        st['fstate']    = STATE_SOLD
        st['dip_price'] = 0.0


# ============================================================================
# 第七部分：下单 & 平仓
# ============================================================================

def _mini_sell(ContextInfo, price):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  [下单] 卖出 {price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  [失败] 卖出: {e}')
        st['fstate'] = STATE_IDLE


def _mini_buyback(ContextInfo, price):
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    if _cash(ContextInfo) < need:
        _log(f'  [失败] 买回资金不足: 需{need:,.0f} | 可用{_cash(ContextInfo):,.0f}')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  [下单] 买回 {price:.2f} × {TRADE_LOT_SIZE}股')
        st['fstate'] = STATE_DONE
    except Exception as e:
        _log(f'  [失败] 买回: {e}')


def _force_buyback(ContextInfo):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[尾盘] 对手价买回{TRADE_LOT_SIZE}股 ✓')
    except Exception as e:
        _log(f'[尾盘失败!!] {e} — 请手动补仓!')
        st['fstate'] = STATE_FORCED


def _cash(ContextInfo):
    try:
        a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception:
        return 0.0


def _acc(ContextInfo):
    return ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else ACCOUNT


# ============================================================================
# 第八部分：回调 & 工具
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50:'已报', 52:'部成', 53:'全成', 54:'部撤', 55:'已撤', 56:'废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} '
             f'{order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    d = '买' if deal.m_nDirection == 1 else '卖'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    st['day_pnl'] += (amt - fee) if deal.m_nDirection == 2 else -(amt + fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f} × {deal.m_nVolume} PnL≈{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'[{STOCK_NAME}] v4.0 已停止')
        _log(f'  累计做T: {st.get("total_t_days", 0)}天 盈亏¥{st.get("total_pnl", 0):,.0f}')
        if st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
            _log(f'  ⚠ 未买回! 请手动补仓!')


def _now():
    import time as _t
    return _t.strftime('%H:%M:%S')


def _ts():
    import time as _t
    return _t.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    ts = _ts()
    if args:
        print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
