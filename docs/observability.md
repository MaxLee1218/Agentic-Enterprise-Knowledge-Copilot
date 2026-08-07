# Observability

Stage 16 provides local, replaceable observability without requiring an external log, metric, or
trace service. The composition root constructs one context manager, structured event logger,
bounded metrics registry, bounded trace store, and performance analyzer and injects an
application-owned port into the API, task service, graph runtime, approval service, and governed
tool executor. Observability reports business execution; it does not define a second task, step,
tool, policy, evidence, or audit state.

## Correlation lifecycle

`POST /v1/tasks` accepts `X-Trace-ID` and `X-Request-ID`. A value must be 3–128 characters and use
only letters, digits, `.`, `_`, `:`, or `-`; invalid values are replaced. When absent, the API
creates `TRACE-<uuid>` and `REQUEST-<uuid>`. The response carries both headers. The API places the
Trace ID on `NaturalLanguageTaskCommand`; Task Intake preserves it in `TrustedTaskContext`, and the
same value reaches LangGraph and every `ToolExecutionContext`. CLI intake generates a Trace ID in
the same Task Service when one is not supplied.

`ObservabilityContext` is immutable and bound with `ContextVar`. Nested scopes restore their exact
parent in `finally`, including exception paths. It contains correlation identifiers only; raw task
text, SQL, credentials, evidence content, and tool payloads are not context fields.

## Trace hierarchy

The in-process hierarchy is:

```text
request.http (EXTERNAL_SERVICE, API only)
`- task.total (TASK; one per start or resume segment)
   `- <graph node name> (GRAPH_NODE)
      `- step.<step_id> (STEP; one per governed attempt)
         `- tool.<tool_name> (TOOL)
```

Every completed span records UTC start/end timestamps, monotonic `latency_ms`, status, parent ID,
task/step correlation, a bounded controlled attribute set, and a safe error type. Exceptions,
timeouts, and cancellation close spans in `finally`. Retry attempts use the `attempt` attribute;
resumed execution uses the original Trace ID and creates another `task.total` segment. A trace
summary includes total task-segment latency, stage/tool latency, calls, retries, replans, approvals,
failed spans, and the slowest span.

There is no sampling in this version: all completed internal spans enter a process-local deque of
`MAX_TRACE_SPANS`. This preserves deterministic local tests but is not durable distributed tracing.

## Structured log schema and events

`LOG_FORMAT=json` is the safe default. Each JSON line contains `timestamp` (timezone-aware UTC),
`level`, `logger`, stable `event`, safe `message`, and any available correlation fields:
`task_id`, `trace_id`, `step_id`, `node_name`, `tool_name`, `request_id`, `tenant_id`, `user_id`, and
`session_id`. Operational fields such as `status`, numeric `latency_ms`, `attempt`, `retry_count`,
`error_type`, HTTP status, and route template may be added. Optional fields are omitted rather than
written as ambiguous empty strings.

Stable event families are `request.*`, `task.*`, `graph.node.*`, `step.*`, `tool.*`,
`approval.*`, `plan.replanned`, and `performance.limit_exceeded`. Development may explicitly use
`LOG_FORMAT=text`; JSON remains the production/test-oriented format.

## Metrics

The thread-safe local registry supports allowlisted counters, gauges, and histograms:

- requests; task started/completed/failed/cancelled/resumed; graph execution/failure;
- tool execution/success/failure/timeout/retry and tool attempt failure rate;
- approval request/approve/reject, plan repair/replan, verification failure, and limit exceeded;
- active tasks and active tool calls;
- request, task, graph-node, step, tool, and external-service latency.

Labels are limited to low-cardinality controlled dimensions such as node name, registered tool
name, status, HTTP method/status, and route template. Task, trace, request, user, tenant, supplier,
document, query, SQL, prompt, token, and secret values are forbidden as labels.

Histograms retain the latest `METRICS_WINDOW_SIZE` samples per complete label series. `p50` and
`p95` use deterministic nearest-rank selection: sort the current window and select
`ceil(q * count)`, with a minimum rank of one. Empty windows return `null`; one sample returns that
sample. Histogram total count/sum/min/max describe all observations since the last registry reset,
while quantiles describe only the bounded current window. Metrics are process-local and reset on
restart.

## Sensitive-data handling

The Stage 15 recursive redactor is applied before observability-specific filtering. Field-aware
handling covers passwords, secrets, tokens, authorization/cookies, bank accounts, government IDs,
private email, and phone numbers. Text filtering removes secret-shaped values, full SQL,
tracebacks, and local absolute paths and then length-bounds the result. Span attributes use both a
name allowlist and count/value limits. Payload summaries retain type, size, field names where safe,
record count, and SHA-256—not raw content.

## Troubleshooting

Use the Task ID to inspect durable business/audit facts and the Trace ID for live process spans:

```bash
python scripts/smoke_agent.py
python scripts/smoke_agent.py --show-trace
python scripts/inspect_task.py TASK_ID --performance
```

The smoke command prints every sanitized Task/Node/Step/Tool span, total and component latency,
p50/p95, slowest stage/step/tool, failure rate, retries/replans, status, and Artifact. The inspect
command uses the live Trace when available and durable workflow/tool audit timing after restart.

## Known limitations

- Trace and metric stores are bounded and process-local; full spans are not checkpoint-persisted.
- There is no cross-process propagation/export, tail sampling, external dashboard, or alerting.
- Parallel critical-path calculation is intentionally reported as unavailable; nested span sums
  are not presented as wall-clock time.
- Logger/export failures are best-effort and do not replace business results. Security filtering
  and configured execution limits remain fail-closed at their governed boundaries.

