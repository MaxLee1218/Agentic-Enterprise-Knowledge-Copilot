"""ContextVar-based immutable correlation context with nested restoration."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from copilot.contracts import ObservabilityContext
from copilot.services.observability import validate_correlation_id

_CONTEXT: ContextVar[ObservabilityContext | None] = ContextVar(
    "copilot_observability_context",
    default=None,
)
_FIELDS = frozenset(ObservabilityContext.model_fields)


class ObservabilityContextManager:
    """Bind request/task/span fields without mutable global dictionaries."""

    @property
    def current(self) -> ObservabilityContext:
        """Return the immutable context for the current execution flow."""
        return _CONTEXT.get() or ObservabilityContext()

    @contextmanager
    def bind(self, **values: str | None) -> Iterator[ObservabilityContext]:
        """Overlay explicit fields and restore the exact parent context in ``finally``."""
        unknown = set(values).difference(_FIELDS)
        if unknown:
            raise ValueError(f"Unknown observability context field: {sorted(unknown)[0]}")
        current = self.current
        updates = {
            name: value
            for name, value in values.items()
            if value is not None or getattr(current, name) is not None
        }
        if (
            "trace_id" in values
            and values["trace_id"] != current.trace_id
            and "span_id" not in values
        ):
            # A parent Span ID is meaningful only inside its own Trace.
            updates["span_id"] = None
        bound = current.model_copy(update=updates)
        token = _CONTEXT.set(bound)
        try:
            yield bound
        finally:
            _CONTEXT.reset(token)

    def clear(self) -> None:
        """Clear the current flow, primarily for deterministic test cleanup."""
        _CONTEXT.set(ObservabilityContext())


__all__ = ["ObservabilityContextManager", "validate_correlation_id"]
