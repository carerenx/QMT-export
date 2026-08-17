# MiniQMT DayTrading v20 策略 — 全面分析报告

> 分析日期: 2026-08-11 | 标的: 601869 长飞光纤 | 运行环境: MiniQMT (QMT 极简模式)

---

## 目录

1. [架构总览](#1-架构总览)
2. [运行流程](#2-运行流程)
3. [信号系统 (core/signals.py)](#3-信号系统)
4. [动态乘数模型](#4-动态乘数模型)
5. [状态机](#5-状态机)
6. [反T (Short) 交易逻辑](#6-反t-交易逻辑)
7. [正T (Long) 交易逻辑](#7-正t-交易逻辑)
8. [风控体系](#8-风控体系)
9. [下单 & 成交确认](#9-下单-成交确认)
10. [命令行接口 & 运行模式](#10-命令行接口)
11. [依赖关系](#11-依赖关系)
12. [参数速查表](#12-参数速查表)
13. [潜在问题 & 改进方向](#13-潜在问题)

---

## 1. 架构总览

```
                    ┌──────────────────────────────────┐
                    │     DayTradeing_v20_stragety     │
                    │          _miniqmt.py             │
                    │                                  │
                    │  StrategyRunner                  │
                    │   ├── init / reset_daily         │
                    │   ├── IDLE → SPIKING → SOLD →    │
                    │   │   DIPPING → 买回             │
                    │   ├── IDLE → BT_DIPPING →        │
                    │   │   BT_BOUGHT → BT_SPIKING →   │
                    │   │   卖出                     │
                    │   ├── 锁仓 & 恢复                 │
                    │   └── 下单 & 成交确认              │
                    └──────┬───────────┬───────────────┘
                           │           │
              ┌────────────▼───┐ ┌─────▼──────────┐
              │ core/signals   │ │ infra/connector │
              │  动态乘数模型    │ │  MiniQMT连接层   │
              │  趋势+波动率+   │ │  MockContextInfo│
              │  量+RSI → 信号  │ │  order_shares   │
              └──────┬─────────┘ └─────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  core/config   core/indicators  infra/logger
   参数集中      技术指标纯函数     文件日志
```

**设计模式**: 模块化分层架构。`core/` 层纯计算无副作用，`infra/` 层封装 MiniQMT xtquant SDK，`StrategyRunner` 编排业务逻辑。

**与 QMT 内部策略的本质区别**:
| 维度 | MiniQMT v20 | QMT 内部策略 (v5/v6/v7) |
|------|-------------|--------------------------|
| 主循环 | `StrategyRunner.run()` 自写 while 循环 | `ContextInfo.run_time("ontimer", ...)` 定时回调 |
| 行情 | `xtdata.get_full_tick()` / `.get_local_data()` | `ContextInfo.get_full_tick()` / `.get_history_data()` |
| 交易 | `xttrader.XtQuantTrader` 直接下单 | `order_shares()` QMT 内置函数 |
| 日志 | `FileLogger` 写 .log 文件 | `print()` → QMT 控制台 |
| 回测 | 独立回测引擎 `backtest/` | QMT 内置回测面板 |
| 信号 | 四因子动态乘数 + 双向(反T+正T) | 同源派生, 但需单文件打包 |
| 编码 | UTF-8 | GBK |

---

## 2. 运行流程

### 2.1 启动 (main → StrategyRunner)

```
python DayTradeing_v20_stragety_miniqmt.py --mode [signal|live|backtest]
```

```
main()
 ├─ 解析参数 (argparse)
 ├─ --mode backtest → run_backtest_mode() → 走独立回测引擎
 ├─ --mode signal/live:
 │   ├─ 创建 FileLogger (v20版)
 │   ├─ set_logger() 注册全局日志
 │   ├─ dry_run = (mode == 'signal')
 │   └─ StrategyRunner(dry_run).run()
 │       ├─ 初始化 MiniQMTConnector
 │       │   ├─ connect_data()  → xtdata.connect()
 │       │   ├─ connect_trade() → xttrader 连接+订阅账号 (dry_run 跳过)
 │       │   └─ set_global_conn() → 注册全局引用给 get_trade_detail_data / order_shares
 │       ├─ _init_state()  → ContextInfo.st 注入全部状态字段
 │       ├─ _daily_init()  → 第一个交易信号计算
 │       └─ while self._running:
 │           ├─ 非交易时段: 心跳 5s/10s 间隔等待
 │           ├─ 交易时段: get_full_tick() → 状态机路由 → sleep(1s)
 │           └─ 尾盘/午休特殊处理
 └─ KeyboardInterrupt → 优雅退出 → stop() 打总
```

### 2.2 每日初始化 (_daily_init)

执行时机: `StrategyRunner.run()` 启动时调用，以及盘中每日数据变化时刷新。

**核心流程**:
```
_daily_init()
 ├─ 判断是否新交易日 (= 重置 vs 刷新)
 ├─ 获取日线历史数据 (open/high/low/close/volume, 长度 80)
 ├─ _refresh_position() — 从 MiniQMT 读取实时持仓
 ├─ get_full_tick() — 获取实时价格 & 今日开盘价
 ├─ compute_signal() — ★核心★ 四因子动态乘数信号计算
 ├─ 正T/反T可行性判定 (资金/持仓检查)
 ├─ 买入触发线 & 卖出目标线计算
 └─ 状态机初始化 (fstate=IDLE, 重置峰值/低谷/锁定)
```

### 2.3 盘中主循环 (StrategyRunner.run)

```python
while self._running:
    now = cfg.now_hms()
    if not cfg.is_market_open(now):
        # 非交易时段: 心跳等待
        if 09:25 <= now < 09:30: _daily_init()  # 盘前5分钟刷新信号
        sleep(5-10s); continue

    # 交易时段:
    fstate = st['fstate']

    # 状态为 DONE/FORCED: 检查恢复条件
    if fstate in (DONE, FORCED):
        _maybe_resume_trading()  # 还有剩余次数 → 恢复IDLE
        sleep(3s); continue

    # 活跃状态: 获取实时价格 → 路由到状态处理器
    price = get_full_tick()['lastPrice']

    if fstate == IDLE:
        _assess_strength()  # 锁仓评估
        _handle_idle()      # 检查触发条件
    elif fstate == SPIKING:  _handle_spiking()
    elif fstate == SOLD:     _handle_sold()
    elif fstate == DIPPING:  _handle_dipping()
    # ... 正T状态类似

    # 尾盘强平 & 止损检查
    sleep(1s)
```

---

## 3. 信号系统

### 3.1 信号计算入口: `compute_signal(opens, highs, lows, closes, volumes)`

输入: 5 个日线数据列表 (长度 ≥ 60)
输出: 信号 dict

```python
{
    'do_short':          bool,    # 反T是否允许
    'blocked_reason':    str,     # 禁止原因
    'trend':             str,     # 市场趋势
    'sell_trigger':      float,   # 卖出触发线 (价格)
    'sell_mult':         float,   # 最终乘数
    'sell_mult_base':    float,   # 基准乘数
    'factor_details':    dict,    # 各因子偏差明细
    'open_price':        float,   # 今日开盘价
    'atr':               float,   # 当前ATR值(元)
    'atr_pct':           float,   # 当前ATR%(相对值)
    'rsi':               float,   # RSI值
    'vol_ratio':         float,   # 量比(当前/20日均)
    'range_capped':      bool,    # 是否振幅约束触发
    # ... 正T相关字段
    'buy_trigger':       float,   # 正T买入触发线
    'sellback_target_hint': float,# 正T卖出目标提示
}
```

### 3.2 趋势判定

```python
if cc > ma20 AND ma5 > ma20 AND rsi > 70 AND up_streak >= 5:
    trend = 'strong_bull'      # 强牛 → 彻底禁反T
elif cc > ma20 AND ma5 > ma20:
    trend = 'weak_bull'        # 弱牛
elif cc < ma20 AND ma5 < ma20:
    trend = 'bear'             # 熊市
else:
    trend = 'sideways'         # 震荡
```

### 3.3 反T方向决策 (三类风控)

| 条件 | 动作 | 原因 |
|------|------|------|
| `trend == 'strong_bull'` | `do_short = False` | 强牛行情卖飞风险极高 |
| `vol_ratio < 0.4` | `do_short = False` | 无量震荡不适合做T |
| `rsi > 75` | `do_short = False` | RSI 超买区间不宜做空 |

---

## 4. 动态乘数模型

★ **v20 最核心的能力: 四因子自适应卖出触发乘数**

### 4.1 因子拓扑

```
卖出触发线 = 开盘价 + ATR × 最终乘数
                                    │
             ┌──────────────────────┼──────────────────────┐
             ▼                      ▼                      ▼
        基准乘数              因子偏差总和          硬上限(Dynamic)
       = f(trend)           ∑ 四个因子偏差          MIN=0.20 MAX=1.50
             │
      bear:      0.40
      sideways:  0.55     (±0.00~0.25 每因子)
      weak_bull: 0.65
      strong_bull: +999 → do_short=False

最终乘数 = clamp(基准 + ∑偏差, 0.20, 1.50)
```

### 4.2 四因子偏差明细

**因子 1: 趋势 (Trend)**
| 趋势 | 条件 | 偏差 |
|------|------|------|
| bear | up_streak=0(转跌首日) | -0.25 |
| bear | up_streak>0 | -0.15 |
| strong_bull | — | +999 → 直接禁反T |
| weak_bull | up_streak≥3 | +0.20 |
| weak_bull | up_streak≥1 | +0.12 |
| weak_bull | — | +0.05 |
| sideways | — | +0.00 |

**因子 2: 波动率 (Volatility) — 双维度加权**

| ATR% | atr_d | ATR比 | atrd_d | 合并(0.55×atr_d+0.45×atrd_d) |
|------|-------|-------|--------|------|
| >8% | -0.30 | >1.50 | -0.25 | -0.28 ~ -0.35 |
| >7% | -0.22 | >1.25 | -0.18 | -0.21 ~ -0.24 |
| >6% | -0.15 | >1.10 | -0.10 | -0.13 ~ -0.18 |
| >5% | -0.08 | >0.90 | 0.00 | -0.04 ~ -0.05 |
| >3% | +0.05 | >0.70 | +0.12 | +0.08 ~ +0.15 |
| >2% | +0.15 | >0.50 | +0.20 | +0.17 ~ +0.22 |
| ≤2% | +0.25 | — | +0.25 | +0.25 |

> **设计意图**: 波动率越高 → 乘数越低 → 卖出触发越晚 → 更难触发(反向保护)。
> 双维度 (绝对ATR% + 相对ATR比) 考虑了"当前波动"和"历史波动位阶"。

**因子 3: 成交量 (Volume)**
| 量比 | 偏差 |
|------|------|
| >2.00 | -0.25 (放量不利) |
| >1.50 | -0.18 |
| >1.20 | -0.08 |
| 0.80~1.20 | 0.00 (中性) |
| >0.60 | +0.12 (缩量有利) |
| >0.40 | +0.20 |
| ≤0.40 | +0.25 |

**因子 4: RSI**
| RSI | 偏差 |
|-----|------|
| >80 | -0.25 (超买危险) |
| >70 | -0.18 |
| >60 | -0.08 |
| 45~60 | 0.00 |
| >40 | +0.03 |
| >30 | +0.10 |
| >20 | +0.20 |
| ≤20 | +0.25 (超卖有利) |

### 4.3 振幅约束

```python
# 日内振幅MA限制
daily_range_ma10 = avg((high-low)/open, 10天)
max_trigger_by_range = open × (1 + daily_range_ma10 × 0.80)

if sell_trigger_raw > max_trigger_by_range:
    sell_trigger = max_trigger_by_range  # 被振幅约束
    range_capped = True
```

防止极端行情下（ATR 异常飙升）卖出触发线过高导致策略失效。

---

## 5. 状态机

### 5.1 8 个状态

```
STATE_IDLE       = 'IDLE'        # 空闲, 等待触发
STATE_SPIKING    = 'SPIKING'     # 反T: 价格突破触发线, 追踪峰值
STATE_SOLD       = 'SOLD'        # 反T: 已卖出, 等待买回
STATE_DIPPING    = 'DIPPING'     # 反T: 跌到买回线, 等回升确认
STATE_DONE       = 'DONE'        # 本轮交易完成
STATE_FORCED     = 'FORCED'      # 强制买回/强平
STATE_BT_DIPPING = 'BT_DIPPING'  # 正T: 价格跌破买入线, 等回升确认
STATE_BT_BOUGHT  = 'BT_BOUGHT'   # 正T: 已买入, 等卖出
STATE_BT_SPIKING = 'BT_SPIKING'  # 正T: 价格突破卖出线, 追踪峰值
```

### 5.2 状态转移图

```
【反T】
                    ┌── price < sell_trigger ──┐
                    │   (假突破回退)              │
IDLE ──price≥sell_trigger──▶ SPIKING ──回落≥0.1%──▶ SOLD
   ▲                             │ 更新peak         │
   │                             ▼              ┌───┼───────────┐
   │                         [卖出执行]          │   │           │
   ◀── _maybe_resume ── DONE ◀── 买回          │   │           │
                              │           跌到买回线   紧急 3%+   14:57
                              │               │       │         │
                              ▼               ▼       ▼         ▼
                          DIPPING           强制买回  强制买回  强制买回
                            │
                    ◀── bounce ≥ 0.1% ──▶ 买回 → DONE
                    
【正T】
IDLE ──price≤buy_trigger──▶ BT_DIPPING ──bounce≥0.1%──▶ BT_BOUGHT
   ▲                           │更新dip          │止损1.5%
   │                           ▼                 ▼
   │                     [买入执行]          BT_FORCED
   │                                           
   ◀── DONE ◀── 回落≥0.1%── BT_SPIKING ◀── price≥target
                  │ 更新peak
                  ▼
              [卖出执行]
```

---

## 6. 反T (Short) 交易逻辑

### 6.1 触发 → 卖出

```
进入条件  (IDLE):  price ≥ sell_trigger + 可用持仓≥1手 + 实心数未满
峰值追踪  (SPIKING):  持续更新 peak_price
假突破回退:  price < sell_trigger → 回到 IDLE
卖出确认:  (peak - price) / peak ≥ 0.10% → 执行卖出
```

**卖出价**: 回落时的实时 `lastPrice`
**手数**: 1 手 (TRADE_LOT_SIZE=100 或 200)

### 6.2 买回触发 — ★ v20 核心: 基于实际卖出价动态计算

```
buyback_target  = 实际卖出成交价 × (1 - ATR% × 0.15)
buyback_target_pct = ATR% × 0.15 × 100
```

**参数含义**:
| ATR% | 乘数 | 回撤阈值 | 实际效果 |
|------|------|---------|----------|
| 2% (低波动股) | 0.15 | 0.3% | 小幅回撤就买回(快进快出) |
| 7% (高波动股) | 0.15 | 1.05% | 跌够1%才触发(防假跌破) |
| 13% (极端波动) | 0.15 | 1.95% | 容忍更大回撤 |

### 6.3 买回线动态收紧

```python
# 条件: 卖出后超过30秒未触发 AND 当前价 > 卖价×0.995
if sell_elapsed_bars > 30 and price > sp * 0.995:
    tightened_bt = sp * (1 - ATR% × 0.15 × 0.60)  # 乘数再×0.6收窄
    buyback_target = max(tightened_bt, original_bt)
```

### 6.4 三路买回

| 优先级 | 条件 | 动作 |
|--------|------|------|
| 1 (紧急) | `price ≥ sell_price × 1.03` (卖飞 3%+) | `_mini_buyback(price, '紧急')` → 立即买回 |
| 2 (正常) | `price ≤ buyback_target` → DIPPING → `bounce ≥ 0.1%` | `_mini_buyback(price, '正常')` |
| 3 (尾盘) | `now ≥ 14:57:00` + 状态=SOLD/DIPPING | `_force_buyback()` → 对手价 |

---

## 7. 正T (Long) 交易逻辑

### 7.1 触发 → 买入

```
买入触发 = max(open × 0.97, current_price × 0.98)  # 取较保守值
            floor保底          trail追踪
        
进入条件  (IDLE):  price ≤ buy_trigger + 可用现金≥1手 + 有可卖底仓
探底监控  (BT_DIPPING):  持续更新 bt_dip_price
买入确认:  (price - dip) / dip ≥ 0.10% → 执行买入
```

### 7.2 卖出

```
卖出目标 = 买入成交价 × 1.012  (+1.2%)
止损触发 = 买入价 × 0.985      (-1.5%)
尾盘强平 = 14:57
```

---

## 8. 风控体系

### 8.1 五层风控

| 层 | 机制 | 位置 | 效果 |
|----|------|------|------|
| **1** 方向过滤 | `do_short=False` (强牛/缩量/RSI超买) | `compute_signal()` | 从源头禁止反T |
| **2** 仓位检查 | `base_can_use / avail_cash ≥ TRADE_LOT_SIZE` | `_handle_idle()` | 资金不足不触发 |
| **3** 每日上限 | `trade_count ≤ MAX_DAILY_TRADES(3)` | `_handle_idle()` | 每天最多3轮 |
| **4** 止损 | `day_pnl < -base_shares × open × 1.5%` | `_handle_sold()` | 单日亏损超限强制买回 |
| **5** 锁仓 | 盘中动量检测 (见 8.2) | `_assess_strength()` | 强牛暂停反T |

### 8.2 锁仓机制

```python
_handle_strength(price, now_ts):
    # 维护 300s 价格历史
    history = [(t, p) for t, p in history if t >= now - 300s]

    # 三个条件全部满足 → 锁仓
    cond1: pn > open × 1.015       # 价格高于开盘 1.5%
    cond2: (pn - p5)/p5 > 0.5%     # 近300秒动量 > 0.5%
    cond3: (high - pn)/high < 0.5% # 从高点回撤 < 0.5% (持续走强)

    should_lock = cond1 AND cond2 AND cond3
    
    # 解锁需要: 条件不满足 + 冷却时间(120s)已过
```

**设计意图**: 开盘后如果出现持续强势上涨（价格新高+动量强+无回撤）→ 立即锁仓避免"卖了就涨"。适用于实盘的盘中防御。

### 8.3 紧急买回 & 止损

| 触发条件 | 动作 | 阈值 |
|----------|------|------|
| `price ≥ sell_price × 1.03` | 紧急买回 | 卖飞 3%+ |
| `day_pnl < -base_shares × open × 0.015` | 止损强制买回 | 单日亏损 1.5% |
| `now ≥ 14:57:00 + enable_force_close` | 尾盘强制买回 | 不管盈亏 |

---

## 9. 下单 & 成交确认

### 9.1 下单方式

| 函数 | 手数 | 方向 | 价格类型 |
|------|------|------|----------|
| `_mini_sell()` | -TRADE_LOT_SIZE | 卖出(反T) | `FIX` |
| `_mini_buyback()` | +TRADE_LOT_SIZE | 买回(反T) | `FIX` |
| `_mini_buy()` | +TRADE_LOT_SIZE | 买入(正T) | `FIX` |
| `_mini_sell_bt()` | -TRADE_LOT_SIZE | 卖出(正T) | `FIX` |
| `_force_buyback()` | +TRADE_LOT_SIZE | 强制买回 | `COMPETE`(对手价) |
| `_do_bt_force_sell()` | -TRADE_LOT_SIZE | 强制卖出 | `COMPETE` |

### 9.2 成交确认 (_wait_for_fill)

```python
_wait_for_fill(snap_before, expected_delta, label, price, shares, next_state, timeout=5.0):
    while waited < 5s:
        sleep(0.5s)
        读取持仓变化
        if actual == expected:  # 精确匹配
            _verify_trade() → OK
            return True
    # 超时但持仓确实变了
    _verify_trade(label + '(超时)') → 仍算成功
    return False
```

**特色**: 交易后 0.5s 轮询持仓变化确认成交（类似"轮询 FIX 委托状态"）。

### 9.3 交易校验 (_verify_trade)

```
snap_before → 下单 → 等待 → snap_after
  ├─ 持仓变化 d_shares = after.shares - before.shares
  ├─ 资金变化 d_cash   = after.cash   - before.cash
  ├─ 资产变化 d_asset  = after.total  - before.total
  └─ 判断:
      d_shares == trade_shares  → OK
      d_shares == 0             → PENDING
      其他                       → PARTIAL (没精确匹配)
```

---

## 10. 命令行接口 & 运行模式

```bash
# 信号模式 (只计算信号, 不连接交易) — 开盘前检查信号
python DayTradeing_v20_stragety_miniqmt.py --mode signal

# 实盘模式 (连接行情+交易) — 需要二次确认
python DayTradeing_v20_stragety_miniqmt.py --mode live
# 提示: "!!! 实盘启动确认 !!! 输入 yes 继续:"

# 回测模式 (独立引擎)
python DayTradeing_v20_stragety_miniqmt.py --mode backtest --start 20250801 --end 20260806
```

### 信号模式 vs 实盘模式

| | signal | live |
|----|--------|------|
| 行情连接 | ✓ xtdata | ✓ xtdata |
| 交易连接 | ✗ | ✓ xttrader |
| 下单 | 模拟日志 | 真实下单 |
| 成交确认 | 跳过 `_wait_for_fill` | 轮询持仓变化 |
| 日志文件 | ✓ | ✓ |

---

## 11. 依赖关系

```
DayTradeing_v20_stragety_miniqmt.py
 ├── core/config.py           # 99个参数常量 + 时间工具函数
 │   ├── STOCK_CODE/NAME/QMT   # 标的配置
 │   ├── ATR_PERIOD=14        # 技术指标参数
 │   ├── SELL_TRIGGER_BASE_*  # 乘数基准
 │   ├── PULLBACK/BOUNCE_PCT  # 回落/回升阈值
 │   ├── BUYBACK_TRIGGER_MULT # ★买回乘数(核心参数)
 │   ├── BUY_TRIGGER_PCT/TRAIL# 正T参数
 │   ├── LOCK_* 参数          # 锁仓参数
 │   ├── FORCE_CLOSE_TIME     # 尾盘时间
 │   └── now_hms()/is_market_open()/time_to_open()
 ├── core/signals.py          # 信号计算核心
 │   ├── calc_dynamic_sell_mult()  # ★四因子乘数
 │   ├── compute_signal()          # 日级信号生成
 │   └── 依赖 core/indicators
 ├── core/indicators.py       # 技术指标纯函数
 │   ├── sma()                # 简单移动平均
 │   ├── atr()                # 平均真实波幅
 │   ├── rsi()                # 相对强弱指标
 │   ├── up_streak()          # 连续上涨天数
 │   └── daily_range_ma()     # 日内振幅MA
 ├── infra/connector.py       # MiniQMT 连接 & QMT 模拟层
 │   ├── MiniQMTConnector     # xtdata + xttrader 封装
 │   ├── MockContextInfo      # 模拟 QMT ContextInfo
 │   ├── MockPosition/Account # 模拟 QMT 数据对象
 │   ├── get_trade_detail_data() # 模拟 QMT 全局函数
 │   └── order_shares()       # 模拟 QMT 下单
 └── infra/logger.py          # 文件日志
     ├── FileLogger           # 双写(控制台+文件) 每次flush
     └── _log()               # 全局日志 [HH:MM:SS] 前缀
```

---

## 12. 参数速查表

### 标的 & 账户
| 参数 | 值 | 说明 |
|------|-----|------|
| `ACCOUNT` | `8890145315` | QMT 资金账号 |
| `STOCK_CODE` | `601869` | 长飞光纤 |
| `STOCK_QMT` | `601869.SH` | QMT 代码格式 |
| `TRADE_LOT_SIZE` | `100`(源) / `200`(实盘) | 每手股数 |

### 反T参数
| 参数 | 值 | 说明 |
|------|-----|------|
| `SELL_TRIGGER_BASE_BEAR` | 0.40 | 熊市基准乘数 |
| `SELL_TRIGGER_BASE_SIDEWAYS` | 0.55 | 震荡基准乘数 |
| `SELL_TRIGGER_BASE_WEAK_BULL` | 0.65 | 弱牛基准乘数 |
| `DYNAMIC_MULT_MIN/MAX` | 0.20/1.50 | 乘数硬上下限 |
| `PULLBACK_PCT` | 0.0010 (0.1%) | 回落确认阈值 |
| `BUYBACK_TRIGGER_MULT` | 0.15 | ★买回目标 = 卖价 × (1-ATR%×此值) |
| `BOUNCE_PCT` | 0.0010 (0.1%) | 回升确认阈值 |
| `BUYBACK_TIGHTEN_MULT` | 0.60 | 买回线收紧乘数 |
| `EMERGENCY_BUYBACK_PCT` | 0.03 (3%) | 紧急买回触发线 |

### 正T参数
| 参数 | 值 | 说明 |
|------|-----|------|
| `BUY_TRIGGER_PCT` | 0.030 (3%) | 买入floor = 开×0.97 |
| `BUY_TRIGGER_TRAIL` | 0.020 (2%) | 买入trail = 现×0.98 |
| `SELLBACK_RISE_PCT` | 0.012 (1.2%) | 卖出目标 = 买价×1.012 |

### 风控参数
| 参数 | 值 | 说明 |
|------|-----|------|
| `VOLUME_FILTER_RATIO` | 0.4 | 量比低于此禁反T |
| `RSI_OVERBOUGHT` | 75 | RSI超买禁反T |
| `STRONG_BULL_RSI` | 70 | 强牛判定RSI |
| `STRONG_BULL_STREAK` | 5 | 强牛判定连涨天数 |
| `STOP_LOSS_PCT` | 0.015 (1.5%) | 单日止损线 |
| `LOCK_PRICE_RATIO` | 0.015 (1.5%) | 锁仓价格比 |
| `LOCK_LOOKBACK_SEC` | 300 | 锁仓回看窗口 |
| `LOCK_COOLDOWN_SEC` | 120 | 解锁冷却时间 |

### 仓位参数
| 参数 | 值 | 说明 |
|------|-----|------|
| `MAX_POSITION_LOTS` | 5 | 最大仓位手数 |
| `MIN_POSITION_LOTS` | 1 | 最小仓位手数 |
| `MAX_DAILY_TRADES` | 3 | 每日最大交易次数 |

### 数据 & 费率
| 参数 | 值 | 说明 |
|------|-----|------|
| `HIST_DATA_LEN` | 80 | 历史数据长度(天) |
| `COMMISSION` | 0.00025 (万2.5) | 佣金费率 |
| `STAMP_TAX` | 0.001 (千1) | 印花税 |

---

## 13. 潜在问题 & 改进方向

### 13.1 已识别问题

| # | 问题 | 影响 | 改进建议 |
|---|------|------|----------|
| **1** | `get_full_tick` 的开盘价可能为 0 (集合竞价时段) | 买入触发线异常 | 加 `if today_open > 0` 判断后再覆盖 `opens[-1]` |
| **2** | `_do_backtest_init_buy` 用 bar close 作为开盘价买入 | 回测成交价不真实 | 应使用 `THIS_CLOSE` 订单或面板配初始持仓 |
| **3** | `_wait_for_fill` 轮询 `0.5s × 10 = 5s` 超时 | 实盘滑点大时持仓确认延迟 | 减小轮询间隔至 0.2s |
| **4** | 正T 与反T 共享 `MAX_DAILY_TRADES=3` 计数独立 | 一天内两边都做时上限含义模糊 | 可改为各自独立上限 |
| **5** | `sell_price = max(open_p, sell_trig)` 容易高估卖价 | OHLC 回测判定过于乐观 | 使用更保守的价格估算 |
| **6** | `_assess_strength` 在回测无效 | 回测中不锁仓，行为与实盘不同 | 加回测模式的锁仓模拟 |

### 13.2 与 QMT 内部版本的对应关系

| 版本 | 核心特征 | 信号来源 |
|------|----------|----------|
| MiniQMT v20 | 四因子 + 双方向 + 锁仓 + tick 确认 | `core/signals.py` |
| QMT v5 | 仅反T + 动态买回 | 二因子(趋势+ATR) |
| QMT v6 | 仅反T + 动态乘数 | 四因子 |
| QMT v7.1-7.5 | 双方向 + OHLC回测 | 四因子(内联) |

### 13.3 实盘运行要点

1. **必须先启动 MiniQMT** (QMT → 右上角"极简 mode")，策略才能连接行情和交易
2. **信号模式可用于盘前检查**: `--mode signal` 会在 09:25 计算信号但不交易
3. **实盘二次确认**: `--mode live` 需要控制台输入 `yes` 才能启动
4. **编码问题**: MiniQMT 用 UTF-8，QMT 内部策略用 GBK
5. **日志持久化**: 每次运行生成独立 `.log` 文件 (时间戳命名)
