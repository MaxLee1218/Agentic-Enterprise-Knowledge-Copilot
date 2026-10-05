# Recruiter and Interviewer Overview

## The short version

Agentic Enterprise Knowledge Copilot is a portfolio project demonstrating how an enterprise AI
agent can complete bounded business tasks without allowing model text to become execution
authority.

Instead of returning an unsupported chat answer, the system turns a natural-language request into
a durable Task, validates a plan, applies permissions and approvals, executes allowlisted tools,
records evidence, generates a report and independently verifies the result before publishing it.

The project currently supports two read-only business workflows:

- quarterly Supplier Quality Analysis;
- Accounts Payable compliance and exception investigation.

## What the project demonstrates

### Product engineering

- A chat-first React workspace for submission, clarification, approval, execution inspection and
  verified artifact delivery.
- Stable FastAPI contracts and generated TypeScript API types.
- Deterministic JSON and PDF reports rather than chat-only output.

### Agent and backend engineering

- LangGraph orchestration with typed state and bounded transitions.
- Deterministic compilation of untrusted model proposals into validated executable plans.
- Transactional asynchronous submission, independent Workers, leases, heartbeats and fencing.
- Durable clarification, approval, cancellation, checkpoint and recovery behavior.

### Enterprise governance

- Deny-by-default permissions, tenant isolation and purpose/data-scope enforcement.
- Allowlisted read-only query templates instead of arbitrary SQL.
- Exact approval binding and tightening-only approval edits.
- Separate document, database, calculation, audit and artifact lineage.
- Independent evidence, numeric, citation, safety and integrity verification.

### Engineering quality

- 696 backend test functions across unit, integration, contract, smoke and security suites.
- Real PostgreSQL migration, persistence and recovery gates.
- Real-browser Playwright E2E coverage for the React/FastAPI boundary.
- Deterministic Supplier Quality and Accounts Payable evaluation baselines, plus protocol-boundary
  safety checks for the future MCP extension.
- 23 Architecture Decision Records explaining significant trade-offs.

## Suggested review paths

### Two-minute recruiter path

1. View the [README product tour](../README.md#product-tour).
2. Read [The 30-second overview](../README.md#the-30-second-overview).
3. Review the [Engineering proof](../README.md#engineering-proof).
4. Read [Project ownership](../README.md#project-ownership).

### Five-minute engineering path

1. Start with the [README architecture](../README.md#architecture).
2. Inspect the [architecture rules](architecture.md).
3. Review the [async runtime authority model](async-runtime-architecture.md).
4. Inspect [Evidence and Verification](evidence-and-verification.md).
5. Open the [ADR index](adr/README.md) and the [CI workflow](../.github/workflows/ci.yml).

### Security-focused path

1. [Security Model](security-model.md)
2. [Database Tool](database-tool.md)
3. [MCP Security](mcp-security.md)
4. [`tests/security`](../tests/security/)

## My role

I designed and implemented the repository end to end as a portfolio project, covering contracts,
agent orchestration, execution governance, persistence, the asynchronous runtime, evidence and
verification, reporting, frontend experience, tests, evaluation and documentation.

The primary engineering thesis is:

> Probabilistic reasoning can propose and interpret, but deterministic code must own authority,
> validation, calculation, state transitions and completion.

## Three decisions worth discussing in an interview

### 1. Why compile plans instead of executing model output?

The model produces a lightweight `ProposedPlan` without authorization metadata. A deterministic
compiler binds it to the selected domain manifest, registered tool profile, schema and canonical
dependencies. This prevents a plausible-looking model response from inventing executable
authority.

See [ADR-018](adr/ADR-018-deterministic-plan-compilation.md).

### 2. Why is the Task database authoritative?

At-least-once delivery means a Queue message can be duplicated or delayed. Worker memory can be
lost, and a checkpoint can be incomplete. Keeping Task/runtime state authoritative in PostgreSQL
allows delivery, takeover and recovery decisions to be fenced and reconciled deterministically.

See [ADR-012](adr/ADR-012-async-task-submission-model.md),
[ADR-014](adr/ADR-014-worker-lease-fencing.md) and
[ADR-015](adr/ADR-015-checkpoint-recovery-authority.md).

### 3. Why is report generation not the final step?

A renderer can succeed while the report is still unsupported, numerically inconsistent or linked
to incomplete evidence. The generated report is therefore a candidate artifact until independent
verification checks evidence coverage, citations, numeric consistency, safety and file integrity.

See [Evidence and Verification](evidence-and-verification.md).

## Honest maturity statement

The local and synthetic vertical slices are implemented and extensively tested. This is not a
claim of production deployment readiness. Production identity, live enterprise data and policy
ownership, centralized telemetry, shared artifact storage, capacity evidence and formal HA/DR
remain environment-specific responsibilities.
