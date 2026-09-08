# ADR-022: Governed Semantic Clarification and Natural Response Composition

## Status

Accepted

## Date

2026-09-06

## Context

ADR-021 sends every initial request and clarification response through structured Task
Understanding, but accepts mandatory fields only from deterministic normalization. A model may
therefore correctly extract `from 2026-01-01 to 07-01` while the narrower parser rejects it and
repeats a template question. Fixed clarification copy also hides the semantic understanding that
was already obtained from the model.

## Decision

Supersede ADR-021 for Accounts Payable clarification presentation and model-candidate handling.
Every initial request and clarification response continues to make a structured semantic Task
Understanding call. Deterministic normalization remains the first acceptance path.

When deterministic normalization cannot resolve a mandatory field, a complete schema-valid model
candidate may be retained only when deterministic code proves that the latest message contains a
field-appropriate semantic anchor, the candidate satisfies hard limits and snapshot bounds, and
identifiers resolve inside the current trusted authorized set. Such a candidate is never execution
authority: it is persisted as the existing version-bound `CandidateInterpretation` and requires
explicit confirmation before entering TaskContract.

After deterministic resolution, a separate bounded structured model call may compose the complete
assistant clarification message from safe typed facts: interaction kind, accepted interpretations,
unresolved fields, reasons, authorized alternatives and permitted examples. It receives no raw
user text, credentials, tool arguments or unrestricted data. Code verifies required-field coverage,
applies the shared output guard, and rejects governed identifiers or ISO dates that were not present
in the validated facts. Provider, schema, timeout or validation failure falls back to deterministic
wording without failing or retrying the business Task.

The durable clarification record adds an optional `assistant_message`; questions and typed context
remain authoritative. Existing records without it use the template fallback. No new Task status,
generic chat log, approval path or execution capability is added.

## Consequences

Users receive contextual natural replies while deterministic policy remains authoritative. A
model-derived interpretation may add one confirmation round but cannot silently broaden scope.
The response call adds bounded latency and token cost measured separately from Task Understanding.
Evaluation covers semantic-candidate precision, false acceptance, required-field coverage,
fallback behavior and prompt-injection resistance.

## Related Documents

- [ADR-019](ADR-019-interactive-clarification-resume.md)
- [ADR-020](ADR-020-chat-first-task-workspace.md)
- [ADR-021](ADR-021-natural-language-clarification-resolution.md)
- [Conversational Task Workspace](../design/conversational-task-workspace.md)
- [Task Understanding and Planning](../task-understanding-and-planning.md)
