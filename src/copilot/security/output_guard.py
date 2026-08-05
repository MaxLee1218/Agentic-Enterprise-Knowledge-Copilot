"""Central output scanning, redaction, and blocking before persistence or exposure."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from copilot.security.models import (
    ContentSourceType,
    OutputDisposition,
    RedactionRecord,
    SecurityFinding,
    SecuritySeverity,
)
from copilot.security.redaction import contains_secret
from copilot.security.sensitive_data import SensitiveDataRegistry


@dataclass(frozen=True, slots=True)
class _OutputRule:
    rule_id: str
    category: str
    severity: SecuritySeverity
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class OutputGuardResult:
    """Deterministic safe content and decision for one output boundary."""

    disposition: OutputDisposition
    content: JsonValue | None
    findings: tuple[SecurityFinding, ...]
    redactions: tuple[RedactionRecord, ...]


class OutputGuardBlockedError(ValueError):
    """Safe signal that persistence refused content under the shared output policy."""


_BLOCKING_RULES = (
    _OutputRule(
        "python_traceback",
        "STACK_TRACE",
        SecuritySeverity.CRITICAL,
        re.compile(r"Traceback \(most recent call last\)|File \"[^\"]+\.py\", line \d+"),
    ),
    _OutputRule(
        "internal_absolute_path",
        "INTERNAL_PATH",
        SecuritySeverity.HIGH,
        re.compile(
            r"(?<![A-Za-z0-9])(?:/(?:Users|home|var|private|opt|srv|workspace)/[^\s\"'<>]+|[A-Za-z]:\\(?:[^\\\s]+\\)+[^\s\"']+)"
        ),
    ),
    _OutputRule(
        "raw_sql",
        "RAW_SQL",
        SecuritySeverity.HIGH,
        re.compile(
            r"(?is)\bSELECT\b.{0,500}\bFROM\b|\b(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE)\b"
        ),
    ),
    _OutputRule(
        "database_url",
        "DATABASE_CONNECTION",
        SecuritySeverity.CRITICAL,
        re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|oracle|mssql)://[^\s]+"),
    ),
    _OutputRule(
        "system_prompt",
        "SYSTEM_PROMPT",
        SecuritySeverity.CRITICAL,
        re.compile(r"(?i)(?:BEGIN|END)\s+SYSTEM\s+PROMPT|system_prompt\s*[:=]"),
    ),
)


class OutputGuard:
    """Apply identical safety rules to structured reports and rendered text formats."""

    def __init__(self, registry: SensitiveDataRegistry | None = None) -> None:
        self._registry = registry or SensitiveDataRegistry()

    def guard(
        self,
        value: JsonValue,
        *,
        source_type: ContentSourceType,
        source_id: str,
        target: str = "report",
    ) -> OutputGuardResult:
        """Redact safe-to-repair fields and block content with severe disclosure indicators."""
        sensitive = self._registry.sanitize_json(value, target=target)
        findings: list[SecurityFinding] = []
        for path in sensitive.blocked_paths:
            findings.append(
                _finding(
                    category="SECRET_FIELD",
                    severity=SecuritySeverity.CRITICAL,
                    rule="sensitive_field_block",
                    source_type=source_type,
                    source_id=source_id,
                    field_path=path,
                    content=_text_for_hash(value),
                )
            )
        for path, text in _strings(sensitive.value):
            if contains_secret(text):
                findings.append(
                    _finding(
                        category="SECRET_DETECTED",
                        severity=SecuritySeverity.CRITICAL,
                        rule="secret_pattern",
                        source_type=source_type,
                        source_id=source_id,
                        field_path=path,
                        content=text,
                    )
                )
            for rule in _BLOCKING_RULES:
                if rule.pattern.search(text) is not None:
                    findings.append(
                        _finding(
                            category=rule.category,
                            severity=rule.severity,
                            rule=rule.rule_id,
                            source_type=source_type,
                            source_id=source_id,
                            field_path=path,
                            content=text,
                        )
                    )
        if findings:
            return OutputGuardResult(
                disposition=OutputDisposition.BLOCKED,
                content=None,
                findings=tuple(_deduplicate_findings(findings)),
                redactions=sensitive.redactions,
            )
        disposition = (
            OutputDisposition.ALLOWED_WITH_REDACTIONS
            if sensitive.redactions
            else OutputDisposition.ALLOWED
        )
        return OutputGuardResult(
            disposition=disposition,
            content=sensitive.value,
            findings=(),
            redactions=sensitive.redactions,
        )

    def guard_bytes(
        self,
        content: bytes,
        *,
        source_type: ContentSourceType,
        source_id: str,
        media_type: str,
    ) -> OutputGuardResult:
        """Scan UTF-8/JSON or conservatively decoded binary content with the same rules."""
        if media_type == "application/json":
            try:
                value = cast(JsonValue, json.loads(content.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = content.decode("utf-8", errors="replace")
        else:
            value = content.decode("latin-1", errors="replace")
        return self.guard(
            value,
            source_type=source_type,
            source_id=source_id,
            target="artifact",
        )


def _strings(value: JsonValue, path: str = "") -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path, value))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(_strings(child, f"{path}.{key}" if path else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_strings(child, f"{path}[{index}]"))
    return tuple(found)


def _finding(
    *,
    category: str,
    severity: SecuritySeverity,
    rule: str,
    source_type: ContentSourceType,
    source_id: str,
    field_path: str,
    content: str,
) -> SecurityFinding:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    finding_seed = f"{source_id}:{field_path}:{rule}:{digest}".encode()
    return SecurityFinding(
        finding_id=f"SF-{hashlib.sha256(finding_seed).hexdigest()[:24]}",
        category=category,
        severity=severity,
        source_type=source_type,
        source_id=source_id,
        matched_rule=rule,
        content_hash=digest,
        recommended_action="BLOCK_OUTPUT",
        field_path=field_path,
    )


def _text_for_hash(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _deduplicate_findings(findings: list[SecurityFinding]) -> list[SecurityFinding]:
    return list({finding.finding_id: finding for finding in findings}.values())


__all__ = ["OutputGuard", "OutputGuardBlockedError", "OutputGuardResult"]
