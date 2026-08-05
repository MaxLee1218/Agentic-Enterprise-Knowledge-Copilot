"""Small serializable security values shared across governed boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ContentSourceType(StrEnum):
    """Origin labels that prevent untrusted data from acquiring instruction authority."""

    SYSTEM_INSTRUCTION = "SYSTEM_INSTRUCTION"
    USER_INPUT = "USER_INPUT"
    RETRIEVED_DOCUMENT = "RETRIEVED_DOCUMENT"
    DATABASE_RESULT = "DATABASE_RESULT"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    LLM_OUTPUT = "LLM_OUTPUT"
    APPROVAL_INPUT = "APPROVAL_INPUT"
    INTERNAL_CONFIGURATION = "INTERNAL_CONFIGURATION"


class TrustLevel(StrEnum):
    """Trust labels used at prompt, Evidence, and tool-output boundaries."""

    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"
    SANITIZED = "SANITIZED"
    QUARANTINED = "QUARANTINED"


class SecuritySeverity(StrEnum):
    """Stable security finding severities."""

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class OutputDisposition(StrEnum):
    """Final deterministic decision for content leaving a governed boundary."""

    ALLOWED = "ALLOWED"
    ALLOWED_WITH_REDACTIONS = "ALLOWED_WITH_REDACTIONS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    """Safe finding metadata that never retains the matched sensitive text."""

    finding_id: str
    category: str
    severity: SecuritySeverity
    source_type: ContentSourceType
    source_id: str
    matched_rule: str
    content_hash: str
    recommended_action: str
    field_path: str = ""


@dataclass(frozen=True, slots=True)
class RedactionRecord:
    """A safe record of one redaction without the removed value."""

    field_path: str
    classification: str
    strategy: str
    original_hash: str


__all__ = [
    "ContentSourceType",
    "OutputDisposition",
    "RedactionRecord",
    "SecurityFinding",
    "SecuritySeverity",
    "TrustLevel",
]
