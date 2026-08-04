# Agentic Enterprise Knowledge Copilot

Production-oriented Python foundation for a governed, evidence-backed enterprise task completion
system. This milestone provides configuration, CLI, API health checks, frozen v1.1 domain
contracts, a governed tool-runtime foundation, HTTP Enterprise RAG and read-only SQLite database
adapters, and one
deterministic offline Supplier Quality workflow with evidence, audit, retries, verification, and
production deterministic PDF/JSON report generation, and a LangGraph workflow with SQLite
checkpoint/restart recovery. Stage 11 adds optional structured LLM task understanding and planning
with DeepSeek and deterministic MockLLM providers, bounded plan repair/replan, and an unchanged
policy/Registry/Executor execution boundary.

Stage 12 implements v1.1 Human-in-the-loop approval with durable `APPROVE`, bounded `EDIT`, and
`REJECT` decisions plus checkpoint resume. It intentionally does not add CAPA creation or any
business write operation; the frozen four-tool scope remains unchanged.

Stage 13 adds stable `/v1` task-management APIs and matching local CLIs for task submission,
status, steps, Evidence, Artifact metadata/download, and cooperative cancellation. These adapters
reuse the same application services and do not create a second Graph, Registry, or execution path.

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
python scripts/run_task.py \
  "Analyze Q2 2026 supplier quality deviations, identify the highest-risk suppliers, compare them with the Supplier Quality Manual, and generate a JSON management report."
uvicorn copilot.bootstrap.api:app
```

The positional task and `--task` form are equivalent:

```bash
python scripts/run_task.py \
  --task "Analysiere die Lieferantenqualität im 2. Quartal 2026 und erstelle einen JSON-Bericht."
```

Only the natural-language task is required. Optional `--output-format`, `--max-steps`,
`--read-only`, `--require-approval`, and `--session-id` values can select an already-supported
format or tighten server constraints; they cannot expand policy, permission, approval, tool, or
step limits.

The HTTP task endpoint uses the same application service and LangGraph:

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Analyze Q2 2026 supplier quality deviations, identify the highest-risk suppliers, compare them with the Supplier Quality Manual, and generate a JSON management report.",
    "output_format": "json",
    "read_only": true
  }'
```

`POST /v1/tasks` requires only `task`; the caller does not supply a goal, entities, time range
object, deliverables, plan, tool names, SQL, or tool arguments. Task Understanding creates the
`TaskContract`, the Planner creates the `TaskPlan`, deterministic Plan Validator checks it, and
Policy/Approval plus ToolExecutor remain mandatory. The original task text is persisted
before model execution and is passed unchanged to Task Understanding.

When a submission returns 202, use `pending_approval_id` with the approval API. The authorized
GET endpoint returns the complete proposed tool input. `edit` must send that complete object and
may only reduce `knowledge_search.top_k` or `database_query.row_limit`; a valid edit creates a new
resolved action fingerprint and resumes without replaying successful prerequisites.

```bash
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/approvals/APPROVAL_ID

curl -X POST http://127.0.0.1:8000/v1/tasks/TASK_ID/approvals/APPROVAL_ID \
  -H "Content-Type: application/json" \
  -d '{"action":"approve","reason":"Reviewed and approved"}'
```

See [Stage 12 Human-in-the-loop](docs/stage-12/human-in-the-loop.md) and the
[HTTP API](docs/api.md) for edit/reject payloads, permissions, persistence, recovery, and errors.

Completed or failed submissions execute synchronously inside `POST /v1/tasks` and return
`201 Created` with the reached state. A durable approval pause returns `202 Accepted` with
`WAITING_APPROVAL`; it is not a claim that a background task queue exists. Task-management calls:

```bash
curl http://127.0.0.1:8000/v1/tasks/TASK_ID
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/steps
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/evidence
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts
curl -OJ http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts/ARTIFACT_ID
curl -X POST http://127.0.0.1:8000/v1/tasks/TASK_ID/cancel
```

Artifact metadata and task-creation responses expose an Artifact ID and safe filename, never the
local storage path. Downloads are streamed only after task ownership, Artifact ownership, root
containment, size, and checksum validation.

Local task inspection and the deterministic Stage 13 smoke test use the same services:

```bash
python scripts/inspect_task.py TASK_ID
python scripts/inspect_task.py TASK_ID --json
python scripts/smoke_agent.py
```

CLI exit codes are `0` for success or an expected approval pause, `1` for task/operation failure,
`2` for invalid CLI input, `3` for configuration failure, `4` for an unavailable dependency, and
`5` for permission/approval denial. The current CLI/API identity is an explicitly local Demo
Identity and must be replaced by a trusted authentication adapter for deployment.

The service health endpoint remains available at `GET /health`.

The frozen Supplier Quality v1.1 Artifact contract supports PDF and JSON, not Markdown. An
explicit year and quarter remain mandatory. When they are missing, Task Understanding records
the missing information and the frozen state machine terminates the Task as `FAILED`; a corrected
request starts a new Task because v1.1 has no multi-turn clarification-resume state.

The standalone Enterprise RAG checks use the real HTTP adapter without starting the complete
workflow:

```bash
python scripts/check_rag_health.py
python scripts/ask_knowledge.py \
  --question "What is the supplier quality deviation procedure?" \
  --show-evidence
```

See the Chinese
[Knowledge Tool Verification Guide](docs/knowledge_tool_verification_guide.md) for macOS,
Windows PowerShell, live integration tests, exit codes, and troubleshooting. The composed
workflow keeps its deterministic mock in development and test environments; `APP_ENV=production`
registers the HTTP Knowledge Tool and SQLAlchemy Database Tool while preserving the frozen v1.1
input/output contracts.

Create or reset the deterministic SQLite demo database before running a real database workflow:

```bash
python scripts/seed_demo_database.py
```

The Database Tool accepts only the frozen query-template contract, never caller-provided raw SQL.
See [Database Tool](docs/database-tool.md) for the schema, read-only boundary, Evidence model, and
PostgreSQL migration notes.

The composed development/test API and CLI use the bounded offline structured mock provider and
run without a network or external model service. They write a verified
`QUALITY_ANALYSIS_REPORT_JSON` file beneath `ARTIFACT_DIR`
(default `data/artifacts`) while the CLI prints only its Artifact ID and safe filename. Pass
`--report-format PDF` to generate and independently verify the frozen PDF alternative. Markdown
and HTML are intentionally not emitted because the frozen Supplier Quality v1.1 Artifact contract
supports only PDF and JSON. See the
[Deterministic Workflow](docs/deterministic-workflow.md) for execution, retry, Evidence, failure,
and compatibility details, and [Deterministic Report Tool](docs/report-tool.md) for report model,
rendering, and Artifact integrity behavior.

After report generation, deterministic Evidence, lineage, deliverable, citation, numeric, safety,
and Artifact verifiers produce a persisted structured result. A Task reaches `COMPLETED` only when
that result has no Errors. See
[Evidence Ledger and Deterministic Verification](docs/evidence-and-verification.md).

LangGraph supplies explicit nodes, routing, bounded tool loops, SQLite checkpointing, and
task/tenant-scoped resume. Domain facts, Evidence, Artifact metadata, leases, and audit remain in
separate business tables rather than using checkpoints as the source of truth. See
[Deterministic LangGraph Workflow](docs/langgraph-workflow.md).

An LLM planning service is injected by the API/CLI composition root. It produces only candidate
understanding and plans; deterministic Contract binding, PlanValidator, policy, approval,
ToolExecutor, Evidence, and verification remain mandatory. See
[Structured LLM Architecture](docs/llm-architecture.md) and
[Task Understanding and Planning](docs/task-understanding-and-planning.md).

The real DeepSeek smoke test is opt-in and stops after deterministic plan validation:

```bash
LLM_PROVIDER=deepseek LLM_API_KEY=... python scripts/smoke_llm_planner.py
```

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

The adapters in `tests/mocks` remain narrow Tool Runtime test doubles. Development/test composition
uses offline knowledge and database fixtures, the real deterministic Analytics Tool, and the real
deterministic Report Tool. Production composition replaces knowledge and database fixtures with
the HTTP Knowledge Tool and SQLAlchemy Database Tool. Reporting remains network- and model-free.

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

All current tests run offline. Database integration tests use isolated disposable SQLite files;
no live enterprise database, LLM, or network service is required.

## Agent Evaluation

Stage 14 provides a reproducible offline evaluation system that executes 15 Supplier Quality cases
through the same Task Service, LangGraph, policy, Registry/Executor, Evidence, approval, reporting,
and verification path used by the API and CLI:

```bash
python evaluation/run_eval.py
python evaluation/run_eval.py --tag smoke
python evaluation/run_eval.py \
  --baseline evaluation/baselines/supplier_quality_v1.json \
  --fail-on-regression
```

Reports are written to `evaluation/reports/latest.json`, `latest.md`, and an immutable run
directory. Dataset authoring, metric formulas, exit codes, baseline updates, and reproducibility
rules are documented in [Offline Agent Evaluation](docs/evaluation.md).

The checked-in fixed Mock baseline was generated on 2026-08-03 at commit
`5b4d403a9b1138a39cdd470cdd92908ecc06023b`, using dataset `1.0.0` with hash
`sha256:9c31d359cc2b8e3d178dbe9d4bde11b96df056dd1ca4adb432b85d75a798dc92`, mode
`mock`, and seed `42`.

| Baseline metric | Result |
|---|---:|
| Cases satisfying their oracle | 100.00% (15/15) |
| Initial / final plan validity | 100.00% (10/10) / 100.00% (10/10) |
| Tool selection accuracy | 100.00% (15/15) |
| Tool execution success | 97.14% (34/35; one expected transient failure) |
| Evidence coverage / citation correctness | 100.00% (28/28) / 100.00% (32/32) |
| Numeric accuracy | 100.00% (4/4) |
| Safety violation rate | 0.00% (0/6) |
| Replan recovery | 100.00% (1/1) |
| Average steps / measured wall latency | 2.73 / 38.73 ms |
| Fixed Mock token usage / estimated cost | 5,400 / USD 0.00 |

Known failures: none. Controlled `FAILED`, `CANCELLED`, and `WAITING_APPROVAL` outcomes count as
successful only when required by the case oracle. This Mock baseline validates deterministic
regression behavior; it is not a claim about live-model, live-RAG, production-data, latency,
token, cost, or security performance.

## Current limitations

- Cancellation is cooperative at persisted state/Graph boundaries; it cannot forcibly interrupt
  an arbitrary in-flight external HTTP or database call.
- There is no production task queue, exactly-once external-call guarantee, cross-database atomic
  transaction, object-store download implementation, or automatic crash recovery for in-memory
  deployments.
- CAPA writes, business database writes, email, procurement actions, Web UI, MCP, and Multi-Agent
  execution remain out of scope.
