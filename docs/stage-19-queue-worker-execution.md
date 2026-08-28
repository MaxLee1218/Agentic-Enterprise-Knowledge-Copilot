# Stage 19 — Queue + Worker Execution Engineering Report

**Decision: COMPLETE**

**Verified:** 2026-08-28

Stage 19 is complete against the frozen async-runtime contracts and accepted
[ADR-017](adr/ADR-017-postgresql-backed-queue-v1.md). This is an engineering-stage decision: it
does not claim distributed exactly-once execution, broker-independent high availability,
hard-kill cancellation, Accounts Payable production readiness, or whole-system production
readiness.

## A. Baseline Audit

Before Stage 19, `POST /v1/tasks` ran the complete LangGraph workflow in the API request and
normally returned synchronous `201`; there was no concrete Queue adapter, dispatcher, independent
Worker, heartbeat loop, or ordinary-execution recovery scanner. PostgreSQL Task persistence,
LangGraph checkpoints, approval recovery, durable step records, Artifact/Evidence repositories,
and the existing `workflow_leases` mechanism already existed.

The implementation evolved those components in place. It reuses the frozen Task state machine,
`WorkflowRepository`, checkpoint authority rules, approval/audit rows, tool registry/executor,
policy and Evidence boundaries, existing lease table, Alembic, and the two governed Supplier
Quality and Accounts Payable workflows. It does not add a parallel workflow, business state
machine, lease system, or outbox.

## B. Frozen Contract Compliance

| Frozen decision | Evidence |
|---|---|
| `TaskStatus` unchanged | Queue, dispatch, lease, recovery, and dead-letter states remain runtime models; no Task enum value was added. |
| Repository authoritative | Worker reloads tenant-qualified Task/runtime/dispatch state before acquiring authority and before terminal/no-op decisions. |
| Checkpoint subordinate | Recovery reconciles checkpoint identity/version with repository status, plan version, approval state, and durable successful steps. |
| Single active lease | Migration `0005` evolves `workflow_leases`; no second lease table exists. PostgreSQL concurrency has one acquisition winner. |
| Fencing enforced | Generation, lease identity, and monotonically increasing fencing token guard Task, step, Evidence, approval, Artifact metadata, and finalization writes. |
| At-least-once delivery | Delivery visibility, receipt-scoped ACK/NACK, redelivery, and terminal no-op handling are implemented; no exactly-once claim is made. |
| Retry ownership preserved | Queue/runtime retries recover delivery or process failure; Graph/tool retries remain owned by their existing policies. |

The Supplier Quality, Accounts Payable, Tool, Evidence, Approval, Artifact, and MCP business
contracts were not redesigned.

## C. Architecture Implemented

```text
POST /v1/tasks
  -> authenticate / authorize / validate / backpressure
  -> TaskSubmissionService
  -> one PostgreSQL transaction
       workflow_tasks + workflow_task_runtime
       task_dispatches(PENDING) + submission idempotency
  -> 202 Accepted

Independent Worker process
  -> RecoveryScanner (bounded, SKIP LOCKED)
  -> OutboxDispatcher (bounded, SKIP LOCKED)
  -> PostgreSQL Queue delivery (visibility + opaque receipt)
  -> TaskExecutionService
       reload authoritative Task
       acquire existing workflow_lease
       start heartbeat + runtime attempt
       reconcile Repository / checkpoint
       execute or resume LangGraph
       fenced Step / Evidence / Approval / Artifact / final writes
  -> durable dispatch disposition
  -> receipt ACK or NACK

GET /v1/tasks/{task_id}
  -> authoritative repository projection polled by CLI/frontend
```

API and Worker have separate composition roots. The API never starts the Worker loop. The Worker
combines bounded recovery, outbox publication, Queue consumption, lease/heartbeat, and Graph host
integration without moving business truth into Queue or process memory.

## D. API Migration

| Concern | Before | Stage 19 |
|---|---|---|
| `POST /v1/tasks` | synchronous Graph execution, normally `201` | validation plus atomic acceptance only, `202` |
| Response | completed/interrupted execution projection | `TaskSubmissionResponse` with Task/runtime state and status/artifact URLs |
| Long work | API request process | independent Worker |
| Approval resolution | service could resume inline | atomic decision plus resume dispatch; Worker resumes |
| Cancellation | synchronous service outcome | durable `202` request; Worker observes/fences late results |
| Client | assumed submit response contained execution outcome | navigates to Task detail and polls authoritative reads |

Submission idempotency binds tenant, caller, key, and canonical request fingerprint. Equivalent
retries return the existing Task; a mismatched payload is rejected. A configured outstanding-work
limit returns typed `429 TASK_QUEUE_CAPACITY_EXCEEDED` without partially saving a Task.

## E. Queue Implementation

Queue v1 is PostgreSQL 16+ and is isolated behind the frozen `TaskQueue` port. The existing
`task_dispatches` table stays the transactional outbox and immutable execution intent. Migration
`0006` adds `task_queue_deliveries`, containing only tenant-qualified dispatch identity,
availability/visibility timestamps, attempt count, receipt, and ACK time.

- Delivery is durable and at least once.
- Dispatcher and consumers coordinate through `FOR UPDATE SKIP LOCKED` with bounded batches.
- Enqueue/re-arm is idempotent for the same dispatch; consumers only receive authoritative
  receivable dispatch states.
- Receive advances visibility and creates a fresh opaque receipt. Only the exact current receipt
  may ACK or NACK; an expired unacknowledged receipt is redelivered.
- ACK acknowledges transport only. Dispatch outcome is committed separately after authoritative
  completion, approval suspension, retry scheduling, or terminal duplicate no-op.
- A failure while arming a delivery and changing `PENDING` to `ENQUEUED` rolls back the transaction;
  the durable PENDING intent is retried later.
- Queue health, eligible depth, and oldest eligible age use bounded PostgreSQL queries.

PostgreSQL Queue and Task persistence intentionally share an outage domain. An outage stops new
submission/dispatch/consumption, while already committed outbox intent survives and is published
after recovery.

## F. Worker Runtime

`python -m copilot.worker` starts a distinct application with its own database pool, Queue,
dispatcher, recovery scanner, Task execution service, heartbeat, tool/Graph dependencies, and
Worker identity. Startup fails clearly when async prerequisites or PostgreSQL are unavailable.

Each bounded cycle updates Queue metrics, scans recovery candidates, publishes due dispatches,
receives up to configured concurrency, and processes claims in bounded executor slots. A delivery
does not grant execution authority: the Worker must reload the Task and win the one authoritative
lease. Duplicate, terminal, cancelled, stale-generation, or approval-suspended deliveries become
durable no-ops.

`SIGTERM`/`SIGINT` stops new claims and begins a configured cooperative drain. Active work may
finish at a safe boundary; when the grace period elapses, local cancellation is signalled and
lease/fencing prevents a late authoritative commit. Worker health checks persistence, Queue, and
required execution dependencies independently of API readiness.

## G. Lease / Fencing Evidence

The P0 PostgreSQL suite uses independent connections and real row locks. It proves:

- two Workers racing for one tenant/Task yield exactly one lease winner;
- takeover is allowed at the database-time expiry boundary and increments the task-scoped fencing
  token without inventing a new execution generation;
- stale heartbeat, release, Task/step/Evidence/approval/Artifact, and final commits fail closed;
- the current Worker renews normally; authority loss cancels local invocation and discards late
  results;
- two recovery scanners and two dispatchers/consumers do not create two logical owners.

The combined P0 command in §R passed 18 tests against PostgreSQL 16.

## H. Crash Recovery Evidence

`tests/integration/test_worker_hard_kill.py` starts a real Worker subprocess, blocks it after a
durable successful step/checkpoint, sends a non-graceful kill, waits for lease expiry, and starts a
replacement Worker. The scanner re-arms the same dispatch/generation, the replacement obtains a
higher fencing token, reconciles repository and checkpoint state, and completes automatically.

Assertions prove that the already successful step is not rerun, one logical result is finalized,
and the killed Worker's token cannot commit. This is the required crash-takeover P0 evidence; it is
separate from graceful shutdown coverage.

## I. Approval Async Resume Evidence

When execution enters `WAITING_APPROVAL`, the runtime commits suspension, releases the execution
lease, ACKs the current delivery, and returns the Worker slot. No polling Worker is retained.

Approval resolution atomically records approval/history, transitions the authoritative Task,
increments execution generation, and creates a new resume dispatch bound to the checkpoint. It
returns `202` without calling the Graph. A Worker consumes the dispatch, validates approval and
checkpoint identity, and resumes without replaying durable successful steps. Duplicate resolution
remains conflict-safe; restart coverage proves the approval and resume intent survive process
loss. Cancelling while waiting revokes the pending approval and makes later resolution fail.

## J. Cancellation Evidence

| Scenario | Result |
|---|---|
| queued Task | durable cancellation makes later delivery an ACKed authoritative no-op |
| executing Task | Task becomes cancelled, lease is invalidated, heartbeat observes authority loss, and a late Tool result cannot publish Evidence/Artifact/final state |
| waiting approval | pending gate is revoked atomically, no resume dispatch is created, stale resolution returns conflict |
| duplicate request | same request is idempotent and leaves one terminal outcome |
| already terminal | cancellation cannot reopen or overwrite terminal truth |

Cancellation is cooperative at Worker, node, Tool invocation, Evidence/Artifact, and authoritative
commit boundaries. It does not claim to forcibly terminate arbitrary blocking external I/O.

## K. Idempotency Evidence

| Boundary | Implementation/evidence |
|---|---|
| submission | tenant/caller/key uniqueness plus canonical fingerprint; atomic Task/runtime/dispatch persistence |
| dispatch | tenant/Task/generation uniqueness; equivalent creation and Queue re-arm are idempotent |
| Queue | receipt-scoped ACK/NACK, visibility redelivery, stale receipt rejection |
| lease | one tenant/Task row; exact identity renewal/release; monotonic fencing takeover |
| steps | existing durable step identity and successful-step reuse, now under execution authority |
| Artifact | Artifact ID derives deterministically from the Tool idempotency key; atomic no-overwrite bytes plus durable metadata adoption after crash |
| finalization | immutable final result and fenced terminal compare-and-set; terminal redelivery is a no-op |

The Artifact crash test interrupts between final bytes and metadata, then starts with fresh Tool
state. Retry adopts the same bytes and produces exactly one metadata row/file. This closes the
process-memory idempotency gap without promising exactly-once external side effects.

## L. Failure Matrix

Only the statuses required by the frozen instruction are used.

| Failure | Status | Automated evidence |
|---|---|---|
| API dies before Task commit | TESTED | atomic submission rollback/idempotency and capacity-rejection tests leave no partial Task |
| API dies after Task commit | TESTED | committed PENDING intent remains dispatchable; controlled Queue arm failure later retries it |
| enqueue fails | TESTED | dispatcher transaction rolls back; subsequent pass publishes |
| duplicate enqueue | TESTED | idempotent re-arm plus one delivery identity |
| Worker dies before lease | TESTED | delivery visibility/consumer restart redelivery |
| Worker dies after lease | TESTED | real hard-kill, expiry, takeover, higher fence |
| Worker dies after successful step | TESTED | hard-kill checkpoint recovery does not repeat the step |
| Worker hangs | TESTED | expiry/recovery takeover and stale-Worker fencing |
| heartbeat failure | TESTED | authority loss propagates local cancellation and rejects late commit |
| lease expiry | TESTED | database-time boundary and scanner recovery |
| old Worker returns | TESTED | stale heartbeat/release/all authoritative writes rejected |
| duplicate Queue delivery | TESTED | concurrent receive plus duplicate delivery/no-op tests |
| checkpoint missing | TESTED | reconciliation allows only pristine CREATED work; otherwise fails closed |
| checkpoint stale | TESTED | version/plan/checkpoint mismatch fails closed |
| cancel queued | TESTED | durable cancellation plus later Queue no-op |
| cancel executing | TESTED | blocked Tool, cancellation observation, late Artifact/result fence |
| cancel approval | TESTED | approval revoked and stale resolution rejected |
| approval during restart | TESTED | durable approval/checkpoint/resume-dispatch recovery |
| approval double resolution | TESTED | one durable resolution; subsequent resolution conflicts |
| Artifact before crash | TESTED | deterministic bytes/ID adoption after process interruption |
| final commit + ACK lost | TESTED | completed dispatch redelivery is authoritative no-op; Tool not replayed |
| cross-tenant stale message | TESTED | forged tenant/dispatch envelope rejected before publication/execution |

## M. Security / Tenant Isolation

Runtime, dispatch, Queue delivery, lease, attempt, submission-idempotency, approval, Evidence, and
Artifact lookups remain tenant-qualified. Composite database constraints bind dispatch/delivery to
the same tenant and Task. The Worker treats Queue payloads as untrusted hints, reloads authority,
and rejects mismatched or stale envelopes before acquiring a lease.

Queue rows carry no authorization context, credentials, business records, raw prompts, Plan,
Evidence, or Artifact bytes. Logs use identifiers and typed errors, not payloads. The PostgreSQL
P0 suite includes a forged cross-tenant dispatch test; the complete security suite also passed as
part of the 769-test run.

## N. Observability

Structured runtime events include `task_accepted`, `dispatch_published`, `dispatch_received`,
`lease_acquired`, `lease_acquire_conflict`, `lease_heartbeat`, `lease_released`,
`worker_execution_start`, `worker_execution_end`, `duplicate_dispatch_ignored`,
`runtime_recovery_scan`, `runtime_recovery`, `runtime_recovery_failed`, `task_cancel_requested`,
`cancel_observed`, `worker_started`, `worker_drain_expired`, and `worker_stopped`.

Runtime metrics include Queue depth/oldest age, Queue wait, active Workers, active leases,
execution duration, lease conflicts/expiry, recoveries/failures, runtime retry count, waiting
approval count, and cancellation latency. Correlation fields include the available `tenant_id`,
`task_id`, `trace_id`, `dispatch_id`, `execution_generation`, `lease_id`, `fencing_token`, Worker
identity, runtime attempt, typed outcome, and error code. High-cardinality identifiers are logged,
not used as metric labels.

## O. Frontend Changes

The create page handles acceptance-only `202`, immediately navigates to the Task detail URL, and
does not interpret the submission response as completion. Task overview polling continues across
business and runtime activity, stops at terminal status, backs off on transient errors, and
surfaces durable failure/cancellation.

The UI distinguishes business status from runtime labels such as queued/ready, leased/running,
retrying/recovering, and approval suspension without extending `TaskStatus`. Approval resolution
and cancellation use their async `202` contracts. OpenAPI JSON and generated TypeScript declarations
were regenerated and checked for drift. Vitest and Playwright cover submit → detail → polling →
completion plus approval/cancellation projections.

## P. Database Migrations

- `20260826_0005_async_runtime_persistence.py` creates `task_dispatches`,
  `workflow_task_runtime`, `task_submission_idempotency`, and `task_runtime_attempts`; evolves
  `workflow_leases` in place; backfills legacy Tasks/leases; and provides a tested downgrade.
- `20260826_0006_postgresql_queue_v1.py` creates the subordinate PostgreSQL
  `task_queue_deliveries` transport table and due/visibility indexes with tenant-qualified
  constraints and a tested downgrade.

The migration head is `20260826_0006`. Neither migration changes the governed business Database
Tool schema. Fresh install, legacy upgrade/backfill, constraint, and downgrade paths pass.

## Q. Files Added / Modified

Added:

```text
docs/adr/ADR-017-postgresql-backed-queue-v1.md
docs/async-runtime-operations.md
docs/stage-19-queue-worker-execution.md
migrations/versions/20260826_0005_async_runtime_persistence.py
migrations/versions/20260826_0006_postgresql_queue_v1.py
scripts/inspect_runtime.py
src/copilot/bootstrap/runtime_cli.py
src/copilot/bootstrap/worker.py
src/copilot/persistence/async_runtime_repository.py
src/copilot/persistence/fencing.py
src/copilot/persistence/postgres_queue.py
src/copilot/persistence/postgres_recovery.py
src/copilot/services/execution_authority.py
src/copilot/services/task_execution.py
src/copilot/services/task_submission.py
src/copilot/tools/reporting/idempotency.py
src/copilot/worker/__init__.py
src/copilot/worker/__main__.py
src/copilot/worker/health.py
src/copilot/worker/runtime.py
tests/async_runtime_helpers.py
tests/integration/test_async_worker_api.py
tests/integration/test_postgres_async_runtime.py
tests/integration/test_postgres_queue_worker.py
tests/integration/test_worker_hard_kill.py
tests/unit/persistence/test_async_runtime_repository.py
tests/unit/worker/test_runtime.py
```

Modified:

```text
.env.example
.github/workflows/ci.yml
README.md
docker-compose.local-enterprise.yml
docker-compose.production.yml
docker-compose.yml
docs/adr/README.md
docs/api.md
docs/architecture.md
docs/async-runtime-architecture.md
docs/deployment.md
docs/frontend-audit.md
docs/operations.md
docs/task-lifecycle.md
docs/troubleshooting.md
frontend/openapi/openapi.json
frontend/src/api/generated/schema.d.ts
frontend/src/api/types.ts
frontend/src/pages/TaskCreatePage.test.tsx
frontend/src/pages/TaskCreatePage.tsx
frontend/src/pages/TaskOverviewPage.tsx
frontend/src/test/fixtures.ts
frontend/src/utils/status.test.tsx
frontend/src/utils/status.ts
frontend/tests/e2e/execution-console.spec.ts
frontend/tests/e2e/local-enterprise-live.spec.ts
pyproject.toml
scripts/check_architecture.py
src/copilot/agent/graph.py
src/copilot/agent/state.py
src/copilot/api/app.py
src/copilot/api/dependencies.py
src/copilot/api/error_handlers.py
src/copilot/api/mappers.py
src/copilot/api/routes/approvals.py
src/copilot/api/routes/tasks.py
src/copilot/api/schemas/approvals.py
src/copilot/api/schemas/tasks.py
src/copilot/bootstrap/api.py
src/copilot/bootstrap/cli.py
src/copilot/bootstrap/container.py
src/copilot/cli/main.py
src/copilot/config.py
src/copilot/contracts/errors.py
src/copilot/evidence/ledger.py
src/copilot/observability/instrumentation.py
src/copilot/observability/metrics.py
src/copilot/persistence/approval_repository.py
src/copilot/persistence/artifact_repository.py
src/copilot/persistence/models.py
src/copilot/persistence/task_repository.py
src/copilot/services/approval_service.py
src/copilot/services/async_runtime.py
src/copilot/services/observability.py
src/copilot/services/task_service.py
src/copilot/services/task_views.py
src/copilot/tools/mock_supplier_quality.py
src/copilot/tools/reporting/ap_tool.py
src/copilot/tools/reporting/tool.py
tests/contract/test_local_enterprise_compose_contract.py
tests/contract/test_production_ap_policy_contract.py
tests/contract/test_tasks_api_contract.py
tests/frontend_e2e_app.py
tests/integration/test_accounts_payable_stage9_api.py
tests/integration/test_alembic_migrations.py
tests/integration/test_human_approval.py
tests/integration/test_stage13_task_api.py
tests/security/test_identity_boundary.py
tests/smoke/test_cli.py
tests/unit/test_artifact_repository_reporting.py
tests/unit/test_cli_task_parser.py
```

## R. Test Results

| Command | Passed | Failed | Skipped | Reason/coverage |
|---|---:|---:|---:|---|
| `TEST_POSTGRES_URL=... pytest tests/integration/test_postgres_async_runtime.py tests/integration/test_postgres_queue_worker.py tests/integration/test_async_worker_api.py tests/integration/test_worker_hard_kill.py -q` | 18 | 0 | 0 | PostgreSQL Queue/lease/fence/recovery/API/AP/Supplier/cancel P0 |
| `.venv/bin/pytest -q` | 769 | 0 | 27 | complete configured suite; skips are provider/environment gates; 18 deprecation warnings |
| reporting/Artifact focused suite | 29 | 0 | 0 | deterministic Artifact crash adoption and reporting regression |
| `.venv/bin/ruff check .` | n/a | 0 | n/a | passed |
| `.venv/bin/ruff format --check .` | 485 files | 0 | n/a | passed |
| `.venv/bin/mypy` | 475 files | 0 | n/a | passed |
| frontend Vitest | 32 | 0 | 0 | eight test files |
| Playwright execution-console E2E with installed Chrome | 6 | 0 | 0 | async submit/poll/approval/cancel UX |
| frontend TypeScript, ESLint, Prettier, Vite build | 4 checks | 0 | n/a | all passed |
| OpenAPI export and generated TypeScript byte comparison | 2 checks | 0 | n/a | no generated-contract drift |
| Supplier evaluation with regression baseline | 30 | 0 | 0 | mock seed 42 |
| Accounts Payable evaluation with regression baseline | 25 | 0 | 0 | mock seed 42 |
| MCP interoperability and safety evaluation | 25 | 0 | 0 | 13 interoperability + 12 safety tests |
| development/local-enterprise/production `docker compose ... config --quiet` | 3 | 0 | n/a | all deployment shapes parse |
| backend and frontend `docker build` | 2 images | 0 | n/a | both current-source images built |
| `scripts/check_docs.py` and `scripts/check_architecture.py` | 2 checks | 0 | n/a | documentation and dependency boundaries passed |

The localhost-binding tests and Docker engine checks were run with the required sandbox permission.
The MCP command also set `PYTHONPATH` to the current worktree to avoid a stale editable-package
installation in the virtual environment.

## S. Remaining Limitations

- Artifact bytes still require storage shared by every Worker (or an object-store adapter). A
  process-local filesystem is not a horizontally safe production Artifact backend.
- Delivery and commits are at least once and idempotent at internal boundaries; there is no
  distributed exactly-once guarantee for arbitrary external systems.
- Cancellation is cooperative. An already-issued, non-cancellable external operation may run to
  completion, but its late authoritative result is fenced.
- PostgreSQL Queue and Task persistence share an outage domain. Multi-region/broker-independent
  HA, failover drills, production load/soak evidence, autovacuum tuning, and capacity validation
  remain deployment work.
- Queue/Worker concurrency is bounded and configured, but this stage is not a production sizing or
  SLO certification.
- Live LLM, enterprise RAG, identity provider, business database, external object storage, and
  production policy-bundle behavior remain provider/environment-specific validation.
- Accounts Payable retains its previously documented Stage 12 `NOT READY` production conclusion;
  completing this runtime closes only the async execution gap.
- Stage I deterministic failure gates are covered; extended chaos/load/soak work and the Stage J
  production-readiness review remain incomplete. Therefore this report must not be used as a
  whole-system production-readiness approval.

## T. Final Decision

All 20 Stage 19 engineering acceptance gates pass:

| Gate | Result | Evidence |
|---:|---|---|
| 1 Frozen contracts | PASS | §B |
| 2 API truly asynchronous | PASS | `202`, Graph absent from request path, async API E2E |
| 3 Durable dispatch | PASS | atomic Task/runtime/PENDING dispatch plus retry test |
| 4 Real Queue | PASS | PostgreSQL adapter ACK/NACK/visibility/restart integration tests |
| 5 Independent Worker | PASS | Worker completes after API request/process path ends |
| 6 Single lease | PASS | real PostgreSQL one-winner race |
| 7 Fencing | PASS | stale authoritative commits rejected |
| 8 Heartbeat | PASS | renewal, loss, expiry, late-result tests |
| 9 Crash recovery | PASS | non-graceful subprocess kill and automatic takeover |
| 10 Duplicate delivery | PASS | concurrent/duplicate delivery yields one logical execution |
| 11 Approval suspension | PASS | no lease and Worker slot released |
| 12 Approval redispatch | PASS | no inline Graph; Worker checkpoint resume |
| 13 Cancellation | PASS | queued/executing/approval/late-result scenarios |
| 14 ACK loss | PASS | terminal redelivery no-op |
| 15 Artifact idempotency | PASS | deterministic crash-boundary adoption |
| 16 Tenant isolation | PASS | forged envelope and full security suite |
| 17 Frontend | PASS | Vitest + Playwright async flow |
| 18 Existing use cases | PASS | Supplier 30/30 and AP 25/25 baselines; async E2Es |
| 19 MCP regression | PASS | 13 interoperability + 12 safety tests |
| 20 Engineering gates | PASS | Python/frontend/static/docs/architecture/OpenAPI/Docker/Compose |

Accordingly, **Stage 19 Queue + Worker Execution is COMPLETE** for the frozen engineering scope.
The limitations in §S remain explicit release inputs and prevent a broader production-ready claim.
