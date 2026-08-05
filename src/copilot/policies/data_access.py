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
_ROLE_TABLES = {
    "quality_analyst": _QUALITY_TABLES,
    "quality_data_approver": _QUALITY_TABLES,
}
_ROLE_FIELDS = {
    "quality_analyst": _QUALITY_FIELDS,
    "quality_data_approver": _QUALITY_FIELDS,
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
        if request.purpose != "supplier_quality_analysis.v1":
            return _decision(
                False,
                "V1_1_CAPABILITY_NOT_ALLOWED",
                "Data-access purpose is outside the frozen scenario",
            )
        allowed_tables = set().union(*(_ROLE_TABLES[role] for role in roles))
        if not set(request.table_names).issubset(allowed_tables):
            return _decision(False, "TABLE_NOT_ALLOWED", "Query references an unauthorized table")
        allowed_fields = set().union(*(_ROLE_FIELDS[role] for role in roles))
        if not set(request.field_names).issubset(allowed_fields):
            return _decision(False, "FIELD_NOT_ALLOWED", "Query references an unauthorized field")
        return _decision(True, "ALLOWED", "Tables and fields are explicitly allowed")


def _decision(allowed: bool, code: str, reason: str) -> PolicyDecision:
    return PolicyDecision(
        allowed=allowed,
        reason_code=code,
        reason=reason,
        required_permissions=(),
        matched_rules=("supplier_quality_data_scope_v1",) if allowed else ("deny_by_default",),
    )


__all__ = [
    "DataAccessPolicy",
    "DataAccessRequest",
    "access_profile_for_query_template",
]
