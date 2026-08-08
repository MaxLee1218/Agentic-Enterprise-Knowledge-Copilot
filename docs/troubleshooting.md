# Troubleshooting

Run diagnostics from a trusted operator environment and redact secrets before sharing output. Each
entry follows symptom, probable cause, diagnostic, and resolution.

## PostgreSQL

### Connection refused

- **Symptom:** API startup or readiness reports persistence unavailable; driver reports connection
  refused.
- **Probable cause:** PostgreSQL is down, starting, listening on another address, or blocked by the
  network.
- **Diagnostic:** `docker compose ps postgres`; `docker compose logs --tail=100 postgres`;
  `docker compose exec postgres pg_isready -U copilot -d copilot`.
- **Resolution:** Start PostgreSQL, correct the host/port/network route, and wait for health before
  rerunning migration or API startup. Inside Compose use service name `postgres`, not `localhost`.

### Authentication failed

- **Symptom:** PostgreSQL rejects the role/password.
- **Probable cause:** Secret mismatch, rotated credentials, wrong role, or malformed URL.
- **Diagnostic:** Compare secret references and PostgreSQL role configuration without printing the
  password; inspect safe PostgreSQL logs.
- **Resolution:** Correct or rotate the managed secret and restart the migration/API jobs. Do not
  place the URL in issue text or logs.

### Database does not exist

- **Symptom:** Connection succeeds to the server but reports an unknown database.
- **Probable cause:** Wrong database name or provisioning did not create the Copilot database.
- **Diagnostic:** Connect to the administrative database and run `\l` with an authorized operator
  role.
- **Resolution:** Provision the intended isolated database, grant the deployment/runtime roles, then
  run migrations. Do not reuse the enterprise business database.

### Migration not applied

- **Symptom:** API startup fails with `PERSISTENCE_SCHEMA_MISSING`, or readiness is 503.
- **Probable cause:** The migration job was skipped or failed.
- **Diagnostic:** `docker compose run --rm copilot-api alembic current` and
  `docker compose logs migrate`.
- **Resolution:** Fix the migration error and run `docker compose run --rm migrate` before restarting
  the API. Do not enable automatic schema creation in production.

### Migration revision mismatch

- **Symptom:** `alembic current` is not the application head or reports multiple/unrecognized heads.
- **Probable cause:** Wrong image/config pair, partially applied release, or diverged migration
  history.
- **Diagnostic:** `alembic current`; `alembic heads`; `alembic history --verbose` using the exact
  application image.
- **Resolution:** Stop rollout, match the image to its migrations, back up data, and apply an
  explicitly reviewed forward migration. Never stamp a production database merely to silence the
  mismatch.

### Connection pool exhausted

- **Symptom:** Requests time out waiting for a connection while PostgreSQL itself is reachable.
- **Probable cause:** Pool too small for worker concurrency, leaked/long transactions, or PostgreSQL
  connection limit saturation.
- **Diagnostic:** Inspect structured latency/error logs and `pg_stat_activity`; compare worker count
  with `DB_POOL_SIZE` plus `DB_MAX_OVERFLOW`.
- **Resolution:** Fix leaked/slow transactions first, bound workload, then tune pool and database
  limits together. Avoid unbounded overflow.

## RAG

### RAG unavailable

- **Symptom:** readiness is degraded/not ready and Knowledge steps fail to connect.
- **Probable cause:** Independent RAG service is down, DNS/port is wrong, or network policy blocks it.
- **Diagnostic:** `docker compose ps enterprise-rag-engine`; inspect RAG logs; run
  `docker compose exec copilot-api python scripts/check_rag_health.py`.
- **Resolution:** Restore RAG, correct `RAG_BASE_URL`, DNS, port, or routing. In Compose use
  `enterprise-rag-engine`, not `localhost`.

### RAG timeout

- **Symptom:** Knowledge attempts exhaust their timeout/retry budget.
- **Probable cause:** RAG overload, slow backend/index, network latency, or an unrealistic timeout.
- **Diagnostic:** Correlate Copilot tool latency/retry events with RAG request logs and health.
- **Resolution:** Repair RAG capacity/query performance, then adjust bounded timeout/retry settings
  only within the task execution budget.

### Invalid RAG response

- **Symptom:** The Knowledge adapter reports malformed or contract-invalid output.
- **Probable cause:** Incompatible RAG version, proxy error page, or corrupt response.
- **Diagnostic:** Check RAG version and safe response metadata; run its contract tests. Do not paste
  unrestricted document contents into logs.
- **Resolution:** deploy a contract-compatible RAG version or adapter fix; never bypass structured
  validation.

### RAG authentication failure

- **Symptom:** RAG returns 401/403.
- **Probable cause:** Missing/expired token, wrong audience/scope, or secret not injected.
- **Diagnostic:** Inspect secret reference, token metadata, and server authorization logs without
  printing the token.
- **Resolution:** rotate/reissue the approved credential with least privilege and restart the API.
  Never encode credentials in `RAG_BASE_URL`.

## API

### Container unhealthy

- **Symptom:** Compose reports `copilot-api` unhealthy.
- **Probable cause:** process failure or `/health/live` cannot be reached.
- **Diagnostic:** `docker compose ps`; `docker inspect` health output; `docker compose logs --tail=200
  copilot-api`.
- **Resolution:** fix the startup error, port binding, or image entrypoint. Dependency failure alone
  should affect readiness, not liveness.

### Startup failure

- **Symptom:** API exits before binding its port.
- **Probable cause:** invalid config, database/schema unavailable, import error, or filesystem denial.
- **Diagnostic:** inspect the first safe fatal log and migration status; run the same image command
  once interactively with the deployment environment.
- **Resolution:** correct the named dependency/configuration and redeploy. Do not add a blanket
  exception handler or retry loop around deterministic configuration errors.

### Configuration validation failure

- **Symptom:** startup reports a `SETTINGS` validation error.
- **Probable cause:** production uses demo identity, lacks a valid signing secret, uses
  debug/auto-schema, Mock providers, SQLite, loopback RAG, a blank required URL/credential, or an
  unsupported environment.
- **Diagnostic:** compare injected variable names and non-secret values with `.env.example` and
  `docs/deployment.md`.
- **Resolution:** supply a valid production profile. Do not weaken validation to accept demo values.

### Signed identity rejected

- **Symptom:** a protected API route returns HTTP 401 with `IDENTITY_RESOLUTION_FAILED`.
- **Probable cause:** the gateway assertion is missing, stale, signed with a different secret,
  modified after signing, missing roles/scopes/data scope, or affected by clock skew.
- **Diagnostic:** correlate request/trace ID with gateway Audit; compare secret version and clocks;
  verify the canonical header names and assertion age without printing header values or signature.
- **Resolution:** repair the gateway/header-stripping/signing path, synchronize clocks, or rotate the
  managed secret. Never fall back to Demo Identity or accept unsigned headers.

### Wrong tenant returns not found

- **Symptom:** a task, Evidence, Approval, Artifact, Audit, or checkpoint exists for one caller but
  is missing for another tenant.
- **Probable cause:** expected tenant isolation, or an upstream tenant mapping defect.
- **Diagnostic:** verify the authenticated tenant claim and query the authoritative repository with
  both tenant and object ID. Use hashes/IDs only; do not inspect another tenant's payload casually.
- **Resolution:** if the claim is wrong, fix the IdP/gateway mapping and revoke affected sessions. If
  a cross-tenant read ever succeeds, stop task acceptance and follow the P0 tenant-incident runbook.

### HTTP 500 response

- **Symptom:** a request returns the stable internal error contract.
- **Probable cause:** unhandled application/dependency defect associated with the request.
- **Diagnostic:** capture response correlation/task/trace ID, then query structured logs and Audit;
  verify database, RAG, business DB, and Artifact readiness.
- **Resolution:** repair the typed failure at its boundary and add a regression test. Do not expose
  raw exceptions, SQL, URLs, or stack traces to the caller.

## Artifact

### Permission denied

- **Symptom:** readiness or report generation says Artifact storage is not writable.
- **Probable cause:** volume owner/mode does not permit UID/GID 10001.
- **Diagnostic:** `docker compose exec copilot-api sh -c 'id; ls -ld /app/data/artifacts; test -w
  /app/data/artifacts'`.
- **Resolution:** change the deployment volume ownership/ACL for the non-root runtime user; do not run
  the application as root as a workaround.

### Artifact missing

- **Symptom:** metadata exists but download returns missing/integrity error.
- **Probable cause:** volume not restored/mounted, file manually deleted, or database/volume backups
  are from different times.
- **Diagnostic:** list the task's Artifact metadata, inspect the configured volume, and verify backup
  timestamps.
- **Resolution:** restore the matching Artifact backup or regenerate through an authorized task when
  policy permits. Preserve the incident evidence.

### Artifact volume not mounted

- **Symptom:** files disappear after restart or are written to the container layer.
- **Probable cause:** missing/wrong mount target.
- **Diagnostic:** `docker inspect` the API mounts and compare to `ARTIFACT_DIR`.
- **Resolution:** mount persistent storage at the exact configured path and restore its content and
  ownership.

### Checksum mismatch

- **Symptom:** Artifact read rejects content whose digest differs from metadata.
- **Probable cause:** file corruption, unauthorized modification, or inconsistent restore.
- **Diagnostic:** calculate the file checksum in a controlled environment and compare to immutable
  metadata/Audit.
- **Resolution:** quarantine the file, restore a verified matching copy, and investigate access.
  Never rewrite metadata to bless corrupted content.

## Agent

### Task appears stuck

- **Symptom:** task state stops changing.
- **Probable cause:** it is legitimately waiting for approval, an external call is in progress, a
  lease has not expired, or a worker failed.
- **Diagnostic:** inspect task, steps, approval, Audit, trace, lease, and checkpoint using
  `python scripts/inspect_task.py TASK_ID --performance`.
- **Resolution:** resolve a legitimate approval, restore the dependency, or use the existing bounded
  recovery path after lease expiry. Do not edit state rows.

### Cancellation remains requested

- **Symptom:** the task no longer accepts a result, but a Knowledge, Database, or Report worker is
  still draining.
- **Probable cause:** the adapter is truthfully classified non-cancellable; Python cannot forcibly
  stop its synchronous thread safely.
- **Diagnostic:** correlate task/tool Audit and trace, token request time, dependency timeout, and
  shutdown grace period. Confirm no Evidence or Artifact was committed from the late result.
- **Resolution:** wait for the bounded adapter timeout or terminate the process after the deployment
  grace policy. Repair an unbounded dependency timeout; do not relabel requested work as already
  interrupted.

### Checkpoint recovery failure

- **Symptom:** relational task state exists but graph resume cannot load its checkpoint.
- **Probable cause:** checkpoint setup missing, wrong PostgreSQL database/thread mapping, or
  inconsistent backup.
- **Diagnostic:** verify checkpoint migration setup and compare task ID/thread configuration with
  checkpoint tables using read-only operator queries.
- **Resolution:** restore the matching checkpoint/database backup or repair the adapter with a
  regression test. Never invent a checkpoint from final text.

### Task failed

- **Symptom:** task reaches the frozen failed state.
- **Probable cause:** typed tool, policy, validation, dependency, evidence, or artifact error.
- **Diagnostic:** use task/step endpoints, Audit, Evidence, and trace IDs to locate the first failure.
- **Resolution:** fix the root dependency/input or code defect and submit a new task/retry only when
  the frozen retry/idempotency rules permit.

### Approval pending

- **Symptom:** task is `NEEDS_APPROVAL` and no further tools run.
- **Probable cause:** intended human-in-the-loop gate.
- **Diagnostic:** query the task's approval resource and confirm action/scope/requester.
- **Resolution:** an authorized reviewer approves, rejects, or makes a permitted restrictive edit
  through the API. Restarting does not remove the pending record.

### Verification failed

- **Symptom:** tools completed but no successful final response is emitted.
- **Probable cause:** missing/invalid Evidence, citation/numeric inconsistency, policy issue, or
  Artifact integrity failure.
- **Diagnostic:** inspect Verification history, Evidence lineage, tool results, and Artifact checks.
- **Resolution:** repair the upstream evidence/calculation/report defect. Do not bypass the verifier
  or relabel hypotheses as facts.

## Docker

### Port conflict

- **Symptom:** container cannot publish 8000, 8001, or 5432.
- **Probable cause:** another local service already owns the host port.
- **Diagnostic:** `docker compose ps`; `lsof -nP -iTCP:8000 -sTCP:LISTEN` (repeat for the port).
- **Resolution:** stop the conflicting development service or change the host-side Compose port; do
  not change service-to-service container ports without updating health/config.

### Service name resolution failure

- **Symptom:** API cannot resolve `postgres` or `enterprise-rag-engine`.
- **Probable cause:** services are on different networks, wrong hostname, or API launched outside
  Compose with an internal DNS name.
- **Diagnostic:** inspect `docker compose config`; from the API container run `getent hosts` for the
  service name.
- **Resolution:** attach services to the same Compose network or use the environment-appropriate
  routable hostname.

### Volume permission failure

- **Symptom:** PostgreSQL or API repeatedly reports permission denied.
- **Probable cause:** bind/named volume ownership conflicts with its non-root runtime user.
- **Diagnostic:** inspect mounts and container UID/GID; inspect host ACLs without recursively changing
  an unverified path.
- **Resolution:** correct ownership on the exact deployment volume. Never apply recursive permission
  changes to a broad directory.

### Container restart loop

- **Symptom:** restart count continuously grows.
- **Probable cause:** deterministic startup/config failure, failed migration dependency, missing RAG
  image, or liveness failure.
- **Diagnostic:** `docker compose ps`; `docker inspect` restart/health state; component logs from the
  first failure.
- **Resolution:** stop the loop, repair config/migration/image/dependency, then start once and verify
  liveness/readiness before restoring the restart policy.
