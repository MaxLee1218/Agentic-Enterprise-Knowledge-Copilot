# ADR-023: Bounded Dynamic Supplier Quality Planning

## Status

Accepted

## Date

2026-10-03

## Context

ADR-018 introduced a safe non-executable `ProposedPlan`, but the Supplier compiler discarded its
capability and dependency choices and regenerated the same four-step plan. The Planner therefore had
no causal influence on execution topology.

## Decision

Supplier Quality keeps the four existing capabilities as an allowlist. Database query, deterministic
analysis and report generation remain the mandatory report chain. Knowledge search is conditional:
it is mandatory for a trusted policy-comparison Contract and absent otherwise. The proposal must
match the Contract capability set exactly. The compiler binds
each proposed capability to Registry-owned tool metadata and server-owned retry policy while
preserving valid proposed dependencies. The validator enforces the mandatory chain, policy evidence,
DAG safety, limits and Registry consistency.

Reports without a knowledge step require database and calculation Evidence but not document Evidence.
Policy-comparison reports continue to require all three evidence types. No planner argument becomes
an executable argument; runtime inputs continue to come from Contract, trusted context, prior results
and Evidence.

## Consequences

Different valid proposals can compile to different canonical plans and tool trajectories. Existing
runtime, persistence, approval, recovery and execution authority remain unchanged. The system remains
a bounded plan-at-start agent, not an observation-conditioned ReAct agent. Pure retrieval and
non-report Supplier tasks remain unsupported. The old fixed factory remains only behind the legacy
structured-command compatibility service and is not used by natural-language API/CLI planning.
