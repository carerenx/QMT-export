# QMT-export domain context

QMT-export is an A-share quantitative-trading workspace. The actively maintained
path is the MiniQMT intraday T strategy for 长飞光纤 (601869). Historical strategy
files are immutable records; a behavior change is introduced through a new
versioned entrypoint.

## Ubiquitous language

- **交易日会话 (Trading Session)**: all strategy state and orders belonging to one
  exchange trading date.
- **主交易状态 (Primary Trading State)**: the state machine that coordinates 正T
  and 反T. It must never claim a leg is closed before the broker confirms the fill.
- **交易腿 (Trade Leg)**: a price and share quantity opened by one intraday action.
  Partial fills reduce a leg; they do not erase it.
- **可卖底仓 (Sellable Inventory)**: shares reported as available for sale by the
  broker. New 正T or 反T legs reserve this inventory so concurrent mechanisms cannot
  promise the same shares twice.
- **委托意图 (Order Intent)**: a domain request describing effect, quantity,
  reference price, and business label. It is not yet a broker order.
- **委托生命周期 (Order Lifecycle)**: submit, inspect, optionally cancel, and
  confirm terminal state. A timeout without terminal confirmation triggers 对账保护.
- **对账保护 (Reconciliation Guard)**: a fail-closed condition that blocks new
  opening legs while account or order truth is unknown. Closing a known leg remains
  permitted when safe.
- **账户快照 (Portfolio Snapshot)**: one validated view of total shares, sellable
  shares, cash, cost, and last price.
- **执行 Adapter (Execution Adapter)**: translation between the trading domain and
  a runtime such as MiniQMT or deterministic simulation.
- **行情事件 (Market Tick)**: an immutable price/time input delivered to the
  decision engine. It contains no connector or wall-clock behavior.
- **日内交易计划 (Daily Plan)**: completed-bar signal thresholds plus the current
  portfolio capacity used to prepare one Trading Session.
- **成交完成事件 (Execution Completed)**: the normalized result fed back to the
  engine. Only this event may advance Trade Legs and filled-position states.

## Invariants

1. An opening order cannot consume reserved sellable inventory.
2. A buy order cannot exceed available cash after the fee buffer.
3. State advances by actually filled shares, never submitted shares.
4. An unconfirmed cancellation or unreadable order enters 对账保护.
5. Runtime state is written atomically. A stale session is restored only when it
   contains open Trade Legs, which are carried under Reconciliation Guard.
