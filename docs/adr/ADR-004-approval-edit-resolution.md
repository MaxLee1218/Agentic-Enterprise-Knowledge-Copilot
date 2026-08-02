# ADR-004: Approval Edit Resolution

## Status

Accepted

## Date

2026-08-02

## Context

The Supplier Quality Analysis v1.0 approval model allowed a pending request to be approved or
rejected. It did not define how an approver could recommend a bounded parameter change and let the
same approved step continue. Implementing an unspecified edit as a free-form patch would make the
executed action differ from the action reviewed by policy and would weaken schema validation,
idempotency, auditability, checkpoint recovery, and approval scope binding.

The v1.1 design needs one narrowly bounded behavior: an approver may replace editable arguments and
approve the resulting action without adding a new task state, changing the plan, or replaying
already successful prerequisites.

## Decision

Add `EDIT` as an ApprovalRequest resolution action alongside `APPROVE` and `REJECT`.

- `EDIT` is not an ApprovalStatus. A valid edit resolves the request to `APPROVED` while preserving
  `resolution_action=EDIT`.
- The approver submits a resolution reason and a complete replacement argument object. Patch and
  deep-merge semantics are forbidden.
- The original proposed arguments and action fingerprint remain immutable. The accepted replacement
  arguments produce a separate resolved action fingerprint.
- The current registry snapshot must still match the bound tool version and input-schema fingerprint.
  The replacement must pass that bound schema and may differ only in fields explicitly listed by
  policy in `editable_fields`.
- The frozen v1.1 allowlist contains only `knowledge_search.top_k` and
  `database_query.row_limit`, and either value may only be reduced. The other two tools have an
  empty edit allowlist.
- The edit cannot change the TaskContract, plan version, step, tool, schema, tenant, requester,
  supplier/date/data scope, permission, risk classification, or read-only guarantees.
- A valid edit is allowed only before the target tool's first call. It emits `APPROVAL_EDITED`,
  resumes `WAITING_APPROVAL -> EXECUTING` from the persisted checkpoint, and executes the target and
  remaining downstream steps. Successful prerequisites are not replayed.
- The resolved arguments and fingerprint become the canonical ToolCall input and idempotency,
  audit, recovery, and verification binding. Retries do not reopen or mutate the edit.
- A malformed, incomplete, stale, conflicting, or out-of-scope edit leaves the ApprovalRequest
  `PENDING` and the Task `WAITING_APPROVAL`; no tool call occurs.
- A recommendation that requires a different contract, plan, tool, capability, permission, risk,
  or data scope must use rejection/revocation followed by versioned replanning or a new Task and a
  new approval.

The Supplier Quality Analysis task type, TaskStatus set, four-tool allowlist, existing tool schemas,
evidence model, artifacts, retry rules, replan rules, and business scope are unchanged.

## Alternatives Considered

### Treat an edit as a fourth approval status

Rejected. `EDIT` describes how the request was resolved, while `APPROVED` describes whether the
resolved action is authorized. Combining the two would complicate existing authorization checks.

### Accept a free-form comment and let the agent infer parameters

Rejected. A model-inferred mutation would not be the exact action approved by the human and could
silently broaden access or alter deterministic tool behavior.

### Apply JSON Patch or deep-merge semantics

Rejected. Partial mutation creates ambiguity around omitted, defaulted, stale, and nested fields.
A complete replacement is deterministic and can be validated and fingerprinted as a whole.

### Replan after every edit

Rejected for the bounded case. Replanning is unnecessary when only allowlisted arguments change
within the same Contract, Plan, Step, Tool, Schema, permissions, risk, and data scope. Any broader
change still requires replanning or a new Task.

## Consequences

- Approval persistence must retain both proposed and resolved argument versions and fingerprints.
- Resolution must use single-winner concurrency control so an approval cannot be approved, edited,
  or rejected twice.
- Policy and executor boundaries must validate editable-field differences and final action binding
  before execution.
- Checkpoint recovery must prove that the target tool has not run and must not replay committed
  predecessor results.
- Tests must cover valid edits, incomplete and unauthorized edits, stale/conflicting resolution,
  first-call enforcement, resolved idempotency, and checkpoint resume without predecessor replay.
- Production implementation remains a separate change; this ADR records the approved v1.1 design.

## Related Documents

- [Frozen design baseline v1.1](../design/design_baseline.md)
- [Domain model](../design/domain_model.md)
- [State machine](../design/state_machine.md)
- [Tool contract](../design/tool_contract.md)
- [Supplier Quality Analysis walkthrough](../design/walkthrough.md)
- [Design conflict review](../design/design_review.md)
