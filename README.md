# Agentic Enterprise Knowledge Copilot

Production-oriented Python foundation for a governed, evidence-backed enterprise task completion
system. This milestone provides configuration, CLI, API health checks, frozen v1.0 domain
contracts, tests, and development tooling. Agent execution, retrieval, database access, tool runtime,
and persistence behavior remain future work.

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
`enterprise_copilot.config.get_settings`. The committed `.env.example` contains safe local defaults;
`.env` is intended for local configuration and must not contain committed secrets.

## Run

```bash
enterprise-copilot --help
python scripts/run_task.py --task "Analyze supplier quality issue" --dry-run
uvicorn enterprise_copilot.api.app:app
```

The service health endpoint is available at `GET /health`.

## Verify

```bash
pytest
ruff check .
mypy src
```

All current tests run offline and do not require an LLM, database connection, or network service.
