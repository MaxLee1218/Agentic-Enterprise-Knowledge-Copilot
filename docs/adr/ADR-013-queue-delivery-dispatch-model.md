# ADR-013: Queue Delivery and Transactional Dispatch Model

## Status

Accepted

## Date

2026-08-25

## Context

Persisting a Task and publishing a Queue message in separate operations loses work when either
side succeeds alone. Broker delivery can duplicate, delay, or redeliver messages, and a Queue
must not become a second Task database or authorization source.

## Decision

Use a transactional outbox. The Task, `CREATED` state, submission-idempotency binding, and initial
PENDING dispatch commit in one PostgreSQL transaction. A separate dispatcher publishes the same
immutable minimal `TaskDispatch` and compare-and-sets its durable status. Uncertain publication is
retried with the same dispatch ID and execution generation.

The Queue contract is provider-neutral and at least once. The envelope contains tenant, task,
trace, dispatch, execution generation, expected Task version, enqueue-intent time, and
`not_before`. Approval resume additionally binds the immediately preceding generation and exact
checkpoint ID. Crash takeover, runtime retry, and broker republish reuse the same dispatch and
generation; a higher fencing token distinguishes the takeover owner. The envelope contains no
Task/Plan/Evidence/Approval, authorization facts, credentials, business rows, prompts, or
Artifact content. Workers reload authoritative state before claiming.

ACK occurs only after a durable terminal, approval-suspended, retry-scheduled, or verified no-op
outcome. ACK is not Task completion. One tenant/task/generation durable identity prevents
unbounded active dispatch creation.

## Alternatives Considered

`save_task(); publish()` was rejected because it has an unavoidable crash window. Storing the full
Task in the Queue was rejected because it duplicates authority and sensitive content. Exactly-once
broker delivery was rejected because it is not portable or sufficient for external side effects.
A fake in-process Queue was rejected because it would prove none of the required failure behavior.

## Consequences

Stage B requires `task_dispatches` and atomic submission persistence; Stage C may choose a broker
only behind `TaskQueue`. Duplicate messages become normal and must be absorbed by leases, fencing,
and idempotent commits. Broker-specific receipt/visibility types remain in the adapter.

## Related Documents

- [Async runtime architecture](../async-runtime-architecture.md)
- [ADR-006 persistence boundary](ADR-006-deployment-persistence-boundary.md)
- [Architecture](../architecture.md)
