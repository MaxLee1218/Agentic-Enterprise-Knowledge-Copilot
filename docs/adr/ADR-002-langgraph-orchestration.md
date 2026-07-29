# ADR-002: LangGraph Orchestration and SQLite Recovery

## Status

Accepted

## Date

2026-07-28

## Context

The serial `WorkflowRunner` proved the frozen Supplier Quality Analysis v1.0 contracts, tool
boundaries, evidence flow, and verification lifecycle. It did not provide explicit node routing,
durable checkpoints, restart recovery, or a stable start/resume interface. The frozen state
machine and report-before-verification order must remain unchanged.

## Decision

Use a LangGraph `StateGraph` as the default application orchestrator. `AgentGraphState` is a
checkpointable envelope around the existing immutable domain contracts, Evidence identifiers,
bounded normalized execution snapshots, and control counters; it is not a second `TaskState`.
Raw source documents, Artifact bytes, and full Evidence content are excluded. Nodes call injected
application ports and every
capability invocation remains `ToolExecutor -> ToolRegistry -> Tool`.

Use `SqliteSaver` for orchestration checkpoints. Separate SQLite business tables remain
authoritative for Task, plan, state, results, Evidence, Artifact metadata, leases, and audit.
The configured task/tenant thread key prevents collisions. A database-backed lease and
SQL-backed TaskState compare-and-swap prevent concurrent or stale execution. Checkpoint
deserialization is restricted to an explicit frozen-type allowlist with no pickle fallback.
Execution is auditable at-least-once: stable idempotency keys and unique result records suppress
duplicate effects, but no exactly-once claim is made.

The fixed report step remains inside `EXECUTING`; only after its Artifact is committed may the
state transition to `VERIFYING`. Automatic LLM planning, repair, replan, and approval-resume APIs
remain out of scope.

## Alternatives Considered

- Extend the serial loop with custom checkpoints: rejected because it would duplicate graph
  scheduling and recovery mechanics.
- Store business facts only in LangGraph checkpoints: rejected because checkpoints are recovery
  snapshots, not the domain system of record.
- Change verification to run before report generation: rejected because it conflicts with the
  frozen state machine and walkthrough.

## Consequences

The CLI and application service now use the LangGraph engine by default. The old runner remains
only as a deprecated regression implementation. SQLite is suitable for this local vertical slice;
multi-worker production deployment will require a reviewed durable database/checkpointer
adapter without changing domain contracts.

## Related Documents

- [LangGraph workflow](../langgraph-workflow.md)
- [Frozen state machine](../design/state_machine.md)
- [Architecture](../architecture.md)
