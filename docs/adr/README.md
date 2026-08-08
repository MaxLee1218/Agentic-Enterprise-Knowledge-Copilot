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

An ADR does not override the frozen Supplier Quality Analysis v1.1 design. A decision that changes
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
| [ADR-003](ADR-003-llm-provider-and-structured-output.md) | Accepted | 2026-07-31 | Use replaceable structured LLM providers with deterministic validation, repair, and replan gates |
| [ADR-004](ADR-004-approval-edit-resolution.md) | Accepted | 2026-08-02 | Resolve bounded approval edits with complete replacement arguments and checkpoint resume |
| [ADR-005](ADR-005-local-observability-boundary.md) | Accepted | 2026-08-06 | Use injected provider-independent observability with bounded local logs, spans, metrics, and analysis |
| [ADR-006](ADR-006-deployment-persistence-boundary.md) | Accepted | 2026-08-07 | Separate Copilot PostgreSQL persistence from the enterprise business database and use explicit migrations |
| [ADR-007](ADR-007-stage-18-mcp-readiness-boundary.md) | Accepted | 2026-08-08 | Preserve frozen v1.1 scope and require Stage 17.1 hardening gates before any separate Stage 18 MCP work |
