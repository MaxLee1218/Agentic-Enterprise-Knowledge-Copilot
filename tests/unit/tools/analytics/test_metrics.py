"""Deterministic metric formula, edge-case, and precision tests."""

from copilot.contracts.base import JsonMapping
from copilot.tools.analytics.metrics import calculate_metrics
from copilot.tools.analytics.schemas import AnalyticsMetricResult, AnalyticsRequest
from tests.unit.tools.analytics.helpers import DEFAULT_ROWS, analytics_arguments


def _request(**changes: object) -> AnalyticsRequest:
    payload = analytics_arguments().root | changes
    return AnalyticsRequest.model_validate(payload)


def test_all_four_metrics_are_deterministic_and_use_four_decimal_places() -> None:
    request = _request(group_by=["supplier_id"])

    first = calculate_metrics(request)
    second = calculate_metrics(request)

    assert first == second
    metrics, warnings = first
    by_name: dict[str, list[AnalyticsMetricResult]] = {}
    for metric in metrics:
        by_name.setdefault(metric.metric.value, []).append(metric)
    assert by_name["defect_count"][0].value == 25
    assert by_name["inspected_count"][0].value == 2000
    assert by_name["defect_rate"][0].value == 0.0125
    trends = by_name["period_over_period_trend"]
    assert [item.value for item in trends] == [None, 0.005]
    assert any("first period" in warning for warning in warnings)


def test_empty_dataset_is_successful_empty_result_material() -> None:
    request = AnalyticsRequest.model_validate(analytics_arguments([]).root)

    metrics, warnings = calculate_metrics(request)

    assert metrics == ()
    assert warnings == ("No rows were available for calculation",)


def test_zero_denominator_returns_null_and_never_nan() -> None:
    rows: list[JsonMapping] = [
        {
            "supplier_id": "S-000",
            "period": "2026-01",
            "inspected_count": 0,
            "defect_count": 0,
        }
    ]
    request = AnalyticsRequest.model_validate(
        analytics_arguments(
            rows,
            metrics=["defect_rate", "period_over_period_trend"],
        ).root
    )

    metrics, warnings = calculate_metrics(request)

    assert all(metric.value is None for metric in metrics)
    assert any("inspected_count is zero" in warning for warning in warnings)


def test_input_rows_are_not_mutated() -> None:
    before = [dict(row) for row in DEFAULT_ROWS]
    request = AnalyticsRequest.model_validate(analytics_arguments(DEFAULT_ROWS).root)

    calculate_metrics(request)

    assert before == DEFAULT_ROWS
