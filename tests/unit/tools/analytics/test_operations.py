"""Stable grouping and exact aggregation tests."""

from copilot.tools.analytics.operations import aggregate_counts, group_rows
from copilot.tools.analytics.schemas import AnalyticsDimension, QualityMetricRow


def test_group_rows_supports_two_dimensions_in_stable_order() -> None:
    rows = (
        QualityMetricRow(
            supplier_id="S-200",
            period="2026-02",
            inspected_count=50,
            defect_count=2,
        ),
        QualityMetricRow(
            supplier_id="S-100",
            period="2026-01",
            inspected_count=100,
            defect_count=1,
        ),
        QualityMetricRow(
            supplier_id="S-100",
            period="2026-01",
            inspected_count=25,
            defect_count=1,
        ),
    )

    grouped = group_rows(
        rows,
        (AnalyticsDimension.SUPPLIER_ID, AnalyticsDimension.PERIOD),
    )

    assert [group.dimensions for group in grouped] == [
        {"supplier_id": "S-100", "period": "2026-01"},
        {"supplier_id": "S-200", "period": "2026-02"},
    ]
    assert aggregate_counts(grouped[0].rows) == (2, 125)


def test_group_rows_without_dimensions_preserves_duplicate_rows() -> None:
    row = QualityMetricRow(
        supplier_id="S-100",
        period="2026-01",
        inspected_count=100,
        defect_count=2,
    )

    grouped = group_rows((row, row), ())

    assert len(grouped) == 1
    assert aggregate_counts(grouped[0].rows) == (4, 200)
