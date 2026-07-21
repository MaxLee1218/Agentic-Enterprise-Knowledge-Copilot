# Architecture Overview

This document states the architecture rules that apply to the current repository. It describes
the system as it exists and the boundaries that new implementation must preserve. The rationale
for choosing these boundaries is recorded in
[ADR-001](adr/ADR-001-package-and-layer-boundary.md). The frozen Supplier Quality Analysis v1.0
documents under [`design/`](design/) remain the sole authority for that scenario's behavior.
Machine-checked governance covers the dependency matrix and calling direction. It also covers each
layer boundary, composition root, and transaction boundary where static analysis can enforce it.

Most packages are currently scaffolds. Today, the stable domain contracts and governed tool
runtime are implemented; the agent graph, application services, concrete enterprise adapters,
durable persistence, and MCP interoperability are not. A path in this document identifies an
approved boundary, not proof that the capability is implemented.

## 1. System Architecture

All production Python code belongs to the single import package `copilot` under `src/copilot`.
The distribution and console command may use the product name
`agentic-enterprise-knowledge-copilot` / `enterprise-copilot`, but those names do not create
additional Python packages.

The conceptual layers are:

```text
External actor
      |
      v
Interfaces (api, cli; future MCP endpoint)
      |
      v
Application (services, agent orchestration, policies, governed execution)
      |
      v
Domain (contracts and deterministic domain rules)
      ^
      |
Infrastructure implementations (persistence, LLM, evidence storage, capability adapters)

Bootstrap / composition root constructs and connects all of the above.
```

Dependency inversion explains the upward arrow: application code depends on ports expressed as
stable contracts or protocols. Infrastructure implements those ports and is injected at startup.
Application code does not import a PostgreSQL repository, model SDK, vector database client, or
renderer implementation directly.

### Current package map

| Conceptual responsibility | Repository packages | Status and boundary |
|---|---|---|
| Domain | `copilot.contracts` | Implemented v1.0 typed contracts; provider- and framework-independent |
| Application | `copilot.services`, `copilot.agent`, `copilot.policies` | Approved boundaries; currently scaffolds except the initial policy adapter |
| Governed capability runtime | `copilot.tools.base`, `registry`, `executor`, `runner`, `schema` | Implemented application-facing port, registration, authorization, execution, evidence, and audit sequence |
| Capability adapters | `copilot.tools.knowledge`, `database`, `analytics`, `reporting` | Infrastructure edge; currently scaffolds, not working enterprise tools |
| Infrastructure | `copilot.persistence`, `copilot.llm`, `copilot.evidence`, `copilot.observability` | Mostly planned adapters; the in-memory evidence ledger and tool audit repository support the current runtime |
| Interfaces | `copilot.api`, `copilot.cli` | Minimal health API and dry-run CLI are implemented |
| Protocol boundary | `copilot.mcp` | Future Phase 5 boundary; scaffold only |
| Bootstrap | `copilot.bootstrap` | Designated future composition root; it does not exist yet |
| Configuration | `copilot.config` | Typed environment configuration consumed at startup and infrastructure edges |

`tools` is a governed capability boundary rather than permission to bypass the layers. Its generic
protocols and executor support application orchestration; concrete capability subpackages are
infrastructure adapters. Tools never call one another or mutate task state.

## 2. Package Dependency Rules

The matrix is read as “row may import column.” “Ports only” means stable protocols or contracts,
never a concrete adapter. Bootstrap is the sole wiring exception because it must see every
implementation it constructs.

| Consumer | Domain | Application | Infrastructure | Interfaces | Bootstrap |
|---|---:|---:|---:|---:|---:|
| Domain | Yes | No | No | No | No |
| Application | Yes | Yes | No | No | No |
| Infrastructure | Yes | Ports only | Yes | No | No |
| Interfaces | DTOs only | Yes | No | Yes | No |
| Bootstrap | Yes | Yes | Yes | Yes | Yes |

Allowed dependency examples:

```text
copilot.services -> copilot.contracts
copilot.agent -> copilot.services / copilot.contracts / governed tool executor
copilot.persistence -> repository protocols and copilot.contracts
copilot.api -> application services and public request/response contracts
copilot.bootstrap -> concrete adapters and application services
```

Forbidden dependency examples:

```text
copilot.contracts -> FastAPI / database driver / model SDK
copilot.services -> concrete PostgreSQL repository
copilot.agent.nodes -> knowledge client / database connection / renderer
copilot.api.routes -> database / retrieval / tool implementation
tool implementation -> another tool implementation / TaskState mutation
MCP protocol handler -> business tool / database / retrieval system directly
```

Additional mandatory boundaries:

- Agent execution reaches capabilities only through the tool registry and executor.
- Policy and any required approval are checked before capability execution.
- Material outputs pass through evidence, audit, observability, and verification boundaries.
- SDK-specific MCP types stop at `copilot.mcp.protocol`; internal code uses
  `copilot.contracts.mcp`.
- Imported MCP capabilities, when Phase 5 is authorized, use stable server namespaces and the
  existing governed executor. Exported capabilities are deny-by-default and explicitly
  allowlisted.
- Framework-, vendor-, and database-specific types do not cross into domain contracts.

## 3. Runtime Call Direction

The intended governed task flow is:

```text
User or approved protocol client
  -> API / CLI / protocol adapter
  -> Application service
  -> Task understanding and classification
  -> Planner and plan validator
  -> Policy check and approval gate
  -> Tool registry and executor
  -> Concrete capability adapter
  -> Approved external system
  -> Tool result and evidence registration
  -> Report composition
  -> Verification
  -> Final task result and artifact references
```

Control flows from an external interface toward application orchestration and then to an injected
adapter. Results flow back through the same governed boundaries. Static source dependencies do not
reverse merely because a callback or port is invoked at runtime: the application owns the port,
and infrastructure supplies its implementation.

No route, agent node, protocol handler, or tool may create an alternate execution path around
policy, approval, registry, executor, evidence, audit, or verification.

## 4. Composition Root

The designated composition root is `src/copilot/bootstrap/`. When application wiring is
implemented, this package may contain small modules such as `container.py` and `factory.py` that:

- load and validate `copilot.config.Settings`;
- create repository, evidence, audit, model, storage, and external-service adapters;
- create and populate an instance-scoped `ToolRegistry`;
- construct policy, approval, executor, planner, verifier, and application services;
- inject those services into API, CLI, worker, and approved protocol entry points;
- own startup, shutdown, and resource cleanup.

The composition root is not a business service and contains no task decisions. Until it exists,
the API and CLI remain minimal entry points and must not be described as a composed task runtime.

Business modules receive dependencies explicitly:

```python
service = TaskService(
    task_repository=task_repository,
    tool_executor=tool_executor,
)
```

They must not construct infrastructure internally:

```python
class TaskService:
    repository = PostgreSQLTaskRepository()
```

Scripts under `scripts/` call reusable package APIs. They are not composition roots for business
logic.

## 5. Transaction Boundaries

### Task consistency boundary

The Task is the lifecycle consistency boundary, but it is not one mutable in-memory object tree.
The frozen v1.0 model stores related objects according to their own ownership and immutability:

```text
Task lifecycle boundary
  |- TaskRequest and versioned TaskContract
  |- versioned TaskPlan and immutable TaskStep definitions
  |- authoritative TaskState snapshot plus immutable state events
  |- StepResult and ToolResult references
  |- EvidenceItem references (content belongs to the Evidence Ledger)
  |- ApprovalRequest references
  `- TaskResult and Artifact references
```

State changes use compare-and-swap or equivalent optimistic concurrency. Each accepted transition
updates the authoritative `TaskState` version and appends its immutable audit event consistently.
Plans, tool attempts, approvals, evidence, and artifacts are appended or versioned; they are not
silently overwritten as part of a state update.

Finalization is a distinct commit boundary: a verified `TaskResult`, final artifact references,
and the transition to `COMPLETED` must be committed atomically or by an equivalent recoverable
protocol. A commit conflict leaves the prior state authoritative and is safe to retry. Repository
interfaces define these units; route handlers and tool adapters do not own transactions.

### External side-effect boundary

Knowledge stores, analytical databases, LLMs, artifact storage, file systems, and future MCP
servers are outside the Task transaction. A database transaction cannot be held open across these
calls. Each call requires:

- an attempt timeout and the Task's overall deadline;
- a stable idempotency key and explicit retry eligibility;
- typed failure normalization and bounded retry or replanning;
- checkpoint and recovery metadata sufficient to find an already committed result;
- policy and approval validation before access;
- evidence lineage, append-only audit, and structured observability;
- safe handling of late results after cancellation or a terminal transition.

Artifact generation uses temporary storage followed by atomic commit where supported. Recovery
checks for an already committed result before repeating work. Compensation means invalidating or
cleaning an uncommitted artifact and recording the outcome; it never rewrites immutable evidence
or audit history.

## 6. Extension Rules

### Adding a tool

Every new tool requires:

1. A concrete, approved use case and measurable success criteria.
2. Versioned `ToolDefinition`, input/output contracts, typed failures, risk, timeout, approval, and
   idempotency metadata.
3. A capability adapter under the appropriate `copilot.tools` subpackage that implements the
   generic tool port.
4. Registration through `ToolRegistry` and execution only through `ToolExecutor`.
5. Policy, tenant, purpose, data-scope, and approval rules evaluated separately from execution.
6. Evidence lineage, audit, latency/outcome observability, and bounded output handling.
7. Unit tests plus integration, contract, smoke, and safety coverage proportionate to the boundary.
8. Documentation and evaluation cases; an ADR when the change affects architecture, contracts,
   security, persistence, or an externally visible boundary.

For Supplier Quality Analysis v1.0, only the four tools and exact behavior in the frozen design are
authorized. A broader tool or changed contract requires the documented design-change process
before implementation.

### Adding other modules

- A use case belongs in `services` or a distinct, testable `agent` node and depends on ports.
- A vendor client, repository, storage provider, or renderer belongs at an infrastructure edge.
- A new transport belongs in `api`, `cli`, or an approved protocol boundary and only translates
  external requests and failures.
- A shared field is added to a typed contract before behavior consumes it; incompatible changes
  require versioning.
- A new persistent field uses a migration and explicit repository model.
- A new business scenario, state transition, external side effect, MCP behavior, or broadened
  Supplier Quality scope requires design review and, when architectural, a new ADR.

## Related Documents

- [ADR index and process](adr/README.md)
- [ADR-001: Package Naming and Layer Boundary](adr/ADR-001-package-and-layer-boundary.md)
- [Frozen design baseline](design/design_baseline.md)
- [Domain contracts](domain-contracts.md)
- [Repository-wide contributor rules](../AGENTS.md)
