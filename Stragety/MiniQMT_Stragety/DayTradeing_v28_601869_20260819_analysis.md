# DayTrading v28 成交分析报告

> 标的: 长飞光纤 601869.SH | 日期: 2026-08-19 | 策略版本: v28

---

## 一、成交记录汇总

当日共发生 **4 笔成交**，均来自短线动量反转机制 (MOM) 和正T机制 (FWD-T)：

| # | 时间 | 方向 | 机制 | 价格 | 数量 | 金额 | 持仓变化 | 现金变化 |
|---|------|------|------|------|------|------|----------|----------|
| 1 | 10:34:40 | **买入** | FWD-T buy | ¥352.24 | 100股 | ¥35,224 | 200→300 | -35,224 |
| 2 | 10:42:37 | **卖出** | MOM short | ¥355.01 | 100股 | ¥35,501 | 300→200 | +35,501 |
| 3 | 10:50:41 | **买入** | MOM buyback | ¥354.99 | 100股 | ¥35,499 | 200→300 | -35,499 |
| 4 | - | **未成交** | MOM buyback | - | 100股 | - | 300→300 | 0 (现金不足) |

### 交易盈亏

| 腿 | 卖出价 | 买回价 | 股数 | 毛利 |
|----|--------|--------|------|------|
| MOM 短线反T | ¥355.01 | ¥354.99 | 100 | **+¥2** (几乎持平) |
| MOM 短线反T(第2腿) | ¥355.01 | 未买回 | 100 | **未平仓** ❌ |
| FWD-T 正T | ¥352.24 买入 | 未卖出 | 100 | **未平仓** (持仓过夜) |

---

## 二、成交流程图

```mermaid
sequenceDiagram
    participant M as 市场
    participant FT as FWD-T机制
    participant MOM as MOM短线机制
    participant ACC as 账户

    Note over M,ACC: 09:30:02 开盘 ¥364.42

    rect rgb(255, 240, 240)
    Note over FT: 09:30~10:34 FWD-T反复触发但现金不足
    M->>FT: 09:30:04 价格跌至¥364.42 (need -3.16%)
    FT->>ACC: 检查现金: planned 100, actual 96
    FT-->>M: ❌ SKIP 现金不足
    Note over FT: 此模式重复约80次...<br/>价格从¥364→¥352持续下跌<br/>每次 bounce 触发买入检查<br/>均因现金不足跳过
    end

    rect rgb(240, 255, 240)
    Note over FT: 10:34:40 终于成交
    M->>FT: 10:34:40 低点¥352.00 回升0.11%→¥352.38
    FT->>ACC: 检查现金: planned 100, actual OK
    FT->>M: 下单买入 ¥352.38×100股
    M-->>ACC: ✅ 成交 ¥352.24×100股 = ¥35,224
    Note over ACC: 持仓: 200→300股<br/>现金: 35,336→约90元
    end

    rect rgb(240, 240, 255)
    Note over MOM: 10:41:28 MOM短线触发
    M->>MOM: 2分钟涨+1.00% (ATR自适应阈值)
    MOM->>MOM: 进入MOM_SPIKING 冲高回落监测
    M->>MOM: 10:42:36 峰值¥355.45 回落0.12%→¥355.01
    MOM->>M: 下单卖出 ¥355.01×100股
    M-->>ACC: ✅ 成交 ¥355.01×100股 = ¥35,501
    Note over ACC: 持仓: 300→200股<br/>现金: 约90→35,591元
    Note over MOM: MOM短线反T腿#1建立<br/>买回触发 ≤¥350.75 (跌1.2%)
    end

    rect rgb(255, 255, 240)
    Note over MOM: 10:42~10:50 等待买回
    M->>MOM: 10:49:29 委托买入¥354.99 (系统自动)
    M-->>ACC: 10:50:41 ✅ 成交 ¥354.99×100股 = ¥35,499
    Note over ACC: 持仓: 200→300股<br/>现金: 约35,591→92元
    Note over MOM: MOM短线反T腿#1平仓 (毛利+¥2)
    end

    rect rgb(255, 230, 230)
    Note over MOM: 11:19:07 MOM短线反T腿#2触发买回
    M->>MOM: 价格¥350.66 ≤ ¥350.75 (跌1.2%)
    MOM->>ACC: 检查现金: planned 100, actual 0
    MOM-->>M: ❌ SKIP 现金不足
    Note over MOM: 此模式重复400+次直到日志结束...<br/>每次回升触发买回检查<br/>均因现金=0跳过<br/>MOM腿#2始终未平仓
    end
```

---

## 三、现金不足根因分析

```mermaid
flowchart TD
    A[初始现金 ¥35,336] --> B[09:30~10:34<br/>FWD-T反复检查买入<br/>实际可买96~99股 < 100股]
    B --> C[10:34:40 FWD-T买入<br/>¥352.24×100 = ¥35,224]
    C --> D[现金 ≈ ¥112]
    D --> E[10:42:37 MOM卖出<br/>¥355.01×100 = +¥35,501]
    E --> F[现金 ≈ ¥35,591]
    F --> G[10:50:41 MOM买回<br/>¥354.99×100 = ¥35,499]
    G --> H[现金 ≈ ¥92]
    H --> I{MOM腿#2买回?}
    I -->|需要¥35,000+| J[现金仅¥92<br/>❌ 无法买回]
    J --> K[反复尝试400+次<br/>全部SKIP]

    style A fill:#4CAF50,color:#fff
    style D fill:#FF9800,color:#fff
    style F fill:#4CAF50,color:#fff
    style H fill:#f44336,color:#fff
    style J fill:#f44336,color:#fff
    style K fill:#f44336,color:#fff
```

### 核心问题

**MOM短线机制的两笔交易在时间上交叉**：
1. **MOM腿#2** (FWD-T正T): 10:34:40 买入100股 → 花费¥35,224
2. **MOM腿#1** (反T): 10:42:37 卖出100股 → 收回¥35,501
3. **MOM腿#1买回**: 10:50:41 买回100股 → 花费¥35,499

MOM卖回的¥35,501被立即用于买回腿#1 (¥35,499)，**没有剩余现金**给腿#2的后续操作。当MOM腿#2触发买回时，账户现金已接近0。

---

## 四、量化策略整体流程图

### 4.1 主状态机 + MOM短线机制 双轨并行

```mermaid
stateDiagram-v2
    direction TB

    state "每日初始化" as INIT
    state "盘前信号计算" as PRE

    INIT --> PRE: 09:25 计算日线信号
    PRE --> IDLE: 09:30 开盘

    state "主机制 (日线信号驱动)" as MAIN {
        direction TB
        state "IDLE 空闲监控" as IDLE
        state "SPIKING 冲高回落监测" as SPIKING
        state "SOLD 等待买回" as SOLD
        state "DIPPING 探底回升买回" as DIPPING
        state "BT_DIPPING 探底回升买入" as BT_DIPPING
        state "BT_BOUGHT 等待卖回" as BT_BOUGHT
        state "BT_SPIKING 冲高回落卖出" as BT_SPIKING
        state "DONE 本轮完成" as DONE

        [*] --> IDLE
        IDLE --> SPIKING: 反T: 价格≥卖点触发价
        IDLE --> BT_DIPPING: 正T: 价格≤买点触发价
        SPIKING --> SOLD: 冲高回落确认(卖出)
        SOLD --> DIPPING: 价格≤买回目标
        SOLD --> SOLD: 价格未到/阶梯加卖
        DIPPING --> DONE: 探底回升买回成功
        DIPPING --> SOLD: 买回失败
        BT_DIPPING --> BT_BOUGHT: 探底回升买入成功
        BT_BOUGHT --> BT_SPIKING: 价格≥卖回目标
        BT_BOUGHT --> BT_DIPPING: 阶梯加买(跌1.5%)
        BT_SPIKING --> DONE: 冲高回落卖出成功
        DONE --> IDLE: 可继续交易
    }

    state "MOM短线机制 (2分钟事件驱动)" as MOM {
        direction TB
        state "MOM_IDLE 短线空闲" as MOM_IDLE
        state "MOM_SPIKING 冲高回落卖出" as MOM_SPIKING
        state "MOM_SOLD 等待买回" as MOM_SOLD
        state "MOM_DIPPING 探底回升买回" as MOM_DIP
        state "MOM_BT_DIPPING 探底回升买入" as MOM_BT_DIP
        state "MOM_BT_BOUGHT 等待卖回" as MOM_BT_BOUGHT
        state "MOM_BT_SPIKING 冲高回落卖出" as MOM_BT_SPIKE

        [*] --> MOM_IDLE
        MOM_IDLE --> MOM_SPIKING: 2分钟涨≥2×ATR
        MOM_IDLE --> MOM_BT_DIP: 2分钟跌≥2×ATR
        MOM_SPIKING --> MOM_SOLD: 回落确认(卖出1手)
        MOM_SOLD --> MOM_DIP: 跌1.2%触发买回
        MOM_DIP --> MOM_IDLE: 探底回升买回成功
        MOM_BT_DIP --> MOM_BT_BOUGHT: 探底回升买入成功
        MOM_BT_BOUGHT --> MOM_BT_SPIKE: 涨1.5%触发卖回
        MOM_BT_SPIKE --> MOM_IDLE: 冲高回落卖出成功
        MOM_SOLD --> MOM_IDLE: 尾盘强制买回
        MOM_BT_BOUGHT --> MOM_IDLE: 尾盘强制卖出
    }
```

### 4.2 信号计算流程

```mermaid
flowchart TD
    A[每日09:25] --> B[获取历史数据<br/>60日K线 OHLCV]
    B --> C[compute_signal<br/>计算日线信号]
    C --> D{趋势判断}

    D -->|strong_bull/weak_bull| E[牛市信号]
    D -->|sideways| F[震荡信号]
    D -->|bear| G[熊市信号]

    E --> H[计算 ATR/RSI/成交量比]
    F --> H
    G --> H

    H --> I[输出: trend, atr_pct, rsi,<br/>vol_ratio, sell_mult]
    I --> J{仓位检查}

    J -->|反T: 可卖≥1手| K[✅ REV-T 启用<br/>sell_trigger = open×sell_mult]
    J -->|反T: 可卖<1手| L[❌ REV-T 禁用]
    J -->|正T: 现金≥1手| M[✅ FWD-T 启用<br/>buy_trigger = max(floor, trail)]
    J -->|正T: 现金<1手| N[❌ FWD-T 禁用]

    K --> O[输出每日简报]
    L --> O
    M --> O
    N --> O
```

### 4.3 反T完整流程 (REV-T)

```mermaid
flowchart TD
    A[IDLE 空闲] -->|价格≥sell_trigger| B[SPIKING<br/>冲高回落监测]
    B -->|继续涨| B1[更新peak_price]
    B1 --> B
    B -->|回落≥PULLBACK_PCT| C[卖出信号]
    C --> D[下单卖出<br/>_submit_order]
    D -->|FILLED| E[SOLD 等待买回<br/>记录sell_fill_price]
    D -->|PARTIAL| E1[部分成交<br/>按实际股数入腿]
    D -->|TIMEOUT/SKIP| A

    E -->|阶梯加卖条件| B2[追加SPIKING<br/>卖价+1.5%]
    B2 --> B

    E -->|紧急止损| F[价格≥卖价+3%<br/>强制买回]
    E -->|正常买回| G[价格≤buyback_target<br/>进入DIPPING]

    G -->|回升≥BOUNCE_PCT| H[买回信号]
    H --> I[下单买回]
    I -->|成功| J[DONE<br/>记录毛利]
    I -->|失败| E

    F --> K[强制买回]
    K --> J

    J -->|可继续| A
    J -->|达上限| L[当日结束]
```

### 4.4 正T完整流程 (FWD-T)

```mermaid
flowchart TD
    A[IDLE 空闲] -->|价格≤buy_trigger| B[BT_DIPPING<br/>探底回升监测]
    B -->|继续跌| B1[更新dip_price]
    B1 --> B
    B -->|回升≥BOUNCE_PCT| C[买入信号]
    C --> D[下单买入<br/>_submit_order]
    D -->|FILLED| E[BT_BOUGHT 等待卖回<br/>记录bt_buy_fill_price]
    D -->|PARTIAL| E1[部分成交]
    D -->|TIMEOUT/SKIP| A

    E -->|阶梯加买条件| B2[追加BT_DIPPING<br/>买价-1.5%]
    B2 --> B

    E -->|止损| F[价格≤均价-STOP_LOSS%<br/>强制卖出]
    E -->|正常卖回| G[价格≥sellback_target<br/>进入BT_SPIKING]

    G -->|回落≥PULLBACK_PCT| H[卖出信号]
    H --> I[下单卖出]
    I -->|成功| J[DONE<br/>记录毛利]
    I -->|失败| E

    F --> K[强制卖出]
    K --> J

    J -->|可继续| A
    J -->|达上限| L[当日结束]
```

### 4.5 MOM短线机制流程 (2分钟事件驱动)

```mermaid
flowchart TD
    A[MOM_IDLE] -->|2分钟涨≥2×ATR| B[MOM_SPIKING<br/>冲高回落卖出]
    A -->|2分钟跌≥2×ATR| C[MOM_BT_DIPPING<br/>探底回升买入]

    B -->|回落≥PULLBACK_PCT| D[卖出1手]
    D --> E[MOM_SOLD<br/>卖价记录]

    E -->|跌1.2%| F[MOM_DIPPING<br/>探底回升买回]
    E -->|反涨3%+紧急开关开| G[紧急止损买回]
    E -->|尾盘| H[强制买回]

    F -->|回升≥BOUNCE_PCT| I[买回1手]
    I --> J[MOM_IDLE<br/>记录毛利]
    G --> J
    H --> J

    C -->|回升≥BOUNCE_PCT| K[买入1手]
    K --> L[MOM_BT_BOUGHT<br/>买价记录]

    L -->|涨1.5%| M[MOM_BT_SPIKING<br/>冲高回落卖出]
    L -->|反跌止损| N[止损卖出]
    L -->|尾盘| O[强制卖出]

    M -->|回落≥PULLBACK_PCT| P[卖出1手]
    P --> J
    N --> J
    O --> J
```

---

## 五、当日价格走势 & 关键事件时间线

```
时间        价格     事件
────────────────────────────────────────────────────────────
09:30:02   ¥364.42  🟢 开盘, BEAR信号, ATR 11.1%
09:30:04   ¥364.42  🔄 FWD-T触发 (need -3.16%) — 现金不足 SKIP
  ...              (FWD-T反复触发约80次, 全部SKIP)
09:45:46   ¥361.30  📉 MOM dip 2min -1.00% — 现金不足 SKIP
10:07:46   ¥358.68  📉 MOM dip 2min -1.00% — 现金不足 SKIP
10:29:42   ¥355.21  📉 MOM dip 2min -1.00% — 现金不足 SKIP
10:34:40   ¥352.24  ✅ FWD-T 买入 100股 @ ¥352.24 (现金终于够了!)
10:41:28   ¥354.49  📈 MOM spike 2min +1.00%
10:42:36   ¥355.01  ✅ MOM 卖出 100股 @ ¥355.01 (冲高回落)
10:50:41   ¥354.99  ✅ MOM 买回 100股 @ ¥354.99 (腿#1平仓, +¥2)
11:19:07   ¥350.66  🔄 MOM买回触发 (≤¥350.75) — 现金=0 SKIP
  ...              (MOM买回反复触发400+次, 全部SKIP)
11:28:28   ¥348.51  📍 日志结束, MOM腿#2仍未平仓
```

---

## 六、问题总结与改进建议

### 问题

| # | 问题 | 影响 |
|---|------|------|
| 1 | **MOM机制两腿交叉导致现金锁死** | FWD-T买入花光现金 → MOM卖出回笼 → 立即被MOM买回消耗 → 腿#2无法买回 |
| 2 | **现金不足时高频重试** | 11:23~11:28 期间约400次买回尝试全部SKIP，浪费大量日志和CPU |
| 3 | **FWD-T长时间无法成交** | 09:30~10:34 约64分钟内80+次SKIP，错失低点买入机会 |

### 改进建议

1. **MOM机制增加现金预检**: 在进入MOM_SOLD状态前，确保账户有足够现金用于后续买回，或将MOM腿#2与腿#1解耦（不同手数）
2. **增加SKIP冷却**: 连续N次SKIP后进入冷却期（如5分钟），避免高频无效重试
3. **FWD-T买入手数自适应**: 当现金不足1手时，尝试买入实际可买股数（如96股），而非直接SKIP
