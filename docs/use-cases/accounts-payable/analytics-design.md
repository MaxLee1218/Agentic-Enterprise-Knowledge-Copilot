# Deterministic Analytics Design

## 1. Principles

The LLM proposes which approved exception types satisfy the request. SQLAlchemy templates obtain
facts. `analysis_engine` validates lineage and performs every amount, date, grouping, threshold and
summary calculation. Report composition copies structured values and never recalculates them.

Every operation uses Decimal, stable sorting, explicit eligibility/exclusion counts, immutable
dataset checksums, `ap_rules.2026.1`, and calendar-day date arithmetic. Duplicate input rows are
not silently deduplicated except where an operation explicitly defines a business record key.

## 2. Common operation contract

Every v1 operation defines:

```text
operation_name / operation_version
input template IDs and DATABASE Evidence IDs
required columns
policy rule snapshot and Document Evidence bindings
eligibility predicate
formula
grouping and stable ordering
threshold and materiality semantics
precision/rounding
null and empty behavior
business-record dedup behavior
typed output records and metrics
CALCULATION lineage
reason-coded warnings/exclusions
```

Empty datasets are successful and return no exception records, zero eligible count and a coverage
warning. A non-empty dataset with zero eligible records fails the requested operation as
`AP_DATA_INCOMPLETE`; a report cannot imply compliance from unsupported data.

## 3. Detection operations

### 3.1 `ap.exact_duplicate_invoice_detection.v1`

| Item | Frozen rule |
|---|---|
| Input | `ap_duplicate_invoice_candidates_v1`; invoice opaque key, tenant, supplier, normalized number, invoice date, gross amount, currency, status |
| Eligibility | posted/paid STANDARD invoice, positive gross amount, non-empty normalized number |
| Key | `(tenant_id, supplier_id, normalized_invoice_number, gross_amount, currency, invoice_date)` |
| Detection | group size `>= 2`; exact equality after source normalization and Decimal quantization to stored 4 places |
| Grouping | exact key; group sorted by `invoice_record_key` |
| Canonical member | lexicographically smallest `invoice_record_key`; classification is not a deletion instruction |
| Output | one group plus member keys; each noncanonical member is an exception record |
| Metrics | `duplicate_group_count`, `duplicate_invoice_count` (noncanonical), `duplicate_exposure_amount_by_currency` |
| Exposure | sum `gross_amount` of noncanonical members once each |
| Empty/null | empty succeeds; null key field excluded with `DUPLICATE_KEY_INCOMPLETE` |
| Lineage | database checksum, normalization version, key formula and member keys |

Potential duplicate is not implemented. No edit distance, embeddings, phonetic similarity or LLM
judgment runs in v1. v1.1 may propose a normalized similarity algorithm only with labeled data,
explanations, false-positive/negative evaluation and a separate rule version.

### 3.2 `ap.invoice_po_variance_detection.v1`

```text
variance_amount = gross_amount - approved_amount
variance_rate   = variance_amount / approved_amount
absolute_variance_amount = abs(variance_amount)
absolute_variance_rate   = abs(variance_rate)
```

An exception occurs when either controlled tolerance is exceeded:

```text
absolute_variance_rate > allowed_variance_rate
OR
absolute_variance_amount > allowed_variance_amount[currency]
```

Equality is within tolerance, not an exception. Output retains signed and absolute values.

| Edge | Frozen behavior |
|---|---|
| `approved_amount = 0` | excluded `PO_AMOUNT_ZERO`; never divide |
| negative/credit invoice | OUT_OF_SCOPE; excluded `CREDIT_NOTE_UNSUPPORTED` |
| partial invoice | OUT_OF_SCOPE; excluded by matching basis |
| multiple invoices against PO | OUT_OF_SCOPE; `matching_basis != SINGLE_INVOICE` exclusion |
| multiple PO lines | header `approved_amount` only; line matching OUT_OF_SCOPE |
| currency mismatch | excluded `AP_CURRENCY_MISMATCH_EXCLUDED`; no conversion |
| supplier/entity/unit mismatch | consistency error, not a variance result |
| missing PO | handled only by missing-PO operation |

Results group by supplier/currency and sort by absolute variance descending then opaque invoice
key. Rates are quantized to 8 decimal places for Evidence; displays are percentages derived by the
renderer from the canonical ratio. Monetary values remain four-place Decimal.

### 3.3 `ap.missing_po_detection.v1`

For each STANDARD invoice without `purchase_order_id`, resolve the active
`PO_REQUIRED_AMOUNT` rule by tenant, legal entity, currency, invoice date and invoice type:

```text
po_required = gross_amount >= po_required_min_amount[currency]
valid_exception = no_po_exception_approved
                  AND nonblank no_po_exception_ref
                  AND exception reference is present in the governed query result
exception = po_required AND NOT valid_exception
```

Equality at the PO-required amount requires a PO. A below-threshold missing PO is a normal
nonexception result, retained in coverage. A purported approval without its reference is excluded
as `INVALID_NO_PO_EXCEPTION`; it is not treated as approved. Policy rule absence fails closed.

### 3.4 `ap.payment_term_compliance_detection.v1`

The source-approved `due_date` is authoritative. v1 does not recompute it from
`payment_terms_days`, holidays or invoice receipt date; it checks that `due_date >= invoice_date`
and reports inconsistency if `due_date != invoice_date + payment_terms_days` calendar days.

For exactly one eligible settled payment:

```text
delta_days = (payment_date - due_date).days
days_late  = max(delta_days, 0)
days_early = max(-delta_days, 0)

late_payment = days_late > 0
material_early_payment = days_early >= material_early_days
```

Equality at `material_early_days` is an exception. Days are calendar days; holidays, weekends and
time zones do not alter DATE arithmetic. On-time yields both values zero. Unpaid, void/reversed,
partial, or multiple-payment invoices are excluded with reason codes. Average days late is the
arithmetic mean over late-payment exceptions only, Decimal-quantized to two places; when there are
none it is null, not zero.

### 3.5 `ap.overpayment_detection.v1`

For the same one-settled-payment eligibility:

```text
overpayment_amount = payment_amount - gross_amount
exception = overpayment_amount > overpayment_tolerance[currency]
```

Equality at tolerance is not an exception. Negative values are not overpayment and are not
classified as partial-payment exceptions. Currency mismatch and multi/partial payment are
reason-coded exclusions. Credit notes are out of scope.

## 4. Aggregation and metric operations

### 4.1 `ap.exception_summary.v1`

Type: aggregation. Inputs are all requested detection CALCULATION Evidence items, the common
invoice population DATABASE Evidence and the same rule manifest checksum.

It merges by `invoice_record_key` and emits:

| KPI | Formula |
|---|---|
| `invoice_count` | count unique eligible population invoice keys |
| `invoice_amount_by_currency` | sum gross amount once per population invoice/currency |
| `exception_invoice_count` | count unique invoice keys in any exception record |
| `exception_rate` | exception invoice count / eligible invoice count; null if denominator zero |
| `exception_invoice_amount_by_currency` | sum gross amount once per exception invoice/currency |
| `exception_count_by_type` | count records per exception type |
| `finding_count` / `warning_count` | count after effective materiality labeling |
| `exclusion_count_by_reason` | unioned reason counts without claiming exceptions |

An invoice with duplicate, variance and late-payment exceptions counts once in
`exception_invoice_count` and its gross amount counts once in exception invoice amount, but it
appears in each type-specific count. No cross-currency total is emitted.

### 4.2 `ap.supplier_exception_rate.v1`

Type: metric/grouping. For each supplier:

```text
supplier_exception_rate = unique exception invoice count
                          / eligible invoice count
```

The output includes numerator, denominator, ratio, invoice amount by currency, exception amount
by currency and exclusion count. Zero denominator produces null and a warning. Ranking is stable
by ratio descending (null last), exception count descending, then supplier ID. It is an exception
review ordering, not a supplier-risk score.

## 5. Materiality classification

Detection always runs first. For each monetary exception, exposure is:

| Type | Exposure used for materiality |
|---|---|
| exact duplicate | noncanonical invoice `gross_amount` |
| PO variance | `absolute_variance_amount` |
| missing PO | invoice `gross_amount` |
| overpayment | positive `overpayment_amount` |

Payment timing uses the rule-defined day threshold and is `FINDING` once detected; a future
monetary carrying-cost rule is outside v1. For monetary types:

```text
effective_materiality[currency]
  = min(organization_policy_threshold, user_requested_threshold_if_any)

status = FINDING if exposure >= effective_materiality else WARNING
```

The organization threshold and user request are both recorded. The user cannot raise, remove or
change currency of the governed threshold. Materiality affects presentation severity only and
never removes a detected exception from counts or Evidence.

## 6. Calculation Evidence payload

Each operation emits:

```text
operation_name / operation_version / engine_version
input Evidence IDs and dataset checksums
formula and normalization version
policy rule IDs/versions and manifest checksum
eligibility count, exclusion count/reasons, empty_result
exception records and metrics
precision and rounding mode
output checksum
```

Large result sets are deterministically batched by sorted invoice key. Each batch is its own
CALCULATION Evidence item with the same operation/run identifier and batch index/count. Summary
Evidence references all batches. A report count must resolve to the summary and through it to
every batch and DATABASE Evidence item.

## 7. Cross-record consistency gate

Before any detection, deterministic validation checks:

- every child and parent share `tenant_id`;
- invoice supplier/legal entity/business unit match the referenced PO;
- payment invoice/entity/unit relationships match;
- invoice, PO and payment currency agree for operations that compare amounts;
- source record keys are unique inside a dataset;
- gross = net + tax at stored precision;
- due date and payment cardinality are valid for the requested operation.

Tenant or parent mismatch is a high-severity verification/execution failure. Currency, due-date,
settlement-shape and amount-quality problems use the explicit exclusion semantics above. The gate
prevents a bad join from becoming a financial conclusion.
