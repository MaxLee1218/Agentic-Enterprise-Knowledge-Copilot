"""Local observability adapters composed behind application-owned ports."""

from copilot.observability.context import ObservabilityContextManager, validate_correlation_id
from copilot.observability.instrumentation import InMemoryObservability
from copilot.observability.logging import JsonLogFormatter, StructuredEventLogger, configure_logging
from copilot.observability.metrics import MetricsRegistry
from copilot.observability.performance import PerformanceAnalyzer, PerformanceLimits
from copilot.observability.tracing import InMemoryTracer
from copilot.services.observability import EventName

__all__ = [
    "EventName",
    "InMemoryObservability",
    "InMemoryTracer",
    "JsonLogFormatter",
    "MetricsRegistry",
    "ObservabilityContextManager",
    "PerformanceAnalyzer",
    "PerformanceLimits",
    "StructuredEventLogger",
    "configure_logging",
    "validate_correlation_id",
]
