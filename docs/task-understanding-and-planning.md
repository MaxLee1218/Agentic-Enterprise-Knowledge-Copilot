# Task Understanding and Planning

## Trust boundary

Task Understanding still treats the original request as untrusted data. It cannot create tools,
infer permissions, invent scope or dates, disclose prompts, or bypass policy. Deterministic code
binds its candidate to the trusted tenant, authorized business scope, approval, deadline,
deliverable, read-only rule and system limits to create the existing `TaskContract`.

The Planner receives that validated Contract and one explicitly selected domain capability
manifest. It does not select a domain, clarify missing information, discover global/MCP tools or
execute anything.

## ProposedPlan and canonical TaskPlan

The `planner-v3` model output is a non-executable `ProposedPlan`:

```text
TaskContract + domain capability view
  -> LLM ProposedPlan
  -> deterministic PlanCompiler
  -> existing canonical TaskPlan
  -> existing PlanValidator
  -> Policy / Approval / Executor
```

A `ProposedStep` has only `step_id`, `capability`, `purpose`, optional semantic `arguments` and
`depends_on`. Extra fields are forbidden. Its argument tree rejects execution-authority fields
such as versions, profiles, schemas, risk, approval, roles, tenant scope, retry and timeout. The
complete proposal must have unique IDs, local dependencies and an acyclic graph.

`PlannerToolManifestBuilder` still resolves the selected profiles from the live Registry, but its
model-facing result contains only the domain task type, capability name, bounded semantic
description and semantic argument hints. Versions, schemas, risk, approval, permissions and
runtime settings never enter the Planner prompt.

`PlanCompiler` requires the exact domain capability set, then delegates exact Supplier/AP
operation expansion to the existing canonical plan factories. It binds tool versions, profiles,
schemas, retry metadata and frozen dependencies from the `DomainCapabilityManifest`, Registry,
TaskContract and code-owned runtime configuration. Semantic arguments never become executable
step input; `StepInputBuilder` continues to derive inputs later from the Contract, successful
dependencies, Evidence and trusted execution context.

The current frozen domains intentionally have almost no executable Planner freedom:

| Field | Previous model responsibility | Model decides now? | New authority |
|---|---|---:|---|
| capability category | selected from full Registry-derived dump | bounded suggestion | selected domain manifest |
| tool version | copied into every step | no | Registry |
| contract profile | copied into every step | no | domain manifest |
| input/output schemas | copied into every step | no | Registry definition |
| retry/idempotency/timeout | copied into every step or prompt | no | Registry/runtime configuration |
| risk/approval/roles/permissions | present in manifest | no | Registry, Policy and trusted context |
| tenant/business scope | repeated in plan data | no | TaskContract and execution context |
| domain operation expansion | full executable DAG | no | Supplier/AP canonical factory |
| dependencies | full model responsibility | advisory | proposal plus frozen domain invariants |
| purpose/semantic hints | model | yes, non-authoritative | ProposedPlan only |

## Layered recovery and errors

Recovery budgets are independent and bounded:

1. DeepSeek retries only eligible network/429/502/503/504 failures.
2. Empty, truncated or invalid JSON receives a structured-output retry. The parser accepts plain
   JSON or one complete JSON fence; it does not guess missing fields.
3. Duplicate dependency edges and frozen domain dependency ordering receive only unambiguous
   deterministic normalization.
4. Schema, capability-set, compilation or final-validator defects receive targeted LLM repair
   containing the lightweight candidate, at most eight safe validation summaries, the minimal
   Contract view and allowed capabilities.
5. Exhaustion raises a typed `PlannerProviderError`, `PlannerTimeoutError`,
   `PlannerInvalidJsonError`, `PlannerSchemaValidationError`,
   `PlannerUnsupportedCapabilityError`, `PlannerCompilationError` or
   `PlannerRepairExhaustedError`.

The frozen public `PLAN_INVALID`/LLM error codes remain compatible. `TaskError.details` carries the
specific Planner root code, attempt count and node without exposing model content.

## Diagnostics and persistence

Each structured call can report provider/model, provider and workflow attempts, latency,
`finish_reason`, token usage, response character count/hash, JSON parse status, schema status,
bounded `field_path`/`error_type`/expected/received-type issues and repair type. Full prompts and
raw responses are not logged, audited, checkpointed, persisted or stored as Artifacts. A bounded
raw candidate exists in memory only for targeted repair.

Only the compiled existing `TaskPlan` is persisted. `ProposedPlan` is deliberately transient, so
existing task rows, checkpoints, approval resume, inspection and API/frontend contracts require no
migration.

## Payload audit

The table uses compact serialized JSON and the same Supplier/AP Contract fixtures before and after
the change. Character counts are reproducible and tokenizer-independent.

| Payload | Before | After |
|---|---:|---:|
| Supplier Planner prompt | 12,121 chars | 3,193 chars |
| AP Planner prompt | 51,637 chars | 3,545 chars |
| Supplier model-facing manifest | 7,070 chars | 909 chars |
| AP model-facing manifest | 45,929 chars | 1,131 chars |
| model output JSON Schema | 3,120 chars (`TaskPlan`) | 1,282 chars (`ProposedPlan`) |

For scale, deterministic canonical plans serialize to 7,325 Supplier characters and 187,719 AP
characters, while the current deterministic lightweight proposal is 680 characters for either
fixture. Those canonical sizes are structural proxies, not historical model completion telemetry.
Historical parse success, repair rate, token usage and latency were not captured reliably, so no
before values are claimed for them. A provider tokenizer is not bundled, so estimated token counts
are also unavailable.

## Graph behavior and running

The lifecycle remains:

```text
understand_task -> classify_task -> create_plan/compile -> validate_plan
                                                valid -> policy_check -> execution
verify_result -> bounded replan/compile -> validate_plan -> policy_check
```

The older graph `repair_plan` node remains a compatibility safety net if a compiled plan ever fails
final validation; ordinary schema and compilation repair now occurs inside the planning service
before any canonical plan is persisted.

Offline CI exercises both domains 100 times each and requires every proposal to compile to the
same valid canonical plan. The live DeepSeek harness is explicit opt-in and stops after validation:

```bash
LLM_PROVIDER=deepseek LLM_API_KEY=... \
  python scripts/smoke_planner_stability.py \
  --task "Analyze supplier quality for Q3 2026 and generate a PDF report." \
  --runs 20

LLM_PROVIDER=deepseek LLM_API_KEY=... \
  python scripts/smoke_planner_stability.py --scenario accounts-payable --runs 20
```

Without both settings it prints `NOT_VERIFIED` and does not silently fall back to mock.
