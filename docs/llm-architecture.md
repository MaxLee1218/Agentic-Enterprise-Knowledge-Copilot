# Structured LLM Architecture

## Scope

The structured LLM path supports the two explicitly selected governed domains without changing
their frozen execution contracts:

```text
TaskRequest + trusted scope
  -> Task Understanding candidate
  -> frozen TaskContract
  -> lightweight ProposedPlan
  -> deterministic PlanCompiler
  -> existing canonical TaskPlan
  -> existing PlanValidator
  -> Policy -> Approval -> ToolRegistry -> ToolExecutor
```

It does not route task types, clarify through multiple turns, discover open-domain/MCP tools,
create business rules or execute tools directly. Provider implementations under `copilot.llm`
implement the application-owned `copilot.services.llm.LLMProvider` port and are injected only at
composition.

## Provider and structured-output contract

Every provider accepts versioned messages, a Pydantic output type, correlation context and bounded
generation options. Success returns parsed output plus provider/model, latency, usage,
`finish_reason`, request ID, provider attempts and response length/hash.

`DeepSeekProvider` uses JSON-object mode as a transport hint, not as strict schema-constrained
decoding. The shared adapter independently parses JSON and validates the requested Pydantic model.
It accepts a native mapping/model, plain JSON or one complete JSON Markdown fence. It rejects
empty, truncated, prose-wrapped, non-object, missing, wrong-type and extra-field results. It never
guesses missing fields or repairs business semantics.

Only timeout, transport errors, 429, 502, 503 and 504 receive provider retries. Parse/schema
failures return diagnostics to the Planner recovery layer. Logs contain safe correlation and
diagnostic metadata, never authorization values, prompts or response bodies. Validation summaries
are limited to eight items and include only field path, error type, bounded expected shape and
received type.

`MockLLM` is the deterministic test provider. `OfflineMockLLM` emits the same minimal semantic
proposal for Supplier and AP; `PlanCompiler` performs domain-specific expansion.

## Planning authority

`ProposedPlan` is untrusted and non-executable. It carries only capability categories, purposes,
semantic hints and suggested dependencies from the already selected domain manifest. It has no
versions, profiles, schemas, risk, approval, roles, permission scope, tenant scope, retry, timeout
or idempotency authority.

`PlanCompiler` obtains all executable facts from:

- the validated `TaskContract` for business scope, period and deliverable;
- `DomainCapabilityManifest` for the exact capability/profile boundary;
- `ToolRegistry` definitions for exact versions, schemas and runtime metadata;
- code-owned Supplier/AP plan factories for operation expansion and frozen dependencies;
- runtime/Policy configuration for authorization and execution controls.

Semantic proposal arguments cannot populate executable step input. `StepInputBuilder` continues to
derive every runtime payload from the Contract, successful StepResults, Evidence and trusted
execution context. The compiled existing `TaskPlan` is validated and persisted; the proposal is
not persisted.

## Retry, repair and replan

- Provider retry repeats only eligible transient transport requests.
- Structured-output retry repeats an invalid/empty/incomplete JSON response within its own budget.
- Deterministic normalization handles only duplicate dependency edges and frozen dependency
  ordering.
- Targeted Plan Repair sends the small candidate, bounded validation errors, minimal Contract view
  and allowed capabilities; it never resends executable schemas.
- Runtime Replan remains separately bounded and only follows an eligible frozen state-machine
  event. Successful non-report steps and Evidence remain immutable.

All paths compile and validate again. No proposal or failed compilation reaches Policy or tool
execution. The specific typed Planner cause is preserved in safe Task error details while the
frozen public lifecycle and error compatibility remain intact.

Prompt versions are `task-understanding-v2`, `planner-v3`, `plan-repair-v3` and `replan-v3`.
Planning schema context is `proposed-plan.v1`; the compiled Plan retains the existing frozen
contract/profile versions.

See [ADR-018](adr/ADR-018-deterministic-plan-compilation.md) and
[Task Understanding and Planning](task-understanding-and-planning.md) for the decision, payload
measurements, test harness and authority audit.
