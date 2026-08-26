# ADR-015: Checkpoint and Recovery Authority

## Status

Accepted

## Date

2026-08-25

## Context

LangGraph checkpoints can resume a workflow, while PostgreSQL stores externally visible Task,
approval, cancellation, result, Evidence, and Artifact facts. Automatic recovery must not let a
stale or newer checkpoint overwrite durable business truth or confuse approval resume with
ordinary crash takeover.

## Decision

The Copilot Task database is always authoritative. A checkpoint stores continuation position and
bounded graph state only. Every resume/recovery reconciles tenant, task, Task terminal/cancel
state, Task and plan version, checkpoint identity, current step, durable-success set, approval
binding, execution generation, lease, and fencing token.

Terminal or cancelled DB state makes stale delivery/checkpoint a no-op. Cross-scope and plan
mismatch fail closed. A checkpoint generation must equal the current execution generation except
when a new approval-resume dispatch durably binds the immediately preceding generation and exact
checkpoint ID; every other generation mismatch fails closed. A checkpoint claiming success absent
from DB is rejected. DB success absent from checkpoint is preserved and not replayed. An expired
active execution without a safe checkpoint fails closed; a never-started accepted Task may
redispatch from durable intake. Crash takeover and runtime retry preserve the dispatch generation
and use a higher fencing token for fresh ownership.

A future bounded RecoveryScanner considers READY/orphan dispatch, expired execution lease, due
WAITING_RETRY, and orphan outbox records. It excludes unresolved WAITING_APPROVAL, terminal Tasks,
and valid leases. Runtime recoveries default to three; exhaustion uses existing Task `FAILED` plus
dispatch `DEAD_LETTERED`, not a new TaskStatus.

## Alternatives Considered

Checkpoint-as-source-of-truth was rejected because it lacks business authority. Blindly replaying
from a stale node was rejected because successful tools and Artifacts may duplicate. Treating the
existing `engine.resume` primitive as automatic recovery was rejected because no scanner,
redelivery, takeover, or poison accounting exists.

## Consequences

Recovery may fail rather than guess; this is intentional. Stage G implements scanning only after
Stage B fencing and Stage D Worker boundaries exist. Recovery errors and stale commit rejection
are typed, audited, and visible to operators without exposing business payloads.

## Related Documents

- [Async runtime architecture](../async-runtime-architecture.md)
- [Current task lifecycle](../task-lifecycle.md)
- [ADR-002 LangGraph orchestration](ADR-002-langgraph-orchestration.md)
