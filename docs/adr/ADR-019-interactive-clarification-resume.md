# ADR-019: Durable Interactive Clarification and Resume

## Status

Accepted

## Date

2026-09-01

## Context

Task Understanding previously treated required missing information as a recoverable validation
error but still moved the business Task to `FAILED`. The audit event named the need for
clarification, yet there was no interaction contract, durable answer, API, checkpoint handoff, or
way to continue the same Task. This forced a corrected request to create a different Task and
lost the intended human-in-the-loop continuity.

The asynchronous runtime already has the correct hosting primitives: authoritative Task state,
PostgreSQL checkpoints, transactional dispatch, execution generations, leases, fencing,
at-least-once delivery, cancellation, and the `WAITING_APPROVAL` suspension pattern. Clarification
must reuse these authorities without making a browser, Queue message, Worker memory, model output,
or checkpoint the Task authority.

## Decision

Add non-terminal `WAITING_CLARIFICATION`. `UNDERSTANDING` enters it only for
`REQUIRED_USER_INPUT`; invalid, unauthorized, malformed, or unsupported requests still fail.
`SAFE_DETERMINISTIC_DEFAULT` remains domain-defined. A response can leave the wait only through
`UNDERSTANDING`, never directly through the Planner.

Persist one versioned `TaskClarification` per round with typed questions, trusted-scope choices,
accumulated validated context, the bounded response, immutable history, and a one-pending-round
database invariant. `TaskRequest.raw_input` remains immutable. Validated clarification facts are
passed beside the original request in `ClarificationContext`; answers are never concatenated into
the authoritative request.

The response API authenticates the current caller, checks Task ownership, tenant, role, purpose,
task type, and current data scope, validates typed values, binds the suspended checkpoint, and in
one transaction changes `PENDING` to `SUBMITTED`, moves the Task to `UNDERSTANDING`, and creates a
new dispatch generation. It returns `202` and never invokes LangGraph inline. The Worker reloads
the submitted record and server-created refreshed context, validates checkpoint/generation
binding under a new lease/fence, and resumes from the `request_clarification` checkpoint edge into
Task Understanding.

Selectable values come only from the current trusted caller scope. The LLM can explain or parse a
candidate answer but cannot enumerate or expand authority. Partial answers are accepted; validated
facts are retained and a later round asks only for remaining required fields. The loop is bounded
by `MAX_CLARIFICATION_ROUNDS` (default five), after which the Task fails with
`CLARIFICATION_LIMIT_EXCEEDED`.

`WAITING_CLARIFICATION` and `WAITING_APPROVAL` share runtime suspension semantics but have distinct
records and legal transitions. A suspended Task releases its Worker slot and lease. Cancellation
finalizes the active clarification, invalidates old responses, and creates no resume dispatch.
Recovery scanners exclude unresolved clarification waits. Duplicate responses use response
fingerprints and compare-and-swap; concurrent different responses yield one dispatch and one
conflict; duplicate delivery is an authoritative no-op.

## Alternatives Considered

- Keeping `FAILED` and asking the user to submit a corrected Task was rejected because it is not
  resume and loses stable Task identity and checkpoint continuity.
- Appending answers to `TaskRequest.raw_input` was rejected because it destroys the immutable audit
  origin and blurs trusted validation with untrusted text.
- Letting the Planner ask questions was rejected because no executable `TaskPlan` may exist before
  a complete validated `TaskContract`.
- Calling LangGraph from the response route was rejected because it violates asynchronous runtime
  ownership and can hold HTTP requests, Workers, and leases inconsistently.
- Keeping clarification only in browser state was rejected because restart, concurrency,
  cancellation, audit, and tenant isolation require durable server authority.

## Consequences

Incomplete but otherwise valid Supplier Quality and Accounts Payable tasks can continue under the
same Task ID across multiple responses and process restarts. The API and frontend gain a typed
human-interaction surface, and operators gain clarification counters, round histograms, waiting
gauges, safe audit events, and explicit exhaustion errors.

The persistence schema and frozen Supplier/AP lifecycle baselines require versioned changes.
Deployments must migrate before serving the new API and must run compatible API and Worker builds.
Free-form answers remain untrusted until Task Understanding validates them, and current
authorization is re-evaluated at response time; external IAM changes after acceptance still depend
on the deployment's trusted identity freshness guarantees.

## Related Documents

- [Task lifecycle](../task-lifecycle.md)
- [Asynchronous runtime architecture](../async-runtime-architecture.md)
- [Supplier Quality design baseline](../design/design_baseline.md)
- [Accounts Payable design baseline](../use-cases/accounts-payable/design-baseline.md)
- [ADR-015](ADR-015-checkpoint-recovery-authority.md)
- [ADR-018](ADR-018-deterministic-plan-compilation.md)
