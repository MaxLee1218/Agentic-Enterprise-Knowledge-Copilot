# Implemented verification lifecycle

The frozen Supplier Quality v1.0 state machine in
[`docs/design/state_machine.md`](design/state_machine.md) is authoritative.

The current deterministic workflow follows:

```text
CREATED
  -> UNDERSTANDING
  -> PLANNING
  -> EXECUTING
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

The lifecycle is now scheduled by explicit LangGraph nodes. SQLite checkpoints are written after
graph steps, while separate SQLite business tables remain authoritative for TaskState, results,
Evidence, Artifact metadata, audit, and leases. Resume uses the tenant/task checkpoint key and
continues the next safe node without repeating committed successful steps. See
[`langgraph-workflow.md`](langgraph-workflow.md).
