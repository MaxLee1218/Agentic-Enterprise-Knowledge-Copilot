# Agentic Enterprise Knowledge Copilot

This file defines repository-wide instructions for human contributors and AI coding agents. It applies to every file and directory in this repository unless a more specific `AGENTS.md` is added deeper in the tree.

## 1. Project Overview

Agentic Enterprise Knowledge Copilot is an enterprise AI agent system intended to turn traditional retrieval-augmented generation into a governed, traceable task-completion platform.

The system is designed to:

- Understand complex enterprise tasks and constraints.
- Plan and execute multi-step workflows.
- Retrieve knowledge from internal documents and approved sources.
- Query structured business databases through guarded tools.
- Perform reproducible data analysis.
- Generate professional reports and reusable artifacts.
- Require approval for sensitive or high-risk actions.
- Produce evidence-backed, auditable outputs.

The intended evolution is:

```text
Enterprise RAG Engine
        |
        v
Knowledge Retrieval
        |
        v
Agentic Enterprise Knowledge Copilot
        |
        v
Governed Task Completion System
```

The repository currently contains an initial project scaffold. Empty modules represent planned boundaries, not completed functionality. Do not describe an empty module as implemented.

## 2. Product Vision

The product must behave as an enterprise knowledge worker, not as a generic chatbot.

For a request such as “Analyze supplier quality issues in Q2 and generate a report,” the desired behavior is to:

1. Understand the business goal, scope, and constraints.
2. Identify missing information and required approvals.
3. Build and validate an execution plan.
4. Retrieve relevant policies and source documents.
5. Query approved operational data.
6. Calculate trends with explicit methods.
7. Analyze likely causes and distinguish facts from hypotheses.
8. Verify important claims and numeric results.
9. Generate a management-ready report.
10. Attach citations, query lineage, and calculation evidence.

The core product objective is:

> Convert governed enterprise knowledge into traceable, actionable decisions.

Product behavior must prioritize correctness, evidence, safety, and recoverability over fluent but unsupported answers.

## 3. System Architecture

The target system uses a layered, contract-first architecture:

```text
User / API Client
       |
       v
API and Service Layer
       |
       v
Agent Graph and State
       |
       +-------------------------+
       |                         |
       v                         v
Planning and Policy        Approval Workflow
       |
       v
Tool Registry and Executor
       |
       +--------------+---------------+----------------+
       |              |               |                |
       v              v               v                v
Knowledge Tool   Database Tool   Analytics Tool   Reporting Tool
       |              |               |                |
       +--------------+---------------+----------------+
                              |
                              v
                  Evidence and Verification
                              |
                              v
                  Artifacts and Final Response
```

Primary architectural responsibilities:

- `api/` exposes transport concerns and maps failures to stable API errors.
- `services/` coordinates application use cases without embedding transport logic.
- `agent/` owns task state, graph transitions, planning, routing, execution, and verification nodes.
- `contracts/` defines stable typed boundaries shared across layers.
- `tools/` contains capability adapters accessed only through the registry and executor.
- `policies/` evaluates permissions, risk, approvals, and data access before execution.
- `evidence/` records sources, lineage, citations, and validation results.
- `persistence/` stores task, audit, checkpoint, and artifact state.
- `llm/` isolates model providers, prompts, and structured-output handling.
- `observability/` provides logging, tracing, metrics, and correlation context.

Dependencies should point inward toward contracts and domain behavior. Provider-specific, database-specific, and web-framework-specific details must remain at the edges.

Every agent execution should follow this lifecycle:

```text
Input
  -> Task Understanding
  -> Classification
  -> Planning
  -> Plan Validation
  -> Policy Check
  -> Approval, when required
  -> Tool Execution
  -> Observation and Evidence Aggregation
  -> Verification
  -> Report Composition
  -> Final Response and Artifacts
```

## 4. Agent Design Principles

### 4.1 The agent is a planner, not a chatbot

The agent must understand intent, identify constraints, create an explicit plan, select tools, inspect intermediate results, and verify the final output. It must not hide a multi-step task behind a single unstructured model call.

### 4.2 Tool-first execution

When a claim depends on external knowledge, business data, calculation, or artifact generation, the agent must use the appropriate tool. The language model must not invent data that an approved tool is expected to provide.

### 4.3 Contract-first boundaries

Agent nodes and tools must exchange typed contracts rather than loosely structured dictionaries. Add or change a contract before adding behavior that depends on new fields.

### 4.4 Traceability by default

Material findings must be traceable to source documents, database queries or query fingerprints, tool outputs, calculation methods, and generated artifacts. Preserve the distinction between retrieved evidence, computed results, model inference, and user-provided information.

### 4.5 Policy before action

Check permissions, data classification, operational risk, and approval requirements before a tool executes. A post-hoc audit record does not replace pre-execution authorization.

### 4.6 Human approval for high-risk actions

Pause and request approval when an action is destructive, irreversible, externally visible, financially consequential, or accesses data beyond the established scope. Approval records must bind the approver, action, scope, and timestamp.

### 4.7 Deterministic control, bounded autonomy

Use deterministic code for validation, permissions, calculations, retries, and state transitions. Use model reasoning where interpretation is necessary. Bound loops, tool calls, retries, runtime, and cost.

### 4.8 Fail safely

Prefer a clear partial result or blocked state over an unsupported answer. Errors must be typed, observable, and safe to retry where appropriate. Never silently broaden access or bypass a policy check to complete a task.

### 4.9 Verify before reporting

Validate required evidence, citation coverage, numeric consistency, policy compliance, and artifact integrity before producing the final response.

## 5. Repository Structure

The target repository layout is:

```text
Agentic-Enterprise-Knowledge-Copilot/
├── src/copilot/
│   ├── api/                  # API application, dependencies, handlers, and routes
│   ├── agent/                # Agent graph, state, routing, and execution nodes
│   ├── contracts/            # Shared typed task, plan, tool, evidence, and error models
│   ├── tools/                # Tool framework and enterprise capability adapters
│   ├── policies/             # Permission, risk, approval, and data-access controls
│   ├── evidence/             # Evidence ledger, lineage, citations, and validation
│   ├── persistence/          # Repositories, checkpoints, and persistence models
│   ├── llm/                  # Model abstraction, provider adapters, prompts, and parsing
│   ├── observability/        # Structured logs, traces, metrics, and request context
│   ├── services/             # Application-level orchestration services
│   └── config.py             # Typed application configuration
├── scripts/                  # Local operational and smoke-test entry points
├── evaluation/               # Evaluation datasets, evaluators, runner, and reports
├── tests/                    # Unit, integration, contract, and smoke tests
├── data/                     # Local demo data, databases, and generated artifacts
├── docs/                     # Architecture, lifecycle, contracts, security, and ADRs
├── migrations/               # Versioned database migrations
├── reports/                  # Generated human-readable reports
├── .github/workflows/        # Continuous integration workflows
├── pyproject.toml            # Python project and tool configuration
├── docker-compose.yml        # Local service composition
└── AGENTS.md                 # Repository-wide contributor and agent instructions
```

Keep production code under `src/copilot`. Tests should mirror the source package where practical. Generated evaluation output belongs in `evaluation/reports`; generated business artifacts belong in `data/artifacts` or `reports` according to their purpose.

Do not add substantial behavior to `scripts/`. Scripts should call reusable package APIs.

## 6. Development Rules

1. Inspect existing contracts, architecture documents, tests, and call sites before modifying behavior.
2. Prefer small, incremental changes over broad rewrites.
3. Do not create abstractions without at least one concrete use case.
4. Keep every module focused on one responsibility with explicit inputs and outputs.
5. Preserve backward compatibility unless a breaking change is explicitly approved and documented.
6. Add dependencies only when the standard library and current dependencies are insufficient. Document the reason and operational impact.
7. Keep framework and provider details behind interfaces in `api/`, `tools/`, `llm/`, or `persistence/`.
8. Do not bypass the tool registry, policy engine, evidence ledger, or verifier from agent nodes.
9. Do not place business logic in route handlers, persistence models, prompts, or CLI scripts.
10. Treat prompts as versioned behavior: review, test, and evaluate prompt changes.
11. Use configuration for environment-specific values. Do not embed credentials, hosts, model names, or tenant identifiers in source code.
12. Update relevant documentation and evaluation coverage when behavior changes.
13. Do not claim a scaffolded or placeholder capability is implemented.

Each implemented module must provide:

- Clear typed input and output contracts.
- Appropriate error handling and safe failure behavior.
- Structured logging or tracing at meaningful boundaries.
- Unit tests and, when it crosses a boundary, integration or contract tests.
- Documentation for non-obvious decisions and externally visible behavior.

## 7. Coding Standards

### Python

- Use Python 3.11 or later.
- Use type hints for all public functions, methods, and class attributes.
- Prefer dataclasses or Pydantic models for structured domain data.
- Use `pathlib.Path` for filesystem paths.
- Use timezone-aware timestamps in UTC for persisted and exchanged data.
- Prefer enums or constrained literal types for finite state values.
- Keep functions short and explicit; extract logic when a function owns multiple responsibilities.
- Avoid mutable default arguments and hidden global state.
- Use async code only for genuinely asynchronous I/O paths; do not mix blocking work into the event loop.
- Raise domain-specific exceptions defined in `contracts/errors.py` at architectural boundaries.

### Naming and interfaces

- Use `snake_case` for modules, functions, and variables; `PascalCase` for classes and models; `UPPER_SNAKE_CASE` for constants.
- Name tool classes and operations by business capability, not by implementation vendor.
- Keep tool inputs and outputs serializable and versionable.
- Use dependency injection for databases, model clients, clocks, and external services.
- Make idempotency expectations explicit for commands that may be retried.

### Logging and observability

Important operations must emit structured records containing appropriate fields such as:

- `task_id`, `trace_id`, and `tenant_id` when available.
- Agent node or tool name.
- Outcome and typed error code.
- Latency, retry count, and token or cost measurements where applicable.
- Evidence or artifact identifiers rather than sensitive payloads.

Never log passwords, API keys, authorization tokens, raw secrets, unnecessary personal data, unrestricted document contents, or unredacted database results.

### Documentation

- Use docstrings for public APIs and non-obvious invariants.
- Record significant architectural decisions in `docs/adr/`.
- Update `README.md` for user-facing setup or workflow changes.
- Keep examples safe, minimal, and runnable when implementation exists.

## 8. Testing Requirements

Before merging, the complete configured test suite and static checks must pass.

Every implemented feature must include proportionate coverage:

- **Unit tests:** deterministic behavior, validation, state transitions, and error paths.
- **Integration tests:** interactions with databases, model adapters, tools, persistence, or API boundaries.
- **Contract tests:** compatibility of shared contracts, tool schemas, API payloads, and persistence boundaries.
- **Smoke tests:** one representative end-to-end path for operational confidence.

Testing rules:

1. A bug fix must include a regression test that fails without the fix.
2. Tests must not depend on live production services, production credentials, or nondeterministic external state.
3. Stub model outputs at unit boundaries; use a controlled test provider for integration tests.
4. Database tests must use isolated disposable data and must not mutate shared environments.
5. Validate both success and failure paths, including denial, timeout, malformed output, retry exhaustion, and partial evidence.
6. Test policy decisions separately from tool execution.
7. Test numeric calculations with explicit expected values and tolerances.
8. Test citation and evidence lineage for traceable outputs.
9. Keep smoke scripts thin and return a non-zero exit code on failure.
10. Do not weaken or delete tests merely to make a change pass.

When the toolchain is configured, the expected local checks should include commands equivalent to:

```text
pytest
lint check
format check
type check
evaluation smoke suite
```

Use the exact commands defined in `pyproject.toml` and continuous integration once those files are implemented.

## 9. Evaluation Framework

Evaluation is a product requirement, not an optional benchmark. Each material capability must define success criteria before release.

### Agent-level metrics

- Task success rate.
- Plan validity and plan quality.
- Tool selection accuracy.
- Tool argument accuracy.
- Approval-routing accuracy.
- Recovery rate after tool or model failure.
- End-to-end execution latency and cost.

### Retrieval and grounding metrics

- Recall@K, precision@K, and hit rate.
- Context precision and context recall.
- Answer relevance and faithfulness.
- Citation correctness and citation completeness.
- Unsupported-claim rate.

Retrieval implementations should be evaluated for dense, BM25, hybrid, and reranked retrieval where those strategies are supported. Chunking experiments may compare standard and parent-child approaches.

### Numeric and analytical metrics

- Exact match or tolerance-based numeric accuracy.
- Formula and aggregation correctness.
- Unit, time-window, and denominator correctness.
- Reproducibility of derived results.

### Safety and system metrics

- Policy compliance and dangerous-action rejection rate.
- Sensitive-data leakage rate.
- Cross-tenant isolation failures.
- p50 and p95 latency.
- Tool and model failure rates.
- Retry, timeout, and human-escalation rates.

Evaluation datasets must be versioned, documented, sanitized, and representative of real task categories. Store evaluator implementations in `evaluation/evaluators`, input cases in `evaluation/datasets`, and generated results in `evaluation/reports`.

Every evaluation report must record the code revision, dataset version, configuration, model/provider identifier, prompt version, timestamp, metric definitions, and known limitations. Do not compare runs whose inputs or metric definitions differ without explicitly disclosing the difference.

## 10. Data & Security Rules

### Secrets and configuration

- Load secrets from approved environment or secret-management systems.
- Keep `.env` files, credentials, tokens, certificates, and private keys out of version control.
- Maintain `.env.example` with names and safe descriptions only; never include real values.
- Fail startup clearly when required configuration is missing.

### Data access

- Apply least privilege and deny access by default.
- Enforce tenant, user, purpose, and resource scope at the data-access boundary.
- Classify sensitive data and redact it from logs, traces, prompts, fixtures, and artifacts unless explicitly required and authorized.
- Do not use production data in tests or evaluation datasets.
- Record data lineage and access decisions in the audit trail.
- Define retention and deletion behavior for tasks, checkpoints, evidence, and artifacts before persisting sensitive data.

### Database tools

- Use parameterized queries for values.
- Validate generated SQL against an allowlist and registered schema.
- Use read-only connections for analytical access by default.
- Apply row limits, statement timeouts, result-size limits, and resource limits.
- Allow safe read operations such as `SELECT`, `JOIN`, `WHERE`, `GROUP BY`, and approved aggregates.
- Reject `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, privilege changes, multi-statement execution, and equivalent mutations unless a separately designed capability receives explicit authorization and approval.
- Do not expose raw connection errors or database credentials to users or model prompts.

### Model and tool safety

- Treat retrieved documents, tool output, and user-provided content as untrusted input.
- Defend against prompt injection by separating instructions from data and enforcing policy in deterministic code.
- Validate structured model output before use.
- Limit tool inputs, outputs, time, retries, and accessible resources.
- Require an approval token for gated actions and verify that its scope matches the requested operation.
- Never let model text directly authorize an action.

### Auditability

Audit records should capture who requested an action, what was requested, what policy decision was made, which tools ran, which evidence supported the result, which approvals were used, and which artifacts were produced. Audit records must avoid unnecessary sensitive payloads and should be tamper-evident where required.

## 11. Git Workflow

Use short-lived branches with one of these prefixes:

- `feature/` for new capabilities.
- `bugfix/` for defect corrections.
- `experiment/` for isolated research that is not yet production behavior.
- `docs/` for documentation-only changes.
- `refactor/` for behavior-preserving restructuring.

Use Conventional Commit-style subjects:

```text
feat: add SQL analysis tool
fix: reject multi-statement database queries
docs: describe the approval lifecycle
test: add grounding evaluation cases
refactor: isolate evidence validation
```

Workflow rules:

1. Keep commits focused and reviewable.
2. Do not commit secrets, generated private data, local databases, caches, or large artifacts.
3. Rebase or update from the target branch before merge according to team policy.
4. Require passing tests, checks, and relevant evaluations before merge.
5. Describe behavioral changes, risks, migrations, configuration changes, and verification evidence in the pull request.
6. Obtain review for contract, security, policy, persistence, migration, or externally visible API changes.
7. Use migration files for persistent schema changes; never rely on manual production edits.
8. Do not force-push shared branches or rewrite published history without explicit coordination.

## 12. Future Extension Guidelines

Before adding a capability, classify it as one or more of:

- A new tool or external capability.
- A new agent behavior or graph transition.
- A new shared contract.
- A policy or approval rule.
- Infrastructure, persistence, or observability.
- An evaluation capability.

Then follow this sequence:

```text
Define the use case and success metrics
  -> Define or update typed contracts
  -> Assess security, policy, and approval requirements
  -> Design the smallest stable interface
  -> Implement behind the interface
  -> Add unit, integration, contract, and smoke coverage as applicable
  -> Add evidence and observability hooks
  -> Update documentation and ADRs
  -> Update evaluation datasets and metrics
  -> Verify compatibility and rollout behavior
```

Extension rules:

- Register new tools through `tools/registry.py`; do not hard-code tool selection in unrelated nodes.
- Keep provider-specific behavior behind adapters.
- Version contracts when compatibility cannot be preserved.
- Add new graph nodes only when they own a distinct, testable decision or transformation.
- Add policy rules in the policy layer, not in prompts.
- Add new persisted fields through explicit models and migrations.
- Design for cancellation, retry, idempotency, and partial failure when work crosses process or network boundaries.
- Add evaluation cases before promoting experimental behavior to the default path.
- Prefer feature flags or controlled rollout for high-impact changes.

Likely roadmap phases are:

1. Enterprise RAG and evidence foundation.
2. Agent graph, planning, execution, and memory/checkpointing.
3. Governed database, analytics, and reporting tools.
4. Authentication, tenant isolation, monitoring, deployment, and feedback loops.

Roadmap items are directional. Do not mark them complete without implemented code, tests, documentation, and evaluation evidence.

## 13. Common Tasks

### Add a new tool

1. Define typed input, output, and error contracts in `src/copilot/contracts`.
2. Implement the tool under the appropriate `src/copilot/tools` capability directory.
3. Register it in `src/copilot/tools/registry.py`.
4. Route execution through `src/copilot/tools/executor.py`.
5. Add permission, risk, approval, and data-access policies.
6. Record evidence, lineage, latency, and outcome metadata.
7. Add unit, integration, contract, safety, and smoke coverage as applicable.
8. Document the interface and update evaluation cases.

### Add or change an agent node

1. Define its responsibility, input state, output state, and allowed transitions.
2. Update contracts before relying on new state fields.
3. Implement the node in `src/copilot/agent/nodes`.
4. Wire it through `graph.py` and `routing.py` with bounded transitions.
5. Add policy and approval handling before side effects.
6. Test success, failure, retry, cancellation, and invalid-transition paths.
7. Add task-success and plan-quality evaluation cases.

### Change retrieval behavior

1. Document the retrieval hypothesis and target metric.
2. Add or update a versioned evaluation dataset.
3. Implement the change behind the knowledge tool interface.
4. Evaluate retrieval, reranking, grounding, citation coverage, latency, and cost.
5. Compare against the current baseline with identical inputs and configuration.
6. Document the result and roll back changes that do not meet the acceptance criteria.

### Add a database-backed use case

1. Register the approved schema and access scope.
2. Use a least-privilege read-only connection.
3. Add SQL validation, parameterization, row limits, and timeouts.
4. Normalize results through the database tool boundary.
5. Add fixtures with synthetic data and known expected calculations.
6. Test forbidden statements, injection attempts, oversized results, and permission denial.
7. Record query lineage without exposing secrets or unnecessary row data.

### Add an evaluation

1. Define the metric, inputs, expected result, tolerance, and failure interpretation.
2. Add sanitized cases under `evaluation/datasets`.
3. Implement a deterministic evaluator under `evaluation/evaluators` where possible.
4. Register it in `evaluation/run_eval.py`.
5. Produce a versioned report containing full run metadata.
6. Document limitations and manual-review requirements.

### Prepare a change for review

1. Inspect the diff for unrelated changes and sensitive data.
2. Run the configured formatter, linter, type checker, and complete test suite.
3. Run relevant evaluation and smoke suites.
4. Update documentation, examples, migrations, and environment templates.
5. Confirm new logs and artifacts do not expose restricted data.
6. Summarize scope, design decisions, risks, and verification evidence in the pull request.

### Handle a failed agent task

1. Use the task and trace identifiers to inspect structured events.
2. Identify the failing node, tool, policy decision, or contract boundary.
3. Reproduce with sanitized inputs in an isolated environment.
4. Preserve the original evidence and audit trail.
5. Add a regression test before implementing a fix.
6. Re-run relevant tests and evaluations, including safety checks.
7. Document operational impact and whether affected tasks require replay.

When modifying this repository, understand the existing architecture first, preserve working behavior, make the smallest justified change, and provide fresh verification evidence before claiming completion.
