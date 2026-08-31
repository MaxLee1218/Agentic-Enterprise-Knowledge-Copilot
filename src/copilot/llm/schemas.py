"""Strict intermediate schemas that adapt model output into frozen domain contracts."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from copilot.contracts import (
    APExceptionType,
    ArtifactType,
    CapabilityName,
    MoneyThreshold,
    ReportLanguage,
    TaskType,
)


class UnderstandingEntities(BaseModel):
    """Business entities extracted from untrusted natural language."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supplier_ids: tuple[str, ...] = ()


class UnderstandingTimeRange(BaseModel):
    """Explicit year and quarter; omission remains visible to deterministic code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int | None = Field(default=None, ge=2000, le=9999)
    quarter: int | None = Field(default=None, ge=1, le=4)


class UnderstandingDeliverable(BaseModel):
    """Requested frozen report output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: ArtifactType = ArtifactType.QUALITY_ANALYSIS_REPORT_PDF
    language: ReportLanguage = ReportLanguage.EN_US
    required_sections: tuple[str, ...] = (
        "scope",
        "quality_policy_findings",
        "supplier_quality_data",
        "analysis_results",
        "key_risks",
        "recommendations",
        "evidence_references",
    )


class UnderstandingConstraints(BaseModel):
    """Model-visible non-authoritative limits preserved for deterministic enforcement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    read_only: bool = True
    max_steps: int = Field(ge=1)
    metrics: tuple[str, ...] = (
        "defect_count",
        "inspected_count",
        "defect_rate",
        "period_over_period_trend",
    )


class TaskUnderstandingOutput(BaseModel):
    """Candidate interpretation before trusted scope and date derivation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=2000)
    task_type: TaskType
    entities: UnderstandingEntities
    time_range: UnderstandingTimeRange
    deliverable: UnderstandingDeliverable
    constraints: UnderstandingConstraints
    missing_information: tuple[str, ...] = ()

    @model_validator(mode="after")
    def preserve_missing_time_information(self) -> TaskUnderstandingOutput:
        """Do not silently accept a partial or invented frozen time range."""
        if (self.time_range.year is None) != (self.time_range.quarter is None):
            raise ValueError("year and quarter must either both be present or both be absent")
        return self


class APDateRangeCandidate(BaseModel):
    """Explicit AP date range; missing endpoints remain visible to deterministic code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def reject_partial_range(self) -> APDateRangeCandidate:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("AP start_date and end_date must both be present or absent")
        return self


class APDeliverableCandidate(BaseModel):
    """Untrusted AP format and language preference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: ArtifactType = ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF
    language: ReportLanguage = ReportLanguage.EN_US


class APTaskUnderstandingOutput(BaseModel):
    """Model-facing AP intent candidate with no authorization-bearing fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=2000)
    task_type: TaskType
    time_range: APDateRangeCandidate
    requested_supplier_ids: tuple[str, ...] = ()
    requested_legal_entity_ids: tuple[str, ...] = ()
    requested_business_unit_ids: tuple[str, ...] = ()
    currency_scope: tuple[str, ...] = ()
    exception_types: tuple[APExceptionType, ...] = ()
    requested_materiality: tuple[MoneyThreshold, ...] = ()
    deliverable: APDeliverableCandidate = APDeliverableCandidate()
    include_policy_comparison: bool = True
    missing_information: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_ap_candidate(self) -> APTaskUnderstandingOutput:
        if self.task_type is not TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1:
            raise ValueError("AP understanding requires accounts_payable_analysis.v1")
        collections = (
            self.requested_supplier_ids,
            self.requested_legal_entity_ids,
            self.requested_business_unit_ids,
            self.currency_scope,
            self.exception_types,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("AP candidate scope collections must be unique")
        return self


class PlannerCapabilityManifestEntry(BaseModel):
    """Semantic capability view with all executable Registry metadata removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: CapabilityName
    description: str
    semantic_arguments: tuple[str, ...] = ()


class PlannerCapabilityManifest(BaseModel):
    """Stable domain-filtered capability suggestion boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "planner-capability-manifest-v3"
    task_type: TaskType
    capabilities: tuple[PlannerCapabilityManifestEntry, ...]


__all__ = [
    "PlannerCapabilityManifest",
    "PlannerCapabilityManifestEntry",
    "APDateRangeCandidate",
    "APDeliverableCandidate",
    "APTaskUnderstandingOutput",
    "TaskUnderstandingOutput",
    "UnderstandingConstraints",
    "UnderstandingDeliverable",
    "UnderstandingEntities",
    "UnderstandingTimeRange",
]
