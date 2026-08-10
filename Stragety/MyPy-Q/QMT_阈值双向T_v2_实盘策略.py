# -*- coding: gbk -*-
"""
================================================================================
 QMT 阈值双向T策略 v2.0 — 纯tick驱动版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【v2.0 核心改动 — 从历史数据驱动切换到tick数据驱动】

  v1问题:
    - get_history_data('1d','open')[-1] 在数据加载期间返回各种历史开盘价(¥74~¥117)
    - handlebar 高频触发反复重置状态机, sell_side_done 守卫被绕过
    - 导致触发线错误 + 无限重复卖出

  v2方案:
    - 开盘价取自 get_full_tick()['open'] — 今日真实开盘价, 稳定不变
    - handlebar 只运行一次(initialized标志), 之后完全空转, 永不碰状态机
    - ontimer 完全自包含状态机, 不依赖 handlebar
    - 三层防重复卖出: _handle_idle入口 + _handle_spiking确认点 + handlebar永不重置

 【机制1 — 卖出(反T)】
   价格 ≥ 开盘×(1+SELL_TRIGGER_PCT) → SPIKING跟踪峰值
     → 从峰值回落 ≥ PULLBACK_PCT → 卖出 → SOLD等买回
     → 跌到卖价下方BUYBACK_DROP_PCT → BUY_DIPPING探底
     → 回升 ≥ BOUNCE_PCT → 买回 → DONE

 【机制2 — 买入(正T)】
   价格 ≤ 开盘×(1-BUY_TRIGGER_PCT) → DIPPING跟踪谷值
     → 从谷值回升 ≥ BOUNCE_PCT → 买入 → BOUGHT等卖出
     → 涨到买价上方SELLBACK_RISE_PCT → SELL_SPIKING冲高
     → 回落 ≥ PULLBACK_PCT → 卖出 → DONE
================================================================================
"""
import time as _time

# ============================================================================
# 第一部分：全局配置 — ★ 用户输入区
# ============================================================================
ACCOUNT = '8890145315'

STOCK_CODE = '601869'
STOCK_NAME = '长飞光纤'
STOCK_QMT  = f'{STOCK_CODE}.SH'

TRADE_LOT_SIZE = 100
TIMER_INTERVAL = '1nSecond'

# ── ★ 核心参数(用户输入) ──
SELL_TRIGGER_PCT = 0.09            # 卖出触发: 涨超开盘价9%
BUY_TRIGGER_PCT  = 0.05            # 买入触发: 跌超开盘价5%

# ── 确认参数 ──
PULLBACK_PCT = 0.0005              # 冲高回落0.05% → 卖出确认
BOUNCE_PCT   = 0.0005              # 探底回升0.05% → 买入确认

# ── 闭环参数 ──
BUYBACK_DROP_PCT  = 0.02           # 卖后跌2%开始监控买回
SELLBACK_RISE_PCT = 0.01           # 买后涨1%开始监控卖出

# ── 双向开关 ──
ENABLE_SELL_SIDE = True            # 启用反T
ENABLE_BUY_SIDE  = True            # 启用正T

# ── 尾盘 ──
FORCE_CLOSE_TIME = '14:57:00'
ENABLE_END_BUYBACK = 0  # 尾盘强制买回/卖出
# ── 数据 ──
HIST_DATA_LEN = 60


# ============================================================================
# 第二部分：状态定义
# ============================================================================

STATE_IDLE         = 'IDLE'
STATE_SPIKING      = 'SPIKING'       # 反T: 冲高→等回落
STATE_SOLD         = 'SOLD'          # 反T: 已卖出→等买回
STATE_BUY_DIPPING  = 'BUY_DIPPING'   # 反T: 跌到位→等回升确认买回
STATE_DIPPING      = 'DIPPING'       # 正T: 探底→等回升
STATE_BOUGHT       = 'BOUGHT'        # 正T: 已买入→等卖出
STATE_SELL_SPIKING = 'SELL_SPIKING'  # 正T: 涨到位→等回落确认卖出
STATE_DONE         = 'DONE'
STATE_FORCED       = 'FORCED'


# ============================================================================
# 第三部分：init / handlebar
# ============================================================================

def init(ContextInfo):
    """策略初始化"""
    try:
        ContextInfo.set_universe([STOCK_QMT])
    except Exception:
        pass
    try:
        ContextInfo.set_account(ACCOUNT)
    except Exception:
        pass

    state = {
        # 持仓
        'base_shares': 0,
        'base_cost': 0.0,
        'entry_price': 0.0,

        # 今日参数(handlebar一次性设置)
        'open_price': 0.0,
        'sell_trigger': 0.0,
        'buy_trigger': 0.0,

        # 状态机
        'fstate': STATE_IDLE,

        # 卖出侧(反T)
        'sell_peak_price': 0.0,
        'sell_fill_price': 0.0,
        'buyback_target': 0.0,

        # 买入侧(正T)
        'buy_dip_price': 0.0,
        'buy_fill_price': 0.0,
        'sellback_target': 0.0,

        # 交易统计
        'day_pnl': 0.0,
        'total_t_days': 0,
        'total_pnl': 0.0,
        'total_sell_trades': 0,
        'total_buy_trades': 0,

        # ★ 关键标志
        'initialized': False,          # handlebar初始化完成?
        'sell_side_done': False,       # 当日已卖出?(防重复)
        'buy_side_done': False,        # 当日已买入?(防重复)
        'startup_printed': False,
        'last_fstate': '',             # 上一次状态(用于状态变化日志)
    }
    ContextInfo.st = state
    today_str = _time.strftime('%Y-%m-%d')
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, f"{today_str} 09:00:00", "SH")


def handlebar(ContextInfo):
    """日线回调 — ★ v2: 仅首次初始化, 之后完全空转"""
    st = ContextInfo.st

    # ★ 已完成初始化? 直接返回, 不碰任何状态
    if st.get('initialized', False):
        return

    # ── 首次: 从tick获取今日开盘价 + 读取持仓 ──
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
        if STOCK_QMT in tick:
            today_open = tick[STOCK_QMT].get('open', 0)
            if today_open > 0:
                st['open_price']   = today_open
                st['sell_trigger'] = round(today_open * (1.0 + SELL_TRIGGER_PCT), 2)
                st['buy_trigger']  = round(today_open * (1.0 - BUY_TRIGGER_PCT), 2)
                st['initialized']  = True
    except Exception:
        pass

    if not st['initialized']:
        return  # tick拿不到开盘价, 等下次handlebar再试

    # 获取持仓
    try:
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                st['base_shares'] = pos.m_nVolume
                st['base_cost']   = pos.m_dOpenPrice
                break
    except Exception:
        pass

    if st['entry_price'] == 0.0:
        st['entry_price'] = st['base_cost']

    if st['base_shares'] < TRADE_LOT_SIZE:
        _log(f'[警告] 底仓不足1手({st["base_shares"]}股)')
        return

    # 打印启动信息(仅一次)
    _log(f'{"="*55}')
    _log(f'  {STOCK_NAME} 阈值双向T v2.0 — tick驱动版')
    _log(f'  今日开盘: ¥{st["open_price"]:.2f}')
    _log(f'  卖出触发线: ¥{st["sell_trigger"]:.2f} (开盘+{SELL_TRIGGER_PCT*100:.1f}%)')
    _log(f'  买入触发线: ¥{st["buy_trigger"]:.2f} (开盘-{BUY_TRIGGER_PCT*100:.1f}%)')
    _log(f'  回落确认: {PULLBACK_PCT*100:.2f}% | 回升确认: {BOUNCE_PCT*100:.2f}%')
    _log(f'  反T: {"✓" if ENABLE_SELL_SIDE else "✗"} | 正T: {"✓" if ENABLE_BUY_SIDE else "✗"}')
    _log(f'  持仓: {st["base_shares"]}股 | 成本: ¥{st["base_cost"]:.2f}')
    _log(f'{"="*55}')
    st['startup_printed'] = True


# ============================================================================
# 第四部分：ontimer — 完全自包含状态机
# ============================================================================

def ontimer(ContextInfo):
    """定时器回调 — ★ v2: 完全自包含, 不依赖handlebar"""
    st = ContextInfo.st
    now = _now()

    # 未初始化完成则跳过
    if not st.get('initialized', False):
        return

    if not _is_market_open(now):
        return

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return

    if st.get('base_shares', 0) < TRADE_LOT_SIZE:
        return

    # 获取实时tick
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

    # ── 状态变化时打印 ──
    if fstate != st.get('last_fstate', ''):
        _log(f'>>> 状态切换: {st.get("last_fstate","?")} → {fstate} | ¥{price:.2f}')
        st['last_fstate'] = fstate

    # ── 状态路由 ──
    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price)
    elif fstate == STATE_BUY_DIPPING:
        _handle_buy_dipping(ContextInfo, price)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price)
    elif fstate == STATE_BOUGHT:
        _handle_bought(ContextInfo, price)
    elif fstate == STATE_SELL_SPIKING:
        _handle_sell_spiking(ContextInfo, price)

    # ── 尾盘强制平仓 ──
    if now >= FORCE_CLOSE_TIME and ENABLE_END_BUYBACK:
        if fstate in (STATE_SOLD, STATE_BUY_DIPPING):
            _log(f'[尾盘] {now} 强制买回')
            _force_buyback(ContextInfo)
        elif fstate == STATE_BOUGHT:
            _log(f'[尾盘] {now} 强制卖出')
            _force_sellback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_DIPPING, STATE_IDLE):
            st['fstate'] = STATE_DONE


# ============================================================================
# 第五部分：状态处理 — 卖出侧(反T)
# ============================================================================

def _handle_idle(ContextInfo, price):
    """IDLE: 等待触发"""
    st = ContextInfo.st

    # ── 卖出侧 ──
    if ENABLE_SELL_SIDE and price >= st['sell_trigger']:
        if st['sell_side_done']:       # ★ 第一层: 入口守卫
            return
        st['fstate']          = STATE_SPIKING
        st['sell_peak_price'] = price
        _log(f'[卖出监控] ¥{price:.2f} ≥ ¥{st["sell_trigger"]:.2f} → 等回落{PULLBACK_PCT*100:.2f}%')
        return

    # ── 买入侧 ──
    if ENABLE_BUY_SIDE and price <= st['buy_trigger']:
        if st['buy_side_done']:        # ★ 入口守卫
            return
        st['fstate']        = STATE_DIPPING
        st['buy_dip_price'] = price
        _log(f'[买入监控] ¥{price:.2f} ≤ ¥{st["buy_trigger"]:.2f} → 等回升{BOUNCE_PCT*100:.2f}%')
        return


def _handle_spiking(ContextInfo, price):
    """SPIKING: 跟踪峰值, 等回落确认卖出"""
    st = ContextInfo.st

    if price > st['sell_peak_price']:
        st['sell_peak_price'] = price

    peak = st['sell_peak_price']
    pullback = (peak - price) / peak if peak > 0 else 0

    if pullback >= PULLBACK_PCT:
        if st['sell_side_done']:       # ★ 第二层: 确认点守卫
            return
        _log(f'[卖出] 峰值¥{peak:.2f} 回落{pullback*100:.2f}% → ¥{price:.2f}')
        _do_sell(ContextInfo, price)
        st['fstate']          = STATE_SOLD
        st['sell_side_done']  = True
        st['sell_fill_price'] = price
        st['buyback_target']  = round(price * (1.0 - BUYBACK_DROP_PCT), 2)
        _log(f'  卖价¥{price:.2f} | 买回线¥{st["buyback_target"]:.2f}(-{BUYBACK_DROP_PCT*100:.2f}%)')
        return

    # 假突破
    if price < st['sell_trigger']:
        _log(f'[假突破] ¥{price:.2f} < ¥{st["sell_trigger"]:.2f}')
        st['fstate'] = STATE_IDLE
        st['sell_peak_price'] = 0.0


def _handle_sold(ContextInfo, price):
    """SOLD: 已卖出, 等跌到买回线"""
    st = ContextInfo.st
    bt = st['buyback_target']

    if price <= bt:
        drop = (st['sell_fill_price'] - price) / st['sell_fill_price'] * 100
        st['fstate']        = STATE_BUY_DIPPING
        st['buy_dip_price'] = price
        _log(f'[买回监控] 跌{drop:.2f}% → ¥{price:.2f} ≤ ¥{bt:.2f} | 等回升{BOUNCE_PCT*100:.2f}%')
        return


def _handle_buy_dipping(ContextInfo, price):
    """BUY_DIPPING: 跌到位, 等回升确认买回"""
    st = ContextInfo.st

    if price < st['buy_dip_price']:
        st['buy_dip_price'] = price

    dip = st['buy_dip_price']
    bounce = (price - dip) / dip if dip > 0 else 0

    if bounce >= BOUNCE_PCT:
        sp = st['sell_fill_price']
        gross = (sp - price) * TRADE_LOT_SIZE
        _log(f'[买回] 谷值¥{dip:.2f} 回升{bounce*100:.2f}% → ¥{price:.2f} | 毛利≈¥{gross:.0f}')
        _do_buyback(ContextInfo, price)
        st['fstate'] = STATE_DONE
        st['total_t_days'] += 1
        st['total_sell_trades'] += 1
        st['total_pnl'] += gross
        return

    # 假跌破
    if price > st['buyback_target']:
        _log(f'[假跌破] ¥{price:.2f} > ¥{st["buyback_target"]:.2f}')
        st['fstate'] = STATE_SOLD
        st['buy_dip_price'] = 0.0


# ============================================================================
# 第六部分：状态处理 — 买入侧(正T)
# ============================================================================

def _handle_dipping(ContextInfo, price):
    """DIPPING: 跟踪谷值, 等回升确认买入"""
    st = ContextInfo.st

    if price < st['buy_dip_price']:
        st['buy_dip_price'] = price

    dip = st['buy_dip_price']
    bounce = (price - dip) / dip if dip > 0 else 0

    if bounce >= BOUNCE_PCT:
        if st['buy_side_done']:        # ★ 确认点守卫
            return
        # 检查资金
        need = price * TRADE_LOT_SIZE * 1.001
        avail = _cash(ContextInfo)
        if avail < need:
            _log(f'[买入失败] 资金不足: 需¥{need:,.0f} > ¥{avail:,.0f}')
            st['fstate'] = STATE_IDLE
            return
        _log(f'[买入] 谷值¥{dip:.2f} 回升{bounce*100:.2f}% → ¥{price:.2f}')
        _do_buy(ContextInfo, price)
        st['fstate']         = STATE_BOUGHT
        st['buy_side_done']  = True
        st['buy_fill_price'] = price
        st['sellback_target'] = round(price * (1.0 + SELLBACK_RISE_PCT), 2)
        _log(f'  买价¥{price:.2f} | 卖回线¥{st["sellback_target"]:.2f}(+{SELLBACK_RISE_PCT*100:.2f}%)')
        return

    # 假跌破
    if price > st['buy_trigger']:
        _log(f'[假跌破] ¥{price:.2f} > ¥{st["buy_trigger"]:.2f}')
        st['fstate'] = STATE_IDLE
        st['buy_dip_price'] = 0.0


def _handle_bought(ContextInfo, price):
    """BOUGHT: 已买入, 等涨到卖回线"""
    st = ContextInfo.st
    st_target = st['sellback_target']

    if price >= st_target:
        rise = (price - st['buy_fill_price']) / st['buy_fill_price'] * 100
        st['fstate']          = STATE_SELL_SPIKING
        st['sell_peak_price'] = price
        _log(f'[卖回监控] 涨{rise:.2f}% → ¥{price:.2f} ≥ ¥{st_target:.2f} | 等回落{PULLBACK_PCT*100:.2f}%')
        return


def _handle_sell_spiking(ContextInfo, price):
    """SELL_SPIKING: 涨到位, 等回落确认卖出"""
    st = ContextInfo.st

    if price > st['sell_peak_price']:
        st['sell_peak_price'] = price

    peak = st['sell_peak_price']
    pullback = (peak - price) / peak if peak > 0 else 0

    if pullback >= PULLBACK_PCT:
        bp = st['buy_fill_price']
        gross = (price - bp) * TRADE_LOT_SIZE
        _log(f'[卖回] 峰值¥{peak:.2f} 回落{pullback*100:.2f}% → ¥{price:.2f} | 毛利≈¥{gross:.0f}')
        _do_sellback(ContextInfo, price)
        st['fstate'] = STATE_DONE
        st['total_t_days'] += 1
        st['total_buy_trades'] += 1
        st['total_pnl'] += gross
        return

    # 假突破
    if price < st['sellback_target']:
        _log(f'[假突破] ¥{price:.2f} < ¥{st["sellback_target"]:.2f}')
        st['fstate'] = STATE_BOUGHT
        st['sell_peak_price'] = 0.0


# ============================================================================
# 第七部分：下单函数
# ============================================================================

def _do_sell(ContextInfo, price):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 卖出 ¥{price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  >>> 卖出失败: {e}')
        st['fstate'] = STATE_IDLE


def _do_buyback(ContextInfo, price):
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)
    if avail < need:
        _log(f'  >>> 买回失败: 资金不足(需¥{need:,.0f} > ¥{avail:,.0f})')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 买回 ¥{price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  >>> 买回失败: {e}')


def _do_buy(ContextInfo, price):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 买入 ¥{price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  >>> 买入失败: {e}')
        st['fstate'] = STATE_IDLE


def _do_sellback(ContextInfo, price):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 卖回 ¥{price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  >>> 卖回失败: {e}')


def _force_buyback(ContextInfo):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[强制买回] 对手价 × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'[强制买回失败!!] {e}')


def _force_sellback(ContextInfo):
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[强制卖出] 对手价 × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'[强制卖出失败!!] {e}')


def _cash(ContextInfo):
    try:
        a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception:
        return 0.0


def _acc(ContextInfo):
    try:
        if hasattr(ContextInfo, 'accID') and ContextInfo.accID:
            return ContextInfo.accID
    except Exception:
        pass
    return ACCOUNT


# ============================================================================
# 第八部分：QMT 回调
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    d = '买' if deal.m_nDirection == 1 else '卖'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    if deal.m_nDirection == 2:
        st['day_pnl'] += (amt - fee)
    else:
        st['day_pnl'] -= (amt + fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f}×{deal.m_nVolume} | 当日PnL≈¥{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'\n{"="*55}')
        _log(f'  {STOCK_NAME} v2.0 已停止')
        _log(f'  反T: {st.get("total_sell_trades", 0)}次 | 正T: {st.get("total_buy_trades", 0)}次')
        _log(f'  总PnL≈¥{st.get("total_pnl", 0):,.0f}')
        fstate = st.get('fstate', '')
        if fstate in (STATE_SOLD, STATE_BUY_DIPPING):
            _log(f'  ⚠ 已卖出未买回! 请手动检查!')
        if fstate == STATE_BOUGHT:
            _log(f'  ⚠ 已买入未卖出! 请手动检查!')
        _log(f'{"="*55}')


# ============================================================================
# 第九部分：工具函数
# ============================================================================

def _now():
    return _time.strftime('%H:%M:%S')


def _ts():
    return _time.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    ts = _ts()
    if args:
        print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
