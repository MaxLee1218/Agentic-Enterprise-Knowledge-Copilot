# Stage 18 Readiness Review

## Decision

```text
Stage 18 Readiness: READY
```

Audit date: 2026-08-09
Scope: implemented Stage 0–17.1 code, frozen v1.1 design, contracts, API/CLI, graph, registry,
executor, policy, approval, identity, tenant persistence, evidence, audit, observability,
cancellation, migrations, deployment, CI, evaluations, and the separate Stage 18 boundary.

The earlier 2026-08-07 review correctly stopped MCP while Stage 17.1 controls were absent. Those
findings were remediated and verified before Stage 18 implementation began. The frozen Supplier
Quality Analysis v1.1 business behavior remains unchanged; Stage 18 is a separate protocol edge.

Pre-implementation evidence was green: Ruff, format, Mypy, architecture checks, and the configured
Stage 0–17 suite (`534 passed, 2 skipped`, where the two skips are explicit opt-in external-service
tests). The production deployment Gate 6 evidence is recorded in the Stage 17.1 report.

## Readiness matrix

| Area | Status | Evidence | Risk | Stage 18 impact |
|---|---|---|---|---|
| Tool Registry | PASS | Namespaces, origin/provenance, generation, atomic refresh and revoke are implemented and tested | P3 | Stable imported capability ownership |
| Tool Executor | PASS | Mandatory trusted `ExecutionContext`; all local and imported calls use the same executor | P3 | No MCP execution bypass |
| Policy | PASS | Central role/data policy plus deny-by-default MCP server/capability/tenant/scope rules | P3 | Import/export can reuse existing authorization |
| Approval | PASS | Exact task/step/schema/arguments/expiry binding is checked at execution | P3 | Remote calls cannot omit required approval |
| Identity | PASS | Production gateway identity exists; MCP server has issuer/audience/expiry-bound JWT verification | P3 | Stable transport-neutral principal |
| Tenant isolation | PASS | Tenant-qualified repository APIs, composite keys/FKs and cross-tenant tests | P3 | Sessions and invocation metadata are tenant-bound |
| Evidence | PASS | Immutable tenant-scoped ledger and output guard | P3 | MCP origin/provenance can be recorded without a second ledger |
| Audit | PASS | Tenant-scoped append-only tool/workflow audit with trace, principal, approval and origin | P3 | Cross-network path is reconstructable |
| Observability | PASS | Context propagation, structured events, spans and bounded metrics | P3 | MCP lifecycle/latency/failure/reconnect metrics can be added |
| Checkpoint | PASS | Durable task/checkpoint recovery and leases tested | P3 | MCP session recovery remains separate from task checkpoints |
| Cancellation | PASS | Mandatory token, live invocation registry, cooperative semantics and late-result discard | P2 | Protocol cancellation has a truthful boundary |
| Persistence | PASS | SQLAlchemy/Alembic, SQLite and real PostgreSQL verification | P3 | Versioned MCP state migration is safe to add |
| Security | PASS | Identity, tenant, approval, redaction, injection and data-boundary suites pass | P3 | External exposure does not inherit demo defaults |
| CI | PASS | Lint, format, type, tests, evaluation, docs, architecture, build and PostgreSQL gates | P3 | Hermetic MCP gates can be added without internet services |
| Deployment | PASS | Non-root image, production Compose, migration/readiness/rollback and Gate 6 validation | P2 | Authenticated localhost-first server can be operated safely |

## Findings by severity

- P0: none open.
- MCP-blocking P1: none open.
- P2: production OAuth/IdP and remote MCP peers remain deployment-specific and require an approved
  rollout; cooperative cancellation cannot forcibly terminate arbitrary synchronous Python code.
- P3: add more third-party interoperability profiles after security review.

## Seven admission gates

1. Registry/Executor unified path — PASS.
2. Policy, Approval, Evidence and Audit — PASS.
3. Identity, tenant isolation, data access and redaction — PASS.
4. Checkpoint, cancellation, retry, timeout, recovery and typed errors — PASS.
5. Cross-thread/async/network logs, traces and metrics — PASS.
6. CI, deployment, operations and incident response — PASS.
7. No production-critical Stage 0–17 mock/placeholder — PASS. Offline mocks remain explicitly
   development/test adapters and are not used as evidence of live external behavior.

## Architecture decision

Stage 18 could proceed because there were no open P0 or MCP-blocking P1 findings. Implementation
must remain optional (`MCP_ENABLED=false` preserves the existing vertical slice), keep official SDK
types inside `copilot.mcp.protocol`, import capabilities through the existing registry/executor,
and export only explicit allowlist entries.
