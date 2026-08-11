# Database Tool

## Purpose

The Database Tool is the governed structured-data capability for Supplier Quality Analysis v1.1.
It reads a deterministic SQLite or PostgreSQL demo database through the existing Tool Registry and Tool
Executor. It is not a general SQL console, Text-to-SQL component, or direct database port for
workflow and agent code.

The only permitted runtime path is:

```text
Workflow -> ToolExecutor -> DatabaseTool -> DatabaseConnection -> SQLite/PostgreSQL
```

Routes, services, agent nodes, workflows, planners, analytics tools, and report tools do not import
or call the concrete database adapter.

## Demo schema

The SQLAlchemy models are internal persistence models and never cross the adapter boundary.

| Table | Purpose | Principal relationship |
|---|---|---|
| `suppliers` | Tenant-scoped supplier master data | Parent of all quality records |
| `supplier_deviations` | Dated quality deviations and defect quantities | Many-to-one supplier |
| `incoming_inspections` | Accepted and rejected incoming quantities | Many-to-one supplier |
| `corrective_actions` | Open, overdue, in-progress, and closed CAPA records | Many-to-one supplier |

The deterministic seed contains 17 fictional suppliers across two tenants and exactly 5,000
incoming inspections spanning all 12 months of 2026. Profile-driven records expose stable-good,
persistent-poor, deterioration, improvement, incident, high-volume, low-volume/high-rate, and
supplier-specific seasonal behavior. The four deviation and four corrective-action rows remain
legacy compatibility fixtures; approved templates and Analytics do not read them. The seed also
preserves the `S-100`/`S-200` Q1 2026 frozen walkthrough totals.

Initialize or reset only these four demo tables with:

```bash
python scripts/seed_demo_database.py --reset
```

The script reads `DATABASE_URL`, accepts an explicit SQLite/PostgreSQL URL, defaults to seed
`20260811`, and is deterministic and repeatable under `--reset`. It writes a database-derived
profile and checksum under `data/demo`. The default `.env.example` location is
`data/database/enterprise_demo.db`; generated database files are ignored by Git. See
[Synthetic Supplier Quality Enterprise Business Database](demo-business-database.md).

## Frozen query contract

The frozen design does not accept a caller-provided `sql` field. Input contains one approved
`query_template_id`, structured parameters, `schema_version=quality.v1`, a snapshot timestamp, and
a bounded row limit. The two approved templates are:

- `supplier_quality_summary_v1`: supplier/month inspected and defect quantities.
- `supplier_quality_trend_v1`: supplier/inspection-date inspected and defect quantities.

Trusted template builders create parameterized SQLAlchemy `Select` objects. `SQLValidator` walks
that expression tree and rejects textual SQL fragments, non-SELECT constructs, unregistered
tables or fields, wildcards, unapproved functions, and statements without a limit. Raw SQL is
always rejected, including a raw string that appears to contain only `SELECT`.

The `SchemaRegistry` is deny-by-default and contains only the four existing demo tables, their
declared columns, the two templates, and the SQL functions required by those templates. The
effective runtime surface is narrower: `DataAccessPolicy` and both templates allow only
`suppliers` and `incoming_inspections`.

## Read-only and scope enforcement

- The Tool Definition risk is `MEDIUM`; execution remains subject to policy and approval.
- The input tenant must match the trusted `ToolCall.tenant_id`.
- Dates and supplier identifiers become bind parameters, never SQL structure.
- A query uses one statement, a maximum requested output of 10,000 rows, and an 8-second database
  statement deadline within the Tool Executor's 10-second attempt timeout.
- SQLite connections enable foreign keys and `PRAGMA query_only`; PostgreSQL uses
  `SET TRANSACTION READ ONLY` plus server-side statement timeout. Sessions and result connections
  are always closed.
- Missing schemas, connection failures, timeouts, and denials map to the frozen typed tool errors.
- Logs contain identifiers, template ID, query fingerprint, row count, truncation, and latency,
  but never complete SQL or database rows.

An empty result is a successful business fact: `rows=[]`, `row_count=0`, and
`empty_result=true`. It still creates DATABASE Evidence and does not trigger a retry.

## Output and Evidence

Successful output follows the frozen contract:

```text
columns, rows, row_count, empty_result, truncated,
query_fingerprint, snapshot_at
```

The Database Tool returns one `EvidenceDraft`. The Tool Executor and Evidence Ledger bind it to
the immutable `task_id`, `step_id`, `tool_call_id`, `evidence_id`, and timestamp. Its source
reference records the safe database name, template ID, query fingerprint, schema version,
snapshot, table names, row count, and hashed tenant/supplier scope. Content contains only minimal
aggregate facts and a checksum of the normalized dataset; it does not duplicate full rows or SQL.

## PostgreSQL support

The ORM model, session factory, seed transaction, parameterized queries, normalization, and Tool
contract are database-driver independent. The month projection compiles to SQLite `strftime` or
PostgreSQL `to_char`; PostgreSQL read execution sets the transaction read-only and applies a
server-side statement timeout. Development Compose provides a physically separate business
PostgreSQL service, write-capable one-shot seed identity, and SELECT-only runtime identity.

The seed utility is a development/test initializer, not a production migration system. Enterprise
deployments still require approved credentials, schema migration/ownership, least-privilege
grants, backup, retention, and isolated integration tests. No deployment may broaden the two
approved query templates or bypass Registry, policy, approval, Executor, Evidence, audit, or
verification.
