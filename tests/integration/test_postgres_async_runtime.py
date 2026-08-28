"""Real PostgreSQL Stage B simultaneous-acquire and fencing contract gate."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.orm import Session

from copilot.config import PROJECT_ROOT, get_settings
from copilot.contracts import TaskStatus
from copilot.contracts.async_runtime import (
    DispatchStatus,
    ExecutionLease,
    LeaseTimingPolicy,
    RuntimeStatus,
    TaskDispatch,
    TaskSubmissionResponse,
    WorkerIdentity,
)
from copilot.contracts.tasks import TaskRequest, TaskState
from copilot.persistence.async_runtime_repository import AsyncRuntimeRepository
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import WorkflowTaskRow
from tests.contract.async_runtime_repository_contract import (
    AsyncRuntimePostgresLeaseContract,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured"),
]


class SharedControlledDatabaseClock:
    """Thread-safe test clock injected at the repository's database-time seam."""

    def __init__(self, current: datetime) -> None:
        self._current = current
        self._lock = Lock()

    def __call__(self, _session: Session) -> datetime:
        with self._lock:
            return self._current

    def advance(self, current: datetime) -> None:
        with self._lock:
            self._current = current


@dataclass
class RuntimeHarness:
    repositories: tuple[AsyncRuntimeRepository, AsyncRuntimeRepository]
    dispatch: TaskDispatch
    workers: tuple[WorkerIdentity, WorkerIdentity]
    timing: LeaseTimingPolicy
    initial_time: datetime
    clock: SharedControlledDatabaseClock

    def advance_database_time(self, observed_at: datetime) -> None:
        self.clock.advance(observed_at)

    def attempt_fenced_probe_commit(self, lease: ExecutionLease) -> None:
        self.repositories[0].assert_fenced_probe_commit(lease)


class TestPostgresAsyncRuntimeLease(AsyncRuntimePostgresLeaseContract):
    """Collect the frozen Stage A lease contract unchanged against two real connections."""

    @pytest.fixture
    def runtime_harness(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[RuntimeHarness]:
        assert POSTGRES_URL is not None
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("PERSISTENCE_DATABASE_URL", POSTGRES_URL)
        get_settings.cache_clear()
        command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")

        databases = (PersistenceDatabase(POSTGRES_URL), PersistenceDatabase(POSTGRES_URL))
        initial_time = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
        clock = SharedControlledDatabaseClock(initial_time)
        repositories = (
            AsyncRuntimeRepository(databases[0], database_clock=clock),
            AsyncRuntimeRepository(databases[1], database_clock=clock),
        )
        suffix = uuid4().hex
        task_id = f"T-PG-RUNTIME-{suffix}"
        dispatch_id = f"D-PG-RUNTIME-{suffix}"
        request = TaskRequest(
            id=f"REQ-PG-RUNTIME-{suffix}",
            user_id="U-PG-RUNTIME",
            raw_input="Execute the PostgreSQL runtime concurrency contract.",
            created_at=initial_time,
        )
        state = TaskState(
            task_id=task_id,
            state=TaskStatus.CREATED,
            version=1,
            updated_at=initial_time,
            last_event_id=f"EVT-PG-RUNTIME-{suffix}",
        )
        dispatch = TaskDispatch(
            tenant_id="TENANT-PG-RUNTIME",
            task_id=task_id,
            trace_id=f"TRACE-PG-RUNTIME-{suffix}",
            dispatch_id=dispatch_id,
            execution_generation=1,
            expected_task_version=1,
            enqueued_at=initial_time,
            not_before=initial_time,
        )
        response = TaskSubmissionResponse(
            task_id=task_id,
            trace_id=dispatch.trace_id,
            task_status=TaskStatus.CREATED,
            runtime_status=RuntimeStatus.READY,
            accepted_at=initial_time,
            status_url=f"/v1/tasks/{task_id}",
            artifacts_url=f"/v1/tasks/{task_id}/artifacts",
        )
        repositories[0].persist_task_and_dispatch(
            request,
            state,
            dispatch,
            response,
            idempotency=None,
        )
        repositories[0].compare_and_set_status(
            dispatch_id,
            tenant_id=dispatch.tenant_id,
            expected=DispatchStatus.PENDING,
            replacement=DispatchStatus.ENQUEUED,
            observed_at=initial_time,
        )
        harness = RuntimeHarness(
            repositories=repositories,
            dispatch=dispatch,
            workers=(
                _worker("W-PG-RUNTIME-A", initial_time),
                _worker("W-PG-RUNTIME-B", initial_time),
            ),
            timing=LeaseTimingPolicy(
                heartbeat_interval_seconds=5,
                lease_ttl_seconds=20,
            ),
            initial_time=initial_time,
            clock=clock,
        )
        try:
            yield harness
        finally:
            with databases[0].session() as session:
                session.execute(
                    delete(WorkflowTaskRow).where(
                        WorkflowTaskRow.tenant_id == dispatch.tenant_id,
                        WorkflowTaskRow.task_id == dispatch.task_id,
                    )
                )
            for database in databases:
                database.dispose()
            get_settings.cache_clear()


def _worker(worker_id: str, started_at: datetime) -> WorkerIdentity:
    return WorkerIdentity(
        worker_id=worker_id,
        deployment_id="DEPLOYMENT-PG-RUNTIME",
        started_at=started_at,
    )
