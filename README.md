# Agentic Enterprise Knowledge Copilot

> A governed, evidence-backed platform that turns enterprise analysis requests into traceable,
> reviewable tasks and verified reports.

Agentic Enterprise Knowledge Copilot is not a general-purpose chatbot. It accepts a natural-language
business request, resolves its scope, creates and validates an execution plan, applies permissions
and approval rules, runs allowlisted tools, records evidence and audit lineage, and verifies the
result before publishing a JSON or PDF artifact.

The repository currently provides two read-only business workflows:

- **Supplier Quality Analysis** — analyzes quarterly supplier defects, inspection volume, defect
  rates, and period-over-period trends.
- **Accounts Payable Investigation** — identifies supported invoice compliance and exception cases
  within an authorized finance scope.

The project is at version `0.1.0`. Its local and synthetic vertical slices are implemented, but the
repository does **not** claim whole-system production readiness. In particular, production identity,
approved data and policy sources, operational ownership, capacity validation, and disaster recovery
remain deployment responsibilities.

For a Chinese-language introduction, see the
[project overview](docs/project-overview-zh.md).

## Table of contents

- [Why this project](#why-this-project)
- [Core capabilities](#core-capabilities)
- [Supported use cases](#supported-use-cases)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Using the API](#using-the-api)
- [Frontend](#frontend)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Development and testing](#development-and-testing)
- [Security and current limitations](#security-and-current-limitations)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Why this project

Enterprise AI needs stronger guarantees than a fluent answer. Important findings must be tied to
authorized data, reproducible calculations, explicit policy decisions, and durable evidence.

This project is designed around five principles:

1. **Policy before action** — permissions, tenant scope, data classification, risk, and approvals
   are checked before a tool runs.
2. **Tool-first execution** — business facts and calculations come from approved tools rather than
   being invented by a model.
3. **Traceability by default** — reports preserve document citations, query lineage, calculation
   evidence, audit events, and artifact checksums.
4. **Deterministic control** — validation, formulas, state transitions, retry limits, and completion
   gates are implemented in code.
5. **Safe failure** — missing evidence, invalid scope, failed verification, or unavailable
   dependencies produce a typed failure or suspended task instead of an unsupported result.

## Core capabilities

| Area | What is implemented |
|---|---|
| Task lifecycle | Durable task intake, planning, bounded execution, retry/replan, cancellation, clarification, approval, verification, and terminal results |
| Agent workflow | LangGraph-based orchestration with typed contracts and deterministic plan validation |
| Tool governance | Registry and executor for `knowledge_search`, `database_query`, `analysis_engine`, and `report_generator` |
| Human-in-the-loop | Persisted clarification and approval flows; approval edits may only tighten allowlisted arguments |
| Evidence and audit | Document, database, and calculation lineage plus append-only audit records |
| Reporting | Deterministic JSON and PDF artifacts with integrity checks |
| Asynchronous runtime | PostgreSQL-backed transactional dispatch, independent Worker, leases, heartbeats, fencing, and crash recovery |
| User interfaces | FastAPI HTTP API, Typer CLI, and React/TypeScript execution console |
| Persistence | SQLite for controlled tests; PostgreSQL 16 for the asynchronous service runtime |
| Interoperability | Optional MCP `2025-11-25` client/server boundary, disabled by default |
| Quality | Unit, integration, contract, smoke, security, frontend, and offline evaluation suites |

The Enterprise RAG Engine is an independent service and repository. This project consumes its
approved HTTP interface through the Knowledge Tool; it does not embed or reimplement that engine.

## Supported use cases

### Supplier Quality Analysis

The workflow accepts an authorized supplier scope and an explicit year and quarter, retrieves
approved quality knowledge, queries registered read-only views, calculates deterministic metrics,
and creates an internal report. If required time or scope information is missing, the task can
suspend for bounded interactive clarification and resume in the same durable task.

Example:

```text
Analyze Q2 2026 supplier quality deviations, compare them with the previous period,
and generate a JSON management report.
```

The supported analysis is intentionally limited to defect counts, inspection counts, defect rates,
and period trends. It does not make causal claims or execute corrective actions.

### Accounts Payable Investigation

The workflow analyzes a bounded invoice-date range using controlled policy snapshots, five
allowlisted read models, and seven deterministic analytics operations. It supports six exception
types: exact duplicate invoices, PO amount variance, missing required PO, late payment, material
early payment, and overpayment.

Example:

```text
Review accounts payable invoice compliance for Q2 2026 and generate a PDF exception report.
```

The Accounts Payable vertical slice is implemented and validated with local synthetic data, but its
formal production readiness decision remains **NOT READY** until the deployment-specific gates in
the [readiness review](docs/use-cases/accounts-payable/stage-12-production-readiness-review.md) are
satisfied.

## How it works

```text
Client
  |
  v
FastAPI / CLI
  -> authenticate and validate
  -> persist Task + dispatch atomically
  -> return 202 Accepted
  |
  v
PostgreSQL Queue
  |
  v
Independent Worker
  -> task understanding and clarification
  -> planning and plan validation
  -> policy check and approval, when required
  -> governed tool execution
  -> evidence aggregation
  -> report generation
  -> independent verification
  -> durable result + JSON/PDF artifact
```

The Copilot persistence database and the enterprise business database are separate security
boundaries:

- `PERSISTENCE_DATABASE_URL` stores tasks, runtime state, dispatches, evidence, approvals, audit
  records, artifact metadata, leases, and checkpoints.
- `DATABASE_URL` is available only to the registered read-only Database Tool and cannot access
  Copilot-owned tables through the application architecture.

The API does not execute the graph inline. A submission returns `202 Accepted`, and clients poll the
task resource while an independent Worker executes or resumes the workflow. The task database—not
the queue, Worker memory, or a LangGraph checkpoint—is authoritative.

For design details, see [Architecture](docs/architecture.md) and the
[asynchronous runtime architecture](docs/async-runtime-architecture.md).

## Quick start

### Prerequisites

- Python 3.11 or later
- PostgreSQL 16 for the full asynchronous runtime
- Node.js 22 and npm for frontend development
- Docker Engine with Compose v2 for the containerized environment
- An approved or locally built Enterprise RAG Engine image for the complete Compose topology

### 1. Install the project

From the repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

The default development configuration uses mock LLM, knowledge, and business-database adapters. Do
not place real credentials in `.env` or commit that file.

### 2. Run the offline smoke workflow

The fastest way to verify the core governed workflow requires no external service:

```bash
python scripts/smoke_agent.py
```

This runs an isolated Supplier Quality task with deterministic adapters and verifies task state,
steps, evidence, tracing, and the generated JSON artifact.

### 3. Start the service stack

The development Compose topology requires the independent RAG image. Obtain or build that image,
then expose its tag through `RAG_IMAGE` (the default is `enterprise-rag-engine:local`):

```bash
export RAG_IMAGE=enterprise-rag-engine:local
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

Compose starts two separate PostgreSQL databases, database migration and seed jobs, an RAG health
check, the Copilot API, and an independent Worker. Verify the API:

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

Stop the stack without deleting its named volumes:

```bash
docker compose down
```

For the complete browser-to-artifact environment, including the frontend and controlled RAG
ingestion, follow [Local Enterprise E2E](docs/local-enterprise-e2e.md). On macOS, that guide also
documents the included `一键启动或关闭.command` shortcut.

## Using the API

Submit a task:

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: supplier-quality-q2-2026' \
  -d '{
    "task": "Analyze Q2 2026 supplier quality deviations and generate a JSON report."
  }'
```

A successful submission returns `202 Accepted` with a `task_id`, `trace_id`, `status_url`, and
`artifacts_url`. Poll the returned task resource:

```bash
curl http://127.0.0.1:8000/v1/tasks/TASK_ID
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/steps
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/evidence
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts
curl -OJ http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts/ARTIFACT_ID
```

Cancel a non-terminal task:

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks/TASK_ID/cancel
```

Tasks may enter `WAITING_CLARIFICATION` or `WAITING_APPROVAL`. Both states release the Worker lease
and expose a persisted interaction through the task detail response. Submitting a clarification or
approval decision creates a new asynchronous dispatch; it does not execute the graph in the API
request.

See the [HTTP API guide](docs/api.md) for task listing, clarification, approval, cancellation,
artifact download, health semantics, and stable error responses.

### CLI

The installed console command submits through the same configured asynchronous service:

```bash
enterprise-copilot --help
enterprise-copilot \
  "Analyze Q2 2026 supplier quality deviations and generate a JSON report." \
  --demo --wait
```

The CLI requires the same migrated PostgreSQL persistence and a running Worker. `--demo` is allowed
only in development or test environments; production tasks must enter through the authenticated API.

## Frontend

The React + TypeScript frontend provides a chat-first task workspace with task history, multi-round
clarification, approval and cancellation controls, execution/evidence details, verified artifact
cards, and health status.

Run it against a local API:

```bash
cd frontend
npm ci
npm run dev
```

The Vite server listens on [http://127.0.0.1:5173](http://127.0.0.1:5173) and proxies `/api` to the
backend on port `8000`. See [Frontend development](docs/frontend-development.md) for setup and test
details.

## Configuration

All application configuration is loaded through `copilot.config.Settings`. Start with
[`.env.example`](.env.example) and keep secrets in an approved runtime secret manager.

| Variable | Purpose | Development default |
|---|---|---|
| `APP_ENV` | Runtime profile | `development` |
| `IDENTITY_PROVIDER` | Caller identity adapter | `demo` |
| `PERSISTENCE_DATABASE_URL` | Copilot-owned state database | Local fallback; PostgreSQL required by the async service |
| `PERSISTENCE_AUTO_CREATE_SCHEMA` | Development schema helper | `true` |
| `QUEUE_PROVIDER` | Task queue adapter | `postgresql` |
| `DATABASE_PROVIDER` | Enterprise business-data adapter | `mock` |
| `DATABASE_URL` | Read-only enterprise business database | Synthetic SQLite URL |
| `KNOWLEDGE_PROVIDER` | Knowledge adapter | `mock` |
| `RAG_BASE_URL` | Independent Enterprise RAG endpoint | `http://127.0.0.1:8000` |
| `LLM_PROVIDER` | Planning/model adapter | `mock` |
| `ARTIFACT_DIR` | Generated artifact content root | `data/artifacts` |
| `WORKER_CONCURRENCY` | Per-process execution slots | `4` |
| `MCP_ENABLED` | Optional MCP boundary | `false` |

Production configuration fails closed when required identity, secret, PostgreSQL, model, RAG,
checkpoint, policy-snapshot, or business-database requirements are missing. See the
[deployment guide](docs/deployment.md) for the complete production contract.

## Project structure

```text
.
├── src/copilot/
│   ├── api/              # FastAPI routes, schemas, and error mapping
│   ├── agent/            # LangGraph state, routing, and workflow nodes
│   ├── contracts/        # Stable typed boundaries
│   ├── evidence/         # Evidence lineage and verification inputs
│   ├── llm/              # Model adapters and structured output
│   ├── mcp/              # Optional MCP protocol boundary
│   ├── persistence/      # Task, runtime, audit, and checkpoint persistence
│   ├── policies/         # Permissions, risk, and approval decisions
│   ├── services/         # Application orchestration
│   ├── tools/            # Governed knowledge, database, analytics, and report tools
│   └── worker/           # Independent queue Worker runtime
├── frontend/             # React/TypeScript execution console
├── tests/                # Unit, integration, contract, smoke, and security tests
├── evaluation/           # Datasets, evaluators, baselines, and generated reports
├── migrations/           # Copilot persistence migrations
├── business_migrations/  # Synthetic business-data migrations
├── scripts/              # Thin operational and smoke entry points
├── docs/                 # Architecture, operations, security, and use-case documentation
├── docker-compose.yml    # Development service topology
└── pyproject.toml        # Python package and tool configuration
```

## Development and testing

Run the backend quality gates from the repository root:

```bash
ruff check .
ruff format --check .
mypy
pytest tests/unit
pytest tests/integration tests/contract tests/smoke
pytest tests/security
python scripts/check_docs.py
python scripts/check_architecture.py
python -m build
```

Run the deterministic evaluation suite:

```bash
python evaluation/run_eval.py --mode mock --seed 42 \
  --baseline evaluation/baselines/supplier_quality_v1.json \
  --fail-on-regression
python evaluation/run_mcp_eval.py --output /tmp/mcp-evaluation.json
```

Run frontend checks from `frontend/`:

```bash
npm run api:check
npm run typecheck
npm run lint
npm run format:check
npm run test
npm run build
npm run test:e2e
```

PostgreSQL integration tests require an isolated `TEST_POSTGRES_URL`. Ordinary unit tests and mock
evaluations do not call public internet services, production databases, or live model providers.

## Security and current limitations

Implemented controls include deny-by-default permissions, tenant and data-scope enforcement,
allowlisted read-only database templates, bounded tool inputs and outputs, approval binding,
prompt-injection isolation, sensitive-data filtering, artifact integrity checks, execution fencing,
and structured audit/evidence records.

Important boundaries:

- No arbitrary SQL or Python execution.
- No business-database writes, payment execution, CAPA execution, email, procurement action, or
  supplier-status change.
- No open-internet retrieval or automatic trust of third-party connectors.
- No guarantee of distributed exactly-once execution or forced interruption of an in-flight
  external call.
- No bundled enterprise IAM/SSO, secret manager, object store, centralized telemetry backend, or
  complete HA/DR solution.
- MCP is opt-in, namespace-restricted, and cannot bypass policy, approval, evidence, audit, or
  verification controls.
- Local and synthetic test results are not evidence of production-data, live-model, live-RAG, or
  production-load quality.

Read [Security Model](docs/security-model.md), [Operations](docs/operations.md), and
[Troubleshooting](docs/troubleshooting.md) before operating the system outside a local environment.

## Documentation

| Topic | Document |
|---|---|
| Project architecture | [docs/architecture.md](docs/architecture.md) |
| HTTP API | [docs/api.md](docs/api.md) |
| Task lifecycle | [docs/task-lifecycle.md](docs/task-lifecycle.md) |
| Deterministic workflow | [docs/deterministic-workflow.md](docs/deterministic-workflow.md) |
| Evidence and verification | [docs/evidence-and-verification.md](docs/evidence-and-verification.md) |
| Local enterprise environment | [docs/local-enterprise-e2e.md](docs/local-enterprise-e2e.md) |
| Deployment | [docs/deployment.md](docs/deployment.md) |
| Operations | [docs/operations.md](docs/operations.md) |
| Evaluation | [docs/evaluation.md](docs/evaluation.md) |
| MCP architecture and security | [docs/mcp-architecture.md](docs/mcp-architecture.md), [docs/mcp-security.md](docs/mcp-security.md) |
| Supplier Quality frozen baseline | [docs/design/design_baseline.md](docs/design/design_baseline.md) |
| Accounts Payable use case | [docs/use-cases/accounts-payable/README.md](docs/use-cases/accounts-payable/README.md) |
| Architecture decisions | [docs/adr/README.md](docs/adr/README.md) |

## Contributing

Before changing behavior, read [`AGENTS.md`](AGENTS.md) and the applicable frozen design documents.
Use small, contract-first changes; add proportionate tests; preserve policy, evidence, audit, and
verification boundaries; and run the relevant quality gates before opening a review.

The repository uses Conventional Commit-style subjects such as `feat:`, `fix:`, `docs:`, `test:`,
and `refactor:`.

## License

No software license is currently declared: the repository's `LICENSE` file is empty. Until the
project owner adds license terms, no permission to copy, modify, or distribute the code should be
assumed.
