# Agentic Enterprise Knowledge Copilot

Production-oriented Python foundation for a governed, evidence-backed enterprise task completion
system. This initial milestone provides configuration, CLI, API health checks, tests, and development
tooling only; agent, retrieval, database, and tool execution behavior remain future work.

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
