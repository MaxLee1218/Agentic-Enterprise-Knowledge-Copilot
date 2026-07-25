"""Deny-by-default registry for the Supplier Quality ``quality.v1`` schema."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from sqlalchemy import Table

from copilot.tools.database.models import (
    CorrectiveAction,
    IncomingInspection,
    Supplier,
    SupplierDeviation,
)

SCHEMA_VERSION = "quality.v1"


def _registered_columns() -> dict[str, frozenset[str]]:
    tables = (
        cast(Table, Supplier.__table__),
        cast(Table, SupplierDeviation.__table__),
        cast(Table, IncomingInspection.__table__),
        cast(Table, CorrectiveAction.__table__),
    )
    return {table.name: frozenset(column.name for column in table.columns) for table in tables}


class SchemaRegistry:
    """Expose only approved tables, fields, functions, and query templates."""

    def __init__(
        self,
        *,
        schema_version: str = SCHEMA_VERSION,
        tables: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._schema_version = schema_version
        registered = dict(tables or _registered_columns())
        self._tables: Mapping[str, frozenset[str]] = MappingProxyType(registered)
        self._query_templates = frozenset(
            {"supplier_quality_summary_v1", "supplier_quality_trend_v1"}
        )
        self._functions = frozenset({"strftime", "sum"})

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

    def get_schema(self) -> Mapping[str, frozenset[str]]:
        """Return the immutable table-to-column allowlist."""
        return self._tables

    def list_tables(self) -> tuple[str, ...]:
        """List approved tables in deterministic order."""
        return tuple(sorted(self._tables))

    def list_templates(self) -> tuple[str, ...]:
        """List approved query templates in deterministic order."""
        return tuple(sorted(self._query_templates))


__all__ = ["SCHEMA_VERSION", "SchemaRegistry"]
