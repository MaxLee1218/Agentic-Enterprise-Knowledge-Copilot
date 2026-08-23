"""Deterministic claim, deliverable, and verification contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, JsonValue, field_validator, model_validator

from copilot.contracts.approvals import ApprovalRequest
from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import TaskType
from copilot.contracts.tools import ToolCall, ToolDefinition, ToolResult
from copilot.contracts.validators import utc_now, validate_identifier, validate_utc_datetime


class VerificationStatus(StrEnum):
    """Aggregated deterministic verification outcome."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"


class VerificationSeverity(StrEnum):
    """Audit severity for one deterministic verification issue."""

    WARNING = "WARNING"
    ERROR = "ERROR"


class ClaimType(StrEnum):
    """Supported structured claim categories without extending EvidenceType."""

    POLICY = "POLICY"
    DATA = "DATA"
    NUMERIC = "NUMERIC"
    GENERAL = "GENERAL"


class VerificationIssue(ImmutableContractModel):
    """One safe, structured failure or warning emitted by a verifier."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: VerificationSeverity
    verifier: str = Field(min_length=1)
    task_id: str
    step_id: str | None = None
    claim_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    details: JsonObject = Field(default_factory=lambda: JsonObject({}))

    _validate_task_id = field_validator("task_id")(validate_identifier)


class VerificationCheck(ImmutableContractModel):
    """Summary of one named verifier execution."""

    verifier: str = Field(min_length=1)
    passed: bool
    issue_codes: tuple[str, ...] = ()
    verified_evidence_ids: tuple[str, ...] = ()


class VerificationResult(ImmutableContractModel):
    """Serializable result of a complete deterministic verification pass."""

    task_id: str
    trace_id: str | None = None
    status: VerificationStatus
    issues: tuple[VerificationIssue, ...]
    checks: tuple[VerificationCheck, ...]
    verified_at: datetime = Field(default_factory=utc_now)
    duration_ms: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    verified_evidence_ids: tuple[str, ...] = ()

    _validate_task_id = field_validator("task_id")(validate_identifier)
    _validate_verified_at = field_validator("verified_at")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_aggregate(self) -> VerificationResult:
        """Require counts and status to agree with the issue collection."""
        warning_count = sum(issue.severity is VerificationSeverity.WARNING for issue in self.issues)
        error_count = sum(issue.severity is VerificationSeverity.ERROR for issue in self.issues)
        if warning_count != self.warning_count or error_count != self.error_count:
            raise ValueError("verification issue counts do not match issues")
        expected = (
            VerificationStatus.FAILED
            if error_count
            else (
                VerificationStatus.PASSED_WITH_WARNINGS
                if warning_count
                else VerificationStatus.PASSED
            )
        )
        if self.status is not expected:
            raise ValueError("verification status does not match issue severities")
        return self


class DeliverableRecord(ImmutableContractModel):
    """Structured output record mapped from the frozen report contract."""

    deliverable_id: str
    producing_step_id: str
    content: JsonValue
    evidence_ids: tuple[str, ...] = ()
    empty_result: bool = False

    _validate_ids = field_validator("deliverable_id", "producing_step_id")(validate_identifier)


class CitationClaim(ImmutableContractModel):
    """A structured claim and its evidence references."""

    claim_id: str
    claim_type: ClaimType
    evidence_ids: tuple[str, ...]
    step_id: str | None = None
    policy_governed: bool = False
    rule_ids: tuple[str, ...] = ()

    _validate_claim_id = field_validator("claim_id")(validate_identifier)


class NumericClaim(ImmutableContractModel):
    """Exact numeric assertion mapped from a structured analytics result."""

    claim_id: str
    metric_name: str
    value: Decimal | int | None
    unit: str
    precision: int = Field(ge=0, le=12)
    evidence_ids: tuple[str, ...]
    dimensions: JsonObject = Field(default_factory=lambda: JsonObject({}))
    ranking: tuple[str, ...] = ()
    operation_name: str | None = None

    _validate_ids = field_validator("claim_id", "metric_name", "unit")(validate_identifier)

    @field_validator("value")
    @classmethod
    def reject_non_finite_value(cls, value: Decimal | int | None) -> Decimal | int | None:
        """Reject NaN and infinity at the structured claim boundary."""
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("numeric claim value must be finite")
        return value


class APReportClaimV1(ImmutableContractModel):
    """One explicit AP report claim mapped without natural-language parsing."""

    claim_id: str
    claim_type: ClaimType
    evidence_ids: tuple[str, ...]
    policy_governed: bool = False
    rule_ids: tuple[str, ...] = ()
    metric_name: str | None = None
    value: Decimal | int | None = None
    unit: str | None = None
    precision: int | None = Field(default=None, ge=0, le=12)
    operation_name: str | None = None
    dimensions: JsonObject = Field(default_factory=lambda: JsonObject({}))

    _validate_claim_id = field_validator("claim_id")(validate_identifier)

    @field_validator("evidence_ids", "rule_ids")
    @classmethod
    def validate_unique_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject blank or duplicate AP Evidence/rule references."""
        normalized = tuple(validate_identifier(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("AP report Evidence and rule references must be unique")
        return normalized

    @field_validator("value")
    @classmethod
    def reject_non_finite_value(cls, value: Decimal | int | None) -> Decimal | int | None:
        """Reject non-finite material AP values at the report boundary."""
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("AP report claim value must be finite")
        return value

    @model_validator(mode="after")
    def validate_claim_shape(self) -> APReportClaimV1:
        """Require deterministic baseline coordinates for every numeric AP claim."""
        if self.policy_governed and not self.rule_ids:
            raise ValueError("policy-governed AP claims require rule_ids")
        numeric_fields = (
            self.metric_name,
            self.unit,
            self.precision,
            self.operation_name,
        )
        if self.claim_type is ClaimType.NUMERIC:
            if any(value is None for value in numeric_fields):
                raise ValueError("numeric AP claims require metric, unit, precision, and operation")
            if not self.policy_governed:
                raise ValueError("numeric AP claims must retain their controlled rule binding")
        elif any(value is not None for value in numeric_fields) or self.value is not None:
            raise ValueError("non-numeric AP claims cannot define a numeric baseline")
        return self


class VerifierProfileV1(ImmutableContractModel):
    """Versioned domain allowlists and verifier-selection identity."""

    profile_id: str = Field(min_length=1)
    task_type: TaskType
    allowed_tables: tuple[str, ...]
    allowed_columns: tuple[str, ...]
    allowed_query_templates: tuple[str, ...]
    sensitive_fields: tuple[str, ...]

    @model_validator(mode="after")
    def validate_unique_sorted_allowlists(self) -> VerifierProfileV1:
        """Keep code-owned verification profiles canonical and unambiguous."""
        for name in (
            "allowed_tables",
            "allowed_columns",
            "allowed_query_templates",
            "sensitive_fields",
        ):
            values = getattr(self, name)
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{name} must be unique and sorted")
        return self


class CandidateResult(ImmutableContractModel):
    """Structured candidate output consumed by deterministic verifiers."""

    task_id: str
    deliverables: tuple[DeliverableRecord, ...]
    claims: tuple[CitationClaim, ...]
    numeric_claims: tuple[NumericClaim, ...]
    output_fields: tuple[str, ...] = ()

    _validate_task_id = field_validator("task_id")(validate_identifier)


class VerificationContext(ImmutableContractModel):
    """Serializable policy, approval, schema, and execution context."""

    trace_id: str | None = None
    verifier_profile_id: str | None = None
    registered_tools: tuple[ToolDefinition, ...]
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...]
    approvals: tuple[ApprovalRequest, ...] = ()
    allowed_tables: tuple[str, ...] = ()
    allowed_columns: tuple[str, ...] = ()
    allowed_query_templates: tuple[str, ...] = ()
    sensitive_fields: tuple[str, ...] = ()
    readonly_task: bool = True
