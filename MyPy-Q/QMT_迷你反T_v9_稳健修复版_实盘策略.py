# -*- coding: utf-8 -*-
"""
================================================================================
 QMT 迷你反T策略 v9.0 — v8稳健修复版
================================================================================
 QMT运行时注入函数(IDE报红可忽略):
   get_trade_detail_data() / order_shares()
   ContextInfo.get_history_data() / get_full_tick() / run_time()
   order_callback() / deal_callback()

 【v9.0 — v8代码审查修复】
  v8中发现了2个会导致运行时异常的bug和多个安全隐患, 本版全部修复。

  修复清单:
  ┌────────┬──────────────────────────────────────┬──────────────────────────┐
  │ 严重度  │ v8问题                                │ v9修复                    │
  ├────────┼──────────────────────────────────────┼──────────────────────────┤
  │ ★★★   │ handlebar每根bar都打印配置信息(行344) │ 移到startup_printed块内   │
  │ ★★★   │ _handle_sold动态收紧用min而非max      │ min→max, 收紧逻辑生效     │
  │        │ → 收紧永远不生效                      │                          │
  │ ★★    │ _now()/_ts()每次调用都import time     │ import time改为模块级     │
  │ ★★    │ _handle_dipping: dip==0可能ZeroDiv    │ 添加防御性检查            │
  │ ★★    │ ontimer未检查base_shares>0            │ 添加持仓检查              │
  │ ★★    │ set_universe可能已由外部配置           │ try/except保护            │
  │ ★     │ 部分函数缺少注释                       │ 全面补充中文注释          │
  │ ★     │ total_pnl累计为毛利(非净利)           │ 注释标注, 保持与v5一致     │
  └────────┴──────────────────────────────────────┴──────────────────────────┘

  策略逻辑继承v8不变:
    - BASE: bear=0.40, sideways=0.55, weak_bull=0.65
    - mult_min/max: 0.20/1.50
    - 趋势4级: strong_bull(禁), weak_bull(允/谨慎), bear(允/积极), sideways(默认)
    - 紧急买回: 3%, 动态收紧: 卖后30s不跌则收紧买回目标
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

# ★ v8/v9核心: BASE根据市场趋势动态调整
#   bear(熊市): 放低触发线, 积极捕捉反T机会
#   sideways(震荡): 默认水平
#   weak_bull(弱牛): 偏高触发, 谨慎做反T
#   strong_bull(强牛): 完全禁止反T (在信号层处理)
SELL_TRIGGER_BASE_BEAR      = 0.40             # 熊市BASE — 触发线=开+ATR×0.20
SELL_TRIGGER_BASE_SIDEWAYS  = 0.55             # 震荡BASE — 与v7默认值一致
SELL_TRIGGER_BASE_WEAK_BULL = 0.65             # 弱牛BASE — 偏高, 谨慎

DYNAMIC_MULT_MIN = 0.20                        # 乘数下限 (v7=0.35, v8=0.20)
DYNAMIC_MULT_MAX = 1.50                        # 乘数上限

# 日内振幅约束 — 触发线不能超过近期日均振幅的合理倍数
DAILY_RANGE_CAP_ENABLED = True                 # 是否启用振幅上限
DAILY_RANGE_CAP_MULT    = 0.80                 # 触发线 ≤ 开盘×(1+近10日均振幅×0.8)

# ============================================================================
# 【冲高→回落→卖出】
# 价格触及触发线后进入SPIKING状态, 从最高点回落超过此比例即卖出
# ============================================================================
PULLBACK_PCT = 0.0010                          # 回落0.10%确认卖出

# ============================================================================
# 【卖出→下跌→买回】
# 卖出后等价格跌到 卖价×(1 - ATR%×买回乘数) 后进入DIPPING,
# 再从最低点回升超过此比例即买回
# ============================================================================
BUYBACK_TRIGGER_MULT = 0.15                    # 买回触发=卖价×(1-ATR%×0.15)
BOUNCE_PCT           = 0.0010                  # 回升0.10%确认买回

# ★ v8/v9新增: 卖后动态收紧
# 卖出30秒后如果价格仍在卖价99.5%以上(几乎没跌), 收紧买回目标
# 公式: 收紧目标 = 卖价 × (1 - ATR% × 0.15 × 0.60)
# 效果: 买回触发线更靠近卖价, 减少等待时间, 控制踏空风险
BUYBACK_TIGHTEN_MULT = 0.60                    # 收紧系数(0.60=买回目标上移40%)

# ============================================================================
# 【熔断 & 过滤】 — 以下情况禁止反T
# ============================================================================
VOLUME_FILTER_RATIO = 0.4                      # 量比<0.4 → 缩量不交易
RSI_OVERBOUGHT      = 75                       # RSI>75 → 超买不交易
STRONG_BULL_RSI     = 70                        # 强牛判断: RSI必须>70
STRONG_BULL_STREAK  = 5                         # 强牛判断: 必须连涨≥5天
# strong_bull = (价格>MA20 且 MA5>MA20) AND RSI>70 AND 连涨≥5天

# ============================================================================
# 【紧急买回 & 止损】
# 卖出后价格不跌反涨 → 触发紧急买回(亏手续费+价差)
# ============================================================================
EMERGENCY_BUYBACK_PCT = 0.03                   # 卖后涨3%→紧急买回 (v7=2%)
STOP_LOSS_PCT         = 0.015                  # 亏损超持仓1.5%→强制止损

# ============================================================================
# 【时间 & 数据 & 费用】
# ============================================================================
FORCE_CLOSE_TIME = '14:57:00'                  # 尾盘强制买回时间
HIST_DATA_LEN    = 80                          # 历史数据长度(日线)
COMMISSION       = 0.00025                     # 佣金率(双边)
STAMP_TAX        = 0.001                       # 印花税(仅卖出)

# ============================================================================
# 第二部分：技术指标
# ============================================================================
# 注意: 所有指标函数接收 QMT 原生 list, 返回等长 list
# QMT 通过 ContextInfo.get_history_data() 返回 {股票代码: [值列表]} 字典
# 取值方式: data[STOCK_QMT][-1] = 最新值

import time as _time                            # ★ v9: 模块级import, 避免每次函数调用都import

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
    用于衡量价格波动强度, 是动态乘数模型的核心输入
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
    用于判断超买/超卖, 是动态乘数模型的因子之一
    """
    n = len(closes)
    if n < period + 1:
        return [50.0] * n                       # 数据不足, 返回中性值

    rsi = [50.0] * n
    gains = []
    losses = []

    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)

    # 初始平均值
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss) if avg_loss > 0 else 100.0

    # Wilder's smoothing
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
    防止在低波动日设定过高的触发线(永远触不到)
    """
    n = len(opens)
    ranges = [0.0] * n
    for i in range(n):
        if opens[i] > 0:
            ranges[i] = (highs[i] - lows[i]) / opens[i]
    return _sma(ranges, period)


# ============================================================================
# 第三部分：v8/v9 核心 — 自适应动态乘数模型
# ============================================================================

def calc_dynamic_sell_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    """
    计算动态卖出乘数 — v9核心定价模型

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
        base = SELL_TRIGGER_BASE_BEAR           # 0.40 — 熊市: 放低触发线
    elif trend == 'weak_bull':
        base = SELL_TRIGGER_BASE_WEAK_BULL       # 0.65 — 弱牛: 偏高触发(谨慎)
    else:
        base = SELL_TRIGGER_BASE_SIDEWAYS        # 0.55 — 震荡/强牛(不会用到)

    deviations = {}                              # 记录各因子贡献(日志用)
    total_deviation = 0.0

    # ── STEP 2: 因子1 — 趋势强度 (±0.25) ──
    # 熊市中连跌(up_streak==0)→-0.25(最积极), 连涨反弹→-0.15(略收敛)
    # 弱牛中连涨越多→乘数越高(越谨慎)
    # strong_bull在信号层已被禁止, 此处d=999作为安全兜底(会被clamp到MAX)
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
    # 高波动(ATR%>8%) → 减乘数(等回落空间大, 触发线放低)
    # 低波动(ATR%<3%) → 加乘数(振幅小, 需要更高触发线)
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

    # 合并: ATR绝对水平权重0.55, ATR变化率权重0.45
    vol_d = atr_d * 0.55 + atrd_d * 0.45
    vol_d = max(-0.35, min(0.30, vol_d))        # clamp到合理范围
    deviations['波动率'] = round(vol_d, 2)
    total_deviation += vol_d

    # ── STEP 4: 因子3 — 成交量(流动性) (±0.25) ──
    # 放量(>2倍) → 波动大, 减乘数
    # 缩量(<0.6倍) → 波动小, 加乘数
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
    # RSI>80(极度超买) → -0.25, RSI<20(极度超卖) → +0.25
    # 细化40-60区间: 55-60→-0.03(微偏空), 40-45→+0.03(微偏多)
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
    返回: dict包含:
      - do_short: 是否允许反T
      - sell_trigger: 卖出触发价格
      - trend: 4级趋势分类
      - 各技术指标值(ATR%, RSI, 量比, 连涨天数等)
      - sell_mult: 最终乘数, sell_mult_base: 使用的BASE
    """
    n = len(closes)
    if n < 60:
        return None                              # 数据不足60根K线, 指标不可靠

    # ── 今日基础数据 ──
    co = opens[-1]                               # 今日开盘价
    cc = closes[-1]                              # 昨日收盘价(用于趋势判断)
    cv = volumes[-1]                             # 昨日成交量

    # ── ATR计算 ──
    atr_arr = _atr(highs, lows, closes, ATR_PERIOD)
    curr_atr = atr_arr[-1] or cc * 0.03          # fallback: 昨收×3%
    curr_atr_pct = curr_atr / cc if cc > 0 else 0.03

    # ATR变化率: 当前ATR / 20日均ATR
    atr_ma20 = _sma(atr_arr, 20)[-1] if n >= 20 else curr_atr
    atr_ratio = curr_atr / atr_ma20 if atr_ma20 > 0 else 1.0

    # ── 趋势判断 ──
    ma5  = _sma(closes, 5)[-1]
    ma20 = _sma(closes, 20)[-1]

    curr_rsi = _rsi(closes)[-1]
    up_streak = _up_streak(closes)[-1]

    # ★ v8/v9: 4级趋势分类
    price_above_ma = (cc > ma20) and (ma5 > ma20)   # 多头排列
    price_below_ma = (cc < ma20) and (ma5 < ma20)   # 空头排列

    if price_above_ma and curr_rsi > STRONG_BULL_RSI and up_streak >= STRONG_BULL_STREAK:
        trend = 'strong_bull'                    # 强牛: 趋势+动量共振, 禁止反T
    elif price_above_ma:
        trend = 'weak_bull'                      # 弱牛: 趋势向上但动能不足, 允许反T(谨慎)
    elif price_below_ma:
        trend = 'bear'                           # 熊市: 趋势向下, 积极做反T
    else:
        trend = 'sideways'                       # 震荡: MA5和MA20方向不一致

    # ── 量比 ──
    ma20_vol = _sma(volumes, 20)
    curr_vr = cv / ma20_vol[-1] if ma20_vol[-1] > 0 else 1.0

    # ── ★ v8/v9: 调用自适应乘数模型 ──
    sell_mult, factor_details, base_used = calc_dynamic_sell_mult(
        trend, curr_atr_pct, atr_ratio, curr_vr, curr_rsi, up_streak
    )

    # ── 日内振幅约束 ──
    daily_range_ma10 = _daily_range_ma(highs, lows, opens, 10)[-1]
    max_trigger_by_range = co * (1.0 + daily_range_ma10 * DAILY_RANGE_CAP_MULT)
    range_capped = False

    # 原始触发线 = 开盘 + ATR × 乘数
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
    """
    QMT策略初始化 — 设置股票池、账户、定时器

    QMT调用时机: 策略加载/启动时调用一次
    注意: set_universe 如果有外部配置(UI中已设定), 这里可能重复,
         用 try/except 保护以避免启动失败
    """
    # 设定股票池 (如果已由QMT界面配置则可能失败, 但不影响运行)
    try:
        ContextInfo.set_universe([STOCK_QMT])
    except Exception:
        pass  # 忽略: 股票池可能已由QMT界面配置

    # 设定交易账户
    try:
        ContextInfo.set_account(ACCOUNT)
    except Exception:
        pass  # 忽略: 账户可能已由QMT界面配置

    # 初始化策略状态字典 — 所有跨bar/跨tick的变量都存在ContextInfo.st中
    # QMT保证ContextInfo.st在策略生命周期内持久化
    state = {
        # 每日信号
        'daily_signal': None,

        # 持仓信息
        'base_shares': 0,                        # 当前持仓股数
        'base_cost':   0.0,                      # 当前持仓成本

        # 状态机
        'fstate':       STATE_IDLE,              # 当前状态
        'peak_price':   0.0,                     # SPIKING: 日内最高价
        'dip_price':    0.0,                     # DIPPING: 日内最低价

        # 交易记录
        'sell_fill_price':    0.0,               # 实际卖出成交价
        'buyback_target':     0.0,               # 买回触发价格
        'buyback_target_pct': 0.0,               # 买回触发百分比

        # 盈亏跟踪
        'day_pnl':       0.0,                    # 当日累计盈亏(由deal_callback更新)
        'stop_loss_hit': False,                  # 是否已触发止损
        'total_t_days':  0,                      # 累计反T天数
        'total_pnl':     0.0,                    # 累计毛利(★注意: 未扣佣金, 近似值)

        # 成本基准
        'entry_price': 0.0,                      # 初始持仓成本(用于计算浮动盈亏)

        # 日志控制
        'startup_printed': False,                # 是否已打印启动信息(仅历史回放时打印一次)

        # 时间追踪
        'state_enter_time': '',                  # 进入当前状态的时间(HH:MM:SS)

        # ★ v8/v9新增: 卖后计时器
        'sell_elapsed_bars': 0,                  # 卖出后经过的ontimer周期数(≈秒数)
    }
    ContextInfo.st = state

    # 注册每秒定时器 — 盘中每秒调用ontimer()驱动状态机
    # 参数: (函数名, 间隔, 起始时间, 市场)
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")


# ============================================================================
# handlebar — 日线触发 (每个交易日开始时调用, + 历史K线回放)
# ============================================================================

def handlebar(ContextInfo):
    """
    QMT日线回调 — 每根K线触发一次

    职责:
      1. 拉取历史数据计算当日信号
      2. 读取当前持仓和账户状态
      3. 重置状态机, 准备新交易日
      4. 打印信号和状态日志

    QMT注意事项:
      - 历史回放时 is_last_bar()=False (回放80根历史K线)
      - 实时运行时 is_last_bar()=True (当前最新bar)
      - handlebar在ontimer之前触发, 所以状态机重置安全
    """
    st = ContextInfo.st
    is_live = ContextInfo.is_last_bar()          # True=实时, False=历史回放

    # ── 拉取历史数据 ──
    # QMT API: get_history_data(长度, 周期, 字段)
    # 返回: {股票代码: [值列表]} 字典
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')

    # 数据不足 → 不执行
    if STOCK_QMT not in closes or len(closes[STOCK_QMT]) < 60:
        return

    # ── 读取持仓 ──
    # QMT API: get_trade_detail_data(账户, 品种, 类型)
    # POSITION返回list, 每个元素有属性: m_strInstrumentID, m_nVolume, m_dOpenPrice
    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    accounts  = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')

    base_shares = 0
    base_cost   = 0.0
    for pos in positions:
        if pos.m_strInstrumentID == STOCK_CODE:  # 注意: 是纯代码(不含.SH)
            base_shares = pos.m_nVolume
            base_cost   = pos.m_dOpenPrice
            break

    # 底仓不足1手 → 暂停策略
    if base_shares < TRADE_LOT_SIZE:
        if is_live:
            _log(f'[警告] 底仓不足1手({base_shares}股) — 策略等待中')
        st['base_shares'] = 0
        return

    st['base_shares'] = base_shares
    st['base_cost']   = base_cost

    # 首次运行时记录入场成本(用于后续浮动盈亏计算)
    if st['entry_price'] == 0.0:
        st['entry_price'] = base_cost

    # ── 当前状态 ──
    curr_close  = closes[STOCK_QMT][-1]
    avail_cash  = accounts[0].m_dAvailable if accounts else 0.0
    pos_value   = base_shares * curr_close
    total_value = pos_value + avail_cash

    # ── 计算当日信号 ──
    signal = compute_signal(
        opens[STOCK_QMT], highs[STOCK_QMT],
        lows[STOCK_QMT], closes[STOCK_QMT], volumes[STOCK_QMT]
    )
    if signal is None:
        return

    st['daily_signal'] = signal

    # ── ★ 新交易日: 重置状态机 ──
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

    # ── 打印信号 (仅实时) ──
    if is_live:
        _print_signal(ContextInfo, curr_close, avail_cash, pos_value)
    elif not st['startup_printed']:
        # ★ v9修复: 启动信息只在历史回放的第一根bar打印一次
        _log(f'{"="*55}')
        _log(f'  {STOCK_NAME} v9.0 稳健修复版 — 已加载')
        _log(f'  BASE: bear={SELL_TRIGGER_BASE_BEAR} / sideways={SELL_TRIGGER_BASE_SIDEWAYS} / weak_bull={SELL_TRIGGER_BASE_WEAK_BULL}')
        _log(f'  乘数范围: [{DYNAMIC_MULT_MIN}, {DYNAMIC_MULT_MAX}]')
        _log(f'  紧急买回: +{EMERGENCY_BUYBACK_PCT*100:.0f}% | 止损: {STOP_LOSS_PCT*100:.1f}% | 尾盘: {FORCE_CLOSE_TIME}')
        _log(f'  动态收紧: 卖后30s不跌→买回目标×{BUYBACK_TIGHTEN_MULT}')
        _log(f'{"="*55}')
        st['startup_printed'] = True


def _print_signal(ContextInfo, curr_close, avail_cash, pos_value):
    """打印当日信号和账户状态 (仅实时模式)"""
    s  = ContextInfo.st['daily_signal']
    st = ContextInfo.st
    cost = st['entry_price']

    # 浮动盈亏
    unreal_pnl = (curr_close - cost) * st['base_shares'] if cost > 0 else 0
    unreal_pct = (curr_close / cost - 1) * 100 if cost > 0 else 0
    total_val  = pos_value + avail_cash

    _log(f'━━━ {"账户 & 信号":─^25} ━━━')
    _log(f'  持仓: ¥{pos_value:,.0f} | 浮动: ¥{unreal_pnl:,.0f}({unreal_pct:+.1f}%) | 总资产: ¥{total_val:,.0f}')
    _log(f'  开盘: ¥{s["open_price"]:.2f} | ATR: ¥{s["atr"]:.2f}({s["atr_pct"]*100:.1f}%) | 近10日均振幅: {s["daily_range_ma10"]*100:.1f}%')
    _log(f'  趋势: {s["trend"]}(连涨{s["up_streak"]}天) | RSI: {s["rsi"]:.0f} | 量比: {s["vol_ratio"]:.2f}')

    if s['do_short']:
        _log(f'  ┌─ v9自适应乘数 (BASE={s["sell_mult_base"]}) ─')
        for name, dev in s['factor_details'].items():
            if dev != 0:
                _log(f'  │ {name:<6} {dev:+.2f}')
        _log(f'  ├─ 乘数={s["sell_mult"]:.2f} → 触发线 = {s["open_price"]:.2f} + {s["atr"]:.2f}×{s["sell_mult"]:.2f} = ¥{s["sell_trigger_raw"]:.2f}')
        if s['range_capped']:
            _log(f'  │ [振幅约束] ¥{s["sell_trigger_raw"]:.2f} → ¥{s["sell_trigger"]:.2f}')
        _log(f'  │ 买回触发 ≈ 卖价 - {s["atr_pct"]*s["buyback_mult"]*100:.1f}%')
        _log(f'  └{"─"*30}')
    else:
        _log(f'  反T: [禁止] {s["blocked_reason"]}')

    # 正T可行性
    lot_cost = curr_close * TRADE_LOT_SIZE
    if avail_cash >= lot_cost * 1.01:
        _log(f'  正T: [可用] (1手需¥{lot_cost:,.0f})')
    else:
        _log(f'  正T: [不可用] 缺¥{lot_cost - avail_cash:,.0f}')

    if st['total_t_days'] > 0:
        _log(f'  累计反T: {st["total_t_days"]}天 | 毛利≈¥{st["total_pnl"]:,.0f}')


# ============================================================================
# 第六部分：ontimer — 盘中每秒状态机驱动
# ============================================================================

def ontimer(ContextInfo):
    """
    QMT定时器回调 — 盘中每秒执行一次, 驱动状态机

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
    st = ContextInfo.st

    # ── 前置检查 ──
    signal = st.get('daily_signal')
    if signal is None or not signal.get('do_short'):
        return                                   # 无信号或信号被禁止

    # ★ v9新增: 底仓检查
    if st.get('base_shares', 0) < TRADE_LOT_SIZE:
        return                                   # 没有底仓, 无法卖出

    now = _now()
    if not _is_market_open(now):
        return                                   # 非交易时间

    if st['fstate'] in (STATE_DONE, STATE_FORCED):
        return                                   # 当日已完成交易

    # ── 获取实时价格 ──
    # QMT API: get_full_tick([股票代码])
    # 返回: {股票代码: {lastPrice, bid1-5, ask1-5, ...}}
    try:
        tick = ContextInfo.get_full_tick([STOCK_QMT])
    except Exception:
        return                                   # 获取行情失败, 跳过本tick

    if STOCK_QMT not in tick:
        return

    price = tick[STOCK_QMT].get('lastPrice', 0)
    if price <= 0:
        return                                   # 无效价格

    fstate = st['fstate']

    # ── 状态路由 ──
    if fstate == STATE_IDLE:
        _handle_idle(ContextInfo, price)
    elif fstate == STATE_SPIKING:
        _handle_spiking(ContextInfo, price)
    elif fstate == STATE_SOLD:
        _handle_sold(ContextInfo, price)
    elif fstate == STATE_DIPPING:
        _handle_dipping(ContextInfo, price)

    # ── 卖后计时 (用于动态收紧) ──
    if st['fstate'] in (STATE_SOLD, STATE_DIPPING):
        st['sell_elapsed_bars'] += 1

    # ── 尾盘强制处理 ──
    if now >= FORCE_CLOSE_TIME:
        if fstate in (STATE_SOLD, STATE_DIPPING):
            _log(f'[尾盘] {now} 强制买回')
            _force_buyback(ContextInfo)
        elif fstate in (STATE_SPIKING, STATE_IDLE):
            st['fstate'] = STATE_DONE            # 未触发的直接结束

    # ── 止损检查 (仅在SOLD状态) ──
    if fstate == STATE_SOLD and not st['stop_loss_hit']:
        # 止损线 = 持仓市值 × STOP_LOSS_PCT
        loss_limit = st['base_shares'] * signal['open_price'] * STOP_LOSS_PCT
        if st['day_pnl'] < -loss_limit:
            _log(f'[止损] 亏损¥{st["day_pnl"]:.0f} 超过限制¥{loss_limit:.0f}({STOP_LOSS_PCT*100:.1f}%)')
            st['stop_loss_hit'] = True
            _force_buyback(ContextInfo)


# ============================================================================
# 状态处理函数
# ============================================================================

def _handle_idle(ContextInfo, price):
    """
    IDLE状态: 等待价格触及触发线

    触发条件: price >= sell_trigger (开盘价 + ATR × 动态乘数)
    触发后 → SPIKING状态, 开始跟踪最高价
    """
    st = ContextInfo.st
    trigger = st['daily_signal']['sell_trigger']

    if price >= trigger:
        st['fstate']            = STATE_SPIKING
        st['peak_price']        = price
        st['state_enter_time']  = _now()
        _log(f'[冲高] ¥{price:.2f} >= ¥{trigger:.2f} (BASE={st["daily_signal"]["sell_mult_base"]}, 乘数={st["daily_signal"]["sell_mult"]}) → 进入冲高监控')


def _handle_spiking(ContextInfo, price):
    """
    SPIKING状态: 价格已过触发线, 跟踪最高价等回落确认

    卖出条件: price从最高点回落 >= PULLBACK_PCT (0.1%)
    假突破:   price跌回触发线以下 → 回到IDLE
    """
    st = ContextInfo.st
    trigger = st['daily_signal']['sell_trigger']

    # 更新最高价
    if price > st['peak_price']:
        st['peak_price'] = price

    peak = st['peak_price']
    pullback = (peak - price) / peak             # 从最高点的回落比例

    # ── 回落确认 → 卖出 ──
    if pullback >= PULLBACK_PCT:
        _log(f'[卖出] 最高¥{peak:.2f} 回落{pullback*100:.2f}% → ¥{price:.2f} 确认卖出')
        _mini_sell(ContextInfo, price)

        # 状态切换到 SOLD
        st['fstate']          = STATE_SOLD
        st['sell_fill_price'] = price
        st['state_enter_time'] = _now()

        # ★ 基于实际卖出价动态计算买回触发线
        atr_pct = st['daily_signal']['atr_pct']
        buyback_pct = atr_pct * st['daily_signal']['buyback_mult']
        buyback_target = round(price * (1.0 - buyback_pct), 2)

        st['buyback_target']     = buyback_target
        st['buyback_target_pct'] = buyback_pct * 100
        st['sell_elapsed_bars']  = 0             # 重置卖后计时器

        _log(f'  买回触发线: ¥{buyback_target:.2f} (卖价-{buyback_pct*100:.2f}%)')
        _log(f'  紧急买回线: ¥{price*(1+EMERGENCY_BUYBACK_PCT):.2f} (卖价+{EMERGENCY_BUYBACK_PCT*100:.1f}%)')

    # ── 假突破 → 回到IDLE ──
    elif price < trigger:
        _log(f'[假突破] ¥{price:.2f} 跌回触发线下 ¥{trigger:.2f} (最高触及 ¥{peak:.2f})')
        st['fstate']     = STATE_IDLE
        st['peak_price'] = 0.0


def _handle_sold(ContextInfo, price):
    """
    SOLD状态: 已卖出, 等待价格下跌到买回触发线

    三个触发条件 (优先级递减):
      1. 紧急买回: price >= sell_price × (1 + EMERGENCY_BUYBACK_PCT)
      2. 进入DIPPING: price <= buyback_target
      3. ★ v8/v9: 动态收紧 — 30秒后仍不跌, 提高买回触发线

    注意: 止损检查在ontimer主循环中统一处理
    """
    st = ContextInfo.st
    sp = st['sell_fill_price']                   # 卖出成交价
    bt = st['buyback_target']                    # 原始买回触发线

    # ── 条件1: 紧急买回 (卖飞防护) ──
    emergency_line = sp * (1.0 + EMERGENCY_BUYBACK_PCT)
    if price >= emergency_line:
        rise_pct = (price - sp) / sp * 100
        _log(f'[紧急买回] 卖¥{sp:.2f} → 现¥{price:.2f}(+{rise_pct:.2f}%) >= 紧急线¥{emergency_line:.2f}')
        _mini_buyback(ContextInfo, price, '紧急')
        return

    # ── ★ v9修复: 动态收紧 (min→max) ──
    # 卖出30秒后, 如果价格仍在卖价99.5%以上(几乎没跌),
    # 收紧买回目标: bt_tight = sp × (1 - ATR% × 0.15 × 0.60)
    # 效果: 买回触发价上移, 买回更早触发, 减少踏空风险
    tightened_bt = bt
    if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
        tightened_bt = sp * (
            1.0 - st['daily_signal']['atr_pct']
            * BUYBACK_TRIGGER_MULT
            * BUYBACK_TIGHTEN_MULT
        )
        # ★ v9修复: 使用 max 而非 min
        #   收紧后买回线应更靠近卖价(更高), 所以取max(收紧值, 原始值)
        #   v8的min(收紧值, 原始值)会永远取原始值, 导致收紧不生效
        tightened_bt = round(max(tightened_bt, bt), 2)

    # ── 条件2: 进入DIPPING ──
    if price <= tightened_bt:
        drop_pct = (sp - price) / sp * 100
        st['fstate']     = STATE_DIPPING
        st['dip_price']  = price
        st['state_enter_time'] = _now()

        tag = '(收紧)' if tightened_bt > bt else ''
        _log(f'[买回触发{tag}] ¥{price:.2f}(-{drop_pct:.2f}%) <= ¥{tightened_bt:.2f} → 进入反弹监控')
        _log(f'  dip=¥{price:.2f} | 回升线=¥{price*(1+BOUNCE_PCT):.2f}(+{BOUNCE_PCT*100:.2f}%)')


def _handle_dipping(ContextInfo, price):
    """
    DIPPING状态: 价格已到买回区域, 等待从最低点回升确认

    三个分支:
      1. 价格继续跌 → 更新最低价
      2. 从最低点回升 >= BOUNCE_PCT → 确认买回!
      3. 价格涨回买回触发线之上 → 假跌破, 回到SOLD

    ★ v9: 添加 dip==0 防线, 防止ZeroDivisionError
    """
    st = ContextInfo.st
    bt = st['buyback_target']

    # 更新最低价
    if price < st['dip_price']:
        st['dip_price'] = price

    dip = st['dip_price']

    # ★ v9新增: 防御性检查 — dip应该已经被设置为price(>0)
    if dip <= 0:
        _log(f'[异常] dip={dip}, 重置为当前价')
        st['dip_price'] = price
        dip = price

    bounce = (price - dip) / dip                  # 从最低点的回升比例

    # ── 回升确认 → 买回 ──
    if bounce >= BOUNCE_PCT:
        sell_p = st['sell_fill_price']
        gross_profit = (sell_p - price) * TRADE_LOT_SIZE

        _log(f'[买回] 最低¥{dip:.2f} 回升{bounce*100:.2f}% → ¥{price:.2f}')
        _log(f'  卖¥{sell_p:.2f} → 买¥{price:.2f} | 毛利≈¥{gross_profit:.0f} | 日内PnL≈¥{st["day_pnl"]:.0f}')

        _mini_buyback(ContextInfo, price, '正常')
        st['total_t_days'] += 1
        st['total_pnl']    += gross_profit        # ★ 注意: 累计的是毛利(未扣佣金), 与v5一致

    # ── 假跌破 → 回到SOLD ──
    elif price > bt:
        _log(f'[假跌破] ¥{price:.2f} 涨回买回线上 ¥{bt:.2f} (最低触及 ¥{dip:.2f})')
        st['fstate']     = STATE_SOLD
        st['dip_price']  = 0.0
        st['state_enter_time'] = _now()


# ============================================================================
# 第七部分：下单函数
# ============================================================================
# QMT API order_shares(stockcode, shares, [style, price], ContextInfo, [accId])
#   shares>0 = 买入, shares<0 = 卖出
#   style: 'FIX'=指定价, 'COMPETE'=对手价(市价), 'LATEST'=最新价(默认)

def _mini_sell(ContextInfo, price):
    """
    限价卖出1手 — 状态: SPIKING → SOLD
    失败时回退到IDLE, 避免状态机卡死
    """
    st = ContextInfo.st
    try:
        order_shares(STOCK_QMT, -TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 下单卖出: ¥{price:.2f} × {TRADE_LOT_SIZE}股')
    except Exception as e:
        _log(f'  >>> 卖出失败: {e}')
        st['fstate'] = STATE_IDLE                # 失败回退


def _mini_buyback(ContextInfo, price, reason=''):
    """
    限价买回1手 — 状态: SOLD/DIPPING → DONE
    买回前检查资金是否充足
    """
    st = ContextInfo.st
    need = price * TRADE_LOT_SIZE * 1.001        # 留0.1%的缓冲
    avail = _cash(ContextInfo)

    if avail < need:
        _log(f'  >>> 买回失败: 资金不足 (需¥{need:,.0f} > 可用¥{avail:,.0f} 缺¥{need-avail:,.0f})')
        return

    try:
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'FIX', price, ContextInfo, _acc(ContextInfo))
        _log(f'  >>> 下单买回({reason}): ¥{price:.2f} × {TRADE_LOT_SIZE}股')
        st['fstate'] = STATE_DONE
    except Exception as e:
        _log(f'  >>> 买回失败({reason}): {e}')


def _force_buyback(ContextInfo):
    """
    尾盘/止损强制买回 — 使用对手价(COMPETE)市价成交
    QMT API: style='COMPETE' 不需要price参数
    """
    st = ContextInfo.st
    try:
        # COMPETE=对手价(相当于市价), 无需指定price
        order_shares(STOCK_QMT, TRADE_LOT_SIZE, 'COMPETE', ContextInfo, _acc(ContextInfo))
        st['fstate'] = STATE_FORCED
        _log(f'[尾盘买回] 已下单(对手价)')
    except Exception as e:
        _log(f'[尾盘失败!!] {e}')
        st['fstate'] = STATE_FORCED              # 标记已处理, 避免重复尝试


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
    """
    获取交易账户ID
    优先使用ContextInfo.accID(QMT注入属性),
    如果不存在则回退到模块级ACCOUNT常量
    """
    try:
        # ContextInfo.accID 是QMT内部属性, 并非所有版本都有
        if hasattr(ContextInfo, 'accID') and ContextInfo.accID:
            return ContextInfo.accID
    except Exception:
        pass
    return ACCOUNT


# ============================================================================
# 第九部分：QMT回调 (委托回报 & 成交回报)
# ============================================================================

def order_callback(ContextInfo, order):
    """
    委托回报 — QMT异步推送
    order对象属性: m_nOrderStatus, m_dOrderPrice, m_nVolumeTraded, m_nVolumeTotal
    """
    status_map = {
        50: '已报', 52: '部成', 53: '全成',
        54: '部撤', 55: '已撤', 56: '废单'
    }
    if order.m_nOrderStatus in status_map:
        _log(f'[委托] ¥{order.m_dOrderPrice:.2f} {order.m_nVolumeTraded}/{order.m_nVolumeTotal}股 → {status_map[order.m_nOrderStatus]}')


def deal_callback(ContextInfo, deal):
    """
    成交回报 — QMT异步推送, 用于跟踪当日PnL

    deal对象属性:
      m_nDirection: 1=买入, 2=卖出
      m_dPrice: 成交价
      m_nVolume: 成交量
      m_fCommission: 佣金
      m_fStampTax: 印花税
    """
    st = ContextInfo.st
    direction = '买' if deal.m_nDirection == 1 else '卖'
    amount = deal.m_dPrice * deal.m_nVolume
    fee = deal.m_fCommission + deal.m_fStampTax

    # 反T逻辑: 卖出(先) → day_pnl += 收入; 买入(后) → day_pnl -= 支出
    if deal.m_nDirection == 2:                   # 卖出
        st['day_pnl'] += (amount - fee)
    else:                                        # 买入
        st['day_pnl'] -= (amount + fee)

    _log(f'[成交] {direction} ¥{deal.m_dPrice:.2f}×{deal.m_nVolume}股 | 当日PnL≈¥{st["day_pnl"]:.0f}')


def stop(ContextInfo):
    """策略停止回调 — QMT卸载策略时调用"""
    st = getattr(ContextInfo, 'st', None)
    if st:
        _log(f'{STOCK_NAME} v9.0 停止 | 累计 {st.get("total_t_days", 0)}天 | 毛利≈¥{st.get("total_pnl", 0):,.0f}')
        # 如果停止时有未平仓的卖出, 发出警告
        if st.get('fstate') in (STATE_SOLD, STATE_DIPPING):
            _log(f'  [警告] 策略停止时有未买回头寸! 请手动检查持仓!')


# ============================================================================
# 第十部分：工具函数
# ============================================================================

def _now():
    """当前时间 HH:MM:SS — 用于日志和状态机判断"""
    return _time.strftime('%H:%M:%S')


def _ts():
    """带方括号的时间戳 [HH:MM:SS] — 用于日志前缀"""
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
    注意: 不含集合竞价(9:15-9:25), 只覆盖连续竞价
    时间格式: 'HH:MM:SS', 字符串比较在此格式下是安全的
    """
    return ('09:30:00' <= now <= '11:30:00') or ('13:00:00' <= now <= '15:00:00')
