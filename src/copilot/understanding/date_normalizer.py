"""Calendar-safe deterministic normalization of supported business time expressions."""

from __future__ import annotations

import calendar
import re
from datetime import date

from pydantic import JsonValue

from copilot.contracts import FieldResolution, ResolutionSource, ResolutionStatus
from copilot.understanding.entity_resolver import (
    CANONICAL_SUPPLIER_PATTERN,
    NATURAL_SUPPLIER_PATTERN,
)

_YEAR = r"(?:20\d{2}|[3-9]\d{3})"
_ISO_TOKEN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_NUMERIC_RANGE = re.compile(
    rf"(?<!\d)({_YEAR})\s*(?:年\s*|[./-]\s*|\s+)"
    r"(0?[1-9]|1[0-2])[./-](0?[1-9]|[12]\d|3[01])\s*"
    rf"(?:to|through|until|至|到|[-–—~～])\s*(?:({_YEAR})\s*(?:年\s*|[./-]\s*))?"
    r"(0?[1-9]|1[0-2])[./-](0?[1-9]|[12]\d|3[01])(?!\d)",
    re.IGNORECASE,
)
_YEAR_ONLY = re.compile(
    rf"^\s*(?:(?:full\s+)?year\s*)?({_YEAR})\s*[.!]?\s*$",
    re.IGNORECASE,
)
_YEAR_COMPACT = re.compile(rf"\byear\s*({_YEAR})\b", re.IGNORECASE)
_BARE_YEAR = re.compile(rf"(?<!\d)({_YEAR})(?!\d)")
_SUPPLIER_YEAR = re.compile(
    r"(?:(?<![A-Za-z0-9_-])|(?<=year))(-?\d{4,})(?=q\d+\b|[^A-Za-z0-9_]|$)",
    re.IGNORECASE,
)
_QUARTER = re.compile(rf"\bq([1-4])\s*(?:of\s+)?({_YEAR})\b", re.IGNORECASE)
_YEAR_QUARTER = re.compile(rf"\b({_YEAR})\s*[-/]?\s*q([1-4])\b", re.IGNORECASE)
_QUARTER_ONLY = re.compile(r"(?<![A-Za-z_])q(-?\d+)\b", re.IGNORECASE)
_WORD_QUARTER = re.compile(
    rf"\b(first|second|third|fourth)\s+quarter(?:\s+of)?\s+({_YEAR})\b",
    re.IGNORECASE,
)
_GERMAN_QUARTER = re.compile(r"\b(-?\d+)\.\s*Quartal\b", re.IGNORECASE)
_CHINESE_QUARTER = re.compile(r"第\s*([一二三四五六七八九十0-9]+)\s*季度")
_SUPPLIER_WORD_QUARTER = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth)\s+quarter\b", re.IGNORECASE
)
_SUPPLIER_COUNT_UNIT = re.compile(
    r"\s*(?:rows?|records?|units?|items?|defects?|inspections?|batches?|samples?)\b",
    re.IGNORECASE,
)
_SUPPLIER_COUNT_LABEL = re.compile(
    r"\b(?:defect[ _]count|inspected[ _]count)\s*[:=]?\s*$", re.IGNORECASE
)
_HALF = re.compile(
    rf"\b(first|second)\s+half(?:\s+of)?\s+({_YEAR})\b",
    re.IGNORECASE,
)
_RELATIVE = re.compile(
    r"\b(?:recent(?:ly)?|last period|usual period|a while ago)\b",
    re.IGNORECASE,
)
_MONTH_NAMES = {
    name.casefold(): number
    for number in range(1, 13)
    for name in {calendar.month_name[number], calendar.month_abbr[number]}
}
_MONTH_TOKEN = "|".join(sorted((re.escape(item) for item in _MONTH_NAMES), key=len, reverse=True))
_MONTH_YEAR = re.compile(rf"\b({_MONTH_TOKEN})\.?\s+({_YEAR})\b", re.IGNORECASE)
_MONTH_RANGE = re.compile(
    rf"\b({_MONTH_TOKEN})\.?\s+(?:through|to|-)\s+({_MONTH_TOKEN})\.?(?:\s+({_YEAR}))?\b",
    re.IGNORECASE,
)
_MONTH_ONLY = re.compile(rf"\b({_MONTH_TOKEN})\.?\s+only\b", re.IGNORECASE)


def _value(start: date, end: date) -> dict[str, JsonValue]:
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _accepted(
    raw_text: str,
    start: date,
    end: date,
    *,
    exact: bool,
    reason: str,
    input_source: ResolutionSource = ResolutionSource.ORIGINAL_REQUEST,
) -> FieldResolution:
    value = _value(start, end)
    return FieldResolution(
        field_name="time_range",
        status=ResolutionStatus.EXACT if exact else ResolutionStatus.NORMALIZED,
        raw_text=raw_text,
        candidate_value=value,
        canonical_value=value,
        reason=reason,
        source=(input_source if exact else ResolutionSource.DETERMINISTIC_NORMALIZER),
    )


def _missing(raw_text: str, reason: str) -> FieldResolution:
    return FieldResolution(
        field_name="time_range",
        status=ResolutionStatus.MISSING,
        raw_text=raw_text,
        reason=reason,
        source=ResolutionSource.DETERMINISTIC_NORMALIZER,
    )


def normalize_accounts_payable_time_range(
    text: str,
    *,
    prior_start: date | None = None,
    prior_end: date | None = None,
    input_source: ResolutionSource = ResolutionSource.ORIGINAL_REQUEST,
) -> FieldResolution:
    """Resolve the AP v1 absolute calendar expressions explicitly allowed by policy."""
    normalized = " ".join(text.strip().split())
    iso_tokens = _ISO_TOKEN.findall(normalized)
    if len(iso_tokens) >= 2:
        try:
            start, end = date.fromisoformat(iso_tokens[0]), date.fromisoformat(iso_tokens[1])
        except ValueError:
            return FieldResolution(
                field_name="time_range",
                status=ResolutionStatus.INVALID,
                raw_text=normalized,
                reason="The supplied calendar date is invalid.",
                source=ResolutionSource.DETERMINISTIC_NORMALIZER,
                validation_errors=("Use valid dates in YYYY-MM-DD form.",),
            )
        if end < start:
            return FieldResolution(
                field_name="time_range",
                status=ResolutionStatus.INVALID,
                raw_text=normalized,
                reason="The end date precedes the start date.",
                source=ResolutionSource.DETERMINISTIC_NORMALIZER,
                validation_errors=("End date must be on or after start date.",),
            )
        return _accepted(
            normalized,
            start,
            end,
            exact=True,
            reason="Explicit ISO date range",
            input_source=input_source,
        )

    numeric_range = _NUMERIC_RANGE.search(normalized)
    if numeric_range is not None:
        start_year = int(numeric_range.group(1))
        end_year = int(numeric_range.group(4) or start_year)
        try:
            start = date(
                start_year,
                int(numeric_range.group(2)),
                int(numeric_range.group(3)),
            )
            end = date(
                end_year,
                int(numeric_range.group(5)),
                int(numeric_range.group(6)),
            )
        except ValueError:
            return FieldResolution(
                field_name="time_range",
                status=ResolutionStatus.INVALID,
                raw_text=normalized,
                reason="The supplied calendar date is invalid.",
                source=ResolutionSource.DETERMINISTIC_NORMALIZER,
                validation_errors=("Use valid calendar dates.",),
            )
        if end < start:
            return FieldResolution(
                field_name="time_range",
                status=ResolutionStatus.INVALID,
                raw_text=normalized,
                reason="The end date precedes the start date.",
                source=ResolutionSource.DETERMINISTIC_NORMALIZER,
                validation_errors=("End date must be on or after start date.",),
            )
        return _accepted(
            normalized,
            start,
            end,
            exact=False,
            reason="Normalized abbreviated numeric date range",
        )

    if iso_tokens:
        return FieldResolution(
            field_name="time_range",
            status=ResolutionStatus.INVALID,
            raw_text=normalized,
            reason="Both start and end dates are required.",
            source=ResolutionSource.DETERMINISTIC_NORMALIZER,
            validation_errors=("Provide two valid ISO dates.",),
        )

    quarter_match = _QUARTER.search(normalized) or _YEAR_QUARTER.search(normalized)
    if quarter_match is not None:
        if quarter_match.re is _YEAR_QUARTER:
            year, quarter = int(quarter_match.group(1)), int(quarter_match.group(2))
        else:
            quarter, year = int(quarter_match.group(1)), int(quarter_match.group(2))
        month = (quarter - 1) * 3 + 1
        return _accepted(
            normalized,
            date(year, month, 1),
            _month_end(year, month + 2),
            exact=False,
            reason=f"Normalized calendar quarter Q{quarter} {year}",
        )
    word_quarter = _WORD_QUARTER.search(normalized)
    if word_quarter is not None:
        quarter = ("first", "second", "third", "fourth").index(word_quarter.group(1).casefold()) + 1
        year = int(word_quarter.group(2))
        month = (quarter - 1) * 3 + 1
        return _accepted(
            normalized,
            date(year, month, 1),
            _month_end(year, month + 2),
            exact=False,
            reason=f"Normalized calendar quarter Q{quarter} {year}",
        )

    half = _HALF.search(normalized)
    if half is not None:
        year = int(half.group(2))
        second = half.group(1).casefold() == "second"
        return _accepted(
            normalized,
            date(year, 7 if second else 1, 1),
            date(year, 12 if second else 6, 31 if second else 30),
            exact=False,
            reason=f"Normalized {'second' if second else 'first'} calendar half of {year}",
        )

    month_range = _MONTH_RANGE.search(normalized)
    if month_range is not None:
        start_month = _MONTH_NAMES[month_range.group(1).rstrip(".").casefold()]
        end_month = _MONTH_NAMES[month_range.group(2).rstrip(".").casefold()]
        year_text = month_range.group(3)
        range_year = int(year_text) if year_text else _single_prior_year(prior_start, prior_end)
        if range_year is None:
            return _missing(normalized, "The month range needs an explicit year.")
        if end_month < start_month:
            return FieldResolution(
                field_name="time_range",
                status=ResolutionStatus.INVALID,
                raw_text=normalized,
                reason="A month range cannot cross a year without explicit endpoint years.",
                source=ResolutionSource.DETERMINISTIC_NORMALIZER,
                validation_errors=("Provide exact start and end dates.",),
            )
        return _accepted(
            normalized,
            date(range_year, start_month, 1),
            _month_end(range_year, end_month),
            exact=False,
            reason="Normalized inclusive calendar-month range",
        )

    month_match = _MONTH_YEAR.search(normalized)
    if month_match is not None:
        month_number = _MONTH_NAMES[month_match.group(1).rstrip(".").casefold()]
        month_year = int(month_match.group(2))
        return _accepted(
            normalized,
            date(month_year, month_number, 1),
            _month_end(month_year, month_number),
            exact=False,
            reason="Normalized complete calendar month",
        )

    month_only = _MONTH_ONLY.search(normalized)
    prior_year = _single_prior_year(prior_start, prior_end)
    if month_only is not None and prior_year is not None:
        month_number = _MONTH_NAMES[month_only.group(1).rstrip(".").casefold()]
        return _accepted(
            normalized,
            date(prior_year, month_number, 1),
            _month_end(prior_year, month_number),
            exact=False,
            reason=f"Applied the requested month to the previously validated year {prior_year}",
        )

    compact_year = _YEAR_COMPACT.search(normalized)
    year_only = _YEAR_ONLY.fullmatch(normalized)
    bare_years = tuple(dict.fromkeys(_BARE_YEAR.findall(normalized)))
    if compact_year is not None or year_only is not None or len(bare_years) == 1:
        if compact_year is not None:
            year = int(compact_year.group(1))
        elif year_only is not None:
            year = int(year_only.group(1))
        else:
            year = int(bare_years[0])
        return _accepted(
            normalized,
            date(year, 1, 1),
            date(year, 12, 31),
            exact=False,
            reason=f"Normalized full calendar year {year}",
        )

    if _RELATIVE.search(normalized):
        return _missing(normalized, "Relative time has no frozen AP calendar definition.")
    return _missing(normalized, "No supported absolute AP period was supplied.")


def normalize_supplier_period(
    text: str, *, prior_year: int | None = None, prior_quarter: int | None = None
) -> FieldResolution:
    """Retain explicit partial facts; only a complete period is NORMALIZED.

    A MISSING resolution may carry a partial canonical value for clarification context,
    never an executable TaskContract. No model candidate supplies these facts.
    """
    normalized = " ".join(text.strip().split())
    # Lex calendar tokens independently: punctuation cannot hide an invalid value,
    # and neither a business ID nor an explicitly labelled count supplies a year.
    period_text = NATURAL_SUPPLIER_PATTERN.sub("", normalized)
    period_text = CANONICAL_SUPPLIER_PATTERN.sub("", period_text)
    years = {
        int(match.group(1))
        for match in _SUPPLIER_YEAR.finditer(period_text)
        if _SUPPLIER_COUNT_UNIT.match(period_text, match.end()) is None
        and _SUPPLIER_COUNT_LABEL.search(period_text[: match.start()]) is None
    }
    quarters = {
        int(token)
        for pattern in (_QUARTER_ONLY, _GERMAN_QUARTER)
        for token in pattern.findall(period_text)
    }
    ordinals = ("first", "second", "third", "fourth", "fifth", "sixth")
    quarters.update(
        ordinals.index(token.casefold()) + 1
        for token in _SUPPLIER_WORD_QUARTER.findall(period_text)
    )
    for token in _CHINESE_QUARTER.findall(period_text):
        quarters.add(
            int(token) if token.isdigit() else {"一": 1, "二": 2, "三": 3, "四": 4}.get(token, 0)
        )
    if (
        not quarters.issubset({1, 2, 3, 4})
        or len(quarters) > 1
        or len(years) > 1
        or any(not 2000 <= year <= 9999 for year in years)
    ):
        return FieldResolution(
            field_name="time_range",
            status=ResolutionStatus.INVALID,
            raw_text=normalized,
            reason=(
                "Supply one valid reporting quarter and year; "
                "explicit conflicts cannot use prior values."
            ),
            source=ResolutionSource.DETERMINISTIC_NORMALIZER,
        )
    year = next(iter(years)) if years else prior_year
    quarter = next(iter(quarters)) if quarters else prior_quarter
    if year is None or quarter is None:
        partial: dict[str, JsonValue] = {}
        if year is not None:
            partial["year"] = year
        if quarter is not None:
            partial["quarter"] = quarter
        return FieldResolution(
            field_name="time_range",
            status=ResolutionStatus.MISSING,
            raw_text=normalized,
            canonical_value=partial or None,
            reason="Supplier Quality requires an explicit calendar quarter and year.",
            source=ResolutionSource.DETERMINISTIC_NORMALIZER,
        )
    value: dict[str, JsonValue] = {"year": year, "quarter": quarter}
    return FieldResolution(
        field_name="time_range",
        status=ResolutionStatus.NORMALIZED,
        raw_text=normalized,
        candidate_value=value,
        canonical_value=value,
        reason=f"Normalized Supplier Quality quarter Q{quarter} {year}",
        source=ResolutionSource.DETERMINISTIC_NORMALIZER,
    )


def _single_prior_year(prior_start: date | None, prior_end: date | None) -> int | None:
    if prior_start is None or prior_end is None or prior_start.year != prior_end.year:
        return None
    return prior_start.year


__all__ = ["normalize_accounts_payable_time_range", "normalize_supplier_period"]
