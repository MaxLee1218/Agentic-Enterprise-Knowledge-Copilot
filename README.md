# Agentic Enterprise Knowledge Copilot

Agentic Enterprise Knowledge Copilot is a governed, evidence-backed task-completion system. It
turns a natural-language enterprise request into a validated plan, executes only approved tools,
records Evidence and audit lineage, verifies the result, and produces an immutable report
Artifact. Stage 17 packages the implemented Stage 0–16 vertical slice as an installable,
migration-driven, Docker-ready service with SQLite development storage and PostgreSQL deployment
persistence.

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
and [ADR-006](docs/adr/ADR-006-deployment-persistence-boundary.md).

## Supported vertical slice

The only implemented business scenario is **Supplier Quality Deviation Investigation / Supplier
Quality Analysis v1.1**. A request must include an explicit year and quarter. The frozen four
tools are `knowledge_search`, `database_query`, `analysis_engine`, and `report_generator`.
Artifacts are PDF or JSON.

Current boundaries are intentional:

- no CAPA execution, email, procurement, supplier-status change, or business-database write;
- no arbitrary SQL/Python, open internet source, or unregistered connector;
- no cross-database atomic transaction or external API exactly-once guarantee;
- no background task queue or forced interruption of an in-flight external call;
- no production IAM/SSO adapter—the checked-in API/CLI identity is a Demo Identity;
- no MCP behavior. MCP files are future placeholders, not an implementation.

## Requirements and installation

- Python 3.11 or later
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
python scripts/seed_demo_database.py
export DATABASE_PROVIDER=sqlalchemy
export DATABASE_URL=sqlite:///data/database/enterprise_demo.db
```

This database is enterprise business data for Tool reads; it is not Copilot persistence.

## Docker Compose

First obtain an approved independently packaged RAG image and set its tag:

```bash
export RAG_IMAGE=approved-registry.example/enterprise-rag-engine:VERSION
```

The current sibling Enterprise RAG Engine source checkout does not itself provide a Dockerfile.
Its owning deployment must publish or otherwise supply this image; this Copilot repository does
not invent that packaging or copy the RAG source.

Then start this repository:

```bash
cp .env.example .env
docker compose config
docker compose build
docker compose up
```

Compose starts `postgres`, `enterprise-rag-engine`, one-shot `migrate` and `rag-health` services,
and `copilot-api`. The migration service runs Alembic and the official LangGraph PostgreSQL saver
setup; `rag-health` uses the Copilot's real HTTP Knowledge client without assuming utilities exist
inside the independent RAG image. Both must succeed before the API starts. The API reaches RAG as
`http://enterprise-rag-engine:8000`, never through container-local `localhost`. Local ports default
to Copilot `8000`, RAG `8001`, and PostgreSQL `5432`.

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
  "Analyze Q2 2026 supplier quality deviations and generate a JSON report."
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
| `PERSISTENCE_DATABASE_URL` | local SQLite fallback | required PostgreSQL URL |
| `PERSISTENCE_AUTO_CREATE_SCHEMA` | `true` | `false`; run migrations separately |
| `DATABASE_URL` | demo business SQLite | approved read-only business DB |
| `DATABASE_PROVIDER` | `mock` | `sqlalchemy` |
| `KNOWLEDGE_PROVIDER` | `mock` | `http` |
| `RAG_BASE_URL` | host-local URL | approved non-loopback service URL |
| `ARTIFACT_DIR` | `data/artifacts` | persistent, writable, backed-up volume |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | structured stdout/stderr |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `10` | size for deployment concurrency |
| `MAX_TASK_STEPS` | `10` | may be tightened, not expanded past policy |

The complete safe template is [.env.example](.env.example). The Demo Identity remains a known
deployment blocker for a true production rollout and must be replaced by a trusted authentication
adapter; Stage 17 does not pretend otherwise.

## Security

The implemented path uses a deny-by-default permission matrix, read-only query templates and AST
validation, tenant/supplier scope, bounded output, prompt-injection isolation, sensitive-data
filtering, Approval binding, append-only Audit, Evidence lineage, Artifact integrity checks, and
independent Verification. Logs redact secret-shaped values and do not emit database URLs, raw
SQL, Authorization headers, tool payloads, or ordinary stack traces.

These controls are a governed demo foundation, not production IAM, a Secret Manager, tamper-proof
audit, or a disaster-recovery platform. See [Security Model](docs/security-model.md).

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
python evaluation/run_eval.py --mode mock --seed 42 \
  --baseline evaluation/baselines/supplier_quality_v1.json --fail-on-regression
python scripts/check_docs.py
python scripts/check_architecture.py
python -m build
docker build .
RAG_IMAGE=enterprise-rag-engine:local docker compose config
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

## Roadmap

Stage 17 is deployment engineering only. **Stage 18: MCP Interoperability is not implemented and
remains a future phase.** Existing MCP directories and documentation are placeholders and do not
mean MCP client, server, transport, OAuth, import, or export behavior exists.
