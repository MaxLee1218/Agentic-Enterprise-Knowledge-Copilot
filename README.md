# Agentic Enterprise Knowledge Copilot

Production-oriented Python foundation for a governed, evidence-backed enterprise task completion
system. This milestone provides configuration, CLI, API health checks, frozen v1.0 domain
contracts, a governed tool-runtime foundation, and one deterministic offline Supplier Quality
workflow with evidence, audit, retries, verification, and JSON Artifact generation. Agent graph
execution, dynamic planning, real retrieval/database/analytics/report adapters, and durable
persistence remain future work.

The typed Supplier Quality Analysis contracts and lifecycle are documented in
[Domain Contracts](docs/domain-contracts.md).

Architecture boundaries and their decision history are documented in
[Architecture Overview](docs/architecture.md) and the
[Architecture Decision Record index](docs/adr/README.md).

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
python scripts/run_task.py \
  --task supplier-quality-analysis \
  --supplier-id SUP-001 \
  --material-id MAT-001 \
  --time-range 2026-Q1
uvicorn copilot.api.app:app
```

The service health endpoint is available at `GET /health`.

The fixed workflow runs without an LLM, database, network, or other external service. It writes a
verified `QUALITY_ANALYSIS_REPORT_JSON` file beneath `ARTIFACT_DIR` (default `data/artifacts`) and
prints its path. Markdown is intentionally not emitted because the frozen Supplier Quality v1.0
Artifact contract supports only PDF and JSON. See the
[Deterministic Workflow](docs/deterministic-workflow.md) for execution, retry, Evidence, failure,
and compatibility details.

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

The four adapters in `tests/mocks` remain narrow Tool Runtime test doubles. The composed fixed
workflow uses deterministic offline adapters in `copilot.tools.mock_supplier_quality`; these do
not implement enterprise retrieval, database access, or external report generation.

## Verify

### Quality Gates

Every push and pull request must pass the consolidated GitHub Actions CI pipeline:

- ✓ Ruff lint and format checks
- ✓ Mypy strict type checking
- ✓ Pytest unit, integration, contract, and smoke tests
- ✓ Offline evaluation smoke test
- ✓ Documentation governance check
- ✓ AST-based architecture dependency check
- ✓ Editable install and distribution build verification

The same gates can be run locally without LLM or enterprise data services:

```bash
ruff check .
ruff format --check .
mypy
pytest
python evaluation/run_eval.py --smoke
python scripts/check_docs.py
python scripts/check_architecture.py
python -m build
```

All current tests run offline and do not require an LLM, database connection, or network service.
