# -*- coding: gbk -*-
"""
================================================================================
 QMT 阈值双向T策略 v4.0 — 稳定版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【v4.0 核心改动 — 全面稳定性重构】

  v3已知问题:
    - 崩溃退出: ontimer 内未捕获异常导致策略进程被QMT杀死
    - 状态机反复重置: 1分钟K线 handlebar 反复触发初始化
    - 补单循环: 状态重置导致订单状态被覆盖, 补单永不停
    - 无心跳日志: 策略静默时用户不知道是否还在运行

  v4方案:
    - ★ ontimer 全局异常捕获: 任何未处理异常都会被记录并跳过, 不会崩溃
    - ★ handlebar 完全禁用: 空函数, 所有逻辑在 ontimer 单一路径
    - ★ 30秒初始化冷却 + 最大10次尝试: 防止初始化风暴
    - ★ 60秒心跳日志: 策略空闲时用户知道它还活着
    - ★ 状态切换去重: 相同状态变化1分钟内不重复打印
    - ★ 初始化与状态机完全解耦: init 失败不影响状态机运行

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
   下单 → 超时检查 (deal_callback清除pending标记)
     → 未成交? 补单 COMPETE (最多2次)
     → 第3次仍未成交? 市价单 MARKET 兜底 → 放弃
================================================================================
"""
import time as _time
import sys as _sys
import traceback as _traceback

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
SELL_TRIGGER_PCT = 0.02            # 卖出触发: 涨超开盘价2%
BUY_TRIGGER_PCT  = 0.03            # 买入触发: 跌超开盘价3%

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
ENABLE_END_BUYBACK = 0             # 尾盘强制买回/卖出

# ── 补单 ──
RETRY_DELAY_SEC  = 2.0             # 补单检查间隔(秒)
MAX_RETRIES      = 3               # 最大补单次数(最后一次用MARKET兜底)

# ── 稳定性 ──
INIT_COOLDOWN_SEC = 30.0           # 初始化重试冷却(秒)
MAX_INIT_ATTEMPTS = 10             # 最大初始化尝试次数
HEARTBEAT_SEC     = 60             # 心跳日志间隔(秒)
STATE_LOG_COOLDOWN = 60            # 同状态切换日志冷却(秒)


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
    """策略初始化 — v4: 最小化, 所有逻辑延迟到 ontimer"""
    try:
        ContextInfo.set_universe([STOCK_QMT])
    except Exception:
        pass
    try:
        ContextInfo.set_account(ACCOUNT)
    except Exception:
        pass

    state = {
        # 持仓(ontimer 初始化时填充)
        'base_shares': 0,
        'base_cost': 0.0,
        'entry_price': 0.0,

        # 今日参数(ontimer 初始化时填充)
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

        # 交易统计(跨日保留)
        'day_pnl': 0.0,
        'total_t_days': 0,
        'total_pnl': 0.0,
        'total_sell_trades': 0,
        'total_buy_trades': 0,

        # ── 关键标志 ──
        'trade_date': '',              # 当前交易日(跨日检测)
        'initialized': False,          # 今日开盘价已获取?
        'init_attempts': 0,            # 初始化尝试次数
        'last_init_time': 0.0,         # 上次初始化时间戳
        'sell_side_done': False,       # 当日已卖出?
        'buy_side_done': False,        # 当日已买入?
        'startup_printed': False,
        'last_fstate': '',
        'last_state_log_time': 0.0,    # 上次状态切换日志时间
        'last_heartbeat': 0.0,         # 上次心跳时间
        'ontimer_errors': 0,           # ontimer异常计数

        # ── 补单追踪 ──
        'order_pending': False,
        'order_side': '',
        'order_signal_price': 0.0,
        'order_sent_at': 0.0,
        'order_retries': 0,
        'order_retry_logged': False,
    }
    ContextInfo.st = state
    today_str = _time.strftime('%Y-%m-%d')
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, f"{today_str} 09:00:00", "SH")


def handlebar(ContextInfo):
    """日线回调 — v4: 完全禁用.

    QMT主K线为SH000300[1分钟], handlebar每分钟触发且is_last_bar()=True.
    任何初始化逻辑放在这里都会导致每分钟执行一次 → 状态机反复重置.
    所有逻辑统一在 ontimer 中处理.
    """
    pass


# ============================================================================
# 第四部分：ontimer — 核心引擎(v4: 全局异常保护)
# ============================================================================

def ontimer(ContextInfo):
    """定时器回调 — v4: 唯一入口 + 全局异常捕获"""
    try:
        _ontimer_impl(ContextInfo)
    except Exception as e:
        st = getattr(ContextInfo, 'st', None)
        err_count = st.get('ontimer_errors', 0) + 1 if st else 1
        if st:
            st['ontimer_errors'] = err_count
        # 最多打印3次异常详情, 之后只计数
        if err_count <= 3:
            _log(f'[!!异常#{err_count}] {e}')
            _log(f'  {_traceback.format_exc()[-200:]}')
        if err_count == 10:
            _log(f'[!!] ontimer已累计{err_count}次异常, 策略可能不稳定')


def _ontimer_impl(ContextInfo):
    """ontimer 实际逻辑 — v4: 跨日检测 + 初始化 + 心跳 + 状态机"""
    st = ContextInfo.st
    now = _now()
    today = _time.strftime('%Y-%m-%d')
    now_ts = _time.time()

    # ═══════════════════════════════════════════════════════════
    # 1. 跨日检测(唯一入口)
    # ═══════════════════════════════════════════════════════════
    if st.get('trade_date', '') != today:
        if st.get('trade_date', ''):
            _log(f'\n[新交易日] {st["trade_date"]} → {today} 重置')
        st['trade_date'] = today
        _reset_daily_state(st)

    # ═══════════════════════════════════════════════════════════
    # 2. 初始化(唯一入口, 带冷却和最大尝试)
    # ═══════════════════════════════════════════════════════════
    if not st.get('initialized', False):
        attempts = st.get('init_attempts', 0)
        if attempts >= MAX_INIT_ATTEMPTS:
            # 超过最大尝试次数, 不再重试(避免API风暴)
            if attempts == MAX_INIT_ATTEMPTS:
                _log(f'[!!] 初始化失败已达{MAX_INIT_ATTEMPTS}次, 放弃今日交易')
                st['init_attempts'] = attempts + 1  # 只打印一次
            return
        if now_ts - st.get('last_init_time', 0) < INIT_COOLDOWN_SEC:
            return  # 冷却中
        st['last_init_time'] = now_ts
        st['init_attempts'] = attempts + 1
        _try_init_today(ContextInfo)
        if st.get('initialized', False):
            if not st.get('startup_printed'):
                _print_startup(ContextInfo)
        else:
            if attempts == 0:
                _log(f'[初始化] 等待开盘价... (尝试#{st["init_attempts"]})')
        return  # 初始化未完成 → 不进入状态机

    # 初始化完成, 重置尝试计数
    if st.get('init_attempts', 0) > 0:
        st['init_attempts'] = 0

    # ═══════════════════════════════════════════════════════════
    # 3. 心跳日志(证明策略还活着)
    # ═══════════════════════════════════════════════════════════
    if now_ts - st.get('last_heartbeat', 0) >= HEARTBEAT_SEC:
        st['last_heartbeat'] = now_ts
        fstate = st.get('fstate', '?')
        _log(f'[心跳] {fstate} | ¥{st.get("open_price", 0):.2f}')

    # ═══════════════════════════════════════════════════════════
    # 4. 交易时段检查
    # ═══════════════════════════════════════════════════════════
    if not _is_market_open(now):
        return

    # ═══════════════════════════════════════════════════════════
    # 5. 补单检查(优先级最高 — 订单是实时的)
    # ═══════════════════════════════════════════════════════════
    if st.get('order_pending', False):
        _check_and_retry(ContextInfo)

    # ═══════════════════════════════════════════════════════════
    # 6. 终态检查
    # ═══════════════════════════════════════════════════════════
    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return

    if st.get('base_shares', 0) < TRADE_LOT_SIZE:
        return

    # ═══════════════════════════════════════════════════════════
    # 7. 获取实时行情
    # ═══════════════════════════════════════════════════════════
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return

    if STOCK_QMT not in tick:
        return

    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0:
        return

    # ═══════════════════════════════════════════════════════════
    # 8. 状态变化日志(去重: 60秒内同状态不重复)
    # ═══════════════════════════════════════════════════════════
    fstate = st['fstate']
    last_fs = st.get('last_fstate', '')
    if fstate != last_fs:
        # 去重: 60秒内同状态切换只打印一次
        if fstate != st.get('_last_logged_state', '') or \
           now_ts - st.get('last_state_log_time', 0) >= STATE_LOG_COOLDOWN:
            _log(f'>>> 状态: {last_fs or "启动"} → {fstate} | ¥{price:.2f}')
            st['_last_logged_state'] = fstate
            st['last_state_log_time'] = now_ts
        st['last_fstate'] = fstate

    # ═══════════════════════════════════════════════════════════
    # 9. 状态路由
    # ═══════════════════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════════════════
    # 10. 尾盘强制平仓
    # ═══════════════════════════════════════════════════════════
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

    # ── 卖出侧(反T) ──
    if ENABLE_SELL_SIDE and price >= st['sell_trigger']:
        if st['sell_side_done']:
            return
        st['fstate']          = STATE_SPIKING
        st['sell_peak_price'] = price
        _log(f'[卖出监控] ¥{price:.2f} ≥ ¥{st["sell_trigger"]:.2f} → 等回落{PULLBACK_PCT*100:.2f}%')
        return

    # ── 买入侧(正T) ──
    if ENABLE_BUY_SIDE and price <= st['buy_trigger']:
        if st['buy_side_done']:
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
        if st['sell_side_done']:
            return
        _log(f'[卖出] 峰值¥{peak:.2f} 回落{pullback*100:.2f}% → ¥{price:.2f}')
        _do_sell(ContextInfo, price)
        st['fstate']          = STATE_SOLD
        st['sell_side_done']  = True
        st['sell_fill_price'] = price
        st['buyback_target']  = round(price * (1.0 - BUYBACK_DROP_PCT), 2)
        _log(f'  卖价¥{price:.2f} | 买回线¥{st["buyback_target"]:.2f}(-{BUYBACK_DROP_PCT*100:.2f}%)')
        return

    # 假突破: 价格跌回触发线以下
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

    # 假跌破: 价格涨回买回线以上
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
        if st['buy_side_done']:
            return
        need = price * TRADE_LOT_SIZE * 1.001
        avail = _cash(ContextInfo)
        if avail < need:
            _log(f'[买入失败] 资金不足: 需¥{need:,.0f} > ¥{avail:,.0f}')
            st['fstate'] = STATE_IDLE
            return
        _log(f'[买入] 谷值¥{dip:.2f} 回升{bounce*100:.2f}% → ¥{price:.2f}')
        _do_buy(ContextInfo, price)
        st['fstate']          = STATE_BOUGHT
        st['buy_side_done']   = True
        st['buy_fill_price']  = price
        st['sellback_target'] = round(price * (1.0 + SELLBACK_RISE_PCT), 2)
        _log(f'  买价¥{price:.2f} | 卖回线¥{st["sellback_target"]:.2f}(+{SELLBACK_RISE_PCT*100:.2f}%)')
        return

    # 假跌破: 价格涨回触发线以上
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

    # 假突破: 价格跌回卖回线以下
    if price < st['sellback_target']:
        _log(f'[假突破] ¥{price:.2f} < ¥{st["sellback_target"]:.2f}')
        st['fstate'] = STATE_BOUGHT
        st['sell_peak_price'] = 0.0


# ============================================================================
# 第七部分：下单函数
# ============================================================================

def _submit_order(ContextInfo, shares, price, side_label):
    """通用下单 — 对手价 COMPETE"""
    st = ContextInfo.st

    # 防止状态机异常导致的重复下单
    if st.get('order_pending', False):
        existing = st.get('order_side', '?')
        _log(f'  [下单拦截] 已有{existing}挂单未成交, 跳过{side_label}')
        return

    try:
        order_shares(STOCK_QMT, shares, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['order_pending']       = True
        st['order_side']          = side_label
        st['order_signal_price']  = price
        st['order_sent_at']       = _time.time()
        st['order_retries']       = 0
        st['order_retry_logged']  = False
        _log(f'  >>> {side_label} 对手价 × {abs(shares)}股 (信号¥{price:.2f})')
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


def _check_and_retry(ContextInfo):
    """检查订单是否超时未成交, 超时则补单(最多MAX_RETRIES次)"""
    st = ContextInfo.st

    # 已放弃 → 不再检查
    if st.get('order_retry_logged', False):
        return

    elapsed = _time.time() - st.get('order_sent_at', 0)
    if elapsed < RETRY_DELAY_SEC:
        return

    retries = st.get('order_retries', 0)

    # 超过最大次数 → 永久放弃
    if retries >= MAX_RETRIES:
        if not st.get('order_retry_logged'):
            _log(f'[!] 补单{MAX_RETRIES}次未成交, 放弃. 请检查持仓!')
            st['order_retry_logged'] = True
            st['order_pending'] = False
        return

    # 补单
    retries += 1
    st['order_retries'] = retries

    side = st.get('order_side', '?')
    signal_price = st.get('order_signal_price', 0.0)

    # 最后一次用市价单 MARKET
    if retries >= MAX_RETRIES:
        style, style_name = 'MARKET', '市价单'
    else:
        style, style_name = 'COMPETE', '对手价'

    shares_sign = -TRADE_LOT_SIZE if side in ('卖出', '卖回') else TRADE_LOT_SIZE

    try:
        order_shares(STOCK_QMT, shares_sign, style, ContextInfo, _acc(ContextInfo))
        st['order_sent_at'] = _time.time()
        _log(f'[补单#{retries}] {side} → {style_name} × {abs(shares_sign)}股 (信号¥{signal_price:.2f})')
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
        # 废单/已撤 → 立即可补单
        if order.m_nOrderStatus in (55, 56):
            ContextInfo.st['order_sent_at'] = 0.0


def deal_callback(ContextInfo, deal):
    st = ContextInfo.st
    # 成交 → 清除待成交标记, 终止补单
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
        errors = st.get('ontimer_errors', 0)
        _log(f'\n{"="*55}')
        _log(f'  {STOCK_NAME} v4.0 已停止')
        _log(f'  反T: {st.get("total_sell_trades", 0)}次 | 正T: {st.get("total_buy_trades", 0)}次')
        _log(f'  总PnL≈¥{st.get("total_pnl", 0):,.0f}')
        if errors > 0:
            _log(f'  ontimer异常: {errors}次')
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
    st['initialized']         = False
    st['init_attempts']       = 0
    st['last_init_time']      = 0.0
    st['sell_side_done']      = False
    st['buy_side_done']       = False
    st['startup_printed']     = False
    st['last_fstate']         = ''
    st['_last_logged_state']  = ''
    st['last_state_log_time'] = 0.0
    st['fstate']              = STATE_IDLE
    st['open_price']          = 0.0
    st['sell_trigger']        = 0.0
    st['buy_trigger']         = 0.0
    st['sell_peak_price']     = 0.0
    st['sell_fill_price']     = 0.0
    st['buyback_target']      = 0.0
    st['buy_dip_price']       = 0.0
    st['buy_fill_price']      = 0.0
    st['sellback_target']     = 0.0
    st['day_pnl']             = 0.0
    st['order_pending']       = False
    st['order_side']          = ''
    st['order_signal_price']  = 0.0
    st['order_sent_at']       = 0.0
    st['order_retries']       = 0
    st['order_retry_logged']  = False


def _try_init_today(ContextInfo):
    """尝试获取今日开盘价, 成功则设置 initialized=True"""
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
    """打印每日启动信息"""
    st = ContextInfo.st

    # 读取持仓
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
    _log(f'  {STOCK_NAME} 阈值双向T v4.0 — 稳定版')
    _log(f'  交易日: {st.get("trade_date", "?")}')
    _log(f'  今日开盘: ¥{st["open_price"]:.2f}')
    _log(f'  卖出触发: ¥{st["sell_trigger"]:.2f} (+{SELL_TRIGGER_PCT*100:.1f}%)')
    _log(f'  买入触发: ¥{st["buy_trigger"]:.2f} (-{BUY_TRIGGER_PCT*100:.1f}%)')
    _log(f'  回落{ PULLBACK_PCT*100:.2f}% | 回升{BOUNCE_PCT*100:.2f}%')
    _log(f'  反T: {"✓" if ENABLE_SELL_SIDE else "✗"} | 正T: {"✓" if ENABLE_BUY_SIDE else "✗"}')
    _log(f'  下单: COMPETE | 补单: {MAX_RETRIES}次 | 心跳: {HEARTBEAT_SEC}s')
    _log(f'  持仓: {st["base_shares"]}股 | 成本: ¥{st["base_cost"]:.2f}')
    _log(f'{"="*55}')
    st['startup_printed'] = True
    st['last_heartbeat'] = _time.time()


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
