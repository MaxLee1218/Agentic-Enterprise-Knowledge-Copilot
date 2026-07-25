"""SQL validator safety tests for trusted template ASTs."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, func, literal_column, select

from copilot.tools.database.errors import DatabaseQueryValidationError
from copilot.tools.database.models import Supplier
from copilot.tools.database.query_templates import QueryTemplateRegistry
from copilot.tools.database.schema_registry import SchemaRegistry
from copilot.tools.database.sql_validator import SQLValidator


def test_approved_parameterized_select_is_validated() -> None:
    registry = SchemaRegistry()
    template = QueryTemplateRegistry(registry).build(
        "supplier_quality_summary_v1",
        filter_supplier_ids=True,
    )

    validated = SQLValidator(registry).validate(template.statement)

    assert validated.table_names == ("incoming_inspections", "suppliers")
    assert "suppliers.tenant_id" in validated.column_names


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM suppliers",
        "INSERT INTO suppliers (id) VALUES (1)",
        "UPDATE suppliers SET name = 'unsafe'",
        "DELETE FROM suppliers",
        "DROP TABLE suppliers",
        "ALTER TABLE suppliers ADD COLUMN unsafe TEXT",
        "SELECT id FROM suppliers; SELECT id FROM suppliers",
        "SELECT id FROM unauthorized_table",
        "SELECT id FROM suppliers -- bypass",
    ],
)
def test_all_raw_sql_is_rejected_even_when_it_looks_read_only(sql: str) -> None:
    with pytest.raises(DatabaseQueryValidationError, match="Raw SQL"):
        SQLValidator(SchemaRegistry()).validate(sql)


def test_unregistered_table_column_wildcard_and_function_are_rejected() -> None:
    validator = SQLValidator(SchemaRegistry())
    secret = Table("secrets", MetaData(), Column("id", Integer))

    with pytest.raises(DatabaseQueryValidationError, match="unregistered table"):
        validator.validate(select(secret.c.id).limit(1))
    with pytest.raises(DatabaseQueryValidationError, match="Unbound column"):
        validator.validate(
            select(Supplier.supplier_code, literal_column("password"))
            .select_from(Supplier)
            .limit(1)
        )
    with pytest.raises(DatabaseQueryValidationError, match="Wildcard"):
        validator.validate(select(literal_column("*")).select_from(Supplier).limit(1))
    with pytest.raises(DatabaseQueryValidationError, match="function"):
        validator.validate(select(func.random()).select_from(Supplier).limit(1))
