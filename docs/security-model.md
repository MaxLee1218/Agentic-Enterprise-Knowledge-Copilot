# Security Model

The frozen Supplier Quality Analysis v1.1 design remains authoritative for identity, task state,
policy, approval, tool execution, Evidence, and verification. Stage 15 hardens those boundaries; it
does not add business capabilities or change the frozen four-tool scope.

## Security goals and protected assets

The system is designed to fail closed when untrusted content attempts to change authority, expand
scope, invoke an unregistered capability, expose sensitive data, or bypass verification. Protected
assets include enterprise knowledge, business data, sensitive fields, system prompts, tool
definitions and schemas, plans, Evidence, Artifacts, approvals, audit events, credentials,
connection configuration, internal paths, exceptions, and caller/tenant facts.

The current implementation prioritizes:

- least privilege and deny-by-default decisions;
- deterministic authorization before and immediately before execution;
- source, trust, and lineage metadata for untrusted content;
- safe output before Evidence, report, Artifact, API, audit, or log persistence;
- stable error/reason codes without reflecting the rejected value;
- a failed task or an evidence-insufficient result instead of an unsupported completion.

## Trust boundaries

| Source | Default trust | Authority |
|---|---|---|
| `SYSTEM_INSTRUCTION` | trusted | May define model behavior; cannot bypass deterministic policy |
| `INTERNAL_CONFIGURATION` | trusted after validation | Supplies server-owned limits and adapters |
| `APPROVAL_INPUT` | untrusted until schema, role, tenant, and fingerprint checks pass | May resolve only the exact pending action |
| `USER_INPUT` | untrusted | Business-request data only |
| `RETRIEVED_DOCUMENT` | untrusted | Factual source candidate only |
| `DATABASE_RESULT` | classified data | Facts from an approved read-only template only |
| `TOOL_OUTPUT` | untrusted until schema/output checks pass | Never grants instruction authority |
| `LLM_OUTPUT` | untrusted candidate | Must pass typed parsing and deterministic validation |

User text, documents, tool results, model results, and external errors cannot create roles, tools,
approvals, permissions, table/field access, or new state transitions. `SecurityFinding` retains a
finding ID, category, severity, source, rule, content hash, action, and optional field path; it
does not retain the matched malicious or sensitive value.

## Identity and trusted context

API, CLI, internal evaluation, and graph entry paths create or receive `TrustedCallerContext` and
derive `TrustedTaskContext`. These are the existing security contexts; there is no parallel
Principal model. They carry user, tenant, data scope, supplier scope, roles, authentication source,
demo marker, purpose, task/session/trace IDs, read-only/approval constraints, and deadline.

Roles and authorization facts are never read from task text or user JSON metadata. Metadata keys
that represent credentials are rejected at intake. Missing roles are denied for non-demo callers.
The checked-in API/CLI adapter is explicitly a demo identity: an empty demo role receives only the
`quality_analyst` fallback. This fallback is not authentication and is not suitable for production.

## Demo role and permission matrix

`PermissionMatrix` centrally evaluates task, Evidence, Artifact, cancellation, approval, report,
and tool actions. Unknown roles, purposes, tools, resources, and operations are denied.

| Permission | `quality_analyst` | `quality_data_approver` |
|---|---:|---:|
| Execute the four frozen tools | yes | yes |
| Read own task and Evidence | yes | yes |
| Read own published Artifact | yes | yes |
| Generate an internal report | yes | yes |
| Cancel own task | yes | yes |
| Resolve a gated action | no | yes, with exact approval binding |

Neither role can execute database writes, arbitrary SQL/Python, email, procurement, CAPA, record
deletion, an unregistered tool, a new enterprise connector, or MCP. A high-privilege role never
overrides the system-level v1.1 allowlist.

Authorization is enforced at plan validation, policy check, `ToolExecutor`, the database adapter,
approval service, task service, and Artifact service. `ToolExecutor` rechecks the current explicit
trusted context for every real attempt, so a persisted plan or direct executor call is not an
authorization grant.

## Database table and field access

The database boundary accepts only `supplier_quality_summary_v1` and
`supplier_quality_trend_v1`. The template access profile and `DataAccessPolicy` allow only the
required `incoming_inspections` and `suppliers` fields for the Supplier Quality purpose. The
SQLAlchemy AST validator rejects raw SQL, textual fragments, wildcards, unbound columns,
unregistered tables/fields, unapproved functions, and queries without a row limit. It resolves
aliases back to physical columns, so table aliases, field labels, joins, CTEs, and subqueries do
not hide lineage.

The real Database Tool re-evaluates the AST table/field set using the trusted roles and purpose
immediately before the read. It enforces tenant/date/supplier parameters, read-only connection,
timeout, row limit, normalized output columns, query fingerprint, Evidence, and safe structured
logging. Logs, API errors, reports, and ordinary audit summaries do not contain raw SQL.

## Prompt-injection defenses

Prompt-injection resistance is layered, not based on a promise that a keyword detector is
complete:

```text
source/trust separation
  -> bounded prompt sections and structured output
  -> ToolRegistry manifest
  -> Plan Validator and permission matrix
  -> approval policy
  -> ToolExecutor reauthorization
  -> read-only template/AST validation
  -> Evidence trust metadata
  -> Safety Verifier
  -> Output Guard and audit
```

Task-understanding prompts separate system rules, trusted context, output schema, and sanitized
untrusted user input. Planner prompts receive the immutable TaskContract and trusted Registry
manifest, not retrieved document instructions. The lightweight `PromptInjectionDetector` is only
a risk signal and content isolator. It removes instruction-shaped segments, preserves independent
business facts, marks sanitized or quarantined content, and records only hashes and safe findings.
Quarantined Evidence cannot support a final result.

## Sensitive-data registry

`SensitiveDataRegistry` recursively inspects keys, aliases, nested dictionaries, and lists.

| Canonical field | Classification | Ordinary output behavior |
|---|---|---|
| `personal_email`, `phone` | `CONFIDENTIAL` | mask |
| `bank_account` | `RESTRICTED` | retain last four only |
| `salary` | `RESTRICTED` | remove the field |
| `government_id` | `RESTRICTED` | mask |
| `password_hash`, `secret`, `token`, `password`, authorization/cookie fields | `SECRET` | block report/Evidence/Artifact; mask logs/API-safe summaries |

The registry is the shared policy for structured tool results, Evidence, report models, Artifacts,
API-safe summaries, audit metadata, and logs. Removed values are represented in
`RedactionRecord` by field path, classification, strategy, and a one-way hash only.

## Output Guard and Artifact safety

`OutputGuard` returns `ALLOWED`, `ALLOWED_WITH_REDACTIONS`, or `BLOCKED`. The same scanner handles
structured JSON and rendered content and checks sensitive keys, API/access/Bearer tokens,
passwords, connection strings, private-key markers, raw SQL, database URLs, internal absolute
paths, Python tracebacks, and system-prompt markers.

The production Report Tool scans its structured model before rendering and the rendered bytes
before persistence. `LocalArtifactRepository` independently scans again and persists the rewritten
safe JSON when redaction is possible. A non-repairable finding raises a stable
`SENSITIVE_OUTPUT_BLOCKED` path, creates no Artifact, is audited by the executor/workflow, and
prevents `COMPLETED`.

Artifact reads require task permission, caller ownership, tenant match, Artifact/task binding,
publication in the completed `TaskResult`, repository-root containment, regular-file status,
size, and checksum. Unknown, unpublished, cross-task, and cross-principal Artifacts are hidden.
Filenames are one safe component; absolute paths, `..`, nested paths, and storage-root escape are
rejected.

## API errors, logging, and audit

API handlers return stable codes and generic caller-facing messages. Unknown exceptions become
`INTERNAL_ERROR`; responses do not include tracebacks, module/file paths, SQL, connection strings,
environment values, tool arguments/results, or exception `repr`.

`SensitiveDataFilter` and the shared recursive redactor sanitize log messages, format arguments,
extra fields, Pydantic models, dataclasses, containers, and exception summaries. Tracebacks are
removed from ordinary log records. Allowed diagnostics are identifiers, hashes, counts,
classification, latency, status, and reason codes.

The append-only tool and workflow audit repositories retain safe events for successful, failed,
and denied tool calls; database decisions; prompt-injection findings; approval lifecycle;
Artifact creation/read and denial; task cancellation; permission denial; output redaction/block;
verification; and workflow failure/completion. Audit metadata is recursively redacted before
memory or SQLite persistence. High-risk execution fails if its required tool audit cannot be
written.

## Safety failure semantics

| Condition | Stable result |
|---|---|
| unknown caller/role/purpose | `UNKNOWN_PRINCIPAL`, `UNKNOWN_ROLE`, or `V1_1_CAPABILITY_NOT_ALLOWED`; task fails |
| unregistered/forbidden tool | `TOOL_NOT_ALLOWED` or plan validation failure; no call |
| unauthorized table/field | `TABLE_NOT_ALLOWED` / `FIELD_NOT_ALLOWED`; no query |
| invalid approval authority/binding | `APPROVAL_PERMISSION_DENIED` or approval conflict; target remains unexecuted |
| quarantined evidence leaves insufficient support | verification failure / evidence insufficient |
| secret or unsafe output | `SECRET_DETECTED` / `SENSITIVE_OUTPUT_BLOCKED`; no Artifact; task fails |
| unauthorized Artifact read | hidden/denied request; original task state is unchanged |
| internal exception | safe typed technical failure or generic API error; never reflected verbatim |

Security violations are verifier errors, not warnings, and cannot result in `COMPLETED`.

## Known limitations and production replacements

- Prompt injection risk is reduced by layered deterministic controls; it is not eliminated.
- The rule-based injection and secret detectors are bounded and require continued evaluation.
- The role matrix and identity providers are demo/portfolio adapters, not OAuth, SSO, or enterprise
  IAM. Production must supply real authentication, role/scope refresh, and revocation.
- Local SQLite audit/checkpoint stores and filesystem Artifacts are not enterprise tamper-evident
  audit or object storage. Production needs retention, legal hold, access logging, and KMS-backed
  storage controls.
- Production requires a Secret Manager, credential rotation, database-native least privilege,
  centralized observability redaction, and incident response.
- The system remains read-only and supports no business writes, CAPA automation, email,
  procurement, arbitrary enterprise schemas, new live integrations, or MCP execution.

Security regression behavior is measured by the fixed synthetic Stage 15 cases documented in
[Offline Agent Evaluation](evaluation.md).
