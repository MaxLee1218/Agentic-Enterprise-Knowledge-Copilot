# Stage 18 Engineering Report

Date: 2026-08-10

## 1. Pre-implementation Audit

```text
Readiness: READY
```

The audit covered the frozen v1.1 design, contracts, API/CLI, LangGraph runtime, Registry,
Executor, Policy, Approval, Identity, tenant boundaries, Evidence, Audit, Observability,
Checkpoint, Cancellation, Persistence, Security, CI and deployment. Stage 17.1 had remediated every
P0 and MCP-blocking P1 from the earlier stop decision. Before Stage 18 code was admitted, Ruff,
format, Mypy, architecture checks and the existing regression suite were green.

The frozen Supplier Quality Analysis v1.1 business lifecycle and four local tool contracts were
not changed. Stage 18 is a separate optional protocol boundary.

## 2. Architecture Findings

- P0: none open.
- MCP-blocking P1: none open.
- P2: public TLS/reverse proxy and enterprise IdP issuance are deployment-specific; cooperative
  cancellation cannot forcibly terminate arbitrary synchronous Python code; a single imported
  namespace is bound to one active tenant session at a time.
- P3: add reviewed third-party server profiles and broader latency datasets after rollout.

## 3. Stage 18 Gate

| Gate | Result | Evidence |
|---|---|---|
| 1. Stable Registry/Executor path | PASS | Qualified namespaces, origin/provenance, atomic refresh/revoke; real imported and exported tools use the existing executor |
| 2. Policy/Approval/Evidence/Audit | PASS | Direct provider and real transport tests prove allow, deny, approval refusal, Evidence and audit records |
| 3. Identity/tenant/data/redaction | PASS | JWT issuer/audience/expiry binding, tenant repositories, scope/origin rules and security suites |
| 4. Checkpoint/cancel/retry/timeout/recovery/errors | PASS | Explicit lifecycle, typed errors, local retry authorization, real timeout/cancel and reconnect tests |
| 5. Logs/traces/metrics | PASS | MCP lifecycle/invocation/reconnect/latency metrics and trace/task/session metadata propagate to audit |
| 6. CI/deployment/operations | PASS | Dedicated CI gates, package and Docker builds, PostgreSQL 16.4 migration, operations/security docs |
| 7. No critical-path placeholders | PASS | Real official SDK servers and both transports prove protocol behavior; offline mocks are not used as protocol evidence |

## 4. Architecture Decision

Stage 18 was allowed because all seven gates passed with no open P0 or blocking P1. ADR-008 pins
MCP `2025-11-25` and SDK `>=1.29,<2.0`, restricts SDK types to one protocol adapter, reuses the
existing governance path, isolates sessions and requires explicit import/export policy.

## 5. Implemented Architecture

### MCP Client

- Per-server event-loop/runtime and SDK session; no mutable global session.
- stdio and Streamable HTTP initialization, exact server identity, revision negotiation and
  discovery of tools, resources and prompts.
- Stable namespace, origin, endpoint fingerprint, protocol/server/schema provenance and atomic
  Registry refresh/revocation.
- Imported tools execute only through `ToolRegistry -> ToolExecutor -> Policy -> Evidence/Audit`.
- Explicit reconnect, credential re-resolution, reauthorization, rediscovery and recovery state.
- Sampling, elicitation and roots callbacks are omitted by default and policy-gated when enabled.
- Progress, tools/resources/prompts list-change notifications, timeout and cancellation are
  adapted to stable application callbacks.

### MCP Server

- Real low-level SDK server over stdio and authenticated Streamable HTTP.
- Explicit `MCPExportRule`; no automatic local-tool export.
- Discovery visibility and invocation permission are separate checks.
- Exported tool calls construct the same `ToolCall` and mandatory `ExecutionContext` as native
  calls, then use the existing Registry/Executor/Policy/Approval/Evidence/Audit path.
- Resources and prompts have separate explicit providers; system prompts are not exposed.

### Protocol, transport and security

- Official SDK imports exist only in `src/copilot/mcp/protocol.py`.
- Stable contracts and typed errors contain no SDK models.
- HTTP uses Bearer verification, session identity binding, request/idle limits, canonical host and
  Origin checks, DNS-rebinding controls, no ambient proxy routing and loopback-only plaintext.
- stdio requires fixed absolute executable/arguments/directory and a minimal non-secret
  environment; no shell command string.
- Untrusted names/schemas/descriptions are bounded, normalized and prompt-injection sanitized.

### Persistence and composition

- Tenant-scoped non-secret connection/session/recovery records and append-only minimized invocation
  metadata.
- Credential references and token fingerprints only; raw credentials/results are not persisted.
- `MCP_ENABLED=false` keeps the existing Stage 0–17 API, CLI, graph and four-tool workflow
  independent of MCP.

## 6. Files Created

```text
docs/adr/ADR-008-mcp-protocol-2025-11-25.md
docs/stage-18-engineering-report.md
evaluation/run_mcp_eval.py
migrations/versions/20260809_0003_stage18_mcp_state.py
src/copilot/bootstrap/mcp.py
tests/contract/mcp/__init__.py
tests/contract/mcp/test_protocol_contract.py
tests/fixtures/mcp/governed_oauth_server.py
tests/fixtures/mcp/real_test_server.py
tests/integration/mcp/__init__.py
tests/integration/mcp/test_governed_export_provider.py
tests/integration/mcp/test_governed_import.py
tests/integration/mcp/test_real_client_primitives.py
tests/integration/mcp/test_real_oauth_export.py
tests/integration/mcp/test_real_protocol.py
tests/integration/mcp/test_real_resilience_and_isolation.py
tests/mcp_helpers.py
tests/security/__init__.py
tests/security/mcp/__init__.py
tests/security/mcp/test_mcp_security.py
tests/smoke/mcp/__init__.py
tests/smoke/mcp/test_client_smoke.py
tests/unit/evaluation/test_mcp_evaluators.py
tests/unit/mcp/__init__.py
tests/unit/mcp/test_contracts_lifecycle_security.py
```

## 7. Files Modified

Configuration, CI and architecture documentation:

```text
.env.example
.github/workflows/ci.yml
README.md
docs/adr/README.md
docs/architecture.md
docs/mcp-architecture.md
docs/mcp-operations.md
docs/mcp-security.md
docs/security-model.md
docs/stage-18-readiness-review.md
pyproject.toml
```

Runtime, contracts, policy, persistence and scripts:

```text
scripts/inspect_mcp_connection.py
scripts/run_mcp_server.py
scripts/smoke_mcp.py
src/copilot/bootstrap/container.py
src/copilot/config.py
src/copilot/contracts/__init__.py
src/copilot/contracts/mcp.py
src/copilot/mcp/__init__.py
src/copilot/mcp/capabilities.py
src/copilot/mcp/client/capability_importer.py
src/copilot/mcp/client/connection_registry.py
src/copilot/mcp/client/elicitation_handler.py
src/copilot/mcp/client/manager.py
src/copilot/mcp/client/roots_provider.py
src/copilot/mcp/client/sampling_handler.py
src/copilot/mcp/client/session.py
src/copilot/mcp/config.py
src/copilot/mcp/errors.py
src/copilot/mcp/lifecycle.py
src/copilot/mcp/protocol.py
src/copilot/mcp/security/connection_policy.py
src/copilot/mcp/security/credential_provider.py
src/copilot/mcp/security/origin_validator.py
src/copilot/mcp/security/scope_mapper.py
src/copilot/mcp/server/authorization.py
src/copilot/mcp/server/capability_exporter.py
src/copilot/mcp/server/prompt_provider.py
src/copilot/mcp/server/resource_provider.py
src/copilot/mcp/server/server.py
src/copilot/mcp/server/tool_provider.py
src/copilot/mcp/transports/base.py
src/copilot/mcp/transports/stdio.py
src/copilot/mcp/transports/streamable_http.py
src/copilot/persistence/mcp_connection_repository.py
src/copilot/persistence/mcp_session_repository.py
src/copilot/persistence/models.py
src/copilot/policies/mcp_access.py
src/copilot/tools/registry.py
```

Evaluation and regression coverage:

```text
evaluation/evaluators/mcp_interoperability.py
evaluation/evaluators/mcp_safety.py
tests/integration/test_alembic_migrations.py
tests/integration/test_postgres_persistence.py
```

## 8. Database Migrations

Alembic revision `20260809_0003` upgrades Stage 17.1 revision `20260808_0002` and adds:

- `mcp_connections`: composite tenant/connection key, tenant namespace uniqueness, non-secret
  configuration payload and origin fields.
- `mcp_sessions`: composite tenant/session key, tenant-qualified connection FK, lifecycle,
  expiration and recovery payload.
- `mcp_invocations`: append-only tenant/invocation key with session/task/trace indexes and minimized
  metadata only.

Fresh upgrade, Stage 17 database upgrade and downgrade were tested on isolated SQLite. Upgrade to
head and MCP repository cross-tenant denial were also tested against a disposable PostgreSQL 16.4
container; it was stopped and automatically deleted after validation. No existing production data
is deleted by upgrade. Downgrade drops Stage 18 tables and requires backup/explicit approval.

## 9. Security Controls

| Control | Implementation |
|---|---|
| Authentication | JWT signature, issuer, audience, expiry, issued-at, subject, client, user and tenant validation |
| Scope | Known MCP scopes map to internal permissions; unknown/missing scopes fail closed |
| Tenant | Connection/session/repository/provider checks; one active imported namespace per tenant session |
| Allowlist | Server IDs, origins, namespaces, capabilities, roots and exports are explicit |
| Origin | Canonical endpoint, approved host, fresh DNS resolution, loopback HTTP and Origin validation |
| Credential | Runtime reference resolution; no URL/config/log/persistence raw token |
| Sampling | Disabled by omission; explicit capability/type/scope/local policy required |
| Elicitation | Disabled by omission; explicit policy and secret-field rejection |
| Schema/content | Size/depth/node/property bounds, no remote `$ref`, injection quarantine, bounded results |
| Retry/cancel | Local retry permission plus read-only/idempotent/non-destructive requirement; bounded timeout/cancel |

## 10. Test Results

Final full suite:

```text
Unit:        424 passed
Contract:     17 passed
Integration:  78 passed, 2 explicit environmental skips
Smoke:        21 passed
Security:     21 passed
Total:       561 passed, 2 skipped
```

Stage 18-specific tests contribute 27 passing cases: 8 unit/evaluator, 2 contract, 13 integration,
1 smoke and 3 security. The two full-suite skips are unchanged opt-in external-service tests, not
Stage 18 failures. Real PostgreSQL was executed separately: 1 passed.

Evaluation:

```text
MCP interoperability: 13/13 passed
MCP safety:            12/12 passed
Existing Agent eval:   30/30 passed; baseline regression gate passed
Existing regression:  pre-Stage-18 legacy subset remained green; post-change total 561 passed
```

The protocol tests use official SDK server/client behavior, real stdio subprocesses and real
localhost HTTP/JWT sessions. They do not substitute an in-memory protocol mock.

## 11. Quality Gates

| Gate | Result |
|---|---|
| Ruff lint | PASS |
| Ruff format | PASS; 381 files formatted |
| Mypy strict | PASS; 377 source files |
| Pytest | PASS; 561 passed, 2 opt-in skips |
| Architecture dependency check | PASS |
| Documentation governance | PASS |
| Alembic SQLite fresh/upgrade/downgrade | PASS; 2/2 |
| PostgreSQL 16.4 migration/repository/restart | PASS; 1/1 |
| Agent evaluation | PASS; 30/30 |
| MCP interoperability evaluation | PASS; 13/13 |
| MCP safety evaluation | PASS; 12/12 |
| Isolated sdist/wheel build | PASS |
| Wheel | PASS; 352,087 bytes; SHA-256 `0c738bbb73fbc769e6be36a9a6a81cf9a4314db018269ead7daf9ff821093227` |
| sdist | PASS; 271,612 bytes; SHA-256 `58fe5d0620d6970b82c61e308e7e7bde0ae68a57dc592db462498fc6d92435ee` |
| Docker image | PASS; `enterprise-copilot:stage18`, non-root `appuser`, manifest `sha256:14bfca54ca1091eded3002fe07d26b9148319d2d23654058a9091406ac07338d` |

## 12. Stage 18 Acceptance Matrix

| # | Requirement | Result | Evidence |
|---:|---|---|---|
| 1 | 2025-11-25 initialization/negotiation | PASS | real protocol + contract tests |
| 2 | Client stdio | PASS | real subprocess server round trip |
| 3 | Client Streamable HTTP | PASS | real localhost session round trip |
| 4 | Stable server namespace | PASS | normalization/import tests |
| 5 | Imported tool uses Registry/Executor | PASS | governed import integration |
| 6 | Server explicit allowlist | PASS | exporter/security tests |
| 7 | Unauthorized capability hidden/denied | PASS | empty/wrong-tenant/unknown export tests |
| 8 | Server uses Registry/Executor | PASS | OAuth server and provider integration |
| 9 | Imported call passes Policy | PASS | MCP-aware authorizer integration |
| 10 | Exported call passes Policy | PASS | existing local authorizer integration |
| 11 | Approval not bypassed | PASS | gated export returns `APPROVAL_REQUIRED` |
| 12 | Evidence generated | PASS | import and export ledger assertions |
| 13 | Audit generated | PASS | tool and MCP invocation repository assertions |
| 14 | Trace propagated | PASS | task/trace/step/call metadata assertions |
| 15 | Per-server session isolation | PASS | two live stdio sessions; peer disconnect test |
| 16 | Tenant isolation | PASS | repository, server visibility and active namespace tests |
| 17 | Origin traceable | PASS | Evidence and invocation origin assertions |
| 18 | Provenance traceable | PASS | revision/server/schema digest assertions |
| 19 | Scope traceable | PASS | JWT/scope policy and invocation metadata |
| 20 | Sampling default off | PASS | callback omitted; default-deny test |
| 21 | Elicitation default off | PASS | callback omitted; default-deny test |
| 22 | Sampling authorized path | PASS | real callback request/response |
| 23 | Elicitation authorized path | PASS | real callback plus secret rejection |
| 24 | OAuth/Authorization integration | PASS | real signed Bearer JWT; bad audience denied |
| 25 | Reconnect/recovery | PASS | credential/policy rediscovery and recovery count |
| 26 | Prompt injection | PASS | malicious description quarantined before registration |
| 27 | Token leakage | PASS | fingerprint-only verifier/repository assertions |
| 28 | Cross-tenant | PASS | client/server/repository denial tests |
| 29 | Cross-server | PASS | isolated session/origin/disconnect test |
| 30 | Privilege escalation | PASS | empty export/unknown capability/approval tests |
| 31 | Client smoke | PASS | real `scripts/smoke_mcp.py` test |
| 32 | Server smoke | PASS | real OAuth exported-tool round trip |
| 33 | Interoperability evaluation | PASS | 13/13 |
| 34 | Safety evaluation | PASS | 12/12 |
| 35 | Operations docs | PASS | `docs/mcp-operations.md` |
| 36 | Credential rotation docs | PASS | re-resolve/revoke/reconnect procedure |
| 37 | Protocol upgrade docs | PASS | ADR/review/test/staged rollout procedure |
| 38 | Rollback docs | PASS | flags/image/config/data-preserving rollback |
| 39 | Real protocol, not mocks | PASS | official SDK, subprocess stdio, HTTP/JWT servers |
| 40 | Stage 0–17 regression | PASS | 561 total passes; Agent eval 30/30 |

Additional contract primitives verified by the same real server are resources, prompts, roots,
sampling, elicitation, progress and tools/resources/prompts list-change notifications. Invalid
JSON-RPC and hostile Origin requests are rejected.

## 13. Remaining Limitations

- Interoperability is verified against repository-owned hermetic servers, not every third-party
  MCP implementation.
- Production OAuth/IdP token issuance, public TLS termination, reverse proxy and remote network
  latency are deployment responsibilities and were not exercised against a public service.
- Plain HTTP is deliberately loopback-only. Public bind requires explicit configuration and
  security review.
- One active imported namespace binds to one tenant session. Concurrent tenants require separate
  approved connection IDs/namespaces or tenant-isolated workers.
- Reconnect reinitializes and rediscovers state; durable replay of an external server's event store
  across a process restart is not claimed.
- Cancellation is truthful and bounded at the protocol adapter, but cannot forcibly interrupt
  arbitrary non-cooperative synchronous code after it has started.
- Sampling and elicitation are implemented only behind explicit callbacks/policy and remain off by
  default; no production model or human-interaction provider is bundled.

## 14. Final Conclusion

```text
STAGE 18 COMPLETE
```
