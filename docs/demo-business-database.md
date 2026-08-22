# Synthetic Enterprise Business Database

## Purpose and boundary

The Supplier Quality and Accounts Payable development/test datasets are synthetic enterprise
demo data. They contain no production company, person, credential, or customer data. Supplier
Quality exercises the current governed Agent path; AP Stage 2 supplies only a non-executable data
foundation.

It is not Copilot persistence:

- `PERSISTENCE_DATABASE_URL` stores Copilot-owned Task, State, Plan, Result, Evidence, Approval,
  Audit, Checkpoint, and Artifact metadata.
- `DATABASE_URL` points to this isolated business source. Normal Agent execution opens
  it through the governed read-only adapter only.

The explicit seed utility needs write privileges for initialization. Those privileges are not
registered as a tool and never enter application startup or the Agent runtime. Production
enterprise databases are supplied and governed by the enterprise; they are never automatically
seeded.

## Contract-derived schema

Models live in `src/copilot/tools/database/models.py`. Explicit deployment DDL is owned by the
separate `business_alembic.ini` / `business_migrations` history, not Copilot persistence and not
production `Base.metadata.create_all()`.

| Table | Dataset role | Active Agent use |
|---|---|---|
| `suppliers` | tenant-scoped fictional supplier master | joined and filtered by both approved templates |
| `incoming_inspections` | dated inspected/accepted/rejected quantities | source of `inspected_count` and `defect_count` |
| `supplier_deviations` | four legacy compatibility fixtures | not read by current templates or Analytics |
| `corrective_actions` | four legacy compatibility fixtures | not read by current templates or Analytics |
| `legal_entities` / `business_units` | tenant and authorization dimensions for AP | Stage 2 data foundation only |
| `purchase_orders` / `invoices` / `payments` | deterministic AP header facts | AP execution remains disabled |

The active business chain therefore depends only on `suppliers` and `incoming_inspections`.
Reserved deviation/CAPA models were not expanded. The detailed type, key, nullability,
constraint, Registry, policy, query, Analytics, and Evidence audit is in
[Database Contract Audit](database-contract-audit.md).

## Default dataset

| Property | Value |
|---|---|
| deterministic seed | `20260811` |
| dataset checksum | `7afe142367bd8a69d3051d90b5dca694545cf7b62da4a56c8a60e9e06bc07bd4` |
| suppliers | 17 total: 15 primary-demo + 2 isolation/walkthrough |
| tenants | `TENANT-DEMO`, `TENANT-A` |
| incoming inspections | exactly 5,000 |
| period | `2026-01-01` through `2026-12-31` |
| months | all 12 calendar months; every month populated |
| dates | naturally spread across weekdays; no holiday dependency |

Supplier names are fictional. Master fields use realistic categories and country codes already
present in the ORM. Each inspection has positive total quantity, non-negative rejected quantity,
`rejected <= total`, and `accepted + rejected = total`. Every inspection belongs to an existing
supplier.

## Seed and profile commands

After installing the repository in editable mode, seed the configured business database:

```bash
python scripts/seed_demo_database.py --reset
python scripts/seed_demo_database.py --dataset accounts-payable-v1 --reset
```

Useful explicit options are:

```bash
python scripts/seed_demo_database.py --reset --seed 20260811
python scripts/seed_demo_database.py --reset \
  --database-url sqlite:///data/database/enterprise_demo.db
python scripts/seed_demo_database.py --reset \
  --profile-output data/demo/supplier_quality_dataset_profile.json
```

Without `--reset`, the selected populated dataset is rejected rather than appended to. Quality
should be initialized before AP. Quality reset replaces Quality rows; AP reset replaces only AP
rows and never deletes Quality facts. Rows are flushed, counted, checked for integrity, validated
against the relevant frozen oracle, and only then committed. An exception rolls the data
transaction back, so a complete dataset is not replaced by a partial initialization.

The command prints safe counts, period, and checksum and writes
`data/demo/supplier_quality_dataset_profile.json`. The profile is computed from the database rows,
not directly from profile configuration. It records records per supplier/month, annual inspected
and defect quantities/rates, quarterly rates and quarter-over-quarter deltas, plus the test oracle.

The AP command writes `data/demo/accounts_payable_dataset_profile.json`. Its frozen seed is `42`,
schema/profile versions are `accounts_payable.v1` / `ap-demo-dataset.v1`, and its reviewed checksum
is `e920b4b13403831b0c4e7150edea452736f5c278cb2ed272b98c25da66b02f91`. The profile records 3
legal entities, 4 business units, 6 referenced suppliers, 24 POs, 27 invoices, 11 payments and
the independently revalidated exception/exclusion oracles.

## Business pattern catalogue

The oracle labels seed intent for tests and documentation only. It is not stored in a business
table or supplied to the Agent; the Agent must infer patterns from actual query rows.

| Supplier | Pattern | Evidence in default final aggregates |
|---|---|---|
| `SUP-001` | stable high quality | annual 759 / 108,451 = 0.6999%; quarterly range 0.6827%–0.7100% |
| `SUP-002` | persistently poor | annual 2,872 / 59,653 = 4.8145%; every quarter remains about 4.8% |
| `SUP-004` | second persistently poor supplier | every quarter remains about 4.1% |
| `SUP-005` | sudden deterioration | Q1 0.9372%, Q2 1.0083%, Q3 3.9601%, Q4 4.3542% |
| `SUP-006` | sustained improvement | Q1 4.1944% → Q2 2.7296% → Q3 1.5387% → Q4 0.9326% |
| `SUP-007` | major quality incident | Q3 rises to 3.3957% versus roughly 1% in Q1/Q2/Q4; August is the numeric spike |
| `SUP-008` | high volume, moderate rate | 199,413 inspected and 3,583 defects at 1.7968%; highest absolute defects |
| `SUP-009` | low volume, high rate | 10,575 inspected and 606 defects at 5.7305%; high rate but far fewer defects than `SUP-008` |
| `SUP-003/010/012/014/015` | additional stable high-quality suppliers | low, bounded rates with mild natural variation |
| `SUP-011/013` | supplier-specific seasonality | summer or year-end volume/rate pressure without a shared identical curve |

Monthly record volume is intentionally non-uniform: the default dataset ranges from 367 records
in February to 461 in July. Supplier record allocations range from 130 to 650 per year. These
differences make record volume, inspected quantity, defect count, and defect rate distinct
business concepts.

The `TENANT-A` walkthrough controls preserve the frozen design example:

| Supplier | 2026 Q1 inspected | 2026 Q1 defects | Rate |
|---|---:|---:|---:|
| `S-100` | 12,000 | 180 | 1.5% |
| `S-200` | 10,000 | 80 | 0.8% |

## Existing query and Analytics behavior

The acceptance surface remains exactly the frozen pair:

- `supplier_quality_summary_v1`: monthly supplier aggregates;
- `supplier_quality_trend_v1`: inspection-date supplier aggregates.

Both bind tenant, closed date range, supplier codes, and limit. `DatabaseTool` still rejects raw
SQL, writes, unregistered objects/fields/functions, tenant mismatch, invalid scopes, and output
beyond the bounded row limit. The dataset does not add a write template or expose seed access to
the Registry.

The current Analytics input remains `supplier_id`, `period`, `inspected_count`, and
`defect_count`; supported metrics remain counts, `defect_count / inspected_count`, and period rate
delta. No top-N, risk model, CAPA analysis, forecast, or causal attribution was added.

## SQLite and PostgreSQL

SQLite is the fast local/test path:

```bash
python scripts/seed_demo_database.py --reset \
  --database-url sqlite:///data/database/enterprise_demo.db
```

For an isolated development PostgreSQL database, initialize with a write-capable seed identity,
then configure the Agent with a different SELECT-only identity:

```bash
python scripts/seed_demo_database.py --reset \
  --database-url postgresql+psycopg://SEED_USER:SEED_PASSWORD@DB_HOST:5432/supplier_quality

export DATABASE_PROVIDER=sqlalchemy
export DATABASE_URL=postgresql+psycopg://READONLY_USER:READONLY_PASSWORD@DB_HOST:5432/supplier_quality
```

The development Compose topology provides a separate `business-postgres` service, ordered
`seed-business-database` and `seed-ap-business-database` jobs, and a `quality_readonly` role. It
does not reuse the Copilot persistence database. Local ports default to 5432 for persistence
PostgreSQL and 5433 for business
PostgreSQL. The committed passwords are local demo values only.

The opt-in PostgreSQL integration test requires an isolated resettable database:

```bash
TEST_BUSINESS_POSTGRES_URL=postgresql+psycopg://SEED_USER:SEED_PASSWORD@DB_HOST:5432/supplier_quality \
TEST_BUSINESS_POSTGRES_READONLY_URL=postgresql+psycopg://READONLY_USER:READONLY_PASSWORD@DB_HOST:5432/supplier_quality \
pytest tests/integration/test_postgres_business_database.py
```

Never point the seed URL or that test at a production business database.

## Limitations

- All data is deterministic synthetic demo data, not evidence about a real supplier.
- Only Supplier Quality has registered runtime query templates; Stage 2 AP facts are not
  executable until later controlled-query and analytics stages are complete.
- The physical schema has no incident-reason field; incident explanations remain seed
  documentation, while database facts are numeric only.
- The dataset supports current descriptive metrics, not root-cause proof or predictive quality.
- SQLite verifies local behavior quickly; production authorization, credentials, retention,
  backup, migrations, and source-system synchronization remain deployment responsibilities.
