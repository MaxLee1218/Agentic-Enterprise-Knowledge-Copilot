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
            assert revision == "20260831_0007"
        step_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("workflow_step_results")
        }
        assert ("tenant_id", "task_id", "step_id") in step_uniques
        assert ("step_id",) not in step_uniques
        mcp_tables = {"mcp_connections", "mcp_sessions", "mcp_invocations"}
        assert mcp_tables.issubset(inspect(engine).get_table_names())
        runtime_tables = {
            "task_dispatches",
            "task_runtime_attempts",
            "task_submission_idempotency",
            "workflow_task_runtime",
        }
        assert runtime_tables.issubset(inspect(engine).get_table_names())
        clarification_tables = {
            "workflow_clarifications",
            "workflow_clarification_history",
        }
        assert clarification_tables.issubset(inspect(engine).get_table_names())
        clarification_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("workflow_clarifications")
        }
        assert ("tenant_id", "task_id", "round") in clarification_uniques
        assert ("tenant_id", "active_task_id") in clarification_uniques
        lease_columns = {item["name"] for item in inspector.get_columns("workflow_leases")}
        assert {
            "dispatch_id",
            "execution_generation",
            "task_version",
            "worker_id",
            "lease_id",
            "fencing_token",
            "acquired_at",
            "heartbeat_at",
            "expires_at",
        }.issubset(lease_columns)
        assert "owner_id" not in lease_columns
        assert any(
            item["constrained_columns"] == ["tenant_id", "task_id", "dispatch_id"]
            and item["referred_table"] == "task_dispatches"
            for item in inspector.get_foreign_keys("workflow_leases")
        )

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
        assert revision == "20260831_0007"
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_existing_step_results_upgrade_without_data_loss_and_allow_task_local_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'step-result-upgrade.db'}"
    _configure_migration_environment(monkeypatch, database_url)
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(configuration, "20260809_0003")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_task(connection, "TENANT-A", "T-A")
            _insert_task(connection, "TENANT-A", "T-B")
            _insert_step_result(connection, "TENANT-A", "T-A", "step-1-knowledge-search", "A")

        command.upgrade(configuration, "head")
        command.upgrade(configuration, "head")

        with engine.begin() as connection:
            _insert_step_result(connection, "TENANT-A", "T-B", "step-1-knowledge-search", "B")
            rows = connection.execute(
                text(
                    "SELECT tenant_id, task_id, step_id, result_json "
                    "FROM workflow_step_results ORDER BY task_id"
                )
            ).all()
            assert [tuple(row) for row in rows] == [
                ("TENANT-A", "T-A", "step-1-knowledge-search", "A"),
                ("TENANT-A", "T-B", "step-1-knowledge-search", "B"),
            ]
            with pytest.raises(IntegrityError):
                _insert_step_result(
                    connection,
                    "TENANT-A",
                    "T-A",
                    "step-1-knowledge-search",
                    "duplicate",
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_step_result_downgrade_refuses_cross_task_step_id_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'unsafe-step-result-downgrade.db'}"
    _configure_migration_environment(monkeypatch, database_url)
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(configuration, "head")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_task(connection, "TENANT-A", "T-A")
            _insert_task(connection, "TENANT-A", "T-B")
            _insert_step_result(connection, "TENANT-A", "T-A", "step-1", "A")
            _insert_step_result(connection, "TENANT-A", "T-B", "step-1", "B")

        with pytest.raises(RuntimeError, match="step_id is reused across tasks"):
            command.downgrade(configuration, "20260809_0003")

        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "20260812_0004"
            assert (
                connection.execute(text("SELECT count(*) FROM workflow_step_results")).scalar_one()
                == 2
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_async_runtime_migration_backfills_and_restores_legacy_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy-lease-upgrade.db'}"
    _configure_migration_environment(monkeypatch, database_url)
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(configuration, "20260812_0004")
    engine = create_engine(database_url)
    state_json = (
        '{"task_id":"T-LEASE","state":"EXECUTING","version":4,'
        '"updated_at":"2026-08-26T08:00:00Z","last_event_id":"EVT-LEASE"}'
    )
    request_json = (
        '{"id":"REQ-LEASE","user_id":"U-LEASE","raw_input":"resume",'
        '"created_at":"2026-08-26T08:00:00Z","metadata":{}}'
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workflow_tasks "
                    "(task_id, tenant_id, request_json, contract_json, plan_json, state_json) "
                    "VALUES ('T-LEASE', 'TENANT-LEASE', :request_json, NULL, NULL, :state_json)"
                ),
                {"request_json": request_json, "state_json": state_json},
            )
            connection.execute(
                text(
                    "INSERT INTO workflow_leases (tenant_id, task_id, owner_id, expires_at) "
                    "VALUES ('TENANT-LEASE', 'T-LEASE', 'W-LEGACY', "
                    "'2026-08-26 08:10:00+00:00')"
                )
            )
        engine.dispose()

        command.upgrade(configuration, "head")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            lease = connection.execute(
                text(
                    "SELECT worker_id, task_version, execution_generation, fencing_token "
                    "FROM workflow_leases WHERE task_id = 'T-LEASE'"
                )
            ).one()
            runtime = connection.execute(
                text(
                    "SELECT runtime_status, current_dispatch_id, fencing_counter "
                    "FROM workflow_task_runtime WHERE task_id = 'T-LEASE'"
                )
            ).one()
            dispatch_count = connection.execute(
                text("SELECT count(*) FROM task_dispatches WHERE task_id = 'T-LEASE'")
            ).scalar_one()
        assert tuple(lease) == ("W-LEGACY", 4, 1, 1)
        assert tuple(runtime)[0] == "LEASED"
        assert tuple(runtime)[1] is not None
        assert tuple(runtime)[2] == 1
        assert dispatch_count == 1

        command.downgrade(configuration, "20260812_0004")
        engine.dispose()
        engine = create_engine(database_url)
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT tenant_id, task_id, owner_id FROM workflow_leases "
                    "WHERE task_id = 'T-LEASE'"
                )
            ).one()
            revision = MigrationContext.configure(connection).get_current_revision()
        assert tuple(restored) == ("TENANT-LEASE", "T-LEASE", "W-LEGACY")
        assert revision == "20260812_0004"
    finally:
        engine.dispose()
        get_settings.cache_clear()


def _configure_migration_environment(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", database_url)
    get_settings.cache_clear()


def _insert_task(connection: object, tenant_id: str, task_id: str) -> None:
    from sqlalchemy.engine import Connection

    assert isinstance(connection, Connection)
    connection.execute(
        text(
            "INSERT INTO workflow_tasks "
            "(task_id, tenant_id, request_json, contract_json, plan_json, state_json) "
            "VALUES (:task_id, :tenant_id, '{}', NULL, NULL, '{}')"
        ),
        {"task_id": task_id, "tenant_id": tenant_id},
    )


def _insert_step_result(
    connection: object,
    tenant_id: str,
    task_id: str,
    step_id: str,
    payload: str,
) -> None:
    from sqlalchemy.engine import Connection

    assert isinstance(connection, Connection)
    connection.execute(
        text(
            "INSERT INTO workflow_step_results "
            "(tenant_id, task_id, step_id, result_json, execution_json) "
            "VALUES (:tenant_id, :task_id, :step_id, :payload, '{}')"
        ),
        {
            "tenant_id": tenant_id,
            "task_id": task_id,
            "step_id": step_id,
            "payload": payload,
        },
    )
