"""Thread-safe bounded in-process Counter, Gauge, and Histogram registry."""

from __future__ import annotations

import math
import re
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock

from copilot.contracts import HistogramSnapshot, MetricSnapshot
from copilot.contracts.validators import utc_now

COUNTER_NAMES = frozenset(
    {
        "requests_total",
        "tasks_started_total",
        "tasks_completed_total",
        "tasks_failed_total",
        "tasks_cancelled_total",
        "task_resumes_total",
        "graph_node_executions_total",
        "graph_node_failures_total",
        "tool_executions_total",
        "tool_successes_total",
        "tool_failures_total",
        "tool_timeouts_total",
        "tool_retries_total",
        "approvals_requested_total",
        "approvals_approved_total",
        "approvals_rejected_total",
        "replans_total",
        "plan_repairs_total",
        "verification_failures_total",
        "performance_limit_exceeded_total",
        "lease_acquire_conflicts",
        "lease_expirations",
        "task_recoveries",
        "recovery_failures",
        "runtime_retry_count",
    }
)
GAUGE_NAMES = frozenset(
    {
        "active_tasks",
        "active_tool_calls",
        "task_queue_depth",
        "task_queue_oldest_age_seconds",
        "active_workers",
        "active_execution_leases",
        "waiting_approval_count",
    }
)
HISTOGRAM_NAMES = frozenset(
    {
        "request_latency_ms",
        "task_latency_ms",
        "graph_node_latency_ms",
        "step_latency_ms",
        "tool_latency_ms",
        "external_service_latency_ms",
        "task_queue_wait_seconds",
        "task_execution_seconds",
        "cancel_latency_seconds",
    }
)
LABEL_ALLOWLIST = frozenset(
    {
        "approval_status",
        "error_type",
        "http_method",
        "http_status",
        "node_name",
        "route_template",
        "status",
        "task_type",
        "tool_name",
    }
)
_SAFE_LABEL_VALUE = re.compile(r"^[A-Za-z0-9_.:/{}-]{1,96}$")


@dataclass(slots=True)
class _Histogram:
    samples: deque[float]
    count: int = 0
    total: float = 0
    minimum: float | None = None
    maximum: float | None = None

    def record(self, value: float) -> None:
        self.samples.append(value)
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)


@dataclass(slots=True)
class MetricsRegistry:
    """A replaceable process-local registry with a bounded latency sample window."""

    window_size: int = 1000
    clock: Callable[[], datetime] = utc_now
    _counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(
        init=False, default_factory=dict
    )
    _gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        init=False, default_factory=dict
    )
    _histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = field(
        init=False, default_factory=dict
    )
    _lock: RLock = field(init=False, default_factory=RLock)

    def __post_init__(self) -> None:
        if self.window_size < 1:
            raise ValueError("metrics window_size must be positive")

    def increment(
        self,
        name: str,
        amount: int = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Increase an allowlisted counter by a non-negative integer."""
        if name not in COUNTER_NAMES:
            raise ValueError(f"unknown counter: {name}")
        if amount < 0:
            raise ValueError("counter increments must not be negative")
        key = (name, _labels(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def gauge_add(
        self,
        name: str,
        amount: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Atomically add a finite value to an allowlisted gauge."""
        if name not in GAUGE_NAMES:
            raise ValueError(f"unknown gauge: {name}")
        if not math.isfinite(amount):
            raise ValueError("gauge value must be finite")
        key = (name, _labels(labels))
        with self._lock:
            self._gauges[key] = self._gauges.get(key, 0) + amount

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Set an allowlisted gauge to one finite value."""
        if name not in GAUGE_NAMES:
            raise ValueError(f"unknown gauge: {name}")
        if not math.isfinite(value):
            raise ValueError("gauge value must be finite")
        with self._lock:
            self._gauges[(name, _labels(labels))] = value

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Record a finite non-negative sample in a bounded reservoir window."""
        if name not in HISTOGRAM_NAMES:
            raise ValueError(f"unknown histogram: {name}")
        if not math.isfinite(value) or value < 0:
            raise ValueError("histogram observations must be finite and non-negative")
        key = (name, _labels(labels))
        with self._lock:
            histogram = self._histograms.get(key)
            if histogram is None:
                histogram = _Histogram(deque(maxlen=self.window_size))
                self._histograms[key] = histogram
            histogram.record(value)

    def snapshot(self) -> MetricSnapshot:
        """Return a detached snapshot with nearest-rank p50/p95 and failure rates."""
        with self._lock:
            counters = {_series_name(*key): value for key, value in self._counters.items()}
            gauges = {_series_name(*key): value for key, value in self._gauges.items()}
            histograms = {
                _series_name(*key): HistogramSnapshot(
                    count=value.count,
                    window_count=len(value.samples),
                    minimum=value.minimum,
                    maximum=value.maximum,
                    total=value.total,
                )
                for key, value in self._histograms.items()
            }
            quantiles = {
                _series_name(*key): {
                    "p50": _quantile(tuple(value.samples), 0.50),
                    "p95": _quantile(tuple(value.samples), 0.95),
                }
                for key, value in self._histograms.items()
            }
            rates = _failure_rates(self._counters)
        return MetricSnapshot(
            generated_at=self.clock(),
            counters=counters,
            gauges=gauges,
            histograms=histograms,
            quantiles=quantiles,
            failure_rates=rates,
        )

    def reset(self) -> None:
        """Clear process-local samples for isolated deterministic tests."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


def _labels(values: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not values:
        return ()
    normalized: list[tuple[str, str]] = []
    for name, value in sorted(values.items()):
        if name not in LABEL_ALLOWLIST:
            raise ValueError(f"metric label is not allowlisted: {name}")
        if not _SAFE_LABEL_VALUE.fullmatch(value):
            raise ValueError(f"metric label value is unsafe: {name}")
        normalized.append((name, value))
    return tuple(normalized)


def _series_name(name: str, labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return name
    rendered = ",".join(f"{key}={value}" for key, value in labels)
    return f"{name}{{{rendered}}}"


def _quantile(samples: tuple[float, ...], quantile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _failure_rates(
    counters: Mapping[tuple[str, tuple[tuple[str, str], ...]], int],
) -> dict[str, float]:
    executions = 0
    failures = 0
    executions_by_tool: dict[str, int] = {}
    failures_by_tool: dict[str, int] = {}
    for (name, labels), value in counters.items():
        label_map = dict(labels)
        tool = label_map.get("tool_name")
        if name == "tool_executions_total":
            executions += value
            if tool is not None:
                executions_by_tool[tool] = executions_by_tool.get(tool, 0) + value
        elif name in {"tool_failures_total", "tool_timeouts_total"}:
            failures += value
            if tool is not None:
                failures_by_tool[tool] = failures_by_tool.get(tool, 0) + value
    rates = {"tool_attempt_failure_rate": failures / executions if executions else 0.0}
    for tool, total in sorted(executions_by_tool.items()):
        rates[f"tool_attempt_failure_rate{{tool_name={tool}}}"] = (
            failures_by_tool.get(tool, 0) / total if total else 0.0
        )
    return rates


__all__ = [
    "COUNTER_NAMES",
    "GAUGE_NAMES",
    "HISTOGRAM_NAMES",
    "LABEL_ALLOWLIST",
    "MetricsRegistry",
]
