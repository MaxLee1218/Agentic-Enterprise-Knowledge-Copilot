"""Lightweight prompt-injection detection used only as a risk signal and isolator."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from copilot.security.models import ContentSourceType, SecurityFinding, SecuritySeverity, TrustLevel


@dataclass(frozen=True, slots=True)
class _InjectionRule:
    rule_id: str
    category: str
    severity: SecuritySeverity
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class InjectionScanResult:
    """Sanitized text and safe findings for one untrusted content fragment."""

    content: str
    trust_level: TrustLevel
    findings: tuple[SecurityFinding, ...]
    quarantined: bool


_RULES = (
    _InjectionRule(
        "instruction_override",
        "INSTRUCTION_OVERRIDE",
        SecuritySeverity.HIGH,
        re.compile(
            r"(?i)\b(?:ignore|disregard|override)\b.{0,80}\b(?:previous|prior|system|rules?|instructions?)\b"
        ),
    ),
    _InjectionRule(
        "system_prompt_exfiltration",
        "PROMPT_EXFILTRATION",
        SecuritySeverity.CRITICAL,
        re.compile(r"(?i)\b(?:reveal|show|print|return|expose)\b.{0,60}\bsystem\s+prompt\b"),
    ),
    _InjectionRule(
        "privilege_claim",
        "PRIVILEGE_ESCALATION",
        SecuritySeverity.HIGH,
        re.compile(r"(?i)\b(?:user|caller|i)\b.{0,40}\b(?:administrator|admin|root)\b"),
    ),
    _InjectionRule(
        "approval_bypass",
        "APPROVAL_BYPASS",
        SecuritySeverity.HIGH,
        re.compile(r"(?i)\b(?:skip|bypass|disable|ignore)\b.{0,50}\bapproval\b"),
    ),
    _InjectionRule(
        "tool_injection",
        "TOOL_INJECTION",
        SecuritySeverity.CRITICAL,
        re.compile(
            r"(?i)\b(?:call|execute|run|use)\b.{0,60}\b(?:database_write|shell_command|unregistered\s+tool|new\s+tool|python)\b"
        ),
    ),
    _InjectionRule(
        "sensitive_exfiltration",
        "SENSITIVE_DATA_EXFILTRATION",
        SecuritySeverity.CRITICAL,
        re.compile(
            r"(?i)\b(?:return|reveal|extract|query|show)\b.{0,80}\b(?:salary|bank\s+account|password|secret|token)\b"
        ),
    ),
    _InjectionRule(
        "evidence_bypass",
        "EVIDENCE_BYPASS",
        SecuritySeverity.HIGH,
        re.compile(r"(?i)\b(?:do\s+not|don't|skip|ignore)\b.{0,50}\b(?:cite|citation|evidence)\b"),
    ),
    _InjectionRule(
        "plan_replacement",
        "PLAN_REPLACEMENT",
        SecuritySeverity.HIGH,
        re.compile(
            r"(?i)\b(?:replace|change|rewrite)\b.{0,60}\b(?:task\s+plan|execution\s+plan)\b"
        ),
    ),
)

_SEVERITY_ORDER = {
    SecuritySeverity.NONE: 0,
    SecuritySeverity.LOW: 1,
    SecuritySeverity.MEDIUM: 2,
    SecuritySeverity.HIGH: 3,
    SecuritySeverity.CRITICAL: 4,
}


class PromptInjectionDetector:
    """Detect and remove instruction-shaped segments without authorizing any action."""

    def scan(
        self,
        content: str,
        *,
        source_type: ContentSourceType,
        source_id: str,
    ) -> InjectionScanResult:
        """Return findings and a minimized safe fragment; never retain matched text in findings."""
        findings: list[SecurityFinding] = []
        safe_segments: list[str] = []
        segments = _segments(content)
        for index, segment in enumerate(segments):
            matched = [rule for rule in _RULES if rule.pattern.search(segment)]
            if not matched:
                if segment.strip():
                    safe_segments.append(segment.strip())
                continue
            digest = hashlib.sha256(segment.encode("utf-8")).hexdigest()
            for rule in matched:
                findings.append(
                    SecurityFinding(
                        finding_id=_finding_id(source_id, index, rule.rule_id, digest),
                        category=rule.category,
                        severity=rule.severity,
                        source_type=source_type,
                        source_id=source_id,
                        matched_rule=rule.rule_id,
                        content_hash=digest,
                        recommended_action="REMOVE_INSTRUCTION_SEGMENT",
                    )
                )
        sanitized = " ".join(safe_segments).strip()
        quarantined = bool(findings) and not sanitized
        trust_level = (
            TrustLevel.QUARANTINED
            if quarantined
            else TrustLevel.SANITIZED
            if findings
            else TrustLevel.UNTRUSTED
        )
        return InjectionScanResult(
            content=sanitized if sanitized else "[QUARANTINED UNTRUSTED CONTENT]",
            trust_level=trust_level,
            findings=tuple(findings),
            quarantined=quarantined,
        )

    @staticmethod
    def maximum_severity(findings: tuple[SecurityFinding, ...]) -> SecuritySeverity:
        """Return the highest stable severity in a finding collection."""
        return max(
            (finding.severity for finding in findings),
            key=lambda value: _SEVERITY_ORDER[value],
            default=SecuritySeverity.NONE,
        )


def _segments(content: str) -> tuple[str, ...]:
    return tuple(
        segment
        for line in content.splitlines() or (content,)
        for segment in re.split(r"(?<=[.!?。！？])\s*", line)
        if segment.strip()
    )


def _finding_id(source_id: str, index: int, rule_id: str, digest: str) -> str:
    value = f"{source_id}:{index}:{rule_id}:{digest}".encode()
    return f"SF-{hashlib.sha256(value).hexdigest()[:24]}"


__all__ = ["InjectionScanResult", "PromptInjectionDetector"]
