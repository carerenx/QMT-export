# ADR 0001: Extract the order lifecycle behind runtime adapters

- Status: Amended by ADR 0002
- Date: 2026-08-22

## Context

The active MiniQMT strategy combines signal decisions, inventory accounting,
broker calls, polling, cancellation, recovery, and file persistence in one large
Implementation. The backtest path uses a different order model, so a safety fix in
live trading is not automatically exercised in simulation.

## Decision

Create a deep `daytrading` Module whose small execution Interface accepts an
`OrderIntent` and returns an `ExecutionResult`. The Module owns resource clamping
and the complete order lifecycle. MiniQMT and deterministic simulation are separate
Adapters at the runtime Seam.

Atomic state persistence is a second focused Module. Versioned strategy files stay
as compatibility entrypoints. The original incremental v31 inheritance decision is
superseded by ADR 0002.

## Consequences

- Order safety has one test surface and gains Leverage across live and simulated
  runtimes.
- Broker-specific field handling remains local to the MiniQMT Adapter.
- Historical strategies remain reproducible.
- The execution Interface remains independent of the decision engine and runtime.
