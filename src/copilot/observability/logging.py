"""Safe JSON structured logging built on the standard-library logging package."""

from __future__ import annotations

import json
import logging
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Final, cast

from copilot.observability.context import ObservabilityContextManager
from copilot.observability.sanitization import sanitize_text, sanitize_value
from copilot.security.redaction import redact_for_logging
from copilot.services.observability import EventName

_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_FORMATTER_MARKER: Final = "_copilot_structured_handler"


class SensitiveDataFilter(logging.Filter):
    """Sanitize messages, arguments, extra fields, and exception summaries in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Always retain the event while replacing unsafe values before formatting."""
        if isinstance(record.msg, str):
            record.msg = sanitize_text(record.msg)
        else:
            record.msg = redact_for_logging(record.msg)
        record.args = redact_for_logging(record.args)  # type: ignore[assignment]
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_")
        }
        safe_extra = redact_for_logging(extra)
        if isinstance(safe_extra, dict):
            record.__dict__.update(safe_extra)
        if record.exc_info is not None:
            exception = record.exc_info[1]
            record.__dict__["exception_summary"] = (
                {
                    "exception_type": type(exception).__name__,
                    "message": sanitize_text(str(exception)),
                }
                if exception is not None
                else {"exception_type": "Exception", "message": "Internal error"}
            )
            record.exc_info = None
            record.exc_text = None
        return True


class JsonLogFormatter(logging.Formatter):
    """Render one bounded JSON object with UTC time and current correlation fields."""

    def __init__(
        self,
        context: ObservabilityContextManager | None = None,
        *,
        max_summary_length: int = 512,
    ) -> None:
        super().__init__()
        self._context = context or ObservabilityContextManager()
        self._max_summary_length = max_summary_length

    def format(self, record: logging.LogRecord) -> str:
        """Format a sanitized record without paths, stack traces, SQL, or raw payloads."""
        context = self._context.current
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": sanitize_text(str(getattr(record, "event", record.msg)), max_length=128),
            "message": sanitize_text(record.getMessage(), max_length=self._max_summary_length),
        }
        for name in (
            "task_id",
            "trace_id",
            "step_id",
            "node_name",
            "tool_name",
            "request_id",
            "tenant_id",
            "user_id",
            "session_id",
        ):
            value = getattr(context, name)
            if value is not None:
                payload[name] = value
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_") or key in payload:
                continue
            payload[key] = sanitize_value(value, max_length=self._max_summary_length)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TextLogFormatter(logging.Formatter):
    """Compact development formatter that retains stable event and trace fields."""

    def __init__(self, *, max_summary_length: int = 512) -> None:
        super().__init__()
        self._max_summary_length = max_summary_length

    def format(self, record: logging.LogRecord) -> str:
        event = sanitize_text(str(getattr(record, "event", record.msg)), max_length=128)
        message = sanitize_text(record.getMessage(), max_length=self._max_summary_length)
        return f"{record.levelname} {event}: {message}"


class StructuredEventLogger:
    """Small injected facade that emits stable events with safe structured fields."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        max_summary_length: int = 512,
    ) -> None:
        self._logger = logger or logging.getLogger("copilot.observability")
        self._max_summary_length = max_summary_length

    def emit(
        self,
        event: str,
        *,
        level: int = logging.INFO,
        message: str | None = None,
        fields: MutableMapping[str, object] | None = None,
    ) -> None:
        """Emit one event; logging/export failures never replace a business result."""
        safe = sanitize_log_mapping(fields or {}, max_summary_length=self._max_summary_length)
        try:
            self._logger.log(level, message or event, extra={"event": event, **safe})
        except Exception:
            # Do not recursively log an exporter failure through the same broken handler.
            return


def install_logging_redaction() -> SensitiveDataFilter:
    """Attach one shared filter to current root and package handlers."""
    filter_instance = SensitiveDataFilter()
    loggers = (logging.getLogger(), logging.getLogger("copilot"))
    for logger in loggers:
        for handler in logger.handlers:
            if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
                handler.addFilter(filter_instance)
    return filter_instance


def configure_logging(
    *,
    level: str = "INFO",
    log_format: str = "json",
    context: ObservabilityContextManager | None = None,
    max_summary_length: int = 512,
) -> None:
    """Idempotently configure the root handler for JSON or compact development text."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = next(
        (item for item in root.handlers if getattr(item, _FORMATTER_MARKER, False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        setattr(handler, _FORMATTER_MARKER, True)
        root.addHandler(handler)
    handler.setLevel(level)
    handler.setFormatter(
        JsonLogFormatter(context, max_summary_length=max_summary_length)
        if log_format == "json"
        else TextLogFormatter(max_summary_length=max_summary_length)
    )
    if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
        handler.addFilter(SensitiveDataFilter())


def sanitize_log_mapping(
    values: MutableMapping[str, object], *, max_summary_length: int = 512
) -> dict[str, object]:
    """Return a detached safe mapping for structured exporters and tests."""
    safe = sanitize_value(redact_for_logging(values), max_length=max_summary_length)
    return cast(dict[str, object], safe) if isinstance(safe, dict) else {"value": safe}


__all__ = [
    "EventName",
    "JsonLogFormatter",
    "SensitiveDataFilter",
    "StructuredEventLogger",
    "TextLogFormatter",
    "configure_logging",
    "install_logging_redaction",
    "sanitize_log_mapping",
]
