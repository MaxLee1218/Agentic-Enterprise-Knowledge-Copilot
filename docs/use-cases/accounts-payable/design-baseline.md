# Accounts Payable v1 Design Baseline

**Baseline ID:** `accounts-payable-design.v1.1`
**Status:** `FROZEN — INTERACTIVE CLARIFICATION AMENDMENT ACCEPTED`
**Freeze date:** 2026-09-01
**Implementation status:** `STAGE 12 NOT READY; CLARIFICATION RELEASE GATES REQUIRED`

### v1.1 amendment

ADR-019 authorizes `WAITING_CLARIFICATION`, durable clarification records, current-scope questions,
partial/multi-round answers, and asynchronous resume into `UNDERSTANDING`. This is the only AP v1.1
business-lifecycle change. All finance formulas, exception definitions, limits, data fixtures,
policy versions, approval bindings, Evidence, report, and verification semantics remain v1.0.

## 1. Authority and scope

This baseline freezes the architecture and contracts for Use Case 2, Accounts Payable Invoice
Compliance & Exception Investigation v1. It authorizes the staged implementation described in the
[implementation plan](implementation-plan.md); it does not claim that any AP production behavior,
schema, seed data, policy corpus, evaluation dataset or UI is implemented.

Repository-wide `AGENTS.md` instructions and accepted ADRs remain higher-level authority. The
Supplier Quality Analysis v1.2 baseline under `docs/design/` is separate. If a UC2
implementation choice conflicts with that frozen baseline or a shared platform contract, work
must stop until the conflict is resolved through an explicit design change.

The following documents jointly form the normative UC2 v1 design set:

| Document | Frozen responsibility |
|---|---|
| [README](README.md) | business outcome, capability boundary, terminology and non-goals |
| [Architecture](architecture.md) | reuse model, manifest routing, lifecycle and hard limits |
| [Domain model](domain-model.md) | entities, roles, relationships and exception taxonomy |
| [Task contract](task-contract.md) | trust boundary, validation, planner and repair contract |
| [Tool contracts](tool-contracts.md) | tool profiles, templates, schemas, failures and idempotency |
| [Database design](database-design.md) | tenant-scoped fact model, precision, migration and seed plan |
| [Analytics design](analytics-design.md) | deterministic formulas, exclusions, aggregation and materiality |
| [Evidence and verification](evidence-and-verification.md) | lineage, claim mapping and verification gates |
| [Security and governance](security-and-governance.md) | permissions, approvals, classification and forbidden actions |
| [Evaluation plan](evaluation-plan.md) | datasets, metrics, regression and release gates |
| [Implementation plan](implementation-plan.md) | sequential delivery and architecture acceptance gates |

[Platform reuse audit](platform-reuse-audit.md) is the frozen current-state evidence used by this
design, not a substitute for a normative contract. [Design review](design-review.md) is the Stage 0
acceptance record.

## 2. Accepted architecture decisions

These accepted decisions are part of the baseline:

- [ADR-009](../../adr/ADR-009-multi-domain-capability-manifests.md): select versioned domain
  profiles through a deny-by-default capability manifest while retaining one shared runtime.
- [ADR-010](../../adr/ADR-010-version-bound-policy-rules.md): bind deterministic AP rules to exact
  controlled document versions, effective dates and checksums.
- [ADR-011](../../adr/ADR-011-accounts-payable-business-data-model.md): add a narrow, tenant-scoped
  AP business schema, reuse suppliers and preserve the business/platform database boundary.

## 3. Frozen identifiers and versions

| Boundary | Frozen identifier |
|---|---|
| design baseline | `accounts-payable-design.v1.0` |
| task type and policy purpose | `accounts_payable_analysis.v1` |
| task contract | `task-contract.v2` |
| database schema | `accounts_payable.v1` |
| policy profile | `accounts_payable_policy.v1` |
| rule set | `ap_rules.2026.1` |
| analytics engine | `accounts_payable_analytics.v1` |
| report model | `accounts_payable_report_model.v1` |
| report template | `accounts_payable_report.v1` |
| artifacts | `ACCOUNTS_PAYABLE_REPORT_JSON`, `ACCOUNTS_PAYABLE_REPORT_PDF` |
| evaluation dataset | `accounts_payable` version `1.0.0`, synthetic seed `42` |

No new capability name is introduced. UC2 uses only `knowledge_search`, `database_query`,
`analysis_engine` and `report_generator`, resolved by tool version and contract profile. The five
database templates and seven analytics operations are exactly those listed in the tool and
analytics contracts.

## 4. Frozen business rules and limits

The six supported exceptions are `EXACT_DUPLICATE_INVOICE`, `PO_AMOUNT_VARIANCE`,
`MISSING_REQUIRED_PO`, `LATE_PAYMENT`, `MATERIAL_EARLY_PAYMENT` and `OVERPAYMENT`. Formulas,
denominators, eligibility, exclusions and materiality semantics are authoritative only as written
in [analytics design](analytics-design.md). Canonical monetary fields are `gross_amount`,
`approved_amount` and `payment_amount`; storage precision is `NUMERIC(20,4)`.

The task is read-only, performs no currency conversion and never aggregates different currencies.
It is limited to 366 inclusive calendar days, 100 suppliers, 10 legal entities, 50 business units,
50,000 source invoice rows, 5,000 exception records, 250 Evidence items, 25 MiB JSON, 15 MiB PDF
and the time/retry ceilings in [architecture](architecture.md). Approval cannot relax a hard limit,
broaden scope or authorize a forbidden operation.

The complete non-goal list in the [README](README.md) is frozen. In particular, payment execution,
business approval, writes, bank data, arbitrary SQL/Python, partial or multiple payments, credit
notes, line matching, fuzzy inference, ERP integration and cross-domain risk scoring remain out of
scope.

## 5. Compatibility and failure posture

- Existing Supplier Quality contracts, plans, checkpoints, states and execution semantics remain
  unchanged. Historical data without profile fields may map only through an exact known Supplier
  Quality schema fingerprint.
- UC2 adds no workflow table, Task state or finance-specific API route.
- Missing required dates or a non-deterministically resolvable legal entity follows the bounded
  interactive clarification path. Relative dates remain missing; unauthorized or malformed scope
  fails and is never converted into a clarification-based authorization path.
- Unavailable policy bindings, unauthorized scope, mixed-currency comparisons, unsupported
  settlement shapes, truncation or verification failure fail closed using the typed behavior in
  the normative documents.
- MCP is neither required by nor an alternate execution path for UC2.

## 6. Change control

Implementation may fill in code behind this baseline but may not silently rename identifiers,
change formulas or denominators, loosen limits, expand scope, add data fields, alter approval or
tenant behavior, introduce another state/API/capability beyond this v1.1 amendment, or weaken
verification and evaluation.

A material change requires all of the following before production code changes:

1. identify every affected normative document and ADR;
2. document compatibility, migration, security, evidence and evaluation impact;
3. resolve all cross-document conflicts;
4. assign a new baseline or contract/profile/rule version as appropriate;
5. record explicit architecture, security and business-data-owner approval;
6. rerun the Stage 0 consistency and documentation gates.

Implementation discoveries that do not change behavior may clarify wording, but the change must
remain traceable in review. Performance validation may tighten a limit; it may not silently loosen
one.

## 7. Stage boundary

Stage 0 is complete because the normative design set is frozen, ADR-009/010/011 are Accepted, the
nine architecture gates have review evidence, and no core design decision remains unresolved.
Stage 1 is not started. Production code, migrations, datasets and generated business artifacts
remain outside this baseline's completion claim.
