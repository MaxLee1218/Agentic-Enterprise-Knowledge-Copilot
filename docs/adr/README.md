# Architecture Decision Records

An Architecture Decision Record (ADR) is a durable record of one significant architecture choice,
the context in which it was made, the alternatives considered, and its consequences. ADRs explain
why a boundary or constraint exists; [`architecture.md`](../architecture.md) states the current
rules that contributors must follow.

## Why this project uses ADRs

The Copilot crosses domain, agent, policy, tool, evidence, persistence, model, and external-system
boundaries. Decisions in any one of those areas can affect security, traceability, recovery, and
compatibility elsewhere. ADRs make those decisions reviewable and prevent future implementation
from inferring architecture from scaffolds or prompts.

Use an ADR for a decision that materially affects package boundaries, shared contracts, runtime
control flow, security or approval behavior, persistence, interoperability, deployment, or an
externally visible interface. Routine implementation details that follow an accepted decision do
not need a separate ADR.

An ADR does not override the frozen Supplier Quality Analysis v1.2 design. A decision that changes
that baseline must first follow its explicit design-change and approval process.

## Naming convention

Files use a zero-padded, monotonically increasing identifier and a short kebab-case title:

```text
ADR-XXX-short-title.md
```

Examples are `ADR-001-package-and-layer-boundary.md` and `ADR-002-task-state-machine.md`. Numbers
are never reused, including when a proposal is rejected or superseded.

## Lifecycle

- `Proposed`: under review and not yet authoritative.
- `Accepted`: approved and authoritative within its stated scope.
- `Deprecated`: retained for history but no longer recommended; the reason must be recorded.
- `Superseded`: replaced by a newer ADR; both records must link to each other.

Accepted ADRs are immutable historical records apart from corrections, clarifications that do not
change the decision, and status/link updates. A material change is proposed in a new ADR. Update
`architecture.md` when acceptance changes the current architecture.

## Process

1. Select the next unused ADR number.
2. Copy the template below and describe one decision and its scope.
3. Link affected contracts, design baselines, security documents, and earlier ADRs.
4. Review consequences, migration needs, compatibility, tests, and evaluation impact.
5. Obtain the reviews required by `AGENTS.md` and the frozen baseline when applicable.
6. Set the status and update `architecture.md` only after the decision is accepted.
7. Preserve rejected, deprecated, and superseded records for traceability.

## Template

```markdown
# ADR-XXX: Title

## Status

Proposed

## Date

YYYY-MM-DD

## Context

What forces, constraints, and problem require a decision?

## Decision

What is decided, what is its scope, and what rules follow?

## Alternatives Considered

What credible alternatives were evaluated and why were they not selected?

## Consequences

What becomes easier or harder, including operational and migration effects?

## Related Documents

- [Document title](relative-path.md)
```

## Index

| ADR | Status | Date | Decision |
|---|---|---|---|
| [ADR-001](ADR-001-package-and-layer-boundary.md) | Accepted | 2026-07-21 | Use one `copilot` production package with explicit conceptual layer and dependency boundaries |
| [ADR-002](ADR-002-langgraph-orchestration.md) | Accepted | 2026-07-28 | Use LangGraph orchestration with separate SQLite checkpoints, business facts, and execution leases |
| [ADR-003](ADR-003-llm-provider-and-structured-output.md) | Superseded by ADR-018 | 2026-07-31 | Use replaceable structured LLM providers with deterministic validation, repair, and replan gates |
| [ADR-004](ADR-004-approval-edit-resolution.md) | Accepted | 2026-08-02 | Resolve bounded approval edits with complete replacement arguments and checkpoint resume |
| [ADR-005](ADR-005-local-observability-boundary.md) | Accepted | 2026-08-06 | Use injected provider-independent observability with bounded local logs, spans, metrics, and analysis |
| [ADR-006](ADR-006-deployment-persistence-boundary.md) | Accepted | 2026-08-07 | Separate Copilot PostgreSQL persistence from the enterprise business database and use explicit migrations |
| [ADR-007](ADR-007-stage-18-mcp-readiness-boundary.md) | Accepted | 2026-08-08 | Preserve frozen v1.1 scope and require Stage 17.1 hardening gates before any separate Stage 18 MCP work |
| [ADR-008](ADR-008-mcp-protocol-2025-11-25.md) | Accepted | 2026-08-09 | Pin MCP 2025-11-25 and reuse the governed Registry/Executor for isolated import and explicit export |
| [ADR-009](ADR-009-multi-domain-capability-manifests.md) | Accepted | 2026-08-22 | Use versioned domain capability manifests and historical tool-profile binding for multiple vertical slices |
| [ADR-010](ADR-010-version-bound-policy-rules.md) | Accepted | 2026-08-22 | Bind deterministic finance rules to exact controlled policy document versions and checksums |
| [ADR-011](ADR-011-accounts-payable-business-data-model.md) | Accepted | 2026-08-22 | Add a narrow tenant-scoped AP business schema while reusing suppliers and preserving database separation |
| [ADR-012](ADR-012-async-task-submission-model.md) | Accepted | 2026-08-25 | Make the future task submission contract acceptance-only with separate runtime state |
| [ADR-013](ADR-013-queue-delivery-dispatch-model.md) | Accepted | 2026-08-25 | Use a minimal at-least-once Queue envelope and transactional dispatch outbox |
| [ADR-014](ADR-014-worker-lease-fencing.md) | Accepted | 2026-08-25 | Extend the database lease with heartbeat, expiry, takeover, and monotonic fencing |
| [ADR-015](ADR-015-checkpoint-recovery-authority.md) | Accepted | 2026-08-25 | Keep Task DB authoritative and require fail-closed checkpoint recovery reconciliation |
| [ADR-016](ADR-016-runtime-graph-retry-ownership.md) | Accepted | 2026-08-25 | Separate Queue/runtime recovery from Graph and Tool business retry ownership |
| [ADR-017](ADR-017-postgresql-backed-queue-v1.md) | Accepted | 2026-08-26 | Use PostgreSQL-backed at-least-once Queue v1 behind the existing transactional outbox |
| [ADR-018](ADR-018-deterministic-plan-compilation.md) | Accepted | 2026-08-30 | Compile lightweight untrusted ProposedPlans into existing canonical TaskPlans using deterministic authorities |
| [ADR-019](ADR-019-interactive-clarification-resume.md) | Accepted | 2026-09-01 | Suspend incomplete Tasks durably and resume the same checkpoint through Understanding after authorized human input |
