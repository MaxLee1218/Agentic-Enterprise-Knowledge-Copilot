"""Stage 1 tests for the versioned Accounts Payable contract union."""

from datetime import date

import pytest
from pydantic import ValidationError

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    ArtifactType,
    ContractSchemaVersion,
    DateRange,
    TaskContract,
    TaskType,
)
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_constraints


def test_ap_contract_binds_v2_constraints_artifact_and_task_type() -> None:
    contract = make_ap_contract()

    assert contract.contract_schema_version is ContractSchemaVersion.TASK_CONTRACT_V2
    assert contract.task_type is TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1
    assert isinstance(contract.constraints, AccountsPayableConstraintsV1)
    assert contract.expected_output.artifact_type is ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF
    assert len(contract.constraints.exception_types) == 6


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("read_only", False, "must be read-only"),
        ("currency_scope", ("usd",), "uppercase three-letter"),
        ("supplier_ids", ("SUP-001", "SUP-001"), "must be unique"),
        ("policy_rule_set_version", "latest", "must be ap_rules.2026.1"),
    ],
)
def test_ap_constraints_reject_scope_and_policy_relaxation(
    field: str, value: object, message: str
) -> None:
    payload = make_ap_contract().constraints.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        AccountsPayableConstraintsV1.model_validate(payload)


def test_ap_date_range_rejects_more_than_366_inclusive_days() -> None:
    with pytest.raises(ValidationError, match="366 inclusive days"):
        DateRange(start_date=date(2025, 1, 1), end_date=date(2026, 1, 2))


def test_task_contract_rejects_cross_domain_constraint_substitution() -> None:
    payload = make_ap_contract().model_dump()
    payload["constraints"] = make_constraints().model_dump()

    with pytest.raises(ValidationError, match="Accounts Payable constraints"):
        TaskContract.model_validate(payload)


def test_task_contract_rejects_cross_domain_artifact_substitution() -> None:
    payload = make_ap_contract().model_dump()
    payload["expected_output"]["artifact_type"] = "QUALITY_ANALYSIS_REPORT_PDF"

    with pytest.raises(ValidationError, match="Accounts Payable report artifact"):
        TaskContract.model_validate(payload)


def test_task_contract_rejects_wrong_schema_version_for_ap() -> None:
    payload = make_ap_contract().model_dump()
    payload["contract_schema_version"] = "task-contract.v1"

    with pytest.raises(ValidationError, match="requires task-contract.v2"):
        TaskContract.model_validate(payload)
