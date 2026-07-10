# QMT 核心运行机制详解：handlebar、subscribe_quote 与 run_time

> 基于迅投 QMT 官方文档整理，涵盖 Python API 三大核心机制的完整解释与实战示例。

---

## 目录

1. [整体架构概览](#1-整体架构概览)
2. [handlebar —— 核心行情驱动函数](#2-handlebar--核心行情驱动函数)
3. [subscribe_quote —— 行情订阅机制](#3-subscribe_quote--行情订阅机制)
4. [run_time —— 定时器机制](#4-run_time--定时器机制)
5. [三大机制协同工作](#5-三大机制协同工作)
6. [完整实战示例](#6-完整实战示例)

---

## 1. 整体架构概览

QMT Python 策略的运行时由三条主线交织而成：

```
┌─────────────────────────────────────────────────────────────────┐
│                        QMT Python 策略运行时                       │
│                                                                   │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐  │
│  │  handlebar() │   │ subscribe_quote() │   │   run_time()    │  │
│  │  K线行情驱动  │   │   实时Tick订阅     │   │   定时器回调     │  │
│  └──────┬───────┘   └────────┬─────────┘   └────────┬────────┘  │
│         │                    │                       │           │
│         ▼                    ▼                       ▼           │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    ContextInfo (全局上下文)                    │ │
│  │  · 股票池 universe      · 历史数据 get_history_data          │ │
│  │  · 当前bar位置 barpos   · 行情数据 get_market_data           │ │
│  │  · 交易下单 passorder   · 画图输出 paint                     │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    交易主推回调 (异步)                         │ │
│  │  order_callback() → deal_callback() → account_callback()     │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

三条主线的分工：
- **handlebar**：负责 K 线级别的策略逻辑判断与交易信号产生
- **subscribe_quote**：负责实时 tick 级别行情数据的接收与处理
- **run_time**：负责时间驱动的定时任务（如定时检查持仓、定时风控）

---

## 2. handlebar —— 核心行情驱动函数

### 2.1 基本定义

`handlebar(ContextInfo)` 是 QMT Python 策略中**必须实现**的核心函数，与 `init(ContextInfo)` 并称为 QMT 策略的两个入口方法。

> *"QMT 系统 Python API 策略代码结构分两个部分，初始化函数 init() 和行情事件函数 handlebar()。"*
> — [Python API, p.30]

### 2.2 函数签名

```python
def handlebar(ContextInfo):
    """
    行情事件函数，每根 K 线运行一次。

    参数:
        ContextInfo: 策略运行环境对象，可以用于存储自定义的全局变量

    返回: 无
    """
```

**来源**: [Python API, p.31-32]

### 2.3 运行机制详解

#### 2.3.1 Bar 的概念

> 我们把单根 K 线称之为 **Bar**，每根 Bar 由 **tick（分笔）** 组成。

— [Python API, p.6]

QMT 模型是**行情驱动、逐 K 线运行**的：

```
时间轴:  Bar[0]    Bar[1]    Bar[2]    ...    Bar[N-1]   Bar[N](当前)
          │         │         │                  │           │
handlebar: ▼         ▼         ▼                  ▼           ▼
          调用1次    调用1次    调用1次            调用1次   每个tick调用1次
                                                             (实时模式)
```

**来源**: [Python API, p.8]

#### 2.3.2 历史 K 线回放阶段

点击"运行"后，模型从第 0 根 K 线开始逐根回放：

> *"点击运行模型时，模型是从第 0 根 K 线开始运行到最后一根 K 线，每根 K 线调用一次 handlebar 函数。"*
> — [Python API, p.8]

可以通过设置"快速计算"限制计算范围，只计算最新的指定数量 K 线。

#### 2.3.3 实时行情阶段（盘中）

到了最后一根 K 线（当前正在形成的 K 线），行为发生变化：

> *"在盘中，最后一根 K 线每变动一次，handlebar 函数被执行一次。"*
> — [Python API, p.8]

**关键规则**：

| 场景 | handlebar 调用次数 | 交易信号 |
|------|-------------------|---------|
| 历史 K 线 | 每根 1 次 | 无效信号（被忽略） |
| 最后一根 K 线的中间 tick | 每个 tick 1 次 | **无效信号**（虚信号） |
| 最后一根 K 线的最后 tick | 1 次 | **有效信号** |

> *"每个 tick 数据来时，最后一根 K 线会随着变动。当一个 tick 数据为所在 K 线最后一个 tick 时，此 tick 调用的 handlebar 所做的更改会被系统保存，如有交易指令，会在下一根 K 线的第一个 tick 到来时发送；其他 tick 可以打印运行结果，但 handlebar 所做更改不会被保存，也不会发送交易信号。"*
> — [Python API, p.12]

#### 2.3.4 有效信号 vs 无效信号

```
最后一根K线的生命周期:

  tick1    tick2    tick3    ...    tickN(最后一个tick)
    │        │        │               │
    ▼        ▼        ▼               ▼
 handlebar handlebar handlebar     handlebar
  ├─ 条件满足  ├─ 条件满足  ├─ 条件满足    ├─ 条件满足
  │  ❌ 无效   │  ❌ 无效   │  ❌ 无效     │  ✅ 有效！
  │  不保存    │  不保存    │  不保存      │  保存+发单
```

> *"有效信号是指在最新 bar 对应的 handlebar 里调用交易函数产生的信号。无效下单是指在非最新 bar 对应的 handlebar 里调用交易函数产生的信号，此时下单委托会被 Python 忽略。"*
> — [Python API, p.3]

### 2.4 快速交易 vs 非快速交易

> *"快速交易是指当根 bar 内产生交易信号后，Python 立即把委托发送至客户端。非快速交易是指当根 bar 内产生交易信号后，在下一个 bar 的第一个分笔到来时再把委托发送至客户端。"*
> — [Python API, p.3]

**实现方式**：

```python
# 非快速交易（默认）—— 下一根K线第一个tick发单
passorder(23, 1101, account, '600000.SH', 5, -1, 100, '', '策略1', ctx)

# 快速交易 —— 立刻发单
passorder(23, 1101, account, '600000.SH', 5, -1, 100, '策略1', 1, ctx)
#                                                          quickTrade=1
```

支持快速交易的函数：`passorder`、`algo_passorder`、`smart_algo_passorder`（`quickTrade=1` 或 `2`）。

> 当 `quickTrade=2` 时，在任何位置产生的信号都是有效信号（包括历史回放阶段）。**请谨慎使用**。
> — [Python API, p.4]

### 2.5 日线及以上的特殊处理

> *"模型运行在日 K 线周期及日 K 线以上周期时，因为是在下一根 K 线的第一个 tick 发出下单信号，而下一个 K 线就是第二日了。所以模型运行当日无法下单，除非：
> （1）设置 passorder 函数中的 quickTrade 参数为 1，立即下单；
> （2）使用 do_order(ContextInfo) 函数。"*
> — [Python API, p.9]

### 2.6 ContextInfo 中的关键属性（在 handlebar 中使用）

| 属性/方法 | 说明 | 来源 |
|-----------|------|------|
| `ContextInfo.barpos` | 当前 bar 索引位置 | [p.32] |
| `ContextInfo.is_last_bar()` | 是否最后一根 bar | [p.32] |
| `ContextInfo.is_new_bar()` | 是否新 bar 的第一个 tick | [p.32] |
| `ContextInfo.stockcode` | 当前主图代码 | [p.33] |
| `ContextInfo.period` | 当前运行周期 | [p.32] |
| `ContextInfo.get_close_price()` | 获取收盘价 | [p.32] |

### 2.7 基本代码模板

```python
#coding:gbk

def init(ContextInfo):
    """初始化：设置股票池、资金账号等"""
    ContextInfo.set_universe(['600000.SH', '000001.SZ'])
    ContextInfo.set_account('1234567890')
    # 自定义全局变量用 ContextInfo 存储
    ContextInfo.position_held = False

def handlebar(ContextInfo):
    """核心逻辑：每根K线调用一次"""
    # 1. 只在新K线的第一个tick执行逻辑（避免重复触发）
    if not ContextInfo.is_new_bar():
        return

    # 2. 只在最后一根K线上执行（避免历史回测信号干扰）
    if not ContextInfo.is_last_bar():
        return

    # 3. 获取行情数据
    close = ContextInfo.get_close_price()

    # 4. 策略逻辑判断
    if close > ContextInfo.ma_value and not ContextInfo.position_held:
        # 5. 下单
        passorder(23, 1101, ContextInfo.accID, '600000.SH', 5, -1, 100,
                  '买入信号', '策略1', ContextInfo)
        ContextInfo.position_held = True

    # 6. 画图输出
    ContextInfo.paint('signal', 1 if ContextInfo.position_held else 0, -1, 0, 'red')
```

> ⚠️ **关键提醒**：ContextInfo 会随着 bar 的切换而重置到上一根 bar 的结束状态，不建议对 ContextInfo 添加自定义属性来存储跨 bar 状态。建议用自建的全局变量（如 `g.position_held`）来存储。
> — [Python API, p.31]

---

## 3. subscribe_quote —— 行情订阅机制

### 3.1 基本定义

`subscribe_quote` 是一个**主动订阅实时行情**的接口，它不依赖 K 线图周期，可以独立接收 tick 级行情推送。适合需要在 tick 级别处理行情数据的场景（如高频策略、盘口监控）。

> *"(暂只支持分笔线周期)"* — 文档说明 tick 周期是目前主要支持的模式。
> — [Python API, p.69]

### 3.2 函数签名

```python
ContextInfo.subscribe_quote(stockcode, period, dividend_type, callback)
```

**参数说明**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `stockcode` | string | 股票代码，格式 `'stkcode.market'`，如 `'600000.SH'` |
| `period` | string | K线周期：`'follow'`(跟随主图), `'tick'`(分笔线), `'1d'`(日线), `'1m'`(分钟线), `'5m'`(5分钟线)。**暂只支持分笔线周期** |
| `dividend_type` | string | 复权方式：`'follow'`(跟随), `'none'`(不复权), `'front'`(前复权), `'back'`(后复权)。分笔周期返回数据均为不复权 |
| `callback` | function | 推送行情的回调函数，每次收到行情数据时被调用 |

**返回值**：订阅号（`subId`），用于后续 `unsubscribe_quote` 反订阅。

**来源**: [Python API, p.69-70]

### 3.3 回调函数机制

subscribe_quote 使用**闭包模式**来传递参数到回调函数：

```python
def quote_callback(stock_code):
    """
    闭包工厂函数：每个股票创建一个独立的回调函数
    参数 stock_code: 股票代码，在闭包中捕获
    返回 callback(data): 实际的行情回调函数
    """
    def callback(data):
        # data 是 tick 行情数据字典
        print(f"{stock_code}: {data}")
        # 在这里处理实时行情
    return callback

# 在 init 中订阅
def init(ContextInfo):
    ContextInfo.subscribe_quote("600000.SH", "tick", "none",
                                quote_callback('600000.SH'))
```

> **设计原因**：使用闭包模式的好处是每个股票可以有独立的回调函数，闭包中捕获的 `stock_code` 参数让你在回调中知道是哪个股票的行情。
> — [Python API, p.70]

### 3.4 完整 API 集合

#### 3.4.1 订阅

```python
subId = ContextInfo.subscribe_quote("600000.SH", "tick", "none",
                                     quote_callback('600000.SH'))
```

#### 3.4.2 反订阅

```python
ContextInfo.unsubscribe_quote(subId)
```

**来源**: [Python API, p.70]

#### 3.4.3 获取所有订阅

```python
data = ContextInfo.get_all_subscription()
# 返回 dict: {"stockCode": 合约代码, "period": 周期, "dividendType": 除权方式}
```

**来源**: [Python API, p.70]

### 3.5 与 handlebar 的本质区别

| 维度 | handlebar | subscribe_quote |
|------|-----------|-----------------|
| 驱动方式 | K 线周期驱动 | tick 实时推送驱动 |
| 调用频率 | 每根 K 线 1 次（尾盘每个 tick 1 次） | 每个 tick 1 次 |
| 历史回放 | 支持（逐 K 线回放） | 不支持（仅实时） |
| 交易下单 | ✅ 主要下单入口 | ❌ 不能直接下单（需回传 handlebar） |
| 适用场景 | 策略主逻辑、信号产生、回测 | 实时监控、盘口分析、高频数据采集 |
| 数据精度 | K 线级别（OHLC） | tick 级别（逐笔成交） |

### 3.6 实战示例

```python
#coding:gbk

def quote_callback(stock_code):
    """闭包工厂：为每个股票创建独立回调"""
    def callback(data):
        # data 包含 tick 级别的行情数据
        print(f"[{stock_code}] 最新价: {data.get('lastPrice')}, "
              f"成交量: {data.get('volume')}, "
              f"时间: {data.get('time')}")
    return callback

def init(ContextInfo):
    # 订阅多只股票的实时 tick 行情
    global g_subIds
    g_subIds = []
    stocks = ['600000.SH', '000001.SZ', '600519.SH']

    for code in stocks:
        subId = ContextInfo.subscribe_quote(
            code, "tick", "none", quote_callback(code)
        )
        g_subIds.append(subId)

    print(f"已订阅 {len(g_subIds)} 只股票的实时行情")

def handlebar(ContextInfo):
    # handlebar 照常运行策略主逻辑
    # subscribe_quote 的回调在后台独立运行
    pass

def stop(ContextInfo):
    """策略停止时清理订阅"""
    for subId in g_subIds:
        ContextInfo.unsubscribe_quote(subId)
    print("已反订阅所有行情")
```

---

## 4. run_time —— 定时器机制

### 4.1 基本定义

`run_time` 是 QMT 提供的**时间驱动的周期执行**机制。它独立于 K 线行情驱动，按照指定的时间间隔周期性地执行回调函数。

> *"模型回测时无效，定时器没有结束方法，会随着策略的结束而结束。另外定时器函数在一次运行前会先等待一个 period。"*
> — [Python API, p.42]

### 4.2 函数签名

```python
ContextInfo.run_time(funcName, period, startTime, market)
```

**参数说明**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `funcName` | string | 回调函数名（**字符串**，不是函数引用） |
| `period` | string | 重复调用的时间间隔，格式：`'数字+单位'` |
| `startTime` | string | 定时器第一次启动的时间，格式 `"YYYY-MM-DD HH:MM:SS"`。要立刻启动可设置历史时间 |
| `market` | string | 市场代码，如 `'SH'`（上海） |

**回调函数签名**：`def myHandler(ContextInfo):`

**来源**: [Python API, p.42]

### 4.3 period 参数详解

| period 值 | 含义 |
|-----------|------|
| `'5nSecond'` | 每 5 秒运行一次 |
| `'10nSecond'` | 每 10 秒运行一次 |
| `'500nMilliSecond'` | 每 500 毫秒运行一次 |
| `'1nDay'` | 每天运行一次 |
| `'5nDay'` | 每 5 天运行一次 |

格式：`'{数字}n{单位}'`，其中 `n` 是分隔符。

### 4.4 核心特性

1. **回测时无效**：run_time 仅在实盘/模拟交易模式下生效，回测时不会触发
2. **先等待再运行**：定时器函数在第一次运行前会先等待一个 period 时长
3. **随策略生命周期**：没有独立的 stop 方法，随策略结束而自动停止
4. **独立于 handlebar**：定时器回调与 handlebar 是两条独立的执行路径，各自独立运行

### 4.5 基本示例

```python
#coding:gbk
import time

def init(ContextInfo):
    # 设置定时器：自 2019-10-14 13:20:00 后每 5 秒运行一次
    ContextInfo.run_time("myHandlebar", "5nSecond",
                         "2019-10-14 13:20:00", "SH")

def myHandlebar(ContextInfo):
    """定时器回调函数"""
    print(f'[{time.strftime("%H:%M:%S")}] 定时器触发: hello world')

def handlebar(ContextInfo):
    # handlebar 照常运行，不受定时器影响
    pass
```

**来源**: [Python API, p.42-43]

### 4.6 run_time 的应用场景

| 场景 | period 建议 | 说明 |
|------|------------|------|
| 定时检查持仓风险 | `'30nSecond'` | 每30秒检查一次持仓盈亏 |
| 定时获取行情快照 | `'5nSecond'` | 每5秒获取一次全市场行情 |
| 收盘前强制平仓 | `'1nSecond'` | 收盘前密集检查，14:57 开始平仓 |
| 定时发送通知 | `'10nMinute'` | 每10分钟汇总一次策略状态 |
| 日内定时调仓 | `'5nMinute'` | 每5分钟判断一次调仓信号 |

### 4.7 实战示例：定时风控

```python
#coding:gbk
import time

def init(ContextInfo):
    ContextInfo.set_account('1234567890')
    # 每30秒检查一次风控
    ContextInfo.run_time("riskControl", "30nSecond",
                         "2020-01-01 09:00:00", "SH")

def riskControl(ContextInfo):
    """定时风控函数：检查持仓盈亏"""
    # 获取当前持仓
    positions = get_trade_detail_data(
        '1234567890', 'stock', 'position'
    )

    total_profit = 0
    for pos in positions:
        # 计算浮动盈亏（伪代码示意）
        profit = (pos.m_dMarketValue - pos.m_dCostPrice * pos.m_nVolume)
        total_profit += profit

    print(f"[{time.strftime('%H:%M:%S')}] 风控检查: "
          f"总浮动盈亏 = {total_profit:.2f}")

    # 亏损超过阈值，全部平仓
    if total_profit < -10000:
        print("⚠️ 触发止损，全部平仓！")
        for pos in positions:
            passorder(24, 1101, ContextInfo.accID,
                      pos.m_strInstrumentID + '.' + pos.m_strExchangeID,
                      5, -1, pos.m_nVolume,
                      '止损平仓', '风控策略', ContextInfo)

def handlebar(ContextInfo):
    pass  # 主策略逻辑可以在这里写
```

---

## 5. 三大机制协同工作

### 5.1 运行时序图

```
策略启动
  │
  ├─ init() 执行一次
  │   ├─ set_universe()      设置股票池
  │   ├─ set_account()       设置资金账号
  │   ├─ run_time()          注册定时器（不立即执行）
  │   └─ subscribe_quote()   注册行情订阅（不立即触发）
  │
  ├─ 历史K线回放阶段
  │   ├─ handlebar(Bar[0])
  │   ├─ handlebar(Bar[1])
  │   ├─ ...
  │   └─ handlebar(Bar[N-1])
  │
  └─ 实时行情阶段
      │
      ├─ tick到来 ──→ handlebar(Bar[N])  ← 每个tick触发
      │                   │
      │                   ├─ 中间tick: 逻辑执行但不保存/不发单
      │                   └─ 最后tick: 保存状态 + 产生交易信号
      │
      ├─ tick到来 ──→ subscribe_quote回调  ← 每个tick触发
      │                   │
      │                   └─ 实时处理tick数据（不能直接下单）
      │
      └─ 定时器独立运行 ──→ run_time回调  ← 按period周期触发
                          │
                          └─ 定时任务（风控/通知/调仓）
```

### 5.2 线程模型

QMT 的三条执行线运行在**不同线程**中：

| 执行线 | 线程 | 下单能力 |
|--------|------|---------|
| handlebar | 主线程（C++驱动） | ✅ 可以下单 |
| subscribe_quote 回调 | 独立线程 | ❌ 不建议直接下单 |
| run_time 回调 | 独立线程 | ⚠️ 可以下单，但需注意线程安全 |

### 5.3 跨机制数据共享

由于三条线在不同线程中，数据共享需要使用**全局变量**或**ContextInfo 自定义属性**：

```python
#coding:gbk
import threading

# 线程安全的全局数据容器
g_data = {
    'latest_tick': {},       # subscribe_quote 写入
    'risk_flag': False,      # run_time 写入
    'signal_count': 0,       # handlebar 写入
}
g_lock = threading.Lock()

def quote_callback(code):
    def callback(data):
        with g_lock:
            g_data['latest_tick'][code] = data
    return callback

def init(ContextInfo):
    ContextInfo.subscribe_quote(
        "600000.SH", "tick", "none", quote_callback("600000.SH")
    )
    ContextInfo.run_time("myTimer", "5nSecond",
                         "2020-01-01 09:00:00", "SH")

def myTimer(ContextInfo):
    """定时器：读取 tick 数据做分析"""
    with g_lock:
        tick = g_data['latest_tick'].get('600000.SH', {})
    if tick:
        print(f"定时器读取到最新价: {tick.get('lastPrice')}")

def handlebar(ContextInfo):
    """主逻辑：读取 tick + 定时器标记，产生交易信号"""
    if not ContextInfo.is_last_bar():
        return

    with g_lock:
        tick = g_data['latest_tick'].get('600000.SH', {})
        risk = g_data['risk_flag']

    if tick.get('lastPrice', 0) > 10.0 and not risk:
        passorder(23, 1101, ContextInfo.accID, '600000.SH',
                  5, -1, 100, '买入', '策略', ContextInfo)
```

---

## 6. 完整实战示例

### 6.1 场景：多维度监控交易策略

结合三种机制实现一个完整的交易策略：

- **handlebar**：日线级别的趋势跟踪，产生买卖信号
- **subscribe_quote**：tick 级别实时监控，检测异常波动
- **run_time**：定时风控，每30秒检查持仓风险

```python
#coding:gbk
"""
完整示例：三大机制协同的 QMT 交易策略

策略逻辑：
  1. handlebar 在日线上运行简单的均线突破策略
  2. subscribe_quote 实时监控 tick 行情，检测异常价格跳变
  3. run_time 定时执行风控检查
"""
import time
import threading

# ============================================================
# 全局变量
# ============================================================
g = type('', (), {})()
g.lock = threading.Lock()
g.tick_data = {}           # subscribe_quote 写入的最新 tick
g.anomaly_alert = False    # 异常标记
g.position = {}            # 当前持仓记录
g.daily_signal = ''        # 当日信号

# ============================================================
# 1. subscribe_quote：实时 tick 监控
# ============================================================
def make_tick_callback(stock_code):
    """闭包工厂：创建 tick 行情回调"""
    def callback(data):
        with g.lock:
            g.tick_data[stock_code] = {
                'price': data.get('lastPrice', 0),
                'volume': data.get('volume', 0),
                'time': data.get('time', ''),
                'bid1': data.get('bid1', 0),
                'ask1': data.get('ask1', 0),
            }

            # 异常检测：价格瞬间跳变超过2%
            old_price = g.tick_data.get('_prev_' + stock_code, 0)
            new_price = data.get('lastPrice', 0)
            if old_price > 0 and abs(new_price / old_price - 1) > 0.02:
                g.anomaly_alert = True
                print(f"⚠️ [{stock_code}] 异常跳变: "
                      f"{old_price:.2f} → {new_price:.2f}")

            g.tick_data['_prev_' + stock_code] = new_price

    return callback

# ============================================================
# 2. run_time：定时风控
# ============================================================
def risk_manager(ContextInfo):
    """定时器回调：每30秒执行一次风控检查"""
    current_time = time.strftime("%H:%M:%S")

    with g.lock:
        alert = g.anomaly_alert
        g.anomaly_alert = False  # 消费异常标记
        positions = dict(g.position)

    # 风控1：异常波动标记
    if alert:
        print(f"[{current_time}] 🔴 风控：检测到异常波动！")

    # 风控2：持仓盈亏检查
    for code, pos in positions.items():
        tick = g.tick_data.get(code, {})
        current_price = tick.get('price', 0)
        if current_price > 0 and pos['volume'] > 0:
            pnl = (current_price - pos['cost']) * pos['volume']
            pnl_pct = (current_price / pos['cost'] - 1) * 100
            print(f"[{current_time}] {code}: "
                  f"成本{pos['cost']:.2f} → 现价{current_price:.2f}, "
                  f"盈亏{pnl:+.2f} ({pnl_pct:+.2f}%)")

            # 止损：亏损超过3%
            if pnl_pct < -3:
                print(f"[{current_time}] 🛑 {code} 触发止损！")
                passorder(24, 1101, ContextInfo.accID,
                          code, 5, -1, pos['volume'],
                          f'止损{pnl_pct:.1f}%', '风控', ContextInfo)
                pos['volume'] = 0

    # 风控3：快到收盘时检查
    if current_time >= "14:55:00":
        for code, pos in positions.items():
            if pos['volume'] > 0:
                print(f"[{current_time}] ⏰ 临近收盘，{code} 强制平仓")
                passorder(24, 1101, ContextInfo.accID,
                          code, 5, -1, pos['volume'],
                          '收盘平仓', '风控', ContextInfo)

# ============================================================
# 3. handlebar：主策略逻辑
# ============================================================
def init(ContextInfo):
    """初始化"""
    # 设置股票池
    ContextInfo.set_universe(['600000.SH', '000001.SZ'])
    # 设置资金账号
    ContextInfo.set_account('1234567890')

    # 注册定时器：每30秒执行风控
    ContextInfo.run_time("risk_manager", "30nSecond",
                         "2020-01-01 09:00:00", "SH")

    # 订阅实时 tick 行情
    g.subIds = []
    for code in ['600000.SH', '000001.SZ']:
        subId = ContextInfo.subscribe_quote(
            code, "tick", "none", make_tick_callback(code)
        )
        g.subIds.append(subId)

    print("策略初始化完成")

def handlebar(ContextInfo):
    """主策略：日线均线突破"""

    # 只在最后一根K线的新tick上执行（避免重复）
    if not ContextInfo.is_new_bar() or not ContextInfo.is_last_bar():
        return

    stock = ContextInfo.stockcode
    barpos = ContextInfo.barpos

    # 获取历史数据计算均线（需要足够的历史数据）
    if barpos < 20:
        return

    # 获取收盘价序列（这里用伪代码示意）
    # 实际中需要使用 get_history_data 获取完整历史
    close_price = ContextInfo.get_close_price()

    # 简单策略：收盘价站上5日均线则买入
    # (实际需要自己计算均线，这里仅示意)

    # 检查是否已持仓
    with g.lock:
        pos = g.position.get(stock, {'volume': 0})

    # 买入信号
    if pos['volume'] == 0:
        # 假设有买入信号
        print(f"📈 [{stock}] 产生买入信号")
        passorder(23, 1101, ContextInfo.accID,
                  stock, 5, -1, 100,
                  '均线突破买入', '主策略', ContextInfo)

        with g.lock:
            g.position[stock] = {
                'volume': 100,
                'cost': close_price
            }

    # 画图
    ContextInfo.paint('close', close_price, -1, 0, 'white', 'noaxis')

def stop(ContextInfo):
    """策略停止时清理"""
    for subId in getattr(g, 'subIds', []):
        ContextInfo.unsubscribe_quote(subId)
    print("策略已停止，订阅已清理")
```

---

## 附录：关键注意事项汇总

### A. handlebar 注意事项

| # | 注意点 | 说明 |
|---|--------|------|
| 1 | 必须定义 | `init()` 和 `handlebar()` 必须存在，否则策略无法运行 [p.31] |
| 2 | ContextInfo 不要存自定义状态 | ContextInfo 随 bar 切换重置，用全局变量存储跨 bar 状态 [p.31] |
| 3 | 日线以上周期当日无法下单 | 除非使用 `quickTrade=1` 或 `do_order()` [p.9] |
| 4 | 最后 tick 才判定信号 | 中间 tick 的条件满足是"虚信号"，不会发单 [p.10] |
| 5 | 编码必须声明 | 第一行必须写 `#coding:gbk` [p.30] |

### B. subscribe_quote 注意事项

| # | 注意点 | 说明 |
|---|--------|------|
| 1 | 仅支持 tick 周期 | 暂只支持分笔线周期 [p.69] |
| 2 | 分笔数据不复权 | period 为 tick 时期，返回数据均为不复权 [p.69] |
| 3 | 回调不能直接下单 | 回调在独立线程中，下单需回传到 handlebar 或通过 run_time |
| 4 | 闭包模式 | 用闭包传递股票代码到回调函数 [p.70] |
| 5 | 需要手动反订阅 | 策略停止时建议调用 `unsubscribe_quote` 清理 |

### C. run_time 注意事项

| # | 注意点 | 说明 |
|---|--------|------|
| 1 | 回测无效 | 仅在实盘/模拟交易模式下生效 [p.42] |
| 2 | 先等待再运行 | 第一次运行前会先等待一个 period 时长 [p.42] |
| 3 | funcName 是字符串 | 参数是函数名的**字符串**，不是函数引用 [p.42] |
| 4 | 不能手动停止 | 没有独立 stop 方法，随策略结束而自动结束 [p.42] |
| 5 | 立刻启动的技巧 | startTime 设置为历史时间可实现立刻启动 [p.42] |
| 6 | 线程安全 | 定时器在独立线程运行，共享数据需加锁 |

---

## 参考来源

- 迅投 QMT 极速策略交易系统 — Python API 使用文档 (171页)
- 所有引用标记 `[p.XX]` 对应 Python API 文档页码

---

> **文档生成时间**: 2026-07-02
> **基于**: 迅投 QMT 官方 Python API 文档 v2.20
