# -*- coding: gbk -*-
"""
================================================================================
 QMT 阈值双向T策略 v1.0 — 双阈值触发版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【核心机制 — 两个独立触发信号】

  机制1 — 卖出(反T: 先卖后买):
    价格 ≥ SELL_TRIGGER_PCT(基于开盘价) → 进入冲高监控
      → 从最高点回落 ≥ PULLBACK_PCT(0.05%) → 确认卖出
      → 卖出后, 价格跌至卖价下方 + 探底回升 → 买回

  机制2 — 买入(正T: 先买后卖):
    价格 ≤ BUY_TRIGGER_PCT(基于开盘价) → 进入探底监控
      → 从最低点回升 ≥ BOUNCE_PCT(0.05%) → 确认买入
      → 买入后, 价格涨至买价上方 + 冲高回落 → 卖出

 【用户可调参数】
  SELL_TRIGGER_PCT  — 卖出触发阈值 (开盘价上方百分比, 如 0.02 = +2%)
  BUY_TRIGGER_PCT   — 买入触发阈值 (开盘价下方百分比, 如 0.02 = -2%)
  PULLBACK_PCT      — 冲高回落确认幅度 (默认0.05%)
  BOUNCE_PCT        — 探底回升确认幅度 (默认0.05%)

 【状态机】
          ┌─ price ≥ sell_trigger → SPIKING(等回落) ─┐
          │                                           ▼
   ┌── IDLE ──┐                                SOLD(等买回)
   │          │                                      │
   │          │                              price跌到买回线
   │          │                                      ▼
   │          │                            BUY_DIPPING(等回升) → 买回 → DONE
   │          │
   │          └─ price ≤ buy_trigger → DIPPING(等回升) ─┐
   │                                                    ▼
   │                                              BOUGHT(等卖出)
   │                                                    │
   │                                            price涨到卖出线
   │                                                    ▼
   │                                          SELL_SPIKING(等回落) → 卖出 → DONE
   └───────────────────────────────────────────────────────────────┘

 【与现有策略的区别】
  - 不依赖 ATR/RSI/量比 等复杂指标, 纯价格阈值驱动
  - 双向交易: 同时支持 反T(先卖) 和 正T(先买)
  - 简单直观: 只有4个核心参数, 用户自己输入两个阈值
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
MIN_LOT        = 100
# TIMER_INTERVAL = '1nSecond'
TIMER_INTERVAL = '500nMilliSecond'

# ── ★ 核心参数: 两个阈值(用户输入) ──
#  卖出触发阈值: 当日价格 ≥ 开盘价 × (1 + SELL_TRIGGER_PCT) 时, 开始监控冲高回落
#  例如: 0.02 表示涨超开盘价2%后开始关注卖出
SELL_TRIGGER_PCT = 0.11            # ★ 卖出触发阈值(用户输入)

#  买入触发阈值: 当日价格 ≤ 开盘价 × (1 - BUY_TRIGGER_PCT) 时, 开始监控探底回升
#  例如: 0.02 表示跌超开盘价2%后开始关注买入
BUY_TRIGGER_PCT  = 0.05            # ★ 买入触发阈值(用户输入)

# ── 确认参数 ──
PULLBACK_PCT = 0.0005              # 冲高回落确认: 从最高点回落0.05% → 卖出
BOUNCE_PCT   = 0.0005              # 探底回升确认: 从最低点回升0.05% → 买入

# ── 买回/卖出参数(完成T+0闭环) ──
#  卖出后买回: 从卖价跌多少开始关注买回(基于卖价)
BUYBACK_DROP_PCT  = 0.005          # 卖价下方0.5%开始监控买回
#  买入后卖出: 从买价涨多少开始关注卖出(基于买价)
SELLBACK_RISE_PCT = 0.005          # 买价上方0.5%开始监控卖出

# ── 风控参数 ──
ENABLE_RISK_CONTROL    = 0      # ★ 风控总开关: True/1=启用紧急买回/止损/紧急卖出, False/0=关闭
EMERGENCY_BUYBACK_PCT  = 0.03      # 卖飞紧急买回: 卖价+3%
STOP_LOSS_PCT          = 0.02      # 单笔止损: 卖价+2% → 强制买回
EMERGENCY_SELL_PCT     = 0.03      # 买套紧急卖出: 买价-3%

# ── 尾盘强制买回开关 ──
ENABLE_END_BUY = 0

# ── 时间 ──
FORCE_CLOSE_TIME = '14:57:00'      # 尾盘强制平仓

# ── 数据 ──
HIST_DATA_LEN = 60
COMMISSION    = 0.00025
STAMP_TAX     = 0.001

# ── 双向交易开关 ──
ENABLE_SELL_SIDE = True            # 启用卖出机制(反T)
ENABLE_BUY_SIDE  = True            # 启用买入机制(正T)


# ============================================================================
# 第二部分：状态定义
# ============================================================================

# 卖出侧(反T): IDLE → SPIKING → SOLD → BUY_DIPPING → DONE
STATE_IDLE         = 'IDLE'
STATE_SPIKING      = 'SPIKING'       # 价格 ≥ 卖出阈值, 跟踪最高点等回落
STATE_SOLD         = 'SOLD'          # 已卖出, 等待买回机会
STATE_BUY_DIPPING  = 'BUY_DIPPING'   # 跌到买回线, 跟踪最低点等回升

# 买入侧(正T): IDLE → DIPPING → BOUGHT → SELL_SPIKING → DONE
STATE_DIPPING      = 'DIPPING'       # 价格 ≤ 买入阈值, 跟踪最低点等回升
STATE_BOUGHT       = 'BOUGHT'        # 已买入, 等待卖出机会
STATE_SELL_SPIKING = 'SELL_SPIKING'  # 涨到卖出线, 跟踪最高点等回落

STATE_DONE         = 'DONE'
STATE_FORCED       = 'FORCED'        # 尾盘/止损强制平仓


# ============================================================================
# 第三部分：QMT 策略入口
# ============================================================================

def init(ContextInfo):
    """QMT策略初始化"""
    try:
        ContextInfo.set_universe([STOCK_QMT])
    except Exception:
        pass
    try:
        ContextInfo.set_account(ACCOUNT)
    except Exception:
        pass

    state = {
        # 持仓信息
        'base_shares': 0,
        'base_cost': 0.0,
        'entry_price': 0.0,

        # 今日信号(handlebar中计算)
        'open_price': 0.0,

        # 状态机
        'fstate': STATE_IDLE,

        # 卖出侧追踪
        'sell_peak_price': 0.0,       # SPIKING期间的最高价
        'sell_fill_price': 0.0,       # 实际卖出成交价
        'buyback_target': 0.0,        # 买回触发线 = 卖价 × (1 - BUYBACK_DROP_PCT)

        # 买入侧追踪
        'buy_dip_price': 0.0,         # DIPPING期间的最低价
        'buy_fill_price': 0.0,        # 实际买入成交价
        'sellback_target': 0.0,       # 卖回触发线 = 买价 × (1 + SELLBACK_RISE_PCT)

        # 交易统计
        'day_pnl': 0.0,
        'total_t_days': 0,
        'total_pnl': 0.0,
        'total_sell_trades': 0,       # 反T次数
        'total_buy_trades': 0,        # 正T次数

        # 状态追踪
        'state_enter_time': '',
        'startup_printed': False,
        'initialized': False,          # ★ handlebar首次完成初始化后置True, 之后不再重置状态机
        'trade_date': '',              # 用于跨日检测(实盘中策略常驻内存)
        'sell_side_done': False,       # ★ 当日卖出侧已成交, 防重复卖出
        'buy_side_done': False,        # ★ 当日买入侧已成交, 防重复买入
    }
    ContextInfo.st = state
    today_str = _time.strftime('%Y-%m-%d')
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, f"{today_str} 09:00:00", "SH")


def handlebar(ContextInfo):
    """日线回调 — 首次调用时初始化参数, 之后只更新数据不重置状态机
    ★ 使用 initialized 标志: 首次完成初始化后置True, 之后handlebar不再触碰
       fstate/sell_side_done/buy_side_done 等状态机变量, 防止模拟模式
       下高频触发handlebar导致状态机被反复打断。
    """
    st = ContextInfo.st
    is_live = ContextInfo.is_last_bar()

    # ── ★ 已完成初始化? 只更新数据, 不重置状态 ──
    if st.get('initialized', False):
        # ── 跨日检测: 新交易日则重新初始化 ──
        today = _time.strftime('%Y%m%d')
        if st.get('trade_date', '') != today:
            st['initialized'] = False  # 触发下方首次初始化逻辑
        else:
            # 同日: 只更新持仓 + 刷新开盘价, 绝不碰状态机
            try:
                positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
                for pos in positions:
                    if pos.m_strInstrumentID == STOCK_CODE:
                        st['base_shares'] = pos.m_nVolume
                        break
            except Exception:
                pass
            # 刷新开盘价(防止首次拿到过期数据)
            try:
                opens = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
                if STOCK_QMT in opens and len(opens[STOCK_QMT]) > 0:
                    new_open = opens[STOCK_QMT][-1]
                    if new_open > 0 and st.get('open_price', 0) > 0:
                        if abs(new_open - st['open_price']) / st['open_price'] > 0.01:
                            st['open_price'] = new_open
                            st['sell_trigger'] = round(new_open * (1.0 + SELL_TRIGGER_PCT), 2)
                            st['buy_trigger']  = round(new_open * (1.0 - BUY_TRIGGER_PCT), 2)
                            _log(f'[数据更新] 开盘价刷新: ¥{new_open:.2f} | 卖出线: ¥{st["sell_trigger"]:.2f} | 买入线: ¥{st["buy_trigger"]:.2f}')
            except Exception:
                pass
            if is_live:
                try:
                    closes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
                    curr_close = closes[STOCK_QMT][-1] if STOCK_QMT in closes else 0
                    accounts = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
                    avail_cash = accounts[0].m_dAvailable if accounts else 0.0
                    pos_value = st['base_shares'] * curr_close if curr_close > 0 else 0
                    _print_status(ContextInfo, curr_close, avail_cash, pos_value)
                except Exception:
                    pass
            return

    # ── 首次初始化: 设置参数 + 重置状态机(仅此一次) ──

    # 获取历史数据
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')

    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 5:
        return

    # 获取持仓
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

    if st['entry_price'] == 0.0:
        st['entry_price'] = base_cost

    # 当日关键价格
    curr_close = closes[STOCK_QMT][-1]
    curr_open  = opens[STOCK_QMT][-1]
    avail_cash = accounts[0].m_dAvailable if accounts else 0.0
    pos_value  = base_shares * curr_close

    # 计算今日触发线
    st['open_price']    = curr_open
    st['sell_trigger']  = round(curr_open * (1.0 + SELL_TRIGGER_PCT), 2)
    st['buy_trigger']   = round(curr_open * (1.0 - BUY_TRIGGER_PCT), 2)

    # 重置状态机
    st['fstate']           = STATE_IDLE
    st['sell_peak_price']  = 0.0
    st['sell_fill_price']  = 0.0
    st['buyback_target']   = 0.0
    st['buy_dip_price']    = 0.0
    st['buy_fill_price']   = 0.0
    st['sellback_target']  = 0.0
    st['day_pnl']          = 0.0
    st['sell_side_done']   = False      # ★ 重置当日卖出标记
    st['buy_side_done']    = False      # ★ 重置当日买入标记
    st['state_enter_time'] = _now()
    st['initialized']      = True       # ★ 标记初始化完成, 此后handlebar不再重置状态机
    st['trade_date']       = _time.strftime('%Y%m%d')  # 记录初始化日期

    if is_live:
        _print_status(ContextInfo, curr_close, avail_cash, pos_value)
    elif not st['startup_printed']:
        _log(f'{"="*55}')
        _log(f'  {STOCK_NAME} 阈值双向T v1.0 — 已加载')
        _log(f'  开盘 ¥{curr_open:.2f} | 昨收 ¥{curr_close:.2f}')
        _log(f'  卖出触发线: ¥{st["sell_trigger"]:.2f} (开盘+{SELL_TRIGGER_PCT*100:.1f}%)')
        _log(f'  买入触发线: ¥{st["buy_trigger"]:.2f} (开盘-{BUY_TRIGGER_PCT*100:.1f}%)')
        _log(f'  回落确认: {PULLBACK_PCT*100:.2f}% | 回升确认: {BOUNCE_PCT*100:.2f}%')
        _log(f'  反T: {"✓" if ENABLE_SELL_SIDE else "✗"} | 正T: {"✓" if ENABLE_BUY_SIDE else "✗"}')
        _log(f'{"="*55}')
        st['startup_printed'] = True


def _print_status(ContextInfo, curr_close, avail_cash, pos_value):
    """打印账户状态"""
    st = ContextInfo.st
    cost = st['entry_price']
    unreal_pnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_close / cost - 1) * 100 if cost > 0 else 0
    
    _log(f'')
    _log(f'━━━ {"账户状态":─^30} ━━━')
    _log(f'  持仓: {st["base_shares"]}股 × ¥{curr_close:.2f} = ¥{pos_value:,.0f}')
    _log(f'  浮盈: ¥{unreal_pnl:,.0f}({unreal_pct:+.1f}%) | 现金: ¥{avail_cash:,.0f}')

    # 正T可行性
    lot_cost = curr_close * TRADE_LOT_SIZE
    if avail_cash >= lot_cost * 1.01:
        _log(f'  正T: ✓ 可用 (1手需¥{lot_cost:,.0f})')
    else:
        _log(f'  正T: ✗ 资金不足 (1手需¥{lot_cost:,.0f} > 现金¥{avail_cash:,.0f})')

    # 交易统计
    if st['total_t_days'] > 0:
        _log(f'  累计: {st["total_t_days"]}天 | 反T{st["total_sell_trades"]}次 正T{st["total_buy_trades"]}次 | PnL≈¥{st["total_pnl"]:,.0f}')

    _log(f'━━━ {"今日信号":─^30} ━━━')
    _log(f'  开盘: ¥{st["open_price"]:.2f}')
    _log(f'  卖出触发线: ¥{st["sell_trigger"]:.2f} (开盘+{SELL_TRIGGER_PCT*100:.1f}%)')
    _log(f'  买入触发线: ¥{st["buy_trigger"]:.2f} (开盘-{BUY_TRIGGER_PCT*100:.1f}%)')


# ============================================================================
# 第四部分：ontimer — 状态机驱动
# ============================================================================

def ontimer(ContextInfo):
    """定时器回调 — 核心状态机"""
    st = ContextInfo.st
    now = _now()

    if not _is_market_open(now):
        return

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return

    if st.get('base_shares', 0) < TRADE_LOT_SIZE:
        return

    # 获取实时价格
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

    # ★ 打印实时价格(每秒一次)
    _print_tick(price, st)

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
    if now >= FORCE_CLOSE_TIME and ENABLE_END_BUY:
        if fstate == STATE_SOLD:
            _log(f'[尾盘] {now} 未买回, 强制买回')
            _force_buyback(ContextInfo)
        elif fstate == STATE_BUY_DIPPING:
            _log(f'[尾盘] {now} 监控中, 强制买回')
            _force_buyback(ContextInfo)
        elif fstate == STATE_BOUGHT:
            _log(f'[尾盘] {now} 未卖出, 强制卖出')
            _force_sellback(ContextInfo)
        elif fstate == STATE_SELL_SPIKING:
            _log(f'[尾盘] {now} 监控中, 强制卖出')
            _force_sellback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_DIPPING, STATE_IDLE):
            st['fstate'] = STATE_DONE

    # ── 风控: 卖飞止损(卖出侧) ──
    if ENABLE_RISK_CONTROL and fstate == STATE_SOLD:
        loss_line = st['sell_fill_price'] * (1.0 + STOP_LOSS_PCT)
        if price >= loss_line:
            _log(f'[止损] 卖¥{st["sell_fill_price"]:.2f} → 现¥{price:.2f}(+{STOP_LOSS_PCT*100:.1f}%) 强制买回')
            _force_buyback(ContextInfo)

    # ── 风控: 买套止损(买入侧) ──
    if ENABLE_RISK_CONTROL and fstate == STATE_BOUGHT:
        loss_line = st['buy_fill_price'] * (1.0 - EMERGENCY_SELL_PCT)
        if price <= loss_line:
            _log(f'[止损] 买¥{st["buy_fill_price"]:.2f} → 现¥{price:.2f}(-{EMERGENCY_SELL_PCT*100:.1f}%) 强制卖出')
            _force_sellback(ContextInfo)


# ============================================================================
# 第五部分：状态处理函数
# ============================================================================

# ─────────────────────────────────────────────────────────────────────────────
#  IDLE: 等待任一阈值触发
# ─────────────────────────────────────────────────────────────────────────────

def _handle_idle(ContextInfo, price):
    """IDLE状态: 同时监控卖出和买入两个触发条件"""
    st = ContextInfo.st

    # 卖出侧: 价格达到卖出阈值 → 进入冲高监控
    if ENABLE_SELL_SIDE and price >= st['sell_trigger']:
        # ★ 防重复卖出: 当日已卖过则跳过
        if st.get('sell_side_done', False):
            return
        # ★ 防超额卖出: 可用持仓不足1手则跳过
        if st.get('base_shares', 0) < TRADE_LOT_SIZE:
            _log(f'[卖出跳过] 可用持仓不足1手({st.get("base_shares", 0)}股)')
            return
        st['fstate']          = STATE_SPIKING
        st['sell_peak_price'] = price
        st['state_enter_time'] = _now()
        over_pct = (price - st['sell_trigger']) / st['sell_trigger'] * 100
        _log(f'[卖出监控] ▲ 价格 ¥{price:.2f} ≥ 触发线 ¥{st["sell_trigger"]:.2f}(+{over_pct:.2f}%)')
        _log(f'  peak=¥{price:.2f} | 回落线=¥{price*(1-PULLBACK_PCT):.2f}(-{PULLBACK_PCT*100:.2f}%)')
        return

    # 买入侧: 价格达到买入阈值 → 进入探底监控
    if ENABLE_BUY_SIDE and price <= st['buy_trigger']:
        # ★ 防重复买入: 当日已买过则跳过
        if st.get('buy_side_done', False):
            return
        st['fstate']          = STATE_DIPPING
        st['buy_dip_price']   = price
        st['state_enter_time'] = _now()
        under_pct = (st['buy_trigger'] - price) / st['buy_trigger'] * 100
        _log(f'[买入监控] ▼ 价格 ¥{price:.2f} ≤ 触发线 ¥{st["buy_trigger"]:.2f}(-{under_pct:.2f}%)')
        _log(f'  dip=¥{price:.2f} | 回升线=¥{price*(1+BOUNCE_PCT):.2f}(+{BOUNCE_PCT*100:.2f}%)')
        return


# ─────────────────────────────────────────────────────────────────────────────
#  卖出侧 — 反T: SPIKING → SOLD → BUY_DIPPING → 买回
# ─────────────────────────────────────────────────────────────────────────────

def _handle_spiking(ContextInfo, price):
    """
    SPIKING状态: 价格已突破卖出阈值, 跟踪最高点等回落

    触发条件: 价格 ≥ 卖出阈值
    退出条件:
      - 从最高点回落 ≥ PULLBACK_PCT → 确认卖出 → 进入SOLD
      - 价格跌回阈值下方 → 假突破 → 回到IDLE
    """
    st = ContextInfo.st

    # 更新最高价
    if price > st['sell_peak_price']:
        old_peak = st['sell_peak_price']
        st['sell_peak_price'] = price
        _log(f'  [新高] ¥{old_peak:.2f} → ¥{price:.2f}(+{(price-old_peak)/old_peak*100:.2f}%)')

    peak = st['sell_peak_price']
    pullback = (peak - price) / peak

    # 回落确认 → 卖出
    if pullback >= PULLBACK_PCT:
        # ★ 双重保护: 当日已卖出则不再卖
        if st.get('sell_side_done', False):
            return
        _log(f'[卖出确认] ✓ 冲高回落!')
        _log(f'  最高 ¥{peak:.2f} → 现价 ¥{price:.2f} (回落 {pullback*100:.2f}%)')
        _do_sell(ContextInfo, price)
        st['fstate']          = STATE_SOLD
        st['sell_side_done']  = True    # ★ 标记当日已卖出, 防止重复
        st['sell_fill_price'] = price
        st['state_enter_time'] = _now()

        # 计算买回触发线: 基于实际卖出价
        st['buyback_target'] = round(price * (1.0 - BUYBACK_DROP_PCT), 2)
        _log(f'  卖价: ¥{price:.2f} | 买回触发线: ¥{st["buyback_target"]:.2f}(-{BUYBACK_DROP_PCT*100:.2f}%)')
        if ENABLE_RISK_CONTROL:
            _log(f'  紧急买回: ¥{price*(1+EMERGENCY_BUYBACK_PCT):.2f} | 止损: ¥{price*(1+STOP_LOSS_PCT):.2f}')
        else:
            _log(f'  风控: 已关闭')
        return

    # 假突破 → 回退
    if price < st['sell_trigger']:
        _log(f'[假突破] ¥{price:.2f} 跌回触发线下 ¥{st["sell_trigger"]:.2f} | 最高触及 ¥{peak:.2f}')
        st['fstate']          = STATE_IDLE
        st['sell_peak_price'] = 0.0
        st['state_enter_time'] = _now()
        return


def _handle_sold(ContextInfo, price):
    """
    SOLD状态: 已卖出, 等待价格跌到买回触发线

    触发条件: 卖出成交后
    退出条件:
      - 紧急买回: price ≥ 卖价 × (1 + EMERGENCY_BUYBACK_PCT)
      - 跌到买回线: price ≤ buyback_target → 进入BUY_DIPPING
    """
    st = ContextInfo.st
    sp = st['sell_fill_price']
    bt = st['buyback_target']

    # 条件1: 紧急买回(卖飞了) — 受风控开关控制
    if ENABLE_RISK_CONTROL:
        emergency_line = sp * (1.0 + EMERGENCY_BUYBACK_PCT)
        if price >= emergency_line:
            rise_pct = (price - sp) / sp * 100
            _log(f'[紧急买回] 🔴 卖飞! 卖¥{sp:.2f} → 现¥{price:.2f}(+{rise_pct:.2f}%) > 紧急线 ¥{emergency_line:.2f}')
            _do_buyback(ContextInfo, price, '紧急')
            st['fstate'] = STATE_DONE
            return

    # 条件2: 跌到买回触发线
    if price <= bt:
        drop_pct = (sp - price) / sp * 100
        st['fstate']        = STATE_BUY_DIPPING
        st['buy_dip_price'] = price  # 复用 buy_dip_price 追踪买回侧最低点
        st['state_enter_time'] = _now()
        _log(f'[买回监控] ▼ 卖¥{sp:.2f} → 现¥{price:.2f}(-{drop_pct:.2f}%) ≤ 触发线 ¥{bt:.2f}')
        _log(f'  dip=¥{price:.2f} | 回升线=¥{price*(1+BOUNCE_PCT):.2f}(+{BOUNCE_PCT*100:.2f}%)')
        return


def _handle_buy_dipping(ContextInfo, price):
    """
    BUY_DIPPING状态: 价格已跌到买回触发线, 跟踪最低点等回升

    触发条件: 价格 ≤ buyback_target
    退出条件:
      - 从最低点回升 ≥ BOUNCE_PCT → 确认买回
      - 价格涨回买回线之上 → 假跌破 → 回到SOLD
    """
    st = ContextInfo.st
    bt = st['buyback_target']

    # 更新最低价
    if price < st['buy_dip_price']:
        old_dip = st['buy_dip_price']
        st['buy_dip_price'] = price
        _log(f'  [新低] ¥{old_dip:.2f} → ¥{price:.2f}(-{(old_dip-price)/old_dip*100:.2f}%)')

    dip = st['buy_dip_price']
    bounce = (price - dip) / dip

    # 回升确认 → 买回
    if bounce >= BOUNCE_PCT:
        sp = st['sell_fill_price']
        gross = (sp - price) * TRADE_LOT_SIZE
        _log(f'[买回确认] ✓ 探底回升!')
        _log(f'  最低 ¥{dip:.2f} → 现价 ¥{price:.2f} (回升 {bounce*100:.2f}%)')
        _log(f'  卖¥{sp:.2f} → 买¥{price:.2f} | 价差 ¥{sp-price:.2f}/股 | 毛利≈¥{gross:.0f}')
        _do_buyback(ContextInfo, price, '正常')
        st['fstate'] = STATE_DONE
        st['total_t_days'] += 1
        st['total_sell_trades'] += 1
        st['total_pnl'] += gross
        return

    # 假跌破 → 回退
    if price > bt:
        _log(f'[假跌破] ¥{price:.2f} 涨回买回线上 ¥{bt:.2f} | 最低触及 ¥{dip:.2f}')
        st['fstate']        = STATE_SOLD
        st['buy_dip_price'] = 0.0
        st['state_enter_time'] = _now()
        return


# ─────────────────────────────────────────────────────────────────────────────
#  买入侧 — 正T: DIPPING → BOUGHT → SELL_SPIKING → 卖出
# ─────────────────────────────────────────────────────────────────────────────

def _handle_dipping(ContextInfo, price):
    """
    DIPPING状态: 价格已跌破买入阈值, 跟踪最低点等回升

    触发条件: 价格 ≤ 买入阈值
    退出条件:
      - 从最低点回升 ≥ BOUNCE_PCT → 确认买入 → 进入BOUGHT
      - 价格涨回阈值上方 → 假跌破 → 回到IDLE
    """
    st = ContextInfo.st

    # 更新最低价
    if price < st['buy_dip_price']:
        old_dip = st['buy_dip_price']
        st['buy_dip_price'] = price
        _log(f'  [新低] ¥{old_dip:.2f} → ¥{price:.2f}(-{(old_dip-price)/old_dip*100:.2f}%)')

    dip = st['buy_dip_price']
    bounce = (price - dip) / dip

    # 回升确认 → 买入
    if bounce >= BOUNCE_PCT:
        # ★ 双重保护: 当日已买入则不再买
        if st.get('buy_side_done', False):
            return
        _log(f'[买入确认] ✓ 探底回升!')
        _log(f'  最低 ¥{dip:.2f} → 现价 ¥{price:.2f} (回升 {bounce*100:.2f}%)')

        # 检查资金
        need = price * TRADE_LOT_SIZE * 1.001
        avail = _cash(ContextInfo)
        if avail < need:
            _log(f'  ✗ 资金不足: 需¥{need:,.0f} > 可用¥{avail:,.0f}')
            st['fstate'] = STATE_IDLE
            return

        _do_buy(ContextInfo, price)
        st['fstate']         = STATE_BOUGHT
        st['buy_side_done']  = True     # ★ 标记当日已买入, 防止重复
        st['buy_fill_price'] = price
        st['state_enter_time'] = _now()

        # 计算卖回触发线: 基于实际买入价
        st['sellback_target'] = round(price * (1.0 + SELLBACK_RISE_PCT), 2)
        _log(f'  买价: ¥{price:.2f} | 卖回触发线: ¥{st["sellback_target"]:.2f}(+{SELLBACK_RISE_PCT*100:.2f}%)')
        if ENABLE_RISK_CONTROL:
            _log(f'  紧急卖出: ¥{price*(1-EMERGENCY_SELL_PCT):.2f}')
        else:
            _log(f'  风控: 已关闭')
        return

    # 假跌破 → 回退
    if price > st['buy_trigger']:
        _log(f'[假跌破] ¥{price:.2f} 涨回触发线上 ¥{st["buy_trigger"]:.2f} | 最低触及 ¥{dip:.2f}')
        st['fstate']        = STATE_IDLE
        st['buy_dip_price'] = 0.0
        st['state_enter_time'] = _now()
        return


def _handle_bought(ContextInfo, price):
    """
    BOUGHT状态: 已买入, 等待价格涨到卖回触发线

    触发条件: 买入成交后
    退出条件:
      - 紧急卖出: price ≤ 买价 × (1 - EMERGENCY_SELL_PCT)
      - 涨到卖回线: price ≥ sellback_target → 进入SELL_SPIKING
    """
    st = ContextInfo.st
    bp = st['buy_fill_price']
    st_target = st['sellback_target']

    # 条件1: 紧急卖出(买套了) — 受风控开关控制
    if ENABLE_RISK_CONTROL:
        emergency_line = bp * (1.0 - EMERGENCY_SELL_PCT)
        if price <= emergency_line:
            drop_pct = (bp - price) / bp * 100
            _log(f'[紧急卖出] 🔴 买套! 买¥{bp:.2f} → 现¥{price:.2f}(-{drop_pct:.2f}%) < 紧急线 ¥{emergency_line:.2f}')
            _do_sellback(ContextInfo, price, '紧急')
            st['fstate'] = STATE_DONE
            return

    # 条件2: 涨到卖回触发线
    if price >= st_target:
        rise_pct = (price - bp) / bp * 100
        st['fstate']          = STATE_SELL_SPIKING
        st['sell_peak_price'] = price  # 复用 sell_peak_price 追踪卖回侧最高点
        st['state_enter_time'] = _now()
        _log(f'[卖回监控] ▲ 买¥{bp:.2f} → 现¥{price:.2f}(+{rise_pct:.2f}%) ≥ 触发线 ¥{st_target:.2f}')
        _log(f'  peak=¥{price:.2f} | 回落线=¥{price*(1-PULLBACK_PCT):.2f}(-{PULLBACK_PCT*100:.2f}%)')
        return


def _handle_sell_spiking(ContextInfo, price):
    """
    SELL_SPIKING状态: 价格已涨到卖回触发线, 跟踪最高点等回落

    触发条件: 价格 ≥ sellback_target
    退出条件:
      - 从最高点回落 ≥ PULLBACK_PCT → 确认卖出
      - 价格跌回卖回线下方 → 假突破 → 回到BOUGHT
    """
    st = ContextInfo.st

    # 更新最高价
    if price > st['sell_peak_price']:
        old_peak = st['sell_peak_price']
        st['sell_peak_price'] = price
        _log(f'  [新高] ¥{old_peak:.2f} → ¥{price:.2f}(+{(price-old_peak)/old_peak*100:.2f}%)')

    peak = st['sell_peak_price']
    pullback = (peak - price) / peak

    # 回落确认 → 卖出
    if pullback >= PULLBACK_PCT:
        bp = st['buy_fill_price']
        gross = (price - bp) * TRADE_LOT_SIZE
        _log(f'[卖回确认] ✓ 冲高回落!')
        _log(f'  最高 ¥{peak:.2f} → 现价 ¥{price:.2f} (回落 {pullback*100:.2f}%)')
        _log(f'  买¥{bp:.2f} → 卖¥{price:.2f} | 价差 ¥{price-bp:.2f}/股 | 毛利≈¥{gross:.0f}')
        _do_sellback(ContextInfo, price, '正常')
        st['fstate'] = STATE_DONE
        st['total_t_days'] += 1
        st['total_buy_trades'] += 1
        st['total_pnl'] += gross
        return

    # 假突破 → 回退
    if price < st['sellback_target']:
        _log(f'[假突破] ¥{price:.2f} 跌回卖回线下 ¥{st["sellback_target"]:.2f} | 最高触及 ¥{peak:.2f}')
        st['fstate']           = STATE_BOUGHT
        st['sell_peak_price']  = 0.0
        st['state_enter_time'] = _now()
        return


# ============================================================================
# 第六部分：下单函数
# ============================================================================

def _do_sell(ContextInfo, price):
    """卖出(反T第一步)"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 卖出下单: ¥{price:.2f} × {TRADE_LOT_SIZE}股 = ¥{price*TRADE_LOT_SIZE:,.0f}')
    except Exception as e:
        _log(f'  >>> 卖出失败: {e}')
        st['fstate'] = STATE_IDLE


def _do_buyback(ContextInfo, price, reason=''):
    """买回(反T第二步)"""
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)
    if avail < need:
        _log(f'  >>> 买回失败: 资金不足 (需¥{need:,.0f} > ¥{avail:,.0f})')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 买回下单({reason}): ¥{price:.2f} × {TRADE_LOT_SIZE}股 = ¥{price*TRADE_LOT_SIZE:,.0f}')
    except Exception as e:
        _log(f'  >>> 买回失败({reason}): {e}')


def _do_buy(ContextInfo, price):
    """买入(正T第一步)"""
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)
    if avail < need:
        _log(f'  >>> 买入失败: 资金不足 (需¥{need:,.0f} > ¥{avail:,.0f})')
        return
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 买入下单: ¥{price:.2f} × {TRADE_LOT_SIZE}股 = ¥{price*TRADE_LOT_SIZE:,.0f}')
    except Exception as e:
        _log(f'  >>> 买入失败: {e}')
        st['fstate'] = STATE_IDLE


def _do_sellback(ContextInfo, price, reason=''):
    """卖出(正T第二步)"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 卖出下单({reason}): ¥{price:.2f} × {TRADE_LOT_SIZE}股 = ¥{price*TRADE_LOT_SIZE:,.0f}')
    except Exception as e:
        _log(f'  >>> 卖出失败({reason}): {e}')


def _force_buyback(ContextInfo):
    """强制买回(对手价)"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[强制买回] ✓ 对手价 × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'[强制买回失败!!] {e}')
        st['fstate'] = STATE_FORCED


def _force_sellback(ContextInfo):
    """强制卖出(对手价)"""
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[强制卖出] ✓ 对手价 × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'[强制卖出失败!!] {e}')
        st['fstate'] = STATE_FORCED


def _cash(ContextInfo):
    """获取可用资金"""
    try:
        a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return a[0].m_dAvailable if a else 0.0
    except Exception:
        return 0.0


def _acc(ContextInfo):
    """获取账户ID"""
    try:
        if hasattr(ContextInfo, 'accID') and ContextInfo.accID:
            return ContextInfo.accID
    except Exception:
        pass
    return ACCOUNT


# ============================================================================
# 第七部分：QMT 回调
# ============================================================================

def order_callback(ContextInfo, order):
    """委托状态回调"""
    sm = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    """成交回调 — 更新当日PnL"""
    st = ContextInfo.st
    d = '买入' if deal.m_nDirection == 1 else '卖出'
    amt = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax
    if deal.m_nDirection == 2:   # 卖出 → 收入
        st['day_pnl'] += (amt - fee)
    else:                         # 买入 → 支出
        st['day_pnl'] -= (amt + fee)
    _log(f'[成交] {d} ¥{deal.m_dPrice:.2f} × {deal.m_nVolume}股 = ¥{amt:,.0f} | 费用¥{fee:.2f} | 当日PnL≈¥{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    """策略停止"""
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'\n{"="*55}')
        _log(f'  {STOCK_NAME} 阈值双向T v1.0 已停止')
        _log(f'  累计交易: {st.get("total_t_days", 0)}天')
        _log(f'  反T: {st.get("total_sell_trades", 0)}次 | 正T: {st.get("total_buy_trades", 0)}次')
        _log(f'  总PnL≈¥{st.get("total_pnl", 0):,.0f}')
        if st.get('fstate') in (STATE_SOLD, STATE_BUY_DIPPING):
            _log(f'  ⚠⚠ 警告: 已卖出未买回! 请手动检查底仓!')
        if st.get('fstate') == STATE_BOUGHT:
            _log(f'  ⚠⚠ 警告: 已买入未卖出! 请手动检查持仓!')
        _log(f'{"="*55}')


# ============================================================================
# 第八部分：工具函数
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


def _print_tick(price, st):
    """每秒打印实时价格 + 当前状态 + 关键触发线"""
    fstate = st.get('fstate', '?')

    # 根据状态组装关键信息
    if fstate == STATE_IDLE:
        extra = f'| 卖出线¥{st.get("sell_trigger",0):.1f} 买入线¥{st.get("buy_trigger",0):.1f}'
    elif fstate == STATE_SPIKING:
        peak = st.get('sell_peak_price', price)
        pullback = (peak - price) / peak * 100 if peak > 0 else 0
        extra = f'| peak¥{peak:.2f} 回落{pullback:.2f}%'
    elif fstate == STATE_SOLD:
        sp = st.get('sell_fill_price', 0)
        chg = (price - sp) / sp * 100 if sp > 0 else 0
        extra = f'| 卖¥{sp:.2f} 现{chg:+.2f}% 买回线¥{st.get("buyback_target",0):.1f}'
    elif fstate == STATE_BUY_DIPPING:
        dip = st.get('buy_dip_price', price)
        bounce = (price - dip) / dip * 100 if dip > 0 else 0
        extra = f'| dip¥{dip:.2f} 回升{bounce:.2f}%'
    elif fstate == STATE_DIPPING:
        dip = st.get('buy_dip_price', price)
        bounce = (price - dip) / dip * 100 if dip > 0 else 0
        extra = f'| dip¥{dip:.2f} 回升{bounce:.2f}% 买入线¥{st.get("buy_trigger",0):.1f}'
    elif fstate == STATE_BOUGHT:
        bp = st.get('buy_fill_price', 0)
        chg = (price - bp) / bp * 100 if bp > 0 else 0
        extra = f'| 买¥{bp:.2f} 现{chg:+.2f}% 卖回线¥{st.get("sellback_target",0):.1f}'
    elif fstate == STATE_SELL_SPIKING:
        peak = st.get('sell_peak_price', price)
        pullback = (peak - price) / peak * 100 if peak > 0 else 0
        extra = f'| peak¥{peak:.2f} 回落{pullback:.2f}%'
    else:
        extra = ''
    _log(f'当前实时价格')
    _log(f'¥{price:.2f} [{fstate}] {extra}')


def _is_market_open(now):
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
