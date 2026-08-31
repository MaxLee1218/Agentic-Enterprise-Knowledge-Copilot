# ADR-018: Lightweight Proposed Plans and Deterministic Executable Plan Compilation

## Status

Accepted

## Date

2026-08-30

## Context

ADR-003 established a replaceable structured provider and deterministic validation gate, but it
also required the Planner model to emit the complete executable `TaskPlan`. That included Registry
versions, profiles, complete input/output schemas, retry settings and a frozen domain DAG. The
Supplier prompt was 12,121 characters and the Accounts Payable prompt was 51,637 characters. A
canonical full-profile AP `TaskPlan` serializes to about 187,719 characters. Real runs produced
both incomplete JSON and schema-invalid JSON before any business tool executed.

There is no evidence captured from those historical failures proving provider truncation or a
particular `finish_reason`; truncation remains a hypothesis. The code and measured payloads do
prove that the model was asked to reproduce execution facts already held by deterministic
authorities. Relaxing `PlanValidator` would hide that architectural mistake and weaken the frozen
governance chain.

## Decision

Continue using the application-owned synchronous `LLMProvider` port, injected infrastructure
adapters, strict Pydantic structured output and deterministic post-model validation established by
ADR-003. Task Understanding continues to emit only an intermediate candidate that deterministic
code binds to the trusted `TaskContract`.

The LLM emits only a non-executable `ProposedPlan`. Each `ProposedStep` contains a local step ID,
one domain-allowlisted capability, a purpose, optional semantic arguments and suggested
dependencies. It cannot contain tool versions, profiles, schemas, risk, approval, permissions,
tenant/role scope, retry, timeout or other execution authority.

`PlanCompiler` converts the untrusted proposal into the existing canonical `TaskPlan`. Its
authoritative inputs are the validated `TaskContract`, selected `DomainCapabilityManifest`,
`ToolRegistry`, frozen Supplier/AP plan factories and trusted runtime configuration. It binds exact
versions/profiles/schemas and canonical domain dependencies, ignores semantic arguments for
execution, records bounded normalization diagnostics and fails closed on incomplete or
out-of-domain capability sets. The existing `PlanValidator` still validates every compiled plan
before Policy, Approval or Executor access.

Structured-output recovery is layered and bounded:

1. provider retry remains owned by the provider adapter;
2. incomplete/invalid JSON gets an independent structured-output retry;
3. duplicate dependencies and frozen dependency ordering receive only unambiguous deterministic
   normalization;
4. schema or compilation defects receive targeted repair using the lightweight candidate,
   bounded validation summaries and capability view;
5. exhausted budgets raise a typed Planner failure.

Full raw responses are not logged, audited, persisted or checkpointed. Safe diagnostics contain
provider/model, attempts, latency, `finish_reason`, usage, response length/hash, parse/schema
status, bounded validation issues and repair type. A bounded raw candidate may exist in memory
only long enough to construct targeted repair input.

`ProposedPlan` remains an internal Planner boundary and is not persisted. The public API,
checkpoint/persistence models, canonical `TaskPlan`, policy/approval bindings, execution,
Evidence, Audit, Verification and Artifact contracts remain unchanged. Supplier Quality and AP
use the same Planner/Compiler architecture; their frozen business rules and production-readiness
claims do not change.

## Alternatives Considered

- Keeping full `TaskPlan` output and adding more prompt instructions was rejected because it keeps
  the redundant schema/version/profile copying burden and does not reduce output complexity.
- Relaxing `PlanValidator` was rejected because an invalid or unauthorized executable plan must
  never become acceptable model output.
- A permissive JSON repair library was rejected because guessing missing fields or business
  semantics is not a deterministic normalization.
- Persisting `ProposedPlan` was rejected because canonical `TaskPlan` is the existing recovery and
  execution contract; storing the untrusted intermediate adds migration and sensitive-data risk
  without execution value.
- Removing the LLM Planner entirely was rejected because semantic planning remains an intended
  boundary and future registered domains may have genuine bounded planning choices.

## Consequences

Planner prompts and completions are substantially smaller, execution metadata cannot be forged by
model output, and Supplier/AP always converge on their existing canonical plans. Failures are more
specific and operationally diagnosable. The current two frozen domains intentionally leave little
executable freedom to the LLM; purpose and semantic arguments are advisory, while domain factories
own the exact operation expansion.

Future Domain Router and clarification work can select a registered domain before this boundary,
then pass an explicit validated `TaskContract` and manifest into the same Planner/Compiler. This
ADR does not implement routing, clarification, conversation context, open-domain planning or MCP
capability adoption.

This ADR supersedes ADR-003. It changes the direct `TaskPlan` output decision and continues
ADR-003's provider port, understanding boundary and deterministic validation principles.

## Related Documents

- [ADR-003](ADR-003-llm-provider-and-structured-output.md)
- [ADR-009](ADR-009-multi-domain-capability-manifests.md)
- [Task Understanding and Planning](../task-understanding-and-planning.md)
- [Structured LLM Architecture](../llm-architecture.md)
- [Frozen Supplier Quality baseline](../design/design_baseline.md)
- [Accounts Payable design baseline](../use-cases/accounts-payable/design-baseline.md)
