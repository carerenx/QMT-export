# QMT 极速策略交易系统 — 完整使用教程

## 1. 什么是 QMT？

迅投 QMT 极速策略交易系统是一款集**行情显示、投资研究、产品交易**于一身，并自带完整风控系统的量化交易平台。支持股票、期货、融资融券、组合交易等多种交易类型。策略开发支持 **Python** 和 **VBA** 两种语言。[系统功能, p.4]

**系统要求：** Windows 10 64-bit、i7+ CPU、8GB RAM、SSD 200G+。[系统功能, p.5]

---

## 2. 登录与界面总览

登录时有三种模式可选 [系统功能, p.6-8]：

| 模式 | 说明 |
|------|------|
| **行情+交易** | 同时查看行情并进行交易 |
| **独立交易** | 只进行交易，不看行情 |
| **独立行情** | 仅查看行情 |
| **极简模式** | 精简界面，专注下单 |

系统有三大功能板块 [系统功能, p.9-11]：

- **我的板块** — 模型研究、模型交易、策略回测的主入口
- **行情板块** — 实时行情报价、个股分析、Level2 数据、技术指标
- **交易板块** — 股票/期货/信用/组合交易、算法交易、篮子交易

---

## 3. 策略开发核心概念（必读！）

### 3.1 两个必须实现的函数

每个 Python 策略**必须**包含 `init()` 和 `handlebar()` [Python API, p.6]：

```python
#coding:gbk

def init(ContextInfo):
    """初始化函数 — 策略启动时只执行一次"""
    pass

def handlebar(ContextInfo):
    """行情事件函数 — 每根K线执行一次（实时行情中每个tick执行一次）"""
    pass
```

### 3.2 Bar 的概念

- 一根 K 线就是一个 **Bar**，由多个 tick（分笔）组成
- 策略逐 K 线运行：从第 0 根 K 线一直运行到最后一根
- 选日线周期 → `handlebar()` 每天调用一次；选分钟线 → 每分钟调用一次 [Python API, p.6]

### 3.3 交易信号机制（非常重要！）

```
有效信号：在最新 Bar 的 handlebar 里调用交易函数 → 信号有效
无效信号：在历史 Bar 的 handlebar 里调用交易函数 → 信号被忽略
```

- **非快速交易（默认）：** 当前 Bar 走完后产生信号，在下一根 Bar 的第一个 tick 发出委托
- **快速交易（quickTrade=1）：** 最后一根 Bar 内立即发出委托（日线策略必备！）[Python API, p.3-4]

### 3.4 模拟信号 vs 实盘信号

- **模拟信号：** 模型交易中以"模拟"模式运行 → 委托不会发送到柜台
- **实盘信号：** 模型交易中以"实盘"模式运行 → 委托真实发送到柜台 [Python API, p.4]

---

## 4. 创建一个策略 — 完整步骤

### Step 1: 新建模型

在「我的板块」→「模型研究」中，点击**"新建模型"** → 选择**"Python 模型"**。弹出一个代码编辑器。[系统功能, p.15]

### Step 2: 设置基本信息

在右侧面板设置 [系统功能, p.18-20]：

| 参数 | 说明 |
|------|------|
| **名称** | 策略名称 |
| **默认周期** | 日线/分钟线/周线等 |
| **默认品种** | 如 `000300.SH` |
| **复权方式** | 前复权/后复权/不复权 |
| **刷新间隔** | 策略每隔多久运行一次 |
| **快速计算** | 设为 N 则只计算最近 N 根 K 线 |

### Step 3: 设置回测参数

| 参数 | 说明 | 代码中对应 |
|------|------|-----------|
| 初始资金 | 回测虚拟账号资金 | `ContextInfo.capital = 10000000` |
| 开始/结束时间 | 回测区间 | `ContextInfo.start / .end` |
| 滑点 | 模拟冲击成本 | `ContextInfo.set_slippage()` |
| 手续费 | 佣金、印花税等 | `ContextInfo.set_commission()` |

### Step 4: 编写代码 → 编译 → 回测 → 实盘

1. 编写代码 → 点击**"编译"**检查语法
2. 编译通过 → 点击**"回测"**查看策略绩效
3. 回测满意 → 点击**"转到实盘交易"**或到「模型交易」新建模拟/实盘交易 [系统功能, p.23-34]

---

## 5. 核心 API 函数详解

### 5.1 ContextInfo — 策略全局环境对象

`ContextInfo` 是 `init()` 和 `handlebar()` 的必传参数，包含了所有与系统交互的方法 [Python API, p.32]。

#### 设置股票池

```python
def init(ContextInfo):
    stocklist = ['000300.SH', '000004.SZ', '600519.SH']
    ContextInfo.set_universe(stocklist)  # 设定股票池
```

[Python API, p.32]

#### 设置交易账号

```python
def init(ContextInfo):
    ContextInfo.set_account('6000000223')  # 资金账号
    # 可多次调用设置多个账号
```

[Python API, p.32]

#### 设置回测参数

```python
def init(ContextInfo):
    ContextInfo.capital = 10000000        # 初始资金 1000万
    ContextInfo.start = '2020-01-01 09:30:00'
    ContextInfo.end = '2025-12-31 15:00:00'
    ContextInfo.set_slippage(1, 0.01)     # 滑点 1%
```

[Python API, p.33]

### 5.2 获取行情数据

#### get_history_data() — 获取股票池历史数据

```python
用法：ContextInfo.get_history_data(len, period, field, dividend_type=0, skip_paused=True)

# 获取股票池所有股票最近 20 日收盘价
def handlebar(ContextInfo):
    his = ContextInfo.get_history_data(20, '1d', 'close')
    for code, prices in his.items():
        if len(prices) >= 20:
            ma20 = sum(prices) / len(prices)
            print(f"{code} MA20={ma20:.2f}")
```

参数说明 [Python API, p.49-50]：

- `len`：获取的数据长度
- `period`：`'1d'`(日), `'1m'`(1分), `'5m'`(5分), `'1h'`(小时), `'1w'`(周), `'1mon'`(月) 等
- `field`：`'open'`, `'high'`, `'low'`, `'close'`, `'quoter'`
- `dividend_type`：`0`不复权, `1`前复权, `2`后复权

#### get_market_data() — 获取任意股票行情（更灵活）

```python
用法：ContextInfo.get_market_data(fields, stock_code=[], start_time='', end_time='',
                                  skip_paused=True, period='follow', dividend_type='follow',
                                  count=-1)

# 获取指定股票行情
def handlebar(ContextInfo):
    df = ContextInfo.get_market_data(
        ['close', 'open', 'high', 'low', 'volume'],
        stock_code=['600519.SH', '000858.SZ'],
        start_time='20240101', end_time='20240131',
        period='1d'
    )
    # df['close'] 是 DataFrame，列是股票代码，行是时间
```

[Python API, p.50-51]

> **两者区别：** `get_history_data()` 必须先设置股票池，返回 dict；`get_market_data()` 可指定任意股票，返回 DataFrame，功能更强。[Python API, p.62]

### 5.3 passorder() — 下单交易（最核心的函数）

```python
passorder(opType, orderType, accountid, orderCode, prType, modelprice, volume,
          [strategyName, quickTrade, userOrderId], ContextInfo)
```

**参数详解** [Python API, p.80-85]：

#### opType（操作类型）

| 值 | 含义 | 值 | 含义 |
|----|------|----|------|
| **23** | **股票买入** | **24** | **股票卖出** |
| 27 | 融资买入 | 28 | 融券卖出 |
| 0 | 期货开多 | 3 | 期货开空 |
| 50 | 期权买入开仓 | 51 | 期权卖出平仓 |

#### orderType（下单方式）

| 值 | 含义 |
|----|------|
| **1101** | 单股、单账号、按股/手下单 |
| 1102 | 单股、单账号、按金额(元)下单 |
| 1113 | 单股、单账号、按总资产比例下单 |
| 1201 | 单股、账号组、按股/手下单 |

#### prType（选价类型）

| 值 | 含义 | 值 | 含义 |
|----|------|----|------|
| **5** | **最新价** | **11** | **指定价(模型价)** |
| 4 | 卖一价 | 6 | 买一价 |
| 14 | 对手价 | 12 | 涨跌停价 |

#### 完整示例

```python
def handlebar(ContextInfo):
    target = '600519.SH'

    # 1. 以最新价买入 100 股
    passorder(23, 1101, 'test', target, 5, -1, 100, ContextInfo)

    # 2. 以指定价 1800 元买入 100 股
    passorder(23, 1101, 'test', target, 11, 1800, 100, ContextInfo)

    # 3. 股票卖出 100 股，最新价
    passorder(24, 1101, 'test', target, 5, -1, 100, ContextInfo)

    # 4. 快速交易模式（最后一根K线立即下单，日线策略必备！）
    passorder(23, 1101, 'test', target, 5, -1, 100, 'myStrategy', 1, ContextInfo)
    #                                                                  ↑ quickTrade=1 立即下单
```

[Python API, p.85]

---

## 6. 完整策略框架示例

基于工程中 [低回撤多因子趋势跟踪策略.py](../MyPy-Q/低回撤多因子趋势跟踪策略.py) 的结构，这是一个标准的 QMT 策略模板：

```python
#coding:gbk
"""
双均线趋势跟踪策略 — 完整示例
"""

# === 全局状态（模块级变量，跨 Bar 持久化） ===
g_positions = {}     # {code: {shares, entry_price, bars_held}}
g_capital = 1000000  # 初始资金


def init(ContextInfo):
    """策略初始化 — 只运行一次"""
    # 1. 设置股票池
    stocklist = ['000300.SH', '600519.SH', '000858.SZ', '601318.SH']
    ContextInfo.set_universe(stocklist)

    # 2. 设置交易账号
    ContextInfo.set_account('your_account_id')

    # 3. 设置回测参数（只在回测模式生效）
    ContextInfo.capital = 10000000
    ContextInfo.start = '2020-01-01 09:30:00'
    ContextInfo.end = '2025-12-31 15:00:00'
    ContextInfo.set_slippage(1, 0.001)  # 滑点 0.1%

    # 4. 设置手续费
    ContextInfo.set_commission(0, [0.0003, 0.001, 0, 0.0003, 0.001, 0, 5])


def handlebar(ContextInfo):
    """核心策略逻辑 — 每根K线执行一次"""
    # 1. 获取行情数据
    close_dict = ContextInfo.get_history_data(60, '1d', 'close')

    # 2. 遍历股票池，生成交易信号
    for code in ContextInfo.get_universe():
        prices = close_dict.get(code, [])
        if len(prices) < 60:
            continue

        # 计算均线
        ma5 = sum(prices[-5:]) / 5
        ma20 = sum(prices[-20:]) / 20
        current_price = prices[-1]

        # 3. 金叉买入信号
        if ma5 > ma20 and code not in g_positions:
            buy_volume = int(g_capital * 0.1 / current_price / 100) * 100
            if buy_volume >= 100:
                passorder(23, 1101, 'test', code, 5, -1, buy_volume,
                          '双均线策略', 1, ContextInfo)
                g_positions[code] = {'price': current_price, 'bars': 0}

        # 4. 死叉卖出信号
        if ma5 < ma20 and code in g_positions:
            passorder(24, 1101, 'test', code, 5, -1,
                      g_positions[code]['shares'], '双均线策略', 1, ContextInfo)
            del g_positions[code]

    # 5. 打印调试信息
    print(f"BarPos: {ContextInfo.barpos}, 持仓数: {len(g_positions)}")
```

---

## 7. 策略运行方式

### 方式一：模型研究中运行（回测/模拟）

1. 在代码编辑器点击**"编译"**
2. 点击**"回测"** → 查看绩效分析（净值曲线、夏普比率、最大回撤等）
3. 点击**"运行"** → 在当前 K 线图上模拟运行，副图显示信号 [系统功能, p.23-27]

### 方式二：模型交易中运行（模拟/实盘）

1. 切换到「模型交易」标签页
2. 点击**"新建模拟交易"** → 选择策略、标的、周期
3. 点击**"运行"**启动策略
4. 可在"运行模式"列切换**模拟** ↔ **实盘** [系统功能, p.32-35]

---

## 8. 关键注意事项

### 8.1 日线策略必须用快速交易！

日线策略中，Bar 走完时已经是收盘，默认模式下信号会在**下个交易日开盘**才发出。要让当日立即下单，必须设置 `quickTrade=1` [Python API, p.9]：

```python
passorder(23, 1101, 'test', code, 5, -1, 100, '策略名', 1, ContextInfo)
#                                                      ↑ quickTrade=1 必须！
```

### 8.2 全局变量要用模块级变量，不要挂在 ContextInfo 上

```python
# ✅ 正确：模块级变量在 handlebar 之间持久化
my_positions = {}

# ❌ 错误：ContextInfo 会在每根 Bar 重置
def handlebar(ContextInfo):
    ContextInfo.my_positions = {}  # 每次都被清空！
```

### 8.3 股票代码格式

QMT 中的股票代码格式为 `代码.市场后缀`：

- **上海**：`600519.SH`、`000300.SH`
- **深圳**：`000858.SZ`、`002415.SZ`

### 8.4 文件编码

策略文件第一行**必须**写 `#coding:gbk`，否则中文注释可能导致编译失败。

---

## 9. 学习路径建议

1. **先跑示例** → 在「模型研究」中打开系统预置的 Python 示例（如"PY简单示例"、"双均线实盘示例"），编译运行看效果
2. **改参数** → 修改示例中的均线周期、股票池等参数，观察回测结果变化
3. **写简单策略** → 从双均线交叉策略开始，逐步加入止盈止损
4. **加入风控** → 加入仓位管理、ATR 止损、移动止盈
5. **模拟交易** → 在「模型交易」中以模拟模式运行，观察信号是否符合预期
6. **小资金实盘** → 确认无误后，切换为实盘模式，用小资金验证
