# -*- coding: gbk -*-
"""
================================================================================
 QMT 阈值双向T策略 v3.0 — 确保成交版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【v3.0 核心改动 — 确保成交 + 跨日运行 + 稳定性修复】

  v2问题:
    - 全部使用 'FIX' 限价单: 回落/反弹触发时价格快速移动, 限价单挂在已过时的
      价格上无法成交 → 错过T点, 甚至当日持仓方向错误
    - 强制补单(_force_*) 只在尾盘触发, 盘中无兜底
    - handlebar 初始化后永久跳过: 跨日运行时开盘价、触发线永不更新

  v3方案:
    - ★ 正常下单全部改用 'COMPETE' 对手价: 卖出吃买一, 买入吃卖一, 立即成交
    - ★ 补单机制: 下单后 ontimer 监控, 未成交自动重试(最多3次)
    - ★ 最后一次补单用 'MARKET' 市价单兜底, 绝对确保成交
    - ★ 跨日自动重置: ontimer 检测日期变更, 新交易日自动重置日内状态
    - 信号价格仅用于日志记录, 便于对比滑点
    - deal_callback 自动清除待成交标记, 终止补单

  v3.0.1 稳定性修复 (2026-08-06):
    - ★ handlebar 改为纯空转: 1分钟K线导致 handlebar 每分钟触发且
      is_last_bar()=True, 反复执行初始化 → 状态机被重置 → 无限卖出循环
    - ★ ontimer 加30秒初始化冷却: 防止频繁重试 tick 数据
    - ★ _submit_order 防重复下单: 已有挂单时拒绝新单, 不再覆盖补单状态
    - ★ _check_and_retry 加防重入: order_retry_logged 后立即终止
    - ⚠ 模拟模式(simulation mode): QMT客户端 → 设置 → 交易模式 → 实盘

  v2保留:
    - tick驱动状态机
    - 反T+正T双向
    - 假突破/假跌破检测
    - 尾盘强制平仓(_force_*保持COMPETE)
    - 所有核心参数

 【机制1 — 卖出(反T)】
   价格 ≥ 开盘×(1+SELL_TRIGGER_PCT) → SPIKING跟踪峰值
     → 从峰值回落 ≥ PULLBACK_PCT → 卖出(对手价) → SOLD等买回
     → 跌到卖价下方BUYBACK_DROP_PCT → BUY_DIPPING探底
     → 回升 ≥ BOUNCE_PCT → 买回(对手价) → DONE

 【机制2 — 买入(正T)】
   价格 ≤ 开盘×(1-BUY_TRIGGER_PCT) → DIPPING跟踪谷值
     → 从谷值回升 ≥ BOUNCE_PCT → 买入(对手价) → BOUGHT等卖出
     → 涨到买价上方SELLBACK_RISE_PCT → SELL_SPIKING冲高
     → 回落 ≥ PULLBACK_PCT → 卖出(对手价) → DONE

 【补单机制】
   下单 → 3秒后检查 (deal_callback清除pending标记)
     → 未成交? 补单 COMPETE (最多2次)
     → 第3次仍未成交? 市价单 MARKET 兜底
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
SELL_TRIGGER_PCT = 0.02            # 卖出触发: 涨超开盘价9%
BUY_TRIGGER_PCT  = 0.03            # 买入触发: 跌超开盘价5%

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

# ── ★ v3: 补单参数 ──
RETRY_DELAY_SEC = 1.0              # 补单检查间隔(秒)
MAX_RETRIES      = 3               # 最大补单次数(最后一次用MARKET兜底)


# ============================================================================
# 第二部分：状态定义
# ============================================================================

STATE_IDLE         = 'IDLE'          # 等待触发  
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
        'trade_date': '',              # 当前交易日(用于跨日检测)
        'initialized': False,          # 今日开盘价已获取?
        'last_init_time': 0.0,         # 上次初始化时间戳(防handlebar反复触发)
        'sell_side_done': False,       # 当日已卖出?(防重复)
        'buy_side_done': False,        # 当日已买入?(防重复)
        'startup_printed': False,
        'last_fstate': '',             # 上一次状态(用于状态变化日志)

        # ★ v3: 补单追踪
        'order_pending': False,        # 是否有未成交委托
        'order_side': '',              # '卖出'/'买回'/'买入'/'卖回' — 补单方向
        'order_signal_price': 0.0,     # 触发价(仅日志)
        'order_sent_at': 0.0,          # 下单时间戳(time.time())
        'order_retries': 0,            # 已补单次数
        'order_retry_logged': False,   # 是否已输出补单日志(防刷屏)
    }
    ContextInfo.st = state
    today_str = _time.strftime('%Y-%m-%d')
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, f"{today_str} 09:00:00", "SH")


def handlebar(ContextInfo):
    """日线回调 — ★ v3fix: 纯空转, 所有逻辑在 ontimer 中处理.

    原因: QMT主K线为SH000300[1分钟], handlebar每分钟触发且 is_last_bar()==True,
    会导致初始化逻辑反复执行、状态机被反复重置. 故改为完全禁用.
    """
    pass


# ============================================================================
# 第四部分：ontimer — 完全自包含状态机
# ============================================================================

def ontimer(ContextInfo):
    """定时器回调 — ★ v3fix: 唯一的状态管理和初始化入口"""
    st = ContextInfo.st
    now = _now()
    today = _time.strftime('%Y-%m-%d')
    now_ts = _time.time()

    # ── 跨日检测(唯一入口) ──
    if st.get('trade_date', '') != today:
        if st.get('trade_date', ''):
            _log(f'\n[新交易日] {st["trade_date"]} → {today} 重置日内状态')
        st['trade_date'] = today
        _reset_daily_state(st)

    # ── 初始化: 30秒冷却防handlebar反复触发 ──
    if not st.get('initialized', False):
        if now_ts - st.get('last_init_time', 0) < 30.0:
            return  # 冷却中, 避免频繁重试
        st['last_init_time'] = now_ts
        _try_init_today(ContextInfo)
        if st.get('initialized', False) and not st.get('startup_printed'):
            _print_startup(ContextInfo)
        return  # 初始化完成前不交易

    if not _is_market_open(now):
        return

    # ── ★ v3: 补单检查(优先级最高) ──
    if st.get('order_pending', False):
        _check_and_retry(ContextInfo, now)
        # 补单期间仍然走状态机(状态已切换, 不会重复下单)

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
# 第七部分：下单函数 — ★ v3: 对手价+补单, 确保成交
# ============================================================================

def _submit_order(ContextInfo, shares, price, side_label):
    """
    通用下单 — 对手价 COMPETE, 立即吃掉对手盘确保成交.
    设置 pending 标记, 超时后 ontimer 自动检查, 未成交则补单.
    """
    st = ContextInfo.st

    # ★ v3fix: 防止状态机异常重置导致的重复下单
    if st.get('order_pending', False):
        existing_side = st.get('order_side', '?')
        existing_price = st.get('order_signal_price', 0)
        _log(f'  [下单拦截] 已有{existing_side}挂单(¥{existing_price:.2f})未成交, 跳过{side_label}')
        return

    try:
        order_shares(STOCK_QMT, shares, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['order_pending']       = True
        st['order_side']          = side_label
        st['order_signal_price']  = price
        st['order_sent_at']       = _time.time()
        st['order_retries']       = 0
        st['order_retry_logged']  = False
        _log(f'  >>> {side_label} 对手价 × {abs(shares)}股 (信号价¥{price:.2f})')
    except Exception as e:
        _log(f'  >>> {side_label}失败: {e}')
        st['fstate'] = STATE_IDLE
        st['order_pending'] = False


def _do_sell(ContextInfo, price):
    _submit_order(ContextInfo, -TRADE_LOT_SIZE, price, '卖出')


def _do_buyback(ContextInfo, price):
    need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)
    if avail < need:
        _log(f'  >>> 买回失败: 资金不足(需¥{need:,.0f} > ¥{avail:,.0f})')
        return
    _submit_order(ContextInfo, TRADE_LOT_SIZE, price, '买回')


def _do_buy(ContextInfo, price):
    _submit_order(ContextInfo, TRADE_LOT_SIZE, price, '买入')


def _do_sellback(ContextInfo, price):
    _submit_order(ContextInfo, -TRADE_LOT_SIZE, price, '卖回')


def _check_and_retry(ContextInfo, now):
    """
    ontimer 中调用: 检查订单是否超时未成交, 超时则补单.
    deal_callback 会清除 order_pending 标记, 终止补单.
    """
    st = ContextInfo.st

    # ★ v3fix: 防重入 — 已标记放弃则不再检查
    if st.get('order_retry_logged', False):
        return

    elapsed = _time.time() - st.get('order_sent_at', 0)
    if elapsed < RETRY_DELAY_SEC:
        return  # 还没到检查时间

    retries = st.get('order_retries', 0)

    # 超过最大次数 → 放弃
    if retries >= MAX_RETRIES:
        if not st.get('order_retry_logged'):
            _log(f'[!] 补单{MAX_RETRIES}次仍未成交, 请手动检查持仓!')
            st['order_retry_logged'] = True
            st['order_pending'] = False
        return

    # ── 补单 ──
    retries += 1
    st['order_retries'] = retries

    side = st.get('order_side', '?')
    signal_price = st.get('order_signal_price', 0.0)

    # 最后一次用市价单 MARKET 兜底
    if retries >= MAX_RETRIES:
        style = 'MARKET'
        style_name = '市价单'
    else:
        style = 'COMPETE'
        style_name = '对手价'

    shares_sign = -TRADE_LOT_SIZE if side in ('卖出', '卖回') else TRADE_LOT_SIZE

    try:
        order_shares(STOCK_QMT, shares_sign, style, ContextInfo, _acc(ContextInfo))
        st['order_sent_at'] = _time.time()  # 重置计时器
        _log(f'[补单#{retries}] {side} → {style_name} × {abs(shares_sign)}股 (信号价¥{signal_price:.2f})')
    except Exception as e:
        _log(f'[补单#{retries}失败!!] {e}')
        st['order_pending'] = False


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


# ============================================================================
# 第八部分：辅助函数
# ============================================================================

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
# 第九部分：QMT 回调
# ============================================================================

def order_callback(ContextInfo, order):
    sm = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
    if order.m_nOrderStatus in sm:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {sm[order.m_nOrderStatus]}')
        # 废单/已撤: 清除pending让补单生效
        if order.m_nOrderStatus in (55, 56):
            ContextInfo.st['order_sent_at'] = 0.0  # 立即触发补单


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    # ★ v3: 成交即清除待成交标记, 终止补单
    st['order_pending'] = False
    st['order_retry_logged'] = False

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
        _log(f'  {STOCK_NAME} v3.0 已停止')
        _log(f'  反T: {st.get("total_sell_trades", 0)}次 | 正T: {st.get("total_buy_trades", 0)}次')
        _log(f'  总PnL≈¥{st.get("total_pnl", 0):,.0f}')
        fstate = st.get('fstate', '')
        if st.get('order_pending', False):
            _log(f'  ⚠ 有未成交委托! 请手动检查!')
        if fstate in (STATE_SOLD, STATE_BUY_DIPPING):
            _log(f'  ⚠ 已卖出未买回! 请手动检查!')
        if fstate == STATE_BOUGHT:
            _log(f'  ⚠ 已买入未卖出! 请手动检查!')
        _log(f'{"="*55}')


# ============================================================================
# 第十部分：工具函数
# ============================================================================

def _reset_daily_state(st):
    """新交易日: 重置所有日内状态, 保留累计统计和底仓"""
    st['initialized']       = False
    st['last_init_time']    = 0.0
    st['sell_side_done']    = False
    st['buy_side_done']     = False
    st['startup_printed']   = False
    st['last_fstate']       = ''
    st['fstate']            = STATE_IDLE
    st['open_price']        = 0.0
    st['sell_trigger']      = 0.0
    st['buy_trigger']       = 0.0
    st['sell_peak_price']   = 0.0
    st['sell_fill_price']   = 0.0
    st['buyback_target']    = 0.0
    st['buy_dip_price']     = 0.0
    st['buy_fill_price']    = 0.0
    st['sellback_target']   = 0.0
    st['day_pnl']           = 0.0
    st['order_pending']     = False
    st['order_side']        = ''
    st['order_signal_price'] = 0.0
    st['order_sent_at']     = 0.0
    st['order_retries']     = 0
    st['order_retry_logged'] = False


def _try_init_today(ContextInfo):
    """尝试获取今日开盘价并计算触发线, 成功则设置 initialized=True"""
    st = ContextInfo.st
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


def _print_startup(ContextInfo):
    """打印每日启动信息(仅一次)"""
    st = ContextInfo.st

    # 更新持仓(可能因分红/送股变化)
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
        st['fstate'] = STATE_DONE
        st['startup_printed'] = True
        return

    _log(f'{"="*55}')
    _log(f'  {STOCK_NAME} 阈值双向T v3.0 — 确保成交版')
    _log(f'  交易日: {st.get("trade_date", "?")}')
    _log(f'  今日开盘: ¥{st["open_price"]:.2f}')
    _log(f'  卖出触发线: ¥{st["sell_trigger"]:.2f} (开盘+{SELL_TRIGGER_PCT*100:.1f}%)')
    _log(f'  买入触发线: ¥{st["buy_trigger"]:.2f} (开盘-{BUY_TRIGGER_PCT*100:.1f}%)')
    _log(f'  回落确认: {PULLBACK_PCT*100:.2f}% | 回升确认: {BOUNCE_PCT*100:.2f}%')
    _log(f'  反T: {"✓" if ENABLE_SELL_SIDE else "✗"} | 正T: {"✓" if ENABLE_BUY_SIDE else "✗"}')
    _log(f'  下单: 对手价COMPETE | 补单: {MAX_RETRIES}次兜底')
    _log(f'  持仓: {st["base_shares"]}股 | 成本: ¥{st["base_cost"]:.2f}')
    _log(f'{"="*55}')
    st['startup_printed'] = True


def _now():
    return _time.strftime('%H:%M:%S')


def _is_market_open(now):
    """检查是否在交易时段内"""
    if '09:25:00' <= now <= '11:30:00':
        return True
    if '13:00:00' <= now <= '15:00:00':
        return True
    return False


def _log(msg):
    print(f'[{_now()}] {msg}')
