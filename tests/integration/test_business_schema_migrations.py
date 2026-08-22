"""Isolated business-schema migration upgrade and rollback coverage."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from copilot.tools.database.ap_seed import seed_accounts_payable_demo_database
from copilot.tools.database.migrations import (
    BUSINESS_SCHEMA_BASELINE_REVISION,
    BUSINESS_SCHEMA_HEAD_REVISION,
    downgrade_business_schema,
    upgrade_business_schema,
)
from copilot.tools.database.seed import seed_demo_database


def _database_url(tmp_path: Path, name: str = "business-migrations.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def test_sqlite_upgrade_downgrade_preserves_quality_tables_and_rows(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)
    quality_report = seed_demo_database(database_url)
    seed_accounts_payable_demo_database(database_url)
    engine = create_engine(database_url)
    try:
        downgrade_business_schema(database_url, BUSINESS_SCHEMA_BASELINE_REVISION)
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "business_schema_version",
            "suppliers",
            "supplier_deviations",
            "incoming_inspections",
            "corrective_actions",
        }
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM business_schema_version")
                ).scalar_one()
                == BUSINESS_SCHEMA_BASELINE_REVISION
            )
            assert connection.execute(text("SELECT count(*) FROM suppliers")).scalar_one() == 17
            assert (
                connection.execute(text("SELECT count(*) FROM incoming_inspections")).scalar_one()
                == quality_report.inspection_count
            )

        upgrade_business_schema(database_url)
        assert {
            "legal_entities",
            "business_units",
            "purchase_orders",
            "invoices",
            "payments",
        }.issubset(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM business_schema_version")
                ).scalar_one()
                == BUSINESS_SCHEMA_HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_business_migration_adopts_reviewed_quality_baseline(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path, "baseline-adoption.db")
    upgrade_business_schema(database_url, BUSINESS_SCHEMA_BASELINE_REVISION)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO suppliers (
                        id, tenant_id, supplier_code, name, country,
                        category, risk_level, created_at
                    ) VALUES (
                        1, 'TENANT-DEMO', 'SUP-LEGACY', 'Legacy Supplier', 'CN',
                        'Components', 'LOW', '2026-01-01T00:00:00+00:00'
                    )
                    """
                )
            )

        upgrade_business_schema(database_url)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT name FROM suppliers WHERE id = 1")).scalar_one()
                == "Legacy Supplier"
            )
        supplier_uniques = {
            tuple(item["column_names"])
            for item in inspect(engine).get_unique_constraints("suppliers")
        }
        assert ("tenant_id", "id") in supplier_uniques
    finally:
        engine.dispose()
