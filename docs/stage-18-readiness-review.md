# Stage 18 Readiness Review

## 1. Decision

```text
Stage 18 Readiness: NOT READY
Final Conclusion: STAGE 18 NOT COMPLETE
```

Audit date: 2026-08-07  
Audited branch: `main` at `7bad10abc4903623ba07db9d933607e09863f8fb`, including the existing dirty working tree  
Audit scope: repository architecture, frozen design, contracts, API/CLI, services, graph runtime,
registry, executor, policy, approval, identity, tenant boundaries, evidence, audit, observability,
cancellation, persistence, migrations, deployment, CI, tests, evaluation, and MCP placeholders

Stage 18 implementation is stopped. Three P0 findings and multiple blocking P1 findings prevent a
remote protocol boundary from being added without weakening identity, tenant, authorization,
cancellation, audit, and design-governance guarantees. This review does not modify MCP code or
claim MCP behavior exists.

The green Stage 0–17 tests do not change this decision: they validate the frozen offline/demo
Supplier Quality v1.1 scenario. They do not exercise production authentication, cross-tenant
repository enforcement, real PostgreSQL in this environment, network trace propagation, real MCP
protocol behavior, or cancellation of a running external operation.

## 2. Frozen-design conflict

The current frozen v1.1 authority explicitly excludes MCP client/server interoperability:

- `docs/design/business_scope.md:88` makes MCP an out-of-scope future phase.
- `docs/design/design_baseline.md:13` prohibits adding MCP behavior to v1.1.
- `docs/design/design_baseline.md:92` identifies MCP paths as future boundaries only.
- `docs/design/design_baseline.md:112-117` requires a new-version design review before any MCP
  behavior and forbids treating empty scaffolds as implemented.
- `docs/architecture.md:368` requires design review for new MCP behavior.
- `docs/superpowers/specs/2026-07-19-mcp-dual-role-design.md:5` is approved only for
  implementation planning. Its acceptance criteria at lines 294–299 intentionally describe a
  zero-byte scaffold, not a production implementation.

No accepted Stage 18 frozen baseline or MCP protocol ADR exists. Implementing the requested
protocol revision, contracts, capabilities, storage, authorization, or exported APIs now would
contradict the frozen design authority. This is independently sufficient to stop implementation.

## 3. Current architecture map

The implemented Supplier Quality path is substantially layered:

```text
FastAPI / CLI
  -> NaturalLanguageTaskService / bootstrap composition
  -> Task intake and trusted task context
  -> LangGraph workflow engine and deterministic node runtime
  -> planning and plan validation
  -> permission, data-access, risk, and approval policy
  -> approval service and persisted checkpoint
  -> ToolExecutor
       -> ToolRegistry
       -> tool authorizer
       -> Knowledge / Database / Analytics / Report tool
       -> output guard
       -> EvidenceLedger
       -> tool audit and observability
  -> evidence aggregation
  -> report artifact
  -> verifier
  -> task, approval, evidence, artifact, audit, and checkpoint repositories
```

Repository scans and architecture tests found no implemented API-to-tool, graph-node-to-database,
graph-node-to-RAG, report-to-database, or MCP-to-business-tool direct call path. The main graph
runtime reaches tools through the existing `ToolExecutor`, and business packages do not import an
MCP SDK.

That favorable dependency direction is not sufficient for Stage 18 because the unified entry point
does not itself enforce all required security invariants. In particular, execution context is
optional and approval-required determination occurs in the graph policy path rather than being an
unavoidable executor invariant. A future protocol adapter could therefore call the nominally
unified executor while still losing caller context or bypassing a required approval.

## 4. Readiness matrix

| Area | Status | Evidence | Risk | Stage 18 impact |
| --- | --- | --- | --- | --- |
| Tool Registry | FAIL | `registry.py:18-39` has register/unregister/get/list/contains and locking, but rejects dots, defaults to the four frozen `CapabilityName` values, and has no origin, provenance, revision, generation, or atomic capability-set refresh | P1 | Cannot safely register/revoke `server_name.tool_name` or invalidate stale plans/handles |
| Tool Executor | FAIL | `executor.py:93-99,207-246` makes `security_context` optional; `executor.py:281-305` synthesizes demo defaults | P0 | Remote invocations can enter without an authenticated tenant/user/scope/origin context |
| Policy | FAIL | Current permissions and data-access checks cover v1.1, but have no server identity, connection, MCP scope, capability origin, or resource mapping | P1 | No governed mapping from remote authorization to the existing policy engine |
| Approval | FAIL | v1.1 approval records bind task/step/tool/schema/arguments and support resume, but `offline.py:141` validates an approval only when an ID is supplied | P0 | A direct executor caller can omit the ID; approval is not an unavoidable executor invariant |
| Identity | FAIL | `api/dependencies.py:30-38` always returns a server-owned Demo Identity; README and security/deployment docs state no production IAM/SSO adapter exists | P0 | A remote client cannot be authenticated as a real principal and must not inherit demo privileges |
| Tenant isolation | FAIL | Persistence models use globally keyed IDs and payload JSON without first-class tenant keys; repository reads do not require tenant context; `task_service.py:475` treats a missing contract as a tenant match | P0 | Tenant enforcement is service-convention based rather than guaranteed at every data boundary |
| Evidence | FAIL | The ledger is immutable/task-scoped on normal paths, but `ledger.py:266` permits an unscoped ID lookup and there is no MCP origin/provenance contract | P1 | MCP resources/results cannot yet prove server/session/protocol provenance or repository tenant scope |
| Audit | FAIL | Tool/workflow audit is append-only and redacted, but `audit_repository.py:58,123` exposes global list operations; tool records omit tenant, trace, approval, scope, origin, server, and session | P1 | Cross-network authorization and provenance cannot be reconstructed or tenant-scoped reliably |
| Observability | FAIL | Local tracing/metrics exist, but ADR-005 declares them process-local with no exporter; thread runner does not explicitly propagate `ContextVar` context | P1 | Trace continuity is not proven across thread, async, process, or network boundaries |
| Checkpoint | PASS | Task state, retry/replan, approval resume, SQL-backed checkpoints, leases, and restart recovery have unit/integration coverage | P2 | A usable task-recovery base exists; MCP session recovery must remain a separate lifecycle |
| Cancellation | FAIL | `runner.py:34` calls `future.cancel()` after timeout, which cannot stop an already-running Python thread; README explicitly records no forced interruption | P1 | MCP cancel/disconnect/shutdown cannot be mapped to actual in-flight operation cancellation |
| Persistence | FAIL | SQLAlchemy/Alembic SQLite/PostgreSQL abstractions exist, but tenant fields/query constraints are absent and PostgreSQL was skipped locally | P0 | New remote state would amplify an unproven isolation boundary |
| Security | FAIL | Prompt-injection, SQL validation, output redaction, error normalization, and data policies exist; production authentication and boundary-wide tenant enforcement do not | P0 | External exposure would convert demo assumptions into an authorization and data-isolation risk |
| CI | PASS | Ruff, format, mypy, unit, integration/contract/smoke, evaluation, docs/architecture checks, PostgreSQL job, and image build are configured | P2 | Existing CI is a solid base, but has no MCP protocol/security suites and local actionlint/PostgreSQL/container execution was incomplete |
| Deployment | FAIL | Dockerfile is non-root with health check; Compose validates, but it runs `APP_ENV=development`, mock DB/LLM, demo credentials, and no production identity adapter | P1 | A buildable demo topology is not a deployable authenticated MCP endpoint |

`PASS` above means the existing subsystem is a credible Stage 18 foundation. It does not mean Stage
18 behavior has been implemented. The current checkpoint and CI strengths do not override the
failing security-critical rows.

## 5. Blocking findings

### P0 — security, authorization, or isolation blocker

#### S18-P0-01 — No production caller identity boundary

- **Root cause:** API and CLI composition inject fixed demo values from settings instead of
  authenticating a request through a production identity adapter. `DEMO_APPROVAL_ROLES` includes
  `quality_data_approver` by default. Production configuration validation does not reject this
  identity mode.
- **Affected files:** `src/copilot/api/dependencies.py`, `src/copilot/bootstrap/cli.py`,
  `src/copilot/config.py`, `.env.example`, `docker-compose.yml`, `docs/security-model.md`,
  `docs/deployment.md`.
- **Architecture impact:** there is no stable authentication port from which API and future MCP
  transports can derive the same internal principal.
- **Security impact:** remote requests could share a server-owned identity and approval-capable
  role. Caller attribution, role mapping, tenant binding, token audience, and session binding
  cannot be proven.
- **Recommended remediation:** add a transport-independent authenticated-principal contract and
  identity-adapter port; make API/CLI adapters explicit; forbid demo identity in production; map
  verified issuer/audience/subject/tenant/roles/scopes to a request-owned context; never accept
  client-supplied role or tenant claims without adapter verification.
- **Required tests:** missing/invalid/expired token denial, issuer/audience/tenant mismatch,
  role/scope mapping, demo-mode production startup rejection, API/CLI parity, identity-to-audit
  attribution, and prevention of implicit administrator/approver inheritance.

#### S18-P0-02 — Tenant isolation is not enforced by persistence contracts

- **Root cause:** tenant/user data is embedded in serialized payloads while table keys and
  repository APIs are primarily task/artifact/approval IDs. Repositories retrieve records before
  service-layer authorization and global audit list methods have no tenant/task filter.
  `NaturalLanguageTaskService._load_authorized` accepts `contract is None` as a tenant match.
- **Affected files:** `src/copilot/persistence/models.py`, `task_repository.py`,
  `approval_repository.py`, `artifact_repository.py`, `audit_repository.py`,
  `src/copilot/evidence/ledger.py`, `src/copilot/services/task_service.py`, database migrations.
- **Architecture impact:** isolation depends on every caller remembering a later service check;
  repository contracts cannot fail closed by construction.
- **Security impact:** guessed/leaked global IDs, pre-contract tasks, audit enumeration, or a future
  MCP resource handler could cross tenant boundaries.
- **Recommended remediation:** add explicit immutable `tenant_id` (and owner/principal where
  required) to persisted rows and indexes; make tenant/context mandatory in repository reads and
  writes; use composite uniqueness/foreign-key rules where practical; authorize before returning
  payloads; remove the `contract is None` tenant shortcut; provide safe admin/audit APIs separately.
- **Required tests:** cross-tenant task/approval/artifact/evidence/audit access, pre-contract task
  access, ID enumeration, tenant mismatch writes, concurrent tenant isolation, migration upgrade and
  rollback tests, and PostgreSQL row/query behavior.

#### S18-P0-03 — Executor context and approval are optional at the last enforcement point

- **Root cause:** `ToolExecutor.execute` accepts `security_context=None`; the fallback authorizer
  supplies a demo analyst identity and the execution metadata supplies default role/purpose values.
  The authorizer validates exact approval binding only if the caller already supplied an
  `approval_id`; it does not independently decide that the operation requires approval.
- **Affected files:** `src/copilot/tools/executor.py`, `src/copilot/policies/offline.py`,
  `src/copilot/policies/approval.py`, `src/copilot/services/task_intake.py`, graph policy/runtime,
  direct executor call sites and tests.
- **Architecture impact:** the existing executor is not yet a complete policy enforcement point.
  Reusing it from MCP would preserve the class name but not the governance invariant.
- **Security impact:** contextless execution loses verified tenant/user/scope/origin; an
  approval-required call can be constructed without an approval ID and reach execution.
- **Recommended remediation:** replace optional context with one mandatory, transport-neutral
  `ExecutionContext`; remove production/demo fallback authorization; include task, step, trace,
  principal, tenant, roles, scopes, purpose, origin, connection/session, deadline, cancellation, and
  approval binding; make the unified authorizer compute policy and approval requirements for every
  attempt; reject missing/mismatched approval before tool invocation.
- **Required tests:** context omission, forged/mismatched context, approval-required call without
  ID, stale/edited/replayed approval, direct executor invocation, API/CLI path parity, retry
  reauthorization, origin/scope propagation, and audit/evidence attribution.

### P1 — blocks correct MCP implementation

#### S18-P1-01 — Frozen design does not authorize MCP implementation

- **Root cause:** v1.1 is the sole implementation authority and explicitly excludes MCP. The MCP
  document is a planning design, and no accepted protocol ADR or versioned frozen baseline exists.
- **Affected files:** all seven `docs/design/*.md` authorities, `docs/architecture.md`,
  `docs/adr/`, and the MCP planning spec.
- **Architecture impact:** implementing Stage 18 would silently expand frozen contracts, capability
  names, lifecycle, persistence, policy, and externally visible behavior.
- **Security impact:** protocol/authentication/credential/tenant decisions would lack accepted
  authority and rollback policy.
- **Recommended remediation:** perform an explicit v1.2/Stage 18 design change; update every
  affected frozen document; resolve cross-document conflicts; approve an ADR pinning revision
  `2025-11-25`, SDK/version, transports, compatibility, upgrade, and rollback; only then modify code.
- **Required tests:** design consistency/doc checks and architecture decision acceptance before code.

#### S18-P1-02 — Registry cannot safely host dynamic namespaced capabilities

- **Root cause:** tool names use `^[a-z][a-z0-9_]{0,63}$`, so a stable dotted namespace is rejected;
  the allowlist defaults to the four frozen enum values. Registry entries do not carry server
  origin, provenance, schema/revision generation, or lease/revocation state.
- **Affected files:** `src/copilot/tools/registry.py`, `src/copilot/contracts/tools.py`, tool schema
  and registry tests, planner/plan validation caches.
- **Architecture impact:** external capabilities cannot share the one required registry without
  either changing the frozen contract or creating a prohibited second registry.
- **Security impact:** stale tool handles and cached plans cannot be deterministically invalidated
  after revocation; server collisions/origin confusion cannot be audited.
- **Recommended remediation:** after design approval, generalize the existing registry with a
  canonical namespace contract, immutable registration metadata, atomic per-server set replacement,
  generation tokens, deterministic collision rejection, and revocation validation at execution.
- **Required tests:** namespaced registration, duplicate/collision handling, concurrent refresh,
  server isolation, removal/change, stale handle/plan denial, schema generation mismatch, and
  backward compatibility for the four local tools.

#### S18-P1-03 — In-flight cancellation is cooperative in state only, not at the tool boundary

- **Root cause:** the executor uses a thread-pool runner. Timeout stops waiting but cannot terminate
  an already-running operation. Task cancellation changes workflow state but has no invocation
  cancellation handle propagated to the tool/adapter.
- **Affected files:** `src/copilot/tools/runner.py`, `src/copilot/tools/executor.py`,
  `src/copilot/services/task_service.py`, graph runtime, tool adapter contracts, shutdown handling.
- **Architecture impact:** MCP cancel, disconnect, timeout, and shutdown cannot share one reliable
  cancellation semantic with existing execution.
- **Security impact:** a cancelled remote request can leave data access or external work running;
  resource cleanup and bounded shutdown are not guaranteed.
- **Recommended remediation:** define a mandatory cancellation/deadline token in execution context;
  require cancellable I/O adapters; track live invocation handles; distinguish timeout, caller
  cancellation, disconnect, and shutdown; bound cleanup and reject non-cancellable production
  adapters where cancellation is required.
- **Required tests:** cancel before start, during I/O, timeout race, disconnect propagation,
  shutdown drain/abort, no late evidence/artifact commit, idempotent repeated cancel, and resource
  cleanup.

#### S18-P1-04 — Audit and observability do not span a network boundary

- **Root cause:** traces and metrics are intentionally process-local; context propagation into the
  tool thread is not a generic invariant. Audit payloads omit tenant, trace, approval, purpose,
  scope, origin, connection, server, session, transport, and protocol revision; repository queries
  are global.
- **Affected files:** `src/copilot/observability/`, `src/copilot/contracts/observability.py`,
  `src/copilot/services/observability.py`, `src/copilot/persistence/audit_repository.py`,
  `src/copilot/tools/base.py`, ADR-005.
- **Architecture impact:** transport, session, executor, tool, evidence, and response events cannot
  be correlated end-to-end with a durable, tenant-scoped record.
- **Security impact:** incident response cannot prove caller/server/scope/origin or reliably search
  one tenant without exposing another tenant's audit data.
- **Recommended remediation:** propagate a single execution context across thread/async/network
  boundaries; define exporter ports and durable correlation; extend audit contracts and storage with
  first-class security/provenance fields; make queries tenant/task scoped; preserve redaction and
  prohibit secrets/tokens/raw payloads.
- **Required tests:** context continuity across thread/async/network, tenant-scoped audit queries,
  denial/error/timeout/cancel records, trace-to-evidence linkage, redaction/token-leakage, exporter
  failure behavior, and multi-session correlation.

#### S18-P1-05 — Deployment proves a demo topology, not an externally exposed service

- **Root cause:** Compose uses development mode and mock database/LLM settings; production IAM is
  absent. The repository has PostgreSQL and container CI definitions, but local PostgreSQL tests
  were skipped and the local Docker daemon was unavailable.
- **Affected files:** `Dockerfile`, `docker-compose.yml`, `.env.example`, `src/copilot/config.py`,
  `.github/workflows/ci.yml`, deployment/operations/troubleshooting docs.
- **Architecture impact:** live startup, migration, readiness, graceful shutdown, credential
  injection/rotation, and recovery are not demonstrated for an authenticated network boundary.
- **Security impact:** exposing the current topology would expose demo identity semantics and mock
  assumptions.
- **Recommended remediation:** add an explicit production profile with real identity, PostgreSQL,
  secret references, migration policy, network allowlists, readiness dependencies, and graceful
  drain; exercise it in CI or an isolated deployment environment before Stage 18.
- **Required tests:** real PostgreSQL migration/repository suite, production-config startup denial,
  authenticated health/readiness behavior, SIGTERM with active calls, secret rotation, backup and
  recovery drill, image scan, and container smoke test.

### P2 — non-blocking hardening after P0/P1 remediation

- **S18-P2-01:** `scripts/check_architecture.py` enforces useful import boundaries, including MCP SDK
  placement, but cannot detect direct runtime calls, missing execution context, approval omission,
  registry-generation staleness, or tenantless repository access. Add AST/type/contract checks and
  boundary integration tests for these invariants.
- **S18-P2-02:** SQL-backed repositories retain names such as `InMemoryToolAuditRepository`, which
  obscures the production storage boundary and can mislead composition/operations. Introduce clear
  interface/provider naming in a behavior-preserving change.
- **S18-P2-03:** the initial isolated package build failed because the sandbox could not download
  `setuptools`; `python -m build --no-isolation` succeeded. Make CI build dependencies reproducible
  through a locked/cacheable builder image or approved package mirror.

### P3 — optional improvements

- **S18-P3-01:** publish a generated dependency/call graph as an audit artifact so future readiness
  reviews can diff boundary changes.
- **S18-P3-02:** separate current-scenario coverage from placeholder-module coverage. Zero-byte MCP
  modules report no statements and therefore should never visually imply MCP test coverage.

## 6. Stage 18 admission gate

| Gate | Result | Evidence |
| --- | --- | --- |
| 1. Stable Registry/Executor; every tool uses one entry | FAIL | Current paths use one executor, but registry cannot host safe namespaced dynamic capabilities and executor context is optional |
| 2. Policy, Approval, Evidence, Audit usable | FAIL | v1.1 path exists, but approval can be omitted at direct executor boundary and evidence/audit lack tenant-scoped MCP provenance |
| 3. Authentication, tenant isolation, data access, redaction tested | FAIL | Data policies/redaction pass existing tests; production authentication is absent and repository-level tenant isolation is not enforced |
| 4. Checkpoint, cancellation, retry, timeout, recovery, errors stable | FAIL | Checkpoint/retry/timeout/errors exist; running calls are not cancellable and network/session recovery is not defined |
| 5. Logs/traces/metrics cover network calls | FAIL | Observability is process-local and network/thread propagation is not proven |
| 6. CI, deployment, operations, security response established | FAIL | Existing CI/docs are useful, but production identity/topology and real external protocol/security suites do not exist |
| 7. No critical Stage 0–17 mock/placeholder/unimplemented path | FAIL | Production entry still uses Demo Identity; Compose uses mock providers; all MCP files and tests are intentional zero-byte placeholders |

Admission also requires zero P0 and zero MCP-blocking P1 findings. The current audit has three P0
findings and five blocking P1 findings, so the gate fails without ambiguity.

## 7. Quality-gate evidence

Commands were taken from `pyproject.toml` and `.github/workflows/ci.yml`; thresholds were not
reduced and no tests were deleted, skipped, or marked xfail by this audit.

| Check | Result | Detail |
| --- | --- | --- |
| Ruff lint | PASS | `ruff check .` |
| Ruff format | PASS | `ruff format --check .`; 346 files already formatted |
| Mypy | PASS | No issues in 344 source files |
| Unit | PASS | 409 passed; total coverage 83% |
| Integration + contract + smoke | PASS WITH ENVIRONMENT SKIPS | 97 passed, 2 skipped, 1 warning; live RAG disabled and `TEST_POSTGRES_URL` absent |
| Evaluation gate | PASS | Mock seed 42; 30/30 passed, 0 failed/error/skipped, no regression, gate passed |
| Documentation check | PASS | `scripts/check_docs.py` |
| Architecture check | PASS | `scripts/check_architecture.py` |
| Actionlint | PASS | Installed local `actionlint` returned no findings |
| Python package build | PASS | Isolated build could not reach package index; `python -m build --no-isolation` built sdist and wheel |
| Compose validation | PASS | Legacy `docker-compose config` succeeded with an explicit local RAG image value |
| Container build | NOT RUNNABLE | Docker client exists, but no local Docker daemon/socket was available |

The evaluation run updated the repository's already-dirty generated `evaluation/reports/latest.*`
files and added its normal run artifact. Those generated outputs and all pre-existing working-tree
changes are outside this review's authored scope and were not reverted.

## 8. Required Stage 17.1 hardening plan

Execute these work packages in order. Do not combine them with MCP implementation.

1. **Governance and contracts**
   - Approve a versioned Stage 18 design baseline and protocol ADR.
   - Record identity, tenant, executor, registry, audit, cancellation, persistence, compatibility,
     deployment, and rollback invariants before code changes.
2. **Identity and tenant enforcement**
   - Implement the production identity adapter and production demo-mode rejection.
   - Add first-class tenant ownership to persistence and make tenant context mandatory in every
     repository and service access path.
   - Migrate existing data explicitly and add adversarial cross-tenant tests.
3. **Unified executor and registry hardening**
   - Make execution context mandatory and remove fallback identity/roles/purpose.
   - Re-evaluate policy and approval requirements inside the unavoidable executor boundary.
   - Extend the existing registry, after design approval, with namespace/provenance/generation and
     atomic refresh/revocation semantics. Do not create an MCP-only registry or executor.
4. **Cancellation, audit, and observability**
   - Add cancellable invocation handles and adapter contracts.
   - Propagate context across thread/async/process/network boundaries.
   - Persist tenant-scoped, redacted authorization/provenance/audit fields and add exporter ports.
5. **Production verification**
   - Exercise PostgreSQL migrations and all repositories in an isolated real service.
   - Build/run the container topology with production-mode config and authenticated requests.
   - Run security, recovery, shutdown, backup/restore, and incident-response drills.
   - Re-run every current quality gate and this readiness audit.

Each work package should be reviewable independently and must include migrations, unit,
integration, contract, security, and operational evidence proportional to the boundary changed.

## 9. Conditions required before Stage 18 may begin

Stage 18 can be reconsidered only when all of the following are evidenced in code and tests:

1. A versioned frozen Stage 18 baseline and accepted MCP protocol ADR authorize the scope.
2. Production requests use an authenticated, transport-independent caller identity; production
   startup rejects Demo Identity.
3. Tenant ownership is first-class in storage and mandatory in repository/API/service access;
   cross-tenant adversarial tests pass on PostgreSQL.
4. `ToolExecutor` requires a complete execution context and enforces policy plus required approval
   for every call, including direct calls, retries, and future protocol calls.
5. The one existing `ToolRegistry` safely supports canonical namespaces, immutable provenance,
   atomic refresh, revision/generation checks, and immediate revocation of stale execution.
6. Cancellation actually reaches running adapters and cleanup/late-commit behavior is tested.
7. Evidence and audit remain immutable/redacted while recording tenant, principal, trace, approval,
   scope, origin, connection, server, session, capability, transport, and protocol provenance.
8. Logs, traces, and metrics preserve context across thread, async, process, and network boundaries
   and have an operational exporter/retention path.
9. Production-mode PostgreSQL migrations, deployment, readiness, graceful shutdown, recovery,
   credential rotation, and incident response are exercised without mock identity/providers.
10. All P0 and blocking P1 findings in this review are closed, existing regressions remain green,
    and the seven admission gates are independently re-audited as PASS.

Until these conditions are met, the correct architecture decision is to harden Stage 17 as Stage
17.1 and leave every MCP placeholder unchanged.
