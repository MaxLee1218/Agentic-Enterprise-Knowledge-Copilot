# Operations Runbook

This runbook operates the Stage 17 deployment. Commands assume the repository root and Docker
Compose unless noted otherwise.

## Service topology

| Service | Responsibility | Durable state |
|---|---|---|
| `copilot-api` | API, agent graph, tools, verification | None outside dependencies |
| `migrate` | Alembic plus official checkpoint setup | Exits after success |
| `rag-health` | Real Copilot Knowledge-client startup probe | Exits after success |
| `postgres` | Copilot internal state and checkpoints | `postgres-data` volume |
| `enterprise-rag-engine` | Independent knowledge retrieval | Owned by the RAG deployment |
| Artifact storage | Immutable report content | `artifact-data` volume |

The enterprise business database is external and read-only from the Database Tool. It is not the
Copilot persistence database.

## Startup

```bash
docker compose config
docker compose up -d postgres
docker compose run --rm migrate
docker compose up -d enterprise-rag-engine
docker compose up -d copilot-api
docker compose ps
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

`docker compose up -d` performs the same dependency order. Explicit steps are useful during an
incident or first deployment.

## Shutdown

```bash
docker compose stop copilot-api
docker compose down
```

Uvicorn handles SIGTERM and the FastAPI lifespan closes the checkpoint connection, repository
engine/pool, and other application resources. `docker compose down` preserves named volumes. Do
not add `--volumes` unless the target is an explicitly disposable development environment and the
data loss is intended.

## Health checks

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/health/live
curl -i http://127.0.0.1:8000/health/ready
docker compose ps
```

Liveness answers whether the process should be restarted. Readiness answers whether new governed
tasks can safely be accepted. A dependency outage can make readiness return 503 while liveness and
historical read endpoints remain useful.

## Observability and logs

```bash
docker compose logs --since=15m copilot-api
docker compose logs --since=15m migrate postgres enterprise-rag-engine
docker compose logs -f --tail=200 copilot-api
```

Correlate structured records using `task_id`, `trace_id`, `step_id`, `node_name`, `tool_name`,
`status`, `latency_ms`, `retry_count`, and typed error fields. Database startup/migration failures
log safe categories rather than URLs or passwords. Do not turn on payload or stack-trace logging in
production.

## Database operations

Check PostgreSQL without printing credentials:

```bash
docker compose exec postgres pg_isready -U copilot -d copilot
docker compose exec postgres psql -U copilot -d copilot -c 'select now();'
```

Monitor connections and tune `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, and
`DB_POOL_TIMEOUT_SECONDS` against API worker concurrency and PostgreSQL limits. Do not point the
Database Tool at Copilot persistence tables.

## Migration operations

```bash
docker compose run --rm migrate
docker compose run --rm copilot-api alembic current
docker compose run --rm copilot-api alembic history
```

Apply schema changes once as a deployment action before starting upgraded workers. API startup
validates required tables but never upgrades the schema. Review every downgrade before use;
production rollback may require restore or a forward fix.

## Artifact operations

List Artifact metadata through the task API:

```bash
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts
curl -OJ http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts/ARTIFACT_ID
docker compose exec copilot-api sh -c 'test -w /app/data/artifacts'
```

Do not edit Artifact files in place: metadata records size, checksum, and immutable filename. A
missing or changed file is an integrity incident, not a reason to update the checksum manually.

## RAG dependency

```bash
docker compose ps enterprise-rag-engine
docker compose logs --tail=200 enterprise-rag-engine
docker compose exec copilot-api python scripts/check_rag_health.py
```

RAG is independently deployed. Confirm service DNS, port, credentials, timeouts, and contract
compatibility. Mock Knowledge tests are not a live dependency check.

## Task and approval inspection

```bash
python scripts/inspect_task.py TASK_ID
python scripts/inspect_task.py TASK_ID --performance
curl http://127.0.0.1:8000/v1/tasks/TASK_ID
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/steps
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/evidence
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/approvals/APPROVAL_ID
```

A `NEEDS_APPROVAL` task is not stuck. Resolve it only through the approval API with an authorized
identity and an appropriate reason. Do not mutate approval rows directly.

## Smoke test

```bash
python scripts/check_rag_health.py
python scripts/smoke_agent.py --show-trace
```

Use approved synthetic data. Confirm final status, Evidence, Audit, Verification, Artifact metadata,
Artifact download/checksum, and a complete trace. A live RAG smoke is a separate gate from the
deterministic Mock evaluation.

## PostgreSQL backup and recovery

Production PostgreSQL must have an external backup strategy with retention, encryption, access
control, and restore testing. A portable baseline is:

```bash
pg_dump --format=custom --dbname="$PERSISTENCE_DATABASE_URL" --file=/secure/backup/copilot.dump
createdb copilot_restore_test
pg_restore --clean --if-exists --no-owner \
  --dbname=postgresql://RESTORE_USER@RESTORE_HOST/copilot_restore_test \
  /secure/backup/copilot.dump
```

Run commands from a secure operator environment so secrets and backups do not enter shell history,
logs, images, or Git. Managed-database snapshots are acceptable when restore is regularly tested.

## Artifact backup and recovery

Back up `ARTIFACT_DIR` or the `artifact-data` volume independently. PostgreSQL contains Artifact
metadata but not file content. For a consistent recovery point, coordinate the database and volume
snapshots; after restore, sample-download Artifacts and verify stored checksums. Restore permissions
for container UID/GID 10001.

## Task recovery

After an API restart, query the task by `task_id`. The relational state and PostgreSQL checkpoint
share that task identifier but remain separate persistence mechanisms. A completed task and a
pending approval must remain queryable; graph recovery must load the matching checkpoint. If they
disagree, stop retries and investigate rather than synthesizing new state.

## Common maintenance

```bash
docker compose ps
docker compose pull                   # when registry images are configured
docker compose build --pull
docker system df                      # diagnostic only
alembic current
python evaluation/run_eval.py --mode mock --seed 42 \
  --baseline evaluation/baselines/supplier_quality_v1.json --fail-on-regression
```

For a disposable local cleanup only, first verify the Compose project and then run
`docker compose down --volumes`. This irreversibly deletes local PostgreSQL and Artifact volumes;
never use it for a shared or production deployment.

## Incident procedure

1. Stop accepting new tasks if readiness is 503 or integrity/policy is uncertain.
2. Capture timestamps, image revision, migration revision, task/trace IDs, and safe component logs.
3. Classify whether the fault is API, PostgreSQL, RAG, business DB, Artifact, configuration, or
   policy/verification.
4. Preserve Audit, database, and Artifact evidence; never edit rows or files to hide a symptom.
5. Restore the dependency, roll back the application if schema-compatible, or deploy a forward fix.
6. Validate readiness, then run a controlled smoke task before reopening traffic.
7. Record scope, cause, recovery steps, and required follow-up without copying secrets or sensitive
   business payloads into the incident record.

Use [Troubleshooting](troubleshooting.md) for symptom-specific diagnostics.
