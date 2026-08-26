# ADR-012: Asynchronous Task Submission Model

## Status

Accepted

## Date

2026-08-25

## Context

The current `POST /v1/tasks` executes LangGraph in the HTTP request thread and normally returns a
terminal result with 201. Its 202 response is reserved for a durable approval interruption. Long
tasks, horizontal Workers, crash recovery, and backpressure require acceptance to be separated
from execution without adding `QUEUED` to the frozen business `TaskStatus`.

## Decision

The future steady-state `POST /v1/tasks` keeps its path but returns `202 Accepted` for every task
whose Task row and initial dispatch commit successfully. It returns the acceptance-only
`TaskSubmissionResponse`: Task/trace identity, `task_status=CREATED`, `runtime_status=READY`, UTC
acceptance time, and controlled Task/Artifact API paths. It does not wait for understanding,
planning, tools, approval, verification, or finalization.

`TaskStatus` remains the frozen business lifecycle. A separate `RuntimeStatus` expresses READY,
LEASED, WAITING_RETRY, SUSPENDED, or FINISHED. `GET /v1/tasks/{task_id}` is the authoritative read.
Approval is observed by polling as `WAITING_APPROVAL + SUSPENDED`, not as a special POST outcome.

Submission supports `Idempotency-Key` scoped by tenant, authenticated caller, and key. The key is
bound to a canonical request fingerprint: an identical retry returns the original accepted Task;
a different request returns HTTP 409. The API and frontend switch only in Stages E and F after the
persistence and Worker gates; this ADR does not change current production behavior.

## Alternatives Considered

Keeping synchronous 201 was rejected because the API request remains the execution host. Adding
`QUEUED` to `TaskStatus` was rejected because it mixes business and runtime state and breaks
frozen consumers. Returning internal storage or Queue URLs was rejected because those are not
public authorization boundaries. A permanent sync/async dual mode on one endpoint was rejected
because clients could not rely on one response contract.

## Consequences

OpenAPI and the frontend require a coordinated migration to polling. Task acceptance becomes fast
and restart-safe only after the transactional dispatch migration exists. Existing Supplier
Quality and AP Task states and final results remain unchanged. The system cannot claim this API
behavior until Stage E.

## Related Documents

- [Async runtime architecture](../async-runtime-architecture.md)
- [Current task lifecycle](../task-lifecycle.md)
- [Frozen Supplier Quality state machine](../design/state_machine.md)

