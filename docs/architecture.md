# Architecture

v32 is a standalone composition root. Historical strategy files remain unchanged
audit records and are not runtime dependencies.

## Dependency direction

```text
DayTradeing_v32 composition root
        |
        +-- StrategySettings
        +-- DailyPlanBuilder <--- completed daily bars + Portfolio Snapshot
        +-- DayTradingEngine <--- Market Tick / Execution Completed
        |          |
        |          +-- Order Intent
        |                      |
        +-- MiniQmtRuntime ----+---> ExecutionCoordinator
                                           |
                                           +-- MiniQmtExecutionAdapter
                                           +-- SimulatedExecutionAdapter
        +-- AtomicJsonStateStore
```

Dependencies point inward. `DayTradingEngine` does not import MiniQMT, pandas,
system time, sleep, logging, or the filesystem.

## Module map

| Module | Interface | Hidden Implementation |
|---|---|---|
| `daytrading.settings` | immutable strategy settings | v32 defaults and config translation |
| `daytrading.planning` | `build(bars, tick, portfolio)` | completed-bar filtering, signal thresholds, capacity |
| `daytrading.engine` | session, `on_tick`, `apply_execution`, snapshot | 反T/正T/MOM states, Trade Legs, PnL, guards |
| `daytrading.execution` | `execute(intent, reserved_sellable)` | clamping, polling, cancellation, terminal confirmation |
| `daytrading.runtime` | `run`, `run_once`, `initialize_session` | MiniQMT connection, clock, scheduling, persistence |
| `daytrading.persistence` | `load_for_date`, `save` | atomic JSON and in-memory Adapters |
| `daytrading.adapters.miniqmt` | execution Adapter methods | QMT calls and broker field translation |
| `daytrading.adapters.simulated` | same execution Adapter methods | deterministic fills for tests and replay |
| `DayTradeing_v32...py` | CLI and `build_runtime` | composition only |

## Invariants

1. A trigger creates an Order Intent; it does not create a filled Trade Leg.
2. Only Execution Completed changes filled quantities and realized PnL.
3. Partial fills preserve FIFO remaining legs.
4. Unknown order terminal state activates Reconciliation Guard.
5. Daily Plan calculations exclude the current unfinished daily bar.
6. v32 has no import or inheritance dependency on v30/v31.

## Remaining project-wide migration

The active MiniQMT v32 path now uses the final composition architecture. Older RSI,
stock-selection and backtest paths still need separate migrations before duplicate
directories and misspelled historical paths can be removed safely.

