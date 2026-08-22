# ADR-011: Tenant-scoped Accounts Payable Business Data Model

## Status

Accepted

## Date

2026-08-22

## Context

AP analysis needs supplier, legal entity, business unit, PO, invoice and payment facts. The
existing enterprise business database contains a reusable tenant-scoped Supplier table and
Supplier Quality facts, while Copilot persistence is a deliberately separate database. AP v1 does
not need line-level or goods-receipt facts because three-way matching is out of scope.

## Decision

- Reuse `suppliers`; do not create an AP supplier master.
- Add tenant-scoped legal entity, business unit, purchase order, invoice and payment tables in the
  enterprise business database. Add a compatibility-preserving `(tenant_id, id)` unique key to
  suppliers for composite foreign keys.
- Put explicit `tenant_id` on every AP table and include it in parent foreign keys, business
  uniqueness and primary query indexes. Source-system record identity is tenant-scoped; invoice
  number is deliberately not unique so true duplicates remain representable.
- Store money as `NUMERIC(20,4)`/Decimal with currency and controlled rounding; never float.
- Omit bank account, IBAN, SWIFT, payment reference and tax ID from the v1 analytical schema.
- Limit v1 PO variance to header-level `SINGLE_INVOICE` matching and payment rules to exactly one
  settled payment. Keep unsupported real records visible through reason-coded exclusions.
- Do not create purchase-order line, invoice-line or goods-receipt tables in v1. Add them only with
  a later three-way-match contract/migration.
- Govern these tables through a business-schema migration path separate from Copilot workflow
  Alembic migrations. Runtime access remains an allowlisted, read-only Database Tool role.

## Alternatives Considered

Creating `ap_suppliers` was rejected because it duplicates identity and can diverge. Inferring
tenant only from parents was rejected because it weakens direct isolation and indexing. Using
workflow persistence for invoices was rejected because it collapses trust boundaries. Adding all
future ERP entities was rejected as scope expansion. Enforcing one payment at the database level
was rejected because real multi-payment records must remain representable even though v1 excludes
them from specific calculations.

## Consequences

The schema is narrow, explainable and suitable for deterministic synthetic evaluation. Composite
keys and repeated tenant columns add migration/validation work but prevent cross-tenant joins.
Three-way matching, partial settlement and credit notes require later schema/contract versions.
Business schema deployment and backup remain operationally separate from Copilot state.

## Related Documents

- [Database design](../use-cases/accounts-payable/database-design.md)
- [Analytics design](../use-cases/accounts-payable/analytics-design.md)
- [ADR-006 deployment persistence boundary](ADR-006-deployment-persistence-boundary.md)
