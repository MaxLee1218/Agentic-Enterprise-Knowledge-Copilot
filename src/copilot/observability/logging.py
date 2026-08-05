"""Central logging filter that recursively removes secrets and unsafe exception details."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping

from copilot.security.redaction import redact_for_logging, redact_text

_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


class SensitiveDataFilter(logging.Filter):
    """Sanitize messages, arguments, extra fields, and exception summaries in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Always retain the event while replacing unsafe values before formatting."""
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        else:
            record.msg = redact_for_logging(record.msg)
        record.args = redact_for_logging(record.args)  # type: ignore[assignment]
        for key in tuple(record.__dict__):
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            record.__dict__[key] = redact_for_logging(record.__dict__[key])
        if record.exc_info is not None:
            exception = record.exc_info[1]
            record.__dict__["exception_summary"] = (
                {
                    "exception_type": type(exception).__name__,
                    "message": redact_text(str(exception)),
                }
                if exception is not None
                else {"exception_type": "Exception", "message": "Internal error"}
            )
            record.exc_info = None
            record.exc_text = None
        return True


def install_logging_redaction() -> SensitiveDataFilter:
    """Attach one shared filter to current root and package handlers."""
    filter_instance = SensitiveDataFilter()
    loggers = (logging.getLogger(), logging.getLogger("copilot"))
    for logger in loggers:
        for handler in logger.handlers:
            if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
                handler.addFilter(filter_instance)
    return filter_instance


def sanitize_log_mapping(values: MutableMapping[str, object]) -> dict[str, object]:
    """Return a detached safe mapping for structured exporters and tests."""
    safe = redact_for_logging(values)
    return safe if isinstance(safe, dict) else {"value": safe}


__all__ = ["SensitiveDataFilter", "install_logging_redaction", "sanitize_log_mapping"]
