"""Immutable clarification-context merge and correction semantics."""

from copilot.contracts import (
    ClarificationContext,
    ClarificationResponse,
    ResolutionStatus,
)
from copilot.understanding.context_merge import (
    APRequiredFieldMerge,
    context_from_ap_merge,
    merge_accounts_payable_required_fields,
)


def _merge(
    message: str,
    *,
    context: ClarificationContext | None = None,
) -> APRequiredFieldMerge:
    return merge_accounts_payable_required_fields(
        original_request="Analyze Accounts Payable compliance recently.",
        authorized_entities=("LE-CN-01", "LE-US-01"),
        context=context,
        response=ClarificationResponse(message=message),
    )


def test_latest_time_correction_preserves_previously_validated_entity() -> None:
    first = _merge("year2025 CN")
    corrected = _merge(
        "No, only July through December.",
        context=context_from_ap_merge(first),
    )

    assert corrected.start_date is not None and corrected.start_date.isoformat() == "2025-07-01"
    assert corrected.end_date is not None and corrected.end_date.isoformat() == "2025-12-31"
    assert corrected.legal_entity_ids == ("LE-CN-01",)
    assert all(item.status is ResolutionStatus.NORMALIZED for item in corrected.resolutions)


def test_latest_entity_correction_overrides_only_the_entity() -> None:
    first = _merge("2025 CN")
    corrected = _merge(
        "Actually use LE-US-01.",
        context=context_from_ap_merge(first),
    )

    assert corrected.start_date is not None and corrected.start_date.isoformat() == "2025-01-01"
    assert corrected.end_date is not None and corrected.end_date.isoformat() == "2025-12-31"
    assert corrected.legal_entity_ids == ("LE-US-01",)


def test_rejecting_a_displayed_candidate_keeps_other_accepted_fields() -> None:
    candidate = _merge("2025 China")
    assert candidate.candidate is not None

    rejected = _merge("no", context=context_from_ap_merge(candidate))

    assert rejected.start_date is not None and rejected.start_date.isoformat() == "2025-01-01"
    assert rejected.legal_entity_ids == ()
    by_field = {item.field_name: item for item in rejected.resolutions}
    assert by_field["legal_entity_ids"].status is ResolutionStatus.MISSING
    assert by_field["time_range"].status is ResolutionStatus.NORMALIZED
