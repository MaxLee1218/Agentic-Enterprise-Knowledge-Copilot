# Business Database and Migration Design

## 1. Boundary

AP operational facts live in the separate enterprise business database addressed by
`DATABASE_URL`. They do not enter the Copilot persistence database addressed by
`PERSISTENCE_DATABASE_URL`. The existing Alembic history governs Copilot-owned workflow tables;
UC2 implementation must add a separate, explicitly invoked business-schema migration path rather
than placing AP tables in workflow migrations or relying on `Base.metadata.create_all` in
production.

The existing `suppliers` table is reused. UC2 does not create `ap_suppliers`.

## 2. Numeric and time conventions

- Monetary columns use `NUMERIC(20,4)` and Python `Decimal`; `float` is forbidden.
- Four stored decimal places preserve source tax/price precision. Comparison and report display
  quantize using the controlled currency scale (normally 2, JPY 0, KWD 3) and
  `ROUND_HALF_EVEN`; the raw Decimal remains in Evidence.
- Rates use `NUMERIC(12,8)` when persisted in controlled fixtures; runtime ratios use Decimal.
- Currency is an uppercase three-letter code. No conversion or cross-currency sum occurs in v1.
- Business dates use `DATE`. Capture/audit timestamps use timezone-aware UTC.
- All business identifiers are strings bounded to 128 characters; surrogate `id` values are
  technical keys only.

## 3. Tenant key strategy

Every new table carries explicit `tenant_id`, even when a parent could imply it. Composite foreign
keys include `tenant_id`, so a child cannot reference a parent from another tenant. Every common
query begins with tenant and date/scope predicates. Business uniqueness is tenant-scoped and, when
necessary, legal-entity- or supplier-scoped.

The existing `suppliers` table receives a compatibility-preserving unique constraint on
`(tenant_id, id)` so AP tables can use composite FKs. Its existing
`UNIQUE(tenant_id, supplier_code)` remains authoritative. No existing row or Supplier Quality
query changes.

## 4. v1 tables

### `legal_entities`

| Column | Type | Rule |
|---|---|---|
| `id` | BIGINT | primary key, technical |
| `tenant_id` | VARCHAR(64) | required |
| `legal_entity_code` | VARCHAR(64) | required business identifier |
| `name` | VARCHAR(200) | required, CONFIDENTIAL |
| `base_currency` | CHAR(3) | required |
| `status` | VARCHAR(16) | `ACTIVE | INACTIVE` |
| `created_at` | TIMESTAMPTZ | required UTC |

Constraints: unique `(tenant_id, id)` and `(tenant_id, legal_entity_code)`. Index
`(tenant_id, status)`.

### `business_units`

| Column | Type | Rule |
|---|---|---|
| `id` | BIGINT | primary key |
| `tenant_id` | VARCHAR(64) | required |
| `legal_entity_id` | BIGINT | required composite FK to legal entity |
| `business_unit_code` | VARCHAR(64) | required business identifier |
| `name` | VARCHAR(200) | required, CONFIDENTIAL |
| `status` | VARCHAR(16) | `ACTIVE | INACTIVE` |
| `created_at` | TIMESTAMPTZ | required UTC |

Constraints: unique `(tenant_id, id)` and `(tenant_id, legal_entity_id,
business_unit_code)`; FK `(tenant_id, legal_entity_id)`; index `(tenant_id, legal_entity_id,
status)`.

### `purchase_orders`

| Column | Type | Rule |
|---|---|---|
| `id` | BIGINT | primary key |
| `tenant_id` | VARCHAR(64) | required |
| `source_system` / `source_record_id` | VARCHAR(64/128) | immutable ingestion identity |
| `po_number` | VARCHAR(128) | required business identifier, CONFIDENTIAL |
| `supplier_id` | BIGINT | required composite FK to existing supplier |
| `legal_entity_id` / `business_unit_id` | BIGINT | required scoped composite FKs |
| `order_date` | DATE | required |
| `approved_amount` | NUMERIC(20,4) | required, non-negative |
| `currency` | CHAR(3) | required |
| `matching_basis` | VARCHAR(24) | v1 eligible value `SINGLE_INVOICE`; `MULTI_INVOICE` is excluded |
| `status` | VARCHAR(16) | `APPROVED | CLOSED | CANCELLED` |
| `approved_at` | TIMESTAMPTZ | required for APPROVED/CLOSED |
| `created_at` | TIMESTAMPTZ | required UTC |

Constraints: unique `(tenant_id, id)`, `(tenant_id, source_system, source_record_id)`, and
`(tenant_id, legal_entity_id, po_number)`; composite FKs to supplier/entity/unit; checks for
amount, currency shape and enums. Indexes `(tenant_id, supplier_id, order_date)`,
`(tenant_id, legal_entity_id, business_unit_id, order_date)`, and
`(tenant_id, po_number)`.

### `invoices`

| Column | Type | Rule |
|---|---|---|
| `id` | BIGINT | primary key |
| `tenant_id` | VARCHAR(64) | required |
| `source_system` / `source_record_id` | VARCHAR(64/128) | immutable ingestion identity |
| `supplier_id` | BIGINT | required composite FK |
| `legal_entity_id` / `business_unit_id` | BIGINT | required scoped FKs |
| `invoice_number` | VARCHAR(128) | required, CONFIDENTIAL; deliberately not unique |
| `normalized_invoice_number` | VARCHAR(128) | required deterministic normalization output |
| `invoice_type` | VARCHAR(16) | v1 eligible `STANDARD`; other values excluded |
| `invoice_date` | DATE | required and task-cohort date |
| `posting_date` | DATE | required |
| `currency` | CHAR(3) | required |
| `net_amount` / `tax_amount` / `gross_amount` | NUMERIC(20,4) | required, non-negative; `net + tax = gross` at stored precision |
| `purchase_order_id` | BIGINT | nullable tenant-scoped FK |
| `payment_terms_days` | INTEGER | required 0..365 |
| `due_date` | DATE | required; must equal the source-approved due date |
| `no_po_exception_ref` | VARCHAR(128) | nullable controlled approval reference |
| `no_po_exception_approved` | BOOLEAN | required default false; true requires reference |
| `status` | VARCHAR(16) | `POSTED | PAID | VOID` |
| `created_at` | TIMESTAMPTZ | required UTC |

Uniqueness is only `(tenant_id, source_system, source_record_id)` and `(tenant_id, id)`. A unique
supplier/invoice-number constraint is forbidden because it would erase the exact duplicate
business condition. Composite FKs bind supplier/entity/unit/PO to the same tenant. Indexes:

```text
(tenant_id, invoice_date)
(tenant_id, supplier_id, invoice_date)
(tenant_id, legal_entity_id, business_unit_id, invoice_date)
(tenant_id, supplier_id, normalized_invoice_number, invoice_date, currency, gross_amount)
(tenant_id, purchase_order_id)
```

Application/query validation also proves an invoice and referenced PO share supplier, legal
entity, business unit and currency before variance calculation.

Normalization is Unicode NFKC, trim, uppercase, and removal of ASCII space, hyphen and slash.
Only this versioned algorithm (`invoice_number_normalization.v1`) populates the stored normalized
value. Raw and normalized values are checked for length; normalization cannot produce empty text.

### `payments`

| Column | Type | Rule |
|---|---|---|
| `id` | BIGINT | primary key |
| `tenant_id` | VARCHAR(64) | required |
| `source_system` / `source_record_id` | VARCHAR(64/128) | immutable ingestion identity |
| `invoice_id` | BIGINT | required tenant-scoped FK |
| `legal_entity_id` / `business_unit_id` | BIGINT | required scoped FKs |
| `payment_date` | DATE | required |
| `payment_amount` | NUMERIC(20,4) | required, positive |
| `currency` | CHAR(3) | required |
| `status` | VARCHAR(16) | `SETTLED | VOID | REVERSED` |
| `created_at` | TIMESTAMPTZ | required UTC |

Constraints: unique `(tenant_id, id)` and `(tenant_id, source_system, source_record_id)`;
composite FKs to invoice/entity/unit; checks for positive amount and enums. Indexes
`(tenant_id, invoice_id, status)`, `(tenant_id, payment_date)`, and
`(tenant_id, legal_entity_id, business_unit_id, payment_date)`.

Payment reference, bank account, IBAN and SWIFT are deliberately absent from the v1 analytical
schema. v1 eligibility requires one settled payment and no second non-void payment; database
constraints do not forbid real multi-payment data because those records must remain visible as
reason-coded exclusions.

## 5. Future entities explicitly not migrated in v1

| Entity | Reason deferred |
|---|---|
| `purchase_order_lines` | only required for line/quantity matching and multi-invoice allocation |
| `invoice_lines` | only required for tax/price/quantity matching |
| `goods_receipts` | only required for three-way matching |

Their future keys must follow the same explicit tenant/composite-FK strategy. They are not empty
v1 scaffolds and no v1 query may reference them.

## 6. Migration plan

Implementation creates a business-schema revision series separate from Copilot persistence:

1. preflight duplicates/orphans and verify existing supplier tenant keys;
2. add unique `(tenant_id, id)` to `suppliers`;
3. create legal entities and business units;
4. create purchase orders, invoices and payments with checks/FKs/indexes;
5. install/select-only grants for the runtime business DB role;
6. register `accounts_payable.v1` tables/columns/templates in code;
7. seed only through the reusable package API called by `scripts/seed_demo_database.py`;
8. run SQLite and PostgreSQL migration/rollback tests.

Rollback drops AP tables in reverse FK order and removes only the added supplier composite unique
constraint. Rollback is allowed only on an isolated environment after proving no AP data needs
retention. Production downgrade is a reviewed data-loss operation, never automatic.

## 7. Deterministic demo dataset

The seed uses a fixed named seed and explicit scenario labels in fixture metadata, not in business
rows. It preserves current Supplier Quality data and adds at least two tenants, three legal
entities, four business units, six suppliers and Q1–Q3 2026 records.

Required patterns include clean invoices; exact duplicate groups; same-number nonduplicates;
within/boundary/above PO tolerance; missing PO below and above required threshold; approved no-PO
exception; on-time, late, boundary-early and materially early payment; exact/boundary overpayment;
zero PO amount; currency mismatch; unpaid invoice; multiple-payment exclusion; multiple suppliers;
multiple tenants; and quarters outside the target cohort.

The profile records seed version, random seed, row counts, expected exception IDs/counts and a
dataset checksum. Re-running with `--reset --dataset accounts-payable-v1` is byte/logically
deterministic and never deletes Supplier Quality rows. Seed validation recomputes every expected
exception through independent queries and fails nonzero on drift.
