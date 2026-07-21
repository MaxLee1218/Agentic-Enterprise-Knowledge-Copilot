# Agentic Enterprise Knowledge Copilot

Production-oriented Python foundation for a governed, evidence-backed enterprise task completion
system. This milestone provides configuration, CLI, API health checks, frozen v1.0 domain
contracts, a governed tool-runtime foundation, tests, and development tooling. Agent graph
execution, real retrieval/database/analytics/report adapters, and durable persistence remain future
work.

The typed Supplier Quality Analysis contracts and lifecycle are documented in
[Domain Contracts](docs/domain-contracts.md).

## Requirements

- Python 3.11 or later

## Setup

```bash
python -m pip install -e '.[dev]'
cp .env.example .env
```

Application code reads configuration only through
`copilot.config.get_settings`. The committed `.env.example` contains safe local defaults;
`.env` is intended for local configuration and must not contain committed secrets.

## Run

```bash
enterprise-copilot --help
python scripts/run_task.py --task "Analyze supplier quality issue" --dry-run
uvicorn copilot.api.app:app
```

The service health endpoint is available at `GET /health`.

## Tool Runtime

The runtime under `src/copilot/tools` treats each enterprise capability as a registered plugin.
Every invocation uses the frozen `ToolCall`, `ToolDefinition`, `ToolResult`, `TaskError`, and
`EvidenceItem` contracts and follows this boundary sequence:

```text
Registry lookup -> input validation -> policy/approval authorization -> bounded execution
  -> output validation -> evidence registration -> append-only audit -> ToolResult
```

`ToolExecutor` depends only on protocols for the tool, authorizer, evidence recorder, and audit
sink. It contains no knowledge, database, analytics, or reporting branches. The supplied default
authorizer denies every call; an application must explicitly inject a policy implementation that
validates tenant, user, scope, plan version, and approval binding.

To add a real v1 adapter:

1. Implement the `Tool` protocol and expose one frozen, versioned `ToolDefinition`.
2. Return `ToolExecutionOutput` with a schema-conforming payload and minimized Evidence drafts.
3. Register the adapter in an instance-scoped `ToolRegistry` configured for its approved name and
   risk level.
4. Compose `ToolExecutor` with the production policy engine, durable Evidence Ledger, and durable
   Audit Repository.
5. Add unit, boundary, contract, and smoke coverage for success, denial, validation, timeout,
   dependency failure, empty-result, and lineage behavior.

The four adapters in `tests/mocks` are offline test doubles only. They do not implement enterprise
retrieval, database access, analytics, or report generation.

## Verify

```bash
pytest
ruff check .
mypy src
```

All current tests run offline and do not require an LLM, database connection, or network service.
