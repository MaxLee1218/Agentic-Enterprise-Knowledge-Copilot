"""Analytics schema and computed-result validation tests."""

import pytest
from pydantic import ValidationError

from copilot.contracts.base import JsonMapping
from copilot.tools.analytics.exceptions import AnalyticsResultError
from copilot.tools.analytics.schemas import (
    AnalyticsMetric,
    AnalyticsMetricResult,
    AnalyticsRequest,
    AnalyticsResult,
)
from copilot.tools.analytics.validators import validate_result
from tests.unit.tools.analytics.helpers import analytics_arguments


def test_schema_rejects_unknown_metric_dimension_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AnalyticsRequest.model_validate(analytics_arguments(metrics=["supplier_reject_rate"]).root)
    with pytest.raises(ValidationError):
        AnalyticsRequest.model_validate(analytics_arguments(group_by=["severity"]).root)
    payload = analytics_arguments().root | {"operation": "sum"}
    with pytest.raises(ValidationError):
        AnalyticsRequest.model_validate(payload)


def test_schema_rejects_missing_and_non_numeric_fields() -> None:
    missing: list[JsonMapping] = [
        {
            "supplier_id": "S-100",
            "period": "2026-01",
            "inspected_count": 100,
        }
    ]
    with pytest.raises(ValidationError):
        AnalyticsRequest.model_validate(analytics_arguments(missing).root)

    non_numeric: list[JsonMapping] = [
        {
            "supplier_id": "S-100",
            "period": "2026-01",
            "inspected_count": "one hundred",
            "defect_count": 1,
        }
    ]
    with pytest.raises(ValidationError):
        AnalyticsRequest.model_validate(analytics_arguments(non_numeric).root)


def test_schema_rejects_dataset_over_frozen_row_limit() -> None:
    row: JsonMapping = {
        "supplier_id": "S-100",
        "period": "2026-01",
        "inspected_count": 1,
        "defect_count": 0,
    }

    with pytest.raises(ValidationError):
        AnalyticsRequest.model_validate(analytics_arguments([row] * 10_001).root)


def test_numeric_validator_rejects_nan_and_inconsistent_rate() -> None:
    nan_result = AnalyticsResult(
        metrics=(
            AnalyticsMetricResult(
                metric=AnalyticsMetric.DEFECT_RATE,
                dimensions={},
                value=float("nan"),
                unit="ratio",
                numerator=1,
                denominator=2,
            ),
        ),
        warnings=(),
        input_row_count=1,
        dataset_checksum="checksum",
        calculation_version="quality_metrics.v1",
        empty_result=False,
    )
    with pytest.raises(AnalyticsResultError, match="non-finite"):
        validate_result(nan_result)

    inconsistent = nan_result.model_copy(
        update={
            "metrics": (
                AnalyticsMetricResult(
                    metric=AnalyticsMetric.DEFECT_RATE,
                    dimensions={},
                    value=0.5,
                    unit="ratio",
                    numerator=1,
                    denominator=4,
                ),
            )
        }
    )
    with pytest.raises(AnalyticsResultError, match="operands"):
        validate_result(inconsistent)
