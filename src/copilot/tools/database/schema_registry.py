"""Deny-by-default registries for frozen governed database schemas."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from sqlalchemy import Table

from copilot.policies.data_access import access_profile_for_query_template
from copilot.tools.database.models import (
    CorrectiveAction,
    IncomingInspection,
    Supplier,
    SupplierDeviation,
)

SCHEMA_VERSION = "quality.v1"
ACCOUNTS_PAYABLE_SCHEMA_VERSION = "accounts_payable.v1"
DEFAULT_SENSITIVE_COLUMNS = frozenset(
    {
        "supplier_deviations.description",
        "corrective_actions.description",
    }
)


def _registered_columns() -> dict[str, frozenset[str]]:
    tables = (
        cast(Table, Supplier.__table__),
        cast(Table, SupplierDeviation.__table__),
        cast(Table, IncomingInspection.__table__),
        cast(Table, CorrectiveAction.__table__),
    )
    return {table.name: frozenset(column.name for column in table.columns) for table in tables}


def _accounts_payable_columns() -> dict[str, frozenset[str]]:
    """Return only fields required by the five frozen AP read models."""
    return {
        "suppliers": frozenset({"id", "tenant_id", "supplier_code"}),
        "legal_entities": frozenset({"id", "tenant_id", "legal_entity_code"}),
        "business_units": frozenset({"id", "tenant_id", "legal_entity_id", "business_unit_code"}),
        "purchase_orders": frozenset(
            {
                "id",
                "tenant_id",
                "supplier_id",
                "legal_entity_id",
                "business_unit_id",
                "approved_amount",
                "currency",
                "matching_basis",
                "status",
            }
        ),
        "invoices": frozenset(
            {
                "id",
                "tenant_id",
                "supplier_id",
                "legal_entity_id",
                "business_unit_id",
                "normalized_invoice_number",
                "invoice_type",
                "invoice_date",
                "posting_date",
                "currency",
                "net_amount",
                "tax_amount",
                "gross_amount",
                "purchase_order_id",
                "payment_terms_days",
                "due_date",
                "no_po_exception_ref",
                "no_po_exception_approved",
                "status",
            }
        ),
        "payments": frozenset(
            {
                "id",
                "tenant_id",
                "invoice_id",
                "legal_entity_id",
                "business_unit_id",
                "payment_date",
                "payment_amount",
                "currency",
                "status",
            }
        ),
    }


class SchemaRegistry:
    """Expose only approved tables, fields, functions, and query templates."""

    def __init__(
        self,
        *,
        schema_version: str = SCHEMA_VERSION,
        tables: Mapping[str, frozenset[str]] | None = None,
        sensitive_columns: frozenset[str] | None = None,
        query_templates: frozenset[str] | None = None,
        functions: frozenset[str] | None = None,
    ) -> None:
        self._schema_version = schema_version
        registered = dict(tables or _registered_columns())
        self._tables: Mapping[str, frozenset[str]] = MappingProxyType(registered)
        self._sensitive_columns = (
            DEFAULT_SENSITIVE_COLUMNS if sensitive_columns is None else sensitive_columns
        )
        self._query_templates = (
            query_templates
            if query_templates is not None
            else frozenset({"supplier_quality_summary_v1", "supplier_quality_trend_v1"})
        )
        self._functions = functions if functions is not None else frozenset({"month_period", "sum"})

    @classmethod
    def accounts_payable(cls) -> SchemaRegistry:
        """Create the isolated ``accounts_payable.v1`` allowlist."""
        return cls(
            schema_version=ACCOUNTS_PAYABLE_SCHEMA_VERSION,
            tables=_accounts_payable_columns(),
            sensitive_columns=frozenset(
                {
                    "invoices.currency",
                    "invoices.due_date",
                    "invoices.gross_amount",
                    "invoices.invoice_date",
                    "invoices.net_amount",
                    "invoices.normalized_invoice_number",
                    "invoices.no_po_exception_ref",
                    "invoices.posting_date",
                    "invoices.tax_amount",
                    "payments.currency",
                    "payments.payment_amount",
                    "payments.payment_date",
                    "purchase_orders.approved_amount",
                    "purchase_orders.currency",
                }
            ),
            query_templates=frozenset(
                {
                    "ap_invoice_population_v1",
                    "ap_duplicate_invoice_candidates_v1",
                    "ap_invoice_po_variance_v1",
                    "ap_payment_terms_v1",
                    "ap_payment_amount_v1",
                }
            ),
            functions=frozenset({"count", "max", "sum"}),
        )

    @property
    def schema_version(self) -> str:
        """Return the immutable registered schema version."""
        return self._schema_version

    @property
    def allowed_functions(self) -> frozenset[str]:
        """Return SQL functions approved for trusted templates."""
        return self._functions

    def is_table_allowed(self, table_name: str) -> bool:
        """Return whether a database object is registered."""
        return table_name in self._tables

    def is_column_allowed(self, table_name: str, column_name: str) -> bool:
        """Return whether a field belongs to a registered database object."""
        return column_name in self._tables.get(table_name, frozenset())

    def is_template_allowed(self, template_id: str) -> bool:
        """Return whether the frozen baseline permits a query template."""
        return template_id in self._query_templates

    def is_sensitive_column(self, table_name: str, column_name: str) -> bool:
        """Return whether a registered field requires restricted-output handling."""
        return f"{table_name}.{column_name}" in self._sensitive_columns

    def list_columns(self) -> tuple[str, ...]:
        """List fully qualified approved fields in deterministic order."""
        return tuple(
            sorted(
                f"{table_name}.{column_name}"
                for table_name, columns in self._tables.items()
                for column_name in columns
            )
        )

    def list_sensitive_columns(self) -> tuple[str, ...]:
        """List configured sensitive fields without exposing any field values."""
        return tuple(sorted(self._sensitive_columns))

    def get_schema(self) -> Mapping[str, frozenset[str]]:
        """Return the immutable table-to-column allowlist."""
        return self._tables

    def list_tables(self) -> tuple[str, ...]:
        """List approved tables in deterministic order."""
        return tuple(sorted(self._tables))

    def list_templates(self) -> tuple[str, ...]:
        """List approved query templates in deterministic order."""
        return tuple(sorted(self._query_templates))

    def access_profile_for_template(
        self, template_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return the complete physical table/field footprint of a frozen query template."""
        if template_id not in self._query_templates:
            return (), ()
        return access_profile_for_query_template(template_id)


__all__ = [
    "ACCOUNTS_PAYABLE_SCHEMA_VERSION",
    "DEFAULT_SENSITIVE_COLUMNS",
    "SCHEMA_VERSION",
    "SchemaRegistry",
]
