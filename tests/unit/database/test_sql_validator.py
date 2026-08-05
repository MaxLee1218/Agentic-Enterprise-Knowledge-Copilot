"""SQL validator safety tests for trusted template ASTs."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, func, literal_column, select
from sqlalchemy.orm import aliased

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


def test_alias_cte_subquery_and_join_cannot_hide_an_unregistered_table() -> None:
    validator = SQLValidator(SchemaRegistry())
    secret = Table("payroll", MetaData(), Column("salary", Integer))
    secret_alias = secret.alias("suppliers")
    secret_cte = select(secret.c.salary).cte("supplier_totals")
    secret_subquery = select(secret.c.salary).subquery("supplier_summary")

    statements = (
        select(secret_alias.c.salary).limit(1),
        select(secret_cte.c.salary).limit(1),
        select(secret_subquery.c.salary).limit(1),
        select(Supplier.supplier_code, secret.c.salary)
        .select_from(Supplier.__table__.join(secret, Supplier.id == secret.c.salary))
        .limit(1),
    )

    for statement in statements:
        with pytest.raises(DatabaseQueryValidationError):
            validator.validate(statement)


def test_registered_table_and_field_aliases_do_not_change_physical_lineage() -> None:
    validator = SQLValidator(SchemaRegistry())
    supplier_alias = aliased(Supplier, name="quality_supplier")
    statement = select(supplier_alias.supplier_code.label("public_name")).limit(1)

    validated = validator.validate(statement)

    assert validated.table_names == ("suppliers",)
    assert validated.column_names == ("suppliers.supplier_code",)


@pytest.mark.parametrize(
    "sql",
    [
        'SeLeCt "supplier_code" FrOm "suppliers"',
        "WITH safe AS (SELECT salary FROM payroll) SELECT salary FROM safe",
        "SELECT supplier_code FROM suppliers UNION SELECT token FROM secrets",
        "SELECT SUM((SELECT salary FROM payroll)) FROM suppliers",
    ],
)
def test_case_quotes_union_and_nested_raw_sql_never_reach_ast_validation(sql: str) -> None:
    with pytest.raises(DatabaseQueryValidationError, match="Raw SQL"):
        SQLValidator(SchemaRegistry()).validate(sql)
