"""Deny-by-default role, capability, table, and field policy tests."""

from __future__ import annotations

from copilot.policies.data_access import DataAccessPolicy, DataAccessRequest
from copilot.policies.permissions import AuthorizationRequest, Permission, PermissionMatrix


def _tool_request(
    tool_name: str,
    *,
    roles: tuple[str, ...] = ("quality_analyst",),
    is_demo_identity: bool = False,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        action=Permission.EXECUTE_TOOL,
        roles=roles,
        resource_type="tool",
        resource_name=tool_name,
        tool_name=tool_name,
        task_id="T-001",
        is_demo_identity=is_demo_identity,
    )


def test_allowed_role_can_run_all_four_frozen_tools() -> None:
    matrix = PermissionMatrix()

    assert all(
        matrix.evaluate(_tool_request(tool_name)).allowed
        for tool_name in (
            "knowledge_search",
            "database_query",
            "analysis_engine",
            "report_generator",
        )
    )


def test_unknown_and_empty_production_roles_are_denied() -> None:
    matrix = PermissionMatrix()

    unknown = matrix.evaluate(_tool_request("knowledge_search", roles=("administrator",)))
    empty = matrix.evaluate(_tool_request("knowledge_search", roles=()))

    assert not unknown.allowed
    assert unknown.reason_code == "UNKNOWN_ROLE"
    assert not empty.allowed
    assert empty.reason_code == "UNKNOWN_ROLE"


def test_demo_empty_role_uses_documented_least_privilege_fallback_only() -> None:
    matrix = PermissionMatrix()

    allowed = matrix.evaluate(_tool_request("knowledge_search", roles=(), is_demo_identity=True))
    approval = matrix.evaluate(
        AuthorizationRequest(
            action=Permission.APPROVE_ACTION,
            roles=(),
            resource_type="approval",
            is_demo_identity=True,
        )
    )

    assert allowed.allowed
    assert not approval.allowed
    assert approval.reason_code == "APPROVAL_PERMISSION_DENIED"


def test_highest_demo_role_cannot_run_system_prohibited_capability() -> None:
    decision = PermissionMatrix().evaluate(
        _tool_request("database_write", roles=("quality_data_approver",))
    )

    assert not decision.allowed
    assert decision.reason_code == "TOOL_NOT_ALLOWED"


def test_data_policy_checks_every_table_and_field() -> None:
    policy = DataAccessPolicy()
    allowed = policy.evaluate(
        DataAccessRequest(
            roles=("quality_analyst",),
            table_names=("incoming_inspections", "suppliers"),
            field_names=(
                "incoming_inspections.rejected_quantity",
                "suppliers.supplier_code",
            ),
            purpose="supplier_quality_analysis.v1",
            is_demo_identity=False,
        )
    )
    table_denied = policy.evaluate(
        DataAccessRequest(
            roles=("quality_analyst",),
            table_names=("incoming_inspections", "payroll"),
            field_names=("incoming_inspections.rejected_quantity",),
            purpose="supplier_quality_analysis.v1",
            is_demo_identity=False,
        )
    )
    field_denied = policy.evaluate(
        DataAccessRequest(
            roles=("quality_data_approver",),
            table_names=("suppliers",),
            field_names=("suppliers.bank_account",),
            purpose="supplier_quality_analysis.v1",
            is_demo_identity=False,
        )
    )

    assert allowed.allowed
    assert table_denied.reason_code == "TABLE_NOT_ALLOWED"
    assert field_denied.reason_code == "FIELD_NOT_ALLOWED"
