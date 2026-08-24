# Local Enterprise Single-Machine E2E

This environment runs the implemented Supplier Quality Analysis v1.1 and Accounts Payable v1.1
workflows as real local services. It is an enterprise-style integration environment for one
developer machine, not a production deployment, high-availability design, or new Agent
capability.

## Architecture

```text
Browser
  -> http://127.0.0.1:8080
  -> frontend (static UI + /api reverse proxy)
  -> copilot-api
       -> enterprise-rag-engine (GET /health, POST /ask)
            -> rag-generation-stub (safe default for grounded generation only)
       -> AP policy snapshot (published once, mounted read-only)
       -> business-postgres (frozen Supplier Quality/AP read-only query templates)
       -> copilot-postgres (Task/Evidence/Audit/Artifact metadata/checkpoints)
       -> optional external Planner HTTPS (only when explicitly authorized)
  -> JSON/PDF Artifact download through copilot-api and frontend
```

The browser uses only the frontend origin. `/api/...` is stripped by Nginx and proxied to the
existing Copilot API, so `/api/v1/tasks` becomes `copilot-api:8000/v1/tasks`. Browser JavaScript
never resolves a Compose service name and never receives a database password, RAG credential, or
model key.

## Service responsibilities

| Service | Responsibility | Lifecycle |
|---|---|---|
| `frontend` | React enterprise execution console and same-origin `/api` reverse proxy | long-running, host port `127.0.0.1:8080` |
| `copilot-api` | Existing API, LangGraph, policies, tools, Evidence, reporting, and verifier | long-running, internal port 8000 |
| `copilot-migrate` | Alembic `upgrade head` plus official LangGraph PostgreSQL saver setup | one-shot, successful exit 0 |
| `copilot-postgres` | Copilot-owned Task, state, Evidence, Approval, Audit, Artifact metadata, and checkpoint data | long-running, private port 5432 |
| `business-postgres` | Synthetic Supplier Quality and AP records queried by governed database tools | long-running, private port 5432 |
| `business-db-seed` | Deterministic Supplier Quality seed and frozen checksum validation | one-shot, successful exit 0 |
| `ap-business-db-seed` | Additive deterministic AP seed and frozen checksum validation | one-shot, successful exit 0 |
| `ap-policy-publish` | Validates and publishes the controlled AP policy bundle to an immutable snapshot | one-shot, successful exit 0 |
| `enterprise-rag-engine` | Independently packaged Enterprise RAG HTTP service | long-running, private port 8000 |
| `enterprise-rag-ingest` | Optional explicit use of the RAG repository's official ingestion command | profile-gated one-shot |
| `rag-generation-stub` | Local OpenAI-compatible final-generation boundary; it does not implement retrieval | long-running, backend-only private port 8000 |

The one-shot seed services are necessary because the authoritative schemas and data are
Python/SQLAlchemy contracts in `copilot.tools.database`, not a second hand-written SQL schema. The
AP seed is additive and must not alter the frozen Supplier Quality fixture.

## Data and network boundaries

`PERSISTENCE_DATABASE_URL` points only to `copilot-postgres`. `DATABASE_URL` points only to
`business-postgres` and is consumed by the governed Database Tool. These databases use separate
users, databases, and named volumes.

The `quality_readonly` runtime role has:

- database `CONNECT`, schema `USAGE`, and table `SELECT` only;
- `default_transaction_read_only=on`;
- no superuser, database creation, role creation, schema creation, or temporary-table authority.

The application still applies its SQLAlchemy Select/template allowlist, AST/schema checks,
tenant/date/supplier restrictions, and server-side read-only transaction. PostgreSQL grants are an
additional boundary, not a replacement for those controls.

The controlled AP policy bundle does not enter the Supplier Quality RAG collection. The publisher
validates its manifest and document bytes, writes a tenant-bound snapshot, and the API mounts the
snapshot volume read-only. Startup fails closed if the current pointer, snapshot identity,
manifest, or any policy document has been changed.

Two private bridge networks keep the presentation and dependency planes separate:

- `enterprise-edge`: `frontend` and `copilot-api` only;
- `enterprise-backend`: `copilot-api`, both PostgreSQL services, migration/seed jobs, formal RAG,
  and the local generation stub.

The backend bridge is not published to the host. It retains normal outbound connectivity because
Copilot and RAG may use separately configured external model endpoints only after an explicit
data-governance decision. Only frontend is published, bound to `127.0.0.1` by default.

## Persistence and recovery boundary

| Named volume | Contents |
|---|---|
| `copilot-postgres-data` | Copilot relational state plus LangGraph checkpoints |
| `business-postgres-data` | Supplier Quality and additive AP business tables and deterministic demo records |
| `copilot-artifacts` | Immutable JSON/PDF report bytes |
| `copilot-policy-snapshots` | Tenant-bound, manifest-validated AP policy snapshots |
| `enterprise-rag-data` | RAG-owned Chroma, parent-store, FAQ, and model cache under `/app/data` |

A complete local Copilot restore requires both `copilot-postgres-data` and
`copilot-artifacts`. PostgreSQL stores Artifact metadata and checksums, not the Artifact bytes.
The RAG volume is independent of both databases.

## Prerequisites

- Docker Engine/Desktop with Compose v2.
- A sibling checkout at `../Enterprise-RAG-Engine` with its formal Dockerfile and controlled corpus.
- The formal image `enterprise-rag-engine:local`, built by the sibling repository.
- The sibling RAG corpus at `../Enterprise-RAG-Engine/enterprise-documents`, or another explicit
  `ENTERPRISE_RAG_DOCUMENTS_PATH`.
- No external Planner key is required for the deterministic Stage 11 acceptance run. An external
  Planner key is required only for a separately authorized provider-path test.
- No external RAG generation key is required for the mandatory Gate A. The safe default uses the
  local generation stub while retaining real PDF ingestion, embedding, Chroma, BM25, hybrid
  retrieval, Cross-Encoder reranking, sources, and contexts.

Build the image from the owning sibling repository; this Copilot repository consumes the image and
does not copy or own the RAG Dockerfile:

```bash
cd ../Enterprise-RAG-Engine
docker build -t enterprise-rag-engine:local .
cd ../Agentic-Enterprise-Knowledge-Copilot
```

## Configuration

Create the ignored local file and replace development placeholders:

```bash
cp .env.local-enterprise.example .env.local-enterprise
```

At minimum, set:

```text
ENTERPRISE_RAG_IMAGE=enterprise-rag-engine:local
COPILOT_LLM_PROVIDER=mock
```

Leave `ENTERPRISE_RAG_DEEPSEEK_BASE_URL=http://rag-generation-stub:8000` for the safe local Gate A.
External grounded generation is an explicit data-governance choice described below.

Use `COPILOT_LLM_PROVIDER=deepseek` and set `LLM_API_KEY` only for an explicitly authorized external
Planner test. Do not commit `.env.local-enterprise`. The frontend service has no `environment`
block, so none of these values enter its container or browser assets. `LLM_API_KEY`, when present,
is passed only to `copilot-api`; the one-shot jobs use the Mock provider and receive no model
credential.

The environment deliberately uses `APP_ENV=development`, `IDENTITY_PROVIDER=demo`, and a configured
`TENANT-DEMO` local identity because no upstream enterprise gateway is part of this single-machine
stage. Demo identity is accepted only outside production; the production profile still requires a
signed trusted-header identity and rejects this configuration.

## Initial RAG ingestion

Query execution never ingests documents. The formal image intentionally contains no index or source
documents. On a fresh volume, start the data services/jobs, run the official one-shot ingestion
command, and only then start the query/API/frontend services:

```bash
docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml down -v

docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml up -d copilot-postgres business-postgres

docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml run --rm copilot-migrate

docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml run --rm business-db-seed

docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml run --rm ap-business-db-seed

docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml run --rm ap-policy-publish
```

Then ingest the five read-only mounted PDFs:

```bash
docker compose \
  --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml \
  --profile ingest \
  run --rm enterprise-rag-ingest
```

This reads controlled PDFs from `/app/enterprise-documents/pdf` and writes only the RAG volume. It
does not run during service startup or `POST /v1/tasks`. A successful fresh standard ingestion is
expected to report five documents, 81 chunks created/stored, and collection
`supplier_quality_demo`.

`GET /health` is process health only. An empty index can still return health 200 while `/ask`
returns 503. During Compose startup, the one-shot `rag-warmup` service calls the real `/ask` path
before the API may start. It loads lazy embedding/reranking dependencies and fails startup when the
query path is unavailable; its output contains only bounded counts, latency, and Trace metadata.

## Startup

### macOS one-click start and shutdown

In Finder, double-click `一键启动或关闭.command` in the repository root. When the
environment is stopped, the shortcut validates and builds the Local Enterprise Compose topology,
waits for the frontend health endpoint, and opens it in the default browser. Double-click the same
shortcut again to run Compose `down` and remove all project containers and networks while
preserving the named database, Artifact, and RAG volumes.

On first use only, if `.env.local-enterprise` does not exist, the shortcut creates it from the
committed example and opens it for editing. Keep the deterministic Mock Planner or configure an
explicitly authorized external Planner, save the file, and double-click the shortcut again. The
formal RAG image and initial RAG ingestion described above remain prerequisites; the shortcut does
not reset or silently re-ingest the governed RAG index.

The shortcut starts Docker Desktop when it is installed but not running. `FRONTEND_PORT` is read
from `.env.local-enterprise`, and startup waits up to 10 minutes by default. Set
`COPILOT_START_TIMEOUT_SECONDS` in the launching environment to override that wait.

The equivalent manual commands follow.

Validate interpolation before starting services:

```bash
docker compose \
  --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml \
  config
```

Build and start the complete topology:

```bash
docker compose \
  --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml \
  up --build -d

docker compose \
  --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml \
  ps
```

Expected steady state:

- `frontend`, `copilot-api`, `copilot-postgres`, `business-postgres`, and
  `enterprise-rag-engine`: running and healthy;
- `copilot-migrate`, `business-db-seed`, `ap-business-db-seed`, `ap-policy-publish`, and
  `rag-warmup`: exited with code 0.

The workflow owns governed retries. Its HTTP adapter performs one transport attempt of at most nine
seconds so that a timeout is normalized to `KNOWLEDGE_TIMEOUT` before the frozen 10-second tool
attempt expires. Workflow attempts remain independently audited and share the frozen 25-second
overall tool deadline.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). A direct Copilot host port is intentionally not
published.

## Example tasks

JSON:

```text
Analyze supplier quality for Q2 2026, compare it with the previous period,
check the approved supplier quality policy, and generate a JSON management report.
```

PDF:

```text
Analyze supplier quality for Q2 2026, compare it with the previous period,
check the approved supplier quality policy, and generate a PDF management report.
```

AP clean JSON:

```text
Analyze Accounts Payable for LE-US-01 on 2026-06-01, identify payment exceptions under the
approved AP policies, and generate a JSON management report.
```

AP mixed JSON/PDF:

```text
Analyze Accounts Payable for LE-US-01 in Q2 2026, identify payment exceptions under the approved
AP policies, and generate a JSON or PDF management report.
```

The UI displays the real `task_id`, status, `trace_id`, four plan steps, Evidence metadata, and
Artifact list. It does not hard-code successful states. If the task enters `WAITING_APPROVAL`, the
pending approval ID is displayed; resolution still requires the existing API and an authorized
approver identity.

The Local Enterprise identity contains the four demo roles required by the two frozen workflows:
`quality_analyst`, `quality_data_approver`, `finance_analyst`, and `finance_approver`. Override
`DEMO_ENTERPRISE_ROLES` only in an uncommitted `.env.local-enterprise` when testing denial. This is
development-only authority and is rejected by the production configuration profile.

## Artifact download and automated smoke

Artifacts are downloaded through the same frontend origin:

```bash
curl -OJ \
  http://127.0.0.1:8080/api/v1/tasks/TASK_ID/artifacts/ARTIFACT_ID
```

Run the browser-facing JSON/PDF chain:

```bash
python scripts/local_enterprise_smoke.py \
  --base-url http://127.0.0.1:8080 \
  --env-file .env.local-enterprise \
  --require-formal-rag \
  --project-name copilot-stage11-e2e \
  --planner-provider-label controlled-local-mock \
  --report-output evaluation/reports/accounts-payable-stage11/DATE/report.json
```

Run the destructive-to-availability but data-preserving local verification suite. This stops and
starts RAG and Business DB, restarts Copilot, reruns migration, and attempts denied SQL writes:

```bash
python scripts/local_enterprise_smoke.py \
  --base-url http://127.0.0.1:8080 \
  --env-file .env.local-enterprise \
  --require-formal-rag \
  --with-runtime-checks
```

The smoke covers Supplier Quality JSON/PDF, AP clean JSON, AP mixed JSON/PDF, AP approval across an
API restart, database/RAG outages, full-stack restart, tenant isolation, PostgreSQL SELECT-only
enforcement, durable Artifact downloads, and checksum validation. It requires the frozen Evidence
types, query fingerprints, calculation lineage, restricted-field absence, and configured-secret
absence. A controlled contract fixture can validate orchestration during development, but only
`--require-formal-rag` checks the formal Supplier Quality image identity, read-only document mount,
real `/ask`, formal corpus provenance, and fixture-marker absence. Use a new Compose project name
for a disposable fresh-volume acceptance run; never run `down -v` against an environment whose
volumes must be retained.

## LLM and data-egress boundaries

There are two independent model boundaries:

- **Copilot Planner LLM** receives the user task, trusted scope, task contract, and tool manifest.
- **RAG Grounded Generation LLM** receives the retrieved document contexts needed to compose the
  answer returned by `/ask`.

The default local Gate A uses a deterministic Mock Planner for the first boundary and a
backend-only deterministic generation stub for the second. Supplier Quality retrieval remains
formal and real; AP policy retrieval uses the controlled local snapshot described above. If an
operator configures either external endpoint, task details or retrieved enterprise-document
context may leave the local machine. The presence of an API key is not data-egress authorization;
enable either path only after an explicit governance decision. A Mock/local-stub result must never
be reported as an external DeepSeek full-path pass.

## Shutdown and reset

Stop containers while preserving all named volumes:

```bash
docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml stop
```

Remove containers and networks while preserving named volumes:

```bash
docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml down
```

Delete the entire disposable local enterprise dataset and rebuild from a fresh state:

```bash
docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml down -v
```

`down -v` deletes both PostgreSQL databases, all Artifact bytes, AP policy snapshots, and the RAG
index. This is expected data loss for an explicitly disposable local environment and is not a
recovery operation. Repeat the explicit migration, both business seeds, AP policy publication, and
formal ingestion sequence before startup.

## Troubleshooting

### RAG unhealthy or query returns no DOCUMENT Evidence

Confirm the independent image exists, `GET /health` works inside its container, the RAG key is set,
and `enterprise-rag-data` contains the configured `supplier_quality_demo` collection. Run the
explicit ingestion step when the index is absent. A 200 health response alone is not a grounding
gate; the E2E smoke must observe DOCUMENT Evidence with source metadata and a RAG trace.

### Copilot migration failure

Inspect `copilot-migrate` logs and verify `copilot-postgres` is healthy. Rerun the one-shot service:

```bash
docker compose --env-file .env.local-enterprise \
  -f docker-compose.local-enterprise.yml run --rm copilot-migrate
```

Do not enable `PERSISTENCE_AUTO_CREATE_SCHEMA` or start API workers before migration succeeds.

### Business DB unavailable or seed failure

Inspect `business-postgres`, `business-db-seed`, and `ap-business-db-seed` logs. The Supplier
Quality seed validates 17 suppliers, 5,000 inspections, full 2026 coverage, and frozen S-100/S-200
Q1 totals. The additive AP seed validates its own frozen dataset checksum without changing those
Supplier Quality totals. The API uses only the `quality_readonly` role, never the seed
administrator.

### AP policy snapshot unavailable or rejected

Inspect `ap-policy-publish` logs and the `copilot-policy-snapshots` mount. Republish from the
controlled repository fixtures; never edit a published snapshot or its current pointer in place.
The API intentionally fails startup when document bytes or manifest identity no longer match.

### Model boundary failure

`LLM_API_KEY` belongs to `copilot-api`. The default RAG generation endpoint is local and uses a
non-secret placeholder token. If external RAG generation has been explicitly authorized,
`ENTERPRISE_RAG_DEEPSEEK_API_KEY` belongs only to RAG. Verify endpoint/key ownership without
printing either value. RAG `/health` can remain 200 even when generation or the index is unusable.

### Artifact permission problem

Check that `copilot-artifacts` is mounted at `/app/data/artifacts` and writable by container UID/GID
10001. Do not edit Artifact files or stored checksums. Restore the file volume together with the
matching Copilot PostgreSQL state.

### Port conflict

Change only `FRONTEND_PORT` in `.env.local-enterprise`. Databases, API, and RAG are intentionally
not published and should not need host-port changes.

### Frontend proxy returns 502

Check `copilot-api` health and Nginx logs. The proxy target must remain
`http://copilot-api:8000`; `localhost` inside frontend would address Nginx itself. Readiness can be
503 while liveness remains 200 during a dependency outage.

## Non-goals

This stage does not add production cloud deployment, AWS, Kubernetes, Helm, high availability,
horizontal scaling, background workers, Redis, Kafka, RabbitMQ, observability backends, S3,
enterprise SSO, multi-Agent behavior, MCP expansion, CAPA/ERP writes, email, Jira, Teams, or Slack.
Existing MCP code remains untouched and disabled.
