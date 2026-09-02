"""Tenant-scoped durable clarification persistence and resume transactions."""

from __future__ import annotations

import json
from threading import RLock

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from copilot.contracts import (
    ClarificationStatus,
    TaskClarification,
    TaskDispatch,
    TaskState,
    TaskStatus,
)
from copilot.contracts.async_runtime import DispatchStatus, RuntimeStatus
from copilot.contracts.errors import DispatchConflictError
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.fencing import assert_fenced_session
from copilot.persistence.models import (
    TaskDispatchRow,
    WorkflowClarificationHistoryRow,
    WorkflowClarificationRow,
    WorkflowStateEventRow,
    WorkflowTaskRow,
    WorkflowTaskRuntimeRow,
)
from copilot.services.workflows.models import TaskStateEvent
from copilot.services.workflows.ports import WorkflowRepository


class ClarificationRepository:
    """Persist clarification rounds and their authoritative Task handoffs."""

    def __init__(
        self,
        database: PersistenceDatabase | None,
        *,
        tasks: WorkflowRepository | None = None,
    ) -> None:
        self._database = database
        self._tasks = tasks
        self._items: dict[tuple[str, str], TaskClarification] = {}
        self._lock = RLock()

    def get(self, clarification_id: str, *, tenant_id: str) -> TaskClarification:
        """Return one tenant-qualified clarification or hide it as not found."""
        if self._database is None:
            with self._lock:
                try:
                    return _snapshot(self._items[(tenant_id, clarification_id)])
                except KeyError as exc:
                    raise KeyError(clarification_id) from exc
        with self._database.session() as session:
            row = session.scalar(
                select(WorkflowClarificationRow).where(
                    WorkflowClarificationRow.tenant_id == tenant_id,
                    WorkflowClarificationRow.clarification_id == clarification_id,
                )
            )
            if row is None:
                raise KeyError(clarification_id)
            return TaskClarification.model_validate_json(row.payload_json)

    def list_by_task(self, task_id: str, *, tenant_id: str) -> tuple[TaskClarification, ...]:
        """Return all rounds in deterministic order."""
        if self._database is None:
            with self._lock:
                return tuple(
                    _snapshot(item)
                    for item in sorted(
                        (
                            item
                            for (scope, _), item in self._items.items()
                            if scope == tenant_id and item.task_id == task_id
                        ),
                        key=lambda item: item.round,
                    )
                )
        with self._database.session() as session:
            rows = tuple(
                session.scalars(
                    select(WorkflowClarificationRow)
                    .where(
                        WorkflowClarificationRow.tenant_id == tenant_id,
                        WorkflowClarificationRow.task_id == task_id,
                    )
                    .order_by(WorkflowClarificationRow.round)
                )
            )
            return tuple(TaskClarification.model_validate_json(row.payload_json) for row in rows)

    def get_pending_for_task(self, task_id: str, *, tenant_id: str) -> TaskClarification | None:
        """Return the single pending round guaranteed by persistence uniqueness."""
        items = self._by_status(task_id, tenant_id=tenant_id, status=ClarificationStatus.PENDING)
        if len(items) > 1:
            raise DispatchConflictError("Task has more than one pending clarification")
        return items[0] if items else None

    def get_submitted_for_task(self, task_id: str, *, tenant_id: str) -> TaskClarification | None:
        """Return the latest submitted response awaiting Worker validation."""
        items = self._by_status(
            task_id,
            tenant_id=tenant_id,
            status=ClarificationStatus.SUBMITTED,
        )
        if len(items) > 1:
            raise DispatchConflictError("Task has more than one submitted clarification")
        return items[0] if items else None

    def create_pending_and_transition(
        self,
        clarification: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> TaskClarification:
        """Atomically create one round and suspend the authoritative Task."""
        _validate_suspension(clarification, previous, current, event)
        if self._database is None:
            if self._tasks is None:
                raise RuntimeError("in-memory clarification persistence requires Task repository")
            with self._lock:
                active = self.get_pending_for_task(
                    clarification.task_id,
                    tenant_id=clarification.tenant_id,
                )
                if active is not None:
                    if active == clarification:
                        return active
                    raise ValueError("Task already has a pending clarification")
                self._tasks.commit_transition(
                    previous,
                    current,
                    event,
                    tenant_id=clarification.tenant_id,
                )
                self._items[(clarification.tenant_id, clarification.clarification_id)] = _snapshot(
                    clarification
                )
                return clarification
        try:
            with self._database.session() as session:
                assert_fenced_session(
                    session,
                    tenant_id=clarification.tenant_id,
                    task_id=clarification.task_id,
                )
                self._cas_task(session, previous, current, event, clarification.tenant_id)
                session.add(_clarification_row(clarification))
                session.flush()
                session.add(_history_row(clarification))
        except IntegrityError as exc:
            raise ValueError("Task already has a pending clarification") from exc
        return clarification

    def resolve_submitted(
        self,
        submitted: TaskClarification,
        resolved: TaskClarification,
    ) -> None:
        """Fenced CAS resolution after Worker validation produces a complete contract."""
        _validate_resolution(submitted, resolved)
        if self._database is None:
            with self._lock:
                key = (submitted.tenant_id, submitted.clarification_id)
                if self._items.get(key) != submitted:
                    raise ValueError("clarification compare-and-swap conflict")
                self._items[key] = _snapshot(resolved)
            return
        with self._database.session() as session:
            assert_fenced_session(
                session,
                tenant_id=submitted.tenant_id,
                task_id=submitted.task_id,
            )
            self._cas_clarification(session, submitted, resolved)

    def replace_submitted_with_pending(
        self,
        submitted: TaskClarification,
        resolved: TaskClarification,
        pending: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> None:
        """Atomically resolve one partial answer, create the next round, and resuspend."""
        _validate_resolution(submitted, resolved)
        _validate_suspension(pending, previous, current, event)
        if pending.round != submitted.round + 1 or pending.context != resolved.context:
            raise ValueError("next clarification round or context is inconsistent")
        if self._database is None:
            if self._tasks is None:
                raise RuntimeError("in-memory clarification persistence requires Task repository")
            with self._lock:
                key = (submitted.tenant_id, submitted.clarification_id)
                if self._items.get(key) != submitted:
                    raise ValueError("clarification compare-and-swap conflict")
                self._tasks.commit_transition(
                    previous,
                    current,
                    event,
                    tenant_id=submitted.tenant_id,
                )
                self._items[key] = _snapshot(resolved)
                self._items[(pending.tenant_id, pending.clarification_id)] = _snapshot(pending)
            return
        try:
            with self._database.session() as session:
                assert_fenced_session(
                    session,
                    tenant_id=submitted.tenant_id,
                    task_id=submitted.task_id,
                )
                self._cas_clarification(session, submitted, resolved)
                self._cas_task(session, previous, current, event, submitted.tenant_id)
                session.add(_clarification_row(pending))
                session.flush()
                session.add(_history_row(pending))
        except IntegrityError as exc:
            raise ValueError("next clarification round conflicts with persisted state") from exc

    def resolve_submitted_and_transition(
        self,
        submitted: TaskClarification,
        resolved: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> None:
        """Atomically finalize a submitted round and terminalize the Task."""
        _validate_resolution(submitted, resolved)
        if (
            previous.state is not TaskStatus.UNDERSTANDING
            or current.state is not TaskStatus.FAILED
            or current.version != previous.version + 1
            or event.event_id != current.last_event_id
        ):
            raise ValueError("clarification terminal transition is invalid")
        if self._database is None:
            if self._tasks is None:
                raise RuntimeError("in-memory clarification persistence requires Task repository")
            with self._lock:
                key = (submitted.tenant_id, submitted.clarification_id)
                if self._items.get(key) != submitted:
                    raise ValueError("clarification compare-and-swap conflict")
                self._tasks.commit_transition(
                    previous,
                    current,
                    event,
                    tenant_id=submitted.tenant_id,
                )
                self._items[key] = _snapshot(resolved)
            return
        with self._database.session() as session:
            assert_fenced_session(
                session,
                tenant_id=submitted.tenant_id,
                task_id=submitted.task_id,
            )
            self._cas_clarification(session, submitted, resolved)
            self._cas_task(session, previous, current, event, submitted.tenant_id)

    def submit_response_and_dispatch(
        self,
        pending: TaskClarification,
        submitted: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
        dispatch: TaskDispatch,
    ) -> None:
        """Atomically CAS a response, resume Task state, and create the next dispatch."""
        if self._database is None:
            raise RuntimeError("asynchronous clarification resume requires durable persistence")
        if (
            pending.status is not ClarificationStatus.PENDING
            or submitted.status is not ClarificationStatus.SUBMITTED
            or submitted.version != pending.version + 1
            or previous.state is not TaskStatus.WAITING_CLARIFICATION
            or current.state is not TaskStatus.UNDERSTANDING
            or current.version != previous.version + 1
            or dispatch.task_id != pending.task_id
            or dispatch.tenant_id != pending.tenant_id
            or dispatch.expected_task_version != current.version
        ):
            raise ValueError("clarification resume transaction is invalid")
        try:
            with self._database.session() as session:
                row = session.scalar(
                    select(WorkflowClarificationRow)
                    .where(
                        WorkflowClarificationRow.tenant_id == pending.tenant_id,
                        WorkflowClarificationRow.clarification_id == pending.clarification_id,
                    )
                    .with_for_update()
                )
                task = session.scalar(
                    select(WorkflowTaskRow)
                    .where(
                        WorkflowTaskRow.tenant_id == pending.tenant_id,
                        WorkflowTaskRow.task_id == pending.task_id,
                    )
                    .with_for_update()
                )
                runtime = session.scalar(
                    select(WorkflowTaskRuntimeRow)
                    .where(
                        WorkflowTaskRuntimeRow.tenant_id == pending.tenant_id,
                        WorkflowTaskRuntimeRow.task_id == pending.task_id,
                    )
                    .with_for_update()
                )
                if (
                    row is None
                    or task is None
                    or runtime is None
                    or row.status != ClarificationStatus.PENDING.value
                    or row.version != pending.version
                    or row.payload_json != pending.model_dump_json()
                    or task.state_json != previous.model_dump_json()
                    or runtime.runtime_status != RuntimeStatus.SUSPENDED.value
                    or dispatch.execution_generation != runtime.execution_generation + 1
                    or dispatch.predecessor_execution_generation != runtime.execution_generation
                    or dispatch.resume_checkpoint_id is None
                ):
                    raise DispatchConflictError("Clarification response compare-and-set conflict")
                row.status = submitted.status.value
                row.version = submitted.version
                row.active_task_id = None
                row.response_fingerprint = submitted.response_fingerprint
                row.resume_dispatch_id = dispatch.dispatch_id
                row.payload_json = submitted.model_dump_json()
                row.submitted_at = submitted.submitted_at
                session.add(_history_row(submitted))
                self._cas_task(session, previous, current, event, pending.tenant_id)
                session.add(_dispatch_row(dispatch))
                runtime.runtime_status = RuntimeStatus.READY.value
                runtime.execution_generation = dispatch.execution_generation
                runtime.predecessor_execution_generation = dispatch.predecessor_execution_generation
                runtime.resume_checkpoint_id = dispatch.resume_checkpoint_id
                runtime.current_dispatch_id = dispatch.dispatch_id
                runtime.retry_not_before = None
                runtime.last_recovery_error = None
                assert submitted.submitted_at is not None
                runtime.updated_at = submitted.submitted_at
        except IntegrityError as exc:
            raise DispatchConflictError("Clarification resume dispatch conflicts") from exc

    def _by_status(
        self,
        task_id: str,
        *,
        tenant_id: str,
        status: ClarificationStatus,
    ) -> tuple[TaskClarification, ...]:
        if self._database is None:
            with self._lock:
                return tuple(
                    _snapshot(item)
                    for (scope, _), item in self._items.items()
                    if scope == tenant_id and item.task_id == task_id and item.status is status
                )
        with self._database.session() as session:
            rows = tuple(
                session.scalars(
                    select(WorkflowClarificationRow).where(
                        WorkflowClarificationRow.tenant_id == tenant_id,
                        WorkflowClarificationRow.task_id == task_id,
                        WorkflowClarificationRow.status == status.value,
                    )
                )
            )
            return tuple(TaskClarification.model_validate_json(row.payload_json) for row in rows)

    @staticmethod
    def _cas_task(
        session: Session,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
        tenant_id: str,
    ) -> None:
        result = session.execute(
            update(WorkflowTaskRow)
            .where(
                WorkflowTaskRow.tenant_id == tenant_id,
                WorkflowTaskRow.task_id == previous.task_id,
                WorkflowTaskRow.state_json == previous.model_dump_json(),
            )
            .values(state_json=current.model_dump_json())
        )
        if not isinstance(result, CursorResult) or result.rowcount != 1:
            raise DispatchConflictError("Task state compare-and-set conflict")
        session.add(
            WorkflowStateEventRow(
                event_id=event.event_id,
                task_id=event.task_id,
                tenant_id=tenant_id,
                payload_json=_event_json(event),
            )
        )

    @staticmethod
    def _cas_clarification(
        session: Session,
        submitted: TaskClarification,
        resolved: TaskClarification,
    ) -> None:
        result = session.execute(
            update(WorkflowClarificationRow)
            .where(
                WorkflowClarificationRow.tenant_id == submitted.tenant_id,
                WorkflowClarificationRow.clarification_id == submitted.clarification_id,
                WorkflowClarificationRow.status == ClarificationStatus.SUBMITTED.value,
                WorkflowClarificationRow.version == submitted.version,
                WorkflowClarificationRow.payload_json == submitted.model_dump_json(),
            )
            .values(
                status=resolved.status.value,
                version=resolved.version,
                payload_json=resolved.model_dump_json(),
                resolved_at=resolved.resolved_at,
            )
        )
        if not isinstance(result, CursorResult) or result.rowcount != 1:
            raise ValueError("clarification compare-and-swap conflict")
        session.add(_history_row(resolved))


def _validate_suspension(
    clarification: TaskClarification,
    previous: TaskState,
    current: TaskState,
    event: TaskStateEvent,
) -> None:
    if (
        clarification.status is not ClarificationStatus.PENDING
        or previous.task_id != clarification.task_id
        or previous.state is not TaskStatus.UNDERSTANDING
        or current.state is not TaskStatus.WAITING_CLARIFICATION
        or current.version != previous.version + 1
        or event.event_id != current.last_event_id
    ):
        raise ValueError("clarification suspension transaction is invalid")


def _validate_resolution(
    submitted: TaskClarification,
    resolved: TaskClarification,
) -> None:
    if (
        submitted.status is not ClarificationStatus.SUBMITTED
        or resolved.status not in {ClarificationStatus.RESOLVED, ClarificationStatus.REJECTED}
        or resolved.clarification_id != submitted.clarification_id
        or resolved.task_id != submitted.task_id
        or resolved.tenant_id != submitted.tenant_id
        or resolved.version != submitted.version + 1
    ):
        raise ValueError("clarification resolution is invalid")


def _clarification_row(item: TaskClarification) -> WorkflowClarificationRow:
    return WorkflowClarificationRow(
        clarification_id=item.clarification_id,
        tenant_id=item.tenant_id,
        task_id=item.task_id,
        round=item.round,
        status=item.status.value,
        version=item.version,
        active_task_id=item.task_id if item.status is ClarificationStatus.PENDING else None,
        response_fingerprint=item.response_fingerprint,
        payload_json=item.model_dump_json(),
        created_at=item.created_at,
        submitted_at=item.submitted_at,
        resolved_at=item.resolved_at,
    )


def _history_row(item: TaskClarification) -> WorkflowClarificationHistoryRow:
    return WorkflowClarificationHistoryRow(
        clarification_id=item.clarification_id,
        version=item.version,
        tenant_id=item.tenant_id,
        payload_json=item.model_dump_json(),
    )


def _dispatch_row(dispatch: TaskDispatch) -> TaskDispatchRow:
    return TaskDispatchRow(
        tenant_id=dispatch.tenant_id,
        dispatch_id=dispatch.dispatch_id,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        predecessor_execution_generation=dispatch.predecessor_execution_generation,
        resume_checkpoint_id=dispatch.resume_checkpoint_id,
        expected_task_version=dispatch.expected_task_version,
        trace_id=dispatch.trace_id,
        status=DispatchStatus.PENDING.value,
        available_at=dispatch.not_before,
        attempt_count=0,
        created_at=dispatch.enqueued_at,
        updated_at=dispatch.enqueued_at,
    )


def _event_json(event: TaskStateEvent) -> str:
    return json.dumps(
        {
            "event_id": event.event_id,
            "task_id": event.task_id,
            "from_state": event.from_state,
            "event": event.event,
            "to_state": event.to_state,
            "timestamp": event.timestamp.isoformat(),
            "reason": event.reason,
        },
        sort_keys=True,
    )


def _snapshot(item: TaskClarification) -> TaskClarification:
    return TaskClarification.model_validate_json(item.model_dump_json())


__all__ = ["ClarificationRepository"]
