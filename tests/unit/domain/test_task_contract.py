"""Tests for structured task-contract scope and approval requirements."""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from copilot.contracts import ApprovalRequirement, TaskConstraints, TaskType
from tests.unit.domain.helpers import make_constraints, make_contract


def test_task_contract_captures_frozen_task_type_and_scope() -> None:
    """A valid contract should bind the supported task type and authorized scope."""
    contract = make_contract()

    assert contract.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
    assert contract.constraints.supplier_ids == ("S-100", "S-200")
    assert contract.approval_requirement.required is True


def test_task_constraints_reject_cross_quarter_range() -> None:
    """A frozen analysis period cannot silently extend outside its declared quarter."""
    values = make_constraints().model_dump()
    values["end_date"] = date(2026, 4, 1)

    with pytest.raises(ValidationError, match="declared year and quarter"):
        TaskConstraints.model_validate(values)


def test_task_constraints_reject_non_utc_deadline() -> None:
    """Execution deadlines must use UTC rather than an arbitrary timezone offset."""
    values = make_constraints().model_dump()
    values["deadline_at"] = datetime.fromisoformat("2026-07-19T09:00:00+08:00")

    with pytest.raises(ValidationError, match="must use UTC"):
        TaskConstraints.model_validate(values)


def test_required_approval_requires_policy_and_role() -> None:
    """A boolean flag alone cannot authorize or describe an approval boundary."""
    with pytest.raises(ValidationError, match="policy_id and approver_role"):
        ApprovalRequirement(required=True, controlled_scope=("quality.v1",))


def test_task_contract_rejects_duplicate_capabilities() -> None:
    """Duplicate registered capabilities must not enter a contract."""
    values = make_contract().model_dump()
    values["required_capabilities"] = ("knowledge_search", "knowledge_search")

    with pytest.raises(ValidationError, match="must be unique"):
        make_contract().__class__.model_validate(values)
