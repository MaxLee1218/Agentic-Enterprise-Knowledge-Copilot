"""Role-aware table and field authorization for frozen read-only query templates."""

from __future__ import annotations

from dataclasses import dataclass

from copilot.policies.permissions import PolicyDecision


@dataclass(frozen=True, slots=True)
class DataAccessRequest:
    """Normalized access request derived from a trusted template AST."""

    roles: tuple[str, ...]
    table_names: tuple[str, ...]
    field_names: tuple[str, ...]
    purpose: str
    is_demo_identity: bool
    scopes: tuple[str, ...] = ()


_QUALITY_TABLES = frozenset({"incoming_inspections", "suppliers"})
_QUALITY_FIELDS = frozenset(
    {
        "incoming_inspections.id",
        "incoming_inspections.supplier_id",
        "incoming_inspections.inspection_date",
        "incoming_inspections.total_quantity",
        "incoming_inspections.accepted_quantity",
        "incoming_inspections.rejected_quantity",
        "incoming_inspections.created_at",
        "suppliers.id",
        "suppliers.tenant_id",
        "suppliers.supplier_code",
        "suppliers.name",
        "suppliers.country",
        "suppliers.category",
        "suppliers.risk_level",
        "suppliers.created_at",
    }
)
_AP_TABLES = frozenset(
    {
        "business_units",
        "invoices",
        "legal_entities",
        "payments",
        "purchase_orders",
        "suppliers",
    }
)
_AP_FIELDS = frozenset(
    {
        "business_units.business_unit_code",
        "business_units.id",
        "business_units.legal_entity_id",
        "business_units.tenant_id",
        "invoices.business_unit_id",
        "invoices.currency",
        "invoices.due_date",
        "invoices.gross_amount",
        "invoices.id",
        "invoices.invoice_date",
        "invoices.invoice_type",
        "invoices.legal_entity_id",
        "invoices.net_amount",
        "invoices.no_po_exception_approved",
        "invoices.no_po_exception_ref",
        "invoices.normalized_invoice_number",
        "invoices.payment_terms_days",
        "invoices.posting_date",
        "invoices.purchase_order_id",
        "invoices.status",
        "invoices.supplier_id",
        "invoices.tax_amount",
        "invoices.tenant_id",
        "legal_entities.id",
        "legal_entities.legal_entity_code",
        "legal_entities.tenant_id",
        "payments.business_unit_id",
        "payments.currency",
        "payments.id",
        "payments.invoice_id",
        "payments.legal_entity_id",
        "payments.payment_amount",
        "payments.payment_date",
        "payments.status",
        "payments.tenant_id",
        "purchase_orders.approved_amount",
        "purchase_orders.business_unit_id",
        "purchase_orders.currency",
        "purchase_orders.id",
        "purchase_orders.legal_entity_id",
        "purchase_orders.matching_basis",
        "purchase_orders.status",
        "purchase_orders.supplier_id",
        "purchase_orders.tenant_id",
        "suppliers.id",
        "suppliers.supplier_code",
        "suppliers.tenant_id",
    }
)
_AP_BASE_FIELDS = frozenset(
    {
        "business_units.business_unit_code",
        "business_units.id",
        "business_units.legal_entity_id",
        "business_units.tenant_id",
        "invoices.business_unit_id",
        "invoices.currency",
        "invoices.id",
        "invoices.invoice_date",
        "invoices.legal_entity_id",
        "invoices.supplier_id",
        "invoices.tenant_id",
        "legal_entities.id",
        "legal_entities.legal_entity_code",
        "legal_entities.tenant_id",
        "suppliers.id",
        "suppliers.supplier_code",
        "suppliers.tenant_id",
    }
)
_AP_PAYMENT_CARDINALITY_FIELDS = frozenset(
    {
        "payments.id",
        "payments.invoice_id",
        "payments.status",
        "payments.tenant_id",
    }
)
_AP_PAYMENT_LINK_FIELDS = _AP_PAYMENT_CARDINALITY_FIELDS | {
    "payments.business_unit_id",
    "payments.legal_entity_id",
}
_ROLE_TABLES = {
    "quality_analyst": _QUALITY_TABLES,
    "quality_data_approver": _QUALITY_TABLES,
    "finance_analyst": _AP_TABLES,
    "finance_approver": _AP_TABLES,
    "finance_auditor": _AP_TABLES,
}
_ROLE_FIELDS = {
    "quality_analyst": _QUALITY_FIELDS,
    "quality_data_approver": _QUALITY_FIELDS,
    "finance_analyst": _AP_FIELDS,
    "finance_approver": _AP_FIELDS,
    "finance_auditor": _AP_FIELDS,
}
_QUERY_TEMPLATE_ACCESS_PROFILES = {
    "supplier_quality_summary_v1": (
        ("incoming_inspections", "suppliers"),
        (
            "incoming_inspections.inspection_date",
            "incoming_inspections.rejected_quantity",
            "incoming_inspections.supplier_id",
            "incoming_inspections.total_quantity",
            "suppliers.id",
            "suppliers.supplier_code",
            "suppliers.tenant_id",
        ),
    ),
    "supplier_quality_trend_v1": (
        ("incoming_inspections", "suppliers"),
        (
            "incoming_inspections.inspection_date",
            "incoming_inspections.rejected_quantity",
            "incoming_inspections.supplier_id",
            "incoming_inspections.total_quantity",
            "suppliers.id",
            "suppliers.supplier_code",
            "suppliers.tenant_id",
        ),
    ),
    "ap_invoice_population_v1": (
        (
            "business_units",
            "invoices",
            "legal_entities",
            "payments",
            "purchase_orders",
            "suppliers",
        ),
        tuple(
            sorted(
                _AP_BASE_FIELDS
                | _AP_PAYMENT_CARDINALITY_FIELDS
                | {
                    "invoices.due_date",
                    "invoices.gross_amount",
                    "invoices.invoice_type",
                    "invoices.net_amount",
                    "invoices.posting_date",
                    "invoices.purchase_order_id",
                    "invoices.status",
                    "invoices.tax_amount",
                    "purchase_orders.id",
                    "purchase_orders.matching_basis",
                    "purchase_orders.status",
                    "purchase_orders.tenant_id",
                }
            )
        ),
    ),
    "ap_duplicate_invoice_candidates_v1": (
        ("business_units", "invoices", "legal_entities", "suppliers"),
        tuple(
            sorted(
                _AP_BASE_FIELDS
                | {
                    "invoices.gross_amount",
                    "invoices.invoice_type",
                    "invoices.normalized_invoice_number",
                    "invoices.status",
                }
            )
        ),
    ),
    "ap_invoice_po_variance_v1": (
        ("business_units", "invoices", "legal_entities", "purchase_orders", "suppliers"),
        tuple(
            sorted(
                _AP_BASE_FIELDS
                | {
                    "invoices.gross_amount",
                    "invoices.invoice_type",
                    "invoices.no_po_exception_approved",
                    "invoices.no_po_exception_ref",
                    "invoices.purchase_order_id",
                    "invoices.status",
                    "purchase_orders.approved_amount",
                    "purchase_orders.business_unit_id",
                    "purchase_orders.currency",
                    "purchase_orders.id",
                    "purchase_orders.legal_entity_id",
                    "purchase_orders.matching_basis",
                    "purchase_orders.status",
                    "purchase_orders.supplier_id",
                    "purchase_orders.tenant_id",
                }
            )
        ),
    ),
    "ap_payment_terms_v1": (
        ("business_units", "invoices", "legal_entities", "payments", "suppliers"),
        tuple(
            sorted(
                _AP_BASE_FIELDS
                | _AP_PAYMENT_LINK_FIELDS
                | {
                    "invoices.due_date",
                    "invoices.invoice_type",
                    "invoices.payment_terms_days",
                    "invoices.status",
                    "payments.currency",
                    "payments.payment_date",
                }
            )
        ),
    ),
    "ap_payment_amount_v1": (
        ("business_units", "invoices", "legal_entities", "payments", "suppliers"),
        tuple(
            sorted(
                _AP_BASE_FIELDS
                | _AP_PAYMENT_LINK_FIELDS
                | {
                    "invoices.gross_amount",
                    "invoices.invoice_type",
                    "invoices.status",
                    "payments.currency",
                    "payments.payment_amount",
                    "payments.payment_date",
                }
            )
        ),
    ),
}


def access_profile_for_query_template(
    template_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the full physical footprint of an approved frozen query template."""
    return _QUERY_TEMPLATE_ACCESS_PROFILES.get(template_id, ((), ()))


class DataAccessPolicy:
    """Fail closed on unknown role, purpose, table, or field."""

    def evaluate(self, request: DataAccessRequest) -> PolicyDecision:
        """Authorize all referenced objects, not merely the projected output columns."""
        roles = request.roles or (("quality_analyst",) if request.is_demo_identity else ())
        if not roles or any(role not in _ROLE_TABLES for role in roles):
            return _decision(False, "UNKNOWN_ROLE", "Data-access role is not recognized")
        if request.purpose not in {
            "supplier_quality_analysis.v1",
            "accounts_payable_analysis.v1",
        }:
            return _decision(
                False,
                "V1_1_CAPABILITY_NOT_ALLOWED",
                "Data-access purpose is outside the frozen scenario",
            )
        if request.purpose == "supplier_quality_analysis.v1" and any(
            role not in {"quality_analyst", "quality_data_approver"} for role in roles
        ):
            return _decision(False, "UNKNOWN_ROLE", "Role is not authorized for this purpose")
        if request.purpose == "accounts_payable_analysis.v1":
            if any(
                role not in {"finance_analyst", "finance_approver", "finance_auditor"}
                for role in roles
            ):
                return _decision(False, "UNKNOWN_ROLE", "Role is not authorized for this purpose")
            if "finance:ap.detail" not in request.scopes:
                return _decision(
                    False,
                    "AP_DETAIL_SCOPE_REQUIRED",
                    "Detailed Accounts Payable data requires finance:ap.detail",
                )
        allowed_tables = set().union(*(_ROLE_TABLES[role] for role in roles))
        if not set(request.table_names).issubset(allowed_tables):
            return _decision(False, "TABLE_NOT_ALLOWED", "Query references an unauthorized table")
        allowed_fields = set().union(*(_ROLE_FIELDS[role] for role in roles))
        if not set(request.field_names).issubset(allowed_fields):
            return _decision(False, "FIELD_NOT_ALLOWED", "Query references an unauthorized field")
        matched_rule = (
            "accounts_payable_data_scope_v1"
            if request.purpose == "accounts_payable_analysis.v1"
            else "supplier_quality_data_scope_v1"
        )
        return _decision(
            True,
            "ALLOWED",
            "Tables and fields are explicitly allowed",
            matched_rule=matched_rule,
        )


def _decision(
    allowed: bool,
    code: str,
    reason: str,
    *,
    matched_rule: str = "supplier_quality_data_scope_v1",
) -> PolicyDecision:
    return PolicyDecision(
        allowed=allowed,
        reason_code=code,
        reason=reason,
        required_permissions=(),
        matched_rules=(matched_rule,) if allowed else ("deny_by_default",),
    )


__all__ = [
    "DataAccessPolicy",
    "DataAccessRequest",
    "access_profile_for_query_template",
]
