"""Strict intermediate schemas that adapt model output into frozen domain contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from copilot.contracts import (
    ArtifactType,
    JsonObject,
    ReportLanguage,
    RiskLevel,
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


class PlannerToolManifestEntry(BaseModel):
    """Minimized deterministic ToolRegistry view exposed to the planner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    tool_version: str
    contract_profile: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    risk_level: RiskLevel
    read_only: bool
    requires_approval: bool
    idempotent: bool


class PlannerToolManifest(BaseModel):
    """Stable sorted manifest plus an explicit schema version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "planner-tool-manifest-v2"
    tools: tuple[PlannerToolManifestEntry, ...]


__all__ = [
    "PlannerToolManifest",
    "PlannerToolManifestEntry",
    "TaskUnderstandingOutput",
    "UnderstandingConstraints",
    "UnderstandingDeliverable",
    "UnderstandingEntities",
    "UnderstandingTimeRange",
]
