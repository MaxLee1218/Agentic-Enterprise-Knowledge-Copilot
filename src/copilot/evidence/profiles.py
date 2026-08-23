"""Code-owned verifier profiles selected by the frozen domain manifest identity."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from copilot.contracts import TaskType, VerifierProfileV1
from copilot.contracts.validators import utc_now
from copilot.evidence.ap_validators import (
    APConsistencyVerifier,
    APEvidenceMetadataVerifier,
    APNumericVerifier,
    APPolicyBindingVerifier,
)
from copilot.evidence.validators import (
    CitationVerifier,
    CompositeVerifier,
    DeliverableVerifier,
    EvidenceStructureVerifier,
    SafetyVerifier,
)
from copilot.security import SensitiveDataRegistry
from copilot.tools.database import SchemaRegistry

SUPPLIER_QUALITY_VERIFIER_PROFILE_ID = "supplier_quality_verifier.v1"
ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID = "accounts_payable_verifier.v1"


def _profile(
    profile_id: str,
    task_type: TaskType,
    schema: SchemaRegistry,
) -> VerifierProfileV1:
    sensitive = SensitiveDataRegistry()
    return VerifierProfileV1(
        profile_id=profile_id,
        task_type=task_type,
        allowed_tables=schema.list_tables(),
        allowed_columns=schema.list_columns(),
        allowed_query_templates=schema.list_templates(),
        sensitive_fields=tuple(sorted(set(sensitive.sensitive_names()))),
    )


SUPPLIER_QUALITY_VERIFIER_PROFILE = _profile(
    SUPPLIER_QUALITY_VERIFIER_PROFILE_ID,
    TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
    SchemaRegistry(),
)
ACCOUNTS_PAYABLE_VERIFIER_PROFILE = _profile(
    ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID,
    TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
    SchemaRegistry.accounts_payable(),
)

_PROFILES = {
    SUPPLIER_QUALITY_VERIFIER_PROFILE_ID: SUPPLIER_QUALITY_VERIFIER_PROFILE,
    ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID: ACCOUNTS_PAYABLE_VERIFIER_PROFILE,
}


def verifier_profile(profile_id: str) -> VerifierProfileV1:
    """Resolve an exact known profile or fail closed without domain fallback."""
    try:
        return _PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown verifier profile: {profile_id}") from exc


def composite_verifier_for_profile(
    profile_id: str,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> CompositeVerifier:
    """Build one CompositeVerifier with domain rules in the existing verification slots."""
    profile = verifier_profile(profile_id)
    if profile.profile_id == SUPPLIER_QUALITY_VERIFIER_PROFILE_ID:
        # Preserve the existing UC1 verifier order and behavior exactly.
        return CompositeVerifier(clock=clock)
    return CompositeVerifier(
        verifiers=(
            EvidenceStructureVerifier(),
            APEvidenceMetadataVerifier(),
            DeliverableVerifier(),
            CitationVerifier(),
            APPolicyBindingVerifier(),
            APConsistencyVerifier(),
            APNumericVerifier(),
            SafetyVerifier(profile=profile, clock=clock),
        ),
        clock=clock,
    )


__all__ = [
    "ACCOUNTS_PAYABLE_VERIFIER_PROFILE",
    "ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID",
    "SUPPLIER_QUALITY_VERIFIER_PROFILE",
    "SUPPLIER_QUALITY_VERIFIER_PROFILE_ID",
    "composite_verifier_for_profile",
    "verifier_profile",
]
