# MOM 波动自适应回撤确认机制

适用对象：`Stragety/MiniQMT_Stragety/DayT/DayTradeing_v41_stragety_miniqmt.py` 中的 MOM 独立短线腿。本文描述策略自身逻辑，不是 QMT 的内置订单或指标功能。

## 它解决什么问题

MOM 在识别到短时上涨或下跌后，不立即在第一个反向 tick 下单，而是先进入“冲高回落”或“探底回升后的卖回”监测状态。自适应回撤确认决定：从这段行情的最高价回落多少，才承认动能可能已经转弱。

固定的回撤比例很难适合所有盘面：

- 波动很小的时候，门槛过低会把盘口噪声当成反转；
- 波动很大的时候，门槛过高会把已经明显的回落拖延太久；
- 高价股即使只有几分钱或几角的跳动，换算成百分比也可能产生误触发。

开启该机制后，MOM 使用最近十分钟的分钟级实际波幅估算确认门槛，并限制在 0.35% 到 0.60% 之间。它的目标是减少噪声卖出，同时限制等待峰值回撤的最大让利。

## 计算方式

策略把最近十分钟的价格按一分钟分桶，计算每个桶的 `最高价 - 最低价`，取这些分钟区间的中位数作为近似 ATR。设：

- `ATR10m`：近十分钟分钟高低区间的中位数；
- `P`：开始确认时的参考价；在实际判断中为实时更新后的峰值；
- `k`：`MOM_PULLBACK_ATR_MULT`。

原始回撤比例为：

```text
raw_pullback = k × ATR10m / P
```

最终用于确认的比例为：

```text
pullback_threshold = clamp(raw_pullback, 0.35%, 0.60%)
```

当前参数中 `k = 0.50`。若 ATR 数据尚不足，策略直接使用下限 0.35%，而不是把阈值降到零。

## 在交易状态机中的作用

```text
MOM 检测到短时动量
        ↓
记录当前价为峰值，并冻结 pullback_threshold
        ↓
价格创新高 → 更新峰值；阈值保持不变
        ↓
(峰值 - 当前价) / 峰值 ≥ pullback_threshold
        ↓
确认回撤
  ├─ MOM 反T：提交 MOM 卖出
  └─ MOM 正T卖回：进入既有冷却确认，再决定卖回
```

“冻结”很重要：阈值在进入 `MOM_SPIKING` 或 `MOM_BT_SPIKING` 时写入 `mom_pullback_pct`。后续即使价格创新高或 ATR 继续变化，同一笔监测仍使用原阈值。这样一次交易的确认标准可复现，不会在跟踪过程中被新波动反复改写。

该机制只控制 MOM 的回撤确认，不改变主 REV-T 的日线触发价。`MOM_REV_PRIORITY_ENABLED` 是另一个独立开关：当 MOM 靠近主 REV-T 的优先区时，MOM 可以让权，避免同一上涨波段重复卖出。

## 当前参数的含义

| 参数 | 当前值 | 作用 |
|---|---:|---|
| `MOM_ADAPTIVE_PULLBACK_ENABLED` | `True` | 启用自适应计算；关闭后恢复全局 `cfg.PULLBACK_PCT`。 |
| `MOM_PULLBACK_ATR_MULT` | `0.50` | 取 ATR10m 的 50% 作为原始回撤金额。调大将更晚确认，调小将更早确认。 |
| `MOM_PULLBACK_MIN_PCT` | `0.0035` | 最低回撤 0.35%，过滤低波动和盘口微回撤。 |
| `MOM_PULLBACK_MAX_PCT` | `0.0060` | 最高回撤 0.60%，避免高波动日等待过深回撤。 |
| `cfg.PULLBACK_PCT` | `0.0010` | 仅在自适应开关关闭时使用，即固定 0.10%。 |

举例：若峰值为 400 元、ATR10m 为 3.2 元，原始比例为 `0.50 × 3.2 / 400 = 0.40%`，在区间内，因此确认线为 `400 × (1 - 0.40%) = 398.40` 元。若计算值为 0.18%，则采用 0.35%；若为 0.90%，则采用 0.60%。

## 预期效果与取舍

开启后，相比关闭开关的固定 0.10% 回撤：

- 在普通低波动盘面，确认会明显更严格，MOM 卖出频率通常下降；
- 在波动扩大时，确认随实际分钟区间放宽，但不会超过 0.60%；
- 代价是部分短促冲高回落不会成交，或在回撤更多后才成交；
- 它不保证盈利，也不能替代成交、仓位、涨停保护、尾盘限制和主策略锁单等既有风控。

若要评估是否合适，应分别统计开启/关闭时的 MOM 触发次数、成交率、从峰值到成交价的回撤、单笔毛收益与最大不利变动；不要只看单日案例。

## 对应实现

- 参数和状态字段：[v41 策略](../Stragety/MiniQMT_Stragety/DayT/DayTradeing_v41_stragety_miniqmt.py#L74)
- 十分钟 ATR 近似与阈值裁剪：[v41 策略](../Stragety/MiniQMT_Stragety/DayT/DayTradeing_v41_stragety_miniqmt.py#L1192)
- MOM 反T 的峰值回撤确认：[v41 策略](../Stragety/MiniQMT_Stragety/DayT/DayTradeing_v41_stragety_miniqmt.py#L1417)
- MOM 正T 卖回的同一确认逻辑：[v41 策略](../Stragety/MiniQMT_Stragety/DayT/DayTradeing_v41_stragety_miniqmt.py#L1643)
