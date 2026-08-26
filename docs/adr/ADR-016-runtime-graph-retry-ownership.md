# ADR-016: Runtime and Graph Retry Ownership

## Status

Accepted

## Date

2026-08-25

## Context

At-least-once Queue redelivery, Worker crash recovery, Graph Tool retry, provider retry, and HTTP
client retry can multiply calls if they all treat the same failure independently. Queue runtime
has no business knowledge and cannot decide whether a RAG, SQL, analytics, report, policy, or
verification result should be recalculated.

## Decision

Runtime/Queue retry owns only dispatch publication, delivery loss, Worker process loss, broker
visibility, expired lease, and runtime infrastructure recovery. It persists a separate recovery
count with a default maximum of three and bounded deterministic backoff. A Worker does not wrap
the Graph in an additional business retry loop.

The existing Graph and frozen per-step RetryPolicy remain the sole owner for transient,
recoverable, idempotent Tool failures: three total attempts for Knowledge/Database and two for
Analytics/Report. Permission, validation, and business outcomes do not retry. Queue redelivery
reloads durable results and does not reset these counters.

In the target runtime, a task-execution HTTP/DB adapter performs one transport request per
persisted Tool attempt or exposes every inner attempt against that same budget. The existing
Knowledge-client inner retry is a migration item that must be removed or budget-integrated before
async cutover. Submission transport retry is safe only with the same Idempotency-Key.

## Alternatives Considered

Retrying every exception at every layer was rejected because attempt counts multiply and audit is
misleading. Giving the Queue business error codes was rejected because it creates a second
orchestrator. Disabling all recovery was rejected because Worker and broker failures are expected.
Claiming exactly-once execution was rejected because external side effects need explicit command
idempotency and reconciliation.

## Consequences

Retry counters, error taxonomies, and latency metrics remain separately attributable. Existing
business retry semantics do not change. Stage D must failure-test process crash independently from
Tool transient failure, and future write tools require external idempotency before registration.

## Related Documents

- [Async runtime architecture](../async-runtime-architecture.md)
- [Frozen Tool contract](../design/tool_contract.md)
- [ADR-003 LLM provider](ADR-003-llm-provider-and-structured-output.md)

