"""Frozen deterministic Supplier Quality metric calculations."""

from __future__ import annotations

from collections import defaultdict

from copilot.tools.analytics.operations import aggregate_counts, group_rows
from copilot.tools.analytics.precision import normalize_decimal, normalized_ratio
from copilot.tools.analytics.schemas import (
    AnalyticsDimension,
    AnalyticsMetric,
    AnalyticsMetricResult,
    AnalyticsRequest,
    MetricUnit,
    QualityMetricRow,
)

FORMULAS: dict[AnalyticsMetric, str] = {
    AnalyticsMetric.DEFECT_COUNT: "sum(defect_count)",
    AnalyticsMetric.INSPECTED_COUNT: "sum(inspected_count)",
    AnalyticsMetric.DEFECT_RATE: "sum(defect_count) / sum(inspected_count)",
    AnalyticsMetric.PERIOD_OVER_PERIOD_TREND: (
        "current_period_defect_rate - previous_period_defect_rate"
    ),
}


def calculate_metrics(
    request: AnalyticsRequest,
) -> tuple[tuple[AnalyticsMetricResult, ...], tuple[str, ...]]:
    """Calculate requested metrics in request order and stable dimension order."""
    if not request.dataset:
        return (), ("No rows were available for calculation",)

    values: list[AnalyticsMetricResult] = []
    warnings: list[str] = []
    groups = group_rows(request.dataset, request.group_by)
    for metric in request.metrics:
        if metric is AnalyticsMetric.PERIOD_OVER_PERIOD_TREND:
            trend_values, trend_warnings = _period_over_period_trend(
                request.dataset,
                request.group_by,
            )
            values.extend(trend_values)
            warnings.extend(trend_warnings)
            continue
        for group in groups:
            defects, inspected = aggregate_counts(group.rows)
            if metric is AnalyticsMetric.DEFECT_COUNT:
                values.append(
                    _metric(
                        metric=metric,
                        dimensions=group.dimensions,
                        value=defects,
                        numerator=defects,
                        denominator=None,
                        unit="count",
                    )
                )
            elif metric is AnalyticsMetric.INSPECTED_COUNT:
                values.append(
                    _metric(
                        metric=metric,
                        dimensions=group.dimensions,
                        value=inspected,
                        numerator=inspected,
                        denominator=None,
                        unit="count",
                    )
                )
            else:
                value = normalized_ratio(defects, inspected)
                values.append(
                    _metric(
                        metric=metric,
                        dimensions=group.dimensions,
                        value=value,
                        numerator=defects,
                        denominator=inspected,
                        unit="ratio",
                    )
                )
                if value is None:
                    warnings.append(
                        _warning(
                            "Defect rate is undefined because inspected_count is zero",
                            group.dimensions,
                        )
                    )
    return tuple(values), tuple(dict.fromkeys(warnings))


def _period_over_period_trend(
    rows: tuple[QualityMetricRow, ...],
    group_by: tuple[AnalyticsDimension, ...],
) -> tuple[list[AnalyticsMetricResult], list[str]]:
    series_dimensions = tuple(
        dimension for dimension in group_by if dimension is not AnalyticsDimension.PERIOD
    )
    series: dict[tuple[str, ...], dict[str, list[QualityMetricRow]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        series_key = tuple(str(getattr(row, dimension.value)) for dimension in series_dimensions)
        series[series_key][row.period].append(row)

    results: list[AnalyticsMetricResult] = []
    warnings: list[str] = []
    for series_key in sorted(series):
        base_dimensions = {
            dimension.value: series_key[index] for index, dimension in enumerate(series_dimensions)
        }
        previous_rate: float | None = None
        has_previous_period = False
        for period in sorted(series[series_key]):
            defects, inspected = aggregate_counts(tuple(series[series_key][period]))
            current_rate = normalized_ratio(defects, inspected)
            dimensions = {**base_dimensions, AnalyticsDimension.PERIOD.value: period}
            if not has_previous_period:
                value = None
                warnings.append(_warning("Trend is undefined for the first period", dimensions))
            elif current_rate is None or previous_rate is None:
                value = None
                warnings.append(
                    _warning(
                        "Trend is undefined because a compared period has zero inspected_count",
                        dimensions,
                    )
                )
            else:
                value = normalize_decimal(current_rate - previous_rate)
            results.append(
                _metric(
                    metric=AnalyticsMetric.PERIOD_OVER_PERIOD_TREND,
                    dimensions=dimensions,
                    value=value,
                    numerator=current_rate,
                    denominator=previous_rate if has_previous_period else None,
                    unit="ratio_delta",
                )
            )
            previous_rate = current_rate
            has_previous_period = True
    return results, warnings


def _metric(
    *,
    metric: AnalyticsMetric,
    dimensions: dict[str, str],
    value: int | float | None,
    numerator: int | float | None,
    denominator: int | float | None,
    unit: MetricUnit,
) -> AnalyticsMetricResult:
    return AnalyticsMetricResult(
        metric=metric,
        dimensions=dimensions,
        value=value,
        numerator=numerator,
        denominator=denominator,
        unit=unit,
    )


def _warning(message: str, dimensions: dict[str, str]) -> str:
    scope = ", ".join(f"{key}={value}" for key, value in sorted(dimensions.items()))
    return f"{message} ({scope})" if scope else message


__all__ = ["FORMULAS", "calculate_metrics"]
