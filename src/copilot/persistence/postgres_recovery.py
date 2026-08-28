"""Bounded PostgreSQL recovery scanner for the frozen asynchronous runtime."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from copilot.contracts import TaskResult, TaskState, TaskStatus
from copilot.contracts.async_runtime import DispatchStatus, RuntimeAttemptStatus, RuntimeStatus
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import (
    TaskDispatchRow,
    TaskQueueDeliveryRow,
    TaskRuntimeAttemptRow,
    WorkflowLeaseRow,
    WorkflowStateEventRow,
    WorkflowTaskRow,
    WorkflowTaskRuntimeRow,
)

LOGGER = logging.getLogger(__name__)
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


@dataclass(frozen=True, slots=True)
class RecoveryScanResult:
    """Operational counts from one bounded, multi-scanner-safe pass."""

    examined: int
    recovered: int
    exhausted: int
    skipped: int


class PostgresRecoveryScanner:
    """Re-arm only current recoverable work while Task DB remains authoritative."""

    def __init__(self, database: PersistenceDatabase, *, max_recovery_attempts: int = 3) -> None:
        if database.backend != "postgresql":
            raise ValueError("PostgreSQL recovery scanning requires PostgreSQL persistence")
        if max_recovery_attempts < 1 or max_recovery_attempts > 10:
            raise ValueError("max recovery attempts must be between 1 and 10")
        self._database = database
        self._max_recovery_attempts = max_recovery_attempts

    def scan_batch(self, *, limit: int) -> RecoveryScanResult:
        """Recover a bounded candidate batch using runtime-row skip locking and DB time."""
        if limit < 1 or limit > 1000:
            raise ValueError("recovery batch limit must be between 1 and 1000")
        recovered = exhausted = skipped = 0
        with self._database.session() as session:
            now = _database_now(session)
            runtimes = tuple(
                session.scalars(
                    self._candidate_statement(now=now, limit=limit).with_for_update(
                        skip_locked=True,
                        of=WorkflowTaskRuntimeRow,
                    )
                )
            )
            for runtime in runtimes:
                disposition = self._recover_locked(session, runtime, now=now)
                if disposition == "RECOVERED":
                    recovered += 1
                elif disposition == "EXHAUSTED":
                    exhausted += 1
                else:
                    skipped += 1
        result = RecoveryScanResult(
            examined=recovered + exhausted + skipped,
            recovered=recovered,
            exhausted=exhausted,
            skipped=skipped,
        )
        if result.examined:
            LOGGER.info(
                "Runtime recovery scan completed",
                extra={
                    "event": "runtime_recovery_scan",
                    "status": "SUCCESS",
                    "examined": result.examined,
                    "recovered": result.recovered,
                    "exhausted": result.exhausted,
                    "skipped": result.skipped,
                },
            )
        return result

    @staticmethod
    def _candidate_statement(*, now: datetime, limit: int) -> Select[tuple[WorkflowTaskRuntimeRow]]:
        active_lease = exists().where(
            WorkflowLeaseRow.tenant_id == WorkflowTaskRuntimeRow.tenant_id,
            WorkflowLeaseRow.task_id == WorkflowTaskRuntimeRow.task_id,
            WorkflowLeaseRow.expires_at > now,
        )
        expired_lease = exists().where(
            WorkflowLeaseRow.tenant_id == WorkflowTaskRuntimeRow.tenant_id,
            WorkflowLeaseRow.task_id == WorkflowTaskRuntimeRow.task_id,
            WorkflowLeaseRow.expires_at <= now,
        )
        unacknowledged_delivery = exists().where(
            TaskQueueDeliveryRow.tenant_id == WorkflowTaskRuntimeRow.tenant_id,
            TaskQueueDeliveryRow.dispatch_id == WorkflowTaskRuntimeRow.current_dispatch_id,
            TaskQueueDeliveryRow.acked_at.is_(None),
        )
        current_publishable_dispatch = exists().where(
            TaskDispatchRow.tenant_id == WorkflowTaskRuntimeRow.tenant_id,
            TaskDispatchRow.dispatch_id == WorkflowTaskRuntimeRow.current_dispatch_id,
            TaskDispatchRow.status.in_(
                (
                    DispatchStatus.ENQUEUED.value,
                    DispatchStatus.ACKNOWLEDGED.value,
                )
            ),
        )
        return (
            select(WorkflowTaskRuntimeRow)
            .where(
                or_(
                    and_(
                        WorkflowTaskRuntimeRow.runtime_status == RuntimeStatus.LEASED.value,
                        expired_lease,
                    ),
                    and_(
                        WorkflowTaskRuntimeRow.runtime_status == RuntimeStatus.WAITING_RETRY.value,
                        WorkflowTaskRuntimeRow.retry_not_before <= now,
                    ),
                    and_(
                        WorkflowTaskRuntimeRow.runtime_status == RuntimeStatus.READY.value,
                        ~active_lease,
                        current_publishable_dispatch,
                        ~unacknowledged_delivery,
                    ),
                )
            )
            .order_by(
                WorkflowTaskRuntimeRow.updated_at,
                WorkflowTaskRuntimeRow.tenant_id,
                WorkflowTaskRuntimeRow.task_id,
            )
            .limit(limit)
        )

    def _recover_locked(
        self,
        session: Session,
        runtime: WorkflowTaskRuntimeRow,
        *,
        now: datetime,
    ) -> str:
        task = session.scalar(
            select(WorkflowTaskRow)
            .where(
                WorkflowTaskRow.tenant_id == runtime.tenant_id,
                WorkflowTaskRow.task_id == runtime.task_id,
            )
            .with_for_update()
        )
        if task is None:
            return "SKIPPED"
        state = TaskState.model_validate_json(task.state_json)
        if state.state in _TERMINAL or state.state is TaskStatus.WAITING_APPROVAL:
            return "SKIPPED"
        lease = session.scalar(
            select(WorkflowLeaseRow)
            .where(
                WorkflowLeaseRow.tenant_id == runtime.tenant_id,
                WorkflowLeaseRow.task_id == runtime.task_id,
            )
            .with_for_update()
        )
        if lease is not None and _as_utc(lease.expires_at) > now:
            return "SKIPPED"
        dispatch = (
            session.scalar(
                select(TaskDispatchRow)
                .where(
                    TaskDispatchRow.tenant_id == runtime.tenant_id,
                    TaskDispatchRow.dispatch_id == runtime.current_dispatch_id,
                )
                .with_for_update()
            )
            if runtime.current_dispatch_id is not None
            else None
        )
        if dispatch is None:
            self._fail_closed(
                session,
                task=task,
                runtime=runtime,
                dispatch=None,
                lease=lease,
                now=now,
                error_code="RECOVERY_DISPATCH_MISSING",
            )
            return "EXHAUSTED"
        if runtime.recovery_attempt_count >= self._max_recovery_attempts:
            self._fail_closed(
                session,
                task=task,
                runtime=runtime,
                dispatch=dispatch,
                lease=lease,
                now=now,
                error_code="RUNTIME_RETRY_EXHAUSTED",
            )
            return "EXHAUSTED"
        if dispatch.status not in {
            DispatchStatus.ENQUEUED.value,
            DispatchStatus.ACKNOWLEDGED.value,
            DispatchStatus.RETRY_SCHEDULED.value,
        }:
            return "SKIPPED"

        scheduled_retry = runtime.runtime_status == RuntimeStatus.WAITING_RETRY.value
        if lease is not None:
            session.delete(lease)
            self._finish_running_attempts(
                session,
                runtime=runtime,
                now=now,
                error_code="LEASE_EXPIRED",
            )
        runtime.runtime_status = RuntimeStatus.READY.value
        runtime.retry_not_before = None
        if not scheduled_retry:
            runtime.recovery_attempt_count += 1
        runtime.last_recovery_error = (
            "LEASE_EXPIRED" if lease is not None else runtime.last_recovery_error
        )
        runtime.updated_at = now
        dispatch.status = DispatchStatus.ENQUEUED.value
        dispatch.available_at = now
        dispatch.attempt_count += 1
        dispatch.updated_at = now
        self._force_rearm_delivery(session, dispatch=dispatch, now=now)
        LOGGER.warning(
            "Runtime task recovered",
            extra={
                "event": "runtime_recovery",
                "tenant_id": runtime.tenant_id,
                "task_id": runtime.task_id,
                "dispatch_id": dispatch.dispatch_id,
                "execution_generation": runtime.execution_generation,
                "recovery_attempt": runtime.recovery_attempt_count,
                "status": "SUCCESS",
            },
        )
        return "RECOVERED"

    @staticmethod
    def _force_rearm_delivery(
        session: Session,
        *,
        dispatch: TaskDispatchRow,
        now: datetime,
    ) -> None:
        delivery = session.scalar(
            select(TaskQueueDeliveryRow)
            .where(
                TaskQueueDeliveryRow.tenant_id == dispatch.tenant_id,
                TaskQueueDeliveryRow.dispatch_id == dispatch.dispatch_id,
            )
            .with_for_update()
        )
        if delivery is None:
            session.add(
                TaskQueueDeliveryRow(
                    tenant_id=dispatch.tenant_id,
                    task_id=dispatch.task_id,
                    dispatch_id=dispatch.dispatch_id,
                    available_at=now,
                    delivery_attempt=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        delivery.available_at = now
        delivery.receipt_id = None
        delivery.receipt_expires_at = None
        delivery.acked_at = None
        delivery.last_error_code = None
        delivery.updated_at = now

    def _fail_closed(
        self,
        session: Session,
        *,
        task: WorkflowTaskRow,
        runtime: WorkflowTaskRuntimeRow,
        dispatch: TaskDispatchRow | None,
        lease: WorkflowLeaseRow | None,
        now: datetime,
        error_code: str,
    ) -> None:
        previous = TaskState.model_validate_json(task.state_json)
        event_id = f"EVT-{uuid4().hex}"
        current = TaskState(
            task_id=runtime.task_id,
            state=TaskStatus.FAILED,
            version=previous.version + 1,
            updated_at=now,
            last_event_id=event_id,
        )
        task.state_json = current.model_dump_json()
        if task.task_result_json is None:
            task.task_result_json = TaskResult(
                task_id=runtime.task_id,
                final_status=TaskStatus.FAILED,
                summary="Task failed because the runtime recovery budget was exhausted.",
            ).model_dump_json()
        session.add(
            WorkflowStateEventRow(
                event_id=event_id,
                task_id=runtime.task_id,
                tenant_id=runtime.tenant_id,
                payload_json=json.dumps(
                    {
                        "event_id": event_id,
                        "task_id": runtime.task_id,
                        "from_state": previous.state.value,
                        "event": "RUNTIME_RETRY_EXHAUSTED",
                        "to_state": TaskStatus.FAILED.value,
                        "timestamp": now.isoformat(),
                        "reason": error_code,
                    },
                    sort_keys=True,
                ),
            )
        )
        if lease is not None:
            session.delete(lease)
        self._finish_running_attempts(
            session,
            runtime=runtime,
            now=now,
            error_code=error_code,
        )
        runtime.runtime_status = RuntimeStatus.FINISHED.value
        runtime.retry_not_before = None
        runtime.last_recovery_error = error_code
        runtime.updated_at = now
        if dispatch is not None:
            dispatch.status = DispatchStatus.DEAD_LETTERED.value
            dispatch.last_error_code = error_code
            dispatch.updated_at = now
            delivery = session.scalar(
                select(TaskQueueDeliveryRow)
                .where(
                    TaskQueueDeliveryRow.tenant_id == dispatch.tenant_id,
                    TaskQueueDeliveryRow.dispatch_id == dispatch.dispatch_id,
                )
                .with_for_update()
            )
            if delivery is not None:
                delivery.receipt_id = None
                delivery.receipt_expires_at = None
                delivery.acked_at = now
                delivery.last_error_code = error_code
                delivery.updated_at = now
        LOGGER.error(
            "Runtime recovery failed closed",
            extra={
                "event": "runtime_recovery_failed",
                "tenant_id": runtime.tenant_id,
                "task_id": runtime.task_id,
                "dispatch_id": dispatch.dispatch_id if dispatch is not None else None,
                "execution_generation": runtime.execution_generation,
                "recovery_attempt": runtime.recovery_attempt_count,
                "status": "FAILED",
                "error_code": error_code,
            },
        )

    @staticmethod
    def _finish_running_attempts(
        session: Session,
        *,
        runtime: WorkflowTaskRuntimeRow,
        now: datetime,
        error_code: str,
    ) -> None:
        rows = tuple(
            session.scalars(
                select(TaskRuntimeAttemptRow)
                .where(
                    TaskRuntimeAttemptRow.tenant_id == runtime.tenant_id,
                    TaskRuntimeAttemptRow.task_id == runtime.task_id,
                    TaskRuntimeAttemptRow.execution_generation == runtime.execution_generation,
                    TaskRuntimeAttemptRow.status == RuntimeAttemptStatus.RUNNING.value,
                )
                .with_for_update()
            )
        )
        for row in rows:
            row.status = RuntimeAttemptStatus.LOST.value
            row.completed_at = now
            row.error_code = error_code


def _database_now(session: Session) -> datetime:
    value = session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        raise RuntimeError("database did not return a timestamp")
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["PostgresRecoveryScanner", "RecoveryScanResult"]
