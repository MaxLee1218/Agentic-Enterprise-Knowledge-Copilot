# ADR-007: Stage 18 MCP Readiness Boundary

## Status

Accepted

## Date

2026-08-08

## Context

The frozen Supplier Quality Analysis v1.1 baseline correctly records that MCP is outside its
implemented scope. A later interoperability stage cannot safely add a new network/protocol entry
point while production identity, tenant persistence, direct executor authorization, cancellation,
registry lifecycle, and cross-boundary correlation remain caller conventions. Changing the v1.1
documents to imply later behavior would erase an accurate historical baseline.

## Decision

- Supplier Quality Analysis v1.1 remains unchanged and continues to exclude MCP behavior.
- Stage 17.1 is a compatibility-preserving hardening stage. It may add general identity,
  execution-context, tenant, repository, registry, cancellation, audit, observability, migration,
  and deployment controls, but it must not add MCP SDK types, transports, sessions, protocol
  handlers, discovery, sampling, elicitation, resources, prompts, or client/server behavior.
- Stage 18 is a separate future design and implementation stage. It may begin only after an
  evidence-based readiness review shows zero open P0 and zero MCP-blocking P1 findings and all
  seven readiness gates pass.
- A future protocol adapter must reuse the single internal Policy, Approval, ToolRegistry,
  ToolExecutor, Evidence, Audit, tenant, cancellation, and observability paths. It may not create a
  parallel route to a concrete business tool or data source.
- General registry source/provenance metadata and namespacing introduced in Stage 17.1 are
  protocol-neutral. They do not represent external capability discovery or an implemented MCP
  capability.
- Any Stage 18 change that alters a frozen v1.1 business contract or lifecycle still requires the
  full frozen-baseline design-change process; this ADR does not grant that authority.

The Stage 18 admission gates are:

1. one stable Registry/Executor path;
2. unavoidable Policy, Approval, Evidence, and Audit controls;
3. production Identity, tenant isolation, data access, and redaction;
4. checkpoint, truthful cancellation, retry, cleanup, and recovery;
5. thread/async/network correlation;
6. green CI, deployment, PostgreSQL, migration, readiness, and operations evidence; and
7. no demo, mock, placeholder, or silent fallback on the production critical path.

## Alternatives Considered

Editing the frozen v1.1 documents to include MCP was rejected because it would rewrite historical
scope and bypass the required design-change process. Starting a protocol implementation while
hardening in parallel was rejected because it would make unsafe entry paths part of the new
architecture. Introducing MCP-specific fields into internal execution contracts was rejected
because the security foundation must remain reusable by any approved transport.

## Consequences

Stage 17.1 can strengthen shared boundaries without breaking the frozen scenario. Stage 18 remains
blocked whenever any admission gate lacks evidence, including environment-dependent PostgreSQL or
Compose verification. Future MCP work must be requested separately and reviewed against the
protocol revision and security rules then in force. Empty MCP scaffolds remain placeholders and
cannot be cited as implemented functionality.

## Related Documents

- [Frozen design baseline](../design/design_baseline.md)
- [Architecture](../architecture.md)
- [Security model](../security-model.md)
- [Stage 18 readiness review](../stage-18-readiness-review.md)
- [Stage 17.1 hardening report](../stage-17-1-hardening-report.md)
