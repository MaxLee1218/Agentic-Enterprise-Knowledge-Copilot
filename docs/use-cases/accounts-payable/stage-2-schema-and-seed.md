# Stage 2 Business Schema and Deterministic Seed Report

**Status:** `COMPLETE — DATA FOUNDATION ONLY`

**Schema:** `accounts_payable.v1`

**Profile:** `ap-demo-dataset.v1`

**Seed:** `42`

**Date:** 2026-08-22

## Delivered boundary

Stage 2 adds the isolated AP fact model to `DATABASE_URL`. It does not add AP query templates,
analytics operations, Agent nodes, report composition or executable AP routing. The Stage 1
manifest continues to deny AP execution.

The Copilot persistence history remains unchanged. Business data uses the independent
`business_alembic.ini` / `business_migrations` history and its own
`business_schema_version` table:

| Revision | Purpose |
|---|---|
| `20260811_b001` | create or strictly adopt the existing Supplier Quality baseline |
| `20260822_b002` | add the supplier tenant key plus legal entity, business unit, PO, invoice and payment facts |

Downgrading `b002` drops AP objects in reverse dependency order and removes only the added
supplier `(tenant_id, id)` unique constraint. Supplier Quality tables and rows remain intact.
Downgrade is intended only for an isolated environment after data-retention review.

## Schema controls

| Control | Implemented evidence |
|---|---|
| tenant isolation | explicit `tenant_id` on every AP table and composite tenant foreign keys |
| entity/unit scope | composite entity and three-column business-unit foreign keys |
| money | `NUMERIC(20,4)` mapped to Python `Decimal`; amount balance and positivity checks |
| dates/time | business `DATE`; capture/approval timestamps are timezone-capable UTC values |
| duplicate retention | invoice number is deliberately non-unique; ingestion identity remains unique |
| normalization | `invoice_number_normalization.v1`: NFKC, trim, uppercase, remove ASCII space/hyphen/slash |
| minimization | payments contain no reference, bank account, IBAN, SWIFT or tax identifier |
| deferred facts | no PO lines, invoice lines or goods receipts are migrated |
| runtime access | Compose creates tables with the seed identity and grants the runtime identity SELECT only |

## Deterministic fixture profile

The reusable package API and `scripts/seed_demo_database.py --dataset accounts-payable-v1`
produce this reviewed profile:

| Property | Value |
|---|---|
| checksum | `e920b4b13403831b0c4e7150edea452736f5c278cb2ed272b98c25da66b02f91` |
| tenants / suppliers | 2 / 6 referenced existing suppliers |
| legal entities / business units | 3 / 4 |
| purchase orders / invoices / payments | 24 / 27 / 11 |
| date coverage | 2026-02-10 through 2026-08-10 |

Scenario metadata is kept in the seed profile, not business rows. It covers clean and exact
duplicate invoices; same-number nonduplicates; PO variance below, at and above tolerance;
missing-PO threshold and approved-exception cases; on-time, late and early-payment boundaries;
overpayment boundaries; zero-PO, currency, unpaid, multi-payment and multi-invoice exclusions;
multiple tenants/suppliers; and Q1/Q3 controls.

Before commit, validation independently queries persisted facts and recomputes the six exception
oracles and five exclusion categories. Re-running AP reset produces the same checksum and replaces
only AP rows. A row-for-row regression proves all existing Supplier Quality facts are preserved.

## Verification matrix

| Gate | Coverage |
|---|---|
| model/DDL checks | columns, unique constraints, checks, indexes and minimized payment schema |
| tenant attacks | cross-tenant supplier/PO reference rejected by the database |
| SQLite migration | upgrade, data-bearing downgrade to Quality baseline, re-upgrade |
| PostgreSQL migration | opt-in isolated upgrade/seed/downgrade/re-upgrade test |
| seed determinism | repeated checksum/profile equality and frozen-seed rejection |
| UC1 compatibility | 17 suppliers, 5,000 inspections and all Quality rows unchanged |
| deployment | Docker image includes both migration histories; Compose chains Quality then AP seeds |

PostgreSQL verification requires the existing isolated `TEST_BUSINESS_POSTGRES_URL`; it is
skipped when that resettable test database is not explicitly configured.
