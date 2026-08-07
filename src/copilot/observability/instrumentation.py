"""Composition-friendly observability runtime and uniform graph-node instrumentation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from time import monotonic
from typing import TypeVar, cast

from copilot.contracts import (
    MetricSnapshot,
    ObservabilityContext,
    PerformanceAnalysis,
    SpanKind,
    SpanStatus,
    TraceSpan,
    TraceSummary,
)
from copilot.observability.context import ObservabilityContextManager
from copilot.observability.logging import StructuredEventLogger
from copilot.observability.metrics import MetricsRegistry
from copilot.observability.performance import PerformanceAnalyzer
from copilot.observability.tracing import InMemoryTracer
from copilot.services.observability import EventName, SpanHandle

TState = TypeVar("TState")
TResult = TypeVar("TResult")


class _DisabledSpan:
    @property
    def span_id(self) -> str:
        return "SPAN-DISABLED"

    def set_status(self, status: SpanStatus, *, error_type: str | None = None) -> None:
        del status, error_type

    def set_attribute(self, name: str, value: object) -> None:
        del name, value


class InMemoryObservability:
    """Unified local implementation used when no external observability platform exists."""

    def __init__(
        self,
        *,
        context: ObservabilityContextManager,
        tracer: InMemoryTracer,
        metrics: MetricsRegistry,
        analyzer: PerformanceAnalyzer,
        logger: StructuredEventLogger,
        max_step_duration_seconds: float,
        enabled: bool = True,
        trace_enabled: bool = True,
        metrics_enabled: bool = True,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._context = context
        self._tracer = tracer
        self._metrics = metrics
        self._analyzer = analyzer
        self._logger = logger
        self._max_step_duration_ms = max_step_duration_seconds * 1000
        self._enabled = enabled
        self._trace_enabled = trace_enabled
        self._metrics_enabled = metrics_enabled
        self._timer = timer

    @property
    def current_context(self) -> ObservabilityContext:
        return self._context.current

    @property
    def tracer(self) -> InMemoryTracer:
        """Expose the owned adapter only to bootstrap/tests, never to business modules."""
        return self._tracer

    @property
    def metrics(self) -> MetricsRegistry:
        """Expose the owned adapter only to bootstrap/tests, never to business modules."""
        return self._metrics

    def bind_context(self, **values: str | None) -> AbstractContextManager[ObservabilityContext]:
        return self._context.bind(**values)

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[SpanHandle]:
        if not self._enabled or not self._trace_enabled:
            yield _DisabledSpan()
            return
        with self._tracer.span(name, kind, attributes=attributes) as handle:
            yield handle

    def instrument_node(
        self,
        name: str,
        function: Callable[[TState], TResult],
    ) -> Callable[[TState], TResult]:
        """Wrap a graph node once while preserving its exact return value and exceptions."""

        def instrumented(state: TState) -> TResult:
            values = _mapping(state)
            intake = values.get("intake_context")
            task_id = _string(values.get("task_id"))
            trace_id = _string(values.get("trace_id"))
            step_id = _string(values.get("current_step_id"))
            started = self._timer()
            labels = {"node_name": name}
            with self.bind_context(
                task_id=task_id,
                trace_id=trace_id,
                step_id=step_id,
                node_name=name,
                tool_name=None,
                tenant_id=_attribute(intake, "tenant_id"),
                user_id=_attribute(intake, "user_id"),
                session_id=_attribute(intake, "session_id"),
            ):
                self.increment("graph_node_executions_total", labels=labels)
                self.emit(EventName.NODE_STARTED, fields={"status": "RUNNING"})
                with self.span(name, SpanKind.GRAPH_NODE) as span:
                    try:
                        result = function(state)
                    except BaseException as exc:
                        latency = max(0.0, (self._timer() - started) * 1000)
                        span.set_status(SpanStatus.FAILED, error_type=type(exc).__name__)
                        self.increment("graph_node_failures_total", labels=labels)
                        self.observe("graph_node_latency_ms", latency, labels=labels)
                        self.emit(
                            EventName.NODE_FAILED,
                            level=logging.ERROR,
                            fields={
                                "status": "FAILED",
                                "latency_ms": latency,
                                "error_type": type(exc).__name__,
                            },
                        )
                        self._record_limit(name, latency)
                        raise
                    latency = max(0.0, (self._timer() - started) * 1000)
                    route, error_type = _result_status(result)
                    failed = error_type is not None
                    span.set_attribute("route", route)
                    span.set_status(
                        SpanStatus.FAILED if failed else SpanStatus.SUCCEEDED,
                        error_type=error_type,
                    )
                    if failed:
                        self.increment("graph_node_failures_total", labels=labels)
                        self._record_result_limit(error_type, latency)
                    self.observe("graph_node_latency_ms", latency, labels=labels)
                    self.emit(
                        EventName.NODE_FAILED if failed else EventName.NODE_COMPLETED,
                        level=logging.ERROR if failed else logging.INFO,
                        fields={
                            "status": "FAILED" if failed else "SUCCEEDED",
                            "latency_ms": latency,
                            "error_type": error_type,
                            "route": route,
                        },
                    )
                    self._record_limit(name, latency)
                    return result

        return instrumented

    def emit(
        self,
        event: str,
        *,
        level: int = logging.INFO,
        message: str | None = None,
        fields: Mapping[str, object] | None = None,
    ) -> None:
        if not self._enabled:
            return
        self._logger.emit(event, level=level, message=message, fields=dict(fields or {}))

    def increment(
        self,
        name: str,
        amount: int = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        if not self._metrics_enabled:
            return
        try:
            self._metrics.increment(name, amount, labels=labels)
        except ValueError:
            return

    def gauge_add(
        self,
        name: str,
        amount: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        if not self._metrics_enabled:
            return
        try:
            self._metrics.gauge_add(name, amount, labels=labels)
        except ValueError:
            return

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        if not self._metrics_enabled:
            return
        try:
            self._metrics.observe(name, value, labels=labels)
        except ValueError:
            return

    def record_workflow_event(
        self,
        event: str,
        *,
        status: str | None = None,
        fields: Mapping[str, object] | None = None,
    ) -> None:
        """Translate existing audit events into non-duplicated lifecycle counters."""
        if event == "REPLAN_STARTED":
            self.increment("replans_total")
            self.emit(EventName.PLAN_REPLANNED, level=logging.WARNING, fields=fields)
        elif event == "PLAN_REPAIR_STARTED":
            self.increment("plan_repairs_total")
        elif event == "task_status_changed" and status == "WAITING_APPROVAL":
            self.increment("approvals_requested_total")
            self.emit(EventName.APPROVAL_REQUESTED, fields=fields)
        elif event == "verification_completed" and status == "FAILED":
            self.increment("verification_failures_total")

    def metrics_snapshot(self) -> MetricSnapshot:
        return self._metrics.snapshot()

    def spans_for_trace(self, trace_id: str) -> tuple[TraceSpan, ...]:
        return self._tracer.spans_for_trace(trace_id)

    def trace_summary(self, trace_id: str, *, status: str = "UNKNOWN") -> TraceSummary | None:
        return self._tracer.summary(trace_id, status=status)

    def analyze_trace(
        self, trace_id: str, *, status: str = "UNKNOWN"
    ) -> PerformanceAnalysis | None:
        summary = self.trace_summary(trace_id, status=status)
        if summary is None:
            return None
        return self._analyzer.analyze(summary, self.spans_for_trace(trace_id))

    def _record_limit(self, name: str, latency_ms: float) -> None:
        if latency_ms <= self._max_step_duration_ms:
            return
        self.increment(
            "performance_limit_exceeded_total",
            labels={"error_type": "STEP_DURATION_LIMIT_EXCEEDED"},
        )
        self.emit(
            EventName.PERFORMANCE_LIMIT_EXCEEDED,
            level=logging.ERROR,
            fields={
                "error_type": "STEP_DURATION_LIMIT_EXCEEDED",
                "node_name": name,
                "latency_ms": latency_ms,
            },
        )

    def _record_result_limit(self, error_type: str | None, latency_ms: float) -> None:
        if error_type not in {
            "LLM_TOKEN_BUDGET_EXCEEDED",
            "TASK_DEADLINE_EXCEEDED",
        }:
            return
        self.increment(
            "performance_limit_exceeded_total",
            labels={"error_type": error_type},
        )
        self.emit(
            EventName.PERFORMANCE_LIMIT_EXCEEDED,
            level=logging.ERROR,
            fields={"error_type": error_type, "latency_ms": latency_ms},
        )


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _attribute(value: object, name: str) -> str | None:
    candidate = getattr(value, name, None)
    return candidate if isinstance(candidate, str) else None


def _result_status(value: object) -> tuple[str, str | None]:
    if not isinstance(value, Mapping):
        return "completed", None
    route_value = value.get("route", "completed")
    route = route_value if isinstance(route_value, str) else "completed"
    failure_tokens = ("failed", "failure", "denied", "invalid", "deadline", "exhausted")
    error_type = "NODE_RESULT_FAILURE" if any(token in route for token in failure_tokens) else None
    errors = value.get("errors")
    if isinstance(errors, list | tuple) and errors:
        first = errors[0]
        code = getattr(first, "error_code", None)
        typed = getattr(first, "error_type", None)
        error_type = (
            code
            if isinstance(code, str)
            else getattr(typed, "value", str(typed))
            if typed is not None
            else error_type
        )
    return route, error_type


__all__ = ["InMemoryObservability"]
