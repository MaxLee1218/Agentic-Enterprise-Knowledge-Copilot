"""Deny-by-default domain manifest selection tests."""

import pytest

from copilot.contracts import CapabilityName, TaskType
from copilot.services.domains import DomainManifestError, builtin_domain_manifest_registry
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_contract


def test_manifest_selection_uses_exact_trusted_task_type() -> None:
    registry = builtin_domain_manifest_registry()
    manifest = registry.validate_contract(make_contract())

    assert manifest.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
    assert manifest.permission_purpose == TaskType.SUPPLIER_QUALITY_ANALYSIS_V1.value
    assert manifest.profile_for(CapabilityName.DATABASE_QUERY) == ("supplier_quality_database.v1")


def test_ap_contract_validates_and_execution_is_enabled_in_stage_8() -> None:
    registry = builtin_domain_manifest_registry()
    manifest = registry.validate_contract(make_ap_contract())

    assert manifest.task_type is TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1
    assert manifest.execution_enabled is True
    assert registry.require_execution(make_ap_contract()) is manifest


def test_unknown_or_fuzzy_domain_has_no_fallback() -> None:
    registry = builtin_domain_manifest_registry()

    with pytest.raises(DomainManifestError) as caught:
        registry.resolve("accounts_payable_analysis")
    assert caught.value.code == "DOMAIN_MANIFEST_NOT_FOUND"


def test_manifest_rejects_capability_set_substitution() -> None:
    contract = make_ap_contract().model_copy(
        update={"required_capabilities": (CapabilityName.KNOWLEDGE_SEARCH,)}
    )

    with pytest.raises(DomainManifestError) as caught:
        builtin_domain_manifest_registry().validate_contract(contract)
    assert caught.value.code == "DOMAIN_CAPABILITY_SET_MISMATCH"


def test_manifest_rejects_contract_with_missing_information() -> None:
    contract = make_contract().model_copy(update={"missing_information": ("year",)})

    with pytest.raises(DomainManifestError) as caught:
        builtin_domain_manifest_registry().validate_contract(contract)
    assert caught.value.code == "TASK_INFORMATION_MISSING"
