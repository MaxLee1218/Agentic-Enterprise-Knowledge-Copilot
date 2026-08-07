# ADR-005: Local Replaceable Observability Boundary

## Status

Accepted

## Date

2026-08-06

## Context

API, task, graph, tool, approval, and evaluation paths need one correlation model, stable events,
bounded metrics, and latency analysis without making an external monitoring platform a local or
test dependency. Direct imports from application layers into a logging or tracing SDK would
violate the repository's dependency matrix. Duplicating task, step, or tool state for telemetry
would also conflict with the frozen Supplier Quality v1.1 authority.

## Decision

Define provider-independent observability value contracts in `copilot.contracts` and an
application-owned `ObservabilityPort` in `copilot.services`. Implement JSON logging,
`ContextVar` propagation, a bounded in-memory trace store, a thread-safe bounded metrics registry,
and performance analysis in `copilot.observability`; construct and inject them only from
`copilot.bootstrap`.

Observability is derived from existing task, graph, step, tool, approval, evidence, and audit
outcomes. It must not authorize actions, change business results, swallow typed failures, or form
a parallel execution path. Durations use monotonic clocks; timestamps use UTC. Logs, attributes,
and labels are allowlisted, bounded, and passed through the shared sensitive-data policy. No
sampling or external exporter is enabled in this version.

## Alternatives Considered

### Import OpenTelemetry throughout business modules

Rejected. It would couple application code to one SDK, complicate deterministic tests, and invert
the accepted layer dependencies.

### Use audit records as the only observability mechanism

Rejected. Audit is durable accountability data, while nested spans, gauges, bounded histograms,
and process health have different retention, cardinality, and failure semantics.

### Persist every span in workflow state

Rejected. It would bloat checkpoints, duplicate business lifecycle state, and make low-risk
telemetry failure part of the task consistency boundary.

## Consequences

- Local development, tests, smoke, CLI inspection, and evaluation work without an external
  collector.
- Application layers depend only on stable contracts/ports; a future exporter can implement the
  same boundary at the composition root.
- Process-local traces and metrics are bounded but not durable or cross-process; durable audit
  timings remain the restart-safe diagnostic fallback.
- Tests must cover correlation isolation, exception completion, percentile math, label/attribute
  controls, limit behavior, and sensitive-data filtering.

## Related Documents

- [Architecture overview](../architecture.md)
- [Observability](../observability.md)
- [Performance analysis and limits](../performance.md)
- [Security model](../security-model.md)
- [Frozen design baseline](../design/design_baseline.md)

