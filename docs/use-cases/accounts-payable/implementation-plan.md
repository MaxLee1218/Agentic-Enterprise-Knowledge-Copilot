# Implementation Plan and Architecture Acceptance

## Delivery rule

Stages are sequential acceptance boundaries, not one large implementation change. Each stage runs
the Supplier Quality regression gate whenever it touches shared code. No stage may describe UC2 as
implemented before its own acceptance criteria and all prior stages pass.

## Stage 0 — Design approval and contract freeze

**Status:** `COMPLETE — 2026-08-22`  
**Acceptance record:** [Stage 0 design review](design-review.md)  
**Frozen baseline:** [accounts-payable-design.v1.0](design-baseline.md)

- **Goal:** freeze this design and record ADR-009/010/011.
- **Scope/work:** consistency review and architecture/security/data-owner decision record.
- **Likely files:** this directory and `docs/adr/ADR-009..011`.
- **Contracts:** freeze names, versions, formulas, limits and non-goals in these documents.
- **Tests:** `scripts/check_docs.py`, link, terminology and unresolved-decision checks.
- **Migration:** none.
- **Security:** finance data owner and tenant/approval review.
- **Backward compatibility:** frozen `docs/design/` unchanged.
- **Acceptance:** no blocking design question; ADRs accepted; all nine gates below pass.
- **Artifacts:** approved design set and review record.
- **Out of scope:** production code, schema migration, seed data.

## Stage 1 — Multi-domain contracts and manifest routing

**Status:** `COMPLETE — 2026-08-22`  
**Compatibility artifact:** [Stage 1 compatibility matrix](stage-1-compatibility-matrix.md)  
**Execution boundary:** AP Contract validates; AP planning/input/tool execution remains disabled.

- **Goal:** represent both task types without changing Supplier Quality semantics.
- **Scope/work:** add AP enums/constraints/artifacts; versioned Contract/Plan fields and historical
  upcaster; minimal `DomainCapabilityManifestRegistry`; select understanding/plan/input/verifier
  profiles by trusted Task type.
- **Likely files:** `contracts/{enums,tasks,plans,artifacts}.py`, `services/task_intake.py`, new
  `services/domains/` or the smallest architecture-conforming equivalent, persistence serializers.
- **Contracts:** `task-contract.v2`, AP scope, `tool_version`, `contract_profile`.
- **Tests:** union validation, scope attacks, old JSON/Plan/checkpoint fixtures, manifest deny
  cases, current UC1 contract suite.
- **Migration:** JSON upcast on read; no bulk rewrite and no workflow table.
- **Security:** Task type/purpose and manifest selected from trusted validated Contract.
- **Backward compatibility:** missing version fields map only to exact historical SQ fingerprints.
- **Acceptance:** old tasks load/run; AP Contract validates but no AP tool execution is enabled.
- **Artifacts:** contract schemas and compatibility matrix.
- **Out of scope:** AP tables and calculations.

## Stage 2 — AP business schema and deterministic seed

**Status:** complete on 2026-08-22. See
[Stage 2 schema and seed report](stage-2-schema-and-seed.md).

- **Goal:** create the isolated synthetic AP fact model.
- **Scope/work:** implement legal entity, business unit, PO, invoice and payment models; additive
  business-schema migrations; composite tenant FKs; deterministic AP seed/profile.
- **Likely files:** `tools/database/models.py`, a business migration package, `seed.py`,
  `scripts/seed_demo_database.py`, Docker business init entrypoint.
- **Contracts:** database schema `accounts_payable.v1`; seed profile `ap-demo-dataset.v1`.
- **Tests:** model checks, uniqueness/FKs, cross-tenant rejection, SQLite/PostgreSQL
  upgrade/downgrade, deterministic checksum and UC1 seed preservation.
- **Migration:** execute the plan in database design; runtime role SELECT-only.
- **Security:** no bank/payment-reference fields; tenant/entity/unit indexes and FKs.
- **Backward compatibility:** existing Supplier/Quality tables and seed counts unchanged.
- **Acceptance:** repeat seed yields identical profile and all labeled patterns.
- **Artifacts:** migration revision, synthetic DB/profile and schema docs.
- **Out of scope:** query templates or task execution.

## Stage 3 — Controlled AP policy corpus and rule manifest

**Status:** `COMPLETE — 2026-08-23`

**Acceptance artifact:** [Stage 3 policy corpus and rules report](stage-3-policy-corpus-and-rules.md)

**Execution boundary:** policy publication validates; AP query, analytics and task execution remain disabled.

- **Goal:** make policy text and executable rules version-consistent.
- **Scope/work:** author sanitized four-document fixture set; rule manifest/schema/loader; atomic
  ingestion and binding consistency command; snapshot metadata.
- **Likely files:** policy data package, knowledge bootstrap/CLI, RAG contract fixtures, config.
- **Contracts:** `accounts_payable_policy.v1`, `ap_rules.2026.1`, binding schema.
- **Tests:** checksum/effective dates, missing/stale binding, tenant namespace, malicious document,
  re-index version retention.
- **Migration:** no database migration; controlled content version publication.
- **Security:** document/rule owner approval, classification, no prompt authority.
- **Backward compatibility:** Supplier Quality collection/snapshot remains separate.
- **Acceptance:** every rule resolves to one exact document version/chunk/checksum.
- **Artifacts:** document fixtures, manifest and ingestion report.
- **Out of scope:** analytics execution.

## Stage 4 — AP database query templates

**Status:** `COMPLETE — 2026-08-23`

**Acceptance artifact:** [Stage 4 AP database query templates report](stage-4-ap-database-query-templates.md)

**Execution boundary:** the AP database adapter is explicitly composable for governed tests and
later workflow integration; the AP domain manifest remains disabled and analytics are absent.

- **Goal:** expose only the five frozen AP read models.
- **Scope/work:** add schema/access profile and SQLAlchemy Select templates; scope predicates,
  sentinel row handling, normalization/output Evidence metadata.
- **Likely files:** `tools/database/{query_templates,schema_registry,tool,result_normalizer}.py`,
  `policies/data_access.py`, domain input builder.
- **Contracts:** `accounts_payable_database.v1` tool profile and five template schemas.
- **Tests:** exact columns/rows, empty/truncated, unauthorized table/field/scope, raw SQL/write,
  query fingerprint, SQLite/PostgreSQL parity.
- **Migration:** none beyond Stage 2.
- **Security:** read-only transaction, AST/template/table/column/function allowlists.
- **Backward compatibility:** both Quality templates retain exact schema/fingerprint behavior.
- **Acceptance:** AP rows are reproducible, scope-complete and DATABASE-evidenced.
- **Artifacts:** registered profile and access audit.
- **Out of scope:** exception classification.

## Stage 5 — Deterministic AP analytics

**Status:** `COMPLETE — 2026-08-23`

**Acceptance artifact:** [Stage 5 deterministic AP analytics report](stage-5-deterministic-ap-analytics.md)

**Execution boundary:** the AP analytics adapter is explicitly composable over governed Stage 3
and 4 Evidence; the AP domain manifest remains disabled and no report or verifier profile exists.

- **Goal:** implement all frozen detection, aggregation and metric operations.
- **Scope/work:** operation union, normalization v1, cross-record consistency, Decimal/date rules,
  batching, materiality and lineage.
- **Likely files:** `tools/analytics/` plus AP domain operation modules.
- **Contracts:** `accounts_payable_analytics.v1`, operation schemas/results.
- **Tests:** every formula, equality/adjacent boundaries, null/empty, currency, multi-payment,
  zero-PO, duplicate nonexamples, deterministic batch checksums.
- **Migration:** none.
- **Security:** verify Task/tenant Evidence ownership and rule manifest before calculating.
- **Backward compatibility:** Quality analytics schemas/formulas stay selectable unchanged.
- **Acceptance:** labeled unit/integration fixtures have exact expected records and amounts.
- **Artifacts:** CALCULATION Evidence fixtures and formula catalogue.
- **Out of scope:** fuzzy duplicates, three-way/multi-payment analytics.

## Stage 6 — AP Evidence and verifier profiles

**Status:** `COMPLETE — 2026-08-23`

**Acceptance artifact:** [Stage 6 AP Evidence and verifier profiles report](stage-6-ap-evidence-and-verifier-profiles.md)

**Execution boundary:** the verifier profile is composable and selected by its exact manifest ID;
the AP manifest remains disabled and no Stage 7 report Artifact or Stage 8 workflow path is enabled.

- **Goal:** enforce claim, numeric, policy and relationship correctness.
- **Scope/work:** AP report-to-claim adapter; AP metadata, policy-binding and consistency verifier
  rules; per-domain Safety allowlists; money/currency baselines.
- **Likely files:** `evidence/{citations,validators,workflow}.py`, `contracts/verification.py`.
- **Contracts:** AP claim mapping and verifier profile version.
- **Tests:** missing/wrong lineage, duplicate baseline, numeric/currency mismatch, rule mismatch,
  cross-record/tenant and truncation failures.
- **Migration:** none.
- **Security:** restricted-field exposure is a verification error; no warning downgrade.
- **Backward compatibility:** existing Composite Verifier order and UC1 candidates remain valid.
- **Acceptance:** all AP material claims resolve and tampering prevents COMPLETED.
- **Artifacts:** verifier matrix/results.
- **Out of scope:** report rendering.

## Stage 7 — AP report model and JSON/PDF

**Status:** `COMPLETE — 2026-08-23`

**Acceptance artifact:** [Stage 7 AP report model and renderers report](stage-7-ap-report-model-and-renderers.md)

**Execution boundary:** the AP report adapter is explicitly composable from governed Stage 3–6
inputs; the AP manifest remains disabled and the shared workflow does not invoke it before Stage 8.

- **Goal:** produce management-ready AP Artifacts from verified structured inputs.
- **Scope/work:** AP model/composer/presentation, JSON/PDF renderer profile, aggregate/detail modes,
  atomic Artifact store and parser.
- **Likely files:** `tools/reporting/`, Artifact enum/service/frontend metadata.
- **Contracts:** `accounts_payable_report_model.v1`, `accounts_payable_report.v1`, AP Artifact types.
- **Tests:** strong model, deterministic render, round trip, limits, Output Guard, citations,
  aggregate detail exclusion, corrupt Artifact.
- **Migration:** none; current Artifact JSON persistence accepts new enum after application rollout.
- **Security:** report-level bank/tax/reference block and tenant ownership.
- **Backward compatibility:** SQ model/renderers/filenames/types unchanged.
- **Acceptance:** JSON/PDF share canonical values and independently verify.
- **Artifacts:** golden synthetic reports.
- **Out of scope:** external delivery or business action.

## Stage 8 — Understanding, Planner and shared Graph integration

**Status:** `COMPLETE — 2026-08-23`

**Acceptance artifact:** [Stage 8 understanding, planner and shared Graph report](stage-8-understanding-planner-and-shared-graph.md)

**Execution boundary:** AP is enabled only through the existing internal Task Service and shared
Graph. Public API/frontend integration remains disabled until Stage 9; production readiness is not
claimed.

- **Goal:** run AP Contract through the existing lifecycle.
- **Scope/work:** AP understanding prompt/schema adapter, manifest-filtered planner, AP Plan rules,
  AP input builder, policy check and fixed offline plan; remove shared-runtime Quality defaults.
- **Likely files:** `llm/{schemas,prompts,planning}.py`, `services/workflows/`, `agent/runtime.py`,
  `bootstrap/container.py`, permission/approval services.
- **Contracts:** planning/repair prompt versions and AP Plan profile.
- **Tests:** natural language, missing information FAILED path, wrong Plan repair, approval pause/
  edit/resume, retries/replan, empty path and end-to-end Evidence.
- **Migration:** checkpoint upcaster from Stage 1.
- **Security:** trusted scope never enters model authority; profile/template/operation escape denied.
- **Backward compatibility:** full UC1 graph and approval replay regression.
- **Acceptance:** AP synthetic tasks reach correct terminal state through one Graph.
- **Artifacts:** execution traces and audit records.
- **Out of scope:** API/frontend changes.

## Stage 9 — Permission, API and frontend integration

- **Goal:** expose UC2 through existing Task resources and console.
- **Scope/work:** finance role/purpose/data profiles; extend public enums; task selector/badge,
  generic copy and AP summary; regenerate OpenAPI types.
- **Likely files:** `policies/`, `security/`, `api/`, `services/`, `frontend/src/`, OpenAPI snapshot.
- **Contracts:** unchanged endpoint shapes with added enum values; scoped AP public metadata.
- **Tests:** API contract, role/scope matrix, list/detail/download isolation, component and browser E2E.
- **Migration:** none.
- **Security:** browser cannot set tenant/role/scope/tool; detail mode is server-derived.
- **Backward compatibility:** existing API clients accepting enum expansion remain valid; document
  enum addition for exhaustive clients.
- **Acceptance:** both use cases submit/inspect/download in one console.
- **Artifacts:** regenerated OpenAPI/types and UI evidence.
- **Out of scope:** `/v1/finance/*`.

## Stage 10 — AP evaluation and security gates

- **Goal:** quantify correctness and attacks independently of UC1.
- **Scope/work:** AP dataset/fixtures/harness adapters/evaluators/baseline; threat cases and
  performance fixture.
- **Likely files:** `evaluation/`, `tests/security/`, evaluation docs/reporters.
- **Contracts:** AP dataset `1.0.0`, AP metric records.
- **Tests:** dataset validation/oracle isolation, every case/metric, deterministic repeat run.
- **Migration:** none.
- **Security:** zero unauthorized execution/leakage gates.
- **Backward compatibility:** Supplier evaluation default behavior or explicit dataset selection is
  preserved; its baseline is never overwritten by AP.
- **Acceptance:** all release gates in evaluation plan pass.
- **Artifacts:** AP baseline and versioned report.
- **Out of scope:** live production data/model claims.

## Stage 11 — Full local enterprise E2E

- **Goal:** prove browser-to-RAG-to-business-DB-to-Artifact behavior on the local topology.
- **Scope/work:** additive AP business seed service, controlled AP RAG ingestion, real PostgreSQL
  business reads, persistence/checkpoint restart and frontend workflow.
- **Likely files:** Compose, Docker init, local E2E scripts/tests/docs.
- **Contracts:** deployed versions exactly match prior stages.
- **Tests:** clean and mixed-exception tasks, approval restart, JSON/PDF download and checksum.
- **Migration:** run business migration then seed; Copilot migrations remain separate.
- **Security:** SELECT-only DB role, backend-only RAG, tenant isolation and no secrets in output.
- **Backward compatibility:** Supplier Quality local E2E runs in the same topology.
- **Acceptance:** reproducible fresh-volume run passes both vertical slices.
- **Artifacts:** E2E report/log IDs without sensitive payloads.
- **Out of scope:** production ERP/SAP/MCP integrations.

## Stage 12 — Regression and production-readiness review

- **Goal:** decide readiness using evidence, not implementation claims.
- **Scope/work:** full Python/frontend/static/build/Compose/evaluation suites; migration, backup,
  rollback, retention, operations, performance and threat review.
- **Likely files:** CI, operations/deployment docs and readiness report; production code only for
  review findings approved in scope.
- **Contracts:** verify all deployed schema/profile/rule/report/dataset versions.
- **Tests:** UC1 + UC2 + shared platform full gate and production-config validation.
- **Migration:** dry-run upgrade/backup/restore; rollback only isolated.
- **Security:** finance owner, architecture and security sign-off; zero P0/P1 blockers.
- **Backward compatibility:** old Task/Artifact/Evidence/Checkpoint/API fixtures all load; UC1
  baseline unchanged.
- **Acceptance:** documented production gate passes or status remains NOT READY with blockers.
- **Artifacts:** readiness report, test/evaluation manifests and migration evidence.
- **Out of scope:** automatic rollout or unsupported ROI claim.

## Architecture acceptance gates

| Gate | Result | Evidence in design |
|---|---|---|
| 1 Business scope | PASS | exact v1 taxonomy and non-goals in README/domain model |
| 2 Contracts | PASS | Task, Planner, Tool, Analytics and Report contracts are versioned and named |
| 3 Reuse | PASS | one Graph/Registry/Executor/Evidence/Persistence/API; manifest selects profiles |
| 4 Determinism | PASS | every amount/date/threshold rule is explicit in analytics design |
| 5 Evidence | PASS | database/calculation/document lineage and claim mapping are frozen |
| 6 Security | PASS | read-only, tenant/dimension scope, allowlists, classifications and threats defined |
| 7 Backward compatibility | PASS | historical upcast and three-suite regression gate defined |
| 8 Evaluation | PASS | normal/exception/boundary/policy/security/planning/recovery cases and metrics defined |
| 9 Implementation readiness | PASS | no unresolved core decision; accepted ADRs provide implementation authority |

## Remaining non-blocking risks

- Synthetic AP data may underrepresent production ERP data-quality shapes; connector-specific
  profiling is a later integration activity, not a change to v1 formulas.
- The 50,000-row and Artifact limits need performance confirmation in Stage 10; implementation may
  tighten but not silently loosen them.
- Policy owners must supply approved document text and threshold values for each deployment;
  failure to supply them blocks tasks safely through the defined error model.
- `NUMERIC(20,4)` and configured currency scales cover v1 synthetic currencies; adding crypto or
  unusually scaled currencies requires a contract version.

## Design consistency review

The final cross-document review resolves each canonical concept without an implicit rename:

| Concept | Task/DB | Analytics | Evidence/Verifier | Report/Evaluation |
|---|---|---|---|---|
| invoice amount | `gross_amount` / `NUMERIC(20,4)` | duplicate, variance, missing-PO, summary input | Decimal + currency baseline | `gross_amount`; exact per-currency assertions |
| PO amount | `approved_amount` | variance denominator | signed/absolute Decimal operands | PO findings and variance assertions |
| payment amount | `payment_amount` | overpayment input | Decimal + currency baseline | payment findings/overpayment assertions |
| date cohort | inclusive `time_range`, anchored on `invoice_date` | common population | scope and snapshot checks | report scope and boundary cases |
| payment timing | `due_date`, `payment_date` | `days_late`, `days_early` calendar days | exact integer claims | payment findings and exact-day cases |
| exceptions | six `APExceptionType` values | five detections + summary/rate | typed Calculation records | matching sections and labeled fixtures |
| materiality | policy/requested/effective threshold by currency | post-detection WARNING/FINDING | rule/document binding | material exceptions and relaxation attacks |
| versions | contract/profile/template/rule/snapshot | operation/engine/rule | source/checksum/binding | report/generator/dataset/baseline |

`invoice_total` and `total_invoice_value` are not alternative field names. User-facing prose may
say “invoice amount,” but the serialized contract always maps it to `gross_amount`. Counts use
unique opaque invoice record keys exactly as defined in analytics; no report or evaluator invents
a different denominator.

Supplier Quality and AP may later feed a separately designed supplier-risk use case, but UC2 v1
does not join quality defects, spend and invoice exceptions or calculate a cross-domain score.

## Final design status

All core choices are resolved and the architecture decisions are recorded as Accepted ADRs.

```text
USE CASE 2 DESIGN STATUS:
READY FOR IMPLEMENTATION
```
