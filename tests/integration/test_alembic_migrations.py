"""Alembic upgrade/current/downgrade integration coverage on isolated SQLite."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from copilot.config import PROJECT_ROOT, get_settings
from copilot.persistence.models import PersistenceBase

pytestmark = pytest.mark.integration


def test_fresh_database_upgrade_reaches_head_and_safe_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "alembic.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", database_url)
    get_settings.cache_clear()
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.upgrade(configuration, "head")

    engine = create_engine(database_url)
    try:
        assert set(PersistenceBase.metadata.tables).issubset(inspect(engine).get_table_names())
        inspector = inspect(engine)
        for table_name in PersistenceBase.metadata.tables:
            tenant = next(
                column
                for column in inspector.get_columns(table_name)
                if column["name"] == "tenant_id"
            )
            assert tenant["nullable"] is False
        assert "ix_workflow_evidence_tenant_task_sequence" in {
            item["name"] for item in inspector.get_indexes("workflow_evidence")
        }
        evidence_foreign_keys = inspector.get_foreign_keys("workflow_evidence")
        assert any(
            item["constrained_columns"] == ["tenant_id", "task_id"]
            and item["referred_table"] == "workflow_tasks"
            for item in evidence_foreign_keys
        )
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
            assert revision == "20260808_0002"

        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys = ON"))
            connection.execute(
                text(
                    "INSERT INTO workflow_tasks "
                    "(task_id, tenant_id, request_json, contract_json, plan_json, state_json) "
                    "VALUES ('T-FK', 'TENANT-A', '{}', NULL, NULL, '{}')"
                )
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO workflow_evidence "
                        "(tenant_id, evidence_id, task_id, fingerprint, payload_json) "
                        "VALUES ('TENANT-B', 'E-FK', 'T-FK', 'sha256:test', '{}')"
                    )
                )

        command.downgrade(configuration, "base")
        assert set(PersistenceBase.metadata.tables).isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_existing_stage17_rows_are_backfilled_and_unknown_ownership_is_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'existing-stage17.db'}"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", database_url)
    get_settings.cache_clear()
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(configuration, "20260807_0001")
    engine = create_engine(database_url)
    try:
        contract = '{"constraints":{"tenant_id":"TENANT-A"}}'
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workflow_tasks "
                    "(task_id, request_json, contract_json, plan_json, state_json) "
                    "VALUES ('T-KNOWN', '{}', :contract, NULL, '{}')"
                ),
                {"contract": contract},
            )
            connection.execute(
                text(
                    "INSERT INTO workflow_tasks "
                    "(task_id, request_json, contract_json, plan_json, state_json) "
                    "VALUES ('T-UNKNOWN', '{}', NULL, NULL, '{}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO workflow_state_events (event_id, task_id, payload_json) "
                    "VALUES ('EVT-KNOWN', 'T-KNOWN', '{}')"
                )
            )
        engine.dispose()

        command.upgrade(configuration, "head")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            task_tenants: dict[str, str] = {
                str(row.task_id): str(row.tenant_id)
                for row in connection.execute(
                    text("SELECT task_id, tenant_id FROM workflow_tasks ORDER BY task_id")
                )
            }
            child_tenant = connection.execute(
                text("SELECT tenant_id FROM workflow_state_events WHERE event_id = 'EVT-KNOWN'")
            ).scalar_one()
            revision = MigrationContext.configure(connection).get_current_revision()
        assert task_tenants == {
            "T-KNOWN": "TENANT-A",
            "T-UNKNOWN": "TENANT-LEGACY-UNSCOPED",
        }
        assert child_tenant == "TENANT-A"
        assert revision == "20260808_0002"
    finally:
        engine.dispose()
        get_settings.cache_clear()
