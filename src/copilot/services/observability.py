"""Application-owned observability ports used by services, graph nodes, tools, and APIs."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol, TypeVar

from copilot.contracts import (
    MetricSnapshot,
    ObservabilityContext,
    PerformanceAnalysis,
    SpanKind,
    SpanStatus,
    TraceSpan,
    TraceSummary,
)

TState = TypeVar("TState")
TResult = TypeVar("TResult")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


def validate_correlation_id(value: str | None) -> str | None:
    """Return a normalized safe external correlation ID, or ``None`` when invalid."""
    if value is None:
        return None
    candidate = value.strip()
    return candidate if _CORRELATION_ID.fullmatch(candidate) else None


class EventName:
    """Stable event constants shared without depending on a logging implementation."""

    REQUEST_RECEIVED = "request.received"
    REQUEST_COMPLETED = "request.completed"
    REQUEST_FAILED = "request.failed"
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_RESUMED = "task.resumed"
    NODE_STARTED = "graph.node.started"
    NODE_COMPLETED = "graph.node.completed"
    NODE_FAILED = "graph.node.failed"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_TIMEOUT = "tool.timeout"
    TOOL_RETRY = "tool.retry"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    PLAN_REPLANNED = "plan.replanned"
    PERFORMANCE_LIMIT_EXCEEDED = "performance.limit_exceeded"
    EXPORT_FAILED = "observability.export_failed"


class SpanHandle(Protocol):
    """Mutable handle for safely completing an internal span."""

    @property
    def span_id(self) -> str: ...

    def set_status(self, status: SpanStatus, *, error_type: str | None = None) -> None: ...

    def set_attribute(self, name: str, value: object) -> None: ...


class ObservabilityPort(Protocol):
    """Replaceable interface implemented by local or future external exporters."""

    @property
    def current_context(self) -> ObservabilityContext: ...

    def bind_context(
        self, **values: str | None
    ) -> AbstractContextManager[ObservabilityContext]: ...

    def span(
        self,
        name: str,
        kind: SpanKind,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> AbstractContextManager[SpanHandle]: ...

    def instrument_node(
        self,
        name: str,
        function: Callable[[TState], TResult],
    ) -> Callable[[TState], TResult]: ...

    def emit(
        self,
        event: str,
        *,
        level: int = 20,
        message: str | None = None,
        fields: Mapping[str, object] | None = None,
    ) -> None: ...

    def increment(
        self,
        name: str,
        amount: int = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None: ...

    def gauge_add(
        self,
        name: str,
        amount: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None: ...

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None: ...

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None: ...

    def record_workflow_event(
        self,
        event: str,
        *,
        status: str | None = None,
        fields: Mapping[str, object] | None = None,
    ) -> None: ...

    def metrics_snapshot(self) -> MetricSnapshot: ...

    def spans_for_trace(self, trace_id: str) -> tuple[TraceSpan, ...]: ...

    def trace_summary(self, trace_id: str, *, status: str = "UNKNOWN") -> TraceSummary | None: ...

    def analyze_trace(
        self, trace_id: str, *, status: str = "UNKNOWN"
    ) -> PerformanceAnalysis | None: ...


class _NoopSpan:
    @property
    def span_id(self) -> str:
        return "SPAN-NOOP"

    def set_status(self, status: SpanStatus, *, error_type: str | None = None) -> None:
        del status, error_type

    def set_attribute(self, name: str, value: object) -> None:
        del name, value


class NoopObservability:
    """Stateless safe default used only when an interface is not composed with telemetry."""

    @property
    def current_context(self) -> ObservabilityContext:
        return ObservabilityContext()

    @contextmanager
    def bind_context(self, **values: str | None) -> Iterator[ObservabilityContext]:
        del values
        yield ObservabilityContext()

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[SpanHandle]:
        del name, kind, attributes
        yield _NoopSpan()

    def instrument_node(
        self,
        name: str,
        function: Callable[[TState], TResult],
    ) -> Callable[[TState], TResult]:
        del name
        return function

    def emit(
        self,
        event: str,
        *,
        level: int = 20,
        message: str | None = None,
        fields: Mapping[str, object] | None = None,
    ) -> None:
        del event, level, message, fields

    def increment(
        self,
        name: str,
        amount: int = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        del name, amount, labels

    def gauge_add(
        self,
        name: str,
        amount: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        del name, amount, labels

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        del name, value, labels

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        del name, value, labels

    def record_workflow_event(
        self,
        event: str,
        *,
        status: str | None = None,
        fields: Mapping[str, object] | None = None,
    ) -> None:
        del event, status, fields

    def metrics_snapshot(self) -> MetricSnapshot:
        from copilot.contracts.validators import utc_now

        return MetricSnapshot(generated_at=utc_now())

    def spans_for_trace(self, trace_id: str) -> tuple[TraceSpan, ...]:
        del trace_id
        return ()

    def trace_summary(self, trace_id: str, *, status: str = "UNKNOWN") -> TraceSummary | None:
        del trace_id, status
        return None

    def analyze_trace(
        self, trace_id: str, *, status: str = "UNKNOWN"
    ) -> PerformanceAnalysis | None:
        del trace_id, status
        return None


__all__ = [
    "EventName",
    "NoopObservability",
    "ObservabilityPort",
    "SpanHandle",
    "validate_correlation_id",
]
