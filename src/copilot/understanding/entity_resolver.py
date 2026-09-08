"""Resolve business identifiers only inside current trusted authorization scope."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import cast

from pydantic import JsonValue

from copilot.contracts import FieldResolution, ResolutionSource, ResolutionStatus

_CANONICAL_LEGAL_ENTITY = re.compile(r"\bLE-[A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
_CANONICAL_SUPPLIER = re.compile(r"\b(?:SUP|S)-[A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
_SUPPLIER_NATURAL = re.compile(
    r"\bsupplier\s+(?:SUP[-\s]?)?(\d+[A-Za-z0-9_-]*)\b",
    re.IGNORECASE,
)
_COUNTRY_ALIASES = {
    "CN": (r"\bCN\b", r"\bChina(?:\s+entity)?\b"),
    "US": (r"\bUS\b", r"\bUSA\b", r"\bUnited States(?:\s+entity)?\b"),
    "DE": (r"\bDE\b", r"\bGermany(?:\s+entity)?\b"),
}


def _resolution(
    field: str,
    status: ResolutionStatus,
    text: str,
    reason: str,
    *,
    value: JsonValue | None = None,
    alternatives: tuple[str, ...] = (),
    requires_confirmation: bool = False,
    input_source: ResolutionSource = ResolutionSource.ORIGINAL_REQUEST,
) -> FieldResolution:
    return FieldResolution(
        field_name=field,
        status=status,
        raw_text=" ".join(text.split()),
        candidate_value=value,
        canonical_value=(
            value if status in {ResolutionStatus.EXACT, ResolutionStatus.NORMALIZED} else None
        ),
        reason=reason,
        source=(
            input_source
            if status is ResolutionStatus.EXACT
            else ResolutionSource.DETERMINISTIC_NORMALIZER
        ),
        requires_confirmation=requires_confirmation,
        alternatives=alternatives,
    )


def resolve_legal_entities(
    text: str,
    authorized_entities: tuple[str, ...],
    *,
    current_alternatives: tuple[str, ...] = (),
    input_source: ResolutionSource = ResolutionSource.ORIGINAL_REQUEST,
) -> FieldResolution:
    """Resolve canonical IDs or country aliases without consulting global entity data."""
    normalized_authorized = tuple(dict.fromkeys(item.upper() for item in authorized_entities))
    authorized = set(normalized_authorized)
    exact = tuple(dict.fromkeys(item.upper() for item in _CANONICAL_LEGAL_ENTITY.findall(text)))
    unauthorized_exact = tuple(item for item in exact if item not in authorized)
    if unauthorized_exact:
        return _resolution(
            "legal_entity_ids",
            ResolutionStatus.UNAUTHORIZED,
            text,
            "A requested legal entity is outside the current authorized scope.",
        )
    if exact:
        return _resolution(
            "legal_entity_ids",
            ResolutionStatus.EXACT,
            text,
            "Matched explicit canonical legal entity IDs.",
            value=cast(JsonValue, list(exact)),
            input_source=input_source,
        )

    if current_alternatives:
        narrowed = _narrow_by_suffix(text, current_alternatives)
        if len(narrowed) == 1:
            return _resolution(
                "legal_entity_ids",
                ResolutionStatus.NORMALIZED,
                text,
                "Uniquely narrowed the previously displayed authorized candidates.",
                value=cast(JsonValue, list(narrowed)),
            )
        if len(narrowed) > 1:
            return _resolution(
                "legal_entity_ids",
                ResolutionStatus.AMBIGUOUS,
                text,
                "The response still matches multiple displayed authorized candidates.",
                alternatives=narrowed,
            )

    requested_codes: list[tuple[str, bool]] = []
    for code, patterns in _COUNTRY_ALIASES.items():
        for index, pattern in enumerate(patterns):
            if re.search(pattern, text, re.IGNORECASE):
                requested_codes.append((code, index > 0))
                break
    if not requested_codes:
        return _resolution(
            "legal_entity_ids",
            ResolutionStatus.MISSING,
            text,
            "No legal entity expression was supplied.",
        )

    selected: list[str] = []
    interpretation_bearing = False
    for code, named_alias in requested_codes:
        matches = tuple(item for item in normalized_authorized if _entity_country(item) == code)
        if not matches:
            return _resolution(
                "legal_entity_ids",
                ResolutionStatus.UNAUTHORIZED,
                text,
                "The requested country has no legal entity in the current authorized scope.",
            )
        if len(matches) > 1:
            return _resolution(
                "legal_entity_ids",
                ResolutionStatus.AMBIGUOUS,
                text,
                f"The {code} alias matches multiple authorized legal entities.",
                alternatives=matches,
            )
        selected.extend(matches)
        interpretation_bearing = interpretation_bearing or named_alias
    selected_tuple = tuple(dict.fromkeys(selected))
    if interpretation_bearing:
        return _resolution(
            "legal_entity_ids",
            ResolutionStatus.CONFIRMATION_REQUIRED,
            text,
            "A natural-language country name uniquely matched the authorized scope.",
            value=cast(JsonValue, list(selected_tuple)),
            requires_confirmation=True,
        )
    return _resolution(
        "legal_entity_ids",
        ResolutionStatus.NORMALIZED,
        text,
        "A country code uniquely matched the authorized scope.",
        value=cast(JsonValue, list(selected_tuple)),
    )


def resolve_suppliers(
    text: str,
    authorized_suppliers: tuple[str, ...],
    *,
    input_source: ResolutionSource = ResolutionSource.ORIGINAL_REQUEST,
) -> FieldResolution:
    """Resolve Supplier IDs and `supplier 001` only against current trusted scope."""
    normalized_authorized = tuple(dict.fromkeys(item.upper() for item in authorized_suppliers))
    authorized = set(normalized_authorized)
    exact = tuple(dict.fromkeys(item.upper() for item in _CANONICAL_SUPPLIER.findall(text)))
    if authorized and any(item not in authorized for item in exact):
        return _resolution(
            "supplier_ids",
            ResolutionStatus.UNAUTHORIZED,
            text,
            "A requested supplier is outside the current authorized scope.",
        )
    if exact:
        return _resolution(
            "supplier_ids",
            ResolutionStatus.EXACT,
            text,
            "Matched explicit canonical supplier IDs.",
            value=cast(JsonValue, list(exact)),
            input_source=input_source,
        )
    natural = _SUPPLIER_NATURAL.search(text)
    if natural is None:
        return _resolution(
            "supplier_ids",
            ResolutionStatus.MISSING,
            text,
            "No supplier expression was supplied.",
        )
    if not normalized_authorized:
        return _resolution(
            "supplier_ids",
            ResolutionStatus.MISSING,
            text,
            "A natural supplier label cannot be resolved without a bounded authorized list.",
        )
    token = natural.group(1).upper().removeprefix("SUP-")
    matches = tuple(item for item in normalized_authorized if item.removeprefix("SUP-") == token)
    if not matches:
        return _resolution(
            "supplier_ids",
            ResolutionStatus.UNAUTHORIZED,
            text,
            "The supplier expression does not match the current authorized scope.",
        )
    if len(matches) > 1:
        return _resolution(
            "supplier_ids",
            ResolutionStatus.AMBIGUOUS,
            text,
            "The supplier expression matches multiple authorized suppliers.",
            alternatives=matches,
        )
    return _resolution(
        "supplier_ids",
        ResolutionStatus.NORMALIZED,
        text,
        "Normalized a supplier label inside the authorized scope.",
        value=cast(JsonValue, list(matches)),
    )


def _entity_country(identifier: str) -> str | None:
    parts = identifier.upper().split("-")
    return parts[1] if len(parts) >= 3 and len(parts[1]) == 2 else None


def _narrow_by_suffix(text: str, alternatives: Iterable[str]) -> tuple[str, ...]:
    tokens = re.findall(r"\b[A-Za-z0-9]+\b", text)
    if not tokens:
        return ()
    return tuple(
        item
        for item in alternatives
        if any(item.upper().endswith(f"-{token.upper()}") for token in tokens)
    )


__all__ = ["resolve_legal_entities", "resolve_suppliers"]
