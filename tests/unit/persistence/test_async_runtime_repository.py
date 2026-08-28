"""Deterministic Stage B repository tests using isolated SQLAlchemy persistence."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from copilot.contracts import ApprovalRequest, ApprovalStatus, JsonObject, TaskStatus
from copilot.contracts.async_runtime import (
    CancellationRequest,
    DispatchStatus,
    LeaseAcquisitionStatus,
    LeaseTimingPolicy,
    RuntimeAttemptStatus,
    RuntimeStatus,
    SubmissionIdempotency,
    TaskDispatch,
    TaskSubmissionResponse,
    WorkerIdentity,
)
from copilot.contracts.errors import (
    DispatchConflictError,
    LeaseExpiredError,
    StaleFencingTokenError,
)
from copilot.contracts.tasks import TaskRequest, TaskState
from copilot.persistence.approval_repository import ApprovalRepository
from copilot.persistence.async_runtime_repository import AsyncRuntimeRepository
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import (
    TaskDispatchRow,
    TaskSubmissionIdempotencyRow,
    WorkflowTaskRow,
    WorkflowTaskRuntimeRow,
)


class ControlledDatabaseClock:
    """Shared test-only database-clock substitute; production queries ``func.now()``."""

    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self, _session: object) -> datetime:
        return self.current


@pytest.fixture
def runtime_repository(
    tmp_path: Path,
) -> Iterator[tuple[AsyncRuntimeRepository, PersistenceDatabase, object]]:
    database = PersistenceDatabase(f"sqlite:///{tmp_path / 'runtime.db'}")
    database.create_schema_for_tests()
    clock = ControlledDatabaseClock(datetime(2026, 8, 26, 8, 0, tzinfo=UTC))
    repository = AsyncRuntimeRepository(database, database_clock=clock)
    try:
        yield repository, database, clock
    finally:
        database.dispose()


def test_submission_is_atomic_and_idempotency_is_fingerprint_bound(
    runtime_repository: tuple[AsyncRuntimeRepository, PersistenceDatabase, object],
) -> None:
    repository, database, _clock = runtime_repository
    request, state, dispatch, response = _submission()
    idempotency = SubmissionIdempotency(
        tenant_id=dispatch.tenant_id,
        caller_id=request.user_id,
        idempotency_key="KEY-1",
        request_fingerprint="a" * 64,
    )

    accepted, reused = repository.persist_task_and_dispatch(
        request,
        state,
        dispatch,
        response,
        idempotency=idempotency,
    )
    duplicate, duplicate_reused = repository.persist_task_and_dispatch(
        request,
        state,
        dispatch,
        response,
        idempotency=idempotency,
    )

    assert accepted == response
    assert reused is False
    assert duplicate == response
    assert duplicate_reused is True
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(WorkflowTaskRow)) == 1
        assert session.scalar(select(func.count()).select_from(TaskDispatchRow)) == 1
        assert session.scalar(select(func.count()).select_from(TaskSubmissionIdempotencyRow)) == 1
    with pytest.raises(DispatchConflictError, match="different request fingerprint"):
        repository.persist_task_and_dispatch(
            request,
            state,
            dispatch,
            response,
            idempotency=idempotency.model_copy(update={"request_fingerprint": "b" * 64}),
        )


def test_takeover_uses_expiry_boundary_and_monotonic_fencing(
    runtime_repository: tuple[AsyncRuntimeRepository, PersistenceDatabase, object],
) -> None:
    repository, database, raw_clock = runtime_repository
    assert isinstance(raw_clock, ControlledDatabaseClock)
    _request, _state, dispatch, _response = _persist_enqueued(repository)
    timing = LeaseTimingPolicy(heartbeat_interval_seconds=5, lease_ttl_seconds=20)
    first_worker = _worker("W-1")
    second_worker = _worker("W-2")

    first = repository.try_acquire_lease(dispatch, first_worker, timing=timing)
    conflict = repository.try_acquire_lease(dispatch, second_worker, timing=timing)
    assert first.status is LeaseAcquisitionStatus.ACQUIRED
    assert first.lease is not None
    assert conflict.status is LeaseAcquisitionStatus.CONFLICT

    raw_clock.current = first.lease.expires_at
    takeover = repository.try_acquire_lease(dispatch, second_worker, timing=timing)
    assert takeover.status is LeaseAcquisitionStatus.ACQUIRED
    assert takeover.lease is not None
    assert takeover.lease.fencing_token > first.lease.fencing_token
    assert takeover.lease.execution_generation == first.lease.execution_generation

    with pytest.raises(StaleFencingTokenError):
        repository.assert_fenced_probe_commit(first.lease)
    with pytest.raises(StaleFencingTokenError):
        repository.heartbeat(first.lease, timing=timing)
    assert repository.release(first.lease) is False
    assert repository.release(takeover.lease) is True
    with database.session() as session:
        runtime = session.scalar(select(WorkflowTaskRuntimeRow))
        assert runtime is not None
        assert runtime.fencing_counter == takeover.lease.fencing_token
        assert runtime.runtime_status == RuntimeStatus.READY.value


def test_cancellation_is_terminal_durable_and_idempotent(
    runtime_repository: tuple[AsyncRuntimeRepository, PersistenceDatabase, object],
) -> None:
    repository, database, raw_clock = runtime_repository
    assert isinstance(raw_clock, ControlledDatabaseClock)
    _request, _state, dispatch, _response = _persist_enqueued(repository)
    approval_repository = ApprovalRepository(database, initialize_schema=False)
    approval_repository.create(
        _pending_approval(dispatch.task_id, dispatch.tenant_id, raw_clock.current),
        tenant_id=dispatch.tenant_id,
    )
    cancellation = CancellationRequest(
        tenant_id=dispatch.tenant_id,
        task_id=dispatch.task_id,
        request_id="CANCEL-1",
        requested_by="U-1",
        requested_at=raw_clock.current,
        reason_code="USER_REQUEST",
    )

    first = repository.request_cancellation(cancellation)
    duplicate = repository.request_cancellation(cancellation)
    snapshot = repository.snapshot(dispatch.task_id, tenant_id=dispatch.tenant_id)
    acquire = repository.try_acquire_lease(
        dispatch,
        _worker("W-1"),
        timing=LeaseTimingPolicy(),
    )

    assert duplicate == first
    assert snapshot.task_status is TaskStatus.CANCELLED
    assert snapshot.runtime_status is RuntimeStatus.FINISHED
    assert snapshot.cancellation == first
    assert snapshot.lease is None
    assert acquire.status is LeaseAcquisitionStatus.CANCELLED
    assert (
        approval_repository.get("AP-1", tenant_id=dispatch.tenant_id).status
        is ApprovalStatus.REVOKED
    )


def test_heartbeat_renews_from_controlled_database_time_and_expires_at_boundary(
    runtime_repository: tuple[AsyncRuntimeRepository, PersistenceDatabase, object],
) -> None:
    repository, _database, raw_clock = runtime_repository
    assert isinstance(raw_clock, ControlledDatabaseClock)
    _request, _state, dispatch, _response = _persist_enqueued(repository)
    timing = LeaseTimingPolicy(heartbeat_interval_seconds=5, lease_ttl_seconds=20)
    acquired = repository.try_acquire_lease(dispatch, _worker("W-1"), timing=timing)
    assert acquired.lease is not None
    original = acquired.lease

    raw_clock.current += timedelta(seconds=5)
    renewed = repository.heartbeat(original, timing=timing)
    assert renewed.heartbeat_at == raw_clock.current
    assert renewed.expires_at == raw_clock.current + timedelta(seconds=20)
    assert renewed.fencing_token == original.fencing_token

    raw_clock.current = renewed.expires_at
    with pytest.raises(LeaseExpiredError):
        repository.heartbeat(renewed, timing=timing)


def test_runtime_attempt_accounting_is_separate_from_business_retries(
    runtime_repository: tuple[AsyncRuntimeRepository, PersistenceDatabase, object],
) -> None:
    repository, _database, _clock = runtime_repository
    _request, _state, dispatch, _response = _persist_enqueued(repository)
    acquired = repository.try_acquire_lease(
        dispatch,
        _worker("W-1"),
        timing=LeaseTimingPolicy(),
    )
    assert acquired.lease is not None

    running = repository.start_runtime_attempt(acquired.lease)
    finished = repository.finish_runtime_attempt(
        running,
        status=RuntimeAttemptStatus.SUCCEEDED,
    )

    assert running.status is RuntimeAttemptStatus.RUNNING
    assert finished.status is RuntimeAttemptStatus.SUCCEEDED
    assert finished.completed_at is not None


def _persist_enqueued(
    repository: AsyncRuntimeRepository,
) -> tuple[TaskRequest, TaskState, TaskDispatch, TaskSubmissionResponse]:
    request, state, dispatch, response = _submission()
    repository.persist_task_and_dispatch(
        request,
        state,
        dispatch,
        response,
        idempotency=None,
    )
    repository.compare_and_set_status(
        dispatch.dispatch_id,
        tenant_id=dispatch.tenant_id,
        expected=DispatchStatus.PENDING,
        replacement=DispatchStatus.ENQUEUED,
        observed_at=response.accepted_at,
    )
    return request, state, dispatch, response


def _submission() -> tuple[TaskRequest, TaskState, TaskDispatch, TaskSubmissionResponse]:
    accepted_at = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
    task_id = "T-1"
    request = TaskRequest(
        id="REQ-1",
        user_id="U-1",
        raw_input="Analyze the governed supplier data.",
        created_at=accepted_at,
    )
    state = TaskState(
        task_id=task_id,
        state=TaskStatus.CREATED,
        version=1,
        updated_at=accepted_at,
        last_event_id="EVT-1",
    )
    dispatch = TaskDispatch(
        tenant_id="TENANT-1",
        task_id=task_id,
        trace_id="TRACE-1",
        dispatch_id="D-1",
        execution_generation=1,
        expected_task_version=1,
        enqueued_at=accepted_at,
        not_before=accepted_at,
    )
    response = TaskSubmissionResponse(
        task_id=task_id,
        trace_id=dispatch.trace_id,
        task_status=TaskStatus.CREATED,
        runtime_status=RuntimeStatus.READY,
        accepted_at=accepted_at,
        status_url=f"/v1/tasks/{task_id}",
        artifacts_url=f"/v1/tasks/{task_id}/artifacts",
    )
    return request, state, dispatch, response


def _worker(worker_id: str) -> WorkerIdentity:
    return WorkerIdentity(
        worker_id=worker_id,
        deployment_id="DEPLOYMENT-1",
        started_at=datetime(2026, 8, 26, 7, 0, tzinfo=UTC) - timedelta(minutes=1),
    )


def _pending_approval(task_id: str, tenant_id: str, created_at: datetime) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id="AP-1",
        task_id=task_id,
        tenant_id=tenant_id,
        step_id="STEP-1",
        planning_version=1,
        tool_name="database_query",
        tool_version="1.0.0",
        input_schema_fingerprint="schema-fingerprint",
        original_action_fingerprint="action-fingerprint",
        controlled_scope=("quality.v1",),
        proposed_arguments=JsonObject({"row_limit": 100}),
        reason="Controlled database access",
        requester="U-1",
        required_role="quality_data_approver",
        status=ApprovalStatus.PENDING,
        policy_version="quality-policy.v1",
        created_at=created_at,
        expires_at=created_at + timedelta(hours=1),
    )
