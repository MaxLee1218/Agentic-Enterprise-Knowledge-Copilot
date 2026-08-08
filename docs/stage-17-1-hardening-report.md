# Stage 17.1 Engineering Report

Date: 2026-08-08

This report records the production-security and MCP-readiness hardening performed after the Stage
18 readiness review. It does not claim that MCP is implemented. The final decision is intentionally
conservative: all code-level P0 findings were remediated, but the production deployment evidence
gate cannot pass on this host because no Docker daemon, real PostgreSQL test URL, or live RAG test
endpoint is available.

## 1. Baseline

The pre-change baseline was captured before implementation and is preserved by the earlier Stage
18 review and evaluation run `20260808T084409.666364Z-da0d4f78`.

| Check | Pre-change result |
|---|---|
| Ruff | PASS |
| Ruff format | PASS; 346 files already formatted |
| Mypy | PASS; 344 source files |
| Unit tests | 409 passed; 83% reported coverage |
| Integration + contract + smoke | 97 passed, 2 skipped, 1 warning |
| Evaluation | 30/30; average 94.5 ms, p50 97 ms, p95 147 ms |
| Documentation check | PASS |
| Architecture check | PASS |
| Actionlint | PASS |
| Python package | `--no-isolation` PASS; isolated build initially blocked by unavailable package-index network |
| Compose config | PASS with standalone `docker-compose` and explicit RAG image |
| Docker image/startup | NOT VERIFIED; Docker client present but daemon/socket unavailable |
| PostgreSQL integration | NOT VERIFIED; `TEST_POSTGRES_URL` absent |
| Live RAG integration | NOT VERIFIED; `RUN_LIVE_RAG_TESTS=1` absent |

The two baseline skips were environmental and were not introduced, hidden, changed to `xfail`, or
used to weaken a security test.

## 2. Audit Revalidation

The prior report's canonical issue count was **3 P0 findings**, not 4. Its third finding,
`S18-P0-03`, combined two separately described runtime symptoms: optional/default execution context
and approval omission at the executor boundary. The Stage 17.1 task restated those symptoms as four
numbered bullets, while the audit grouped them into three finding IDs. Revalidation therefore uses
the finding IDs and also tests both symptoms independently.

| Finding | Revalidation | Root cause and impact confirmed | Remediation status |
|---|---|---|---|
| S18-P0-01: no production caller identity boundary | CONFIRMED | API/CLI used fixed demo authority; production attribution and tenant binding were not trustworthy | REMEDIATED; signed trusted-header adapter, explicit demo mode, production rejection |
| S18-P0-02: persistence did not enforce tenant isolation | CONFIRMED | IDs and caller discipline could bypass tenant ownership at repository/query boundaries | REMEDIATED; mandatory tenant APIs, SQL tenant predicates, tenant columns, composite ownership constraints |
| S18-P0-03a: optional/default executor context | CONFIRMED | direct calls could synthesize demo permissions | REMEDIATED; one mandatory transport-neutral `ExecutionContext`, fail-closed validation |
| S18-P0-03b: approval optional at final enforcement point | CONFIRMED | direct callers could omit or replay an approval | REMEDIATED; executor recomputes requirement and validates exact approval binding |
| S18-P1-01: frozen design did not authorize MCP | CONFIRMED | implementing MCP would contradict the v1.1 authority | REMEDIATED for Stage 17.1 governance by ADR-007; v1.1 history remains unchanged and Stage 18 remains separate |
| S18-P1-02: registry lacked dynamic namespace/provenance/revocation | CONFIRMED | external-origin capabilities could collide or remain stale | REMEDIATED with protocol-neutral metadata, generation, atomic refresh, collision denial, and revoke |
| S18-P1-03: cancellation stopped waiting but not work | CONFIRMED | a running thread could continue and commit late output | REMEDIATED with truthful state, cooperative tokens, live invocation registry, late-result discard; adapters that cannot be interrupted are explicitly non-cancellable |
| S18-P1-04: audit/trace context did not span boundaries | CONFIRMED | thread/network work could lose correlation and tenant audit scope | REMEDIATED across async, copied thread context, HTTP headers, tenant audit, and parallel-isolation tests |
| S18-P1-05: deployment proved only demo topology | CONFIRMED | production startup and external dependencies were not demonstrated | CODE REMEDIATED, EVIDENCE OPEN; production manifest/config and real adapters exist, but full runtime validation is NOT VERIFIED on this host |

Each confirmed issue was traced through its affected class/function and exercised by a regression or
security invariant test. No finding was closed solely because this report says it was fixed.

## 3. Architecture Changes

**Identity.** Added transport-neutral `IdentityProvider` and `IdentityRequest` contracts. The demo
provider is limited to development/test and explicit CLI `--demo`. The production trusted-header
adapter verifies an HMAC-signed, time-bounded assertion over user, tenant, roles, scopes, supplier
scope, and purpose. Missing or invalid assertions return authentication failure; there is no demo
fallback.

**Tenant.** All tenant-owned task, state, plan, step, lease, evidence, approval, artifact, audit, and
checkpoint access now carries mandatory tenant ownership. SQL repositories filter by both tenant
and entity/task identifier. Child tables use composite tenant/task foreign keys. Audit rows remain
tenant-scoped but deliberately do not reference the task table: audit must be able to record denied
or early-lifecycle activity before a task row exists.

**Execution Context.** Added one mandatory `ExecutionContext` carrying task, trace, step, principal,
tenant, roles, scopes, data scope, purpose, authentication, deadline, approval, and cancellation.
Test helpers create contexts explicitly; production runtime never synthesizes one.

**Policy.** API, CLI, graph, and service paths converge on the same registry/executor. The executor
validates context, looks up the current registration, validates inputs, invokes the contextual
authorizer, and only then starts the tool.

**Approval.** Approval enforcement is now an executor invariant. The authorization decision binds
task, tenant, step, tool, schema version, normalized arguments/fingerprint, status, validity time,
and resolved identity. Resume re-enters current identity, tenant, policy, and approval checks.

**Registry.** The existing registry now has stable local and qualified names, immutable generic
origin/provenance metadata, collision denial, thread-safe snapshots, compare-and-swap generation,
atomic namespace replacement, reliable revocation, and explicit cancellation classification. No
protocol or MCP type was introduced.

**Cancellation.** Added `CancellationToken` and `InvocationCancellationRegistry`, with distinct
active, cancellation-requested, cancelled, and completed semantics. Cooperative analytics checks
the token. Timeout/shutdown requests cancellation, non-cancellable adapters report that limitation,
and late results cannot create evidence or artifacts.

**Observability.** First-class execution fields flow through API, service, graph, executor, copied
thread context, Knowledge HTTP correlation headers, persistence, evidence, and audit. Parallel
contexts are isolated. Audit stores hashes and identifiers rather than secrets/raw arguments.

**Deployment.** Production validation requires trusted identity, a sufficiently strong signing
secret, PostgreSQL persistence, migrated schema, checkpointing, non-SQLite read-only business DB,
real HTTP RAG, and a real LLM provider/credential. A separate production Compose manifest contains
one-shot migration, dependency ordering, readiness, non-secret placeholders, and graceful-stop
settings. The Database Tool now supports production PostgreSQL with a read-only transaction,
server-side statement timeout, portable approved templates, and a business-schema readiness probe.

## 4. Security Invariants

| Invariant | Enforcement and evidence |
|---|---|
| No identity -> no execution | API rejects missing/invalid signed identity; task service rejects unauthenticated identity; executor validates authenticated context |
| No tenant -> no tenant-owned repository access | Repository methods require non-empty tenant and SQL includes tenant predicates |
| No approval -> no protected execution | Executor authorizer denies approval-required registrations before tool invocation |
| Wrong tenant -> no data | Task, evidence, artifact, approval, audit, checkpoint, and update cross-tenant tests deny/not-found |
| Direct executor invocation -> still governed | Security tests call `ToolExecutor` directly and prove context, policy, tenant, and approval denial |
| Production -> no demo fallback | Settings reject demo provider and mock/SQLite critical providers; `DemoIdentityProvider` itself rejects production |
| Cancellation -> no false completion | Requested and completed cancellation states are distinct; timed-out/late output is discarded |
| Observability -> no secret expansion | Logs/audit use sanitized summaries, hashes, IDs, and scoped metadata; redaction regression tests pass |

## 5. Files Created

- `docker-compose.production.yml` — fail-closed production topology.
- `docs/adr/ADR-007-stage-18-mcp-readiness-boundary.md` — governance boundary between frozen v1.1,
  Stage 17.1, and future Stage 18.
- `docs/stage-17-1-hardening-report.md` — this report.
- `migrations/versions/20260808_0002_stage17_1_tenant_security.py` — tenant ownership migration.
- `src/copilot/security/identity.py` — demo and signed production identity adapters.
- `src/copilot/services/identity.py` — transport-neutral identity port/contracts.
- `src/copilot/services/execution.py` — mandatory execution context.
- `src/copilot/tools/cancellation.py` — cancellation token and invocation registry.
- `tests/execution_helpers.py` — explicit test-context factory.
- `tests/security/test_identity_boundary.py` — authentication and propagation invariants.
- `tests/security/test_tenant_isolation.py` — cross-tenant persistence invariants.
- `tests/security/test_executor_approval_boundary.py` — direct executor/policy/approval invariants.
- `tests/security/test_cancellation_semantics.py` — truthful cancellation and shutdown invariants.
- `tests/security/test_observability_boundaries.py` — async/thread/HTTP/isolation/redaction invariants.
- `evaluation/reports/runs/20260808T084409.666364Z-da0d4f78/{manifest.json,report.json,report.md}`
  — pre-change baseline artifact.
- `evaluation/reports/runs/20260808T102416.906429Z-da0d4f78/{manifest.json,report.json,report.md}`
  — intermediate post-hardening artifact.
- `evaluation/reports/runs/20260808T103329.122160Z-da0d4f78/{manifest.json,report.json,report.md}`
  — final post-hardening artifact.

## 6. Files Modified

Configuration, CI, deployment, and documentation:

```text
.env.example
.github/workflows/ci.yml
README.md
docker-compose.yml
docs/adr/README.md
docs/architecture.md
docs/deployment.md
docs/operations.md
docs/security-model.md
docs/troubleshooting.md
```

These changes separate demo and production modes, document the identity/tenant/executor/cancellation
boundaries, validate production Compose in CI, add security tests, and add operational response
procedures.

Evaluation and reusable scripts:

```text
evaluation/harness.py
evaluation/reports/latest.json
evaluation/reports/latest.md
scripts/check_architecture.py
scripts/inspect_task.py
scripts/smoke_analytics.py
scripts/smoke_llm_planner.py
```

These changes propagate explicit tenant/execution context, harden architecture checks, and preserve
final reproducible evaluation evidence.

Agent, API, bootstrap, and configuration:

```text
src/copilot/agent/graph.py
src/copilot/agent/runtime.py
src/copilot/agent/state.py
src/copilot/api/app.py
src/copilot/api/dependencies.py
src/copilot/bootstrap/api.py
src/copilot/bootstrap/cli.py
src/copilot/bootstrap/container.py
src/copilot/bootstrap/knowledge_cli.py
src/copilot/cli/main.py
src/copilot/config.py
```

These changes resolve request identity, construct and propagate mandatory execution context, inject
one security stack through the composition root, cancel on close, probe real dependencies, and
fail production startup on unsafe configuration.

Evidence, persistence, policy, and services:

```text
src/copilot/evidence/ledger.py
src/copilot/evidence/validators.py
src/copilot/persistence/approval_repository.py
src/copilot/persistence/artifact_repository.py
src/copilot/persistence/audit_repository.py
src/copilot/persistence/models.py
src/copilot/persistence/task_repository.py
src/copilot/policies/engine.py
src/copilot/policies/offline.py
src/copilot/services/approval_service.py
src/copilot/services/artifact_service.py
src/copilot/services/task_intake.py
src/copilot/services/task_service.py
src/copilot/services/workflows/models.py
src/copilot/services/workflows/ports.py
src/copilot/services/workflows/runner.py
```

These changes enforce tenant ownership at create/read/update/query boundaries, bind approvals,
scope audit/evidence access, and ensure resumed/recovered execution is reauthorized.

Tool framework and adapters:

```text
src/copilot/tools/analytics/tool.py
src/copilot/tools/base.py
src/copilot/tools/database/connection.py
src/copilot/tools/database/query_templates.py
src/copilot/tools/database/schema_registry.py
src/copilot/tools/database/tool.py
src/copilot/tools/exceptions.py
src/copilot/tools/executor.py
src/copilot/tools/knowledge/tool.py
src/copilot/tools/mock_supplier_quality.py
src/copilot/tools/registry.py
src/copilot/tools/reporting/composer.py
src/copilot/tools/reporting/tool.py
src/copilot/tools/runner.py
```

These changes make the executor the unavoidable policy/approval gate, add namespace/provenance and
revocation, propagate cancellation/correlation, block late commits, and provide the real
PostgreSQL read-only business-data path.

Contract and integration tests:

```text
tests/contract/test_tasks_api_contract.py
tests/integration/test_alembic_migrations.py
tests/integration/test_database_tool_executor.py
tests/integration/test_health.py
tests/integration/test_human_approval.py
tests/integration/test_langgraph_workflow.py
tests/integration/test_live_rag_api.py
tests/integration/test_llm_planning_workflow.py
tests/integration/test_natural_language_intake.py
tests/integration/test_postgres_persistence.py
tests/integration/test_stage13_task_api.py
tests/integration/test_supplier_quality_workflow.py
tests/integration/tools/test_analytics_database_flow.py
```

These tests pass explicit identity/tenant context, verify migration/readiness/approval behavior, and
extend the real PostgreSQL suite with cross-tenant coverage when its URL is supplied.

Smoke and unit tests:

```text
tests/smoke/test_cli.py
tests/smoke/test_human_approval.py
tests/unit/database/test_models_and_seed.py
tests/unit/database/test_sql_validator.py
tests/unit/database/test_tool.py
tests/unit/evidence/helpers.py
tests/unit/evidence/test_ledger.py
tests/unit/evidence/test_verifiers.py
tests/unit/knowledge/test_knowledge_tool.py
tests/unit/observability/test_foundations.py
tests/unit/test_approval_repository.py
tests/unit/test_artifact_repository.py
tests/unit/test_artifact_repository_reporting.py
tests/unit/test_artifact_service.py
tests/unit/test_config.py
tests/unit/test_workflow_repository.py
tests/unit/tool/test_executor.py
tests/unit/tool/test_registry.py
tests/unit/tools/analytics/helpers.py
tests/unit/tools/analytics/test_tool.py
tests/unit/tools/reporting/helpers.py
tests/unit/tools/reporting/test_reporting.py
```

These tests remove reliance on hidden demo context and add regression coverage for PostgreSQL
dialect compilation, registry concurrency/revocation, tenant APIs, audit metadata, cancellation,
and production configuration.

## 7. Database Migrations

- Revision: `20260808_0002`; parent: `20260807_0001`.
- Adds non-null `tenant_id` to task, child state, plan, result, lease, evidence, approval, approval
  history, artifact, and audit rows.
- Backfills from trusted task contract, approval, or audit ownership. Unknown legacy ownership is
  quarantined as `TENANT-LEGACY-UNSCOPED`; it is never assigned to a live tenant by guesswork.
- Adds tenant-first indexes including `(tenant_id, task_id)`, `(tenant_id, evidence_id)`,
  `(tenant_id, artifact_id)`, and `(tenant_id, approval_id)`.
- Adds composite unique/FK ownership for task children and approval history. Audit has tenant/task
  indexes but no task FK so pre-task denials and lifecycle events remain recordable.
- Fresh SQLite upgrade/downgrade: PASS.
- Existing Stage 17 SQLite upgrade, backfill, quarantine, FK/index/unique integrity: PASS.
- Real PostgreSQL upgrade/repository/restart test: NOT VERIFIED because `TEST_POSTGRES_URL` is not
  configured on this host.

## 8. Test Results

| Suite | Final result |
|---|---|
| Unit | 416 passed |
| Contract | 15 passed, 1 dependency deprecation warning |
| Integration | 65 passed, 2 skipped, 1 dependency deprecation warning |
| Smoke | 20 passed |
| Security | 18 passed, 1 dependency deprecation warning |
| Total executed pytest passes | 534 passed, 2 environmental skips |
| Coverage | 81.96% (reported as 82%); required 80% reached |
| Evaluation | 30/30; no regression; run `20260808T103329.122160Z-da0d4f78` |

The integration skips are explicit:

- `test_postgres_persistence.py`: `TEST_POSTGRES_URL is not configured`.
- `test_live_rag_api.py`: `RUN_LIVE_RAG_TESTS=1` and a live Enterprise RAG Engine are not configured.

Final evaluation latency was average 91.37 ms, p50 96 ms, p95 146 ms, compared with baseline
94.5/97/147 ms. The change therefore did not produce a material offline vertical-slice latency
regression. Safety leakage rates and missing-audit counts remained zero in the evaluation report.

Static checks also passed: Ruff, format (357 files), Mypy (354 source files), documentation,
architecture boundaries, and Actionlint.

## 9. Build / Deployment Validation

| Validation | Result | Evidence |
|---|---|---|
| Package build without isolation | PASS | sdist and wheel produced |
| Isolated package build | PASS | succeeded after approved package-index access supplied `setuptools>=68` |
| Wheel | PASS | 294 KiB; SHA-256 `ba33059b7a2b24debb37ecc40ca599d1ff522e8b0e011c9f7a73f15f0fe11256` |
| sdist | PASS | 227 KiB; SHA-256 `9d8a710cab0170ec3d352acdff51ae26355462146cd60b555639dda89bc02622` |
| Development Compose config | PASS | standalone `docker-compose ... config --quiet`, exit 0 |
| Production Compose config | PASS | required images/URLs/secrets supplied as non-secret validation placeholders, exit 0 |
| Docker daemon | UNAVAILABLE | `/var/run/docker.sock` missing |
| Docker image build | NOT VERIFIED | environment does not provide a Docker daemon |
| Compose startup/health/smoke/down | NOT VERIFIED | environment does not provide a Docker daemon; approved RAG image/runtime not available |
| PostgreSQL tenant integration | NOT VERIFIED | `TEST_POSTGRES_URL` absent; test skipped explicitly |
| Alembic SQLite fresh/existing migration | PASS | 2/2 targeted tests passed |
| Alembic PostgreSQL migration | NOT VERIFIED | no PostgreSQL endpoint |
| Liveness/readiness behavior | PASS in process | live remains 200 during dependency failure; ready becomes 503; business schema is probed when real adapter is selected |
| Live RAG readiness | NOT VERIFIED | live RAG test not enabled |
| Cancellation/resource shutdown | PASS in security tests | active invocation receives shutdown cancellation and resources close |
| Container SIGTERM/drain | NOT VERIFIED | requires a running container topology |

The production manifest is materially different from the development/demo manifest and contains no
committed credential defaults. Configuration parsing is not treated as proof that the topology can
start.

## 10. Remaining P2 / P3

- **P2 — dependency lock/controlled mirror:** dependency ranges are bounded and both builds pass,
  but the repository has no fully resolved hash lock. An isolated build still needs a controlled
  index or wheel cache. This is supply-chain hardening, not a bypass of runtime identity, tenant,
  policy, or approval controls.
- **P2 — compatibility repository names:** canonical SQL-backed audit repository names are used,
  while deprecated `InMemory*` aliases remain for compatibility. Removing aliases is not required
  for security enforcement.
- **P2 — tamper evidence/export:** audit storage is durable, scoped, and redacted but not yet
  cryptographically tamper-evident and tracing remains an internal correlation foundation rather
  than a claimed full OpenTelemetry deployment.
- **P2 — adapter cancellation limits:** synchronous HTTP/database/report adapters are declared
  non-cancellable; their deadlines and cleanup remain bounded, and late results are discarded.
  Replacing all drivers solely to gain forced interruption is outside this hardening stage.
- **P3 — generated dependency graph:** architecture checks pass, but no generated dependency/call
  graph artifact is published.
- **P3 — placeholder coverage display:** empty MCP scaffold modules remain visible as zero-statement
  modules; they contain no implementation and must not be interpreted as MCP coverage.

These items are not themselves Stage 18 blockers. The separate missing production runtime evidence
is listed in Section 13 and is blocking.

## 11. Stage 18 Readiness Gate

| Gate | Result | Evidence |
|---|---|---|
| 1. Registry / Executor | PASS | Single injected executor; mandatory context; namespace/origin/provenance; atomic refresh; generation; revoke/collision/concurrency tests |
| 2. Policy / Approval / Evidence / Audit | PASS | Direct-executor denial tests, exact approval binding, tenant evidence/audit, success/denial/timeout/cancel audit |
| 3. Identity / Tenant / Security | PASS | Signed production identity, no fallback, mandatory repository tenant filters, cross-tenant suite, redaction tests |
| 4. Checkpoint / Cancellation / Recovery | PASS | Scoped checkpoint IDs, resume reauthorization, bounded retry, truthful cancellation, late-commit prevention, shutdown cleanup tests |
| 5. Cross-network Observability | PASS | async/thread context propagation, RAG HTTP trace header, parallel isolation, audit correlation/redaction |
| 6. CI / Deployment / Operations | **FAIL** | static CI-equivalent checks/build/Compose config/docs pass, but real PostgreSQL, Compose startup, image build, health round trip, and SIGTERM are NOT VERIFIED |
| 7. Critical Path Demo / Mock | PASS | production validation rejects demo/mock/SQLite critical providers; production paths use trusted identity, PostgreSQL persistence/business DB, HTTP RAG, DeepSeek, and migrated schema |

Acceptance criteria audit:

| # | Result | Evidence summary |
|---:|---|---|
| 1 | PASS | Production default is not Demo Identity |
| 2 | PASS | Missing/invalid production identity provider fails closed |
| 3 | PASS | Protected tool calls require `ExecutionContext` |
| 4 | PASS | No implicit demo security context |
| 5 | PASS | Tenant-owned models have explicit ownership |
| 6 | PASS | Repository APIs require tenant scope |
| 7 | PASS | SQL reads/updates include tenant predicates |
| 8 | PASS | Cross-tenant task test |
| 9 | PASS | Cross-tenant evidence test |
| 10 | PASS | Cross-tenant artifact test |
| 11 | PASS | Cross-tenant approval test |
| 12 | PASS | Cross-tenant checkpoint test |
| 13 | PASS | Executor always invokes policy authorizer |
| 14 | PASS | Executor independently requires approval |
| 15 | PASS | Approval binds task/tenant/step/tool/arguments/status/time |
| 16 | PASS | Stable namespaces |
| 17 | PASS | Origin/provenance metadata |
| 18 | PASS | Atomic namespace refresh |
| 19 | PASS | Reliable revocation |
| 20 | PASS | Truthful cancellation states |
| 21 | PASS | Cooperative operation has real token path |
| 22 | PASS | Non-cancellable limitations explicit |
| 23 | PASS | Async/thread trace propagation |
| 24 | PASS | Knowledge HTTP correlation propagation |
| 25 | PASS | Parallel trace isolation |
| 26 | PASS | Logs/trace/audit secret redaction |
| 27 | PASS | Production/development config separation |
| 28 | **NOT VERIFIED** | Real PostgreSQL tenant test skipped |
| 29 | PASS | Alembic fresh/existing SQLite migration tests |
| 30 | **NOT VERIFIED (conditional)** | Docker daemon unavailable |
| 31 | **NOT VERIFIED** | Full Compose topology not started |
| 32 | PASS | Production startup validation tests |
| 33 | **NOT VERIFIED** | In-process shutdown passes; container SIGTERM not run |
| 34 | PASS | Ruff |
| 35 | PASS | Format check |
| 36 | PASS | Mypy |
| 37 | PASS | Unit tests |
| 38 | PASS | Contract tests |
| 39 | PASS WITH 2 ENVIRONMENTAL SKIPS | Integration command exits 0; criteria 28/live RAG remain separately unverified |
| 40 | PASS | Smoke tests |
| 41 | PASS | Security tests |
| 42 | PASS | Evaluation 30/30, no critical regression |
| 43 | PASS | Architecture check |
| 44 | PASS | Readiness audit re-executed in this section |
| 45 | **FAIL** | Gate 6 fails |
| 46 | PASS | Open P0 = 0 |
| 47 | **FAIL** | One blocking deployment-evidence P1 remains open |
| 48 | PASS | No MCP behavior implemented |

Because criteria 28, 31, 33, 45, and 47 are not satisfied, the stage cannot be declared complete.

## 12. Open P0

**Count: 0.**

All three canonical P0 findings, including both symptoms grouped under S18-P0-03, have code and
security-test evidence. No open P0 issue is being reclassified merely because the environment lacks
deployment dependencies.

## 13. Open Blocking P1

**Count: 1.**

### S17.1-BP1-01 — Production deployment evidence is incomplete

- **Blocker:** no Docker daemon/socket, `TEST_POSTGRES_URL`, approved RAG runtime/image, or complete
  production topology is available in this execution environment.
- **Root Cause:** an environment capability gap, not an application fallback: the Docker client
  cannot connect to `/var/run/docker.sock`; the PostgreSQL and live-RAG tests explicitly skip.
- **Affected Architecture:** persistence migration/repositories/checkpoints, container composition,
  health/readiness round trip, graceful SIGTERM/drain, and production operations.
- **Security Impact:** the code-level tenant and startup controls are covered locally, but there is
  no executable evidence on this host that PostgreSQL preserves them or that the complete topology
  starts, drains, and shuts down without cross-tenant or late-commit behavior.
- **Required Design Decision:** none. Do not weaken the gate or substitute SQLite/config parsing for
  the required evidence. Operations must provide an isolated Docker/PostgreSQL/RAG validation
  environment and approved non-production secrets/images.
- **Recommended Next Action:** run the existing PostgreSQL test with `TEST_POSTGRES_URL`, then build
  the immutable image and execute production Compose startup, authenticated smoke, readiness
  failure/recovery, active-call SIGTERM, and clean shutdown. Record image digest and logs without
  secrets; if all pass, rerun Gate 6 and close this finding.

## 14. MCP Boundary Confirmation

- No MCP Client implemented.
- No MCP Server implemented.
- No MCP protocol behavior implemented.
- No MCP SDK dependency introduced.
- Pre-existing empty MCP scaffold files remain placeholders only and were not expanded.
- Registry, identity, execution, cancellation, and observability changes are protocol-neutral
  foundations, not capability discovery, transport, sampling, elicitation, resources, prompts, or
  sessions.

## 15. Final Decision

```text
STAGE 17.1 NOT COMPLETE

P0: 0
Blocking P1: 1

STAGE 18 READINESS: NOT READY
```

The remaining blocker is production runtime evidence: real PostgreSQL tenant/migration/recovery,
full Compose startup and health, Docker image execution, and container SIGTERM/drain are NOT
VERIFIED. Stage 18 MCP implementation must not begin until those checks pass and the readiness audit
is updated with their evidence.
