"""P0 hard-kill recovery gate using real Worker processes and PostgreSQL checkpoints."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, text

from copilot.api.app import create_app
from copilot.bootstrap.container import build_application
from copilot.config import PROJECT_ROOT, Settings, get_settings
from copilot.persistence.checkpoint import migrate_postgres_checkpoints
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import (
    WorkflowArtifactRow,
    WorkflowLeaseRow,
    WorkflowStepResultRow,
    WorkflowTaskRow,
)
from copilot.security.identity import DemoIdentityProvider
from copilot.tools.database.seed import seed_demo_database

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured"),
]


def test_hard_killed_worker_recovers_checkpoint_without_duplicate_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", POSTGRES_URL)
    get_settings.cache_clear()
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    migrate_postgres_checkpoints(POSTGRES_URL.replace("+psycopg", ""))
    seed_demo_database(POSTGRES_URL, reset=True)

    first: subprocess.Popen[bytes] | None = None
    second: subprocess.Popen[bytes] | None = None
    task_id: str | None = None
    settings = _settings(tmp_path)
    environment = _worker_environment(settings)
    container = build_application(settings)
    client = TestClient(
        create_app(
            task_service=container.task_service,
            task_submission_service=container.task_submission_service,
            approval_service=container.approval_service,
            artifact_service=container.artifact_service,
            settings=settings,
            identity_provider=DemoIdentityProvider(settings),
        )
    )
    lock_engine = create_engine(POSTGRES_URL)
    lock_connection = lock_engine.connect()
    lock_transaction = lock_connection.begin()
    try:
        with client:
            submitted = client.post(
                "/v1/tasks",
                json={
                    "task": (
                        "Analyze SUP-005 supplier quality for Q3 2026 and generate a JSON "
                        "management report."
                    ),
                    "output_format": "json",
                },
            )
            assert submitted.status_code == 202
            task_id = str(submitted.json()["task_id"])
            lock_connection.execute(
                text("LOCK TABLE incoming_inspections IN ACCESS EXCLUSIVE MODE")
            )
            first = _start_worker(environment)
            _wait_for_completed_steps(
                container.persistence_database,
                task_id=task_id,
                minimum=1,
                timeout_seconds=15,
            )
            assert first.poll() is None
            assert _active_lease_count(container.persistence_database, task_id=task_id) == 1

            first.kill()
            first.wait(timeout=5)
            lock_transaction.rollback()
            lock_connection.close()
            lock_engine.dispose()

            second = _start_worker(environment)
            completed = _wait_for_terminal(client, task_id=task_id, timeout_seconds=20)
            assert completed["status"] == "COMPLETED"
            assert completed["runtime_status"] == "FINISHED"
            assert completed["artifact_count"] == 1

            database = container.persistence_database
            assert database is not None
            assert container.async_runtime_repository is not None
            snapshot = container.async_runtime_repository.snapshot(
                task_id,
                tenant_id="TENANT-DEMO",
            )
            assert snapshot.recovery_attempt_count == 1
            with database.session() as session:
                step_count = session.scalar(
                    select(func.count())
                    .select_from(WorkflowStepResultRow)
                    .where(
                        WorkflowStepResultRow.tenant_id == "TENANT-DEMO",
                        WorkflowStepResultRow.task_id == task_id,
                    )
                )
                artifact_count = session.scalar(
                    select(func.count())
                    .select_from(WorkflowArtifactRow)
                    .where(
                        WorkflowArtifactRow.tenant_id == "TENANT-DEMO",
                        WorkflowArtifactRow.task_id == task_id,
                    )
                )
            assert step_count == 4
            assert artifact_count == 1
    finally:
        if lock_transaction.is_active:
            lock_transaction.rollback()
        if not lock_connection.closed:
            lock_connection.close()
        lock_engine.dispose()
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        client.close()
        container.close()
        if task_id is not None:
            cleanup = PersistenceDatabase(POSTGRES_URL)
            with cleanup.session() as session:
                session.execute(delete(WorkflowTaskRow).where(WorkflowTaskRow.task_id == task_id))
            cleanup.dispose()
        get_settings.cache_clear()


def _settings(tmp_path: Path) -> Settings:
    assert POSTGRES_URL is not None
    return Settings(
        app_env="test",
        database_url=POSTGRES_URL,
        database_provider="sqlalchemy",
        persistence_database_url=POSTGRES_URL,
        persistence_auto_create_schema=False,
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=True,
        knowledge_provider="mock",
        llm_provider="mock",
        demo_tenant_id="TENANT-DEMO",
        demo_user_id="U-WORKER-KILL",
        worker_concurrency=1,
        worker_poll_interval_seconds=0.05,
        worker_shutdown_grace_seconds=5,
        execution_heartbeat_interval_seconds=1,
        execution_lease_ttl_seconds=5,
        task_queue_visibility_timeout_seconds=60,
        log_level="WARNING",
        log_format="text",
    )


def _worker_environment(settings: Settings) -> dict[str, str]:
    assert POSTGRES_URL is not None
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": POSTGRES_URL,
            "DATABASE_PROVIDER": "sqlalchemy",
            "PERSISTENCE_DATABASE_URL": POSTGRES_URL,
            "PERSISTENCE_AUTO_CREATE_SCHEMA": "false",
            "ARTIFACT_DIR": str(settings.artifact_dir),
            "CHECKPOINT_ENABLED": "true",
            "KNOWLEDGE_PROVIDER": "mock",
            "LLM_PROVIDER": "mock",
            "DEMO_TENANT_ID": "TENANT-DEMO",
            "DEMO_USER_ID": "U-WORKER-KILL",
            "WORKER_CONCURRENCY": "1",
            "WORKER_POLL_INTERVAL_SECONDS": "0.05",
            "WORKER_SHUTDOWN_GRACE_SECONDS": "5",
            "EXECUTION_HEARTBEAT_INTERVAL_SECONDS": "1",
            "EXECUTION_LEASE_TTL_SECONDS": "5",
            "TASK_QUEUE_VISIBILITY_TIMEOUT_SECONDS": "60",
            "LOG_LEVEL": "WARNING",
            "LOG_FORMAT": "text",
        }
    )
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src") + (
        f"{os.pathsep}{existing_python_path}" if existing_python_path else ""
    )
    return environment


def _start_worker(environment: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        _worker_command(),
        cwd=PROJECT_ROOT,
        env=environment,
    )


def _worker_command() -> tuple[str, str, str]:
    """Use the active test interpreter in virtualenv, CI, and system installs alike."""
    return (sys.executable, "-m", "copilot.worker")


def _wait_for_completed_steps(
    database: PersistenceDatabase | None,
    *,
    task_id: str,
    minimum: int,
    timeout_seconds: float,
) -> None:
    assert database is not None
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        with database.session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(WorkflowStepResultRow)
                .where(
                    WorkflowStepResultRow.tenant_id == "TENANT-DEMO",
                    WorkflowStepResultRow.task_id == task_id,
                )
            )
        if int(count or 0) >= minimum:
            return
        sleep(0.05)
    raise AssertionError("Worker did not persist a successful pre-crash step")


def _active_lease_count(database: PersistenceDatabase | None, *, task_id: str) -> int:
    assert database is not None
    with database.session() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(WorkflowLeaseRow)
                .where(
                    WorkflowLeaseRow.tenant_id == "TENANT-DEMO",
                    WorkflowLeaseRow.task_id == task_id,
                )
            )
            or 0
        )


def _wait_for_terminal(
    client: TestClient,
    *,
    task_id: str,
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = client.get(f"/v1/tasks/{task_id}")
        assert response.status_code == 200, response.text
        payload = cast(dict[str, object], response.json())
        if payload["status"] in {"COMPLETED", "FAILED", "CANCELLED"} and (
            payload["runtime_status"] == "FINISHED"
        ):
            return payload
        sleep(0.05)
    raise AssertionError("Replacement Worker did not recover the hard-killed Task")
