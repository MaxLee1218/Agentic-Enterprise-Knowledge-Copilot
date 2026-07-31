# Structured LLM Architecture

## Scope

Stage 11 adds an optional structured LLM path for the frozen
`supplier_quality_analysis.v1` workflow. It does not add a new task type, tool, state, permission
model, SQL capability, or executor. The deterministic fixed-plan path remains the offline default.

```text
TaskRequest + trusted scope
  -> Task Understanding candidate
  -> frozen TaskContract
  -> Planner candidate TaskPlan
  -> deterministic PlanValidator
  -> bounded Plan Repair when eligible
  -> Policy -> ToolRegistry -> ToolExecutor
```

Provider implementations live under `copilot.llm`. Application code depends on the
`copilot.services.llm.LLMProvider` port and injects an implementation at composition time. The
provider has no access to ToolExecutor, Evidence, databases, report rendering, or LangGraph.

## Provider contract

Every provider accepts versioned messages, a Pydantic output type, correlation context, and
bounded generation options. It returns `StructuredLLMResult` with parsed output, provider/model,
latency, usage, finish reason, request ID, and network-attempt count.

`DeepSeekProvider` uses the configured base URL, model, connect/read timeouts, trace header, User
Agent, and bounded retry. Only timeout, transport errors, 429, 502, 503, and 504 are retried. 401,
403, bad configuration, context rejection, invalid JSON, and schema errors are not retried.
Authorization values and provider response bodies are never placed in exceptions or logs.

`MockLLM` is the CI provider. It supports sequential and node-specific outcomes, malformed JSON,
schema-invalid output, and any typed provider failure. Complete prompts are captured only by this
test adapter.

## Structured output

The parser accepts a native mapping, Pydantic instance, plain JSON, or one complete JSON Markdown
fence. It rejects empty, truncated, prose-wrapped, non-object, missing-field, wrong-type, and
extra-field results. It never guesses fields or rewrites business data.

Task Understanding uses an intermediate LLM schema because the frozen `TaskContract` intentionally
does not contain `goal`, `entities`, or `missing_information`. Deterministic code then derives
quarter dates and binds the candidate to the already trusted tenant, data scope, supplier scope,
deadline, approval, deliverable, read-only rule, and system step limit.

Planner output is the existing frozen `TaskPlan`. The frozen `TaskStep` has no `arguments` field;
runtime tool input is still built by `StepInputBuilder` from validated Contract, prior StepResults,
and Evidence. Planner therefore selects registered tools, copies exact registered schemas, and
constructs dependencies only.

## Retry, repair, and replan

- Provider retry repeats the same network request after an eligible transient provider failure.
- Plan Repair occurs before tool execution when a parseable plan fails deterministic validation.
- Replan occurs only after the frozen state machine enters `REPLANNING` for an eligible runtime or
  verification event.

The counters and limits are independent. Every repaired or replanned candidate is validated again.
No candidate plan reaches policy or execution without a valid result.

Prompt versions are `task-understanding-v1`, `planner-v1`, `plan-repair-v1`, and `replan-v1`.
Schema versions are recorded independently in each `LLMCallContext`.

