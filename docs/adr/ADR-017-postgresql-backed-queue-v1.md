# ADR-017: PostgreSQL-backed Queue v1

## Status

Accepted

## Date

2026-08-26

## Context

ADR-013 froze a provider-neutral, at-least-once `TaskQueue` behind the existing transactional
`task_dispatches` outbox, but intentionally did not select its concrete provider. Stage 19 cannot
cut the API over to acceptance-only submission until a durable Queue, independent Worker, and
crash-recovery path have been implemented and failure-tested. The authoritative runtime database
is already PostgreSQL, and the frozen lease, fencing, dispatch, recovery, and checkpoint decisions
must not be redesigned to select a Queue.

## Decision

Queue v1 uses the same PostgreSQL 16-or-later Copilot persistence service as the authoritative
Task runtime. `task_dispatches` remains the transactional outbox and immutable execution intent.
It is not replaced or duplicated. A separate `task_queue_deliveries` table stores only subordinate
transport state: one idempotently re-armable delivery per tenant-qualified dispatch, its
availability and visibility times, delivery-attempt count, and the current opaque receipt token.
It contains no Task, Plan, Evidence, approval, authorization, credential, business row, prompt, or
Artifact payload.

The outbox dispatcher processes a bounded due batch. It idempotently arms the Queue delivery and
compare-and-sets `PENDING` or `RETRY_SCHEDULED` dispatches to `ENQUEUED`. A crash after arming but
before the compare-and-set is safe because consumers join against an `ENQUEUED` dispatch; the next
dispatcher pass repeats the same arm operation. Multiple dispatchers and consumers coordinate
with PostgreSQL row locks and `FOR UPDATE SKIP LOCKED`; no correctness rule depends on
`LISTEN/NOTIFY`, process memory, or an API process.

`receive` claims due, unacknowledged deliveries with database time, increments the delivery
attempt, writes a fresh opaque receipt, and advances `visible_at`. Only that exact current receipt
may `ack` or `nack`. An unacknowledged delivery becomes eligible again after its visibility
timeout. `nack` makes it immediately or deterministically later available. `ack` acknowledges the
transport receipt only; the Worker separately compare-and-sets the durable dispatch outcome after
an authoritative terminal, approval-suspended, retry-scheduled, or verified no-op result.
Recovery re-arms the same dispatch delivery and execution generation. Lease takeover increments
the existing fencing token; Queue claim ownership never grants execution authority. All timing
uses PostgreSQL time. Queue health is a bounded database probe and Queue depth/age are derived
from eligible delivery rows. API and Worker readiness remain separate.

Queue v1 is PostgreSQL-only. SQLite remains a compatibility store for isolated synchronous and
unit tests and must not be used to claim Queue, concurrent Worker, or crash-recovery correctness.
Worker concurrency and dispatcher/recovery batches are explicitly bounded by configuration.

## Alternatives Considered

Redis, RabbitMQ, SQS, Celery, an in-process queue, FastAPI background tasks, and `asyncio` process
memory were not selected. Adding an external broker would add another operational dependency
without changing the frozen authority, lease, fencing, and recovery requirements. Treating
`task_dispatches` itself as both outbox and receipt storage was rejected because ACK/NACK and
visibility are per delivery attempt, while dispatch status is a durable logical lifecycle. An
unbounded PostgreSQL polling loop and correctness based on notifications were rejected.

## Consequences

Stage 19 can implement a real durable Queue without reimplementing Stage B or introducing a
second lease. Queue and authoritative persistence share an outage domain, so a persistence outage
stops submission, dispatch, and consumption; a committed PENDING outbox survives a dispatcher or
Worker outage and is published after recovery. PostgreSQL must be capacity-planned for polling,
row churn, autovacuum, connection pools, and indexes. This v1 does not provide broker-independent
HA, distributed exactly-once delivery, hard cancellation, or a production-readiness claim.

## Related Documents

- [Async runtime architecture](../async-runtime-architecture.md)
- [ADR-013 Queue delivery and transactional dispatch](ADR-013-queue-delivery-dispatch-model.md)
- [ADR-014 Worker lease and fencing](ADR-014-worker-lease-fencing.md)
- [ADR-015 Checkpoint recovery authority](ADR-015-checkpoint-recovery-authority.md)
- [ADR-016 Runtime and Graph retry ownership](ADR-016-runtime-graph-retry-ownership.md)

