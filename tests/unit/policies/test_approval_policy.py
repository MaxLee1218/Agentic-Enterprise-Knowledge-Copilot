"""Structured ALLOW, REQUIRE_APPROVAL, and DENY policy decisions."""

from copilot.contracts import JsonObject
from copilot.policies.approval import PolicyOutcome, SupplierQualityApprovalPolicy
from copilot.tools.mock_supplier_quality import MockDatabaseTool, MockKnowledgeTool
from tests.unit.domain.helpers import make_contract, make_plan


def test_required_contract_waits_at_first_controlled_database_action() -> None:
    contract = make_contract()
    step = make_plan().steps[0]
    definition = MockDatabaseTool.definition

    decision = SupplierQualityApprovalPolicy().evaluate(
        contract=contract,
        step=step,
        definition=definition,
        arguments=JsonObject({}),
    )

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.required_role == "quality_data_approver"
    assert decision.editable_fields == ("row_limit",)


def test_current_plan_approval_allows_remaining_bound_actions() -> None:
    step = make_plan().steps[0]
    decision = SupplierQualityApprovalPolicy().evaluate(
        contract=make_contract(),
        step=step,
        definition=MockDatabaseTool.definition,
        arguments=JsonObject({}),
        has_current_plan_approval=True,
    )

    assert decision.outcome is PolicyOutcome.ALLOW


def test_mismatched_registered_tool_is_denied() -> None:
    step = make_plan().steps[0]
    decision = SupplierQualityApprovalPolicy().evaluate(
        contract=make_contract(),
        step=step,
        definition=MockKnowledgeTool.definition,
        arguments=JsonObject({}),
    )

    assert decision.outcome is PolicyOutcome.DENY
