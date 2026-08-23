"""Schema-bound Stage 4 input construction for frozen AP database templates."""

from __future__ import annotations

from typing import Literal

from copilot.contracts import AccountsPayableConstraintsV1, JsonObject

APDatabaseTemplateId = Literal[
    "ap_invoice_population_v1",
    "ap_duplicate_invoice_candidates_v1",
    "ap_invoice_po_variance_v1",
    "ap_payment_terms_v1",
    "ap_payment_amount_v1",
]

AP_DATABASE_TEMPLATE_IDS: tuple[APDatabaseTemplateId, ...] = (
    "ap_invoice_population_v1",
    "ap_duplicate_invoice_candidates_v1",
    "ap_invoice_po_variance_v1",
    "ap_payment_terms_v1",
    "ap_payment_amount_v1",
)


def build_accounts_payable_database_input(
    constraints: AccountsPayableConstraintsV1,
    template_id: APDatabaseTemplateId,
    *,
    row_limit: int = 50000,
) -> JsonObject:
    """Build one AP database input strictly from a validated trusted task scope.

    This helper does not enable the AP task manifest. It gives later workflow integration a
    deterministic, model-independent input boundary for the five frozen reads.
    """
    if template_id not in AP_DATABASE_TEMPLATE_IDS:
        raise ValueError("Accounts Payable database template is not frozen for v1")
    if row_limit < 1 or row_limit > 50000:
        raise ValueError("Accounts Payable row_limit must be between 1 and 50000")
    return JsonObject(
        {
            "query_template_id": template_id,
            "parameters": {
                "tenant_id": constraints.tenant_id,
                "start_date": constraints.time_range.start_date.isoformat(),
                "end_date": constraints.time_range.end_date.isoformat(),
                "supplier_ids": list(constraints.supplier_ids),
                "legal_entity_ids": list(constraints.legal_entity_ids),
                "business_unit_ids": list(constraints.business_unit_ids),
                "currency_scope": list(constraints.currency_scope),
            },
            "schema_version": "accounts_payable.v1",
            "snapshot_at": constraints.snapshot_at.isoformat(),
            "row_limit": row_limit,
        }
    )


__all__ = [
    "AP_DATABASE_TEMPLATE_IDS",
    "APDatabaseTemplateId",
    "build_accounts_payable_database_input",
]
