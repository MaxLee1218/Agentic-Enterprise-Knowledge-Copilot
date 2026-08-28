# Asynchronous Runtime Operations

This runbook covers the Stage 19 PostgreSQL Queue v1 implementation selected by ADR-017. It is an
operations guide for the implemented API, dispatcher, Queue adapter, Worker, lease/fencing,
recovery scanner, and polling clients. It does not declare a deployment production-ready.

## Runtime topology and authority

```text
API / CLI
  -> one PostgreSQL transaction: Task + runtime + idempotency + PENDING dispatch
  -> 202 Accepted

one or more Worker processes
  -> bounded outbox dispatch (`FOR UPDATE SKIP LOCKED`)
  -> PostgreSQL Queue receive with visibility receipt
  -> authoritative Task/runtime reload
  -> existing `workflow_leases` acquire + heartbeat + monotonic fencing
  -> existing Graph / Policy / Registry / Tools / Evidence / Artifact / Verifier
  -> ACK or durable runtime retry
  -> bounded recovery scan (`FOR UPDATE SKIP LOCKED`)
```

PostgreSQL Task state is authoritative. Queue rows, receipts, Worker memory, and LangGraph
checkpoints never grant execution authority. The implementation is at-least-once. Duplicate or
expired receipts are normal reconciliation inputs, not proof of duplicate business effects.

Each Worker process embeds a bounded dispatcher and recovery pass before receiving work. Multiple
Workers are safe because dispatcher, Queue receive, recovery, and lease acquisition use database
locking/CAS rules. They share the same Copilot PostgreSQL and checkpoint store.

## Required configuration

- `PERSISTENCE_DATABASE_URL` must be PostgreSQL and migrated through Alembic head.
- `CHECKPOINT_ENABLED=true`; the official PostgreSQL checkpoint setup must have run.
- `QUEUE_PROVIDER=postgresql`; ADR-017 permits no alternative v1 provider.
- `TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS` must be at least the execution lease TTL.
- `EXECUTION_LEASE_TTL_SECONDS` must be at least three heartbeat intervals. Defaults are 60/15.
- `WORKER_CONCURRENCY` is a finite process-local slot count. Default is 4.
- `MAX_RUNTIME_RECOVERY_ATTEMPTS` defaults to 3; backoff is 5, 10, then 20 seconds.
- API and Worker must use the same approved business, Knowledge, model, policy, checkpoint, and
  Artifact configuration.

The Worker and API both write Artifact bytes. On the committed single-host Compose topology they
mount the same named volume. A multi-host rollout requires a reviewed shared/object Artifact store
and corresponding failure evidence; a host-local volume is insufficient.

## Start and verify

Run migrations exactly once before API or Worker startup:

```bash
python -m copilot.persistence.migrate
alembic current
python -m copilot.worker.health
```

Run the processes separately:

```bash
uvicorn copilot.bootstrap.api:app --host 0.0.0.0 --port 8000
python -m copilot.worker
```

The installed Worker command is equivalent:

```bash
enterprise-copilot-worker
```

For Compose:

```bash
docker compose up -d postgres migrate copilot-api copilot-worker
docker compose ps
docker compose logs --tail=200 copilot-worker
```

API readiness controls durable acceptance. Worker health succeeds only when PostgreSQL
persistence, PostgreSQL Queue, checkpoints, Artifact storage, and configured business dependencies
are ready. Liveness is not readiness.

## Stop, drain, and restart

Send `SIGTERM` or `SIGINT`. The Worker immediately stops claiming new deliveries and waits up to
`WORKER_SHUTDOWN_GRACE_SECONDS` for current work. After the grace period it signals cooperative
cancellation locally and exits; lease expiry/recovery fences any late process output.

```bash
docker compose stop -t 30 copilot-worker
docker compose up -d copilot-worker
```

Set the Compose stop timeout no shorter than the configured Worker grace. A hard kill is
recoverable after lease expiry but is not a normal rollout mechanism. Restarting the API does not
own or interrupt Worker execution. `WAITING_APPROVAL` Tasks have no Worker lease and need no
resident process.

During a rolling restart, keep at least one healthy Worker only if the Artifact store and all
dependencies are genuinely shared across the participating hosts. Otherwise drain the
single-host Worker before replacement. Queue/lease rules prevent concurrent authoritative commits;
they do not make host-local Artifact bytes shared.

## Inspect one Task

Use the tenant-scoped application command first. It returns IDs, statuses, generation, dispatch,
lease, checkpoint identity, recovery count, cancellation observation, approval status, and durable
successful step IDs without returning Task text, raw Tool output, or Artifact bytes.

```bash
enterprise-copilot-inspect-runtime TASK_ID
# source checkout equivalent
python scripts/inspect_runtime.py TASK_ID
```

The caller identity used for inspection must belong to the Task tenant and have read permission.
Do not use the Queue envelope as authorization.

The following PostgreSQL diagnostics are read-only. Always bind a tenant and, where applicable, a
Task ID; do not copy JSON payload columns into incident tickets.

```sql
SELECT runtime_status, count(*)
FROM workflow_task_runtime
WHERE tenant_id = :tenant_id
GROUP BY runtime_status;

SELECT status, count(*), min(available_at) AS oldest_available_at
FROM task_dispatches
WHERE tenant_id = :tenant_id
GROUP BY status;

SELECT count(*) AS queue_depth,
       min(available_at) AS oldest_available_at
FROM task_queue_deliveries
WHERE tenant_id = :tenant_id AND acked_at IS NULL;

SELECT r.task_id, r.runtime_status, r.execution_generation,
       r.recovery_attempt_count, r.retry_not_before, r.last_recovery_error,
       d.dispatch_id, d.status AS dispatch_status, d.attempt_count,
       l.worker_id, l.lease_id, l.fencing_token,
       l.heartbeat_at, l.expires_at, CURRENT_TIMESTAMP AS database_now
FROM workflow_task_runtime r
LEFT JOIN task_dispatches d
  ON d.tenant_id = r.tenant_id AND d.dispatch_id = r.current_dispatch_id
LEFT JOIN workflow_leases l
  ON l.tenant_id = r.tenant_id AND l.task_id = r.task_id
WHERE r.tenant_id = :tenant_id AND r.task_id = :task_id;
```

Never delete or update dispatch, delivery, lease, runtime, approval, or checkpoint rows to force
progress. The scanner is the only recovery writer. Preserve database and Artifact evidence before
repairing code or restoring a dependency.

## Failure response

| Symptom | Expected behavior | Operator action |
|---|---|---|
| PostgreSQL unavailable | API readiness fails; Worker stops safe commits and keeps polling | restore PostgreSQL; do not extend leases from local time |
| PENDING dispatch grows | dispatcher cannot publish into Queue rows | inspect Worker health/logs and DB locks; durable intent remains |
| Queue receipt expires | same dispatch is redelivered at least once | verify current lease/task; do not purge the duplicate |
| Lease expired | scanner re-arms the same dispatch/generation; takeover gets higher fencing | restore dependency and let scanner act |
| `WAITING_RETRY` | runtime process fault is delayed 5/10/20 seconds | inspect safe error code; do not conflate with Graph/Tool retry |
| recovery count reaches 3 | Task fails closed and dispatch is dead-lettered | investigate checkpoint/dependency/code; submit a new Task only after correction |
| `WAITING_APPROVAL` | runtime is `SUSPENDED`; no lease/Worker slot | resolve through the approval API or cancel; never enqueue manually |
| cancellation while running | Task becomes terminal; lease is removed; late commits are fenced | allow bounded I/O drain; verify Worker observation and no late Artifact |
| queue capacity exceeded | submission returns `429` with `Retry-After` and creates no partial Task | reduce backlog/add reviewed capacity; retry with the same idempotency key |
| Artifact metadata exists but bytes fail verification | final download fails closed | quarantine/restore the matching shared Artifact; never bless different bytes |

## Metrics and alerts

The frozen runtime metrics are:

- gauges: `task_queue_depth`, `task_queue_oldest_age_seconds`, `active_workers`,
  `active_execution_leases`, `waiting_approval_count`;
- counters: `lease_acquire_conflicts`, `lease_expirations`, `task_recoveries`,
  `recovery_failures`, `runtime_retry_count`;
- histograms: `task_queue_wait_seconds`, `task_execution_seconds`,
  `cancel_latency_seconds`.

Alert thresholds are deployment-owned. Review capacity, normal task duration, business dependency
SLAs, and recovery time objectives before setting them. Logs and metrics are process-local exports;
an operational deployment needs an external collector and dashboards.

## Safe rollback boundary

Stopping Workers is reversible and leaves durable work. Rolling application code backward is safe
only when the older code understands the current schema and async API contract. Alembic downgrade
may drop Queue/runtime data and must not be used as a requeue operation. Prefer a forward fix or a
tested full restore. Preserve matching PostgreSQL checkpoint and Artifact snapshots.

