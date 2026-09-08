# ADR-021: Typed Natural-Language Clarification Resolution

## Status

Superseded

## Date

2026-09-05

## Context

ADR-019 made clarification durable, but its first implementation effectively required exact ISO
date ranges and canonical business IDs. A free-text response such as `year2025 CN` reached Task
Understanding, yet the offline extractor recognized neither compact calendar years nor authorized
country aliases. The next round therefore repeated both questions. `allowed_values` constrained
the API and UI but was not an entity resolver, and the persisted context carried values without a
typed explanation of missing, normalized, ambiguous, confirmable, invalid, or unauthorized
fields.

## Decision

Keep `WAITING_CLARIFICATION` as the only interpretation wait state and add three interaction kinds:
`MISSING_INFORMATION`, `AMBIGUITY_RESOLUTION`, and `CANDIDATE_CONFIRMATION`. Add version-two,
backward-compatible clarification contracts for `FieldResolution`, `CandidateInterpretation`, and
`ClarificationContext`. A candidate version is the SHA-256 hash of the exact typed candidate set;
the candidate, kind, round, and accepted field context are persisted in the existing clarification
record and checkpoint payload.

Task Understanding now follows this boundary:

```text
immutable original request + prior validated context + latest response
  -> structured model extraction
  -> deterministic domain normalizers and authorized-set resolvers
  -> ResolutionPolicy
  -> field-level clarification or candidate confirmation
  -> complete TaskContract validation
  -> Planner
```

Model output is never sufficient to establish a date range or business identifier. Mandatory AP
and Supplier scope fields are accepted only from deterministic normalization, an exact structured
answer, or previously validated clarification context. Entity aliases are resolved only within the
current trusted authorized set. Unique country codes are lossless normalized selections; country
names require confirmation; multiple matches remain ambiguous; and any requested canonical or
country scope with no authorized match is denied.

AP supports exact ISO ranges and deterministic calendar years, months, halves, and quarters.
Supplier supports canonical and worded quarters. Relative expressions such as `recently` remain
unresolved. Existing domain validators still enforce the AP 366-day maximum, snapshot boundary,
Supplier quarter rules, deadlines, read-only behavior, and all policy constraints.

An affirmative phrase applies only when the current persisted clarification kind is
`CANDIDATE_CONFIRMATION`; it cannot resolve a governance approval. Rejection removes only the
displayed candidate fields. A correction replaces fields found in the latest response and carries
forward other accepted fields. Every response still resumes through `UNDERSTANDING`; no incomplete
Contract reaches the Planner or tools.

## Consequences

Natural responses can complete or progressively narrow a governed Contract without mutating the
original request. Clarification messages are derived from typed resolution state and ask only for
unresolved fields. Restart, duplicate-response, stale-response, concurrency, cancellation, and
round-limit behavior continue to use ADR-019 and the frozen async runtime.

Audit events record low-sensitivity status/source metadata, confirmation, correction, and
authorization rejection. `TASK_FIELD_AUTHORIZATION_REJECTED` records only the rejected field name,
not raw user text or the requested identifier. Metrics are low-cardinality counters and retain no
raw user text or business identifiers. The frontend continues to use one message composer;
approval remains an explicit action card and endpoint.

This decision does not introduce general chat, memory, a new Planner, new tools, new Task states,
or production-readiness claims. Accounts Payable remains not production-ready.

## Alternatives Considered

- Concatenating responses into the original request was rejected because it destroys immutable
  request provenance and blurs trusted and untrusted data.
- Letting the LLM invent aliases or accept candidates by confidence was rejected because schema
  validity and confidence do not establish authorization or user intent.
- Adding `WAITING_CONFIRMATION` was rejected because the durable clarification record already
  safely expresses the interaction and owns restart/concurrency semantics.
- Treating `yes` as a generic action was rejected because interpretation confirmation and
  governance approval have different authority and audit requirements.

## Related Documents

- [ADR-019](ADR-019-interactive-clarification-resume.md)
- [ADR-020](ADR-020-chat-first-task-workspace.md)
- [Task Understanding and Planning](../task-understanding-and-planning.md)
- [Task lifecycle](../task-lifecycle.md)
- [Offline Agent Evaluation](../evaluation.md)
