# Task Understanding and Planning

## Trust boundary

The Task Understanding system message labels the original request as untrusted data. It forbids
tool creation, planning, scope or permission inference, invented suppliers/time ranges, prompt
disclosure, and policy bypass. Current time is supplied by deterministic code. An explicit year
and quarter are required by the frozen v1 design; an unqualified `Q1` fails safely.

The intermediate result contains:

- a candidate goal and the only allowed TaskType;
- supplier entities;
- explicit year and quarter;
- the frozen report type/language/sections;
- read-only, metrics, and max-step declarations;
- explicit missing-information items.

Deterministic post-processing prevents that output from changing tenant, data scope, approval,
deadline, authorized supplier scope, report request, read-only behavior, or system limits.

## Planner manifest

`PlannerToolManifestBuilder` reads the live `ToolRegistry` in stable name order. It exposes name,
bounded description, input/output schemas, risk, read-only behavior, approval metadata, and
idempotency. A visibility predicate removes capabilities unavailable to the caller or task mode.
There is no hand-maintained prompt tool list.

The PlanValidator returns structured errors with code, step, field, message, repair hint, and
repair eligibility. Its generic rules cover task binding, version and step limits, Contract
capabilities, registry membership, type mapping, and exact Schema identity. The Supplier Quality
rule set separately enforces Database -> Analytics and Knowledge/Analytics -> Report dependencies.

## Graph behavior

The optional LLM graph path is:

```text
understand_task -> classify_task -> create_plan -> validate_plan
                                              invalid and repairable
                                                -> repair_plan -> validate_plan
                                              valid
                                                -> policy_check -> execution
verify_result -> replan -> validate_plan -> policy_check
```

LangGraph checkpoints after each node. Domain facts remain in the workflow repository; candidate
plans and previous verification attempts are retained for recovery/audit. Successful tool steps,
Evidence IDs, and the active report Artifact are not regenerated on ordinary checkpoint resume.

The frozen state machine has no clarification state. Missing required information therefore emits
`TASK_INFORMATION_MISSING`, transitions `UNDERSTANDING -> FAILED`, and requires a new corrected
TaskRequest. Database empty results remain successful business facts and do not trigger Replan.

## Running

Offline CI and normal development use the deterministic path or an injected `MockLLM`.

```bash
pytest tests/unit/llm
pytest tests/integration/test_llm_planning_workflow.py
```

The real provider smoke test is opt-in and stops after plan validation without invoking business
tools:

```bash
LLM_PROVIDER=deepseek LLM_API_KEY=... python scripts/smoke_llm_planner.py
```

Without those settings it prints an explicit `SKIP` and exits successfully.

