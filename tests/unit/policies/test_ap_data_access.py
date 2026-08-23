"""Deny-by-default AP role, purpose, scope, table, and field policy tests."""

from __future__ import annotations

from copilot.policies.data_access import DataAccessPolicy, DataAccessRequest


def _request(
    *,
    roles: tuple[str, ...] = ("finance_analyst",),
    scopes: tuple[str, ...] = ("finance:ap.detail",),
    purpose: str = "accounts_payable_analysis.v1",
    table_names: tuple[str, ...] = ("invoices",),
    field_names: tuple[str, ...] = ("invoices.gross_amount",),
) -> DataAccessRequest:
    return DataAccessRequest(
        roles=roles,
        scopes=scopes,
        table_names=table_names,
        field_names=field_names,
        purpose=purpose,
        is_demo_identity=False,
    )


def test_ap_detail_read_requires_finance_role_purpose_and_scope() -> None:
    policy = DataAccessPolicy()

    allowed = policy.evaluate(_request())
    missing_scope = policy.evaluate(_request(scopes=()))
    wrong_role = policy.evaluate(_request(roles=("quality_analyst",)))
    wrong_purpose = policy.evaluate(_request(purpose="supplier_quality_analysis.v1"))

    assert allowed.allowed is True
    assert allowed.matched_rules == ("accounts_payable_data_scope_v1",)
    assert missing_scope.reason_code == "AP_DETAIL_SCOPE_REQUIRED"
    assert wrong_role.reason_code == "UNKNOWN_ROLE"
    assert wrong_purpose.reason_code == "UNKNOWN_ROLE"


def test_ap_unregistered_sensitive_field_and_table_are_never_approvable() -> None:
    policy = DataAccessPolicy()

    field_denied = policy.evaluate(_request(field_names=("invoices.invoice_number",)))
    table_denied = policy.evaluate(_request(table_names=("bank_accounts",), field_names=()))

    assert field_denied.reason_code == "FIELD_NOT_ALLOWED"
    assert table_denied.reason_code == "TABLE_NOT_ALLOWED"
