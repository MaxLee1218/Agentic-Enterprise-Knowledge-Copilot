"""Deterministic field-level wording derived from typed resolution state."""

from __future__ import annotations

from datetime import date

from copilot.contracts import FieldResolution, ResolutionStatus

_ACCEPTED = {ResolutionStatus.EXACT, ResolutionStatus.NORMALIZED}


def ap_time_question(
    time_resolution: FieldResolution,
    entity_resolution: FieldResolution,
) -> str:
    """Ask only for time while acknowledging an accepted entity when available."""
    prefix = ""
    if entity_resolution.status in _ACCEPTED:
        values = _strings(entity_resolution.canonical_value)
        if values:
            prefix = f"I understood the legal entity as {', '.join(values)}. "
    invalid = (
        f"{time_resolution.reason} " if time_resolution.status is ResolutionStatus.INVALID else ""
    )
    return f"{prefix}{invalid}What exact period would you like me to analyze?"


def ap_entity_question(
    time_resolution: FieldResolution,
    entity_resolution: FieldResolution,
) -> str:
    """Ask only for entity while acknowledging accepted time and listing ambiguity."""
    prefix = ""
    if time_resolution.status in _ACCEPTED and isinstance(time_resolution.canonical_value, dict):
        start = _date(time_resolution.canonical_value.get("start_date"))
        end = _date(time_resolution.canonical_value.get("end_date"))
        if start is not None and end is not None:
            prefix = (
                f"I understood the period as {start.strftime('%B')} {start.day}, {start.year} "
                f"through {end.strftime('%B')} {end.day}, {end.year}. "
            )
    alternatives = tuple(str(item) for item in entity_resolution.alternatives)
    if alternatives:
        return (
            f"{prefix}I found multiple authorized matching legal entities: "
            f"{', '.join(alternatives)}. Which one should I use?"
        )
    return f"{prefix}Which authorized legal entity should I use?"


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ()


__all__ = ["ap_entity_question", "ap_time_question"]
