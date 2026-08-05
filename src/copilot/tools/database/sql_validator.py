"""AST-level validation for trusted SQLAlchemy SELECT templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ColumnClause, TextClause
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.schema import Column, Table

from copilot.tools.database.errors import DatabaseQueryValidationError
from copilot.tools.database.schema_registry import SchemaRegistry


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    """Validated statement plus deterministic lineage metadata."""

    statement: Select[Any]
    table_names: tuple[str, ...]
    column_names: tuple[str, ...]


class SQLValidator:
    """Reject anything except a single structured SELECT over registered fields."""

    def __init__(self, schema_registry: SchemaRegistry) -> None:
        self._schema_registry = schema_registry

    def validate(self, statement: Select[Any] | str) -> ValidatedQuery:
        """Validate a SQLAlchemy AST; raw SQL is always denied by the frozen contract."""
        if isinstance(statement, str):
            raise DatabaseQueryValidationError(
                "Raw SQL is not accepted; an approved query template is required"
            )
        if not isinstance(statement, Select):
            raise DatabaseQueryValidationError("Only a SQLAlchemy SELECT statement is allowed")

        table_names: set[str] = set()
        column_names: set[str] = set()
        for element in visitors.iterate(statement):
            if isinstance(element, TextClause):
                raise DatabaseQueryValidationError("Textual SQL fragments are not allowed")
            if isinstance(element, Table):
                self._validate_table(element.name)
                table_names.add(element.name)
            if isinstance(element, Column):
                physical_columns = {
                    (base.table.name, base.name)
                    for base in element.base_columns
                    if isinstance(base, Column) and isinstance(base.table, Table)
                }
                if not physical_columns:
                    raise DatabaseQueryValidationError(
                        "Column alias has no registered physical source"
                    )
                for table_name, column_name in physical_columns:
                    self._validate_table(table_name)
                    self._validate_column(table_name, column_name)
                    table_names.add(table_name)
                    column_names.add(f"{table_name}.{column_name}")
            elif isinstance(element, ColumnClause):
                if element.name == "*":
                    raise DatabaseQueryValidationError("Wildcard column access is not allowed")
                raise DatabaseQueryValidationError("Unbound column expressions are not allowed")
            if isinstance(element, FunctionElement):
                function_name = element.name.casefold() if element.name else ""
                if function_name not in self._schema_registry.allowed_functions:
                    raise DatabaseQueryValidationError("SQL function is not approved")

        if not table_names:
            raise DatabaseQueryValidationError("SELECT must reference a registered table")
        if not column_names:
            raise DatabaseQueryValidationError("SELECT must reference registered columns")
        if statement._limit_clause is None:
            raise DatabaseQueryValidationError("SELECT must enforce a row limit")
        return ValidatedQuery(
            statement=statement,
            table_names=tuple(sorted(table_names)),
            column_names=tuple(sorted(column_names)),
        )

    def _validate_table(self, table_name: str) -> None:
        if not self._schema_registry.is_table_allowed(table_name):
            raise DatabaseQueryValidationError("Query references an unregistered table")

    def _validate_column(self, table_name: str, column_name: str) -> None:
        if not self._schema_registry.is_column_allowed(table_name, column_name):
            raise DatabaseQueryValidationError("Query references an unregistered column")


__all__ = ["SQLValidator", "ValidatedQuery"]
