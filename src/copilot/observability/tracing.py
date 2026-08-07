"""Bounded in-memory tracing with monotonic duration and UTC timestamps."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from time import monotonic
from uuid import uuid4

from copilot.contracts import (
    JsonObject,
    SpanKind,
    SpanStatus,
    TraceSpan,
    TraceSummary,
)
from copilot.contracts.validators import utc_now
from copilot.observability.context import ObservabilityContextManager
from copilot.observability.sanitization import sanitize_attributes, sanitize_value


@dataclass(slots=True)
class _SpanHandle:
    _span_id: str
    _attributes: dict[str, object]
    _max_attributes: int
    _max_attribute_length: int
    _status: SpanStatus = SpanStatus.RUNNING
    _error_type: str | None = None

    @property
    def span_id(self) -> str:
        return self._span_id

    @property
    def status(self) -> SpanStatus:
        return self._status

    @property
    def error_type(self) -> str | None:
        return self._error_type

    @property
    def attributes(self) -> JsonObject:
        return sanitize_attributes(
            self._attributes,
            max_attributes=self._max_attributes,
            max_length=self._max_attribute_length,
        )

    def set_status(self, status: SpanStatus, *, error_type: str | None = None) -> None:
        self._status = status
        self._error_type = (
            str(sanitize_value(error_type, max_length=128)) if error_type is not None else None
        )

    def set_attribute(self, name: str, value: object) -> None:
        self._attributes[name] = value


@dataclass(slots=True)
class InMemoryTracer:
    """Thread-safe bounded span store that has no external collector dependency."""

    context: ObservabilityContextManager
    max_spans: int = 10_000
    max_attributes: int = 32
    max_attribute_length: int = 256
    clock: Callable[[], datetime] = utc_now
    timer: Callable[[], float] = monotonic
    _spans: deque[TraceSpan] = field(init=False)
    _lock: RLock = field(init=False, default_factory=RLock)

    def __post_init__(self) -> None:
        if self.max_spans < 1:
            raise ValueError("max_spans must be positive")
        if self.max_attributes < 1 or self.max_attribute_length < 1:
            raise ValueError("trace attribute limits must be positive")
        self._spans = deque(maxlen=self.max_spans)

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[_SpanHandle]:
        """Create a child span and always complete it without swallowing exceptions."""
        parent = self.context.current
        trace_id = parent.trace_id or f"TRACE-{uuid4().hex}"
        span_id = f"SPAN-{uuid4().hex}"
        started_at = self.clock()
        started_tick = self.timer()
        handle = _SpanHandle(
            span_id,
            dict(attributes or {}),
            self.max_attributes,
            self.max_attribute_length,
        )
        error: BaseException | None = None
        with self.context.bind(trace_id=trace_id, span_id=span_id):
            try:
                yield handle
            except BaseException as exc:
                error = exc
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    handle.set_status(SpanStatus.TIMED_OUT, error_type=type(exc).__name__)
                elif isinstance(exc, asyncio.CancelledError):
                    handle.set_status(SpanStatus.CANCELLED, error_type=type(exc).__name__)
                else:
                    handle.set_status(SpanStatus.FAILED, error_type=type(exc).__name__)
                raise
            finally:
                if handle.status is SpanStatus.RUNNING:
                    handle.set_status(SpanStatus.SUCCEEDED)
                completed_at = self.clock()
                latency_ms = max(0.0, (self.timer() - started_tick) * 1000)
                completed = TraceSpan(
                    span_id=span_id,
                    trace_id=trace_id,
                    parent_span_id=parent.span_id,
                    task_id=parent.task_id,
                    step_id=parent.step_id,
                    name=name,
                    kind=kind,
                    status=handle.status,
                    started_at=started_at,
                    completed_at=completed_at,
                    latency_ms=latency_ms,
                    attributes=handle.attributes,
                    error_type=handle.error_type or (type(error).__name__ if error else None),
                )
                with self._lock:
                    self._spans.append(completed)

    def spans_for_trace(self, trace_id: str) -> tuple[TraceSpan, ...]:
        """Return a detached chronological snapshot for one trace."""
        with self._lock:
            return tuple(
                sorted(
                    (span for span in self._spans if span.trace_id == trace_id),
                    key=lambda span: (span.started_at, span.span_id),
                )
            )

    def summary(self, trace_id: str, *, status: str = "UNKNOWN") -> TraceSummary | None:
        """Aggregate safe task, node, step, tool, retry, replan, and approval facts."""
        spans = self.spans_for_trace(trace_id)
        if not spans:
            return None
        completed = tuple(span for span in spans if span.completed_at is not None)
        if not completed:
            return None
        started_at = min(span.started_at for span in completed)
        completed_at = max(span.completed_at for span in completed if span.completed_at is not None)
        task_spans = tuple(span for span in completed if span.kind is SpanKind.TASK)
        total_latency = sum(span.latency_ms or 0 for span in task_spans)
        if not task_spans:
            total_latency = max(0.0, (completed_at - started_at).total_seconds() * 1000)
        stage_latencies = _latencies(completed, SpanKind.GRAPH_NODE, "")
        tool_latencies = _latencies(completed, SpanKind.TOOL, "tool.")
        slowest = max(completed, key=lambda span: span.latency_ms or 0)
        root_values = [span.attributes.root for span in completed if span.kind is SpanKind.TASK]
        return TraceSummary(
            task_id=next((span.task_id for span in completed if span.task_id), "TASK-UNKNOWN"),
            trace_id=trace_id,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            total_latency_ms=total_latency,
            span_count=len(completed),
            step_count=len({span.step_id for span in completed if span.step_id}),
            tool_call_count=sum(span.kind is SpanKind.TOOL for span in completed),
            retry_count=_maximum_attribute(root_values, "retry_count"),
            replan_count=_maximum_attribute(root_values, "replan_count"),
            approval_count=_maximum_attribute(root_values, "approval_count"),
            failed_span_count=sum(
                span.status in {SpanStatus.FAILED, SpanStatus.TIMED_OUT} for span in completed
            ),
            slowest_span_name=slowest.name,
            slowest_span_latency_ms=slowest.latency_ms,
            stage_latencies=stage_latencies,
            tool_latencies=tool_latencies,
            error_types=tuple(
                sorted({span.error_type for span in completed if span.error_type is not None})
            ),
        )

    def clear(self) -> None:
        """Clear bounded local spans for deterministic test isolation."""
        with self._lock:
            self._spans.clear()


def _latencies(
    spans: tuple[TraceSpan, ...],
    kind: SpanKind,
    prefix: str,
) -> dict[str, float]:
    totals: dict[str, float] = {}
    for span in spans:
        if span.kind is not kind:
            continue
        name = span.name.removeprefix(prefix)
        totals[name] = totals.get(name, 0.0) + (span.latency_ms or 0)
    return dict(sorted(totals.items()))


def _maximum_attribute(values: Sequence[Mapping[str, object]], name: str) -> int:
    candidates = [value.get(name) for value in values]
    integers = [item for item in candidates if isinstance(item, int) and not isinstance(item, bool)]
    return max(integers, default=0)


__all__ = ["InMemoryTracer"]
