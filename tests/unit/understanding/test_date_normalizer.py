"""Calendar-safe deterministic time normalization coverage."""

from datetime import date

import pytest

from copilot.contracts import ResolutionSource, ResolutionStatus
from copilot.understanding.date_normalizer import (
    normalize_accounts_payable_time_range,
    normalize_supplier_period,
)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    (
        ("year2025", "2025-01-01", "2025-12-31"),
        ("full year 2024", "2024-01-01", "2024-12-31"),
        ("February 2024", "2024-02-01", "2024-02-29"),
        ("February 2025", "2025-02-01", "2025-02-28"),
        ("Aug 2026", "2026-08-01", "2026-08-31"),
        ("first half of 2025", "2025-01-01", "2025-06-30"),
        ("second half of 2025", "2025-07-01", "2025-12-31"),
        ("Q1 2026", "2026-01-01", "2026-03-31"),
        ("Q2 2026", "2026-04-01", "2026-06-30"),
        ("Q3 2026", "2026-07-01", "2026-09-30"),
        ("Q4 2026", "2026-10-01", "2026-12-31"),
        ("second quarter of 2026", "2026-04-01", "2026-06-30"),
        ("2026 1.1-7.1", "2026-01-01", "2026-07-01"),
        ("2026.1.1—7.1", "2026-01-01", "2026-07-01"),
        ("2026/1/1 to 2026/7/1", "2026-01-01", "2026-07-01"),
        ("2026-01-01 to 07-01", "2026-01-01", "2026-07-01"),
    ),
)
def test_accounts_payable_calendar_normalization(text: str, start: str, end: str) -> None:
    result = normalize_accounts_payable_time_range(text)

    assert result.status is ResolutionStatus.NORMALIZED
    assert result.canonical_value == {"start_date": start, "end_date": end}


def test_exact_invalid_relative_and_prior_year_corrections() -> None:
    exact = normalize_accounts_payable_time_range("2025-01-01 to 2025-12-31")
    invalid = normalize_accounts_payable_time_range("2025-02-30 to 2025-03-01")
    relative = normalize_accounts_payable_time_range("recently")
    corrected = normalize_accounts_payable_time_range(
        "No, only July through December",
        prior_start=date(2025, 1, 1),
        prior_end=date(2025, 12, 31),
    )
    month_only = normalize_accounts_payable_time_range(
        "actually use August only",
        prior_start=date(2025, 1, 1),
        prior_end=date(2025, 12, 31),
    )

    assert exact.status is ResolutionStatus.EXACT
    assert exact.source is ResolutionSource.ORIGINAL_REQUEST
    assert invalid.status is ResolutionStatus.INVALID
    assert relative.status is ResolutionStatus.MISSING
    assert corrected.canonical_value == {
        "start_date": "2025-07-01",
        "end_date": "2025-12-31",
    }
    assert month_only.canonical_value == {
        "start_date": "2025-08-01",
        "end_date": "2025-08-31",
    }


def test_exact_clarification_range_preserves_its_input_source() -> None:
    result = normalize_accounts_payable_time_range(
        "2025-01-01 to 2025-12-31",
        input_source=ResolutionSource.CLARIFICATION_RESPONSE,
    )

    assert result.source is ResolutionSource.CLARIFICATION_RESPONSE


@pytest.mark.parametrize(
    "text",
    (
        "Q2 2026",
        "2026 Q2",
        "2026-Q2",
        "second quarter 2026",
        "2. Quartal 2026",
        "2026 年第二季度",
    ),
)
def test_supplier_quarter_normalization(text: str) -> None:
    result = normalize_supplier_period(text)

    assert result.canonical_value == {"year": 2026, "quarter": 2}
