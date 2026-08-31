# ADR-003: Structured LLM Provider and Deterministic Planning Gate

## Status

Superseded

Superseded by [ADR-018](ADR-018-deterministic-plan-compilation.md).

## Date

2026-07-31

## Context

The Stage 10 LangGraph workflow uses a fixed Supplier Quality plan. Stage 11 requires replaceable
task understanding and planning without changing frozen domain contracts or allowing model text
to authorize or execute tools. Provider failures, structured output, retries, plan repair, and
runtime replan need stable and testable semantics.

## Decision

Use an application-owned synchronous `LLMProvider` port with infrastructure adapters under
`copilot.llm`. The synchronous interface matches the existing synchronous LangGraph and tool
runtime and avoids nested event loops. DeepSeek is the first real adapter; `MockLLM` remains the
default test adapter.

All business output uses strict Pydantic structured output. Task Understanding first produces an
intermediate model, then deterministic code binds it to the frozen `TaskContract`. Planner produces
the existing `TaskPlan`, using only a manifest dynamically derived from ToolRegistry. Every plan
passes the existing deterministic PlanValidator before policy or execution.

Provider Retry, pre-execution Plan Repair, and post-execution Replan use distinct nodes, reasons,
counters, limits, and audit events. Prompt and schema versions are explicit call metadata.
Checkpoints hold only structured values and safe summaries; prompts, responses, secrets, and raw
business payloads are excluded.

## Alternatives Considered

- Direct provider calls in agent nodes were rejected because they couple orchestration to a vendor
  and make timeout, retry, error, and secret handling inconsistent.
- Free-form Markdown plans were rejected because they cannot be deterministically validated.
- A second TaskContract/TaskPlan was rejected because it would conflict with the frozen design.
- Prompt-only tool restrictions were rejected because model text cannot enforce authorization.
- An asynchronous provider interface was deferred because the current graph and tool executor are
  synchronous; a future async runtime may add an independently reviewed adapter.

## Consequences

Provider replacement and rollback occur at the composition root. CI can cover all paths without
network access. Prompt changes and schemas are versioned and testable. ADR-018 removes verbose
executable `TaskStep` output from the model while retaining this ADR's provider, understanding and
deterministic validation boundaries. Task Understanding cannot independently bootstrap trusted
identity or authorization fields; those remain deterministic inputs as intended.

## Related Documents

- [Structured LLM Architecture](../llm-architecture.md)
- [Task Understanding and Planning](../task-understanding-and-planning.md)
- [Frozen design baseline](../design/design_baseline.md)
- [LangGraph orchestration ADR](ADR-002-langgraph-orchestration.md)
