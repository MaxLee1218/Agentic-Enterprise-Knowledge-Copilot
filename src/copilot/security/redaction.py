"""Recursive log/API redaction for secret-shaped keys, values, and exception messages."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import fields, is_dataclass

from pydantic import BaseModel

from copilot.security.sensitive_data import SensitiveDataRegistry

_REDACTED = "[REDACTED]"
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"(?i)\bapi[-_]?key[-_=:\s]+[A-Za-z0-9._~+/=-]{4,}"),
    re.compile(
        r"(?i)\b(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd)\s*[:=]\s*['\"]?[^\s,'\"}]{4,}"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb)://[^\s:/]+:[^\s@/]+@[^\s]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def redact_text(value: str) -> str:
    """Replace secret-shaped substrings without returning the matched value."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def contains_secret(value: str) -> bool:
    """Return whether text contains a configured secret-shaped pattern."""
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)


def redact_for_logging(value: object) -> object:
    """Recursively convert common runtime values into a safe logging representation."""
    registry = SensitiveDataRegistry()
    return _redact(value, registry=registry, active=set())


def _redact(value: object, *, registry: SensitiveDataRegistry, active: set[int]) -> object:
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, BaseException):
        return {"exception_type": type(value).__name__, "message": redact_text(str(value))}
    identity = id(value)
    if identity in active:
        return "[RECURSIVE]"
    active.add(identity)
    try:
        if isinstance(value, BaseModel):
            return _redact(value.model_dump(mode="json"), registry=registry, active=active)
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: _redact(getattr(value, field.name), registry=registry, active=active)
                for field in fields(value)
            }
        if isinstance(value, Mapping):
            cleaned: dict[str, object] = {}
            for key, child in value.items():
                name = str(key)
                if registry.policy_for(name) is not None:
                    cleaned[name] = _REDACTED
                else:
                    cleaned[name] = _redact(child, registry=registry, active=active)
            return cleaned
        if isinstance(value, tuple):
            return tuple(_redact(child, registry=registry, active=active) for child in value)
        if isinstance(value, list | set | frozenset):
            return [_redact(child, registry=registry, active=active) for child in value]
        return redact_text(str(value))
    finally:
        active.remove(identity)


__all__ = ["contains_secret", "redact_for_logging", "redact_text"]
