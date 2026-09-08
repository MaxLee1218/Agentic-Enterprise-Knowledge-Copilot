"""Typed resolution-policy and candidate-version coverage."""

import pytest
from pydantic import ValidationError

from copilot.contracts import FieldResolution, ResolutionSource, ResolutionStatus
from copilot.understanding.policy import ResolutionPolicy, build_candidate_interpretation


def _resolution(status: ResolutionStatus) -> FieldResolution:
    confirmable = status is ResolutionStatus.CONFIRMATION_REQUIRED
    return FieldResolution(
        field_name="legal_entity_ids",
        status=status,
        candidate_value=["LE-CN-01"] if confirmable else None,
        canonical_value=["LE-CN-01"] if status is ResolutionStatus.NORMALIZED else None,
        reason="test resolution",
        source=ResolutionSource.DETERMINISTIC_NORMALIZER,
        requires_confirmation=confirmable,
        alternatives=("LE-CN-01", "LE-CN-02") if status is ResolutionStatus.AMBIGUOUS else (),
    )


def test_policy_accepts_only_validated_exact_or_normalized_values() -> None:
    policy = ResolutionPolicy()
    normalized = _resolution(ResolutionStatus.NORMALIZED)
    confirmation = _resolution(ResolutionStatus.CONFIRMATION_REQUIRED)
    unauthorized = _resolution(ResolutionStatus.UNAUTHORIZED)

    assert policy.accepts(normalized)
    assert policy.requires_confirmation(confirmation)
    assert policy.blocks_execution(confirmation)
    assert policy.is_denial(unauthorized)


def test_candidate_hash_is_stable_and_changes_with_displayed_candidate() -> None:
    resolution = _resolution(ResolutionStatus.CONFIRMATION_REQUIRED)
    first = build_candidate_interpretation((resolution,), "legal entity LE-CN-01")
    replay = build_candidate_interpretation((resolution,), "legal entity LE-CN-01")
    changed = build_candidate_interpretation(
        (
            resolution.model_copy(
                update={"candidate_value": ["LE-US-01"], "reason": "different candidate"}
            ),
        ),
        "legal entity LE-US-01",
    )

    assert first.version_hash == replay.version_hash
    assert changed.version_hash != first.version_hash


def test_ambiguous_field_resolution_requires_multiple_alternatives() -> None:
    with pytest.raises(ValidationError, match="at least two alternatives"):
        FieldResolution(
            field_name="legal_entity_ids",
            status=ResolutionStatus.AMBIGUOUS,
            reason="ambiguous",
            source=ResolutionSource.DETERMINISTIC_NORMALIZER,
            alternatives=("LE-CN-01",),
        )
