"""Merge immutable request, validated clarification facts, and the latest user response."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import JsonValue

from copilot.contracts import (
    CandidateInterpretation,
    ClarificationContext,
    ClarificationResponse,
    FieldResolution,
    JsonObject,
    ResolutionSource,
    ResolutionStatus,
)
from copilot.understanding.confirmation import ConfirmationIntent, parse_confirmation_intent
from copilot.understanding.date_normalizer import normalize_accounts_payable_time_range
from copilot.understanding.entity_resolver import resolve_legal_entities


@dataclass(frozen=True, slots=True)
class APRequiredFieldMerge:
    """Resolved AP mandatory fields plus any still-pending interpretation."""

    start_date: date | None
    end_date: date | None
    legal_entity_ids: tuple[str, ...]
    resolutions: tuple[FieldResolution, ...]
    candidate: CandidateInterpretation | None = None


def merge_accounts_payable_required_fields(
    *,
    original_request: str,
    authorized_entities: tuple[str, ...],
    context: ClarificationContext | None,
    response: ClarificationResponse | None,
) -> APRequiredFieldMerge:
    """Apply latest-message correction semantics without mutating the original request."""
    prior_values = dict(context.values.root) if context is not None else {}
    prior_resolutions = {item.field_name: item for item in context.resolutions} if context else {}
    # Only deterministic normalization or previously validated context may set scope.
    # The model candidate is deliberately excluded: schema-valid output alone is not
    # evidence that a date or entity appeared in the user's request.
    start = _date_value(prior_values.get("start_date"))
    end = _date_value(prior_values.get("end_date"))
    entities = _string_tuple(prior_values.get("legal_entity_ids"))
    latest = response.message if response is not None and response.message is not None else ""
    intent = parse_confirmation_intent(latest)

    if context is not None and context.pending_candidate is not None:
        if intent is ConfirmationIntent.AFFIRM:
            resolutions = dict(prior_resolutions)
            for pending_resolution in context.pending_candidate.resolutions:
                accepted = pending_resolution.model_copy(
                    update={
                        "status": ResolutionStatus.NORMALIZED,
                        "canonical_value": pending_resolution.candidate_value,
                        "requires_confirmation": False,
                        "reason": f"Confirmed: {pending_resolution.reason}",
                        "source": ResolutionSource.CLARIFICATION_RESPONSE,
                    }
                )
                resolutions[accepted.field_name] = accepted
                if accepted.field_name == "time_range":
                    start, end = _range_value(accepted.canonical_value)
                elif accepted.field_name == "legal_entity_ids":
                    entities = _string_tuple(accepted.canonical_value)
            return APRequiredFieldMerge(start, end, entities, tuple(resolutions.values()))
        if intent is ConfirmationIntent.REJECT:
            rejected_fields = {item.field_name for item in context.pending_candidate.resolutions}
            resolutions = dict(prior_resolutions)
            for rejected in context.pending_candidate.resolutions:
                resolutions[rejected.field_name] = rejected.model_copy(
                    update={
                        "status": ResolutionStatus.MISSING,
                        "candidate_value": None,
                        "canonical_value": None,
                        "reason": "The displayed interpretation was rejected by the user.",
                        "source": ResolutionSource.CLARIFICATION_RESPONSE,
                        "requires_confirmation": False,
                        "alternatives": (),
                    }
                )
            if "time_range" in rejected_fields:
                start = end = None
            if "legal_entity_ids" in rejected_fields:
                entities = ()
            return APRequiredFieldMerge(start, end, entities, tuple(resolutions.values()))

    structured_range = None
    structured_entities: tuple[str, ...] = ()
    if response is not None:
        structured_range = response.answers.root.get("time_range")
        structured_entities = _string_tuple(response.answers.root.get("legal_entity_ids"))

    resolution_text = latest if response is not None else original_request
    input_source = (
        ResolutionSource.CLARIFICATION_RESPONSE
        if response is not None
        else ResolutionSource.ORIGINAL_REQUEST
    )
    date_resolution: FieldResolution
    if isinstance(structured_range, dict):
        structured_start = _date_value(structured_range.get("start_date"))
        structured_end = _date_value(structured_range.get("end_date"))
        if structured_start is not None and structured_end is not None:
            value: dict[str, JsonValue] = {
                "start_date": structured_start.isoformat(),
                "end_date": structured_end.isoformat(),
            }
            date_resolution = FieldResolution(
                field_name="time_range",
                status=ResolutionStatus.EXACT,
                candidate_value=value,
                canonical_value=value,
                reason="Accepted structured exact date range",
                source=ResolutionSource.CLARIFICATION_RESPONSE,
            )
        else:
            date_resolution = normalize_accounts_payable_time_range(
                resolution_text,
                input_source=input_source,
            )
    else:
        date_resolution = normalize_accounts_payable_time_range(
            resolution_text,
            prior_start=start,
            prior_end=end,
            input_source=input_source,
        )
    if date_resolution.status in {ResolutionStatus.EXACT, ResolutionStatus.NORMALIZED}:
        start, end = _range_value(date_resolution.canonical_value)
    elif (
        start is not None and end is not None and date_resolution.status is ResolutionStatus.MISSING
    ):
        date_resolution = prior_resolutions.get("time_range") or _prior_range(start, end)

    alternatives: tuple[str, ...] = ()
    prior_entity = prior_resolutions.get("legal_entity_ids")
    if prior_entity is not None and prior_entity.status is ResolutionStatus.AMBIGUOUS:
        alternatives = tuple(str(item) for item in prior_entity.alternatives)
    if structured_entities:
        entity_resolution = resolve_legal_entities(
            " ".join(structured_entities),
            authorized_entities,
            input_source=ResolutionSource.CLARIFICATION_RESPONSE,
        )
    else:
        entity_resolution = resolve_legal_entities(
            resolution_text,
            authorized_entities,
            current_alternatives=alternatives,
            input_source=input_source,
        )
    if entity_resolution.status in {ResolutionStatus.EXACT, ResolutionStatus.NORMALIZED}:
        entities = _string_tuple(entity_resolution.canonical_value)
    elif entity_resolution.status is ResolutionStatus.MISSING and entities:
        entity_resolution = prior_entity or _prior_entities(entities)

    resolutions_by_field = dict(prior_resolutions)
    resolutions_by_field["time_range"] = date_resolution
    resolutions_by_field["legal_entity_ids"] = entity_resolution
    confirmable = tuple(
        item
        for item in resolutions_by_field.values()
        if item.status is ResolutionStatus.CONFIRMATION_REQUIRED
    )
    candidate: CandidateInterpretation | None = None
    if confirmable:
        from copilot.understanding.policy import build_candidate_interpretation

        candidate = build_candidate_interpretation(
            confirmable,
            _candidate_display(confirmable),
        )
    return APRequiredFieldMerge(
        start,
        end,
        entities,
        tuple(resolutions_by_field.values()),
        candidate,
    )


def context_from_ap_merge(merge: APRequiredFieldMerge) -> ClarificationContext:
    """Persist only accepted values while retaining typed unresolved state."""
    values: dict[str, JsonValue] = {}
    accepted = {ResolutionStatus.EXACT, ResolutionStatus.NORMALIZED}
    by_field = {item.field_name: item for item in merge.resolutions}
    if (
        merge.start_date is not None
        and merge.end_date is not None
        and by_field.get("time_range") is not None
        and by_field["time_range"].status in accepted
    ):
        values.update(
            start_date=merge.start_date.isoformat(),
            end_date=merge.end_date.isoformat(),
        )
    if (
        merge.legal_entity_ids
        and by_field.get("legal_entity_ids") is not None
        and by_field["legal_entity_ids"].status in accepted
    ):
        values["legal_entity_ids"] = list(merge.legal_entity_ids)
    return ClarificationContext(
        values=JsonObject(values),
        resolutions=merge.resolutions,
        pending_candidate=merge.candidate,
    )


def _date_value(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _range_value(value: object) -> tuple[date | None, date | None]:
    if not isinstance(value, dict):
        return None, None
    return _date_value(value.get("start_date")), _date_value(value.get("end_date"))


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.upper(),)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(dict.fromkeys(item.upper() for item in value))
    return ()


def _prior_range(start: date, end: date) -> FieldResolution:
    value: dict[str, JsonValue] = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    return FieldResolution(
        field_name="time_range",
        status=ResolutionStatus.EXACT,
        candidate_value=value,
        canonical_value=value,
        reason="Retained a previously validated date range",
        source=ResolutionSource.PRIOR_VALIDATED_CLARIFICATION,
    )


def _prior_entities(entities: tuple[str, ...]) -> FieldResolution:
    value: list[JsonValue] = list(entities)
    return FieldResolution(
        field_name="legal_entity_ids",
        status=ResolutionStatus.EXACT,
        candidate_value=value,
        canonical_value=value,
        reason="Retained previously validated legal entities",
        source=ResolutionSource.PRIOR_VALIDATED_CLARIFICATION,
    )


def _candidate_display(resolutions: tuple[FieldResolution, ...]) -> str:
    parts: list[str] = []
    for item in resolutions:
        if item.field_name == "legal_entity_ids":
            parts.append(f"legal entity {', '.join(_string_tuple(item.candidate_value))}")
        elif item.field_name == "time_range":
            start, end = _range_value(item.candidate_value)
            if start is not None and end is not None:
                parts.append(f"{start.isoformat()} through {end.isoformat()}")
    return " and ".join(parts) or "the displayed interpretation"


__all__ = [
    "APRequiredFieldMerge",
    "context_from_ap_merge",
    "merge_accounts_payable_required_fields",
]
