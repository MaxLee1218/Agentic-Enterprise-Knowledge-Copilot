# Architecture

## 1. Current-state constraint

The implemented runtime is a complete but Supplier Quality-specific vertical slice. It already has
one LangGraph, one Task lifecycle, one Registry/Executor path, Evidence, Approval, Persistence,
API, frontend, observability, audit, and deterministic verification. UC2 reuses those facilities.
It does not copy the Graph or create a Finance Agent.

The current code cannot accept UC2 merely by adding prompt text. `TaskType`, `TaskConstraints`,
Artifact types, task-understanding schemas/prompts, the fixed plan, step input builder, tool
schemas, permission purpose, database schema registry, analytics model, report model, verifier
adapter and evaluation harness are bound to Supplier Quality. The exact findings are recorded in
[Platform reuse audit](platform-reuse-audit.md).

## 2. Target architecture

```text
API / CLI / Frontend
        |
        v
Shared Task Intake + Trusted Identity Context
        |
        v
Task Type Classification
        |
        v
Domain Capability Manifest Registry
  | supplier_quality_analysis.v1
  ` accounts_payable_analysis.v1
        |
        v
Shared Planner -> Shared deterministic Plan Validator
        |
        v
Shared LangGraph / Policy / Approval
        |
        v
Shared ToolRegistry -> Shared ToolExecutor
  | knowledge_search (domain profile)
  | database_query  (versioned template profile)
  | analysis_engine (versioned operation profile)
  ` report_generator (versioned report profile)
        |
        v
Shared Evidence / Verification / Persistence / Audit
```

The `DomainCapabilityManifest` is a small, code-owned, deny-by-default mapping justified by two
real use cases. It is not a dynamic plugin framework. Each entry freezes:

| Field | AP v1 value |
|---|---|
| `task_type` | `accounts_payable_analysis.v1` |
| `contract_schema_version` | `task-contract.v2` |
| capability names | existing four names only |
| knowledge profile | `accounts_payable_policy.v1` |
| query templates | the five `ap_*_v1` templates in tool contracts |
| analytics operations | the seven `ap.*.v1` operations in analytics design |
| rule set | `ap_rules.2026.1` |
| report schema/template | `accounts_payable_report_model.v1` / `accounts_payable_report.v1` |
| artifact types | `ACCOUNTS_PAYABLE_REPORT_JSON`, `ACCOUNTS_PAYABLE_REPORT_PDF` |
| plan rules | operation-aware dependency rules and one final report |
| limits | AP v1 limits below |
| policy purpose | `accounts_payable_analysis.v1` |

The Planner receives only the manifest for the classified task type and the already-filtered
Tool Registry definitions. It cannot select another domain's template, operation, rule set or
report profile.

## 3. Versioned tool-profile resolution

The capability names remain stable, but their business contracts are versioned profiles. A
`TaskStep` must persist `tool_version` and `contract_profile`; old stored steps without these fields
are upcast to the schema-fingerprint-matched Supplier Quality v1 profile. Registry resolution is
therefore `(tool_name, tool_version, contract_profile)`, not “whatever definition is latest.”

This prevents a UC2 schema from invalidating an old Supplier Quality Plan or checkpoint. Adapter
implementations may share internal infrastructure, but each profile has an exact input/output
schema. The Executor continues to validate, authorize, audit and register Evidence identically.

## 4. Execution flow

```text
START
 -> validate_request
 -> understand_task
 -> classify_domain and resolve manifest
 -> create_plan
 -> validate_plan (generic + AP manifest rules)
 -> policy_check
 -> knowledge_search
 -> database_query steps
 -> analysis_engine detection steps
 -> analysis_engine exception_summary
 -> report_generator
 -> verify_result (shared + AP verification profile)
 -> persist_result
 -> END
```

The Graph topology and frozen Task states are unchanged. Plans need not have exactly four steps:
AP has one knowledge step, one or more database steps, one analysis step per requested detection,
one summary analysis step, and exactly one final report step. The report depends on policy
Evidence and the summary; the summary depends on every requested detection. Database and
knowledge steps may run independently when dependencies allow.

## 5. Clarification behavior

The implemented state machine has no interactive clarification state. UC2 therefore follows the
real current behavior: missing required information produces recoverable
`TASK_INFORMATION_MISSING`, records `TASK_CLARIFICATION_REQUIRED`, transitions
`UNDERSTANDING -> FAILED`, and returns concrete missing fields. A corrected request creates a new
Task. UC2 must not claim conversational resume.

An explicit `time_range` is mandatory. Supplier omission means the trusted authorized supplier
scope. Legal entity omission is allowed only when trusted context resolves exactly one authorized
legal entity. Business unit omission means all authorized units. Exception types default to the
six v1 exception types produced by five detection operations. Currency omission means all
currencies in scope, analyzed separately.

## 6. Performance and termination boundaries

| Boundary | v1 hard limit | Behavior at limit |
|---|---:|---|
| calendar range | 366 inclusive days | fail `AP_TIME_RANGE_TOO_LARGE` |
| requested/authorized suppliers | 100 | wider access needs a new scoped task; never silently truncate |
| legal entities | 10 | fail validation |
| business units | 50 | fail validation |
| source invoice rows | 50,000 | query returns `truncated=true`; task fails recoverably and requests narrower scope |
| exception records | 5,000 | fail recoverably; summary cannot hide omitted findings |
| database row limit | 50,001 sentinel read | detect truncation while publishing at most 50,000 |
| Evidence items | 250 | batched calculation Evidence; every finding retains record keys inside a checksummed batch |
| report material-detail rows | 100 | management detail ranked deterministically; complete counts remain evidenced; JSON attachment holds up to 5,000 |
| Artifact | 25 MiB JSON, 15 MiB PDF | report failure, no publication |
| query statement / call | 8s / 10s | current database timeout semantics |
| analytics operation | 20s | at most two attempts for technical/timeouts |
| report call | 45s | at most two attempts |
| task deadline | 180s excluding approval wait | bounded failure; approval has its own expiry |

## 7. API and frontend impact

No `/v1/finance/*` API is added. Existing `/v1/tasks` submission, task/steps/evidence/artifact reads,
approval, cancellation and download routes remain the resource model. `TaskType` and `ArtifactType`
enums gain AP values; response shapes remain compatible.

The current frontend already has shared submission, history, timeline, Evidence and Artifact
views. Minimal implementation changes are a task-type selector/example, task-type badge, removal
of Supplier Quality-only empty text, and an AP report summary panel driven by safe report metadata.
Tenant, supplier, business-unit and legal-entity authority remain server-owned. The browser must
not supply trusted scope or select tools/templates.

## 8. Persistence and observability

Task, Contract, Plan, result, Evidence, Artifact, Approval, audit and checkpoint continue to use
the existing repositories and tenant-scoped rows. AP needs no domain-specific workflow table.
Contract JSON and plan JSON are versioned/upcast at repository boundaries.

Existing correlation fields remain: `task_id`, `trace_id`, `step_id`, `tenant_id`, `tool_name`,
status, latency, retry count, error type, Evidence IDs and Artifact IDs. Safe additional fields are
`task_type`, `contract_profile`, `operation_name`, `rule_set_version`, and `query_template_id`.
Invoice numbers, supplier bank data, payment references, tax IDs and monetary payloads are never
log tags.

## 9. API-independent MCP boundary

The repository now implements optional MCP despite the historical Supplier Quality v1.1 design
correctly excluding it. UC2 does not require or export an AP capability through MCP. If later
explicitly allowlisted, MCP must resolve the same domain manifest and pass through the existing
Policy, Approval, Registry/Executor, Evidence and Audit path. This current-repository fact is not
a conflict with UC2 and does not broaden v1.
