# ADR-014: Worker Lease, Heartbeat, and Fencing

## Status

Accepted

## Date

2026-08-25

## Context

The existing `workflow_leases` row prevents concurrent start/resume by primary-key conflict, but
uses a hard-coded ten-minute expiry and has no renew, heartbeat, dispatch binding, execution
generation, or fencing token. Queue receipt and ACK cannot prove that one Worker still owns a
Task when a paused old process returns after takeover.

## Decision

Extend the existing lease table; do not create a parallel lock. Atomic acquisition requires a
current nonterminal Task/dispatch/generation/version and no lease with `expires_at > database_now`.
It returns a lease ID and a task-scoped monotonic fencing token. Every authoritative state, step,
result, approval-resume, dispatch, and Artifact-publication mutation validates the current
generation and fencing token in the same transaction.

The configurable operational defaults are a 15-second heartbeat and 60-second TTL. Heartbeat is
valid from 1..300 seconds; TTL from 5..900 seconds; the heartbeat must be shorter than the TTL,
which must contain at least three heartbeat intervals. Takeover is permitted only at
`database_now >= expires_at`. Heartbeat and release match the exact tenant/task/worker/lease/
generation/fencing identity. A stale release cannot remove a replacement lease.

`WAITING_APPROVAL` has no lease. A Worker persists approval, checkpoint, and state, releases the
lease, ACKs, and becomes free.

## Alternatives Considered

Queue visibility, Redis lock alone, Python mutex, and Worker memory were rejected because none can
fence a stale database writer. TaskState optimistic concurrency alone was rejected because not all
step/Artifact side effects share one TaskState update. Keeping a ten-minute non-renewable lease was
rejected because failure detection and takeover would be both slow and ambiguous.

## Consequences

Stage B needs a migration, atomic SQL/CAS implementation, fake-clock unit tests, and real
PostgreSQL two-connection concurrency tests. Every persistence mutation gains explicit execution
authority. A Worker that cannot renew must stop committing and allow safe takeover; it may not
extend ownership in memory.

## Related Documents

- [Async runtime architecture](../async-runtime-architecture.md)
- [ADR-002 LangGraph orchestration](ADR-002-langgraph-orchestration.md)
- [ADR-006 persistence boundary](ADR-006-deployment-persistence-boundary.md)
