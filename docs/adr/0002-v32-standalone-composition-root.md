# ADR 0002: Make v32 a standalone composition root

- Status: Accepted
- Date: 2026-08-23
- Amends: ADR 0001

## Context

The first v32 implementation inherited `v31.StrategyRunner`, which itself inherited
v30 and modified v30 module globals during import. This retained an implicit
Interface made of private methods, a shared state dictionary, import order, and
historical globals. The inheritance chain prevented the decision Implementation
from being exercised directly by deterministic simulation.

## Decision

`DayTradeing_v32_stragety_miniqmt.py` is only a composition root. It constructs:

- `StrategySettings`, an immutable configuration snapshot;
- `DailyPlanBuilder`, which consumes completed bars;
- `DayTradingEngine`, a pure Market Tick and Execution Completed state machine;
- `ExecutionCoordinator`, which owns the Order Lifecycle;
- `MiniQmtRuntime`, which owns connection, time, scheduling and persistence;
- MiniQMT and simulated execution Adapters at the execution Seam.

v32 must not import or inherit v30/v31. Primary Trading State and Trade Legs advance
only after an Execution Completed result reports actual filled shares.

## Consequences

- The decision Interface becomes the direct test surface for 反T, 正T and MOM.
- MiniQMT concerns have Locality in the runtime and Adapter rather than the engine.
- Deterministic tests gain Leverage from the same engine used by live execution.
- Historical v30/v31 files remain available for audit but cannot affect v32 through
  import-time behavior.
- Behavioral changes require engine event-sequence tests rather than another
  inheritance layer.

