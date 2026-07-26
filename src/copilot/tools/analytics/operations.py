"""Deterministic grouping primitives used by approved quality metrics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from copilot.tools.analytics.schemas import AnalyticsDimension, QualityMetricRow


@dataclass(frozen=True, slots=True)
class RowGroup:
    """A stable dimension key and the rows belonging to it."""

    dimensions: dict[str, str]
    rows: tuple[QualityMetricRow, ...]


def group_rows(
    rows: tuple[QualityMetricRow, ...],
    dimensions: tuple[AnalyticsDimension, ...],
) -> tuple[RowGroup, ...]:
    """Group rows by approved dimensions and return lexicographically stable groups."""
    if not rows:
        return ()
    if not dimensions:
        return (RowGroup(dimensions={}, rows=rows),)
    grouped: dict[tuple[str, ...], list[QualityMetricRow]] = defaultdict(list)
    for row in rows:
        key = tuple(str(getattr(row, dimension.value)) for dimension in dimensions)
        grouped[key].append(row)
    return tuple(
        RowGroup(
            dimensions={dimension.value: key[index] for index, dimension in enumerate(dimensions)},
            rows=tuple(grouped[key]),
        )
        for key in sorted(grouped)
    )


def aggregate_counts(rows: tuple[QualityMetricRow, ...]) -> tuple[int, int]:
    """Return ``(defect_count, inspected_count)`` using exact integer arithmetic."""
    return (
        sum(row.defect_count for row in rows),
        sum(row.inspected_count for row in rows),
    )


__all__ = ["RowGroup", "aggregate_counts", "group_rows"]
