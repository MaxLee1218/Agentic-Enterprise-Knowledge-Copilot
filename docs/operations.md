# Operations Runbook

This runbook operates the shared Supplier Quality and Accounts Payable deployment foundation.
Commands assume the repository root and Docker Compose unless noted otherwise. Plain
`docker compose` refers to the development topology; production commands must pass
`-f docker-compose.production.yml`.

## Service topology

| Service | Responsibility | Durable state |
|---|---|---|
| `copilot-api` | API, agent graph, tools, verification | None outside dependencies |
| `migrate` | Alembic plus official checkpoint setup | Exits after success |
| `rag-health` | Real Copilot Knowledge-client startup probe | Exits after success |
| `postgres` | Copilot internal state and checkpoints | `postgres-data` volume |
| `enterprise-rag-engine` | Independent knowledge retrieval | Owned by the RAG deployment |
| Artifact storage | Immutable report content | `artifact-data` volume |
| AP policy mounts | Approved bundle plus immutable published snapshot | Operator-owned read-only paths |

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
engine/pool, HTTP/model/database clients, and other application resources. Shutdown first signals
all active tool cancellation tokens. Cooperatively cancellable work stops at a checkpoint;
non-cancellable synchronous work remains `CANCELLATION_REQUESTED`, is allowed to drain within the
platform grace period, and its late output is discarded. `docker compose down` preserves named volumes. Do
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

## Identity and authentication operations

Production accepts only signed identity assertions from the approved gateway. The gateway must
remove all inbound `X-Copilot-*` identity headers, authenticate the caller, authorize tenant/role
claims, add a current timestamp, and sign the canonical assertion. Monitor stable
`IDENTITY_RESOLUTION_FAILED`/HTTP 401 rates without logging headers or signatures.

For an identity-provider incident:

1. stop new task acceptance at the gateway if assertion integrity is uncertain;
2. preserve safe request/trace IDs and gateway audit records;
3. verify clock synchronization, secret version, assertion age, tenant mapping, and configured
   provider without printing the secret;
4. rotate the signing secret using the deployment secret mechanism and restart affected instances;
5. run cross-tenant and least-privilege smoke checks before reopening traffic.

Never switch production to `IDENTITY_PROVIDER=demo` as an availability workaround.

## Cancellation inspection

Task cancellation signals every active invocation registered for that task and revokes pending
approvals. `CANCELLED` at the task boundary means no result can be committed. For synchronous
Knowledge, Database, or Report work, the underlying thread may still be draining while its token is
`CANCELLATION_REQUESTED`; do not report forced interruption. Correlate task Audit with
`tool_call_id`, trace, adapter timeout, and shutdown time. Repeated cancellation is idempotent and a
completed task cannot be relabelled cancelled.

## Audit and tenant-incident lookup

Audit, logs, and traces have different purposes: Audit answers who/what/why and policy outcome;
logs diagnose component behavior; traces show the request path. Search by tenant plus task/trace ID
and never by an unscoped task identifier in direct database diagnostics. Audit stores identity and
scope summaries only after redaction, argument hashes instead of arguments, and tool
origin/provenance instead of executable metadata.

For suspected cross-tenant access, stop affected task acceptance, preserve database and gateway
audit, identify the tenant-qualified task/checkpoint key, query every repository table using both
tenant and task, and compare the composite foreign keys and migration revision. Do not copy another
tenant's payload into an incident ticket. Treat any confirmed cross-tenant row/read as a P0
security incident.

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

## Accounts Payable policy snapshot

Production AP startup requires `AP_POLICY_REQUIRE_PUBLISHED_SNAPSHOT=true` and separate read-only
mounts for the approved bundle and its published snapshot root. Publication is an owner-controlled
deployment job, not an API startup side effect. Validate the exact tenant before activation:

```bash
enterprise-copilot-publish-ap-policy \
  --bundle-dir /approved/config/accounts-payable-policy \
  --output-dir /approved/state/accounts-payable-policy-snapshots \
  --tenant-id APPROVED_TENANT \
  --index-revision RELEASE_REVISION
```

Retain the prior immutable snapshot generation until rollback and retention review complete. If
the bundle, current pointer, snapshot identity, document payload or rule manifest differs, keep the
API unavailable for AP work; do not regenerate checksums or fall back to the embedded demo bundle.

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

Both Copilot PostgreSQL and the separately governed enterprise business database must have external
backup strategies with retention, encryption, access control, and restore testing. A portable
Copilot baseline is:

```bash
pg_dump --format=custom --dbname="$PERSISTENCE_DATABASE_URL" --file=/secure/backup/copilot.dump
createdb copilot_restore_test
pg_restore --clean --if-exists --no-owner \
  --dbname=postgresql://RESTORE_USER@RESTORE_HOST/copilot_restore_test \
  /secure/backup/copilot.dump
```

Run commands from a secure operator environment so secrets and backups do not enter shell history,
logs, images, or Git. Managed-database snapshots are acceptable when restore is regularly tested.
Use the business database's approved backup identity and restore into a separate isolated database;
the runtime Database Tool credential must remain SELECT-only and is not a backup credential.

## Artifact backup and recovery

Back up `ARTIFACT_DIR` or the `artifact-data` volume independently. PostgreSQL contains Artifact
metadata but not file content. For a consistent recovery point, coordinate the database and volume
snapshots; after restore, sample-download Artifacts and verify stored checksums. Restore permissions
for container UID/GID 10001.

Back up the approved AP policy bundle and every retained immutable published generation separately.
After restore, run the same bundle/snapshot identity verification used at API startup. RAG-owned
indexes have their own backup authority and must not be inferred from Copilot PostgreSQL.

## Retention and legal hold

Before a production rollout, the data owner must approve explicit periods and deletion owners for
Task/Contract/Plan/Result, Evidence, Approval, Audit, checkpoint, Artifact, policy-snapshot and RAG
state. Audit and legal-hold obligations may require different periods from report content. The
current runtime retains these records and does not provide a coordinated automatic purge, so an
approved external procedure and a tested cross-store deletion reconciliation are release blockers,
not defaults to invent at deployment time.

## Task recovery

After an API restart, query the task by `task_id`. The relational state and PostgreSQL checkpoint
share that task identifier but remain separate persistence mechanisms. A completed task and a
pending approval must remain queryable; graph recovery must load the matching checkpoint. If they
disagree, stop retries and investigate rather than synthesizing new state.

The implemented system has no automatic recovery scanner or background Worker takeover. Ordinary
`engine.resume` is a lower-level controlled primitive; approval resolution is a separate
checkpoint-resume path. Do not report either as deployed automatic crash recovery.

## Future async runtime operations boundary

The future operational contract is frozen in
[`async-runtime-architecture.md`](async-runtime-architecture.md), but no Queue, dispatcher,
Worker daemon, heartbeat loop, or scanner is currently operated. The initial validated defaults
for the future implementation are a 15-second heartbeat, 60-second lease TTL, takeover at
`database_now >= expires_at`, and three runtime recovery attempts. They are configurable
operational values and do not change Tool retry budgets.

When that runtime is implemented, operators must distinguish:

- PENDING/RETRY_SCHEDULED dispatch publication failure;
- duplicate broker delivery with a healthy lease (normal no-op);
- expired execution lease eligible for fenced takeover;
- unresolved `WAITING_APPROVAL` (not recovery eligible);
- due `WAITING_RETRY` runtime failure;
- fail-closed checkpoint mismatch;
- poison Task whose runtime recovery budget is exhausted.

The RecoveryScanner may scan only READY/orphaned dispatch, expired lease, due runtime retry, and
orphan outbox candidates. It must exclude terminal Tasks, unresolved approval waits, and valid
leases. Operators must not manually delete lease or checkpoint rows to force progress. A lost
heartbeat because PostgreSQL is unavailable means the Worker has no safe commit authority; it
must stop committing and wait for database recovery/takeover.

Required operational views/alerts after implementation are Queue depth/oldest age, active Workers
and leases, acquire conflicts, lease expirations, recoveries/failures, runtime retries, Queue wait,
active execution, approval wait, total wall-clock duration, and cancellation latency. Heartbeat
success is a bounded metric rather than a per-beat audit/log stream. Runtime logs contain IDs and
safe error codes, never credentials, Queue authorization payloads, prompts, rows, or Artifact
bytes.

## Common maintenance

```bash
docker compose ps
docker compose pull                   # when registry images are configured
docker compose build --pull
docker system df                      # diagnostic only
alembic current
python evaluation/run_eval.py --mode mock --seed 42 \
  --baseline evaluation/baselines/supplier_quality_v1.json --fail-on-regression
python evaluation/run_eval.py --dataset evaluation/datasets/accounts_payable_v1.jsonl \
  --mode mock --seed 42 --baseline evaluation/baselines/accounts_payable_v1.json \
  --fail-on-regression
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
