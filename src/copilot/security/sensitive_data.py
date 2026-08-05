"""Central sensitive-field registry and deterministic JSON redaction policy."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from pydantic import JsonValue

from copilot.security.models import RedactionRecord


class DataClassification(StrEnum):
    """Security classifications used by the shared registry."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    SECRET = "SECRET"


class RedactionStrategy(StrEnum):
    """Supported deterministic redaction strategies."""

    MASK = "MASK"
    LAST_FOUR = "LAST_FOUR"
    REMOVE = "REMOVE"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class SensitiveFieldPolicy:
    """One canonical sensitive field and its output behavior."""

    canonical_name: str
    aliases: tuple[str, ...]
    classification: DataClassification
    strategy: RedactionStrategy


@dataclass(frozen=True, slots=True)
class SensitiveSanitizationResult:
    """Recursive redaction result for one JSON-compatible value."""

    value: JsonValue
    redactions: tuple[RedactionRecord, ...]
    blocked_paths: tuple[str, ...]


_FIELD_POLICIES = (
    SensitiveFieldPolicy(
        "personal_email",
        ("personal_email", "private_email", "email_address"),
        DataClassification.CONFIDENTIAL,
        RedactionStrategy.MASK,
    ),
    SensitiveFieldPolicy(
        "phone",
        ("phone", "phone_number", "mobile", "mobile_number"),
        DataClassification.CONFIDENTIAL,
        RedactionStrategy.MASK,
    ),
    SensitiveFieldPolicy(
        "bank_account",
        ("bank_account", "bank_account_number", "iban"),
        DataClassification.RESTRICTED,
        RedactionStrategy.LAST_FOUR,
    ),
    SensitiveFieldPolicy(
        "salary",
        ("salary", "base_salary", "compensation"),
        DataClassification.RESTRICTED,
        RedactionStrategy.REMOVE,
    ),
    SensitiveFieldPolicy(
        "government_id",
        ("government_id", "national_id", "ssn", "passport_number"),
        DataClassification.RESTRICTED,
        RedactionStrategy.MASK,
    ),
    SensitiveFieldPolicy(
        "password_hash",
        ("password_hash", "passwd_hash"),
        DataClassification.SECRET,
        RedactionStrategy.BLOCK,
    ),
    SensitiveFieldPolicy(
        "secret",
        ("secret", "client_secret", "api_secret", "private_key"),
        DataClassification.SECRET,
        RedactionStrategy.BLOCK,
    ),
    SensitiveFieldPolicy(
        "token",
        ("token", "access_token", "refresh_token", "id_token", "api_token"),
        DataClassification.SECRET,
        RedactionStrategy.BLOCK,
    ),
    SensitiveFieldPolicy(
        "password",
        ("password", "passwd", "database_password"),
        DataClassification.SECRET,
        RedactionStrategy.BLOCK,
    ),
    SensitiveFieldPolicy(
        "authorization",
        ("authorization", "proxy_authorization", "cookie", "set_cookie"),
        DataClassification.SECRET,
        RedactionStrategy.BLOCK,
    ),
)


class SensitiveDataRegistry:
    """Deny-by-default registry for field-name-based sensitive-data handling."""

    def __init__(self) -> None:
        aliases: dict[str, SensitiveFieldPolicy] = {}
        for policy in _FIELD_POLICIES:
            for alias in policy.aliases:
                aliases[_normalize_key(alias)] = policy
        self._aliases = aliases

    def policy_for(self, field_name: str) -> SensitiveFieldPolicy | None:
        """Return a policy for a field name or alias without fuzzy value matching."""
        return self._aliases.get(_normalize_key(field_name))

    def sensitive_names(self) -> tuple[str, ...]:
        """Return canonical field names for documentation and deterministic tests."""
        return tuple(policy.canonical_name for policy in _FIELD_POLICIES)

    def sanitize_json(self, value: JsonValue, *, target: str) -> SensitiveSanitizationResult:
        """Recursively sanitize structured values for report, Evidence, API, or logs."""
        redactions: list[RedactionRecord] = []
        blocked: list[str] = []

        def visit(current: JsonValue, path: str) -> JsonValue:
            if isinstance(current, dict):
                cleaned: dict[str, JsonValue] = {}
                for key, child in current.items():
                    child_path = f"{path}.{key}" if path else key
                    policy = self.policy_for(key)
                    if policy is None:
                        cleaned[key] = visit(child, child_path)
                        continue
                    strategy = _strategy_for_target(policy.strategy, target)
                    if strategy is RedactionStrategy.BLOCK:
                        blocked.append(child_path)
                        cleaned[key] = "[REDACTED]"
                    elif strategy is RedactionStrategy.REMOVE:
                        pass
                    else:
                        cleaned[key] = _redacted_value(child, strategy)
                    redactions.append(
                        RedactionRecord(
                            field_path=child_path,
                            classification=policy.classification.value,
                            strategy=strategy.value,
                            original_hash=_stable_hash(child),
                        )
                    )
                return cast(JsonValue, cleaned)
            if isinstance(current, list):
                return cast(
                    JsonValue,
                    [visit(child, f"{path}[{index}]") for index, child in enumerate(current)],
                )
            return current

        sanitized = visit(value, "")
        return SensitiveSanitizationResult(
            value=sanitized,
            redactions=tuple(redactions),
            blocked_paths=tuple(blocked),
        )


def _strategy_for_target(strategy: RedactionStrategy, target: str) -> RedactionStrategy:
    if target == "log":
        return RedactionStrategy.MASK
    if target in {"prompt", "api"} and strategy is RedactionStrategy.BLOCK:
        return RedactionStrategy.MASK
    return strategy


def _redacted_value(value: JsonValue, strategy: RedactionStrategy) -> JsonValue:
    if strategy is RedactionStrategy.LAST_FOUR:
        text = str(value)
        return f"***{text[-4:]}" if len(text) >= 4 else "***"
    if strategy is RedactionStrategy.REMOVE:
        return "[REMOVED]"
    return "[REDACTED]"


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _stable_hash(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = str(type(value).__name__).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DataClassification",
    "RedactionStrategy",
    "SensitiveDataRegistry",
    "SensitiveFieldPolicy",
    "SensitiveSanitizationResult",
]
