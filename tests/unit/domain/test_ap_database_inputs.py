"""Stage 4 AP database input builder tests."""

from __future__ import annotations

import pytest

from copilot.contracts import AccountsPayableConstraintsV1
from copilot.services.domains import (
    AP_DATABASE_TEMPLATE_IDS,
    APDatabaseTemplateId,
    build_accounts_payable_database_input,
)
from tests.unit.domain.ap_helpers import make_ap_contract


@pytest.mark.parametrize("template_id", AP_DATABASE_TEMPLATE_IDS)
def test_ap_database_input_is_built_only_from_validated_contract_scope(
    template_id: APDatabaseTemplateId,
) -> None:
    scope = make_ap_contract().constraints
    assert isinstance(scope, AccountsPayableConstraintsV1)
    result = build_accounts_payable_database_input(scope, template_id, row_limit=123)

    assert result.root == {
        "query_template_id": template_id,
        "parameters": {
            "tenant_id": "TENANT-A",
            "start_date": "2026-04-01",
            "end_date": "2026-06-30",
            "supplier_ids": ["SUP-001", "SUP-002"],
            "legal_entity_ids": ["LE-001"],
            "business_unit_ids": ["BU-001"],
            "currency_scope": ["USD", "CNY"],
        },
        "schema_version": "accounts_payable.v1",
        "snapshot_at": "2026-07-01T00:00:00+00:00",
        "row_limit": 123,
    }


@pytest.mark.parametrize("row_limit", [0, 50001])
def test_ap_database_input_rejects_out_of_bounds_row_limit(row_limit: int) -> None:
    scope = make_ap_contract().constraints
    assert isinstance(scope, AccountsPayableConstraintsV1)
    with pytest.raises(ValueError, match="between 1 and 50000"):
        build_accounts_payable_database_input(
            scope,
            "ap_invoice_population_v1",
            row_limit=row_limit,
        )
