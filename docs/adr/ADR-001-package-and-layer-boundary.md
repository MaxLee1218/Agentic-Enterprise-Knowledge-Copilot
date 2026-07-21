# ADR-001: Package Naming and Layer Boundary

## Status

Accepted

## Date

2026-07-21

## Context

The system spans task orchestration, domain contracts, governed tools, knowledge retrieval,
analytical database access, deterministic analytics, reporting, evidence, approvals, persistence,
model providers, and external interfaces. Without explicit boundaries, modules can import concrete
implementations directly, business rules can leak into frameworks, and new capabilities can bypass
policy, evidence, audit, or verification.

The repository already establishes `src/copilot` as its only production Python package and uses
responsibility-oriented subpackages such as `contracts`, `agent`, `services`, `tools`, `policies`,
`evidence`, `persistence`, `llm`, and `api`. Many of these are planned scaffold boundaries, not
implemented capabilities. Introducing top-level peer packages such as `src/domain` and
`src/application` would contradict the repository layout and recreate the package-naming ambiguity
this decision is intended to prevent.

The Supplier Quality Analysis v1.0 design is frozen. This ADR organizes dependencies around that
design; it does not alter its contracts, state machine, tools, approval rules, or persistence
semantics.

## Decision

### Package name

All production Python modules use the single import namespace `copilot` and live under
`src/copilot`. The distribution name and console command may retain the full product branding, but
neither creates another import package. Tests mirror `copilot` boundaries where practical.

### Conceptual layers and repository mapping

The architecture uses the following conceptual layers inside the `copilot` namespace:

```text
src/copilot/
|- contracts/                         # Domain
|- services/ and agent/               # Application orchestration
|- policies/ and governed tools core  # Application governance
|- persistence/, llm/, evidence/,     # Infrastructure adapters
|  observability/, tools/<capability>/
|- api/, cli/, future mcp endpoints   # External interfaces
`- bootstrap/                         # Future composition root
```

This mapping governs future work; it does not authorize empty scaffolds as implemented behavior or
require an immediate directory rewrite.

#### Domain layer

The domain layer contains entities, value objects, aggregates, domain rules, errors, and stable
interfaces that express business meaning. In this repository, `copilot.contracts` is the primary
domain boundary. Examples include `TaskRequest`, `TaskContract`, `TaskPlan`, `TaskState`,
`ToolDefinition`, `EvidenceItem`, `ApprovalRequest`, `Artifact`, and `TaskError`.

Domain code must not depend on a database driver, HTTP framework, LLM or MCP SDK, vector database,
filesystem implementation, or external API client. It receives no authorization from prompt or
model text.

#### Application layer

The application layer contains use cases, workflows, planning, routing, policy coordination,
governed execution, evidence aggregation, verification, and finalization. Its approved homes are
`copilot.services`, `copilot.agent`, `copilot.policies`, and the generic portions of
`copilot.tools` such as its ports, registry, and executor.

Application code coordinates domain objects and invokes ports. It must not import or instantiate a
concrete database repository, model provider, retrieval client, storage adapter, or renderer.
Agent nodes do not bypass the tool registry and executor.

#### Infrastructure layer

The infrastructure layer contains technical implementations of ports: database repositories,
vector or knowledge clients, LLM providers, file or object storage, audit and evidence stores,
observability exporters, and external API clients. These implementations belong in
`copilot.persistence`, `copilot.llm`, `copilot.evidence`, `copilot.observability`, or a concrete
capability subpackage under `copilot.tools`.

Infrastructure may depend on domain contracts and application-owned ports. Upper layers do not
depend on its concrete classes. Provider failures are normalized into stable internal errors at
the boundary.

#### Interfaces layer

The interfaces layer contains external entry points such as FastAPI, CLI, workers, and future
approved MCP endpoints. Its approved homes include `copilot.api`, `copilot.cli`, and the protocol
edge of `copilot.mcp`.

Interfaces authenticate and validate external input, translate it into application commands, call
application services, and map results or typed failures back to transport responses. They contain
no business workflow and do not call databases, retrieval clients, renderers, or tools directly.

#### Tools capability boundary

Tools cross a deliberate internal boundary. Generic tool protocols, definitions, registration,
authorization handoff, bounded execution, output validation, evidence recording, and audit form an
application-facing governed runtime. Knowledge, database, analytics, and reporting implementations
are infrastructure adapters behind that runtime.

All tool calls pass through the registry and executor. Tools do not call other tools, alter task
state, create approvals, or broaden scope. This distinction prevents `tools` from becoming a
parallel application layer.

#### Bootstrap and composition root

`copilot.bootstrap` is the designated future composition root. It creates repositories, adapters,
tools, model clients, executors, policies, and services; registers approved capabilities; injects
dependencies into external interfaces; and owns resource startup and shutdown.

No business module creates infrastructure objects directly. The composition root may import all
layers solely to wire them together and contains no business decisions. The package does not yet
exist, so current entry points must not be represented as a complete task runtime.

### Dependency direction

Source dependencies point inward:

```text
Interfaces -> Application -> Domain
Infrastructure -> application-owned ports and Domain
Bootstrap -> all layers for construction only
```

Runtime calls can travel from an injected application port to its infrastructure implementation,
but the implementation remains replaceable and the application does not import it.

The detailed dependency matrix, runtime flow, composition responsibilities, and transaction rules
are maintained in [`architecture.md`](../architecture.md).

## Alternatives Considered

### Alternative 1: Flat packages by technical noun

```text
services/
models/
utils/
```

This was rejected because ownership and dependency direction are unclear, `utils` tends to become
an unrestricted coupling point, and framework or provider details can easily leak into business
behavior.

### Alternative 2: Feature-only structure

```text
task/
rag/
report/
```

Feature grouping can be useful within a stable layer, but it is not the primary boundary here. A
feature-only layout would duplicate policy, approval, evidence, audit, and execution behavior or
allow each feature to invent its own path to infrastructure. Cross-cutting contracts and governed
execution must remain consistent across capabilities.

### Alternative 3: Multiple top-level layer packages

```text
src/domain/
src/application/
src/infrastructure/
src/interfaces/
```

This makes conceptual layers visually explicit but was rejected for this repository because
`AGENTS.md` mandates production code under `src/copilot`, existing imports already use that stable
namespace, and a second package scheme would create migration and packaging ambiguity without
changing the dependency rules. The concepts are instead mapped to explicit `copilot` subpackages.

## Consequences

Positive consequences:

- package ownership and allowed dependencies are predictable;
- domain and application tests can replace infrastructure with controlled implementations;
- tools and providers can be extended without changing orchestration call sites;
- policy, approval, evidence, audit, and verification remain shared mandatory boundaries;
- external interfaces and future protocols cannot silently create parallel execution paths;
- architectural changes become visible through ADR and design review.

Negative consequences:

- composition and port definitions require more initial structure than direct construction;
- contributors must distinguish generic tool runtime code from concrete tool adapters;
- enforcement initially depends on review and static import checks that have not yet been added;
- some current responsibility-oriented packages do not align one-to-one with conceptual layers,
  so their boundary must be judged by the class or module responsibility.

## Related Documents

- [Current architecture rules](../architecture.md)
- [ADR process](README.md)
- [Repository contributor instructions](../../AGENTS.md)
- [Frozen Supplier Quality Analysis v1.0 baseline](../design/design_baseline.md)
- [Domain contracts](../domain-contracts.md)
