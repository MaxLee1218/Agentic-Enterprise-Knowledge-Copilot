# Performance Analysis and Limits

Stage 16 adds deterministic task-level performance analysis and enforces operational budgets at
existing governed boundaries. It does not change the frozen Supplier Quality v1.1 state machine,
retry/replan policy, tool contracts, or verification result semantics.

## Default budgets

| Setting | Default | Enforcement boundary | Over-limit behavior |
|---|---:|---|---|
| `MAX_TOTAL_EXECUTION_SECONDS` | 300 s | task deadline checked by the graph runtime | existing typed deadline failure route |
| `MAX_STEP_DURATION_SECONDS` | 60 s | ToolExecutor attempt timeout and node performance warning | typed `TOOL_TIMEOUT`; `STEP_DURATION_LIMIT_EXCEEDED` event/metric for slow nodes |
| `MAX_DATABASE_ROWS` | 50,000 | ToolExecutor preflight plus domain database contract | `DATABASE_ROW_LIMIT_EXCEEDED` |
| `MAX_EVIDENCE_ITEMS` | 500 per Task | authoritative Evidence Ledger append/record | `EVIDENCE_LIMIT_EXCEEDED` |
| `REPORT_MAX_SIZE_BYTES` | 26,214,400 bytes | atomic Artifact writer and Report Tool mapping | `ARTIFACT_SIZE_LIMIT_EXCEEDED` |
| `LLM_MAX_OUTPUT_TOKENS` | 4,096 | structured planning result usage check | `LLM_TOKEN_BUDGET_EXCEEDED` |

All values are positive, bounded `Settings` fields and may be tightened. A step limit may be larger
than a task limit; the earlier task deadline still wins. The shared database ceiling is 50,000 rows
for Accounts Payable, while the frozen Supplier Quality input schema continues to cap its queries
at 10,000 rows. Limit failures do not broaden scope, bypass audit/evidence, or silently retry
non-retryable validation errors. Tool-visible limit errors produce a failed span,
`performance.limit_exceeded`, and a labeled limit counter without recording the rejected payload.

## Latency semantics

UTC timestamps describe when events happened. Durations use an injectable monotonic timer so wall
clock corrections cannot create negative latency. `task.total` measures active start/resume graph
segments, node spans measure one LangGraph invocation, and step/tool spans measure one governed
attempt. Request latency includes synchronous API work around the task.

The analyzer reports:

- wall-clock task-segment latency from the trace summary;
- sum of span latency as a separate diagnostic that may double-count nesting;
- slowest graph stage, step ID, and registered tool;
- each stage's percentage of task latency;
- retry overhead (tool attempts greater than one), replan count, and external-service latency;
- warnings when task or step/node duration crosses its configured budget.

For concurrent spans, summing durations is not the critical path. This version does not infer a
parallel dependency graph, so `critical_path_latency_ms` is explicitly `null`. Resumes share one
Trace ID and summaries aggregate all retained segments; waiting-for-approval time is not active
task execution latency.

## Testing and inspection

Functional performance tests use injected clocks/timers or direct histogram samples. They assert
status, typed errors, counters, percentile math, warnings, and bounded storage—not fragile machine
speed thresholds.

```bash
python scripts/smoke_agent.py --show-trace
python scripts/inspect_task.py TASK_ID --performance
pytest tests/unit/observability
```

Machine-dependent latency in Agent Evaluation remains informational and outside the default hard
regression gate. The optional `CapturedExecution.observability_snapshot` reuses the unified trace
summary, performance analysis, and metric snapshot; it identifies its timing as in-process. Real
load, soak, distributed tracing, backend export throughput, and multi-process benchmarks belong in
a separately marked/manual performance job and are not part of ordinary CI.
