"""Schema and deterministic seed regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError

from copilot.tools.database import DatabaseConnection
from copilot.tools.database.errors import DatabaseConfigurationError
from copilot.tools.database.models import Base, IncomingInspection, Supplier
from copilot.tools.database.seed import seed_demo_database


def test_schema_creates_required_tables_and_relationship_foreign_keys(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'schema.db'}"
    connection = DatabaseConnection(database_url, read_only=False)
    try:
        Base.metadata.create_all(connection.engine)
        inspector = inspect(connection.engine)
        assert set(inspector.get_table_names()) == {
            "suppliers",
            "supplier_deviations",
            "incoming_inspections",
            "corrective_actions",
        }
        foreign_keys = inspector.get_foreign_keys("incoming_inspections")
        assert foreign_keys[0]["referred_table"] == "suppliers"
    finally:
        connection.dispose()


def test_connection_accepts_postgresql_without_exposing_credentials() -> None:
    connection = DatabaseConnection(
        "postgresql+psycopg://readonly:secret@127.0.0.1:5432/quality",
        read_only=True,
    )
    try:
        assert connection.database_name == "quality"
        assert connection.database_path is None
    finally:
        connection.dispose()


def test_connection_rejects_unapproved_database_backend() -> None:
    with pytest.raises(DatabaseConfigurationError, match="SQLite and PostgreSQL"):
        DatabaseConnection("mysql+pymysql://readonly:secret@127.0.0.1/quality")


def test_seed_is_deterministic_repeatable_and_contains_boundary_cases(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'seed.db'}"

    first = seed_demo_database(database_url)
    second = seed_demo_database(database_url)

    assert first == second
    assert second.supplier_count == 5
    assert second.deviation_count == 4
    assert second.inspection_count == 13
    assert second.corrective_action_count == 4

    connection = DatabaseConnection(database_url, read_only=True)
    try:
        with connection.session() as session:
            zero_reject = session.scalar(
                select(IncomingInspection).where(IncomingInspection.rejected_quantity == 0)
            )
            no_deviation_supplier = session.scalar(
                select(Supplier).where(Supplier.supplier_code == "SUP-003")
            )
            assert zero_reject is not None
            assert no_deviation_supplier is not None
            assert no_deviation_supplier.deviations == []
            assert no_deviation_supplier.corrective_actions == []
    finally:
        connection.dispose()


def test_read_only_connection_rejects_orm_writes(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'readonly.db'}"
    seed_demo_database(database_url)
    connection = DatabaseConnection(database_url, read_only=True)
    try:
        with pytest.raises(DBAPIError), connection.session() as session:
            session.add(
                Supplier(
                    tenant_id="TENANT-DEMO",
                    supplier_code="SUP-WRITE",
                    name="Denied Write",
                    country="CN",
                    category="Test",
                    risk_level="LOW",
                )
            )
            session.flush()
    finally:
        connection.dispose()
