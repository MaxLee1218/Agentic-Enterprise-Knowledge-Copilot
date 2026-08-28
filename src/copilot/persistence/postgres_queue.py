"""PostgreSQL-backed Queue v1 and bounded transactional-outbox dispatcher."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from copilot.contracts.async_runtime import (
    DispatchStatus,
    QueueDelivery,
    RuntimeStatus,
    TaskDispatch,
)
from copilot.contracts.errors import DispatchConflictError
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import (
    TaskDispatchRow,
    TaskQueueDeliveryRow,
    WorkflowTaskRuntimeRow,
)

LOGGER = logging.getLogger(__name__)
_RECEIVABLE_DISPATCH_STATUSES = (
    DispatchStatus.ENQUEUED.value,
    DispatchStatus.ACKNOWLEDGED.value,
)


class PostgresTaskQueue:
    """Durable at-least-once Queue using row visibility and opaque receipts."""

    def __init__(self, database: PersistenceDatabase) -> None:
        if database.backend != "postgresql":
            raise ValueError("PostgreSQL Queue v1 requires PostgreSQL persistence")
        self._database = database
        self._closed = False

    def enqueue(self, dispatch: TaskDispatch) -> None:
        """Idempotently arm transport state for an existing tenant-qualified dispatch."""
        self._require_open()
        with self._database.session() as session:
            row = self._load_dispatch(session, dispatch, for_update=True)
            now = _database_now(session)
            self._arm_in_session(session, row, now=now, rearm_acknowledged=False)

    def rearm(self, dispatch: TaskDispatch) -> None:
        """Recovery-only operation that makes the same durable dispatch deliverable again."""
        self._require_open()
        with self._database.session() as session:
            row = self._load_dispatch(session, dispatch, for_update=True)
            now = _database_now(session)
            self._arm_in_session(session, row, now=now, rearm_acknowledged=True)

    def receive(
        self,
        *,
        max_messages: int,
        visibility_timeout_seconds: int,
    ) -> tuple[QueueDelivery, ...]:
        """Claim a bounded due batch with `FOR UPDATE SKIP LOCKED`."""
        self._require_open()
        if max_messages < 1 or max_messages > 1000:
            raise ValueError("max_messages must be between 1 and 1000")
        if visibility_timeout_seconds < 1 or visibility_timeout_seconds > 3600:
            raise ValueError("visibility timeout must be between 1 and 3600 seconds")
        deliveries: list[QueueDelivery] = []
        with self._database.session() as session:
            now = _database_now(session)
            rows = tuple(
                session.scalars(
                    select(TaskQueueDeliveryRow)
                    .join(
                        TaskDispatchRow,
                        and_(
                            TaskDispatchRow.tenant_id == TaskQueueDeliveryRow.tenant_id,
                            TaskDispatchRow.dispatch_id == TaskQueueDeliveryRow.dispatch_id,
                        ),
                    )
                    .where(
                        TaskQueueDeliveryRow.acked_at.is_(None),
                        TaskQueueDeliveryRow.available_at <= now,
                        or_(
                            TaskQueueDeliveryRow.receipt_expires_at.is_(None),
                            TaskQueueDeliveryRow.receipt_expires_at <= now,
                        ),
                        TaskDispatchRow.status.in_(_RECEIVABLE_DISPATCH_STATUSES),
                    )
                    .order_by(
                        TaskQueueDeliveryRow.available_at,
                        TaskQueueDeliveryRow.tenant_id,
                        TaskQueueDeliveryRow.dispatch_id,
                    )
                    .limit(max_messages)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                dispatch_row = session.scalar(
                    select(TaskDispatchRow).where(
                        TaskDispatchRow.tenant_id == row.tenant_id,
                        TaskDispatchRow.dispatch_id == row.dispatch_id,
                    )
                )
                if dispatch_row is None:
                    continue
                receipt_id = f"QD-{uuid4().hex}"
                row.delivery_attempt += 1
                row.receipt_id = receipt_id
                row.receipt_expires_at = now + timedelta(seconds=visibility_timeout_seconds)
                row.updated_at = now
                deliveries.append(
                    QueueDelivery(
                        delivery_id=receipt_id,
                        dispatch=_task_dispatch(dispatch_row),
                        received_at=now,
                        delivery_attempt=row.delivery_attempt,
                    )
                )
        for delivery in deliveries:
            LOGGER.info(
                "Task dispatch received",
                extra={
                    "event": "dispatch_received",
                    "tenant_id": delivery.dispatch.tenant_id,
                    "task_id": delivery.dispatch.task_id,
                    "trace_id": delivery.dispatch.trace_id,
                    "dispatch_id": delivery.dispatch.dispatch_id,
                    "execution_generation": delivery.dispatch.execution_generation,
                    "runtime_attempt": delivery.delivery_attempt,
                    "status": "SUCCESS",
                },
            )
        return tuple(deliveries)

    def ack(self, delivery: QueueDelivery) -> None:
        """Acknowledge only the exact current receipt; repeated exact ACK is safe."""
        self._require_open()
        with self._database.session() as session:
            row = self._delivery_for_update(session, delivery)
            if row.acked_at is not None:
                if row.receipt_id == delivery.delivery_id:
                    return
                raise DispatchConflictError("Queue delivery was acknowledged by another receipt")
            if row.receipt_id != delivery.delivery_id:
                raise DispatchConflictError("Queue receipt is stale")
            now = _database_now(session)
            row.acked_at = now
            row.updated_at = now
            row.last_error_code = None

    def nack(
        self,
        delivery: QueueDelivery,
        *,
        retry_at: datetime | None,
        reason_code: str,
    ) -> None:
        """Release the exact receipt immediately or at a bounded not-before time."""
        self._require_open()
        if not reason_code:
            raise ValueError("reason_code is required")
        with self._database.session() as session:
            row = self._delivery_for_update(session, delivery)
            if row.acked_at is not None:
                raise DispatchConflictError("Acknowledged Queue delivery cannot be nacked")
            if row.receipt_id != delivery.delivery_id:
                raise DispatchConflictError("Queue receipt is stale")
            now = _database_now(session)
            available_at = _as_utc(retry_at) if retry_at is not None else now
            row.available_at = max(now, available_at)
            row.receipt_id = None
            row.receipt_expires_at = None
            row.last_error_code = reason_code
            row.updated_at = now

    def health(self) -> bool:
        """Return whether the Queue can reach its PostgreSQL dependency."""
        if self._closed:
            return False
        try:
            self._database.ping()
        except Exception:
            return False
        return True

    def depth(self) -> int:
        """Return the current unacknowledged Queue depth."""
        self._require_open()
        with self._database.session() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(TaskQueueDeliveryRow)
                    .where(TaskQueueDeliveryRow.acked_at.is_(None))
                )
                or 0
            )

    def oldest_age_seconds(self) -> float:
        """Return age of the oldest unacknowledged delivery using database time."""
        self._require_open()
        with self._database.session() as session:
            now = _database_now(session)
            oldest = session.scalar(
                select(func.min(TaskQueueDeliveryRow.available_at)).where(
                    TaskQueueDeliveryRow.acked_at.is_(None)
                )
            )
            if not isinstance(oldest, datetime):
                return 0.0
            return max(0.0, (now - _as_utc(oldest)).total_seconds())

    def shutdown(self) -> None:
        """Stop accepting Queue operations; database ownership remains with composition."""
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Task Queue is shut down")

    @staticmethod
    def _arm_in_session(
        session: Session,
        dispatch: TaskDispatchRow,
        *,
        now: datetime,
        rearm_acknowledged: bool,
    ) -> TaskQueueDeliveryRow:
        delivery = session.scalar(
            select(TaskQueueDeliveryRow)
            .where(
                TaskQueueDeliveryRow.tenant_id == dispatch.tenant_id,
                TaskQueueDeliveryRow.dispatch_id == dispatch.dispatch_id,
            )
            .with_for_update()
        )
        if delivery is None:
            delivery = TaskQueueDeliveryRow(
                tenant_id=dispatch.tenant_id,
                task_id=dispatch.task_id,
                dispatch_id=dispatch.dispatch_id,
                available_at=_as_utc(dispatch.available_at),
                delivery_attempt=0,
                created_at=now,
                updated_at=now,
            )
            session.add(delivery)
            return delivery
        if delivery.acked_at is not None and rearm_acknowledged:
            delivery.acked_at = None
            delivery.available_at = max(now, _as_utc(dispatch.available_at))
            delivery.receipt_id = None
            delivery.receipt_expires_at = None
            delivery.last_error_code = None
            delivery.updated_at = now
        return delivery

    @staticmethod
    def _load_dispatch(
        session: Session,
        dispatch: TaskDispatch,
        *,
        for_update: bool,
    ) -> TaskDispatchRow:
        statement = select(TaskDispatchRow).where(
            TaskDispatchRow.tenant_id == dispatch.tenant_id,
            TaskDispatchRow.dispatch_id == dispatch.dispatch_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = session.scalar(statement)
        if row is None or _task_dispatch(row) != dispatch:
            raise DispatchConflictError("Queue dispatch does not match durable outbox content")
        return row

    @staticmethod
    def _delivery_for_update(
        session: Session,
        delivery: QueueDelivery,
    ) -> TaskQueueDeliveryRow:
        row = session.scalar(
            select(TaskQueueDeliveryRow)
            .where(
                TaskQueueDeliveryRow.tenant_id == delivery.dispatch.tenant_id,
                TaskQueueDeliveryRow.dispatch_id == delivery.dispatch.dispatch_id,
            )
            .with_for_update()
        )
        if row is None or row.task_id != delivery.dispatch.task_id:
            raise DispatchConflictError("Queue delivery does not match a durable dispatch")
        return row


class PostgresOutboxDispatcher:
    """Bounded multi-instance-safe dispatcher for the existing `task_dispatches` outbox."""

    def __init__(
        self,
        database: PersistenceDatabase,
        queue: PostgresTaskQueue,
        *,
        max_recovery_attempts: int = 3,
    ) -> None:
        if database.backend != "postgresql":
            raise ValueError("PostgreSQL outbox dispatch requires PostgreSQL persistence")
        if max_recovery_attempts < 1 or max_recovery_attempts > 10:
            raise ValueError("max recovery attempts must be between 1 and 10")
        self._database = database
        self._queue = queue
        self._max_recovery_attempts = max_recovery_attempts

    def dispatch_batch(self, *, limit: int) -> int:
        """Atomically arm and publish a bounded due batch using row-skip locking."""
        if limit < 1 or limit > 1000:
            raise ValueError("dispatcher batch limit must be between 1 and 1000")
        published: list[TaskDispatch] = []
        with self._database.session() as session:
            now = _database_now(session)
            runtimes = tuple(
                session.scalars(
                    select(WorkflowTaskRuntimeRow)
                    .join(
                        TaskDispatchRow,
                        and_(
                            TaskDispatchRow.tenant_id == WorkflowTaskRuntimeRow.tenant_id,
                            TaskDispatchRow.dispatch_id
                            == WorkflowTaskRuntimeRow.current_dispatch_id,
                        ),
                    )
                    .where(
                        TaskDispatchRow.status.in_(
                            (
                                DispatchStatus.PENDING.value,
                                DispatchStatus.RETRY_SCHEDULED.value,
                            )
                        ),
                        TaskDispatchRow.available_at <= now,
                    )
                    .order_by(
                        TaskDispatchRow.available_at,
                        TaskDispatchRow.tenant_id,
                        TaskDispatchRow.dispatch_id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True, of=WorkflowTaskRuntimeRow)
                )
            )
            for runtime in runtimes:
                row = session.scalar(
                    select(TaskDispatchRow)
                    .where(
                        TaskDispatchRow.tenant_id == runtime.tenant_id,
                        TaskDispatchRow.dispatch_id == runtime.current_dispatch_id,
                    )
                    .with_for_update()
                )
                if row is None or _as_utc(row.available_at) > now:
                    continue
                if row.status == DispatchStatus.RETRY_SCHEDULED.value:
                    if (
                        runtime.runtime_status != RuntimeStatus.WAITING_RETRY.value
                        or runtime.retry_not_before is None
                        or _as_utc(runtime.retry_not_before) > now
                        or runtime.recovery_attempt_count >= self._max_recovery_attempts
                    ):
                        continue
                    runtime.runtime_status = RuntimeStatus.READY.value
                    runtime.retry_not_before = None
                    runtime.updated_at = now
                elif runtime.runtime_status != RuntimeStatus.READY.value:
                    continue
                self._queue._arm_in_session(
                    session,
                    row,
                    now=now,
                    rearm_acknowledged=True,
                )
                row.status = DispatchStatus.ENQUEUED.value
                row.attempt_count += 1
                row.updated_at = now
                row.last_error_code = None
                published.append(_task_dispatch(row))
        for dispatch in published:
            LOGGER.info(
                "Task dispatch published",
                extra={
                    "event": "dispatch_published",
                    "tenant_id": dispatch.tenant_id,
                    "task_id": dispatch.task_id,
                    "trace_id": dispatch.trace_id,
                    "dispatch_id": dispatch.dispatch_id,
                    "execution_generation": dispatch.execution_generation,
                    "status": "SUCCESS",
                },
            )
        return len(published)


def _task_dispatch(row: TaskDispatchRow) -> TaskDispatch:
    created_at = _as_utc(row.created_at)
    return TaskDispatch(
        tenant_id=row.tenant_id,
        task_id=row.task_id,
        trace_id=row.trace_id,
        dispatch_id=row.dispatch_id,
        execution_generation=row.execution_generation,
        predecessor_execution_generation=row.predecessor_execution_generation,
        resume_checkpoint_id=row.resume_checkpoint_id,
        expected_task_version=row.expected_task_version,
        enqueued_at=created_at,
        not_before=_as_utc(row.available_at),
    )


def _database_now(session: Session) -> datetime:
    value = session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        raise RuntimeError("database did not return a timestamp")
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["PostgresOutboxDispatcher", "PostgresTaskQueue"]
