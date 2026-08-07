"""Deterministic Stage 16 observability foundation tests."""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue

from copilot.contracts import (
    ErrorType,
    JsonObject,
    SpanKind,
    SpanStatus,
    TaskError,
    TraceSpan,
    TraceSummary,
)
from copilot.observability.context import ObservabilityContextManager
from copilot.observability.instrumentation import InMemoryObservability
from copilot.observability.logging import (
    JsonLogFormatter,
    SensitiveDataFilter,
    StructuredEventLogger,
)
from copilot.observability.metrics import MetricsRegistry
from copilot.observability.performance import PerformanceAnalyzer, PerformanceLimits
from copilot.observability.sanitization import sanitize_attributes, sanitize_text
from copilot.observability.tracing import InMemoryTracer
from copilot.services.observability import validate_correlation_id


def test_context_nesting_restoration_exception_and_thread_isolation() -> None:
    context = ObservabilityContextManager()
    context.clear()
    with context.bind(trace_id="TRACE-parent", task_id="T-1", span_id="SPAN-parent"):
        assert context.current.trace_id == "TRACE-parent"
        with pytest.raises(RuntimeError), context.bind(trace_id="TRACE-child", step_id="S-1"):
            assert context.current.task_id == "T-1"
            assert context.current.trace_id == "TRACE-child"
            assert context.current.span_id is None
            raise RuntimeError("expected")
        assert context.current.trace_id == "TRACE-parent"
        assert context.current.step_id is None
    assert context.current.trace_id is None

    def isolated(trace_id: str) -> str | None:
        with context.bind(trace_id=trace_id):
            return context.current.trace_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(isolated, ("TRACE-A", "TRACE-B"))) == {"TRACE-A", "TRACE-B"}
    assert context.current.trace_id is None


def test_correlation_id_validation_is_explicit() -> None:
    assert validate_correlation_id(" TRACE-client_01 ") == "TRACE-client_01"
    assert validate_correlation_id("bad id with spaces") is None
    assert validate_correlation_id("x") is None
    assert validate_correlation_id(None) is None


def test_json_logging_has_utc_schema_and_removes_sensitive_content() -> None:
    context = ObservabilityContextManager()
    formatter = JsonLogFormatter(context)
    secret = "stage16-super-secret"
    raw_sql = "SELECT government_id, bank_account FROM supplier_private WHERE token='abc'"
    record = logging.LogRecord(
        name="copilot.test",
        level=logging.ERROR,
        pathname="/private/tmp/test_logging.py",
        lineno=10,
        msg=f"password={secret} {raw_sql} /Users/example/private.txt",
        args=(),
        exc_info=None,
    )
    record.event = "tool.failed"
    record.latency_ms = 12.5
    record.Authorization = "Bearer top-secret-token"
    record.Cookie = "session=private-cookie"
    record.personal_email = "person@example.com"
    record.phone = "+86 138 0013 8000"
    with context.bind(trace_id="TRACE-JSON", task_id="T-JSON", step_id="S-JSON"):
        SensitiveDataFilter().filter(record)
        payload = json.loads(formatter.format(record))

    rendered = json.dumps(payload)
    assert payload["event"] == "tool.failed"
    assert payload["level"] == "ERROR"
    assert payload["trace_id"] == "TRACE-JSON"
    assert payload["step_id"] == "S-JSON"
    assert isinstance(payload["latency_ms"], float)
    assert datetime.fromisoformat(payload["timestamp"]).tzinfo is not None
    for unsafe in (
        secret,
        raw_sql,
        "top-secret-token",
        "private-cookie",
        "person@example.com",
        "+86 138 0013 8000",
        "/Users/example/private.txt",
    ):
        assert unsafe not in rendered


def test_sanitizer_bounds_attributes_and_rejects_uncontrolled_names() -> None:
    safe = sanitize_attributes(
        {
            "attempt": 2,
            "token": "do-not-record",
            "prompt injection field": "malicious",
            "output_size": "x" * 100,
        },
        max_attributes=2,
        max_length=16,
    )
    assert safe.root["attempt"] == 2
    assert "token" not in safe.root
    assert "prompt injection field" not in safe.root
    assert len(str(safe.root["output_size"])) < 80
    assert "Traceback (most recent call last)" not in sanitize_text(
        "Traceback (most recent call last):\n/private/tmp/private.py"
    )


def test_tracing_links_children_and_completes_all_outcomes() -> None:
    context = ObservabilityContextManager()
    context.clear()
    tracer = InMemoryTracer(context=context, max_spans=20)
    with (
        context.bind(trace_id="TRACE-SPANS", task_id="T-SPANS"),
        tracer.span("task.total", SpanKind.TASK) as root,
    ):
        with tracer.span("node.plan", SpanKind.GRAPH_NODE):
            pass
        with tracer.span("step.S-1", SpanKind.STEP) as skipped:
            skipped.set_status(SpanStatus.SKIPPED)
        with pytest.raises(ValueError), tracer.span("tool.failed", SpanKind.TOOL):
            raise ValueError("safe failure")
        with pytest.raises(TimeoutError), tracer.span("tool.timeout", SpanKind.TOOL):
            raise TimeoutError("timed out")
        with pytest.raises(asyncio.CancelledError), tracer.span("tool.cancelled", SpanKind.TOOL):
            raise asyncio.CancelledError()
        root.set_attribute("retry_count", 1)

    spans = tracer.spans_for_trace("TRACE-SPANS")
    by_name = {span.name: span for span in spans}
    assert by_name["node.plan"].parent_span_id == by_name["task.total"].span_id
    assert by_name["task.total"].status is SpanStatus.SUCCEEDED
    assert by_name["step.S-1"].status is SpanStatus.SKIPPED
    assert by_name["tool.failed"].status is SpanStatus.FAILED
    assert by_name["tool.timeout"].status is SpanStatus.TIMED_OUT
    assert by_name["tool.cancelled"].status is SpanStatus.CANCELLED
    assert all(span.completed_at is not None and span.latency_ms is not None for span in spans)
    summary = tracer.summary("TRACE-SPANS", status="FAILED")
    assert summary is not None
    assert summary.retry_count == 1
    assert summary.failed_span_count == 2

    with tracer.span("auto-root", SpanKind.TASK):
        auto_trace_id = context.current.trace_id
    assert auto_trace_id is not None
    auto = next(span for span in tracer.spans_for_trace(auto_trace_id))
    assert auto.trace_id.startswith("TRACE-")
    assert auto.parent_span_id is None


def test_metrics_quantiles_window_failure_rate_and_label_policy() -> None:
    registry = MetricsRegistry(window_size=3)
    for value in (1.0, 2.0, 3.0, 4.0):
        registry.observe("task_latency_ms", value)
    registry.increment("tool_executions_total", 2, labels={"tool_name": "database_query"})
    registry.increment("tool_failures_total", 1, labels={"tool_name": "database_query"})
    registry.gauge_add("active_tasks", 1)
    registry.gauge_add("active_tasks", -1)
    snapshot = registry.snapshot()

    assert snapshot.histograms["task_latency_ms"].count == 4
    assert snapshot.histograms["task_latency_ms"].window_count == 3
    assert snapshot.quantiles["task_latency_ms"] == {"p50": 3.0, "p95": 4.0}
    assert snapshot.failure_rates["tool_attempt_failure_rate"] == 0.5
    assert snapshot.gauges["active_tasks"] == 0
    assert MetricsRegistry().snapshot().failure_rates["tool_attempt_failure_rate"] == 0
    with pytest.raises(ValueError, match="not allowlisted"):
        registry.increment("tasks_started_total", labels={"task_id": "T-high-cardinality"})
    with pytest.raises(ValueError, match="unsafe"):
        registry.increment("tool_executions_total", labels={"tool_name": "secret value"})


def test_performance_analyzer_reports_slowest_and_retry_overhead_without_double_counting() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    spans = (
        _span("node.plan", SpanKind.GRAPH_NODE, 40, started),
        _span("node.execute", SpanKind.GRAPH_NODE, 60, started),
        _span("step.S-1", SpanKind.STEP, 30, started, step_id="S-1"),
        _span(
            "tool.database_query",
            SpanKind.TOOL,
            25,
            started,
            step_id="S-1",
            attributes={"attempt": 2},
        ),
    )
    summary = TraceSummary(
        task_id="T-PERF",
        trace_id="TRACE-PERF",
        status="COMPLETED",
        started_at=started,
        completed_at=started + timedelta(milliseconds=100),
        total_latency_ms=100,
        span_count=len(spans),
        step_count=1,
        tool_call_count=1,
        retry_count=1,
        replan_count=0,
        approval_count=0,
        failed_span_count=0,
    )
    analysis = PerformanceAnalyzer(PerformanceLimits(0.05, 0.05, 100, 10, 1_000, 100)).analyze(
        summary, spans
    )

    assert analysis.slowest_stage == "node.execute"
    assert analysis.slowest_step_id == "S-1"
    assert analysis.slowest_tool == "database_query"
    assert analysis.percentage_by_stage == {"node.plan": 40.0, "node.execute": 60.0}
    assert analysis.retry_overhead_ms == 25
    assert analysis.critical_path_latency_ms is None
    assert {warning.code for warning in analysis.warnings} == {
        "TASK_DURATION_LIMIT_EXCEEDED",
        "STEP_DURATION_LIMIT_EXCEEDED",
    }


def test_workflow_counters_and_node_level_limits_reuse_the_same_registry() -> None:
    context = ObservabilityContextManager()
    metrics = MetricsRegistry()
    logger = logging.getLogger("copilot.test.observability_events")
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    telemetry = InMemoryObservability(
        context=context,
        tracer=InMemoryTracer(context=context),
        metrics=metrics,
        analyzer=PerformanceAnalyzer(PerformanceLimits(30, 5, 10, 10, 1024, 100)),
        logger=StructuredEventLogger(logger),
        max_step_duration_seconds=5,
    )
    telemetry.record_workflow_event("REPLAN_STARTED")
    telemetry.record_workflow_event("PLAN_REPAIR_STARTED")
    telemetry.record_workflow_event("task_status_changed", status="WAITING_APPROVAL")
    telemetry.record_workflow_event("verification_completed", status="FAILED")

    error = TaskError(
        error_code="LLM_TOKEN_BUDGET_EXCEEDED",
        error_type=ErrorType.VALIDATION,
        message="Structured model output exceeded its budget",
        recoverable=False,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    instrumented = telemetry.instrument_node(
        "understand_task",
        lambda _state: {"route": "llm_failure", "errors": [error]},
    )
    instrumented({"task_id": "T-LIMIT", "trace_id": "TRACE-LIMIT"})
    snapshot = metrics.snapshot()

    assert snapshot.counters["replans_total"] == 1
    assert snapshot.counters["plan_repairs_total"] == 1
    assert snapshot.counters["approvals_requested_total"] == 1
    assert snapshot.counters["verification_failures_total"] == 1
    series = "performance_limit_exceeded_total{error_type=LLM_TOKEN_BUDGET_EXCEEDED}"
    assert snapshot.counters[series] == 1
    span = telemetry.spans_for_trace("TRACE-LIMIT")[0]
    assert span.status is SpanStatus.FAILED
    assert span.error_type == "LLM_TOKEN_BUDGET_EXCEEDED"


def _span(
    name: str,
    kind: SpanKind,
    latency_ms: float,
    started_at: datetime,
    *,
    step_id: str | None = None,
    attributes: dict[str, JsonValue] | None = None,
) -> TraceSpan:
    return TraceSpan(
        span_id=f"SPAN-{name}",
        trace_id="TRACE-PERF",
        task_id="T-PERF",
        step_id=step_id,
        name=name,
        kind=kind,
        status=SpanStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=latency_ms),
        latency_ms=latency_ms,
        attributes=JsonObject(attributes or {}),
    )
