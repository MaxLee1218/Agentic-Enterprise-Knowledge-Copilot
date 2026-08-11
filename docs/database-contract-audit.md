# Supplier Quality Business Database Contract Audit

Audit date: 2026-08-11  
Authority: Supplier Quality Analysis v1.1 frozen design plus the current executable code

## Scope and database boundary

This audit was completed before changing the demo seed implementation. The repository has two
separate database boundaries:

- `PERSISTENCE_DATABASE_URL` is Copilot-owned storage for Task, State, Plan, ToolResult,
  Evidence, Approval, Audit, Checkpoint, and Artifact metadata. This task does not change its
  schema or semantics.
- `DATABASE_URL` is the enterprise business database used only by the governed, read-only
  `DatabaseTool`. Demo/test initialization may write through `seed_demo_database`; the runtime
  tool remains template-only and `SELECT`-only.

## Current physical schema

`src/copilot/tools/database/models.py` is the single SQLAlchemy metadata authority for all four
demo tables. There is no second handwritten DDL schema.

### `suppliers`

| Column | SQLAlchemy type | Nullable | Key / constraint |
|---|---|---:|---|
| `id` | `Integer` | no | primary key |
| `tenant_id` | `String(64)` | no | part of unique tenant/code scope |
| `supplier_code` | `String(64)` | no | unique with `tenant_id` |
| `name` | `String(200)` | no | |
| `country` | `String(2)` | no | |
| `category` | `String(100)` | no | |
| `risk_level` | `String(16)` | no | check: `LOW`, `MEDIUM`, or `HIGH` |
| `created_at` | timezone-aware `DateTime` | no | defaults to UTC clock |

Indexes/constraints: `uq_supplier_tenant_code`; `ix_suppliers_tenant_risk`.

### `incoming_inspections`

| Column | SQLAlchemy type | Nullable | Key / constraint |
|---|---|---:|---|
| `id` | `Integer` | no | primary key |
| `supplier_id` | `Integer` | no | FK to `suppliers.id`, cascade on delete |
| `inspection_date` | `Date` | no | inclusive template time filter |
| `total_quantity` | `Integer` | no | check: non-negative |
| `accepted_quantity` | `Integer` | no | check: non-negative |
| `rejected_quantity` | `Integer` | no | check: non-negative |
| `created_at` | timezone-aware `DateTime` | no | defaults to UTC clock |

The balance check requires
`accepted_quantity + rejected_quantity = total_quantity`. The current business vocabulary maps
`total_quantity -> inspected_count` and `rejected_quantity -> defect_count`; those output names
are projections, not physical columns. The supplier/date index is
`ix_incoming_inspections_supplier_date`.

### `supplier_deviations`

| Column | SQLAlchemy type | Nullable | Key / constraint |
|---|---|---:|---|
| `id` | `Integer` | no | primary key |
| `supplier_id` | `Integer` | no | FK to `suppliers.id`, cascade on delete |
| `deviation_date` | `Date` | no | |
| `deviation_type` | `String(100)` | no | |
| `severity` | `String(16)` | no | check: `MINOR`, `MAJOR`, or `CRITICAL` |
| `defect_quantity` | `Integer` | no | check: non-negative |
| `description` | `Text` | no | marked sensitive by SchemaRegistry |
| `created_at` | timezone-aware `DateTime` | no | defaults to UTC clock |

This is an existing reserved model. Neither approved query template, Analytics, nor the current
Agent workflow reads it.

### `corrective_actions`

| Column | SQLAlchemy type | Nullable | Key / constraint |
|---|---|---:|---|
| `id` | `Integer` | no | primary key |
| `supplier_id` | `Integer` | no | FK to `suppliers.id`, cascade on delete |
| `opened_date` | `Date` | no | |
| `due_date` | `Date` | no | |
| `closed_date` | `Date` | yes | |
| `status` | `String(16)` | no | check: `OPEN`, `IN_PROGRESS`, or `CLOSED` |
| `description` | `Text` | no | marked sensitive by SchemaRegistry |
| `created_at` | timezone-aware `DateTime` | no | defaults to UTC clock |

This is also an existing reserved model and is not part of the active query/analytics chain.

## SchemaRegistry and policy surface

`SchemaRegistry(quality.v1)` registers all columns from the four ORM tables, the two frozen query
template IDs, and only the SQL functions `month_period` and `sum`. It marks the two description
columns as sensitive. Registration alone does not make a table queryable by the Agent:
`DataAccessPolicy` grants the Supplier Quality roles only `suppliers` and
`incoming_inspections`, and the trusted template library builds statements over only those two
tables.

The effective runtime surface is therefore narrower than the physical demo schema:

| Layer | Effective tables |
|---|---|
| SQLAlchemy metadata | all four tables |
| SchemaRegistry | all four tables, deny unknown columns |
| DataAccessPolicy | `suppliers`, `incoming_inspections` only |
| Approved query templates | `suppliers`, `incoming_inspections` only |
| Analytics | normalized template rows only; no ORM/table access |

## Query template contracts

Both templates require structured parameters `tenant_id`, inclusive `start_date`/`end_date`, and
`supplier_ids`; the tool input additionally fixes `schema_version=quality.v1`, a timezone-aware
`snapshot_at`, and `row_limit` from 1 through 10,000. SQL structure never comes from the caller.

| Template | Physical fields read | Grouping / output |
|---|---|---|
| `supplier_quality_summary_v1` | supplier `id`, `tenant_id`, `supplier_code`; inspection `supplier_id`, `inspection_date`, `total_quantity`, `rejected_quantity` | supplier + portable `YYYY-MM`; outputs `supplier_id`, `period`, `inspected_count`, `defect_count` |
| `supplier_quality_trend_v1` | same fields | supplier + exact inspection date; outputs the same four normalized fields, with an ISO date period |

Both statements use a parameterized tenant predicate, inclusive date predicates, an optional
expanding supplier-code predicate, stable ordering, and a bound execution limit. The tool fetches
one extra row only to determine `truncated`.

## Tenant, supplier, and time boundaries

- Tenant ownership is defined on `suppliers.tenant_id`. An inspection inherits tenant ownership
  only through its required supplier FK; no duplicate `tenant_id` field exists on inspections.
- The trusted `ToolCall.tenant_id` must equal the template parameter. The join predicate and
  `suppliers.tenant_id` filter enforce isolation in SQL.
- Supplier codes come from the validated TaskContract/step input and are bound through an
  expanding parameter. An empty list means the contract-authorized supplier scope for that
  tenant; it never removes the tenant predicate.
- The physical time fact is `incoming_inspections.inspection_date`. Template filtering is a
  closed interval. Summary periods are calendar months; trend periods are inspection dates.
- Repository examples, the frozen walkthrough, smoke tests, and evaluation cases consistently
  use calendar year 2026. The enterprise demo dataset should therefore cover all 12 months of
  2026 and retain the walkthrough-compatible Q1 totals for `TENANT-A` suppliers `S-100` and
  `S-200`.

## Analytics and Evidence dependencies

`AnalyticsRequest.dataset` accepts only these row fields:

```text
supplier_id: string
period: string
inspected_count: non-negative integer
defect_count: non-negative integer, not greater than inspected_count
```

Analytics supports only `defect_count`, `inspected_count`, `defect_rate`, and
`period_over_period_trend`. Defect rate is the exact aggregate numerator divided by aggregate
inspected quantity, normalized to four decimal places. Trend sorts the period strings and computes
current rate minus previous rate.

Database Evidence depends on template ID, query fingerprint, schema version, snapshot, referenced
table/column names, safe parameter hashes, row count, truncation, aggregate counts, and a canonical
checksum of the normalized rows. Analytics requires that Evidence ID and checksum before it will
calculate.

## Audit answers and implementation consequence

- The active Agent needs supplier tenant/code, inspection date, total quantity, and rejected
  quantity. Supplier name/country/category/risk are realistic master data but are not projected by
  current templates.
- The active templates do not use `accepted_quantity` directly, but the physical balance
  constraint requires it to equal total minus rejected quantity.
- Deviation and corrective-action models already exist, but they are not consumed by the current
  business chain. They remain compatibility fixtures only; this task must not expand CAPA or
  deviation behavior.
- No new business table, column, runtime contract, query template, metric, or write capability is
  required. The correct change is a deterministic profile-driven seed over the existing models,
  with the runtime `DatabaseTool` left read-only.
