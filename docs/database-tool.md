# Database Tool

## Purpose

The Database Tool is the governed structured-data capability for Supplier Quality Analysis v1.0.
It reads a deterministic SQLite demo database through the existing Tool Registry and Tool
Executor. It is not a general SQL console, Text-to-SQL component, or direct database port for
workflow and agent code.

The only permitted runtime path is:

```text
Workflow -> ToolExecutor -> DatabaseTool -> DatabaseConnection -> SQLite
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

The deterministic seed includes normal, high-risk, and boundary cases: a low-deviation supplier
with a completed action, a high-defect supplier with an overdue action, a supplier with no
deviation or corrective action, a zero-reject inspection, and same-day inspection/deviation
records. It also includes the `S-100`/`S-200` Q1 2026 walkthrough totals.

Initialize or reset only these four demo tables with:

```bash
python scripts/seed_demo_database.py
```

The script reads `DATABASE_URL`, supports SQLite only, and is deterministic and repeatable. The
default `.env.example` location is `data/database/enterprise_demo.db`; generated database files
are ignored by Git.

## Frozen query contract

The v1.0 design does not accept a caller-provided `sql` field. Input contains one approved
`query_template_id`, structured parameters, `schema_version=quality.v1`, a snapshot timestamp, and
a bounded row limit. The two approved templates are:

- `supplier_quality_summary_v1`: supplier/month inspected and defect quantities.
- `supplier_quality_trend_v1`: supplier/inspection-date inspected and defect quantities.

Trusted template builders create parameterized SQLAlchemy `Select` objects. `SQLValidator` walks
that expression tree and rejects textual SQL fragments, non-SELECT constructs, unregistered
tables or fields, wildcards, unapproved functions, and statements without a limit. Raw SQL is
always rejected, including a raw string that appears to contain only `SELECT`.

The `SchemaRegistry` is deny-by-default and contains only the four demo tables, their declared
columns, the two templates, and the SQL functions required by those templates.

## Read-only and scope enforcement

- The Tool Definition risk is `MEDIUM`; execution remains subject to policy and approval.
- The input tenant must match the trusted `ToolCall.tenant_id`.
- Dates and supplier identifiers become bind parameters, never SQL structure.
- A query uses one statement, a maximum requested output of 10,000 rows, and an 8-second SQLite
  statement deadline within the Tool Executor's 10-second attempt timeout.
- SQLite connections enable foreign keys and `PRAGMA query_only`; sessions and result connections
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

## PostgreSQL migration

The ORM model, session factory, parameterized query, normalization, and Tool contract boundaries
are database-driver independent. Migration still requires an explicit adapter change and tests:

1. add PostgreSQL configuration and credentials through the approved secret mechanism;
2. replace SQLite `strftime` in the summary template with a dialect-neutral or PostgreSQL
   implementation while preserving output;
3. enforce transaction-level read-only mode and server-side statement timeout;
4. add versioned migrations instead of demo `drop_all/create_all`;
5. run contract, integration, tenant-isolation, timeout, reconnect, and evidence-lineage tests.

No migration may broaden the two approved query templates or bypass Registry, policy, approval,
Executor, Evidence, audit, or verification.
