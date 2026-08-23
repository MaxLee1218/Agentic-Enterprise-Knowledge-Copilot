# Stage 4 AP Database Query Templates Report

**Status:** `COMPLETE — DATABASE READ FOUNDATION ONLY`

**Contract profile:** `accounts_payable_database.v1`

**Schema version:** `accounts_payable.v1`

**Adapter version:** `2.0.0-sqlalchemy`

**Date:** 2026-08-23

## Delivered boundary

Stage 4 implements the five frozen Accounts Payable read models behind the existing
`database_query` capability. It adds no SQL supplied by a planner or user, no database write, no
exception classification, no calculation, and no report generation. The Accounts Payable domain
manifest remains `execution_enabled=false`; this stage can be composed explicitly for governed
adapter tests and later workflow integration without enabling the complete UC2 lifecycle.

The Supplier Quality `quality.v1` adapter remains the default. Its two template schemas,
normalization mode, version and fingerprint canonicalization are unchanged.

## Registered read surface

| Template | Frozen row purpose |
|---|---|
| `ap_invoice_population_v1` | scoped population, dates, exact net/tax/gross values, PO/payment cardinality and eligibility reason |
| `ap_duplicate_invoice_candidates_v1` | tenant-bound exact duplicate key inputs using normalized invoice number and opaque record key |
| `ap_invoice_po_variance_v1` | invoice/PO amounts, currencies, matching basis, governed no-PO exception and comparable parent dimensions |
| `ap_payment_terms_v1` | due-date, terms, one-settled-payment shape and comparable payment parent dimensions |
| `ap_payment_amount_v1` | exact invoice/payment amounts, currencies, settlement shape and comparable payment parent dimensions |

The schema registry exposes only `suppliers`, `legal_entities`, `business_units`,
`purchase_orders`, `invoices`, and `payments`, with a per-template physical-column footprint.
Raw invoice number, raw PO number, source record ID, payment reference, internal account, bank,
IBAN, SWIFT, tax ID, contact data and credentials are not registered fields. Invoice and PO keys
returned to the adapter are opaque technical record identifiers rather than business document
numbers.

## Scope and deterministic output

Every template starts from `Invoice` and applies bound predicates for:

- authenticated `tenant_id`;
- inclusive `start_date` and `end_date`, bounded to 366 days;
- supplier codes when supplied;
- one to ten legal-entity codes;
- business-unit codes when supplied;
- uppercase three-letter currency scope when supplied.

The snapshot timestamp must cover the full date range. The adapter reads at most 50,001 source
rows to return 50,000 plus a truncation sentinel. Scope collections are unique and bounded. List
order is canonicalized only for the AP query fingerprint because SQL `IN` membership is
order-independent.

Dates serialize as ISO values. AP money serializes as exact fixed-point Decimal strings and is
never converted to binary float. Rows are ordered by opaque invoice key, so repeated reads over
the same immutable snapshot produce identical rows, query fingerprints and dataset checksums.

## Authorization and query safety

The AP access profile requires:

- purpose `accounts_payable_analysis.v1`;
- a recognized finance role;
- the explicit `finance:ap.detail` scope;
- the exact template table and field footprint.

The existing Executor authorization boundary remains responsible for caller/task/approval
binding before adapter invocation. Inside the adapter, tenant mismatch, missing detail scope,
unknown role/purpose, unknown template/table/field, excessive scope and malformed dates fail
closed.

Only trusted SQLAlchemy `Select` objects are built. Bound parameters carry all values. The AST
validator rejects raw SQL, writes, textual fragments, wildcard or unbound columns, unregistered
tables/columns/functions and statements without a row limit. SQLite uses query-only connections;
PostgreSQL uses an explicit read-only transaction and server-side statement timeout.

## DATABASE Evidence

Each successful read produces one minimized `DATABASE` Evidence draft containing:

- template ID/version, schema version and schema snapshot timestamp;
- sorted physical tables and columns;
- query fingerprint, row count and dataset checksum;
- hashes and counts for tenant, supplier, legal-entity, business-unit, time and currency scopes;
- `SELECT` and `read_only=true` proof.

Evidence content retains only row count, empty/truncated flags and the dataset checksum. It does
not copy result rows, raw SQL, credentials, source identifiers or financial values.

## Verification matrix

| Gate | Coverage |
|---|---|
| exact templates | exact output-column and 23-row frozen Q2 fixture assertions for all five reads |
| reproducibility | deterministic order, exact four-place Decimal strings, canonical fingerprint and checksum |
| scope completeness | tenant/date/supplier/entity/unit/currency predicates plus empty and cross-tenant cases |
| bounds | 50,001 sentinel behavior, 50,000 hard maximum, list limits, 366-day range and snapshot coverage |
| access policy | finance role/purpose/detail scope plus forbidden table and field denials |
| SQL safety | raw SQL, writes, wildcard, unregistered table/column/function and missing limit rejection |
| lineage | exact AST-derived physical access profile and minimized DATABASE Evidence metadata |
| backward compatibility | complete Supplier Quality unit suite and database integration suite remain green |
| driver parity | one opt-in real PostgreSQL test compares all five outputs/checksums to SQLite; it is skipped unless the isolated PostgreSQL test URLs are configured |

Local acceptance run on 2026-08-23: `507` unit tests passed; the complete integration suite
reported `93 passed, 8 skipped` after the three loopback-socket tests were run with local binding
permission; contract, smoke and security suites reported `72 passed`. The eight integration skips
are opt-in external-service or isolated PostgreSQL cases; the five real PostgreSQL business tests
remain skipped until their isolated test URLs are configured.

Stage 5 may consume these rows only through verified DATABASE Evidence and exact dataset
checksums. Exception formulas, thresholds and classifications remain out of scope for Stage 4.
