# Stage 5 Deterministic AP Analytics Report

**Status:** `COMPLETE — DETERMINISTIC ANALYTICS FOUNDATION ONLY`

**Contract profile:** `accounts_payable_analytics.v1`

**Engine version:** `accounts_payable_analytics.v1`

**Tool version:** `2.0.0-deterministic`

**Operation version:** `1.0.0`

**Date:** 2026-08-23

## Delivered boundary

Stage 5 implements the seven frozen Accounts Payable operations behind the existing stable
`analysis_engine` capability. The implementation accepts only strict typed operation unions and
checksum-bound Stage 3 Document Evidence plus Stage 4 Database Evidence. It performs no LLM
calculation, external access, currency conversion, arbitrary code, business mutation, report
composition or final verification.

The adapter is explicitly composable for governed tests and later workflow integration. It is not
registered in the current Supplier Quality runtime, and the Accounts Payable domain manifest
remains `execution_enabled=false`. Stages 6–10 remain required before UC2 task execution can be
enabled or production readiness can be claimed.

## Registered analytical surface

| Operation | Frozen behavior implemented |
|---|---|
| `ap.exact_duplicate_invoice_detection.v1` | exact six-field key, groups of at least two, lexicographically smallest canonical member and noncanonical exposure |
| `ap.invoice_po_variance_detection.v1` | signed/absolute amount and eight-place ratio; strict rate-or-amount exceedance; zero, unsupported matching and currency exclusions |
| `ap.missing_po_detection.v1` | currency rule resolution, threshold equality detection and exact approved-exception reference handling |
| `ap.payment_term_compliance_detection.v1` | authoritative due date, calendar-day late/early calculations, early equality detection and two-place late average |
| `ap.overpayment_detection.v1` | one settled payment, exact currency, four-place Decimal difference and strict tolerance exceedance |
| `ap.exception_summary.v1` | unique-invoice counts/rate, once-per-invoice currency totals, type/severity counts and unioned exclusions |
| `ap.supplier_exception_rate.v1` | unique per-supplier numerator/denominator, eight-place ratio, currency-partitioned amounts and stable review ordering |

No fuzzy duplicate, multiple/partial-payment calculation, credit-note handling, three-way/line
matching, foreign-exchange conversion or supplier-risk score is present.

## Determinism and rule application

Amounts use `Decimal` with stored four-place precision and `ROUND_HALF_EVEN`. Ratios use eight
places, average late days use two places, and dates use calendar-day arithmetic. Stable operation
ordering and canonical JSON SHA-256 checksums make identical inputs reproducible.
Exact-duplicate Calculation Evidence records the frozen Stage 2 source normalization identifier
`invoice_number_normalization.v1`; other operations record the AP analytics normalization profile.

Every monetary detection applies the frozen organization materiality by currency after detection.
A requested threshold may only tighten the organization threshold; the effective value must equal
the exact minimum and retain every requested currency. Timing findings are always `FINDING` once
detected. Materiality changes presentation severity only and never removes a record or changes a
count.

Every applied controlled rule is resolved from `ap_rules.2026.1` and rechecked against the exact
invoice date, invoice type and legal entity. Missing currency coverage, ineffective rules,
rule/document drift and threshold relaxation fail closed.

## Consistency and lineage gates

Before calculation the adapter proves:

- exact purpose, current task and tenant ownership for every Evidence item;
- the complete rule-manifest checksum and exact rule-to-document/chunk/page/checksum bindings;
- exact AP database template/schema/version, read-only `SELECT` metadata, row count, dataset
  checksum, scope summary and snapshot consistency;
- complete one-to-one common and dedicated invoice cohorts with unique opaque keys;
- `gross_amount = net_amount + tax_amount` at stored precision;
- matching tenant, supplier, legal entity and business unit across invoice, PO and payment facts;
- valid payment cardinality and required parent facts;
- nontruncated source data and complete calculation batches.

Aggregation accepts only current-task detection Calculation Evidence. It reconstructs every batch,
recomputes its content and output checksums, checks its operation/formula/engine/rule/precision
metadata, verifies exact Document and Database parents, and rejects missing batches, multiple runs
of one operation or records outside the eligible common population.

## Calculation Evidence and limits

Each successful operation returns one or more `CALCULATION` Evidence drafts containing common
result metadata and typed batch items. Batches contain at most 1,000 items and share a deterministic
calculation-run identifier, operation metadata and output checksum. Exception records produced by
an aggregation retain the exact source Calculation Evidence batch ID.

The implementation enforces the frozen 50,000 source-row and 5,000 exception-record limits. A
truncated database result or more than 5,000 exceptions fails recoverably with `AP_SCOPE_TOO_LARGE`;
it never returns a partial summary. A truly empty source succeeds with explicit
`EMPTY_SOURCE_POPULATION` coverage warning. A nonempty source with no eligible coverage fails with
`AP_DATA_INCOMPLETE`.

## Verification matrix

| Gate | Coverage |
|---|---|
| strict contracts | seven-operation discriminated union, exact two-dataset detection inputs, aggregation-only Calculation Evidence IDs and generated JSON schemas |
| formula boundaries | equality and immediately-above checks for PO variance, PO-required amount, material early days, overpayment tolerance and materiality |
| eligibility/exclusions | null duplicate keys, zero PO, currency mismatch, invalid approval, unpaid/multiple payment, invalid dates and zero-coverage behavior |
| consistency | tenant ownership, complete cohort, duplicate keys, stored arithmetic and cross-record parent dimensions |
| policy | complete bindings, manifest checksum, currency availability, row-level applicability and tightening-only materiality |
| aggregation | unique invoice counting, once-per-invoice currency amounts, supplier ratios, zero denominators and stable ranking |
| batching | 1,000-item deterministic boundary, repeatable checksums, complete indices and reference-metadata tamper rejection |
| hard limits | truncated sources and more than 5,000 exceptions fail without silent omission |
| real boundary | seeded SQLite Stage 4 rows reproduce the frozen Q2 exception oracle across all five detections and exact summary KPIs |
| backward compatibility | the Supplier Quality analytics profile and its database/analytics integration path remain independently selectable and unchanged |

Local acceptance on 2026-08-23 reported `520` unit tests passed; the complete integration suite
reported `94 passed, 8 skipped` after its three loopback-socket cases ran with local binding
permission; contract, smoke and security suites reported `72 passed`; and the opt-in deployment E2E
test remained skipped. The offline Supplier Quality regression evaluation passed `30/30` cases.
The hermetic MCP interoperability and safety evaluations also remained green at `13/13` and
`12/12`. Documentation, Ruff format/lint and strict MyPy checks passed; MyPy checked `428` source
files. The skipped cases require explicitly configured live/external or isolated PostgreSQL/E2E
environments and do not replace the real SQLite AP database-to-analytics acceptance test.

## Acceptance boundary

Stage 5 is accepted only as the deterministic analytics foundation. It does not implement the AP
Evidence/verifier profile, JSON/PDF report model, workflow graph, API/UI activation, evaluation
release gate or operational rollout. Those remain sequential Stages 6–10, and
`ACCOUNTS_PAYABLE_MANIFEST.execution_enabled` remains false.
