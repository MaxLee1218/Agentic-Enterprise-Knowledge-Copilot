# ADR-008: Governed MCP Protocol 2025-11-25 Boundary

## Status

Accepted

## Date

2026-08-09

## Context

Stage 17.1 closed the identity, tenant, direct-executor authorization, approval, registry,
cancellation, audit, observability, migration, and deployment blockers recorded by ADR-007. Stage
18 requires real bidirectional interoperability without changing the frozen Supplier Quality v1.1
business scenario or creating parallel execution and policy systems.

## Decision

- Pin protocol revision `2025-11-25` and Python SDK `>=1.29,<2.0`.
- Confine official SDK imports and types to `copilot.mcp.protocol`; business layers use versionable
  contracts from `copilot.contracts.mcp`.
- Support approved stdio and localhost-first Streamable HTTP transports.
- Give every external server an isolated session/runtime, stable namespace, canonical origin and
  schema provenance. Registration does not imply planner visibility or execution permission.
- Route imported tools through the existing `ToolRegistry` and `ToolExecutor`.
- Export only reviewed `MCPExportRule` entries and route them through the same executor, policy,
  approval, evidence, audit and observability controls.
- Require issuer/audience/expiry/client/user/tenant/scope-bound Bearer JWTs for HTTP. Store only
  credential references and token fingerprints.
- Keep sampling and elicitation disabled by default; roots are tenant allowlisted.
- Require a new ADR, compatibility review, contract suite and rollback plan before changing the
  default protocol revision or SDK major version.

## Alternatives Considered

FastMCP types throughout the application were rejected because SDK evolution would leak into
business contracts. A second MCP executor/policy/evidence stack was rejected because it would
bypass established governance. Automatic export of every local tool and automatic planner trust of
every discovered tool were rejected as privilege escalation paths.

## Consequences

The system gains real client/server interoperability while retaining one authorization and
execution path. Operators must configure explicit identities, origins, namespaces, tenants,
scopes, exports, credential references and migrations. Protocol upgrades are deliberately slower
because compatibility and security evidence are mandatory.

## Related Documents

- [Stage 18 readiness review](../stage-18-readiness-review.md)
- [MCP architecture](../mcp-architecture.md)
- [MCP security](../mcp-security.md)
- [MCP operations](../mcp-operations.md)
- [ADR-007](ADR-007-stage-18-mcp-readiness-boundary.md)
