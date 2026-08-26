# Deployment Guide

This guide deploys the hardened Copilot API and compiled execution console. It does not deploy the
upstream enterprise IdP/gateway, a managed secret store, an Enterprise RAG implementation, or
MCP. The Enterprise RAG Engine remains an independent service and image.

## Architecture

```text
Client
  -> approved identity gateway (external)
  -> frontend Nginx / React SPA
       -> same-origin /api proxy
       -> Copilot API
       -> Copilot PostgreSQL (task/state/evidence/approval/audit/artifact metadata/checkpoints)
       -> Artifact volume (report content)
       -> Approved AP policy bundle + immutable published snapshot (read-only mounts)
       -> Enterprise RAG Engine (approved HTTP contract)
       -> Enterprise business database (read-only Database Tool only)
```

`PERSISTENCE_DATABASE_URL` and `DATABASE_URL` are intentionally separate. The former is trusted
internal application state. The latter is an enterprise data source available only through the
read-only, allowlisted Database Tool. Neither API code nor agent nodes access either database
directly.

## Prerequisites

- Docker Engine and Docker Compose v2, or Python 3.11 plus PostgreSQL 16.
- An immutable frontend image built from `frontend/Dockerfile`.
- An approved Enterprise RAG Engine endpoint or separately built image.
- An owner-approved, tenant-bound AP policy bundle and its separately published immutable snapshot.
- A writable, persistent Artifact path.
- Deployment-managed secrets and backups for a non-demo environment.

## Environment variables

Start from `.env.example`, but do not put production secrets in a committed `.env` file.

| Variable | Purpose | Production rule |
|---|---|---|
| `APP_ENV` | Configuration profile | `production` |
| `DEBUG` | Debug behavior | `false` |
| `IDENTITY_PROVIDER` | Authentication adapter | `trusted_headers` |
| `IDENTITY_SIGNING_SECRET` | Trusted-gateway assertion verification | Secret injection, at least 32 bytes |
| `PERSISTENCE_DATABASE_URL` | Copilot-owned state | Explicit PostgreSQL URL |
| `PERSISTENCE_AUTO_CREATE_SCHEMA` | Test/dev schema helper | `false` |
| `DATABASE_URL` | Read-only enterprise business DB | Approved non-SQLite source |
| `DATABASE_PROVIDER` | Business DB adapter | `sqlalchemy` |
| `KNOWLEDGE_PROVIDER` | Knowledge adapter | `http` |
| `RAG_BASE_URL` | Independent RAG endpoint | Valid, approved, non-loopback URL |
| `RAG_TIMEOUT_SECONDS` | Per-attempt timeout | Bounded for the environment |
| `AP_POLICY_BUNDLE_DIR` | Controlled AP document/rule input | Read-only deployment mount |
| `POLICY_SNAPSHOT_DIR` | Published immutable AP snapshot root | Read-only deployment mount |
| `AP_POLICY_REQUIRE_PUBLISHED_SNAPSHOT` | Fail-closed AP publication gate | Required `true` |
| `LLM_PROVIDER`, `LLM_API_KEY` | Structured planning provider | Real provider and injected credential |
| `ARTIFACT_DIR` | Artifact content root | Writable persistent volume |
| `DB_POOL_SIZE` | PostgreSQL base pool | Match worker concurrency |
| `DB_MAX_OVERFLOW` | Temporary pool burst | Keep bounded |
| `DB_POOL_TIMEOUT_SECONDS` | Pool checkout wait | Fail within request budget |
| `LOG_LEVEL`, `LOG_FORMAT` | stdout/stderr logging | Structured and non-debug |

Production validation fails fast if trusted identity/signing material is missing, debug or
automatic schema creation is enabled, persistence is not PostgreSQL, real providers are not
selected, the business database uses SQLite, the model credential is absent, checkpointing is
disabled, the AP published-snapshot gate is disabled, the AP paths still point at the repository's
embedded demo locations, or RAG is loopback.
`DemoIdentityProvider` refuses production construction; failed authentication never falls back to
demo authority.

## Docker image build

The multi-stage image installs runtime dependencies from `pyproject.toml`, copies only runtime and
migration assets, and runs as UID/GID 10001.

```bash
docker build --pull -t enterprise-copilot:stage17 .
docker inspect enterprise-copilot:stage17 \
  --format '{{.Config.User}} {{json .Config.Healthcheck.Test}}'
```

No credential, RAG token, database password, or environment-specific URL belongs in the image.

Build the frontend separately. Its first stage performs a locked Node build; the final image is
the existing unprivileged Nginx runtime and contains no Node dependency tree:

```bash
docker build --pull -t enterprise-copilot-frontend:stage17 frontend
```

The frontend image has no secret-bearing environment configuration. It proxies `/api/` to the
Compose service `copilot-api:8000` and serves React Router deep links through `index.html`.

## Docker Compose

`docker-compose.yml` is an explicitly development topology. It contains local demo PostgreSQL
credentials and Mock LLM/business-database providers and must not be promoted as a production
manifest. `docker-compose.production.yml` is the fail-closed production expectation: it requires
immutable Copilot and frontend images, an approved RAG image, trusted identity secret, model
credential, PostgreSQL URL/credentials, read-only enterprise business database URL, and explicit
read-only paths for an approved AP policy bundle and published snapshot. It contains no secret
defaults and runs migration as a one-shot dependency before the API.

Obtain the independently packaged RAG image first:

```bash
export RAG_IMAGE=approved-registry.example/enterprise-rag-engine:VERSION
export COPILOT_IMAGE=approved-registry.example/enterprise-copilot:VERSION
export FRONTEND_IMAGE=approved-registry.example/enterprise-copilot-frontend:VERSION
export AP_POLICY_BUNDLE_PATH=/approved/config/accounts-payable-policy
export AP_POLICY_SNAPSHOT_PATH=/approved/state/accounts-payable-policy-snapshots
docker compose config
docker compose build
docker compose up -d
```

Production topology validation and startup use the separate file:

```bash
docker compose -f docker-compose.production.yml config
docker compose -f docker-compose.production.yml up -d
docker compose -f docker-compose.production.yml ps
curl --fail http://127.0.0.1:${COPILOT_PORT:-8000}/health/live
curl --fail http://127.0.0.1:${COPILOT_PORT:-8000}/health/ready
curl --fail http://127.0.0.1:${FRONTEND_PORT:-8080}/health
curl --fail http://127.0.0.1:${FRONTEND_PORT:-8080}/api/health/live
docker compose -f docker-compose.production.yml down
```

The production file intentionally does not publish PostgreSQL or RAG ports. The frontend port is
bound to host loopback only; an approved gateway on the host or a reviewed deployment override
must be the user-facing edge. Do not expose this port directly as a substitute for the gateway.
Keep `PERSISTENCE_DATABASE_URL` consistent with the composed PostgreSQL credentials, or point it
at an approved managed PostgreSQL and remove the local database through a reviewed override. The
enterprise business database remains an external, independently governed dependency.

The two AP policy paths are mandatory bind mounts. The API mounts both read-only, loads the
controlled bundle, and verifies that the current published snapshot has the exact tenant,
namespace, collection, rule-set, manifest, corpus and payload checksums before accepting the AP
profile. Production startup does not publish or repair policy content. A policy owner must run the
publication command in a separate controlled job, retain the prior immutable generation, and then
deploy the reviewed bundle and snapshot together. The synthetic `TENANT-DEMO` bundle shipped for
tests is not a production policy approval, and production configuration rejects its embedded
default bundle and snapshot paths even outside Compose.

The current sibling Enterprise RAG Engine source checkout has no Dockerfile. Its owner must supply
an approved image or separately governed image packaging. Do not improvise that packaging inside
the Copilot repository or copy RAG source into this image.

The topology contains `postgres`, `enterprise-rag-engine`, one-shot `migrate` and `rag-health`
services, `copilot-api`, and `frontend`. Compose waits for PostgreSQL health, a successful
migration, a successful real Knowledge-client RAG probe, and API readiness before starting the
frontend. The sidecar probe avoids
assuming that Python, curl, or wget exists inside the independent RAG image. `RAG_BASE_URL` uses
the Compose DNS name `http://enterprise-rag-engine:8000`; `localhost` inside the API container
would address the API container itself.

For an externally managed RAG, run the API outside this Compose topology with its approved URL, or
use a deployment-specific override that removes the RAG service and supplies a routable endpoint.
Do not copy the RAG code into this repository.

## PostgreSQL and Alembic

Alembic owns the Copilot relational schema. The LangGraph PostgreSQL saver owns its own checkpoint
tables, initialized explicitly by the same deployment command. API startup validates the schema
and does not run migrations or `create_all` in production.

```bash
export PERSISTENCE_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/copilot'
alembic upgrade head
alembic current
# Equivalent deployment entrypoint, including checkpoint saver setup:
python -m copilot.persistence.migrate
```

For a fresh PostgreSQL database, `alembic current` must report `20260808_0002 (head)` before the API
starts. Grant the runtime API role only the data privileges it needs; a separate deployment role
may own schema changes.

## RAG connectivity

```bash
export KNOWLEDGE_PROVIDER=http
export RAG_BASE_URL='http://approved-rag-host:8000'
python scripts/check_rag_health.py
```

The check must use the real Knowledge client. Passing Mock tests does not prove RAG connectivity.
Set authentication through the RAG client's approved secret mechanism; never put a token in a URL,
image layer, command transcript, or committed file.

## Deployment lifecycle and startup order

1. Prepare non-secret configuration and approved AP bundle/snapshot mounts.
2. Inject database and RAG secrets through the deployment platform.
3. Verify the AP policy bundle/snapshot pair in a separate publication job.
4. Start PostgreSQL and wait for `pg_isready`.
5. Run `python -m copilot.persistence.migrate` once as a deployment job.
6. Verify the independent RAG with `scripts/check_rag_health.py`.
7. Configure the upstream trusted gateway to replace spoofable inbound identity headers and sign
   the normalized identity assertion; route the browser origin to the frontend and preserve the
   signed assertion on `/api` requests.
8. Start `copilot.bootstrap.api:app` using the same composition root as local execution.
9. Start the frontend only after API readiness succeeds.
10. Check API `/health/live`, `/health/ready`, and frontend `/health` through the intended routes.
11. Run authenticated Supplier Quality and AP browser smoke requests with approved test data.

Do not have every API worker race to apply migrations.

## Health checks

- `/health` retains the compatibility response `{"status":"ok"}`.
- `/health/live` proves the process can serve requests; it does not prove dependencies are ready.
- `/health/ready` probes persistence, Artifact storage, the registered enterprise business schema
  when the real Database Tool is configured, and real RAG when configured. HTTP 503 means new task
  acceptance is unsafe. The process may still serve liveness and historical reads.

Health output contains component states and safe error categories, never secrets or raw connection
strings.

## Volumes

Compose creates `postgres-data` and `artifact-data`. PostgreSQL contains Artifact metadata only;
the `artifact-data` volume contains report bytes. AP policy input and snapshots are explicit
operator-owned bind mounts rather than API-writable volumes. Preserve ownership for UID/GID 10001
and mount the Artifact directory read/write. Do not mount source code or `.env` files into a
production image.

## Production configuration and secrets

- Use deployment-injected environment variables or secret-file integration backed by a real secret
  manager; rotate credentials outside the image.
- Use TLS and least-privilege roles for remote PostgreSQL, RAG, and business databases.
- Keep `PERSISTENCE_AUTO_CREATE_SCHEMA=false` and `DEBUG=false`.
- Terminate end-user authentication at the approved gateway, remove client-supplied Copilot
  identity headers, sign a short-lived assertion, and rotate the signing secret.
- Restrict the business database role to the approved read-only schema; the deterministic SQL
  allowlist remains mandatory.
- Send structured stdout/stderr logs to the platform log collector with access and retention rules.

## Deployment upgrade

```text
pull/build immutable application version
-> review release and migration
-> take required database and Artifact backups
-> run migration job
-> start the new application version
-> check readiness
-> run the smoke task
```

This project does not claim zero-downtime upgrades. Coordinate application compatibility with both
old and new schema versions when designing any future rolling deployment.

## Rollback

Application rollback means redeploying a previous immutable image. It is safe only if that image is
compatible with the current database schema.

Database rollback means applying a reviewed Alembic downgrade or restoring a backup. These are not
the same operation. The initial migration downgrade drops the Copilot schema and is destructive.
Not every production migration can be safely or losslessly downgraded. Prefer forward fixes; use a
tested restore procedure when a data-destructive change must be reversed.

## Backup considerations

Use platform snapshots or `pg_dump` independently for Copilot PostgreSQL and the enterprise
business database, and regularly test `pg_restore` into isolated databases. Back up the Artifact
volume, AP policy bundle, published AP policy generations and the independently owned RAG state
separately. Align the recovery point for Copilot metadata and Artifact bytes. A PostgreSQL backup
does not include Artifact or policy files. Preserve application/config/profile versions needed to
interpret a backup; never commit backup files to Git.

The repository CI dry-runs both PostgreSQL backup/restore boundaries with synthetic data. That
gate does not replace an environment-specific restore rehearsal, recovery-time objective,
recovery-point objective, encryption/access review, or Artifact/policy/RAG checksum sampling.

## Known limitations

- The Compose RAG service requires an independently supplied image; the current sibling RAG source
  checkout does not contain a Dockerfile.
- Trusted-header verification depends on a correctly configured upstream enterprise gateway; it is
  not itself an IdP or workforce lifecycle system.
- Artifact content uses one filesystem/volume rather than object storage.
- Audit is durable but not cryptographically tamper-proof.
- No distributed task queue or guaranteed forced cancellation of an in-flight external call.
- The future async runtime architecture is frozen in `docs/async-runtime-architecture.md`, but its
  outbox migration, Queue adapter, Worker, heartbeat/fencing implementation, recovery scanner,
  backpressure, and failure/soak evidence are not deployed by this guide.
- No automatic cross-resource transaction for PostgreSQL, RAG, business DB, and Artifact storage.
- Retention and legal-hold periods remain deployment-owned; the application does not yet perform
  an automatic coordinated purge across Task, Evidence, Audit, checkpoints, Artifacts, policy and
  RAG state.
- MCP, Kubernetes, cloud-provider templates, and zero-downtime deployment are outside Stage 17.1.

## Reproducible and controlled builds

The wheel metadata constrains dependency major versions, and CI rebuilds the distribution in a
clean Python 3.11 runner. The repository does not yet carry a fully resolved hash-locked runtime
dependency file. Consequently an isolated build requires an approved package index (or a
pre-populated wheel cache) and is not an offline supply-chain proof. Production release pipelines
should resolve and scan dependencies against a controlled mirror, build one immutable image, and
deploy that exact digest. This is a documented P2 limitation, not permission to bypass the build
or security gates.
