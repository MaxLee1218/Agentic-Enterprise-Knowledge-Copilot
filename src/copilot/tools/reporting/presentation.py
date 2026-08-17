"""Presentation-only grouping and formatting for management reports.

This module never aggregates, ranks, or reinterprets business performance. It selects
already-calculated metric values, groups them into a readable matrix, and formats units.
"""

from __future__ import annotations

from calendar import month_abbr
from dataclasses import dataclass

from copilot.tools.analytics.schemas import AnalyticsMetric, AnalyticsMetricResult
from copilot.tools.reporting.schemas import ReportDocument


@dataclass(frozen=True, slots=True)
class SupplierOverviewRow:
    """One supplier row backed directly by existing Calculation Evidence values."""

    supplier_id: str
    defect_rates: tuple[int | float | None, ...]
    latest_month_change: int | float | None


def observed_supplier_ids(document: ReportDocument) -> tuple[str, ...]:
    """Return supplier dimensions represented by the canonical analytics result."""
    observed = {
        value
        for metric in document.key_metrics
        if isinstance((value := metric.dimensions.get("supplier_id")), str) and value
    }
    if observed:
        return tuple(sorted(observed))
    return tuple(sorted(document.scope.supplier_ids))


def observed_periods(document: ReportDocument) -> tuple[str, ...]:
    """Return stable period dimensions represented by the canonical analytics result."""
    return tuple(
        sorted(
            {
                value
                for metric in document.key_metrics
                if isinstance((value := metric.dimensions.get("period")), str) and value
            }
        )
    )


def supplier_overview_rows(document: ReportDocument) -> tuple[SupplierOverviewRow, ...]:
    """Pivot existing defect-rate and trend observations without calculating new metrics."""
    periods = observed_periods(document)
    rates = _metric_lookup(document.key_metrics, AnalyticsMetric.DEFECT_RATE)
    trends = _metric_lookup(document.key_metrics, AnalyticsMetric.PERIOD_OVER_PERIOD_TREND)
    latest_period = periods[-1] if periods else None
    return tuple(
        SupplierOverviewRow(
            supplier_id=supplier_id,
            defect_rates=tuple(rates.get((supplier_id, period)) for period in periods),
            latest_month_change=(
                trends.get((supplier_id, latest_period)) if latest_period is not None else None
            ),
        )
        for supplier_id in observed_supplier_ids(document)
    )


def format_metric_value(value: int | float | None, unit: str) -> str:
    """Format one existing metric value without changing its canonical representation."""
    if value is None:
        return "N/A"
    if unit == "count":
        return f"{value:,.0f}"
    if unit == "ratio":
        return f"{value * 100:.2f}%"
    if unit == "ratio_delta":
        return f"{value * 100:+.2f} pp"
    return str(value)


def raw_metric_value(value: int | float | None) -> str:
    """Preserve the canonical raw value for the detailed audit appendix."""
    return "null" if value is None else str(value)


def period_label(value: str) -> str:
    """Use a compact month label when the frozen period dimension is YYYY-MM."""
    parts = value.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        month = int(parts[1])
        if 1 <= month <= 12:
            return month_abbr[month]
    return value


def short_identifier(value: str, *, prefix: int = 10) -> str:
    """Shorten an identifier for management pages while preserving it in the model/appendix."""
    return value if len(value) <= prefix + 3 else f"{value[:prefix]}..."


def wrap_identifier(value: str, *, width: int = 18) -> str:
    """Insert display spaces so complete appendix identifiers wrap inside table cells."""
    return " ".join(value[index : index + width] for index in range(0, len(value), width))


def _metric_lookup(
    metrics: tuple[AnalyticsMetricResult, ...],
    metric_name: AnalyticsMetric,
) -> dict[tuple[str, str], int | float | None]:
    values: dict[tuple[str, str], int | float | None] = {}
    for metric in metrics:
        supplier_id = metric.dimensions.get("supplier_id")
        period = metric.dimensions.get("period")
        if (
            metric.metric is metric_name
            and isinstance(supplier_id, str)
            and isinstance(period, str)
        ):
            values[(supplier_id, period)] = metric.value
    return values


__all__ = [
    "SupplierOverviewRow",
    "format_metric_value",
    "observed_periods",
    "observed_supplier_ids",
    "period_label",
    "raw_metric_value",
    "short_identifier",
    "supplier_overview_rows",
    "wrap_identifier",
]
