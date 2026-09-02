# Accounts Payable v1 Stage 0 Design Review

**Review ID:** `accounts-payable-stage-0-review.2026-08-22`  
**Baseline reviewed:** `accounts-payable-design.v1.1`
**Result:** `PASS — NO BLOCKING DESIGN QUESTION`  
**Product implementation status:** `STAGE 12 NOT READY; CLARIFICATION CHANGE UNDER GATES`

The 2026-09-01 amendment review accepts ADR-019 and the affected AP design updates. It confirms
that clarification changes only missing-input lifecycle behavior and does not change frozen
finance calculations, taxonomy, data, policy, report, or verification semantics.

## 1. Review purpose and method

This record closes Stage 0 by checking the UC2 documents against repository architecture,
Supplier Quality backward compatibility, deterministic finance rules, tenant isolation, approval
boundaries, evidence requirements and implementation readiness. It records a design review, not a
production-readiness or organizational deployment approval.

The review used the architecture, security and finance-data-owner lenses below. A `PASS` means the
design contains an implementable, testable answer; it does not mean the corresponding code or test
already exists.

## 2. Decision and consistency review

| Question | Resolution | Evidence |
|---|---|---|
| Can UC2 reuse the platform without task-type branching throughout the Graph? | Yes. A small deny-by-default domain manifest selects exact profiles; shared Registry, Executor, policy, Evidence, persistence and Graph remain authoritative. | Architecture; ADR-009 |
| Can old tasks resume after profiles become versioned? | Yes, but only an exact historical Supplier Quality schema fingerprint may supply missing legacy fields. There is no latest-profile fallback. | Architecture §3; task contract §3; ADR-009 |
| Is policy prose allowed to become executable instruction? | No. Controlled document text, rule manifests and deterministic analytics have separate authorities and exact version/checksum bindings. | Security §6; ADR-010 |
| Is the AP schema tenant safe? | Yes by design: every AP table carries `tenant_id`, relationships use composite tenant foreign keys, and queries repeat tenant and authorized-dimension predicates. | Database design §§3–4; ADR-011 |
| Are supplier records duplicated? | No. Existing suppliers are reused with additive tenant-key support; AP introduces only the narrow missing fact entities. | Database design §3; ADR-011 |
| Can formulas drift between report and evaluator? | No. Canonical fields, Decimal precision, formulas, denominators and per-currency aggregation are frozen once and referenced by Evidence/report/evaluation. | README terminology; analytics; evidence; evaluation |
| Is multiple/partial payment interpretation ambiguous? | No. Exactly one settled payment is eligible; unsupported settlement shapes use explicit exclusions and are never guessed. | Database design §4; analytics §§3.4–3.5 |
| Can materiality hide detected exceptions? | No. Detection is unchanged; a user may only tighten the effective threshold, and materiality affects severity labeling rather than exception totals. | Task contract §4; analytics §5 |
| How is missing required information handled? | Valid missing dates or unresolved multi-entity choice enters bounded `WAITING_CLARIFICATION`; authorized response resumes the same Task through `UNDERSTANDING`. Unauthorized/malformed scope still fails. | Architecture §5; ADR-019 |
| Can approval authorize bank data, writes or wider scope? | No. Those actions are forbidden and cannot be approved; approval binds one exact controlled read and may only tighten `top_k` or `row_limit`. | Security §§3, 8 |
| Does MCP create a bypass or dependency? | No. UC2 uses the governed internal path and has no MCP dependency. | Architecture §9; README non-goals |

No contradiction remains among the canonical names `gross_amount`, `approved_amount`,
`payment_amount`, `variance_amount`, `variance_rate`, `days_late`, `days_early`,
`exception_invoice_count` and `exception_invoice_amount_by_currency`.

## 3. Architecture acceptance gates

| Gate | Result | Review evidence |
|---|---|---|
| 1. Business scope | PASS | Six exceptions, bounded outcome and explicit non-goals are frozen in README/domain model. |
| 2. Contracts | PASS | Task, planner, tool, database, analytics, Evidence and report boundaries have stable names and versions. |
| 3. Platform reuse | PASS | One governed runtime is retained; domain behavior enters only through exact manifests/profiles. |
| 4. Determinism | PASS | Decimal/date formulas, eligibility, exclusions, thresholds, ordering and limits are explicit. |
| 5. Evidence | PASS | Document, database, calculation, claim and Artifact lineage plus failure gates are specified. |
| 6. Security | PASS | Read-only allowlists, tenant/dimension scope, classification, approvals and threat controls are specified. |
| 7. Backward compatibility | PASS | Frozen Supplier Quality behavior is unchanged; exact-fingerprint upcast and regression gates are required. |
| 8. Evaluation | PASS | Synthetic normal, exception, boundary, policy, security, recovery and performance cases are defined. |
| 9. Implementation readiness | PASS | ADR-009/010/011 are Accepted; stages, migrations, tests and acceptance boundaries are enumerated. |

## 4. Security and finance data-owner lens

| Review item | Result | Frozen decision |
|---|---|---|
| business purpose and data minimization | PASS | Only fields needed for the six read-only exception analyses may enter v1 queries/Evidence/reports. |
| tenant and dimension isolation | PASS | Tenant, legal-entity, business-unit, supplier, purpose and classification checks apply before execution and Artifact access. |
| sensitive finance fields | PASS | Bank account, IBAN, SWIFT, tax ID, payment reference and internal account number are absent or denied. |
| amount and identifier handling | PASS | Financial facts are CONFIDENTIAL; detail requires explicit scope, identifiers are masked, aggregate output excludes them, and logs avoid raw values. |
| approval separation of duties | PASS | `finance_approver` may approve a bound access action only; this is not invoice, PO or payment approval. |
| policy/rule ownership | PASS | Policy owner and rule owner publish version-bound inputs atomically; mismatch fails closed. |
| auditability | PASS | Audit records requester/context, scope hashes, versions, decisions, Evidence/Artifact IDs and verification without copying raw finance results. |
| destructive or external actions | PASS | No write, payment, ERP/bank instruction, external publication or unrestricted execution capability exists. |

Formal deployment-specific owner names, organization thresholds, approved policy documents and
currency configurations are intentionally not invented in Stage 0. They are controlled inputs for
later implementation and release gates. Their absence blocks execution safely; it does not reopen
the v1 architecture.

## 5. Backward-compatibility review

- `docs/design/` remains the sole frozen authority for Supplier Quality Analysis v1.2 and is not
  modified by this baseline.
- UC2 adds values and profiles; it does not reinterpret existing Supplier Quality values.
- Existing `/v1/tasks`, approval, cancellation, Evidence and Artifact resource models are reused.
- No UC2 production code, persistence migration, seed, policy corpus or evaluation baseline is
  part of Stage 0.
- Every later stage that touches shared code must run the Supplier Quality, AP and shared-platform
  regression suites defined in the evaluation plan.

## 6. Risks accepted for later validation

The following are non-blocking because the design already defines their safe boundary:

- synthetic data may not cover connector-specific ERP quality problems;
- 50,000-row, Evidence and Artifact ceilings require performance confirmation;
- deployment policy values and approved text must be supplied and version-bound by owners;
- additional currency scales require a new contract decision when `NUMERIC(20,4)` is insufficient.

Until validated, implementations must fail closed or retain the tighter frozen limit. These risks
do not authorize formula changes, new exception inference or scope broadening.

## 7. Stage 0 acceptance

| Criterion | Result |
|---|---|
| design documents are indexed and frozen | PASS |
| canonical names, versions, formulas, limits and non-goals are explicit | PASS |
| ADR-009, ADR-010 and ADR-011 are Accepted | PASS |
| architecture, security and finance-data-owner concerns have design resolutions | PASS |
| nine architecture gates pass | PASS |
| no placeholder marker or unresolved core decision remains | PASS |
| production implementation is not overstated | PASS |

**Decision:** freeze `accounts-payable-design.v1.0` and permit Stage 1 contract/manifest work.
Stage 1 remains a separate change with its own tests and acceptance criteria.
