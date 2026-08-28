"""Real PostgreSQL Queue v1, dispatcher, recovery, and fencing integration gates."""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text

from copilot.config import PROJECT_ROOT, get_settings
from copilot.contracts import TaskStatus
from copilot.contracts.async_runtime import (
    DispatchStatus,
    LeaseTimingPolicy,
    RuntimeStatus,
    TaskDispatch,
    TaskSubmissionResponse,
    WorkerIdentity,
)
from copilot.contracts.errors import (
    DispatchConflictError,
    RuntimeCapacityError,
    StaleFencingTokenError,
)
from copilot.contracts.tasks import TaskRequest, TaskState
from copilot.persistence.async_runtime_repository import AsyncRuntimeRepository
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import (
    TaskDispatchRow,
    WorkflowTaskRow,
    WorkflowTaskRuntimeRow,
)
from copilot.persistence.postgres_queue import PostgresOutboxDispatcher, PostgresTaskQueue
from copilot.persistence.postgres_recovery import PostgresRecoveryScanner
from copilot.persistence.task_repository import WorkflowRepository
from copilot.services.execution_authority import bind_execution_authority
from copilot.services.workflows.models import TaskStateEvent

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured"),
]


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[PersistenceDatabase]:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", POSTGRES_URL)
    get_settings.cache_clear()
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    value = PersistenceDatabase(POSTGRES_URL)
    try:
        yield value
    finally:
        with value.session() as session:
            session.execute(
                delete(WorkflowTaskRow).where(WorkflowTaskRow.tenant_id.like("TENANT-PG-QUEUE-%"))
            )
        value.dispose()
        get_settings.cache_clear()


def test_publish_receive_nack_ack_redelivery_and_consumer_restart(
    database: PersistenceDatabase,
) -> None:
    repository = AsyncRuntimeRepository(database)
    queue_a = PostgresTaskQueue(database)
    dispatcher = PostgresOutboxDispatcher(database, queue_a)
    dispatch = _persist(repository, tenant_suffix="TRANSPORT")

    assert dispatcher.dispatch_batch(limit=10) == 1
    assert dispatcher.dispatch_batch(limit=10) == 0
    assert queue_a.depth() == 1
    first = queue_a.receive(max_messages=1, visibility_timeout_seconds=60)
    assert len(first) == 1
    assert first[0].dispatch == dispatch
    assert first[0].delivery_attempt == 1
    assert repository.snapshot(dispatch.task_id, tenant_id=dispatch.tenant_id).runtime_status is (
        RuntimeStatus.READY
    )

    queue_a.nack(first[0], retry_at=None, reason_code="TEST_REDELIVERY")
    queue_b = PostgresTaskQueue(database)
    second = queue_b.receive(max_messages=1, visibility_timeout_seconds=60)
    assert len(second) == 1
    assert second[0].delivery_attempt == 2
    queue_b.ack(second[0])
    queue_b.ack(second[0])
    assert queue_b.depth() == 0


def test_two_consumers_and_dispatchers_claim_one_delivery_once(
    database: PersistenceDatabase,
) -> None:
    repository = AsyncRuntimeRepository(database)
    queue_a = PostgresTaskQueue(database)
    queue_b = PostgresTaskQueue(database)
    dispatch_a = PostgresOutboxDispatcher(database, queue_a)
    dispatch_b = PostgresOutboxDispatcher(database, queue_b)
    _persist(repository, tenant_suffix="CONCURRENT")

    with ThreadPoolExecutor(max_workers=2) as pool:
        published = tuple(
            future.result()
            for future in (
                pool.submit(dispatch_a.dispatch_batch, limit=10),
                pool.submit(dispatch_b.dispatch_batch, limit=10),
            )
        )
    assert sum(published) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        deliveries = tuple(
            future.result()
            for future in (
                pool.submit(
                    queue_a.receive,
                    max_messages=1,
                    visibility_timeout_seconds=60,
                ),
                pool.submit(
                    queue_b.receive,
                    max_messages=1,
                    visibility_timeout_seconds=60,
                ),
            )
        )
    claimed = tuple(item for batch in deliveries for item in batch)
    assert len(claimed) == 1
    queue_a.ack(claimed[0])


def test_dispatcher_queue_failure_rolls_back_and_pending_intent_retries(
    database: PersistenceDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A publish fault cannot create a half-armed or lost PostgreSQL outbox record."""
    repository = AsyncRuntimeRepository(database)
    queue = PostgresTaskQueue(database)
    dispatcher = PostgresOutboxDispatcher(database, queue)
    dispatch = _persist(repository, tenant_suffix="OUTAGE")
    original_arm = queue._arm_in_session

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ConnectionError("controlled Queue adapter outage")

    monkeypatch.setattr(queue, "_arm_in_session", unavailable)
    with pytest.raises(ConnectionError, match="Queue adapter outage"):
        dispatcher.dispatch_batch(limit=10)
    snapshot = repository.snapshot(dispatch.task_id, tenant_id=dispatch.tenant_id)
    assert snapshot.dispatch_status is DispatchStatus.PENDING
    assert queue.depth() == 0

    monkeypatch.setattr(queue, "_arm_in_session", original_arm)
    assert dispatcher.dispatch_batch(limit=10) == 1
    delivery = queue.receive(max_messages=1, visibility_timeout_seconds=60)[0]
    assert delivery.dispatch == dispatch
    queue.ack(delivery)


def test_cross_tenant_dispatch_envelope_is_rejected_before_queue_publication(
    database: PersistenceDatabase,
) -> None:
    repository = AsyncRuntimeRepository(database)
    queue = PostgresTaskQueue(database)
    dispatch = _persist(repository, tenant_suffix="TENANT-BOUNDARY")
    forged = dispatch.model_copy(update={"tenant_id": "TENANT-PG-QUEUE-OTHER"})

    with pytest.raises(DispatchConflictError, match="does not match"):
        queue.enqueue(forged)
    assert (
        repository.snapshot(
            dispatch.task_id,
            tenant_id=dispatch.tenant_id,
        ).dispatch_status
        is DispatchStatus.PENDING
    )
    assert queue.depth() == 0


def test_visibility_expiry_redelivers_after_ack_loss_and_rejects_old_receipt(
    database: PersistenceDatabase,
) -> None:
    repository = AsyncRuntimeRepository(database)
    queue_a = PostgresTaskQueue(database)
    dispatch = _persist(repository, tenant_suffix="ACK-LOSS")
    assert PostgresOutboxDispatcher(database, queue_a).dispatch_batch(limit=10) == 1
    first = queue_a.receive(max_messages=1, visibility_timeout_seconds=60)[0]
    with database.session() as session:
        session.execute(
            text(
                "UPDATE task_queue_deliveries "
                "SET receipt_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                "WHERE tenant_id = :tenant_id AND dispatch_id = :dispatch_id"
            ),
            {"tenant_id": dispatch.tenant_id, "dispatch_id": dispatch.dispatch_id},
        )

    queue_b = PostgresTaskQueue(database)
    second = queue_b.receive(max_messages=1, visibility_timeout_seconds=60)[0]
    assert second.dispatch == first.dispatch
    assert second.delivery_attempt == first.delivery_attempt + 1
    with pytest.raises(DispatchConflictError, match="stale"):
        queue_a.ack(first)
    queue_b.ack(second)


def test_expired_lease_scanner_rearms_same_generation_and_fences_old_worker(
    database: PersistenceDatabase,
) -> None:
    repository = AsyncRuntimeRepository(database)
    queue = PostgresTaskQueue(database)
    dispatcher = PostgresOutboxDispatcher(database, queue)
    scanner_a = PostgresRecoveryScanner(database)
    scanner_b = PostgresRecoveryScanner(database)
    dispatch = _persist(repository, tenant_suffix="RECOVERY")
    assert dispatcher.dispatch_batch(limit=10) == 1
    delivery = queue.receive(max_messages=1, visibility_timeout_seconds=60)[0]
    first = repository.try_acquire_lease(
        dispatch,
        _worker("A"),
        timing=LeaseTimingPolicy(heartbeat_interval_seconds=1, lease_ttl_seconds=5),
    )
    assert first.lease is not None
    repository.start_runtime_attempt(first.lease)
    with database.session() as session:
        session.execute(
            text(
                "UPDATE workflow_leases SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                "WHERE tenant_id = :tenant_id AND task_id = :task_id"
            ),
            {"tenant_id": dispatch.tenant_id, "task_id": dispatch.task_id},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            future.result()
            for future in (
                pool.submit(scanner_a.scan_batch, limit=10),
                pool.submit(scanner_b.scan_batch, limit=10),
            )
        )
    assert sum(result.recovered for result in results) == 1
    snapshot = repository.snapshot(dispatch.task_id, tenant_id=dispatch.tenant_id)
    assert snapshot.runtime_status is RuntimeStatus.READY
    assert snapshot.execution_generation == 1
    assert snapshot.recovery_attempt_count == 1
    takeover_delivery = queue.receive(max_messages=1, visibility_timeout_seconds=60)[0]
    assert takeover_delivery.dispatch.dispatch_id == delivery.dispatch.dispatch_id
    second = repository.try_acquire_lease(
        takeover_delivery.dispatch,
        _worker("B"),
        timing=LeaseTimingPolicy(heartbeat_interval_seconds=1, lease_ttl_seconds=5),
    )
    assert second.lease is not None
    assert second.lease.fencing_token > first.lease.fencing_token
    tasks = WorkflowRepository(database, initialize_schema=False)
    previous = tasks.state_for(dispatch.task_id, tenant_id=dispatch.tenant_id)
    committed_at = datetime.now(UTC)
    current = TaskState(
        task_id=dispatch.task_id,
        state=TaskStatus.UNDERSTANDING,
        version=previous.version + 1,
        updated_at=committed_at,
        last_event_id=f"EVT-PG-FENCED-{uuid4().hex}",
    )
    event = TaskStateEvent(
        event_id=current.last_event_id,
        task_id=dispatch.task_id,
        from_state=previous.state.value,
        event="START_UNDERSTANDING",
        to_state=current.state.value,
        timestamp=committed_at,
        reason="New Worker owns execution authority",
    )
    with bind_execution_authority(second.lease):
        tasks.commit_transition(previous, current, event, tenant_id=dispatch.tenant_id)
    with pytest.raises(StaleFencingTokenError):
        repository.assert_fenced_probe_commit(first.lease)
    stale_current = current.model_copy(
        update={
            "state": TaskStatus.PLANNING,
            "version": current.version + 1,
            "last_event_id": f"EVT-PG-STALE-{uuid4().hex}",
        }
    )
    stale_event = TaskStateEvent(
        event_id=stale_current.last_event_id,
        task_id=dispatch.task_id,
        from_state=current.state.value,
        event="UNDERSTANDING_COMPLETED",
        to_state=stale_current.state.value,
        timestamp=committed_at,
        reason="Stale Worker must not commit",
    )
    with bind_execution_authority(first.lease), pytest.raises(StaleFencingTokenError):
        tasks.commit_transition(
            current,
            stale_current,
            stale_event,
            tenant_id=dispatch.tenant_id,
        )
    assert tasks.state_for(dispatch.task_id, tenant_id=dispatch.tenant_id) == current
    queue.ack(takeover_delivery)


def test_retry_exhaustion_fails_task_and_dead_letters_dispatch(
    database: PersistenceDatabase,
) -> None:
    repository = AsyncRuntimeRepository(database)
    queue = PostgresTaskQueue(database)
    dispatcher = PostgresOutboxDispatcher(database, queue)
    scanner = PostgresRecoveryScanner(database, max_recovery_attempts=3)
    dispatch = _persist(repository, tenant_suffix="POISON")
    assert dispatcher.dispatch_batch(limit=10) == 1
    with database.session() as session:
        runtime = session.scalar(
            select(WorkflowTaskRuntimeRow).where(
                WorkflowTaskRuntimeRow.tenant_id == dispatch.tenant_id,
                WorkflowTaskRuntimeRow.task_id == dispatch.task_id,
            )
        )
        row = session.scalar(
            select(TaskDispatchRow).where(
                TaskDispatchRow.tenant_id == dispatch.tenant_id,
                TaskDispatchRow.dispatch_id == dispatch.dispatch_id,
            )
        )
        assert runtime is not None and row is not None
        runtime.runtime_status = RuntimeStatus.WAITING_RETRY.value
        runtime.retry_not_before = datetime(2020, 1, 1, tzinfo=UTC)
        runtime.recovery_attempt_count = 3
        row.status = DispatchStatus.RETRY_SCHEDULED.value
        row.available_at = datetime(2020, 1, 1, tzinfo=UTC)

    result = scanner.scan_batch(limit=10)
    assert result.exhausted == 1
    snapshot = repository.snapshot(dispatch.task_id, tenant_id=dispatch.tenant_id)
    assert snapshot.task_status is TaskStatus.FAILED
    assert snapshot.runtime_status is RuntimeStatus.FINISHED
    assert snapshot.dispatch_status is DispatchStatus.DEAD_LETTERED
    assert queue.receive(max_messages=1, visibility_timeout_seconds=60) == ()


def test_submission_capacity_is_atomic_and_returns_typed_backpressure(
    database: PersistenceDatabase,
) -> None:
    repository = AsyncRuntimeRepository(
        database,
        max_queued_per_tenant=1,
        max_queued_global=1,
        capacity_retry_after_seconds=7,
    )
    first = _submission(tenant_suffix="CAPACITY", task_suffix="A")
    repository.persist_task_and_dispatch(*first, idempotency=None)
    second = _submission(tenant_suffix="CAPACITY", task_suffix="B")
    with pytest.raises(RuntimeCapacityError) as raised:
        repository.persist_task_and_dispatch(*second, idempotency=None)
    assert raised.value.retry_after_seconds == 7


def _persist(repository: AsyncRuntimeRepository, *, tenant_suffix: str) -> TaskDispatch:
    values = _submission(tenant_suffix=tenant_suffix, task_suffix=uuid4().hex)
    repository.persist_task_and_dispatch(*values, idempotency=None)
    return values[2]


def _submission(
    *,
    tenant_suffix: str,
    task_suffix: str,
) -> tuple[TaskRequest, TaskState, TaskDispatch, TaskSubmissionResponse]:
    now = datetime.now(UTC)
    tenant_id = f"TENANT-PG-QUEUE-{tenant_suffix}"
    task_id = f"T-PG-QUEUE-{task_suffix}"
    trace_id = f"TRACE-PG-QUEUE-{task_suffix}"
    request = TaskRequest(
        id=f"REQ-PG-QUEUE-{task_suffix}",
        user_id="U-PG-QUEUE",
        raw_input="Run the PostgreSQL Queue integration contract.",
        created_at=now,
    )
    state = TaskState(
        task_id=task_id,
        state=TaskStatus.CREATED,
        version=1,
        updated_at=now,
        last_event_id=f"EVT-PG-QUEUE-{task_suffix}",
    )
    dispatch = TaskDispatch(
        tenant_id=tenant_id,
        task_id=task_id,
        trace_id=trace_id,
        dispatch_id=f"D-PG-QUEUE-{task_suffix}",
        execution_generation=1,
        expected_task_version=1,
        enqueued_at=now,
        not_before=now,
    )
    response = TaskSubmissionResponse(
        task_id=task_id,
        trace_id=trace_id,
        task_status=TaskStatus.CREATED,
        runtime_status=RuntimeStatus.READY,
        accepted_at=now,
        status_url=f"/v1/tasks/{task_id}",
        artifacts_url=f"/v1/tasks/{task_id}/artifacts",
    )
    return request, state, dispatch, response


def _worker(suffix: str) -> WorkerIdentity:
    return WorkerIdentity(
        worker_id=f"WORKER-PG-QUEUE-{suffix}-{uuid4().hex}",
        deployment_id="DEPLOYMENT-PG-QUEUE",
        started_at=datetime.now(UTC),
    )
