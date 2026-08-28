# Implemented v1.1 task and approval lifecycle

The frozen Supplier Quality v1.1 state machine in
[`docs/design/state_machine.md`](design/state_machine.md) is authoritative.

The current deterministic workflow follows:

```text
Natural-language API / CLI submission
  -> Task Intake validation and trusted constraint merge
  -> TaskRequest + CREATED persisted
  -> TASK_SUBMITTED audit
  -> validate_request
  -> understand_task (original TaskRequest.raw_input)
  -> TaskContract
  -> create_plan
  -> TaskPlan
CREATED
  -> UNDERSTANDING
  -> PLANNING
  -> EXECUTING
  -> WAITING_APPROVAL (when the exact controlled action requires a human)
  -> EXECUTING (APPROVAL_GRANTED or APPROVAL_EDITED)
  -> Report Model and render consistency validated
  -> report Artifact atomically generated
  -> VERIFYING
  -> COMPLETED (PASSED or PASSED_WITH_WARNINGS)
     or FAILED (VerificationStatus.FAILED)
```

`VerificationResult` is persisted before the terminal verification transition. A failed result
does not delete Evidence, ToolResults, the audit trail, or the invalid Artifact, but the invalid
Artifact is not exposed through `TaskResult.artifacts`.

The Report Tool's input, render, and commit checks do not replace this lifecycle gate. Tool
success means only that a structurally valid Artifact was committed. The independent frozen
Verifier still decides whether the Task may reach `COMPLETED`.

See [`evidence-and-verification.md`](evidence-and-verification.md) for verifier rules and the
documented conflict between the proposed pre-report ordering and the frozen lifecycle.

The lifecycle is scheduled by explicit LangGraph nodes. SQLite checkpoints remain available for
local tests; PostgreSQL deployments use the official PostgreSQL saver. Separate SQLAlchemy-backed
business tables remain authoritative for TaskState, results, Evidence, Artifact metadata, audit,
and leases. Resume uses the tenant/task checkpoint key and
continues the next safe node without repeating committed successful steps. See
[`langgraph-workflow.md`](langgraph-workflow.md).

For the implemented Stage 12 path, Knowledge can complete before the exact database input exists.
The next `policy_check` persists a bound ApprovalRequest before applying
`EXECUTING -> WAITING_APPROVAL`. `APPROVE` preserves proposed arguments; `EDIT` requires a complete
replacement and may only lower the frozen allowlisted limit; both resume from the policy
Checkpoint. `REJECT`, `EXPIRED`, and `REVOKED` transition to `CANCELLED` without invoking the
controlled tool. Approval history is append-only and a compare-and-swap permits one decision.

The approved final input is placed back at the `policy_check` boundary and then follows the normal
ToolExecutor path. Previously committed StepResults and Evidence remain in Graph/business storage,
so restart recovery executes the database and downstream steps without replaying Knowledge. See
[`stage-12/human-in-the-loop.md`](stage-12/human-in-the-loop.md).

When an LLM planning service is injected, `understand_task` and `create_plan` call that service and
checkpoint only schema-valid results. `validate_plan` remains deterministic. A repairable initial
plan loops through the separately bounded `repair_plan` node without leaving `PLANNING`. An
eligible verification failure uses the frozen
`VERIFYING -> REPLANNING -> EXECUTING` transitions and a separate replan counter.

The frozen machine has no clarification state. Required missing information therefore records a
recoverable `TASK_INFORMATION_MISSING` error and `TASK_CLARIFICATION_REQUIRED` audit event, but
transitions `UNDERSTANDING -> FAILED`; the API/CLI exposes the missing-information message and a
corrected request starts a new Task. The frozen database-empty path remains Success and never
replans.

The initial persisted row may temporarily contain no Contract or Plan. Understanding commits the
Contract before planning; planning commits the Plan before deterministic validation or any
business tool call. Existing checkpoint records with pre-populated Contract/Plan remain readable,
and internal prepared execution remains available for deterministic compatibility tests.

## Task query and cancellation semantics

The external task-management API reads the authoritative task, plan, StepResult, Evidence, and
Artifact repositories through `NaturalLanguageTaskService` and `ArtifactService`. It never returns
Checkpoint payloads or a serialized `TaskState`.

`POST /v1/tasks` is acceptance-only. It atomically commits Task, runtime, idempotency, and initial
`PENDING` dispatch state, then returns `202 Accepted` with `task_id`, business `task_status`,
separate `runtime_status`, and `status_url`. It never invokes the Graph, Tools, verifier, or
Artifact renderer in the HTTP request thread. Clients poll `GET /v1/tasks/{task_id}`.

Cancellation is the frozen `CANCEL_REQUESTED` domain event. It is valid from `CREATED`,
`UNDERSTANDING`, `PLANNING`, `WAITING_APPROVAL`, `EXECUTING`, `RETRYING`, `REPLANNING`, and
`VERIFYING`. The service atomically compare-and-swaps the state to `CANCELLED`, stops future
resumption by revoking pending approvals, preserves committed StepResults and Evidence, and writes
the cancellation audit/result records. Repeating cancellation on `CANCELLED` is idempotent;
`COMPLETED` and `FAILED` return `TASK_NOT_CANCELLABLE` with HTTP 409. Cancellation is cooperative:
an already-running non-interruptible external call may finish, but its late result cannot move the
terminal task or authorize downstream steps.

## Asynchronous hosting contract

Stages B through H in [`async-runtime-architecture.md`](async-runtime-architecture.md) are now
implemented using the PostgreSQL Queue v1 selected by ADR-017. The business state machine remains
unchanged; runtime state describes only hosting and recovery.

The business state machine in this document remains unchanged. A separate runtime projection is
used only for execution hosting:

| Task/condition | Runtime projection | Worker lease |
|---|---|---|
| accepted and dispatchable | `READY` | none |
| actively hosted | `LEASED` | exactly one unexpired database lease |
| runtime recovery delayed | `WAITING_RETRY` | none |
| `WAITING_APPROVAL` | `SUSPENDED` | none |
| `COMPLETED` / `FAILED` / `CANCELLED` | `FINISHED` | none |

At-least-once Queue delivery does not add `QUEUED` to `TaskStatus`. A Worker reloads Task DB state,
reconciles the tenant/task checkpoint, and acquires the atomic lease before entering the existing
workflow. A monotonic fencing token is checked on authoritative commits. Duplicate/stale delivery
therefore becomes an acknowledged no-op rather than a second Task execution.

Approval resolution persists one immutable decision and, for an approved request, atomically
creates a new dispatch at `execution_generation + 1`; it returns `202` without executing the Graph.
Reject, expiry, revoke, and cancellation create no executable resume dispatch. A
`WAITING_APPROVAL` Task is `SUSPENDED`, has no execution lease, and consumes no Worker slot.
Durable cancellation remains the Task DB source of truth, while process-local cooperative signals
only reduce observation latency. Late Tool results cannot commit through a deleted/stale lease.

See ADR-012 through ADR-017 for submission, dispatch, lease, recovery, retry, and provider
decisions. The deployed environment still must run Alembic, PostgreSQL checkpoints, at least one
independent Worker, and shared Artifact storage appropriate to its topology; source availability
alone is not a production-readiness claim.
