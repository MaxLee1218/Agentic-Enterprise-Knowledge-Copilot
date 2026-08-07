"""Stable internal contracts for local observability and performance analysis."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.validators import validate_utc_datetime


class SpanKind(StrEnum):
    """Low-cardinality categories used to group trace spans."""

    TASK = "TASK"
    GRAPH_NODE = "GRAPH_NODE"
    STEP = "STEP"
    TOOL = "TOOL"
    EXTERNAL_SERVICE = "EXTERNAL_SERVICE"
    PERSISTENCE = "PERSISTENCE"


class SpanStatus(StrEnum):
    """Operational outcome of one span, separate from business state enums."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    SKIPPED = "SKIPPED"


class ObservabilityContext(ImmutableContractModel):
    """Immutable correlation values propagated through ContextVar bindings."""

    task_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    step_id: str | None = None
    node_name: str | None = None
    tool_name: str | None = None
    request_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None


class TraceSpan(ImmutableContractModel):
    """Completed or active span stored by the replaceable tracing boundary."""

    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    name: str
    kind: SpanKind
    status: SpanStatus
    started_at: datetime
    completed_at: datetime | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    attributes: JsonObject = Field(default_factory=lambda: JsonObject({}))
    error_type: str | None = None

    _validate_timestamps = field_validator("started_at", "completed_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_completion(self) -> TraceSpan:
        """Keep completion timestamp, latency, and status mutually consistent."""
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.status is SpanStatus.RUNNING:
            if self.completed_at is not None or self.latency_ms is not None:
                raise ValueError("running span cannot contain completion values")
        elif self.completed_at is None or self.latency_ms is None:
            raise ValueError("finished span requires completion values")
        return self


class TraceSummary(ImmutableContractModel):
    """Bounded task-level rollup derived from immutable spans."""

    task_id: str
    trace_id: str
    status: str
    started_at: datetime
    completed_at: datetime
    total_latency_ms: float = Field(ge=0)
    span_count: int = Field(ge=0)
    step_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    replan_count: int = Field(ge=0)
    approval_count: int = Field(ge=0)
    failed_span_count: int = Field(ge=0)
    slowest_span_name: str | None = None
    slowest_span_latency_ms: float | None = Field(default=None, ge=0)
    stage_latencies: dict[str, float] = Field(default_factory=dict)
    tool_latencies: dict[str, float] = Field(default_factory=dict)
    error_types: tuple[str, ...] = ()

    _validate_summary_timestamps = field_validator("started_at", "completed_at")(
        validate_utc_datetime
    )


class HistogramSnapshot(ImmutableContractModel):
    """Serializable bounded-window histogram values."""

    count: int = Field(ge=0)
    window_count: int = Field(ge=0)
    minimum: float | None = None
    maximum: float | None = None
    total: float = 0


class MetricSnapshot(ImmutableContractModel):
    """Point-in-time process-local metrics export without SDK-specific types."""

    generated_at: datetime
    counters: dict[str, int] = Field(default_factory=dict)
    gauges: dict[str, float] = Field(default_factory=dict)
    histograms: dict[str, HistogramSnapshot] = Field(default_factory=dict)
    quantiles: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    failure_rates: dict[str, float] = Field(default_factory=dict)

    _validate_generated_at = field_validator("generated_at")(validate_utc_datetime)


class PerformanceWarning(ImmutableContractModel):
    """Safe, typed indication that a configured performance budget was crossed."""

    code: str
    message: str
    span_name: str | None = None
    observed_value: float | None = None
    limit_value: float | None = None


class PerformanceAnalysis(ImmutableContractModel):
    """Task-level latency analysis that does not double-count nested spans as wall time."""

    task_id: str
    trace_id: str
    wall_clock_latency_ms: float = Field(ge=0)
    sum_of_span_latency_ms: float = Field(ge=0)
    critical_path_latency_ms: float | None = Field(default=None, ge=0)
    slowest_stage: str | None = None
    slowest_stage_latency_ms: float | None = Field(default=None, ge=0)
    slowest_step_id: str | None = None
    slowest_step_latency_ms: float | None = Field(default=None, ge=0)
    slowest_tool: str | None = None
    slowest_tool_latency_ms: float | None = Field(default=None, ge=0)
    retry_overhead_ms: float = Field(default=0, ge=0)
    external_service_latency_ms: float = Field(default=0, ge=0)
    percentage_by_stage: dict[str, float] = Field(default_factory=dict)
    warnings: tuple[PerformanceWarning, ...] = ()


__all__ = [
    "HistogramSnapshot",
    "MetricSnapshot",
    "ObservabilityContext",
    "PerformanceAnalysis",
    "PerformanceWarning",
    "SpanKind",
    "SpanStatus",
    "TraceSpan",
    "TraceSummary",
]
