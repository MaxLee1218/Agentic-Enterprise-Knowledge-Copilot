"""Bounded observability-only sanitization layered on the Stage 15 redactor."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from copilot.contracts import JsonObject
from copilot.security.redaction import redact_for_logging, redact_text
from copilot.security.sensitive_data import SensitiveDataRegistry

_TRACEBACK = re.compile(r"(?is)Traceback \(most recent call last\):.*")
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home|srv|var|etc|opt|private)/[^\s,;]+")
_SQL = re.compile(
    r"(?is)\b(?:SELECT\s+.+?\s+FROM|INSERT\s+INTO|UPDATE\s+.+?\s+SET|DELETE\s+FROM|DROP\s+(?:TABLE|DATABASE)|ALTER\s+TABLE)\b.*"
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

TRACE_ATTRIBUTE_ALLOWLIST = frozenset(
    {
        "approval_count",
        "artifact_count",
        "attempt",
        "cancelled",
        "column_count",
        "error_code",
        "error_type",
        "evidence_count",
        "external_service",
        "http_method",
        "http_status",
        "input_field_count",
        "input_hash",
        "input_size",
        "model_name",
        "operation",
        "output_size",
        "query_hash",
        "record_count",
        "replan_count",
        "request_source",
        "resume_count",
        "retry_count",
        "route",
        "status",
        "task_status",
        "timeout_seconds",
        "token_count",
        "tool_version",
        "truncated",
    }
)


def sanitize_text(value: str, *, max_length: int = 512) -> str:
    """Remove secret, SQL, traceback, path, email, and phone content and bound the result."""
    cleaned = redact_text(value)
    cleaned = _TRACEBACK.sub("[TRACEBACK_REDACTED]", cleaned)
    cleaned = _SQL.sub(_hashed_marker("SQL", cleaned), cleaned)
    cleaned = _ABSOLUTE_PATH.sub("[PATH_REDACTED]", cleaned)
    cleaned = _EMAIL.sub("[EMAIL_REDACTED]", cleaned)
    cleaned = _PHONE.sub("[PHONE_REDACTED]", cleaned)
    if len(cleaned) > max_length:
        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]
        return f"{cleaned[:max_length]}...[TRUNCATED sha256:{digest}]"
    return cleaned


def sanitize_value(value: object, *, max_length: int = 512) -> JsonValue:
    """Return bounded JSON-safe data after applying the shared recursive redactor."""
    redacted = redact_for_logging(value)
    return _bounded(redacted, max_length=max_length, depth=0)


def sanitize_attributes(
    values: Mapping[str, object] | None,
    *,
    max_attributes: int,
    max_length: int,
) -> JsonObject:
    """Allow only controlled low-cardinality attribute names and bounded safe values."""
    if not values:
        return JsonObject({})
    registry = SensitiveDataRegistry()
    safe: dict[str, JsonValue] = {}
    for name in sorted(values):
        if len(safe) >= max_attributes:
            break
        if (
            name not in TRACE_ATTRIBUTE_ALLOWLIST
            or not _SAFE_KEY.fullmatch(name)
            or registry.policy_for(name) is not None
        ):
            continue
        safe[name] = sanitize_value(values[name], max_length=max_length)
    return JsonObject(safe)


def safe_summary(value: object, *, max_length: int = 512) -> dict[str, JsonValue]:
    """Describe a payload without retaining its raw content."""
    if value is None:
        return {"input_type": "none", "input_size": 0}
    type_name = type(value).__name__
    fields: list[str] = []
    record_count: int | None = None
    if isinstance(value, Mapping):
        fields = sorted(sanitize_text(str(key), max_length=64) for key in value)[:32]
        record_count = len(value)
    elif isinstance(value, list | tuple | set | frozenset):
        record_count = len(value)
    try:
        encoded = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        encoded = type_name.encode("utf-8")
    summary: dict[str, JsonValue] = {
        "input_type": type_name,
        "input_size": len(encoded),
        "input_hash": hashlib.sha256(encoded).hexdigest(),
    }
    if fields:
        summary["input_field_names"] = cast(JsonValue, fields)
    if record_count is not None:
        summary["record_count"] = record_count
    return summary


def _bounded(value: object, *, max_length: int, depth: int) -> JsonValue:
    if depth >= 5:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, bool | int | float):
        return cast(JsonValue, value)
    if isinstance(value, str):
        return sanitize_text(value, max_length=max_length)
    if isinstance(value, Mapping):
        bounded: dict[str, JsonValue] = {}
        registry = SensitiveDataRegistry()
        for key, child in list(value.items())[:64]:
            name = sanitize_text(str(key), max_length=64)
            bounded[name] = (
                "[REDACTED]"
                if registry.policy_for(name) is not None
                else _bounded(child, max_length=max_length, depth=depth + 1)
            )
        return bounded
    if isinstance(value, list | tuple | set | frozenset):
        return [
            _bounded(child, max_length=max_length, depth=depth + 1) for child in list(value)[:64]
        ]
    return sanitize_text(str(value), max_length=max_length)


def _hashed_marker(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"[{kind}_REDACTED sha256:{digest}]"


__all__ = [
    "TRACE_ATTRIBUTE_ALLOWLIST",
    "safe_summary",
    "sanitize_attributes",
    "sanitize_text",
    "sanitize_value",
]
