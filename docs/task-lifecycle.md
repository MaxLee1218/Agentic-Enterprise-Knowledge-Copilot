# Implemented verification lifecycle

The frozen Supplier Quality v1.0 state machine in
[`docs/design/state_machine.md`](design/state_machine.md) is authoritative.

The current deterministic workflow follows:

```text
CREATED
  -> UNDERSTANDING
  -> PLANNING
  -> EXECUTING
  -> report Artifact generated
  -> VERIFYING
  -> COMPLETED (PASSED or PASSED_WITH_WARNINGS)
     or FAILED (VerificationStatus.FAILED)
```

`VerificationResult` is persisted before the terminal verification transition. A failed result
does not delete Evidence, ToolResults, the audit trail, or the invalid Artifact, but the invalid
Artifact is not exposed through `TaskResult.artifacts`.

See [`evidence-and-verification.md`](evidence-and-verification.md) for verifier rules and the
documented conflict between the proposed pre-report ordering and the frozen lifecycle.
