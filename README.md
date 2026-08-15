# Agentic Enterprise Knowledge Copilot

Agentic Enterprise Knowledge Copilot is a governed, evidence-backed task-completion system. It
turns a natural-language enterprise request into a validated plan, executes only approved tools,
records Evidence and audit lineage, verifies the result, and produces an immutable report
Artifact. Stage 17 packages the implemented Stage 0–16 vertical slice as an installable,
migration-driven, Docker-ready service with SQLite development storage and PostgreSQL deployment
persistence. Stage 17.1 hardens identity, mandatory execution context, tenant persistence,
approval enforcement, registry lifecycle, cancellation, correlation, and production configuration
without adding a business capability. Stage 18 adds an optional governed MCP `2025-11-25`
client/server interoperability boundary while preserving that frozen business behavior.

The **Enterprise RAG Engine is a separate service and repository**. The Copilot consumes its
approved HTTP contract through the Knowledge Tool; this repository does not copy, embed, or
reimplement the RAG service.

## Architecture

```text
User -> API / CLI -> Task Understanding -> Planner -> Policy / Approval
     -> Tool Registry / Executor -> Knowledge + Database + Analytics + Reporting
     -> Evidence -> Verifier -> TaskResult + Artifact

External boundaries:
  Enterprise RAG Engine       Copilot PostgreSQL       Artifact filesystem/volume
  Enterprise business DB     (internal state)          (report bytes)
  Approved MCP servers       Authenticated MCP clients (optional Stage 18 edges)
```

The Copilot persistence database and enterprise business database are deliberately different:

- `PERSISTENCE_DATABASE_URL` stores Task, State, plans/results, Evidence, Approval, Audit,
  Artifact metadata, leases, and PostgreSQL checkpoints.
- `DATABASE_URL` is visible only to the registered, read-only enterprise Database Tool. It cannot
  access Copilot internal tables through the application architecture.

The API and CLI share `build_application(settings)` as their composition root. Docker does not
create a second workflow or bypass Policy, Approval, Registry/Executor, Evidence, Audit,
Observability, or Verification.

See [Architecture](docs/architecture.md), the [frozen v1.1 baseline](docs/design/design_baseline.md),
and [ADR-006](docs/adr/ADR-006-deployment-persistence-boundary.md). Stage 18 admission is recorded
in [ADR-007](docs/adr/ADR-007-stage-18-mcp-readiness-boundary.md); the pinned protocol decision is
[ADR-008](docs/adr/ADR-008-mcp-protocol-2025-11-25.md).

## Supported vertical slice

The only implemented business scenario is **Supplier Quality Deviation Investigation / Supplier
Quality Analysis v1.1**. A request must include an explicit year and quarter. The frozen four
tools are `knowledge_search`, `database_query`, `analysis_engine`, and `report_generator`.
Artifacts are PDF or JSON.

Current boundaries are intentional:

- no CAPA execution, email, procurement, supplier-status change, or business-database write;
- no arbitrary SQL/Python, open internet source, or unregistered connector;
- no cross-database atomic transaction or external API exactly-once guarantee;
- no background task queue or forced interruption of a synchronous in-flight external call; such
  calls expose cancellation-requested state and their late output is discarded;
- no bundled enterprise IAM/SSO: production verifies a short-lived signed assertion from an
  approved upstream gateway, while Demo Identity is restricted to explicit development/test use;
- no automatic MCP trust or export: only approved server namespaces and explicit export rules are
  implemented; MCP does not broaden the frozen four-tool business scope.

## Requirements and installation

- Python 3.11 or later
- Node.js 22 and npm for frontend development
- Docker Engine with Compose v2 for the container path
- PostgreSQL 16 for the deployment/integration path
- a separately built or approved Enterprise RAG Engine image for the full Compose topology

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

All configuration enters through `copilot.config.Settings`. Keep real credentials out of `.env`,
Git, image layers, logs, task text, and Artifacts.

## Local development

The default `.env.example` uses offline Mock LLM/Knowledge/Database adapters and local SQLite. It
is suitable for deterministic development:

```bash
python -m copilot.persistence.migrate
uvicorn copilot.bootstrap.api:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/ready
```

`PERSISTENCE_AUTO_CREATE_SCHEMA=true` is a development/test compatibility helper. To exercise the
deployment discipline locally, set it to `false`, set an explicit
`PERSISTENCE_DATABASE_URL=sqlite:///data/database/copilot.db`, and run the migration command before
starting the API.

To use the real read-only business Database Tool with the synthetic SQLite fixture:

```bash
python scripts/seed_demo_database.py --reset
export DATABASE_PROVIDER=sqlalchemy
export DATABASE_URL=sqlite:///data/database/enterprise_demo.db
```

This database is enterprise business data for Tool reads; it is not Copilot persistence.
The deterministic dataset contains 17 fictional suppliers and exactly 5,000 incoming inspections
across all 12 months of 2026. It writes a query-derived profile to
`data/demo/supplier_quality_dataset_profile.json`. See the
[demo business database guide](docs/demo-business-database.md) and
[contract audit](docs/database-contract-audit.md).

The same allowlisted adapter accepts PostgreSQL, uses a server-side read-only transaction and
statement timeout, and is included in `/health/ready` dependency checks. Development Compose now
uses a separate seeded `business-postgres` service and SELECT-only runtime role; it does not reuse
the Copilot persistence PostgreSQL database.

## Frontend

The implemented React + TypeScript console provides tenant/owner-scoped task history, governed
task submission, lifecycle and step inspection, minimized Evidence views, verified Artifact
downloads, cancellation, system health, and an authorized approval workbench. It preserves the
same-origin `/api` boundary and never accepts browser-selected identity, tenant, role, database,
RAG source, or tool configuration.

Install and run it against the local API:

```bash
cd frontend
npm ci
npm run dev
```

The Vite server listens on `http://127.0.0.1:5173` and proxies `/api` to the API on port 8000.
Run the frontend quality gates with:

```bash
npm run api:check
npm run typecheck
npm run lint
npm run format:check
npm run test
npm run build
npm run test:e2e
```

The Playwright suite starts a hermetic real FastAPI/Task Service/Agent workflow and verifies the
browser-to-Evidence-to-Artifact path plus approval, rejection, cancellation, and failure UX. See
[Frontend development](docs/frontend-development.md),
[Frontend architecture](docs/frontend-architecture.md), and the pre-migration
[Frontend audit](docs/frontend-audit.md).

## Docker Compose

### Local Enterprise E2E

For the browser-to-Artifact single-machine topology with separate RAG, Business PostgreSQL, and
Copilot PostgreSQL services, see [Local Enterprise E2E](docs/local-enterprise-e2e.md).

Build the formal image in the owning sibling repository:

```bash
cd ../Enterprise-RAG-Engine
docker build -t enterprise-rag-engine:local .
cd ../Agentic-Enterprise-Knowledge-Copilot
```

The Local Enterprise Compose consumes that image, explicitly ingests the sibling's five controlled
Supplier Quality PDFs into its own named RAG volume, and uses a backend-only local generation stub
by default so retrieved document contexts are not silently sent to an external provider. See the
guide for the fresh-volume sequence and the separate Planner/RAG data-egress boundaries.

Then start this repository:

```bash
cp .env.example .env
docker compose config
docker compose build
docker compose up
```

Compose starts persistence `postgres`, the separate synthetic `business-postgres`,
`enterprise-rag-engine`, one-shot `migrate`, `seed-business-database`, and `rag-health` services,
and `copilot-api`. The migration service runs Alembic and the official LangGraph PostgreSQL saver
setup; the business seed service initializes only the existing Supplier Quality ORM tables;
`rag-health` uses the Copilot's real HTTP Knowledge client without assuming utilities exist inside
the independent RAG image. All one-shot dependencies must succeed before the API starts. The API
reaches RAG as `http://enterprise-rag-engine:8000`, never through container-local `localhost`.
Local ports default to Copilot `8000`, RAG `8001`, persistence PostgreSQL `5432`, and business
PostgreSQL `5433`.

The committed PostgreSQL credentials are local demo values only. Never use them in production.
For an already-running RAG outside Compose, run the API outside Compose with an approved
`RAG_BASE_URL`, or provide a deployment-specific Compose override and network route. See
[Deployment](docs/deployment.md).

## Database and migrations

SQLite remains supported for fast tests and local demos. PostgreSQL is required by the production
configuration profile and is used by Compose. Copilot-owned schema changes are explicit:

```bash
alembic upgrade head
alembic current
alembic history
alembic downgrade -1  # isolated/non-production databases only after reviewing data loss
```

The normal API startup never runs `Base.metadata.create_all`, Alembic, or vendor checkpoint
migrations in production. The deployment command is:

```bash
python -m copilot.persistence.migrate
```

Artifact metadata is stored in the Copilot database. Artifact bytes remain beneath
`ARTIFACT_DIR`; Compose mounts a persistent `artifact-data` volume. A PostgreSQL backup therefore
does **not** include report files.

## Enterprise RAG service

Use the real HTTP adapter by setting:

```bash
KNOWLEDGE_PROVIDER=http
RAG_BASE_URL=http://approved-rag-host:8000
python scripts/check_rag_health.py
```

`RAG_TIMEOUT_SECONDS`, `RAG_MAX_ATTEMPTS`, and `RAG_RETRY_BASE_DELAY_SECONDS` bound dependency
calls. RAG failure can make task acceptance degraded while `/health/live` and historical task
reads remain available. CI uses controlled offline adapters; live RAG verification is explicit
and is not falsely represented by Mock tests.

## Main API

Submit and inspect a task:

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{"task":"Analyze Q2 2026 supplier quality deviations and generate a JSON report."}'

curl http://127.0.0.1:8000/v1/tasks/TASK_ID
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/steps
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/evidence
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts
curl -OJ http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts/ARTIFACT_ID
curl -X POST http://127.0.0.1:8000/v1/tasks/TASK_ID/cancel
```

Approval APIs actually implemented by this repository are:

```bash
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/approvals/APPROVAL_ID
curl -X POST http://127.0.0.1:8000/v1/tasks/TASK_ID/approvals/APPROVAL_ID \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve","reason":"Reviewed"}'
```

`EDIT` requires complete replacement arguments and can only lower the frozen `top_k` or
`row_limit` allowlist value. A waiting approval is durable and can resume after process restart.
See [HTTP API](docs/api.md) and [Human-in-the-loop](docs/stage-12/human-in-the-loop.md).

Health semantics:

- `GET /health` preserves the original process-health contract: `{"status":"ok"}`.
- `GET /health/live` reports process liveness only.
- `GET /health/ready` reports safe database, Artifact storage, and configured RAG status. HTTP 503
  means new governed tasks should not be accepted; it does not imply that the process is dead.

## CLI

```bash
enterprise-copilot --help
python scripts/run_task.py \
  "Analyze Q2 2026 supplier quality deviations and generate a JSON report." --demo
python scripts/inspect_task.py TASK_ID
python scripts/inspect_task.py TASK_ID --performance
python scripts/smoke_agent.py --show-trace
python scripts/check_rag_health.py
```

API and CLI use the same Task Service and LangGraph. CLI exit codes are documented in
[Operations](docs/operations.md).

## Important configuration

| Variable | Development default | Production requirement |
|---|---|---|
| `APP_ENV` | `development` | `production` with strict validation |
| `IDENTITY_PROVIDER` | `demo` | `trusted_headers`; no demo fallback |
| `IDENTITY_SIGNING_SECRET` | blank | injected secret, at least 32 bytes |
| `PERSISTENCE_DATABASE_URL` | local SQLite fallback | required PostgreSQL URL |
| `PERSISTENCE_AUTO_CREATE_SCHEMA` | `true` | `false`; run migrations separately |
| `DATABASE_URL` | demo business SQLite | approved read-only business DB |
| `DATABASE_PROVIDER` | `mock` | `sqlalchemy` |
| `KNOWLEDGE_PROVIDER` | `mock` | `http` |
| `RAG_BASE_URL` | host-local URL | approved non-loopback service URL |
| `LLM_PROVIDER` / `LLM_API_KEY` | `mock` / blank | real provider / injected credential |
| `ARTIFACT_DIR` | `data/artifacts` | persistent, writable, backed-up volume |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | structured stdout/stderr |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `10` | size for deployment concurrency |
| `MAX_TASK_STEPS` | `10` | may be tightened, not expanded past policy |
| `MCP_ENABLED` | `false` | explicit opt-in; client/server roles separately enabled |
| `MCP_PROTOCOL_REVISION` | `2025-11-25` | pinned; upgrades require ADR and compatibility gates |
| `MCP_JWT_ISSUER` / `MCP_JWT_AUDIENCE` | blank | required for HTTP server mode |

The complete safe template is [.env.example](.env.example). The production adapter validates a
gateway-signed user, tenant, roles, scopes, data scope, purpose, and timestamp; the upstream
enterprise gateway/IdP remains a required deployment dependency.

## Security

The implemented path uses a deny-by-default permission matrix, read-only query templates and AST
validation, tenant/supplier scope, bounded output, prompt-injection isolation, sensitive-data
filtering, Approval binding, append-only Audit, Evidence lineage, Artifact integrity checks, and
independent Verification. Logs redact secret-shaped values and do not emit database URLs, raw
SQL, Authorization headers, tool payloads, or ordinary stack traces.

These controls are a production security foundation, not an enterprise IdP, a Secret Manager,
tamper-proof audit, or a disaster-recovery platform. Repository APIs require tenant scope, and the
executor independently requires exact authenticated context, policy, and approval even when
called directly. See [Security Model](docs/security-model.md).

## Observability and operations

Structured events go to stdout/stderr and retain safe correlation fields such as `task_id`,
`trace_id`, `step_id`, `node_name`, `tool_name`, `status`, `latency_ms`, `error_type`, and
`retry_count`. Local spans and metrics are bounded and process-local; durable Audit provides the
restart-safe operational trail.

Use [Operations](docs/operations.md) for service, log, migration, backup, recovery, Artifact, RAG,
and incident procedures. Use [Troubleshooting](docs/troubleshooting.md) for symptom-driven fixes.

## Testing and quality gates

```bash
ruff check .
ruff format --check .
mypy
pytest tests/unit --cov=copilot --cov-report=term-missing --cov-report=xml
pytest tests/integration tests/contract tests/smoke
pytest tests/security
python evaluation/run_eval.py --mode mock --seed 42 \
  --baseline evaluation/baselines/supplier_quality_v1.json --fail-on-regression
python evaluation/run_mcp_eval.py --output /tmp/mcp-evaluation.json
python scripts/check_docs.py
python scripts/check_architecture.py
python -m build
docker build .
RAG_IMAGE=enterprise-rag-engine:local docker compose config
docker compose -f docker-compose.production.yml config
```

Real PostgreSQL coverage uses an isolated `TEST_POSTGRES_URL`; GitHub Actions supplies a PostgreSQL
service container. Unit and ordinary integration tests do not call the public internet, DeepSeek,
production databases, or live RAG.

## Evaluation

```bash
python evaluation/run_eval.py
python evaluation/run_eval.py --tag smoke
```

Reports are written to `evaluation/reports`. The checked-in Mock baseline validates deterministic
regression behavior; it is not evidence of live-model, live-RAG, production-data, or production
latency quality. See [Offline Agent Evaluation](docs/evaluation.md).

## MCP interoperability (implemented, optional)

Stage 18 implements real stdio and Streamable HTTP clients, isolated per-server sessions,
capability discovery/normalization/import, explicit server export, JWT Bearer authorization,
resources, prompts, policy-gated sampling/elicitation/roots, progress, persistence,
reconnect/revocation and hermetic real-SDK tests. Imported and exported tool calls use the existing
Registry, Executor, Policy, Approval, Evidence, Audit and Observability path.

MCP is off by default. Production OAuth/IdP issuance, public TLS/reverse proxy and approval of each
remote server remain deployment responsibilities. See [MCP Architecture](docs/mcp-architecture.md),
[MCP Security](docs/mcp-security.md), and [MCP Operations](docs/mcp-operations.md).

## Roadmap

Stage 18 MCP interoperability is implemented as an optional boundary. Future roadmap work includes
additional reviewed third-party interoperability profiles, production IdP-specific deployment
adapters and any later MCP revision after the ADR-008 upgrade process.
