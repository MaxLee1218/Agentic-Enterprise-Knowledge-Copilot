"""Strong report input and document models for the frozen v1.0 capability."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from copilot.contracts import ContractModel, EvidenceType, JsonObject, ReportLanguage
from copilot.contracts.validators import validate_identifier, validate_utc_datetime
from copilot.tools.analytics.schemas import AnalyticsMetricResult, AnalyticsResult

REPORT_SCHEMA_VERSION: Literal["supplier_quality_report_model.v1"] = (
    "supplier_quality_report_model.v1"
)
REPORT_TEMPLATE_VERSION: Literal["supplier_quality_report.v1"] = "supplier_quality_report.v1"
REPORT_GENERATOR_VERSION: Literal["report_generator.v1"] = "report_generator.v1"


class ReportFormat(StrEnum):
    """Artifact formats authorized by the frozen report tool contract."""

    PDF = "PDF"
    JSON = "JSON"


class ReportScope(ContractModel):
    """Authorized business scope displayed in the report."""

    year: int = Field(ge=2000, le=9999)
    quarter: int = Field(ge=1, le=4)
    start_date: date
    end_date: date
    supplier_ids: tuple[str, ...]

    _validate_supplier_ids = field_validator("supplier_ids")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )

    @model_validator(mode="after")
    def validate_period(self) -> ReportScope:
        """Require one ordered range within the declared calendar quarter."""
        if self.start_date > self.end_date:
            raise ValueError("report scope start_date must not be after end_date")
        start_quarter = (self.start_date.month - 1) // 3 + 1
        end_quarter = (self.end_date.month - 1) // 3 + 1
        if (
            self.start_date.year != self.year
            or self.end_date.year != self.year
            or start_quarter != self.quarter
            or end_quarter != self.quarter
        ):
            raise ValueError("report scope must remain within the declared quarter")
        if len(set(self.supplier_ids)) != len(self.supplier_ids):
            raise ValueError("report scope supplier_ids must be unique")
        return self


class ReportRequest(ContractModel):
    """Exact frozen input accepted by ``report_generator``."""

    task_id: str
    scope: ReportScope
    analysis_result: AnalyticsResult
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    template_version: Literal["supplier_quality_report.v1"]
    format: ReportFormat
    language: ReportLanguage

    _validate_task_id = field_validator("task_id")(validate_identifier)
    _validate_evidence_ids = field_validator("evidence_refs")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> ReportRequest:
        """Reject ambiguous duplicate Evidence references."""
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("report evidence_refs must be unique")
        return self


class ReportTaskSummary(ContractModel):
    """Report identity and lifecycle context."""

    task_id: str
    trace_id: str
    task_status: Literal["EXECUTING"]

    _validate_ids = field_validator("task_id", "trace_id")(validate_identifier)


class ReportPolicyReference(ContractModel):
    """One policy or controlled-document reference."""

    evidence_id: str
    document_id: str
    document_version: str | None = None
    location: str
    excerpt: str

    _validate_id = field_validator("evidence_id")(validate_identifier)


class ReportDataSource(ContractModel):
    """One database data-coverage reference."""

    evidence_id: str
    query_id: str
    row_count: int = Field(ge=0)
    snapshot_at: str | None = None
    checksum: str

    _validate_ids = field_validator("evidence_id", "query_id")(validate_identifier)


class ReportFinding(ContractModel):
    """A deterministic observation copied from structured analytics output."""

    finding_id: str
    title: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    severity: Literal["INFO", "WARNING"]
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    _validate_ids = field_validator("finding_id")(validate_identifier)


class ReportRisk(ContractModel):
    """A bounded risk statement with explicit supporting Evidence."""

    risk_id: str
    statement: str = Field(min_length=1)
    level: Literal["INFORMATIONAL", "REVIEW_REQUIRED"]
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    _validate_id = field_validator("risk_id")(validate_identifier)


class ReportRecommendation(ContractModel):
    """A fixed-rule action, never unconstrained model prose."""

    action_id: str
    action: str = Field(min_length=1)
    basis: Literal["GENERAL_CONTROL", "POLICY_EVIDENCE"]
    evidence_ids: tuple[str, ...] = ()

    _validate_id = field_validator("action_id")(validate_identifier)


class ReportLimitation(ContractModel):
    """A structured qualification on report interpretation."""

    code: str
    statement: str = Field(min_length=1)

    _validate_code = field_validator("code")(validate_identifier)


class ReportEvidenceReference(ContractModel):
    """Human-readable Evidence index with query and calculation lineage."""

    evidence_id: str
    source_type: EvidenceType
    source_step_id: str
    source_tool_call_id: str
    source: JsonObject
    checksum: str
    query_id: str | None = None
    formulas: dict[str, str] = Field(default_factory=dict)
    input_evidence_ids: tuple[str, ...] = ()

    _validate_ids = field_validator("evidence_id", "source_step_id", "source_tool_call_id")(
        validate_identifier
    )


class ReportExecutionStep(ContractModel):
    """Evidence-producing execution entry shown without sensitive payloads."""

    step_id: str
    tool_call_id: str
    evidence_id: str
    source_type: EvidenceType

    _validate_ids = field_validator("step_id", "tool_call_id", "evidence_id")(validate_identifier)


class ReportExecutionMetadata(ContractModel):
    """Generator, schema, language, and timestamp provenance."""

    generated_at: datetime
    schema_version: Literal["supplier_quality_report_model.v1"]
    template_version: Literal["supplier_quality_report.v1"]
    generator_version: Literal["report_generator.v1"]
    language: ReportLanguage
    verification_status: Literal["PENDING"]

    _validate_generated_at = field_validator("generated_at")(validate_utc_datetime)


class ReportDocument(ContractModel):
    """Single strong source model for both frozen PDF and JSON renderers."""

    title: str = Field(min_length=1)
    executive_summary: str = Field(min_length=1)
    task_summary: ReportTaskSummary
    scope: ReportScope
    applicable_policies: tuple[ReportPolicyReference, ...]
    quality_policy_findings: tuple[ReportPolicyReference, ...]
    data_overview: tuple[ReportDataSource, ...]
    supplier_quality_data: tuple[ReportDataSource, ...]
    key_metrics: tuple[AnalyticsMetricResult, ...]
    analysis_results: AnalyticsResult
    supplier_ranking: tuple[JsonObject, ...]
    major_findings: tuple[ReportFinding, ...]
    key_risks: tuple[ReportRisk, ...]
    risk_analysis: tuple[ReportRisk, ...]
    recommended_actions: tuple[ReportRecommendation, ...]
    recommendations: tuple[ReportRecommendation, ...]
    limitations: tuple[ReportLimitation, ...] = Field(min_length=1)
    evidence: tuple[ReportEvidenceReference, ...] = Field(min_length=1)
    evidence_references: tuple[ReportEvidenceReference, ...] = Field(min_length=1)
    execution_trace: tuple[ReportExecutionStep, ...] = Field(min_length=1)
    execution_metadata: ReportExecutionMetadata

    @field_validator("title", "executive_summary")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """Reject visually empty required report text."""
        if not value.strip():
            raise ValueError("required report text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_consistency(self) -> ReportDocument:
        """Validate identity and unique canonical Evidence references."""
        if self.task_summary.task_id != self.task_summary.trace_id:
            raise ValueError("v1 offline trace_id must equal task_id")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("report Evidence references must be unique")
        _reject_non_finite(self.model_dump(mode="python", exclude={"execution_metadata"}))
        return self


def _reject_non_finite(value: object) -> None:
    """Reject NaN and Infinity recursively before serialization or rendering."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("report values must not contain NaN or Infinity")
    if isinstance(value, dict):
        for child in value.values():
            _reject_non_finite(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _reject_non_finite(child)


REPORT_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "task_id",
        "scope",
        "analysis_result",
        "evidence_refs",
        "template_version",
        "format",
        "language",
    ],
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "scope": {
            "type": "object",
            "additionalProperties": False,
            "required": ["year", "quarter", "start_date", "end_date", "supplier_ids"],
            "properties": {
                "year": {"type": "integer", "minimum": 2000, "maximum": 9999},
                "quarter": {"type": "integer", "minimum": 1, "maximum": 4},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "supplier_ids": {
                    "type": "array",
                    "maxItems": 100,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
            },
        },
        "analysis_result": {"type": "object"},
        "evidence_refs": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "template_version": {"type": "string", "const": REPORT_TEMPLATE_VERSION},
        "format": {"type": "string", "enum": [item.value for item in ReportFormat]},
        "language": {"type": "string", "enum": [item.value for item in ReportLanguage]},
    },
}

REPORT_OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "artifact_id",
        "type",
        "location",
        "created_at",
        "checksum",
        "size_bytes",
        "citation_map",
        "generator_version",
    ],
    "properties": {
        "artifact_id": {"type": "string"},
        "type": {
            "type": "string",
            "enum": [
                "QUALITY_ANALYSIS_REPORT_PDF",
                "QUALITY_ANALYSIS_REPORT_JSON",
            ],
        },
        "location": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "checksum": {"type": "string"},
        "size_bytes": {"type": "integer", "minimum": 1},
        "citation_map": {"type": "object"},
        "generator_version": {"type": "string"},
    },
}


__all__ = [
    "REPORT_GENERATOR_VERSION",
    "REPORT_INPUT_SCHEMA",
    "REPORT_OUTPUT_SCHEMA",
    "REPORT_SCHEMA_VERSION",
    "REPORT_TEMPLATE_VERSION",
    "ReportDataSource",
    "ReportDocument",
    "ReportEvidenceReference",
    "ReportExecutionMetadata",
    "ReportExecutionStep",
    "ReportFinding",
    "ReportFormat",
    "ReportLimitation",
    "ReportPolicyReference",
    "ReportRecommendation",
    "ReportRequest",
    "ReportRisk",
    "ReportScope",
    "ReportTaskSummary",
]
