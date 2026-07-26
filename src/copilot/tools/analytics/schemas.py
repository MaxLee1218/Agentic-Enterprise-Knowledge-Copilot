"""Typed input and output schemas for ``analysis_engine`` v1.0."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StrictFloat, StrictInt, field_validator, model_validator

from copilot.contracts.base import ContractModel, JsonMapping

ANALYTICS_ENGINE_VERSION = "quality_metrics.v1"
MAX_DATASET_ROWS = 10_000


class AnalyticsMetric(StrEnum):
    """Only metric names authorized by the frozen Supplier Quality v1.0 baseline."""

    DEFECT_COUNT = "defect_count"
    INSPECTED_COUNT = "inspected_count"
    DEFECT_RATE = "defect_rate"
    PERIOD_OVER_PERIOD_TREND = "period_over_period_trend"


class AnalyticsDimension(StrEnum):
    """Only grouping dimensions authorized by the frozen v1.0 input contract."""

    SUPPLIER_ID = "supplier_id"
    PERIOD = "period"


NonNegativeCount = Annotated[StrictInt, Field(ge=0)]
MetricNumber = StrictInt | StrictFloat | None
MetricUnit = Literal["count", "ratio", "ratio_delta"]


class QualityMetricRow(ContractModel):
    """One normalized database row accepted by the deterministic calculator."""

    supplier_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    inspected_count: NonNegativeCount
    defect_count: NonNegativeCount

    @field_validator("supplier_id", "period")
    @classmethod
    def reject_blank_dimensions(cls, value: str) -> str:
        """Reject ambiguous blank dimensions without silently normalizing them."""
        if not value.strip():
            raise ValueError("dimension values must not be blank")
        return value

    @model_validator(mode="after")
    def validate_count_relationship(self) -> QualityMetricRow:
        """Reject impossible quality rows before aggregation."""
        if self.defect_count > self.inspected_count:
            raise ValueError("defect_count must not exceed inspected_count")
        return self


class AnalyticsRequest(ContractModel):
    """Frozen, checksum-bound request accepted by ``analysis_engine``."""

    dataset: tuple[QualityMetricRow, ...] = Field(max_length=MAX_DATASET_ROWS)
    dataset_evidence_id: str = Field(min_length=1)
    dataset_checksum: str = Field(min_length=1)
    metrics: tuple[AnalyticsMetric, ...] = Field(min_length=1)
    group_by: tuple[AnalyticsDimension, ...] = Field(max_length=2)
    engine_version: Literal["quality_metrics.v1"]

    @field_validator("dataset_evidence_id", "dataset_checksum")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        """Require stable, non-blank lineage identifiers."""
        if not value.strip():
            raise ValueError("lineage identifiers must not be blank")
        return value

    @model_validator(mode="after")
    def validate_unique_collections(self) -> AnalyticsRequest:
        """Reject duplicates that would make requested output ambiguous."""
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("metrics must be unique")
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("group_by dimensions must be unique")
        return self


class AnalyticsMetricResult(ContractModel):
    """One deterministic metric value and its explicit operands."""

    metric: AnalyticsMetric
    dimensions: dict[str, str]
    value: MetricNumber
    unit: MetricUnit
    numerator: MetricNumber
    denominator: MetricNumber


class AnalyticsResult(ContractModel):
    """Frozen normalized output returned by ``analysis_engine``."""

    metrics: tuple[AnalyticsMetricResult, ...]
    warnings: tuple[str, ...]
    input_row_count: int = Field(ge=0)
    dataset_checksum: str = Field(min_length=1)
    calculation_version: Literal["quality_metrics.v1"]
    empty_result: bool

    @model_validator(mode="after")
    def validate_empty_semantics(self) -> AnalyticsResult:
        """Keep empty-dataset semantics aligned with the frozen tool contract."""
        if self.empty_result != (self.input_row_count == 0):
            raise ValueError("empty_result must match input_row_count")
        if self.empty_result and self.metrics:
            raise ValueError("empty datasets must not produce metric values")
        return self


ANALYTICS_INPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "dataset",
        "dataset_evidence_id",
        "dataset_checksum",
        "metrics",
        "group_by",
        "engine_version",
    ],
    "properties": {
        "dataset": {
            "type": "array",
            "maxItems": MAX_DATASET_ROWS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "supplier_id",
                    "period",
                    "inspected_count",
                    "defect_count",
                ],
                "properties": {
                    "supplier_id": {"type": "string", "minLength": 1},
                    "period": {"type": "string", "minLength": 1},
                    "inspected_count": {"type": "integer", "minimum": 0},
                    "defect_count": {"type": "integer", "minimum": 0},
                },
            },
        },
        "dataset_evidence_id": {"type": "string", "minLength": 1},
        "dataset_checksum": {"type": "string", "minLength": 1},
        "metrics": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": [metric.value for metric in AnalyticsMetric],
            },
        },
        "group_by": {
            "type": "array",
            "maxItems": 2,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": [dimension.value for dimension in AnalyticsDimension],
            },
        },
        "engine_version": {"type": "string", "const": ANALYTICS_ENGINE_VERSION},
    },
}

_METRIC_NUMBER_SCHEMA: JsonMapping = {"type": ["number", "null"]}
ANALYTICS_OUTPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "metrics",
        "warnings",
        "input_row_count",
        "dataset_checksum",
        "calculation_version",
        "empty_result",
    ],
    "properties": {
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "metric",
                    "dimensions",
                    "value",
                    "unit",
                    "numerator",
                    "denominator",
                ],
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": [metric.value for metric in AnalyticsMetric],
                    },
                    "dimensions": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "value": _METRIC_NUMBER_SCHEMA,
                    "unit": {
                        "type": "string",
                        "enum": ["count", "ratio", "ratio_delta"],
                    },
                    "numerator": _METRIC_NUMBER_SCHEMA,
                    "denominator": _METRIC_NUMBER_SCHEMA,
                },
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "input_row_count": {"type": "integer", "minimum": 0},
        "dataset_checksum": {"type": "string"},
        "calculation_version": {"type": "string"},
        "empty_result": {"type": "boolean"},
    },
}


__all__ = [
    "ANALYTICS_ENGINE_VERSION",
    "ANALYTICS_INPUT_SCHEMA",
    "ANALYTICS_OUTPUT_SCHEMA",
    "MAX_DATASET_ROWS",
    "AnalyticsDimension",
    "AnalyticsMetric",
    "AnalyticsMetricResult",
    "AnalyticsRequest",
    "AnalyticsResult",
    "QualityMetricRow",
]
