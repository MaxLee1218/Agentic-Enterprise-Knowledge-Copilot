# Stage 8 — AP Understanding, Planner and Shared Graph Integration

**Status:** `COMPLETE — 2026-08-23`  
**Understanding profile:** `accounts_payable_understanding.v1`  
**Plan profile:** `accounts_payable_plan.v1`  
**Verifier profile:** `accounts_payable_verifier.v1`  
**Execution boundary:** AP tasks can execute through the existing internal Task Service and shared
LangGraph. Public API identity selection and frontend exposure remain Stage 9; production
readiness is not claimed.

## Delivered scope

Stage 8 connects the frozen Stage 1–7 Accounts Payable profiles to the existing governed task
lifecycle. It does not create an AP-specific graph or bypass Policy, Approval, Registry/Executor,
Evidence, Audit, Checkpoint, Artifact, Observability or Verification.

- `APTaskUnderstandingOutput` and the versioned understanding prompt parse explicit date, legal
  entity, supplier, business-unit, currency, exception, materiality and output candidates.
- `LLMPlanningService` merges candidates with server-owned `TrustedTaskContext`. Tenant, purpose,
  authorized scope, rule-set identity/version/checksum, policy snapshot, materiality and deadline
  never become model authority. Omitted legal entity is accepted only when trusted scope has
  exactly one entity; requested thresholds may tighten but never relax policy.
- `AccountsPayableAnalysisPlanFactory` and the offline planner emit the same canonical DAG. The
  full profile has 14 steps: one controlled policy retrieval, five read-only database datasets,
  five deterministic detections, two deterministic aggregations and one report.
- `PlanValidator` requires the exact exception-to-template/operation set and exact detection,
  aggregation and report dependencies. A wrong profile, version, schema, template, operation,
  cross-task step or dependency fails before tool execution; eligible structural mistakes enter
  the existing bounded Plan repair path.
- `StepInputBuilder` derives every AP payload from the validated Contract, successful dependency
  results, current-task Evidence and trusted execution context. Database templates and analytics
  operations are code-owned canonical mappings, not planner-provided arguments.
- The existing registry now supports exact secondary version/profile registrations under the
  stable capability names. The Supplier Quality adapters remain primary; AP controlled knowledge,
  read-only database, deterministic analytics and reporting are selected only by the AP manifest.
- The existing policy and approval path recognizes finance roles and purpose. Optional task
  approval pauses immediately before the first controlled database read, requires
  `finance_approver`, permits only a complete replacement with the allowlisted `row_limit` edit,
  and resumes from the checkpoint without replaying completed policy retrieval.
- The shared runtime selects the verifier profile from the Task manifest, emits domain-correct
  terminal summaries even when understanding fails before a Contract exists, and preserves the
  frozen empty-population semantics through a verified empty report.

## Execution and recovery behavior

The AP path uses the existing lifecycle:

```text
Input -> Understanding -> Classification -> Plan -> Validation -> Policy / Approval
      -> Registry / Executor -> Evidence -> Report -> Verification -> TaskResult
```

Read failures use each canonical step's bounded retry policy. Exhausted recoverable failures may
enter the existing bounded replan path, where the Plan version must increase, successful
non-report steps are immutable and the remaining-step budget is enforced. Permission, scope,
profile and policy failures are not replannable.

A truly empty invoice population remains a successful business result: all five database calls
emit scoped empty Database Evidence; all seven analytics operations emit explicit empty results;
the report states that no invoice records were available and does not claim that no compliance
issues exist; verification may then permit `COMPLETED`.

## Security and compatibility gates

- AP detail access is derived only from trusted `finance:ap.detail`; report text cannot grant it.
- Controlled policy retrieval verifies tenant, collection and immutable snapshot before emitting
  exact rule-bound Document Evidence.
- Database calls remain allowlisted, parameterized, read-only, tenant/entity/date/currency scoped,
  capped at 50,000 rows and represented by exact Database Evidence metadata.
- Analytics requires checksum-bound Document and Database parents; aggregations require all
  requested detection Calculation Evidence.
- The dynamic verifier uses domain-specific table, column, template, restricted-field and claim
  rules. It does not reuse Supplier Quality allowlists for AP.
- Existing Supplier Quality plan, tool selection, approval replay, verification and Artifact
  behavior remain independently registered and covered by regression tests.

## Acceptance coverage

Stage 8 coverage proves:

- natural-language understanding and explicit missing-information failure without tool calls;
- trusted scope and materiality enforcement, including entity and threshold escape denial;
- exact full/subset Plan construction, manifest-filtered tool profiles and operation escape denial;
- wrong-Plan repair and version-increasing bounded replan;
- a 14-step seeded AP task with one transient database retry, three Evidence types, one Artifact
  and `VerificationStatus.PASSED`;
- approval pause, legal row-limit edit, resume and no replay of completed work;
- a complete empty-dataset path with zero-row Database Evidence;
- unchanged Supplier Quality and registry compatibility behavior.

Local acceptance on 2026-08-23 reported `552` unit tests passed; `95` integration tests passed,
`8` live/external/PostgreSQL cases skipped and `3` unrelated MCP loopback-socket cases deselected
because this sandbox could not grant local port binding; and contract/smoke/security/E2E suites
reported `72 passed, 1 skipped`. The Stage 8-focused AP and adjacent regression selection reported
`55 passed`. The offline Supplier Quality regression evaluation passed `30/30` cases against its
baseline. Documentation, architecture dependency, Ruff format/lint and strict MyPy checks passed;
MyPy checked `443` files. The three deselected MCP network tests are an environment limitation and
are not represented as passing.

## Deferred work

Stage 8 does not add an AP selector to public API identity, expose AP task metadata in the web
console, add frontend AP summaries, or regenerate public API/client enum surfaces. Those remain
Stage 9. AP evaluation/security release gates, full deployed local enterprise E2E and operational
runbooks remain Stages 10–12. No invoice, purchase order, supplier, payment, bank or external-system
mutation is introduced.
