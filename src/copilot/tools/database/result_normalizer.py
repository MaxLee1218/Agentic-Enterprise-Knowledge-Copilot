"""Normalize database scalar values into bounded JSON-compatible result rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from copilot.contracts.base import JsonMapping


@dataclass(frozen=True, slots=True)
class NormalizedDatabaseResult:
    """JSON-safe output rows and truncation metadata."""

    rows: tuple[JsonMapping, ...]
    row_count: int
    truncated: bool


def normalize_database_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    row_limit: int,
) -> NormalizedDatabaseResult:
    """Normalize at most ``row_limit`` rows and detect a bounded extra row."""
    truncated = len(rows) > row_limit
    normalized = tuple(
        cast(
            JsonMapping,
            {str(key): _normalize_value(value) for key, value in row.items()},
        )
        for row in rows[:row_limit]
    )
    return NormalizedDatabaseResult(
        rows=normalized,
        row_count=len(normalized),
        truncated=truncated,
    )


def _normalize_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported database scalar type: {type(value).__name__}")


def rows_as_json(rows: tuple[JsonMapping, ...]) -> list[JsonMapping]:
    """Return a list suitable for a ``JsonObject`` payload."""
    return cast(list[JsonMapping], list(rows))


__all__ = [
    "NormalizedDatabaseResult",
    "normalize_database_rows",
    "rows_as_json",
]
