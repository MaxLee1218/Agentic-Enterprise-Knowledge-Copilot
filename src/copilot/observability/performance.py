"""Task-level latency analysis and configured performance budgets."""

from __future__ import annotations

from dataclasses import dataclass

from copilot.contracts import (
    PerformanceAnalysis,
    PerformanceWarning,
    SpanKind,
    TraceSpan,
    TraceSummary,
)


@dataclass(frozen=True, slots=True)
class PerformanceLimits:
    """Validated operational budgets injected from the unified Settings object."""

    max_task_duration_seconds: float
    max_step_duration_seconds: float
    max_database_rows: int
    max_evidence_items: int
    max_report_size_bytes: int
    max_llm_output_tokens: int

    def __post_init__(self) -> None:
        values = (
            self.max_task_duration_seconds,
            self.max_step_duration_seconds,
            self.max_database_rows,
            self.max_evidence_items,
            self.max_report_size_bytes,
            self.max_llm_output_tokens,
        )
        if any(value <= 0 for value in values):
            raise ValueError("performance limits must be positive")


class PerformanceAnalyzer:
    """Analyze nested spans without claiming their sum is wall-clock or critical-path time."""

    def __init__(self, limits: PerformanceLimits) -> None:
        self._limits = limits

    def analyze(
        self,
        summary: TraceSummary,
        spans: tuple[TraceSpan, ...],
    ) -> PerformanceAnalysis:
        """Return deterministic slowest-stage/step/tool and retry-overhead observations."""
        stages = _aggregate(spans, SpanKind.GRAPH_NODE, key="name")
        steps = _aggregate(spans, SpanKind.STEP, key="step_id")
        tools = _aggregate(spans, SpanKind.TOOL, key="tool")
        slowest_stage, slowest_stage_latency = _slowest(stages)
        slowest_step, slowest_step_latency = _slowest(steps)
        slowest_tool, slowest_tool_latency = _slowest(tools)
        wall_clock = summary.total_latency_ms
        percentages = {
            name: (latency / wall_clock * 100 if wall_clock else 0.0)
            for name, latency in stages.items()
        }
        retry_overhead = sum(
            span.latency_ms or 0
            for span in spans
            if span.kind is SpanKind.TOOL and _attempt(span) > 1
        )
        external = sum(
            span.latency_ms or 0 for span in spans if span.kind is SpanKind.EXTERNAL_SERVICE
        )
        warnings: list[PerformanceWarning] = []
        task_limit_ms = self._limits.max_task_duration_seconds * 1000
        step_limit_ms = self._limits.max_step_duration_seconds * 1000
        if wall_clock > task_limit_ms:
            warnings.append(
                PerformanceWarning(
                    code="TASK_DURATION_LIMIT_EXCEEDED",
                    message="Task execution exceeded the configured duration budget.",
                    observed_value=wall_clock,
                    limit_value=task_limit_ms,
                )
            )
        for span in spans:
            if span.kind not in {SpanKind.GRAPH_NODE, SpanKind.STEP}:
                continue
            latency = span.latency_ms or 0
            if latency > step_limit_ms:
                warnings.append(
                    PerformanceWarning(
                        code="STEP_DURATION_LIMIT_EXCEEDED",
                        message="A graph node or step exceeded the configured duration budget.",
                        span_name=span.name,
                        observed_value=latency,
                        limit_value=step_limit_ms,
                    )
                )
        return PerformanceAnalysis(
            task_id=summary.task_id,
            trace_id=summary.trace_id,
            wall_clock_latency_ms=wall_clock,
            sum_of_span_latency_ms=sum(span.latency_ms or 0 for span in spans),
            critical_path_latency_ms=None,
            slowest_stage=slowest_stage,
            slowest_stage_latency_ms=slowest_stage_latency,
            slowest_step_id=slowest_step,
            slowest_step_latency_ms=slowest_step_latency,
            slowest_tool=slowest_tool,
            slowest_tool_latency_ms=slowest_tool_latency,
            retry_overhead_ms=retry_overhead,
            external_service_latency_ms=external,
            percentage_by_stage=percentages,
            warnings=tuple(warnings),
        )


def _aggregate(
    spans: tuple[TraceSpan, ...],
    kind: SpanKind,
    *,
    key: str,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for span in spans:
        if span.kind is not kind:
            continue
        name = (
            span.step_id
            if key == "step_id"
            else span.name.removeprefix("tool.")
            if key == "tool"
            else span.name
        )
        if not name:
            continue
        values[name] = values.get(name, 0.0) + (span.latency_ms or 0)
    return values


def _slowest(values: dict[str, float]) -> tuple[str | None, float | None]:
    if not values:
        return None, None
    name = max(values, key=lambda item: values[item])
    return name, values[name]


def _attempt(span: TraceSpan) -> int:
    value = span.attributes.root.get("attempt")
    return value if isinstance(value, int) and not isinstance(value, bool) else 1


__all__ = ["PerformanceAnalyzer", "PerformanceLimits"]
