# -*- coding: gbk -*-
"""
================================================================================
 QMT 迷你反T策略 v12.0 — 百分之一回升买回版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【v12.0 新增】
    - 卖出后若尚未触及原买回触发线，价格先下探至卖价的99%（条件1）
    - 随后价格从99%线下方回升并再次达到99%线时，立即买回
    - 该买回委托使用卖一价（SALE1）

 【v10.0 — 借鉴阈值双向T v1→v8演化经验的全面增强】

  阈值双向T 演化路径回顾:
    v1→v2: handlebar反复重置状态机 → 加initialized守卫, ontimer完全自包含
    v2→v3: FIX限价单不成交   → 改COMPETE对手价 + 补单机制(MARKET兜底)
    v3→v4: ontimer崩溃杀进程  → 全局try/except + 心跳 + 初始化冷却
    v4→v5: 回调属性名错误     → m_dOrderPrice→m_dLimitPrice, m_fCommission→m_dComssion
    v6→v7: 同日多次reset       → _guard_date日期锁, sell标记加固, 启动打印锁
    v7→v8: 卖出不检查可用股   → m_nCanUseVolume检查, 价格校验±15%

  v10 对应改进清单:
  ┌────────┬──────────────────────────────────────┬──────────────────────────────┐
  │ 严重度  │ v9问题                                │ v10修复                       │
  ├────────┼──────────────────────────────────────┼──────────────────────────────┤
  │ ★★★   │ handlebar每根bar都重置状态机           │ 加trade_date锁,每天只初始化一次 │
  │ ★★★   │ FIX限价单可能不成交(快市滑点)         │ 改COMPETE对手价+补单(MARKET兜底)│
  │ ★★★   │ ontimer无异常捕获→崩溃杀进程          │ 全局try/except + 异常计数      │
  │ ★★★   │ 回调属性名错误(m_dOrderPrice等不存在) │ 修正为正确的QMT API属性名       │
  │ ★★    │ 无日期锁,同日可能多次reset             │ _guard_date + _startup_guard   │
  │ ★★    │ 卖出不检查m_nCanUseVolume(T+1锁定)    │ _submit_order持仓校验          │
  │ ★★    │ 无价格校验,可能发超出范围的废单        │ ±15%偏离开盘价拦截             │
  │ ★★    │ 无心跳日志,策略静默时用户不知道状态    │ 60s交易心跳 + 300s终态心跳      │
  │ ★★    │ 状态切换日志可能刷屏                   │ 去重: 同转换不重复打印          │
  │ ★     │ sell标记在_order之后设置(重入风险)     │ 先标记再下单(防重入)            │
  │ ★     │ 缺少补单机制(挂单不成交无人管)         │ 2次补单: COMPETE→MARKET→放弃   │
  │ ★     │ 无重复下单拦截                         │ order_pending守卫              │
  └────────┴──────────────────────────────────────┴──────────────────────────────┘

  策略逻辑继承v9不变:
    - ATR动态乘数模型: BASE bear=0.40, sideways=0.55, weak_bull=0.65
    - 4因子偏差: 趋势±0.25 + 波动率±0.35 + 成交量±0.25 + RSI±0.25
    - mult_min/max: 0.20/1.50
    - 趋势4级: strong_bull(禁), weak_bull(允/谨慎), bear(允/积极), sideways(默认)
    - 紧急买回: 3%, 动态收紧: 卖后30s不跌则收紧买回目标
    - 日内振幅约束: 触发线≤开盘×(1+近10日均振幅×0.8)
================================================================================
"""
# ============================================================================
# 第一部分：全局配置
# ============================================================================

# ---- QMT 账户 ----
ACCOUNT = '8890145315'

# ---- 标的 ----
STOCK_CODE = '601869'                         # 股票代码(纯数字)
STOCK_NAME = '长飞光纤'
STOCK_QMT  = f'{STOCK_CODE}.SH'              # QMT格式: 代码.市场后缀

# ---- 交易参数 ----
TRADE_LOT_SIZE = 100                           # 每手股数 (1手=100股)
MIN_LOT        = 100                           # 最小交易单位
TIMER_INTERVAL = '1nSecond'                    # 定时器间隔(1秒/次)

# ============================================================================
# 【卖出触发线】 — 开盘价 + ATR × 动态乘数
# ============================================================================
ATR_PERIOD = 14                                # ATR计算周期

# ★ v8/v9/v10核心: BASE根据市场趋势动态调整
SELL_TRIGGER_BASE_BEAR      = 0.40             # 熊市BASE — 触发线=开+ATR×0.20
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55             # 震荡BASE — 与v7默认值一致
SELL_TRIGGER_BASE_WEAK_BULL = 0.70             # ★ v11: 0.65→0.70 弱牛卖飞风险大,更谨慎

DYNAMIC_MULT_MIN = 0.20                        # 乘数下限 (v7=0.35, v8=0.20)
DYNAMIC_MULT_MAX = 1.50                        # 乘数上限

# 日内振幅约束 — 触发线不能超过近期日均振幅的合理倍数
DAILY_RANGE_CAP_ENABLED = True                 # 是否启用振幅上限
DAILY_RANGE_CAP_MULT    = 0.80                 # 触发线 ≤ 开盘×(1+近10日均振幅×0.8)

# ============================================================================
# 【冲高→回落→卖出】
# ============================================================================
PULLBACK_PCT = 0.0010                          # 回落0.10%确认卖出

# ============================================================================
# 【卖出→下跌→买回】
# ============================================================================
BUYBACK_TRIGGER_MULT = 0.15                    # 买回触发=卖价×(1-ATR%×0.15)
BOUNCE_PCT           = 0.0010                  # 回升0.10%确认买回
REBOUND_BUYBACK_PCT  = 0.01                    # ★ v12: 下探至卖价99%后, 回升重穿99%买回

# ★ v8/v9/v10: 卖后动态收紧
BUYBACK_TIGHTEN_MULT = 0.60                    # 收紧系数(0.60=买回目标上移40%)

# ============================================================================
# 【熔断 & 过滤】 — 以下情况禁止反T
# ============================================================================
VOLUME_FILTER_RATIO = 0.4                      # 量比<0.4 → 缩量不交易
RSI_OVERBOUGHT      = 75                       # RSI>75 → 超买不交易
STRONG_BULL_RSI     = 70                        # 强牛判断: RSI必须>70
STRONG_BULL_STREAK  = 5                         # 强牛判断: 必须连涨≥5天

# ============================================================================
# 【紧急买回 & 止损】
# ============================================================================
EMERGENCY_BUYBACK_PCT = 0.03                   # 卖后涨3%→紧急买回

# ★ v11: ATR自适应止损 — 替代固定1.5%
#   高ATR环境(>7%): 放宽止损避免噪声触发
#   低ATR环境(≤6%): 保持1.5%底线不变
#   公式: stop_loss = max(STOP_LOSS_MIN_PCT, ATR% × STOP_LOSS_ATR_MULT)
STOP_LOSS_MIN_PCT   = 0.015                    # 止损底线 (ATR%低时不变)
STOP_LOSS_ATR_MULT  = 0.25                     # ATR缩放系数 (ATR%×0.25=止损%)

# ============================================================================
# 【时间 & 数据 & 费用】
# ============================================================================
FORCE_CLOSE_TIME = '14:57:00'                  # 尾盘强制买回时间
HIST_DATA_LEN    = 80                          # 历史数据长度(日线)
COMMISSION       = 0.00025                     # 佣金率(双边)
STAMP_TAX        = 0.001                       # 印花税(仅卖出)

# ============================================================================
# ★ v10新增: 稳定性参数 (借鉴阈值双向T v4→v8)
# ============================================================================
INIT_COOLDOWN_SEC  = 30.0                      # 初始化重试冷却(秒)
MAX_INIT_ATTEMPTS  = 10                        # 最大初始化尝试次数
HEARTBEAT_SEC      = 60                        # 交易心跳间隔(秒)
DONE_HEARTBEAT_SEC = 300                       # 终态心跳间隔(秒, DONE/FORCED)
RETRY_DELAY_SEC    = 2.0                       # 补单间隔(秒)
MAX_RETRIES        = 2                         # 最大补单次数(COMPETE→MARKET→放弃)
STATE_LOG_COOLDOWN = 60                        # 状态切换日志冷却(秒)

# ============================================================================
# 第二部分：技术指标
# ============================================================================
# 注意: 所有指标函数接收 QMT 原生 list, 返回等长 list
# QMT 通过 ContextInfo.get_history_data() 返回 {股票代码: [值列表]} 字典
# 取值方式: data[STOCK_QMT][-1] = 最新值

import time as _time                            # ★ 模块级import
import traceback as _traceback                  # ★ v10: 异常追踪


def _sma(values, period):
    """简单移动平均 — 返回与输入等长的list, 前period-1位为0"""
    n = len(values)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _atr(highs, lows, closes, period=14):
    """
    平均真实波幅 (Average True Range)
    TR = max(H-L, |H-C_prev|, |L-C_prev|)
    ATR = TR的N周期简单移动平均
    """
    n = len(closes)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
    result = [0.0] * n
    for i in range(period, n):
        result[i] = sum(tr[i - period + 1 : i + 1]) / period
    return result


def _rsi(closes, period=14):
    """
    相对强弱指数 (Relative Strength Index) — Wilder's smoothing
    RSI = 100 - 100/(1 + avg_gain/avg_loss)
    """
    n = len(closes)
    if n < period + 1:
        return [50.0] * n

    rsi = [50.0] * n
    gains = []
    losses = []

    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0

    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0

    return rsi


def _up_streak(closes):
    """连涨天数 — 用于判断趋势强度和动能"""
    n = len(closes)
    streak = [0] * n
    for i in range(1, n):
        streak[i] = streak[i - 1] + 1 if closes[i] > closes[i - 1] else 0
    return streak


def _daily_range_ma(highs, lows, opens, period=10):
    """
    近N日均振幅(%) — 用于日内振幅约束
    触发线不能超过 开盘 × (1 + 均振幅 × 0.8)
    """
    n = len(opens)
    ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0:
            ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)


# ============================================================================
# 第三部分：v8/v9/v10 核心 — 自适应动态乘数模型
# ============================================================================

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    """
    计算动态卖出乘数 — v10核心定价模型 (继承v9, 不变)

    原理:
      触发价 = 开盘价 + ATR × 最终乘数
      最终乘数 = BASE + 4因子偏差之和 (clamp到[MIN, MAX])

    BASE自适应:
      bear=0.40  → 熊市放低网, 积极捕捉反T
      sideways=0.55 → 震荡市默认
      weak_bull=0.65 → 弱牛谨慎, 偏高触发

    4因子(每个±0.25~0.35):
      趋势:     连跌不加仓→-0.25, 弱牛连涨→+0.20
      波动率:   ATR高(>8%)→减乘数, ATR低(<3%)→加乘数
      成交量:   放量→减, 缩量→加
      RSI:      超买→减, 超卖→加

    返回: (最终乘数, 因子明细dict, 使用的BASE值)
    """
    # ── STEP 1: 根据趋势选择BASE ──
    if trend == 'bear':
        base = SELL_TRIGGER_BASE_BEAR
    elif trend == 'weak_bull':
        base = SELL_TRIGGER_BASE_WEAK_BULL
    else:
        base = SELL_TRIGGER_BASE_SIDEWAYS

    deviations = {}
    total_deviation = 0.0

    # ── STEP 2: 因子1 — 趋势强度 (±0.25) ──
    if trend == 'bear':
        d = -0.25 if up_streak == 0 else -0.15
    elif trend == 'strong_bull':
        d = +999                                # 安全兜底(会被clamp)
    elif trend == 'weak_bull':
        if up_streak >= 3:
            d = +0.20
        elif up_streak >= 1:
            d = +0.12
        else:
            d = +0.05
    else:  # sideways
        d = 0.00
    deviations['趋势'] = d
    total_deviation += d

    # ── STEP 3: 因子2 — 波动率综合 (±0.35) ──
    if atr_pct > 0.08:
        atr_d = -0.30
    elif atr_pct > 0.07:
        atr_d = -0.22
    elif atr_pct > 0.06:
        atr_d = -0.15
    elif atr_pct > 0.05:
        atr_d = -0.08
    elif atr_pct > 0.03:
        atr_d = +0.05
    elif atr_pct > 0.02:
        atr_d = +0.15
    else:
        atr_d = +0.25

    # ATR变化率(当前ATR / 20日均ATR)
    if atr_ratio > 1.50:
        atrd_d = -0.25
    elif atr_ratio > 1.25:
        atrd_d = -0.18
    elif atr_ratio > 1.10:
        atrd_d = -0.10
    elif atr_ratio > 0.90:
        atrd_d = 0.00
    elif atr_ratio > 0.70:
        atrd_d = +0.12
    elif atr_ratio > 0.50:
        atrd_d = +0.20
    else:
        atrd_d = +0.25

    vol_d = atr_d * 0.55 + atrd_d * 0.45
    vol_d = max(-0.35, min(0.30, vol_d))
    deviations['波动率'] = round(vol_d, 2)
    total_deviation += vol_d

    # ── STEP 4: 因子3 — 成交量(流动性) (±0.25) ──
    if vol_ratio > 2.00:
        d = -0.25
    elif vol_ratio > 1.50:
        d = -0.18
    elif vol_ratio > 1.20:
        d = -0.08
    elif vol_ratio > 0.80:
        d = 0.00
    elif vol_ratio > 0.60:
        d = +0.12
    elif vol_ratio > 0.40:
        d = +0.20
    else:
        d = +0.25
    deviations['成交量'] = d
    total_deviation += d

    # ── STEP 5: 因子4 — RSI (±0.25) ──
    if rsi_val > 80:
        d = -0.25
    elif rsi_val > 70:
        d = -0.18
    elif rsi_val > 60:
        d = -0.08
    elif rsi_val > 55:
        d = -0.03
    elif rsi_val > 45:
        d = 0.00
    elif rsi_val > 40:
        d = +0.03
    elif rsi_val > 30:
        d = +0.10
    elif rsi_val > 20:
        d = +0.20
    else:
        d = +0.25
    deviations['RSI'] = d
    total_deviation += d

    # ── STEP 6: 合成最终乘数 ──
    final = base + total_deviation
    final = max(DYNAMIC_MULT_MIN, min(DYNAMIC_MULT_MAX, final))
    return round(final, 2), deviations, base


# ============================================================================
# 第四部分：信号计算
# ============================================================================

def compute_signal(opens, highs, lows, closes, volumes):
    """
    计算当日反T信号 — 每日handlebar调用一次

    输入: 历史OHLCV list (QMT原生Python list)
    返回: dict
    """
    n = len(closes)
    if n < 60:
        return None

    co = opens[-1]                               # 今日开盘价
    cc = closes[-1]                              # 昨日收盘价
    cv = volumes[-1]                             # 昨日成交量

    # ── ATR计算 ──
    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    # ── 趋势判断 ──
    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]

    curr_rsi = _rsi(closes)[-1]
    up_streak = _up_streak(closes)[-1]

    price_above_ma = (cc > ma20) and (ma5 > ma20)
    price_below_ma = (cc < ma20) and (ma5 < ma20)

    if price_above_ma and curr_rsi > STRONG_BULL_RSI and up_streak >= STRONG_BULL_STREAK:
        trend = 'strong_bull'
    elif price_above_ma:
        trend = 'weak_bull'
    elif price_below_ma:
        trend = 'bear'
    else:
        trend = 'sideways'

    # ── 量比 ──
    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # ── 调用自适应乘数模型 ──
    sell_mult, factor_details, base_used = calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak
    )

    # ── 日内振幅约束 ──
    daily_range_ma10 = _daily_range_ma(highs, lows, opens, 10)[-1]
    max_trigger_by_range = co * (1.0 + daily_range_ma10 * DAILY_RANGE_CAP_MULT)
    range_capped = False

    sell_trigger_raw = co + curr_atr * sell_mult

    if DAILY_RANGE_CAP_ENABLED and sell_trigger_raw > max_trigger_by_range:
        sell_trigger = round(max_trigger_by_range, 2)
        range_capped = True
    else:
        sell_trigger = round(sell_trigger_raw, 2)

    # ── 熔断检查 ──
    do_short = True
    reason = ''

    if trend == 'strong_bull':
        do_short = False
        reason = '强牛禁反T(连涨>=5+RSI>70)'
    elif curr_vr < VOLUME_FILTER_RATIO:
        do_short = False
        reason = f'缩量(量比{curr_vr:.2f})'
    elif curr_rsi > RSI_OVERBOUGHT:
        do_short = False
        reason = f'RSI超买({curr_rsi:.0f})'

    return {
        'do_short':           do_short,
        'blocked_reason':     reason,
        'trend':              trend,
        'sell_trigger':       sell_trigger,
        'sell_trigger_raw':   round(sell_trigger_raw, 2),
        'range_capped':       range_capped,
        'daily_range_ma10':   daily_range_ma10,
        'open_price':         co,
        'close_yday':         cc,
        'atr':                curr_atr,
        'atr_pct':            curr_atr_pct,
        'rsi':                curr_rsi,
        'vol_ratio':          curr_vr,
        'sell_mult':          sell_mult,
        'sell_mult_base':     base_used,
        'factor_details':     factor_details,
        'atr_ratio':          atr_ratio,
        'up_streak':          up_streak,
        'buyback_mult':       BUYBACK_TRIGGER_MULT,
        'bounce_pct':         BOUNCE_PCT,
    }


# ============================================================================
# 第五部分：QMT 策略入口
# ============================================================================

# ── 状态机常量 ──
STATE_IDLE    = 'IDLE'       # 空闲, 等待价格触及触发线
STATE_SPIKING = 'SPIKING'    # 冲高监控, 价格已过触发线, 跟踪最高价等回落
STATE_SOLD    = 'SOLD'       # 已卖出, 等待价格下跌到买回触发线
STATE_DIPPING = 'DIPPING'    # 下跌监控, 价格已到买回区, 跟踪最低价等回升
STATE_DONE    = 'DONE'       # 当日交易已完成(已买回)
STATE_FORCED  = 'FORCED'     # 强制买回(尾盘/止损)

# ============================================================================
# init — QMT策略初始化 (加载时调用一次)
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
        # 每日信号 (handlebar计算)
        'daily_signal': None,

        # 持仓信息
        'base_shares': 0,                        # 当前持仓股数
        'base_can_use': 0,                       # ★ v10: 当日可卖(T+0可用)
        'base_cost':   0.0,                      # 当前持仓成本

        # 状态机
        'fstate':       STATE_IDLE,
        'peak_price':   0.0,                     # SPIKING: 日内最高价
        'dip_price':    0.0,                     # DIPPING: 日内最低价

        # 交易记录
        'sell_fill_price':    0.0,
        'buyback_target':     0.0,
        'buyback_target_pct': 0.0,
        'rebound_99_armed':    False,           # ★ v12: 是否已先下探触及卖价99%
        'rebound_99_last_price': 0.0,           # ★ v12: 用于确认从下向上重穿99%线
        'rebound_99_triggered': False,          # ★ v12: 防止99%回升机制重复下单

        # 盈亏跟踪
        'day_pnl':       0.0,
        'stop_loss_hit': False,
        'total_t_days':  0,
        'total_pnl':     0.0,                    # 累计毛利(未扣佣金, 近似值)

        # 成本基准
        'entry_price': 0.0,

        # ★ v10: 日期与初始化守卫 (借鉴阈值双向T v7)
        'trade_date':       '',                  # 当前交易日(跨日检测)
        '_guard_date':      '',                  # 日期锁, 防止同日多次reset
        'initialized':      False,               # handlebar已完成今日初始化?
        'init_attempts':    0,                   # 初始化尝试次数
        'last_init_time':   0.0,                 # 上次初始化时间戳
        'startup_printed':  False,
        '_startup_guard':   '',                  # 启动打印锁(每日一次)

        # 时间追踪
        'state_enter_time': '',

        # ★ v10: 心跳与日志控制 (借鉴阈值双向T v7/v8)
        'last_heartbeat':        0.0,
        'last_fstate':           '',
        '_last_logged_transition': '',           # 状态日志去重
        'ontimer_errors':        0,
        'callback_errors':       0,

        # ★ v10: 补单追踪 (借鉴阈值双向T v3/v8)
        'order_pending':       False,
        'order_side':          '',               # '卖出' / '买回'
        'order_signal_price':  0.0,
        'order_sent_at':       0.0,
        'order_retries':       0,
        'order_retry_logged':  False,

        # 卖后计时器(用于动态收紧)
        'sell_elapsed_bars': 0,
    }
    ContextInfo.st = state

    today_str = _time.strftime('%Y-%m-%d')
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, f"{today_str} 09:00:00", "SH")


# ============================================================================
# handlebar — 日线触发 (★ v10: 每天只初始化一次, 借鉴阈值双向T v1→v2)
# ============================================================================

def handlebar(ContextInfo):
    """
    QMT日线回调 — ★ v10: 加trade_date锁, 每天仅首次触发时初始化

    阈值双向T演化教训:
      QMT主K线为1分钟时, handlebar每分钟触发且is_last_bar()=True.
      v9无条件重置状态机 → 盘中交易被反复打断 → 状态丢失.
      v10用trade_date锁确保每天只在第一根bar做初始化.

    职责:
      1. 拉取历史数据计算当日信号 (仅一次)
      2. 读取当前持仓和账户状态 (仅一次)
      3. 重置状态机, 准备新交易日 (仅一次)
    """
    st = ContextInfo.st
    today = _time.strftime('%Y%m%d')
    is_live = ContextInfo.is_last_bar()

    # ── ★ v10: 跨日检测 — 新的一天到了才重新初始化 ──
    if st.get('trade_date', '') == today and st.get('initialized', False):
        # 同日已初始化, 只刷新持仓(可能被其他策略修改)
        if is_live:
            _refresh_position(ContextInfo)
        return

    # ── ★ 新交易日: 重置状态, 重新初始化 ──
    if st.get('trade_date', '') and st['trade_date'] != today:
        _log(f'\n[新交易日] {st["trade_date"]} → {today}')

    st['trade_date']  = today
    st['_guard_date'] = today                    # ★ 日期锁, 今天不会再reset
    st['initialized'] = False
    _reset_daily_state(st)

    # ── 拉取历史数据 ──
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')

    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return

    # ── 读取持仓 (含当日可卖股数) ──
    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    accounts  = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')

    base_shares = 0
    base_can_use = 0                              # ★ v10: T+0可卖
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:
            base_shares  = pos.m_nVolume
            base_can_use = getattr(pos, 'm_nCanUseVolume', base_shares)
            base_cost    = pos.m_dOpenPrice
            break

    if base_shares < TRADE_LOT_SIZE:
        if is_live:
            _log(f'[警告] 底仓不足1手({base_shares}股) — 策略等待中')
        st['base_shares']  = 0
        st['base_can_use'] = 0
        return

    st['base_shares']  = base_shares
    st['base_can_use'] = base_can_use
    st['base_cost']    = base_cost

    if st['entry_price'] == 0.0:
        st['entry_price'] = base_cost

    # ── 计算当日信号 ──
    signal = compute_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    st['daily_signal'] = signal

    # ── 重置状态机(新的一天) ──
    st['fstate']             = STATE_IDLE
    st['peak_price']         = 0.0
    st['dip_price']          = 0.0
    st['sell_fill_price']    = 0.0
    st['buyback_target']     = 0.0
    st['buyback_target_pct'] = 0.0
    st['day_pnl']            = 0.0
    st['stop_loss_hit']      = False
    st['state_enter_time']   = _now()
    st['sell_elapsed_bars']  = 0

    st['initialized'] = True                     # ★ 标记初始化完成

    # ── 打印信息 ──
    curr_close  = closes[STOCK_QMT][-1]
    avail_cash  = accounts[0].m_dAvailable if accounts else 0.0
    pos_value   = base_shares * curr_close

    if is_live:
        _print_signal(ContextInfo, curr_close, avail_cash, pos_value)
    elif not st['startup_printed']:
        _print_startup(ContextInfo, base_shares, base_can_use, base_cost)
        st['startup_printed'] = True


def _refresh_position(ContextInfo):
    """盘中刷新持仓(不重置状态机)"""
    st = ContextInfo.st
    try:
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                st['base_shares']  = pos.m_nVolume
                st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', st['base_shares'])
                break
    except Exception:
        pass


def _print_startup(ContextInfo, base_shares, base_can_use, base_cost):
    """打印启动信息(仅历史回放时) — ★ v10: 带_startup_guard"""
    st = ContextInfo.st
    today = st.get('trade_date', '')
    if st.get('_startup_guard', '') == today:
        return
    st['_startup_guard'] = today

    _log(f'{"="*55}')
    _log(f'  {STOCK_NAME} v11.0 ATR止损优化版 — 已加载')
    _log(f'  交易日: {today}')
    _log(f'  BASE: bear={SELL_TRIGGER_BASE_BEAR} / sideways={SELL_TRIGGER_BASE_SIDEWAYS} / weak_bull={SELL_TRIGGER_BASE_WEAK_BULL}')
    _log(f'  乘数范围: [{DYNAMIC_MULT_MIN}, {DYNAMIC_MULT_MAX}]')
    _log(f'  紧急买回: +{EMERGENCY_BUYBACK_PCT*100:.0f}% | 止损: max({STOP_LOSS_MIN_PCT*100:.1f}%, ATR%×{STOP_LOSS_ATR_MULT}) | 尾盘: {FORCE_CLOSE_TIME}')
    _log(f'  动态收紧: 卖后30s不跌→买回目标×{BUYBACK_TIGHTEN_MULT}')
    _log(f'  下单: COMPETE | 补单: {MAX_RETRIES}次 | 心跳: {HEARTBEAT_SEC}s')
    _log(f'  持仓: {base_shares}股(可用{base_can_use}股) | 成本: ￥{base_cost:.2f}')
    _log(f'{"="*55}')
    st['last_heartbeat'] = _time.time()


def _print_signal(ContextInfo, curr_close, avail_cash, pos_value):
    """打印当日信号和账户状态 (仅实时模式) — ★ v10: 带_startup_guard"""
    s  = ContextInfo.st['daily_signal']
    st = ContextInfo.st
    cost = st['entry_price']
    today = st.get('trade_date', '')

    # 启动信息(每天仅一次)
    if st.get('_startup_guard', '') != today:
        st['_startup_guard'] = today
        _log(f'{"="*55}')
        _log(f'  {STOCK_NAME} v10.0 稳健增强版')
        _log(f'  交易日: {today}')
        _log(f'  持仓: {st["base_shares"]}股(可用{st.get("base_can_use",0)}股) | 成本: ￥{st["base_cost"]:.2f}')
        _log(f'  下单: COMPETE | 补单: {MAX_RETRIES}次 | 心跳: {HEARTBEAT_SEC}s')
        _log(f'{"="*55}')
        st['last_heartbeat'] = _time.time()

    # 浮动盈亏
    unreal_pnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_close / cost - 1) * 100 if cost > 0 else 0
    total_val  = pos_value + avail_cash

    _log(f'━━━ {"账户 & 信号":─^25} ━━━')
    _log(f'  持仓: ￥{pos_value:,.0f} | 浮动: ￥{unreal_pnl:,.0f}({unreal_pct:+.1f}%) | 总资产: ￥{total_val:,.0f}')
    _log(f'  开盘: ￥{s["open_price"]:.2f} | ATR: ￥{s["atr"]:.2f}({s["atr_pct"]*100:.1f}%) | 近10日均振幅: {s["daily_range_ma10"]*100:.1f}%')
    _log(f'  趋势: {s["trend"]}(连涨{s["up_streak"]}天) | RSI: {s["rsi"]:.0f} | 量比: {s["vol_ratio"]:.2f}')

    if s['do_short']:
        _log(f'  ┌─ v10自适应乘数 (BASE={s["sell_mult_base"]}) ─')
        for name, dev in s['factor_details'].items():
            if dev != 0:
                _log(f'  │ {name:<6} {dev:+.2f}')
        _log(f'  ├─ 乘数={s["sell_mult"]:.2f} → 触发线 = {s["open_price"]:.2f} + {s["atr"]:.2f}×{s["sell_mult"]:.2f} = ￥{s["sell_trigger_raw"]:.2f}')
        if s['range_capped']:
            _log(f'  │ [振幅约束] ￥{s["sell_trigger_raw"]:.2f} → ￥{s["sell_trigger"]:.2f}')
        _log(f'  │ 买回触发 ≈ 卖价 - {s["atr_pct"]*s["buyback_mult"]*100:.1f}%')
        _log(f'  └{"─"*30}')
    else:
        _log(f'  反T: [禁止] {s["blocked_reason"]}')

    # 正T可行性
    lot_cost = curr_close * TRADE_LOT_SIZE
    if avail_cash >= lot_cost * 1.01:
        _log(f'  正T: [可用] (1手需￥{lot_cost:,.0f})')
    else:
        _log(f'  正T: [不可用] 缺￥{lot_cost - avail_cash:,.0f}')

    if st['total_t_days'] > 0:
        _log(f'  累计反T: {st["total_t_days"]}天 | 毛利≈￥{st["total_pnl"]:,.0f}')


# ============================================================================
# 第六部分：ontimer — 盘中每秒状态机驱动 (★ v10: 全局异常保护)
# ============================================================================

def ontimer(ContextInfo):
    """
    QMT定时器回调 — ★ v10: 全局异常捕获 (借鉴阈值双向T v4)

    状态流转:
      IDLE     → (price >= sell_trigger)        → SPIKING
      SPIKING  → (pullback from peak >= 0.1%)    → SOLD (下单卖出)
      SPIKING  → (price < sell_trigger)          → IDLE (假突破)
      SOLD     → (price >= emergency_line)        → 紧急买回
      SOLD     → (price <= buyback_target)        → DIPPING
      DIPPING  → (bounce from dip >= 0.1%)       → DONE (下单买回)
      DIPPING  → (price > buyback_target)         → SOLD (假跌破)
      任意状态 → (time >= 14:57:00)               → 强制买回/终止
    """
    try:
        _ontimer_impl(ContextInfo)
    except Exception as e:
        st = getattr(ContextInfo, 'st', None)
        err_count = st.get('ontimer_errors', 0) + 1 if st else 1
        if st:
            st['ontimer_errors'] = err_count
        if err_count <= 3:
            _log(f'[!!异常#{err_count}] {e}')
            _log(f'  {_traceback.format_exc()[-200:]}')
        if err_count == 10:
            _log(f'[!!] ontimer已累计{err_count}次异常, 策略可能不稳定')


def _ontimer_impl(ContextInfo):
    """ontimer 实际逻辑"""
    st = ContextInfo.st
    now = _now()
    today = _time.strftime('%Y%m%d')
    now_ts = _time.time()

    # ═══════════════════════════════════════════════════════════
    # 1. ★ v10: 跨日检测(后备 — handlebar应该已经处理, 这里是双保险)
    # ═══════════════════════════════════════════════════════════
    if st.get('trade_date', '') != today and st.get('_guard_date', '') != today:
        if st.get('trade_date', ''):
            _log(f'\n[新交易日-ontimer] {st["trade_date"]} → {today}')
        st['trade_date']  = today
        st['_guard_date'] = today
        st['initialized'] = False
        _reset_daily_state(st)

    # ═══════════════════════════════════════════════════════════
    # 2. 等待handlebar初始化完成
    # ═══════════════════════════════════════════════════════════
    if not st.get('initialized', False):
        return

    # ═══════════════════════════════════════════════════════════
    # 3. 交易时段检查
    # ═══════════════════════════════════════════════════════════
    if not _is_market_open(now):
        return

    # ═══════════════════════════════════════════════════════════
    # 4. ★ v10: 补单检查 (优先级最高 — 借鉴阈值双向T v3)
    # ═══════════════════════════════════════════════════════════
    if st.get('order_pending', False):
        _check_and_retry(ContextInfo)

    # ═══════════════════════════════════════════════════════════
    # 5. ★ v10: 终态检查 (DONE/FORCED时也发心跳 — 借鉴阈值双向T v8)
    # ═══════════════════════════════════════════════════════════
    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        if now_ts - st.get('last_heartbeat', 0) >= DONE_HEARTBEAT_SEC:
            st['last_heartbeat'] = now_ts
            _log(f'[心跳] {st["fstate"]} | 策略存活, 等待下一交易日')
        return

    # ── 前置检查 ──
    signal = st.get('daily_signal')
    if signal is None or not signal.get('do_short'):
        return

    if st.get('base_shares', 0) < TRADE_LOT_SIZE:
        return

    # ═══════════════════════════════════════════════════════════
    # 6. 获取实时价格 (整个ontimer只调一次)
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
    # 7. ★ v10: 心跳日志 (使用刚获取的实时价 — 借鉴阈值双向T v7)
    # ═══════════════════════════════════════════════════════════
    if now_ts - st.get('last_heartbeat', 0) >= HEARTBEAT_SEC:
        st['last_heartbeat'] = now_ts
        fstate = st.get('fstate', '?')
        extra = ''
        if fstate == STATE_SPIKING:
            extra = f' | peak=￥{st.get("peak_price", 0):.2f}'
        elif fstate in (STATE_SOLD, STATE_DIPPING):
            sp = st.get('sell_fill_price', 0)
            chg = (price - sp) / sp * 100 if sp > 0 else 0
            extra = f' | 卖￥{sp:.2f} {chg:+.2f}%'
        _log(f'[心跳] {fstate} | ￥{price:.2f}{extra}')

    # ═══════════════════════════════════════════════════════════
    # 8. ★ v10: 状态变化日志去重 (借鉴阈值双向T v7)
    # ═══════════════════════════════════════════════════════════
    fstate = st['fstate']
    last_fs = st.get('last_fstate', '')
    if fstate != last_fs:
        transition = f'{last_fs or "启动"}→{fstate}'
        if transition != st.get('_last_logged_transition', ''):
            _log(f'>>> 状态: {transition} | ￥{price:.2f}')
            st['_last_logged_transition'] = transition
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
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price)

    # ═══════════════════════════════════════════════════════════
    # 10. 卖后计时 (用于动态收紧)
    # ═══════════════════════════════════════════════════════════
    if st['fstate'] in (STATE_SOLD, STATE_DIPPING):
        st['sell_elapsed_bars'] += 1

    # ═══════════════════════════════════════════════════════════
    # 11. 尾盘强制处理
    # ═══════════════════════════════════════════════════════════
    if now >= FORCE_CLOSE_TIME:
        f = st['fstate']
        if f in (STATE_SOLD, STATE_DIPPING):
            _log(f'[尾盘] {now} 强制买回')
            _force_buyback(ContextInfo)
        elif f in (STATE_SPIKING, STATE_IDLE):
            st['fstate'] = STATE_DONE

    # ═══════════════════════════════════════════════════════════
    # 12. 止损检查 (仅在SOLD状态)
    # ═══════════════════════════════════════════════════════════
    # ★ v11: ATR自适应止损 — 高ATR时放宽, 低ATR时保持底线
    #   stop_pct = max(1.5%, ATR% × 0.25)
    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        atr_pct_signal = signal.get('atr_pct', 0.05)
        stop_pct = max(STOP_LOSS_MIN_PCT, atr_pct_signal * STOP_LOSS_ATR_MULT)
        loss_limit = st['base_shares'] * signal['open_price'] * stop_pct
        if st['day_pnl'] < -loss_limit:
            _log(f'[止损] 亏损￥{st["day_pnl"]:.0f} 超过￥{loss_limit:.0f}(ATR={atr_pct_signal*100:.1f}% 止损={stop_pct*100:.2f}%)')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 状态处理函数
# ============================================================================

def _handle_idle(ContextInfo, price):
    """
    IDLE状态: 等待价格触及触发线
    ★ v10: 先检查可用持仓(借鉴阈值双向T v8)
    """
    st = ContextInfo.st
    trigger = st['daily_signal']['sell_trigger']

    if price >= trigger:
        # ★ v10: 卖出前检查可用股数 (m_nCanUseVolume)
        can_use = st.get('base_can_use', st['base_shares'])
        if can_use < TRADE_LOT_SIZE:
            _log(f'[卖出跳过] 可用{can_use}股 < 1手(T+1锁定{st["base_shares"] - can_use}股)')
            return

        st['fstate']            = STATE_SPIKING
        st['peak_price']        = price
        st['state_enter_time']  = _now()
        _log(f'[冲高] ￥{price:.2f} >= ￥{trigger:.2f} (BASE={st["daily_signal"]["sell_mult_base"]}, 乘数={st["daily_signal"]["sell_mult"]}) → 进入冲高监控')


def _handle_spiking(ContextInfo, price):
    """
    SPIKING状态: 价格已过触发线, 跟踪最高价等回落确认

    卖出条件: price从最高点回落 >= PULLBACK_PCT (0.1%)
    假突破:   price跌回触发线以下 → 回到IDLE
    ★ v10: 先标记状态再下单 (防重入 — 借鉴阈值双向T v7)
    """
    st = ContextInfo.st
    trigger = st['daily_signal']['sell_trigger']

    if price > st['peak_price']:
        st['peak_price'] = price

    peak = st['peak_price']
    pullback = (peak - price) / peak

    # ── 回落确认 → 卖出 ──
    if pullback >= PULLBACK_PCT:
        _log(f'[卖出] 最高￥{peak:.2f} 回落{pullback*100:.2f}% → ￥{price:.2f} 确认卖出')

        # ★ v10: 先计算买回参数, 再标记状态, 最后下单 (借鉴阈值双向T v7)
        atr_pct = st['daily_signal']['atr_pct']
        buyback_pct = atr_pct * st['daily_signal']['buyback_mult']
        buyback_target = round(price * (1.0 - buyback_pct), 2)

        st['sell_fill_price']    = price
        st['buyback_target']     = buyback_target
        st['buyback_target_pct'] = buyback_pct * 100
        st['rebound_99_armed']    = False
        st['rebound_99_last_price'] = 0.0
        st['rebound_99_triggered'] = False
        st['sell_elapsed_bars']  = 0

        # 状态切换
        st['fstate']          = STATE_SOLD
        st['state_enter_time'] = _now()

        _log(f'  买回触发线: ￥{buyback_target:.2f} (卖价-{buyback_pct*100:.2f}%)')
        _log(f'  99%回升线: ￥{price*(1-REBOUND_BUYBACK_PCT):.2f} (先触及、未到原买回线、再向上重穿时按卖一价买回)')
        _log(f'  紧急买回线: ￥{price*(1+EMERGENCY_BUYBACK_PCT):.2f} (卖价+{EMERGENCY_BUYBACK_PCT*100:.1f}%)')

        # 最后下单
        _submit_sell(ContextInfo, price)

    # ── 假突破 → 回到IDLE ──
    elif price < trigger:
        _log(f'[假突破] ￥{price:.2f} 跌回触发线下 ￥{trigger:.2f} (最高触及 ￥{peak:.2f})')
        st['fstate']     = STATE_IDLE
        st['peak_price'] = 0.0


def _handle_sold(ContextInfo, price):
    """
    SOLD状态: 已卖出, 等待价格下跌到买回触发线

    四个触发条件 (优先级递减):
      1. 紧急买回: price >= sell_price × (1 + EMERGENCY_BUYBACK_PCT)
      2. 进入DIPPING: price <= buyback_target
      3. ★ v12: 未到buyback_target时, 先触及卖价99%, 再向上重穿99%线
      4. ★ 动态收紧: 30秒后仍不跌, 提高买回触发线
    """
    st = ContextInfo.st
    sp = st['sell_fill_price']
    bt = st['buyback_target']

    # 卖单成交前不启动任何买回判断；成交回报会用真实成交价重设各条买回线。
    if st.get('order_pending', False) and '卖出' in st.get('order_side', ''):
        return

    # ── 条件1: 紧急买回 (卖飞防护) ──
    emergency_line = sp * (1.0 + EMERGENCY_BUYBACK_PCT)
    if price >= emergency_line:
        rise_pct = (price - sp) / sp * 100
        _log(f'[紧急买回] 卖￥{sp:.2f} → 现￥{price:.2f}(+{rise_pct:.2f}%) >= 紧急线￥{emergency_line:.2f}')
        _submit_buyback(ContextInfo, price, '紧急')
        return

    # ── ★ 动态收紧 (v9修复: max取更靠近卖价的买回线) ──
    tightened_bt = bt
    if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
        tightened_bt = sp * (
            1.0 - st['daily_signal']['atr_pct']
            * BUYBACK_TRIGGER_MULT
            * BUYBACK_TIGHTEN_MULT
        )
        tightened_bt = round(max(tightened_bt, bt), 2)

    # ── 条件2: 进入DIPPING ──
    if price <= tightened_bt:
        drop_pct = (sp - price) / sp * 100
        st['fstate']     = STATE_DIPPING
        st['dip_price']  = price
        st['rebound_99_armed'] = False
        st['rebound_99_last_price'] = 0.0
        st['state_enter_time'] = _now()

        tag = '(收紧)' if tightened_bt > bt else ''
        _log(f'[买回触发{tag}] ￥{price:.2f}(-{drop_pct:.2f}%) <= ￥{tightened_bt:.2f} → 进入反弹监控')
        _log(f'  dip=￥{price:.2f} | 回升线=￥{price*(1+BOUNCE_PCT):.2f}(+{BOUNCE_PCT*100:.2f}%)')
        return

    # ── ★ v12: 卖价99%先下探、后回升重穿买回 ──
    # 原买回触发线优先；只有上方条件未满足时才会运行到这里。
    rebound_line = round(sp * (1.0 - REBOUND_BUYBACK_PCT), 2)
    armed = st.get('rebound_99_armed', False)

    if not armed:
        if price <= rebound_line:
            st['rebound_99_armed'] = True
            st['rebound_99_last_price'] = price
            _log(f'[99%条件1] ￥{price:.2f} <= ￥{rebound_line:.2f}，尚未触及原买回线￥{tightened_bt:.2f} → 等待向上重穿')
        return

    previous_price = st.get('rebound_99_last_price', 0.0)
    st['rebound_99_last_price'] = price

    # 必须有向上运动，且从99%线下方/线上重新达到该线，避免横盘重复触发。
    crossed_up = previous_price <= rebound_line and price >= rebound_line and price > previous_price
    if crossed_up and not st.get('rebound_99_triggered', False):
        gross_profit = (sp - price) * TRADE_LOT_SIZE
        _log(f'[99%回升买回] ￥{previous_price:.2f} → ￥{price:.2f}，向上重穿￥{rebound_line:.2f}')
        _log(f'  卖￥{sp:.2f} → 买入信号￥{price:.2f} | 毛利≈￥{gross_profit:.0f} | 使用卖一价')

        submitted = _submit_buyback(ContextInfo, price, '99%回升', 'SALE1', '卖一价')
        if submitted:
            st['rebound_99_triggered'] = True
            st['fstate'] = STATE_DONE
            st['state_enter_time'] = _now()
            st['total_t_days'] += 1
            st['total_pnl'] += gross_profit


def _handle_dipping(ContextInfo, price):
    """
    DIPPING状态: 价格已到买回区域, 等待从最低点回升确认

    三个分支:
      1. 价格继续跌 → 更新最低价
      2. 从最低点回升 >= BOUNCE_PCT → 确认买回!
      3. 价格涨回买回触发线之上 → 假跌破, 回到SOLD
    """
    st = ContextInfo.st
    bt = st['buyback_target']

    if price < st['dip_price']:
        st['dip_price'] = price

    dip = st['dip_price']

    # ★ 防御性检查
    if dip <= 0:
        _log(f'[异常] dip={dip}, 重置为当前价')
        st['dip_price'] = price
        dip = price

    bounce = (price - dip) / dip

    # ── 回升确认 → 买回 ──
    if bounce >= BOUNCE_PCT:
        sell_p = st['sell_fill_price']
        gross_profit = (sell_p - price) * TRADE_LOT_SIZE

        _log(f'[买回] 最低￥{dip:.2f} 回升{bounce*100:.2f}% → ￥{price:.2f}')
        _log(f'  卖￥{sell_p:.2f} → 买￥{price:.2f} | 毛利≈￥{gross_profit:.0f} | 日内PnL≈￥{st["day_pnl"]:.0f}')

        _submit_buyback(ContextInfo, price, '正常')
        st['total_t_days'] += 1
        st['total_pnl']    += gross_profit

    # ── 假跌破 → 回到SOLD ──
    elif price > bt:
        _log(f'[假跌破] ￥{price:.2f} 涨回买回线上 ￥{bt:.2f} (最低触及 ￥{dip:.2f})')
        st['fstate']     = STATE_SOLD
        st['dip_price']  = 0.0
        st['state_enter_time'] = _now()


# ============================================================================
# 第七部分：下单函数 (★ v10: COMPETE对手价 + 补单机制)
# ============================================================================

def _submit_order(ContextInfo, shares, price, side_label, order_style='COMPETE', style_name='对手价'):
    """
    通用下单 — ★ v10: COMPETE对手价 + 持仓校验 + 价格校验 (借鉴阈值双向T v3/v8)

    卖出方向: 检查 m_nCanUseVolume (当日可卖, 排T+1锁定)
    价格校验: 偏离开盘价±15%则拦截
    防重复: 已有挂单时拒绝新单
    """
    st = ContextInfo.st

    # ── 防重复下单 ──
    if st.get('order_pending', False):
        existing = st.get('order_side', '?')
        _log(f'  [下单拦截] 已有{existing}挂单未成交, 跳过{side_label}')
        return False

    # ── ★ v10: 卖出方向 → 检查可用股数 ──
    if shares < 0:
        try:
            positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
            avail = 0
            total = 0
            for pos in positions:
                if pos.m_strInstrumentID == STOCK_CODE:
                    total = pos.m_nVolume
                    avail = getattr(pos, 'm_nCanUseVolume', total)
                    break
            if avail < abs(shares):
                t1_locked = total - avail
                _log(f'  [下单拦截] 可用{avail}股 < {abs(shares)}股(T+1锁定{t1_locked}股), 跳过{side_label}')
                st['fstate'] = STATE_IDLE
                return False
        except Exception:
            pass  # 取不到持仓时不拦截, 让柜台做最终校验

    # ── ★ v10: 价格合理性校验 ──
    if price <= 0:
        _log(f'  [下单拦截] 价格￥{price:.2f}异常, 跳过')
        return False
    ref = st.get('daily_signal', {}).get('open_price', 0)
    if ref > 0 and (price < ref * 0.85 or price > ref * 1.15):
        _log(f'  [下单拦截] 价格￥{price:.2f}偏离开盘￥{ref:.2f}超±15%, 跳过')
        return False

    # ── 下单(默认COMPETE对手价；v12的99%回升买回使用SALE1卖一价) ──
    try:
        order_shares(STOCK_QMT, shares, order_style, ContextInfo, _acc(ContextInfo))
        st['order_pending']       = True
        st['order_side']          = side_label
        st['order_signal_price']  = price
        st['order_sent_at']       = _time.time()
        st['order_retries']       = 0
        st['order_retry_logged']  = False
        _log(f'  >>> {side_label} {style_name} × {abs(shares)}股 (信号￥{price:.2f})')
        return True
    except Exception as e:
        _log(f'  >>> {side_label}失败: {e}')
        st['fstate'] = STATE_IDLE
        st['order_pending'] = False
        return False


def _submit_sell(ContextInfo, price):
    """卖出下单"""
    _submit_order(ContextInfo, -TRADE_LOT_SIZE, price, '卖出')


def _submit_buyback(ContextInfo, price, reason='', order_style='COMPETE', style_name='对手价'):
    """买回下单 — 先检查资金"""
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001
    avail = _cash(ContextInfo)

    if avail < need:
        _log(f'  >>> 买回失败({reason}): 资金不足 (需￥{need:,.0f} > 可用￥{avail:,.0f} 缺￥{need-avail:,.0f})')
        return False

    return _submit_order(
        ContextInfo,
        TRADE_LOT_SIZE,
        price,
        f'买回({reason})',
        order_style,
        style_name,
    )


def _check_and_retry(ContextInfo):
    """
    ★ v10: 补单检查 (借鉴阈值双向T v3/v8)
    下单后超时未成交 → COMPETE补单 → MARKET兜底 → 放弃
    """
    st = ContextInfo.st

    if st.get('order_retry_logged', False):
        return

    elapsed = _time.time() - st.get('order_sent_at', 0)
    if elapsed < RETRY_DELAY_SEC:
        return

    retries = st.get('order_retries', 0)

    if retries >= MAX_RETRIES:
        if not st.get('order_retry_logged'):
            _log(f'[!] 补单{MAX_RETRIES}次未成交, 放弃. 请手动检查持仓!')
            st['order_retry_logged'] = True
            st['order_pending'] = False
        return

    retries += 1
    st['order_retries'] = retries

    side = st.get('order_side', '?')
    signal_price = st.get('order_signal_price', 0.0)

    # 最后一次用MARKET兜底
    if retries >= MAX_RETRIES:
        style, style_name = 'MARKET', '市价单'
    else:
        style, style_name = 'COMPETE', '对手价'

    shares_sign = -TRADE_LOT_SIZE if '卖出' in side else TRADE_LOT_SIZE

    try:
        order_shares(STOCK_QMT, shares_sign, style, ContextInfo, _acc(ContextInfo))
        st['order_sent_at'] = _time.time()
        _log(f'[补单#{retries}] {side} → {style_name} × {abs(shares_sign)}股 (信号￥{signal_price:.2f})')
    except Exception as e:
        _log(f'[补单#{retries}失败!!] {e}')
        st['order_pending'] = False
        st['order_retry_logged'] = True


def _force_buyback(ContextInfo):
    """
    尾盘/止损强制买回 — 使用对手价(COMPETE)市价成交
    """
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[强制买回] 已下单(对手价)')
    except Exception as e:
        _log(f'[强制买回失败!!] {e}')
        st['fstate'] = STATE_FORCED


# ============================================================================
# 第八部分：辅助函数
# ============================================================================

def _cash(ContextInfo):
    """获取可用资金"""
    try:
        acc = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return acc[0].m_dAvailable if acc else 0.0
    except Exception:
        return 0.0


def _acc(ContextInfo):
    """获取交易账户ID"""
    try:
        if hasattr(ContextInfo, 'accID') and ContextInfo.accID:
            return ContextInfo.accID
    except Exception:
        pass
    return ACCOUNT


# ============================================================================
# 第九部分：QMT回调 (★ v10: 修正属性名 — 借鉴阈值双向T v5)
# ============================================================================

def order_callback(ContextInfo, order):
    """
    委托回报 — ★ v10: 正确属性名 + 异常保护

    v9错误: m_dOrderPrice → 正确: m_dLimitPrice
    v9错误: m_nVolumeTotal → 正确: m_nVolumeTotalOriginal
    """
    try:
        status_map = {
            50: '已报', 52: '部成', 53: '全成',
            54: '部撤', 55: '已撤', 56: '废单'
        }
        status = order.m_nOrderStatus
        if status in status_map:
            _log(f'[委托] ￥{order.m_dLimitPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotalOriginal}股 → {status_map[status]}')
            # 废单/已撤 → 立即可补单
            if status in (55, 56):
                ContextInfo.st['order_sent_at'] = 0.0
    except Exception as e:
        _log(f'[委托回调异常] {e}')
        ContextInfo.st['callback_errors'] = ContextInfo.st.get('callback_errors', 0) + 1


def deal_callback(ContextInfo, deal):
    """
    成交回报 — ★ v10: 正确属性名 + 异常保护

    v9错误: m_fCommission, m_fStampTax → 正确: m_dComssion (API拼写如此)
    ★ 最关键: 先清除pending标记, 再处理其他 (终止补单)
    """
    st = ContextInfo.st
    # ★ 先清除pending — 这是最关键的操作
    st['order_pending'] = False
    st['order_retry_logged'] = False

    try:
        direction = '买' if deal.m_nDirection == 1 else '卖'
        amount = deal.m_dPrice * deal.m_nVolume
        fee = getattr(deal, 'm_dComssion', 0)    # ★ API属性名就是单s

        # 反T逻辑: 卖出(先) → day_pnl += 收入; 买入(后) → day_pnl -= 支出
        if deal.m_nDirection == 2:
            st['day_pnl'] += (amount - fee)

            # ★ v12: 以卖出真实成交价为基准，重算原买回线和99%回升线状态。
            sell_price = deal.m_dPrice
            buyback_pct = (
                st.get('daily_signal', {}).get('atr_pct', 0.0)
                * st.get('daily_signal', {}).get('buyback_mult', BUYBACK_TRIGGER_MULT)
            )
            st['sell_fill_price'] = sell_price
            st['buyback_target'] = round(sell_price * (1.0 - buyback_pct), 2)
            st['buyback_target_pct'] = buyback_pct * 100
            st['rebound_99_armed'] = False
            st['rebound_99_last_price'] = 0.0
            st['rebound_99_triggered'] = False

            _log(
                f'[卖出基准更新] 成交￥{sell_price:.2f} | '
                f'原买回线￥{st["buyback_target"]:.2f} | '
                f'99%回升线￥{sell_price*(1-REBOUND_BUYBACK_PCT):.2f}'
            )
        else:
            st['day_pnl'] -= (amount + fee)

        _log(f'[成交] {direction} ￥{deal.m_dPrice:.2f}×{deal.m_nVolume} | 当日PnL≈￥{st["day_pnl"]:.0f}')
    except Exception as e:
        _log(f'[成交回调异常] {e}')
        st['callback_errors'] = st.get('callback_errors', 0) + 1


def stop(ContextInfo):
    """策略停止回调"""
    st = getattr(ContextInfo, 'st', None)
    if st:
        errors = st.get('ontimer_errors', 0)
        cb_errors = st.get('callback_errors', 0)
        _log(f'\n{"="*55}')
        _log(f'  {STOCK_NAME} v12.0 停止 | 累计 {st.get("total_t_days", 0)}天 | 毛利≈￥{st.get("total_pnl", 0):,.0f}')
        if errors > 0:
            _log(f'  ontimer异常: {errors}次')
        if cb_errors > 0:
            _log(f'  回调异常: {cb_errors}次')
        if st.get('order_pending', False):
            _log(f'  [警告] 有未成交委托! 请手动检查!')
        if st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
            _log(f'  [警告] 策略停止时有未买回头寸! 请手动检查持仓!')
        _log(f'{"="*55}')


# ============================================================================
# 第十部分：工具函数
# ============================================================================

def _now():
    """当前时间 HH:MM:SS"""
    return _time.strftime('%H:%M:%S')


def _ts():
    """带方括号的时间戳 [HH:MM:SS]"""
    return _time.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    """统一日志输出 — 带时间戳"""
    ts = _ts()
    if args:
        print(f'{ts} {args[0]}', *args[1:], **kwargs)
    else:
        print(**kwargs)


def _is_market_open(now):
    """
    判断是否在A股连续竞价时段
    时间格式: 'HH:MM:SS', 字符串比较在此格式下是安全的
    """
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')


def _reset_daily_state(st):
    """
    ★ v10: 新交易日重置日内状态 (借鉴阈值双向T v7)
    保留: _guard_date, base_shares, base_cost, entry_price, total_*
    """
    guard = st.get('_guard_date', '')
    st['daily_signal']         = None
    st['fstate']               = STATE_IDLE
    st['peak_price']           = 0.0
    st['dip_price']            = 0.0
    st['sell_fill_price']      = 0.0
    st['buyback_target']       = 0.0
    st['buyback_target_pct']   = 0.0
    st['rebound_99_armed']     = False
    st['rebound_99_last_price'] = 0.0
    st['rebound_99_triggered'] = False
    st['day_pnl']              = 0.0
    st['stop_loss_hit']        = False
    st['state_enter_time']     = ''
    st['startup_printed']      = False
    st['_startup_guard']       = ''
    st['last_fstate']          = ''
    st['_last_logged_transition'] = ''
    st['sell_elapsed_bars']    = 0
    st['order_pending']        = False
    st['order_side']           = ''
    st['order_signal_price']   = 0.0
    st['order_sent_at']        = 0.0
    st['order_retries']        = 0
    st['order_retry_logged']   = False
    st['_guard_date']          = guard   # ★ 保留日期锁
