"""Worked examples for the oracle, independent of production metric functions."""

from decimal import Decimal

from evaluation.supplier_quality_pilot.contracts import PilotSnapshot
from evaluation.supplier_quality_pilot.source import (
    DEFAULT_SOURCE,
    oracle_metrics,
    ratio,
    source_counts,
)


def test_oracle_matches_worked_counts_rates_and_null_trends() -> None:
    snapshot = PilotSnapshot.model_validate_json(DEFAULT_SOURCE.read_bytes())

    metrics = {(m.metric, m.supplier_id, m.period): m for m in oracle_metrics(snapshot)}

    assert len(metrics) == 24
    assert metrics["defect_count", "S-100", "2026-06"].value == 30
    assert metrics["defect_rate", "S-100", "2026-06"].value == Decimal("0.0300")
    assert metrics["period_over_period_trend", "S-100", "2026-05"].value == Decimal("0.0100")
    assert metrics["period_over_period_trend", "S-100", "2026-04"].value is None
    assert metrics["defect_rate", "S-200", "2026-06"].value is None
    assert metrics["period_over_period_trend", "S-200", "2026-06"].value is None
    assert source_counts(snapshot) == (
        ("S-100", "2026-04", 10, 1000),
        ("S-100", "2026-05", 20, 1000),
        ("S-100", "2026-06", 30, 1000),
        ("S-200", "2026-04", 1, 100),
        ("S-200", "2026-05", 9, 900),
        ("S-200", "2026-06", 0, 0),
    )


def test_oracle_rounds_half_even_and_does_not_average_supplier_ratios() -> None:
    assert ratio(1, 32) == Decimal("0.0312")
    assert ratio(70, 4000) == Decimal("0.0175")
    assert ratio(0, 0) is None
