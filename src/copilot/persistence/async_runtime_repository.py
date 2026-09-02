"""PostgreSQL/SQLAlchemy implementation of the frozen Stage B runtime persistence ports.

This module owns no Queue client and starts no Worker. Queue delivery remains subordinate to the
transactional outbox records persisted here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from copilot.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    ClarificationStatus,
    TaskClarification,
    TaskResult,
    TaskState,
    TaskStatus,
)
from copilot.contracts.async_runtime import (
    CancellationRequest,
    CancellationState,
    DispatchRecord,
    DispatchStatus,
    ExecutionLease,
    LeaseAcquisitionResult,
    LeaseAcquisitionStatus,
    LeaseTimingPolicy,
    RecoveryAction,
    RecoveryDecision,
    RuntimeAttempt,
    RuntimeAttemptStatus,
    RuntimeStatus,
    SubmissionIdempotency,
    TaskDispatch,
    TaskRuntimeSnapshot,
    TaskSubmissionResponse,
    WorkerIdentity,
)
from copilot.contracts.errors import (
    DispatchConflictError,
    LeaseExpiredError,
    LeaseLostError,
    RuntimeCapacityError,
    StaleExecutionGenerationError,
    StaleFencingTokenError,
    TaskAlreadyTerminalError,
)
from copilot.contracts.tasks import TaskRequest
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import (
    TaskDispatchRow,
    TaskRuntimeAttemptRow,
    TaskSubmissionIdempotencyRow,
    WorkflowApprovalHistoryRow,
    WorkflowApprovalRow,
    WorkflowClarificationHistoryRow,
    WorkflowClarificationRow,
    WorkflowLeaseRow,
    WorkflowStateEventRow,
    WorkflowStepResultRow,
    WorkflowTaskRow,
    WorkflowTaskRuntimeRow,
)
from copilot.services.observability import NoopObservability, ObservabilityPort
from copilot.services.workflows.models import TaskStateEvent

LOGGER = logging.getLogger(__name__)
DatabaseClock = Callable[[Session], datetime]
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
_LEGAL_DISPATCH_TRANSITIONS: dict[DispatchStatus, frozenset[DispatchStatus]] = {
    DispatchStatus.PENDING: frozenset({DispatchStatus.ENQUEUED, DispatchStatus.RETRY_SCHEDULED}),
    DispatchStatus.RETRY_SCHEDULED: frozenset(
        {DispatchStatus.ENQUEUED, DispatchStatus.RETRY_SCHEDULED}
    ),
    DispatchStatus.ENQUEUED: frozenset(
        {
            DispatchStatus.ACKNOWLEDGED,
            DispatchStatus.SUPERSEDED,
            DispatchStatus.DEAD_LETTERED,
        }
    ),
    DispatchStatus.ACKNOWLEDGED: frozenset(),
    DispatchStatus.SUPERSEDED: frozenset(),
    DispatchStatus.DEAD_LETTERED: frozenset(),
}


class AsyncRuntimeRepository:
    """One transactional implementation of submission, dispatch, lease, and runtime ports."""

    def __init__(
        self,
        database: PersistenceDatabase,
        *,
        database_clock: DatabaseClock | None = None,
        max_queued_per_tenant: int = 1000,
        max_queued_global: int = 10_000,
        capacity_retry_after_seconds: int = 5,
        observability: ObservabilityPort | None = None,
    ) -> None:
        if max_queued_per_tenant < 1 or max_queued_global < max_queued_per_tenant:
            raise ValueError("runtime queue capacity limits are invalid")
        if capacity_retry_after_seconds < 1:
            raise ValueError("capacity retry delay must be positive")
        self._database = database
        self._database_clock = database_clock or _database_now
        self._max_queued_per_tenant = max_queued_per_tenant
        self._max_queued_global = max_queued_global
        self._capacity_retry_after_seconds = capacity_retry_after_seconds
        self._observability = observability or NoopObservability()

    def persist_task_and_dispatch(
        self,
        request: TaskRequest,
        state: TaskState,
        dispatch: TaskDispatch,
        response: TaskSubmissionResponse,
        *,
        idempotency: SubmissionIdempotency | None,
    ) -> tuple[TaskSubmissionResponse, bool]:
        """Atomically commit Task, CREATED state, runtime row, outbox, and optional key."""
        self._validate_submission(request, state, dispatch, response, idempotency)
        try:
            with self._database.session() as session:
                if idempotency is not None:
                    existing = self._idempotency_row(session, idempotency)
                    if existing is not None:
                        return self._reuse_idempotency(existing, idempotency), True
                self._assert_submission_capacity(session, tenant_id=dispatch.tenant_id)
                session.add(
                    WorkflowTaskRow(
                        task_id=state.task_id,
                        tenant_id=dispatch.tenant_id,
                        request_json=request.model_dump_json(),
                        contract_json=None,
                        plan_json=None,
                        state_json=state.model_dump_json(),
                    )
                )
                session.flush()
                session.add(_dispatch_row(_pending_record(dispatch, response.accepted_at)))
                session.flush()
                session.add(
                    WorkflowTaskRuntimeRow(
                        tenant_id=dispatch.tenant_id,
                        task_id=state.task_id,
                        runtime_status=RuntimeStatus.READY.value,
                        execution_generation=dispatch.execution_generation,
                        predecessor_execution_generation=(
                            dispatch.predecessor_execution_generation
                        ),
                        resume_checkpoint_id=dispatch.resume_checkpoint_id,
                        current_dispatch_id=dispatch.dispatch_id,
                        fencing_counter=0,
                        recovery_attempt_count=0,
                        created_at=response.accepted_at,
                        updated_at=response.accepted_at,
                    )
                )
                if idempotency is not None:
                    session.add(
                        TaskSubmissionIdempotencyRow(
                            tenant_id=idempotency.tenant_id,
                            caller_id=idempotency.caller_id,
                            idempotency_key=idempotency.idempotency_key,
                            request_fingerprint=idempotency.request_fingerprint,
                            task_id=state.task_id,
                            response_json=response.model_dump_json(),
                            created_at=response.accepted_at,
                        )
                    )
        except IntegrityError as exc:
            if idempotency is not None:
                reused = self._load_reused_submission(idempotency)
                if reused is not None:
                    return reused, True
            raise DispatchConflictError("Task or initial dispatch identity already exists") from exc
        LOGGER.info(
            "Task and initial dispatch committed",
            extra={
                "event": "task_accepted",
                "task_id": state.task_id,
                "tenant_id": dispatch.tenant_id,
                "trace_id": dispatch.trace_id,
                "dispatch_id": dispatch.dispatch_id,
                "execution_generation": dispatch.execution_generation,
                "status": "SUCCESS",
            },
        )
        return response, False

    def _assert_submission_capacity(self, session: Session, *, tenant_id: str) -> None:
        """Serialize PostgreSQL capacity checks so concurrent acceptance cannot exceed caps."""
        if self._database.backend != "postgresql":
            return
        session.execute(text("SELECT pg_advisory_xact_lock(19019001)"))
        global_count = int(
            session.scalar(
                select(func.count())
                .select_from(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.runtime_status.in_(
                        (RuntimeStatus.READY.value, RuntimeStatus.WAITING_RETRY.value)
                    )
                )
            )
            or 0
        )
        tenant_count = int(
            session.scalar(
                select(func.count())
                .select_from(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                    WorkflowTaskRuntimeRow.runtime_status.in_(
                        (RuntimeStatus.READY.value, RuntimeStatus.WAITING_RETRY.value)
                    ),
                )
            )
            or 0
        )
        if global_count >= self._max_queued_global or tenant_count >= self._max_queued_per_tenant:
            raise RuntimeCapacityError(
                "Asynchronous Task acceptance is temporarily at capacity",
                retry_after_seconds=self._capacity_retry_after_seconds,
            )

    def create(self, record: DispatchRecord) -> DispatchRecord:
        """Create a dispatch idempotently by tenant/task/execution generation."""
        _validate_dispatch_record(record)
        try:
            with self._database.session() as session:
                session.add(_dispatch_row(record))
        except IntegrityError as exc:
            try:
                existing = self.get(
                    record.dispatch.dispatch_id,
                    tenant_id=record.dispatch.tenant_id,
                )
            except KeyError:
                existing = self._get_by_generation(
                    tenant_id=record.dispatch.tenant_id,
                    task_id=record.dispatch.task_id,
                    execution_generation=record.dispatch.execution_generation,
                )
            if existing == record:
                return existing
            raise DispatchConflictError("Dispatch identity is bound to different content") from exc
        return record

    def resolve_approval_and_update_runtime(
        self,
        pending: ApprovalRequest,
        resolved: ApprovalRequest,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
        *,
        tenant_id: str,
        dispatch: TaskDispatch | None,
    ) -> None:
        """Atomically resolve approval, transition Task, and optionally create resume dispatch."""
        approved = resolved.status is ApprovalStatus.APPROVED
        if approved != (dispatch is not None):
            raise ValueError("only an approved resolution may create an execution dispatch")
        if (
            pending.tenant_id != tenant_id
            or resolved.tenant_id != tenant_id
            or pending.approval_id != resolved.approval_id
            or resolved.version != pending.version + 1
            or previous.task_id != pending.task_id
            or current.task_id != pending.task_id
            or current.version != previous.version + 1
            or event.event_id != current.last_event_id
        ):
            raise ValueError("approval runtime transaction identities do not match")
        if previous.state is not TaskStatus.WAITING_APPROVAL:
            raise ValueError("approval runtime transaction requires WAITING_APPROVAL")
        if approved and current.state is not TaskStatus.EXECUTING:
            raise ValueError("approved resolution must transition Task to EXECUTING")
        if not approved and current.state is not TaskStatus.CANCELLED:
            raise ValueError("non-approved resolution must transition Task to CANCELLED")
        with self._database.session() as session:
            approval_row = session.scalar(
                select(WorkflowApprovalRow)
                .where(
                    WorkflowApprovalRow.tenant_id == tenant_id,
                    WorkflowApprovalRow.approval_id == pending.approval_id,
                )
                .with_for_update()
            )
            task = session.scalar(
                select(WorkflowTaskRow)
                .where(
                    WorkflowTaskRow.tenant_id == tenant_id,
                    WorkflowTaskRow.task_id == pending.task_id,
                )
                .with_for_update()
            )
            runtime = session.scalar(
                select(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                    WorkflowTaskRuntimeRow.task_id == pending.task_id,
                )
                .with_for_update()
            )
            if (
                approval_row is None
                or task is None
                or runtime is None
                or approval_row.status != ApprovalStatus.PENDING.value
                or approval_row.version != pending.version
                or approval_row.payload_json != pending.model_dump_json()
                or task.state_json != previous.model_dump_json()
                or runtime.runtime_status != RuntimeStatus.SUSPENDED.value
            ):
                raise DispatchConflictError("Approval resolution compare-and-set conflict")
            now = self._now(session)
            approval_row.status = resolved.status.value
            approval_row.version = resolved.version
            approval_row.payload_json = resolved.model_dump_json()
            session.add(
                WorkflowApprovalHistoryRow(
                    approval_id=resolved.approval_id,
                    version=resolved.version,
                    tenant_id=tenant_id,
                    payload_json=resolved.model_dump_json(),
                )
            )
            task.state_json = current.model_dump_json()
            session.add(
                WorkflowStateEventRow(
                    event_id=event.event_id,
                    task_id=event.task_id,
                    tenant_id=tenant_id,
                    payload_json=json.dumps(
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
                    ),
                )
            )
            if dispatch is not None:
                if (
                    dispatch.task_id != pending.task_id
                    or dispatch.tenant_id != tenant_id
                    or dispatch.execution_generation != runtime.execution_generation + 1
                    or dispatch.predecessor_execution_generation != runtime.execution_generation
                    or dispatch.resume_checkpoint_id is None
                    or dispatch.expected_task_version != current.version
                ):
                    raise ValueError("approval resume dispatch binding is invalid")
                session.add(_dispatch_row(_pending_record(dispatch, dispatch.enqueued_at)))
                runtime.runtime_status = RuntimeStatus.READY.value
                runtime.execution_generation = dispatch.execution_generation
                runtime.predecessor_execution_generation = dispatch.predecessor_execution_generation
                runtime.resume_checkpoint_id = dispatch.resume_checkpoint_id
                runtime.current_dispatch_id = dispatch.dispatch_id
                runtime.retry_not_before = None
                runtime.last_recovery_error = None
            else:
                task.task_result_json = TaskResult(
                    task_id=pending.task_id,
                    final_status=TaskStatus.CANCELLED,
                    summary="Task cancelled because approval did not authorize execution.",
                ).model_dump_json()
                runtime.runtime_status = RuntimeStatus.FINISHED.value
                runtime.retry_not_before = None
            runtime.updated_at = now

    def get(self, dispatch_id: str, *, tenant_id: str) -> DispatchRecord:
        """Load one tenant-scoped durable dispatch record."""
        with self._database.session() as session:
            row = session.scalar(
                select(TaskDispatchRow).where(
                    TaskDispatchRow.tenant_id == tenant_id,
                    TaskDispatchRow.dispatch_id == dispatch_id,
                )
            )
            if row is None:
                raise KeyError(dispatch_id)
            return _dispatch_record(row)

    def compare_and_set_status(
        self,
        dispatch_id: str,
        *,
        tenant_id: str,
        expected: DispatchStatus,
        replacement: DispatchStatus,
        observed_at: datetime,
        error_code: str | None = None,
    ) -> DispatchRecord:
        """Apply one legal outbox transition with a compare-and-set update."""
        observed_at = _as_utc(observed_at)
        if replacement not in _LEGAL_DISPATCH_TRANSITIONS[expected]:
            raise DispatchConflictError(
                f"Illegal dispatch transition {expected.value} -> {replacement.value}"
            )
        publish_attempt = expected in {
            DispatchStatus.PENDING,
            DispatchStatus.RETRY_SCHEDULED,
        }
        with self._database.session() as session:
            values: dict[str, object] = {
                "status": replacement.value,
                "updated_at": observed_at,
                "last_error_code": error_code,
            }
            if publish_attempt:
                values["attempt_count"] = TaskDispatchRow.attempt_count + 1
            result = session.execute(
                update(TaskDispatchRow)
                .where(
                    TaskDispatchRow.tenant_id == tenant_id,
                    TaskDispatchRow.dispatch_id == dispatch_id,
                    TaskDispatchRow.status == expected.value,
                )
                .values(**values)
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                raise DispatchConflictError("Dispatch compare-and-set conflict")
            row = session.scalar(
                select(TaskDispatchRow).where(
                    TaskDispatchRow.tenant_id == tenant_id,
                    TaskDispatchRow.dispatch_id == dispatch_id,
                )
            )
            assert row is not None
            session.flush()
            return _dispatch_record(row)

    def try_acquire_lease(
        self,
        dispatch: TaskDispatch,
        worker: WorkerIdentity,
        *,
        timing: LeaseTimingPolicy,
    ) -> LeaseAcquisitionResult:
        """Atomically claim one current dispatch with database time and monotonic fencing."""
        with self._database.session() as session:
            now = self._now(session)
            task = session.scalar(
                select(WorkflowTaskRow)
                .where(
                    WorkflowTaskRow.tenant_id == dispatch.tenant_id,
                    WorkflowTaskRow.task_id == dispatch.task_id,
                )
                .with_for_update()
            )
            if task is None:
                return _lease_result(LeaseAcquisitionStatus.STALE_DISPATCH, "TASK_NOT_FOUND")
            state = TaskState.model_validate_json(task.state_json)
            if state.state is TaskStatus.CANCELLED:
                return _lease_result(LeaseAcquisitionStatus.CANCELLED, "TASK_CANCELLED")
            if state.state in _TERMINAL or task.task_result_json is not None:
                return _lease_result(LeaseAcquisitionStatus.TERMINAL, "TASK_TERMINAL")
            runtime = session.scalar(
                select(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == dispatch.tenant_id,
                    WorkflowTaskRuntimeRow.task_id == dispatch.task_id,
                )
                .with_for_update()
            )
            record = session.scalar(
                select(TaskDispatchRow).where(
                    TaskDispatchRow.tenant_id == dispatch.tenant_id,
                    TaskDispatchRow.dispatch_id == dispatch.dispatch_id,
                )
            )
            if (
                runtime is None
                or record is None
                or _dispatch_record(record).dispatch != dispatch
                or runtime.current_dispatch_id != dispatch.dispatch_id
                or runtime.execution_generation != dispatch.execution_generation
                or dispatch.expected_task_version > state.version
                or record.status != DispatchStatus.ENQUEUED.value
                or runtime.runtime_status == RuntimeStatus.SUSPENDED.value
            ):
                return _lease_result(
                    LeaseAcquisitionStatus.STALE_DISPATCH,
                    "DISPATCH_NOT_CURRENT",
                    current_fencing_token=(runtime.fencing_counter or None) if runtime else None,
                )
            existing = session.scalar(
                select(WorkflowLeaseRow)
                .where(
                    WorkflowLeaseRow.tenant_id == dispatch.tenant_id,
                    WorkflowLeaseRow.task_id == dispatch.task_id,
                )
                .with_for_update()
            )
            if existing is not None and _as_utc(existing.expires_at) > now:
                if (
                    existing.worker_id == worker.worker_id
                    and existing.dispatch_id == dispatch.dispatch_id
                    and existing.execution_generation == dispatch.execution_generation
                ):
                    return LeaseAcquisitionResult(
                        status=LeaseAcquisitionStatus.ACQUIRED,
                        lease=_execution_lease(existing),
                        reason_code="LEASE_ALREADY_HELD",
                        current_fencing_token=existing.fencing_token,
                    )
                LOGGER.info(
                    "Execution lease conflict",
                    extra={
                        "event": "lease_acquire_conflict",
                        "task_id": dispatch.task_id,
                        "tenant_id": dispatch.tenant_id,
                        "dispatch_id": dispatch.dispatch_id,
                        "status": "CONFLICT",
                    },
                )
                self._observability.increment("lease_acquire_conflicts")
                return _lease_result(
                    LeaseAcquisitionStatus.CONFLICT,
                    "ACTIVE_LEASE_EXISTS",
                    current_fencing_token=existing.fencing_token,
                )
            if existing is not None:
                self._observability.increment("lease_expirations")
                session.delete(existing)
                session.flush()
            runtime.fencing_counter += 1
            runtime.runtime_status = RuntimeStatus.LEASED.value
            runtime.updated_at = now
            lease_row = WorkflowLeaseRow(
                tenant_id=dispatch.tenant_id,
                task_id=dispatch.task_id,
                dispatch_id=dispatch.dispatch_id,
                execution_generation=dispatch.execution_generation,
                task_version=state.version,
                worker_id=worker.worker_id,
                lease_id=f"L-{uuid4().hex}",
                fencing_token=runtime.fencing_counter,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=timing.lease_ttl_seconds),
            )
            session.add(lease_row)
            session.flush()
            lease = _execution_lease(lease_row)
        LOGGER.info(
            "Execution lease acquired",
            extra={
                "event": "lease_acquired",
                "task_id": dispatch.task_id,
                "tenant_id": dispatch.tenant_id,
                "dispatch_id": dispatch.dispatch_id,
                "worker_id": worker.worker_id,
                "execution_generation": dispatch.execution_generation,
                "fencing_token": lease.fencing_token,
                "status": "SUCCESS",
            },
        )
        return LeaseAcquisitionResult(
            status=LeaseAcquisitionStatus.ACQUIRED,
            lease=lease,
            reason_code="LEASE_ACQUIRED",
            current_fencing_token=lease.fencing_token,
        )

    def heartbeat(
        self,
        lease: ExecutionLease,
        *,
        timing: LeaseTimingPolicy,
    ) -> ExecutionLease:
        """Renew an exact active lease from database time, or fail closed."""
        with self._database.session() as session:
            now = self._now(session)
            row = self._assert_fenced_authority(session, lease, observed_at=now)
            row.heartbeat_at = now
            row.expires_at = now + timedelta(seconds=timing.lease_ttl_seconds)
            session.flush()
            renewed = _execution_lease(row)
        LOGGER.debug(
            "Execution lease renewed",
            extra={
                "event": "lease_heartbeat",
                "task_id": lease.task_id,
                "tenant_id": lease.tenant_id,
                "worker_id": lease.worker_id,
                "fencing_token": lease.fencing_token,
                "status": "SUCCESS",
            },
        )
        return renewed

    def release(self, lease: ExecutionLease) -> bool:
        """Release only the exact current lease; a stale release never removes takeover state."""
        with self._database.session() as session:
            row = session.scalar(
                select(WorkflowLeaseRow)
                .where(
                    WorkflowLeaseRow.tenant_id == lease.tenant_id,
                    WorkflowLeaseRow.task_id == lease.task_id,
                )
                .with_for_update()
            )
            if row is None or not _lease_identity_matches(row, lease):
                return False
            task = session.scalar(
                select(WorkflowTaskRow).where(
                    WorkflowTaskRow.tenant_id == lease.tenant_id,
                    WorkflowTaskRow.task_id == lease.task_id,
                )
            )
            runtime = session.scalar(
                select(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == lease.tenant_id,
                    WorkflowTaskRuntimeRow.task_id == lease.task_id,
                )
                .with_for_update()
            )
            session.delete(row)
            if task is not None and runtime is not None:
                state = TaskState.model_validate_json(task.state_json)
                runtime.runtime_status = _runtime_status_after_release(state.state).value
                runtime.retry_not_before = None
                runtime.updated_at = self._now(session)
        LOGGER.info(
            "Execution lease released",
            extra={
                "event": "lease_released",
                "task_id": lease.task_id,
                "tenant_id": lease.tenant_id,
                "worker_id": lease.worker_id,
                "fencing_token": lease.fencing_token,
                "status": "SUCCESS",
            },
        )
        return True

    def assert_fenced_probe_commit(self, lease: ExecutionLease) -> None:
        """Perform a harmless runtime-row CAS to prove exact lease/fencing write authority."""
        with self._database.session() as session:
            now = self._now(session)
            self._assert_fenced_authority(session, lease, observed_at=now)
            result = session.execute(
                update(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == lease.tenant_id,
                    WorkflowTaskRuntimeRow.task_id == lease.task_id,
                    WorkflowTaskRuntimeRow.execution_generation == lease.execution_generation,
                    WorkflowTaskRuntimeRow.fencing_counter == lease.fencing_token,
                )
                .values(updated_at=now)
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                raise StaleFencingTokenError("Fenced runtime compare-and-set was rejected")

    def snapshot(self, task_id: str, *, tenant_id: str) -> TaskRuntimeSnapshot:
        """Load tenant-scoped Task DB facts without making Queue/checkpoint authoritative."""
        with self._database.session() as session:
            return self._snapshot_in_session(session, task_id=task_id, tenant_id=tenant_id)

    def runtime_status(self, task_id: str, *, tenant_id: str) -> RuntimeStatus:
        """Load the raw runtime projection for status views during short handoff windows."""
        with self._database.session() as session:
            value = session.scalar(
                select(WorkflowTaskRuntimeRow.runtime_status).where(
                    WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                    WorkflowTaskRuntimeRow.task_id == task_id,
                )
            )
            if value is None:
                raise KeyError(task_id)
            return RuntimeStatus(value)

    def runtime_metric_counts(self) -> tuple[int, int]:
        """Return active leases and suspended approvals using authoritative database time."""
        with self._database.session() as session:
            now = self._now(session)
            active_leases = session.scalar(
                select(func.count())
                .select_from(WorkflowLeaseRow)
                .where(WorkflowLeaseRow.expires_at > now)
            )
            waiting_approval = session.scalar(
                select(func.count())
                .select_from(WorkflowTaskRuntimeRow)
                .where(WorkflowTaskRuntimeRow.runtime_status == RuntimeStatus.SUSPENDED.value)
            )
            return int(active_leases or 0), int(waiting_approval or 0)

    def request_cancellation(self, request: CancellationRequest) -> CancellationState:
        """Atomically cancel Task state, revoke pending approval, fence Worker, and store intent."""
        with self._database.session() as session:
            now = self._now(session)
            task = session.scalar(
                select(WorkflowTaskRow)
                .where(
                    WorkflowTaskRow.tenant_id == request.tenant_id,
                    WorkflowTaskRow.task_id == request.task_id,
                )
                .with_for_update()
            )
            runtime = session.scalar(
                select(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == request.tenant_id,
                    WorkflowTaskRuntimeRow.task_id == request.task_id,
                )
                .with_for_update()
            )
            if task is None or runtime is None:
                raise KeyError(request.task_id)
            if runtime.cancellation_json is not None:
                existing = CancellationState.model_validate_json(runtime.cancellation_json)
                if existing.request.request_id != request.request_id:
                    raise DispatchConflictError("Task already has a different cancellation request")
                return existing
            previous = TaskState.model_validate_json(task.state_json)
            if previous.state in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                raise TaskAlreadyTerminalError("Completed or failed Task is not cancellable")
            finalized_at = max(now, request.requested_at)
            cancellation = CancellationState(
                request=request,
                task_finalized_at=finalized_at,
            )
            if previous.state is not TaskStatus.CANCELLED:
                event_id = _cancellation_event_id(request)
                current = TaskState(
                    task_id=request.task_id,
                    state=TaskStatus.CANCELLED,
                    version=previous.version + 1,
                    updated_at=finalized_at,
                    last_event_id=event_id,
                )
                task.state_json = current.model_dump_json()
                session.add(
                    WorkflowStateEventRow(
                        tenant_id=request.tenant_id,
                        event_id=event_id,
                        task_id=request.task_id,
                        payload_json=json.dumps(
                            {
                                "event_id": event_id,
                                "task_id": request.task_id,
                                "from_state": previous.state.value,
                                "event": "CANCEL_REQUESTED",
                                "to_state": TaskStatus.CANCELLED.value,
                                "timestamp": finalized_at.isoformat(),
                                "reason": request.reason_code,
                            },
                            sort_keys=True,
                        ),
                    )
                )
            if task.task_result_json is None:
                task.task_result_json = TaskResult(
                    task_id=request.task_id,
                    final_status=TaskStatus.CANCELLED,
                    summary="Task cancelled by an authorized request.",
                ).model_dump_json()
            self._revoke_pending_approvals(session, request, finalized_at)
            self._cancel_pending_clarifications(session, request, finalized_at)
            session.execute(
                delete(WorkflowLeaseRow).where(
                    WorkflowLeaseRow.tenant_id == request.tenant_id,
                    WorkflowLeaseRow.task_id == request.task_id,
                )
            )
            runtime.cancellation_json = cancellation.model_dump_json()
            runtime.runtime_status = RuntimeStatus.FINISHED.value
            runtime.retry_not_before = None
            runtime.updated_at = finalized_at
        LOGGER.info(
            "Durable task cancellation committed",
            extra={
                "event": "task_cancel_requested",
                "task_id": request.task_id,
                "tenant_id": request.tenant_id,
                "request_id": request.request_id,
                "status": "SUCCESS",
            },
        )
        return cancellation

    def observe_cancellation(
        self,
        task_id: str,
        *,
        tenant_id: str,
        worker_id: str,
    ) -> CancellationState | None:
        """Idempotently record the first Worker observation of durable cancellation."""
        with self._database.session() as session:
            runtime = session.scalar(
                select(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                    WorkflowTaskRuntimeRow.task_id == task_id,
                )
                .with_for_update()
            )
            if runtime is None:
                raise KeyError(task_id)
            if runtime.cancellation_json is None:
                return None
            existing = CancellationState.model_validate_json(runtime.cancellation_json)
            if existing.worker_observed_at is not None:
                return existing
            observed_at = self._now(session)
            observed = existing.model_copy(
                update={
                    "worker_observed_at": observed_at,
                    "observer_worker_id": worker_id,
                }
            )
            runtime.cancellation_json = observed.model_dump_json()
            runtime.updated_at = observed_at
            latency = max(
                0.0,
                (observed_at - existing.request.requested_at).total_seconds(),
            )
        self._observability.observe("cancel_latency_seconds", latency)
        return observed

    def record_recovery_decision(
        self,
        task_id: str,
        decision: RecoveryDecision,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> None:
        """Persist bounded recovery accounting before any future redispatch operation."""
        observed_at = _as_utc(observed_at)
        with self._database.session() as session:
            runtime = session.scalar(
                select(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                    WorkflowTaskRuntimeRow.task_id == task_id,
                )
                .with_for_update()
            )
            if runtime is None:
                raise KeyError(task_id)
            if decision.action in {
                RecoveryAction.REDISPATCH,
                RecoveryAction.RESUME,
                RecoveryAction.FAIL_CLOSED,
            }:
                runtime.recovery_attempt_count += 1
            runtime.last_recovery_error = decision.error_code
            runtime.updated_at = observed_at

    def list_recovery_candidates(
        self,
        *,
        observed_at: datetime,
        limit: int,
    ) -> tuple[TaskRuntimeSnapshot, ...]:
        """Return a bounded deterministic candidate set; Stage G owns reconciliation policy."""
        if limit < 1 or limit > 1000:
            raise ValueError("recovery candidate limit must be between 1 and 1000")
        observed_at = _as_utc(observed_at)
        with self._database.session() as session:
            ids = tuple(
                session.execute(
                    select(WorkflowTaskRuntimeRow.tenant_id, WorkflowTaskRuntimeRow.task_id)
                    .outerjoin(
                        WorkflowLeaseRow,
                        (WorkflowLeaseRow.tenant_id == WorkflowTaskRuntimeRow.tenant_id)
                        & (WorkflowLeaseRow.task_id == WorkflowTaskRuntimeRow.task_id),
                    )
                    .where(
                        or_(
                            WorkflowTaskRuntimeRow.runtime_status == RuntimeStatus.READY.value,
                            (
                                WorkflowTaskRuntimeRow.runtime_status
                                == RuntimeStatus.WAITING_RETRY.value
                            )
                            & (WorkflowTaskRuntimeRow.retry_not_before <= observed_at),
                            (WorkflowTaskRuntimeRow.runtime_status == RuntimeStatus.LEASED.value)
                            & (WorkflowLeaseRow.expires_at <= observed_at),
                        )
                    )
                    .order_by(
                        WorkflowTaskRuntimeRow.updated_at,
                        WorkflowTaskRuntimeRow.tenant_id,
                        WorkflowTaskRuntimeRow.task_id,
                    )
                    .limit(limit)
                )
            )
            return tuple(
                self._snapshot_in_session(session, task_id=task_id, tenant_id=tenant_id)
                for tenant_id, task_id in ids
            )

    def start_runtime_attempt(self, lease: ExecutionLease) -> RuntimeAttempt:
        """Persist one RUNNING host attempt after proving current execution authority."""
        with self._database.session() as session:
            now = self._now(session)
            self._assert_fenced_authority(session, lease, observed_at=now)
            latest = session.scalar(
                select(func.max(TaskRuntimeAttemptRow.runtime_attempt)).where(
                    TaskRuntimeAttemptRow.tenant_id == lease.tenant_id,
                    TaskRuntimeAttemptRow.task_id == lease.task_id,
                    TaskRuntimeAttemptRow.execution_generation == lease.execution_generation,
                )
            )
            attempt = RuntimeAttempt(
                tenant_id=lease.tenant_id,
                task_id=lease.task_id,
                dispatch_id=lease.dispatch_id,
                execution_generation=lease.execution_generation,
                runtime_attempt=int(latest or 0) + 1,
                status=RuntimeAttemptStatus.RUNNING,
                started_at=now,
            )
            session.add(_runtime_attempt_row(attempt))
            return attempt

    def finish_runtime_attempt(
        self,
        attempt: RuntimeAttempt,
        *,
        status: RuntimeAttemptStatus,
        error_code: str | None = None,
    ) -> RuntimeAttempt:
        """Finish one exact persisted runtime attempt; RUNNING is not a terminal disposition."""
        if status is RuntimeAttemptStatus.RUNNING:
            raise ValueError("runtime attempt terminal status is required")
        with self._database.session() as session:
            row = session.scalar(
                select(TaskRuntimeAttemptRow)
                .where(
                    TaskRuntimeAttemptRow.tenant_id == attempt.tenant_id,
                    TaskRuntimeAttemptRow.task_id == attempt.task_id,
                    TaskRuntimeAttemptRow.execution_generation == attempt.execution_generation,
                    TaskRuntimeAttemptRow.runtime_attempt == attempt.runtime_attempt,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError(attempt.runtime_attempt)
            if row.status != RuntimeAttemptStatus.RUNNING.value:
                existing = _runtime_attempt(row)
                expected = attempt.model_copy(
                    update={
                        "status": status,
                        "completed_at": existing.completed_at,
                        "error_code": error_code,
                    }
                )
                if existing.status is status and existing.error_code == expected.error_code:
                    return existing
                raise DispatchConflictError("Runtime attempt already has a different outcome")
            row.status = status.value
            row.completed_at = self._now(session)
            row.error_code = error_code
            session.flush()
            return _runtime_attempt(row)

    def acknowledge_dispatch(self, dispatch: TaskDispatch) -> DispatchRecord:
        """Durably acknowledge one logical dispatch after a Worker outcome or verified no-op."""
        with self._database.session() as session:
            row = session.scalar(
                select(TaskDispatchRow)
                .where(
                    TaskDispatchRow.tenant_id == dispatch.tenant_id,
                    TaskDispatchRow.dispatch_id == dispatch.dispatch_id,
                )
                .with_for_update()
            )
            if row is None or _dispatch_record(row).dispatch != dispatch:
                raise DispatchConflictError("Dispatch acknowledgement identity mismatch")
            if row.status == DispatchStatus.ENQUEUED.value:
                row.status = DispatchStatus.ACKNOWLEDGED.value
                row.updated_at = self._now(session)
                row.last_error_code = None
            elif row.status not in {
                DispatchStatus.ACKNOWLEDGED.value,
                DispatchStatus.SUPERSEDED.value,
                DispatchStatus.DEAD_LETTERED.value,
            }:
                raise DispatchConflictError("Dispatch is not eligible for acknowledgement")
            session.flush()
            return _dispatch_record(row)

    def supersede_dispatch(self, dispatch: TaskDispatch, *, reason_code: str) -> DispatchRecord:
        """Mark a tenant-valid but non-current ENQUEUED dispatch as a durable no-op."""
        with self._database.session() as session:
            row = session.scalar(
                select(TaskDispatchRow)
                .where(
                    TaskDispatchRow.tenant_id == dispatch.tenant_id,
                    TaskDispatchRow.dispatch_id == dispatch.dispatch_id,
                )
                .with_for_update()
            )
            if row is None or _dispatch_record(row).dispatch != dispatch:
                raise DispatchConflictError("Dispatch supersession identity mismatch")
            if row.status == DispatchStatus.ENQUEUED.value:
                row.status = DispatchStatus.SUPERSEDED.value
                row.updated_at = self._now(session)
                row.last_error_code = reason_code
            session.flush()
            return _dispatch_record(row)

    def schedule_runtime_retry(
        self,
        lease: ExecutionLease,
        *,
        retry_at: datetime,
        error_code: str,
    ) -> int:
        """Atomically release exact authority and schedule the same dispatch/generation."""
        retry_at = _as_utc(retry_at)
        with self._database.session() as session:
            now = self._now(session)
            row = self._assert_fenced_authority(session, lease, observed_at=now)
            runtime = session.scalar(
                select(WorkflowTaskRuntimeRow)
                .where(
                    WorkflowTaskRuntimeRow.tenant_id == lease.tenant_id,
                    WorkflowTaskRuntimeRow.task_id == lease.task_id,
                )
                .with_for_update()
            )
            dispatch = session.scalar(
                select(TaskDispatchRow)
                .where(
                    TaskDispatchRow.tenant_id == lease.tenant_id,
                    TaskDispatchRow.dispatch_id == lease.dispatch_id,
                )
                .with_for_update()
            )
            if runtime is None or dispatch is None:
                raise LeaseLostError("Runtime retry scope no longer exists")
            if dispatch.status != DispatchStatus.ENQUEUED.value:
                raise DispatchConflictError("Only an ENQUEUED dispatch can schedule retry")
            available_at = max(now, retry_at)
            runtime.runtime_status = RuntimeStatus.WAITING_RETRY.value
            runtime.retry_not_before = available_at
            runtime.recovery_attempt_count += 1
            runtime.last_recovery_error = error_code
            runtime.updated_at = now
            dispatch.status = DispatchStatus.RETRY_SCHEDULED.value
            dispatch.available_at = available_at
            dispatch.last_error_code = error_code
            dispatch.updated_at = now
            session.delete(row)
            attempts = runtime.recovery_attempt_count
        self._observability.increment("runtime_retry_count")
        return attempts

    def _snapshot_in_session(
        self,
        session: Session,
        *,
        task_id: str,
        tenant_id: str,
    ) -> TaskRuntimeSnapshot:
        task = session.scalar(
            select(WorkflowTaskRow).where(
                WorkflowTaskRow.tenant_id == tenant_id,
                WorkflowTaskRow.task_id == task_id,
            )
        )
        runtime = session.scalar(
            select(WorkflowTaskRuntimeRow).where(
                WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                WorkflowTaskRuntimeRow.task_id == task_id,
            )
        )
        if task is None or runtime is None:
            raise KeyError(task_id)
        state = TaskState.model_validate_json(task.state_json)
        dispatch = (
            session.scalar(
                select(TaskDispatchRow).where(
                    TaskDispatchRow.tenant_id == tenant_id,
                    TaskDispatchRow.dispatch_id == runtime.current_dispatch_id,
                )
            )
            if runtime.current_dispatch_id is not None
            else None
        )
        lease_row = session.scalar(
            select(WorkflowLeaseRow).where(
                WorkflowLeaseRow.tenant_id == tenant_id,
                WorkflowLeaseRow.task_id == task_id,
            )
        )
        pending = tuple(
            session.scalars(
                select(WorkflowApprovalRow).where(
                    WorkflowApprovalRow.tenant_id == tenant_id,
                    WorkflowApprovalRow.task_id == task_id,
                    WorkflowApprovalRow.status == ApprovalStatus.PENDING.value,
                )
            )
        )
        if len(pending) > 1:
            raise DispatchConflictError("Task has more than one pending approval")
        pending_clarifications = tuple(
            session.scalars(
                select(WorkflowClarificationRow).where(
                    WorkflowClarificationRow.tenant_id == tenant_id,
                    WorkflowClarificationRow.task_id == task_id,
                    WorkflowClarificationRow.status == ClarificationStatus.PENDING.value,
                )
            )
        )
        if len(pending_clarifications) > 1:
            raise DispatchConflictError("Task has more than one pending clarification")
        plan_version: int | None = None
        if task.plan_json:
            raw_plan = json.loads(task.plan_json)
            plan_version = int(raw_plan["planning_version"])
        successful_steps = tuple(
            session.scalars(
                select(WorkflowStepResultRow.step_id)
                .where(
                    WorkflowStepResultRow.tenant_id == tenant_id,
                    WorkflowStepResultRow.task_id == task_id,
                )
                .order_by(WorkflowStepResultRow.sequence_id)
            )
        )
        return TaskRuntimeSnapshot(
            tenant_id=tenant_id,
            task_id=task_id,
            task_status=state.state,
            task_version=state.version,
            runtime_status=RuntimeStatus(runtime.runtime_status),
            execution_generation=runtime.execution_generation,
            predecessor_execution_generation=runtime.predecessor_execution_generation,
            resume_checkpoint_id=runtime.resume_checkpoint_id,
            plan_version=plan_version,
            current_dispatch_id=runtime.current_dispatch_id,
            dispatch_status=DispatchStatus(dispatch.status) if dispatch else None,
            retry_not_before=(
                _as_utc(runtime.retry_not_before) if runtime.retry_not_before else None
            ),
            lease=_execution_lease(lease_row) if lease_row else None,
            cancellation=(
                CancellationState.model_validate_json(runtime.cancellation_json)
                if runtime.cancellation_json
                else None
            ),
            pending_approval_id=pending[0].approval_id if pending else None,
            pending_approval_status=ApprovalStatus.PENDING if pending else None,
            pending_clarification_id=(
                pending_clarifications[0].clarification_id if pending_clarifications else None
            ),
            pending_clarification_status=(
                ClarificationStatus.PENDING if pending_clarifications else None
            ),
            successful_step_ids=successful_steps,
            recovery_attempt_count=runtime.recovery_attempt_count,
            last_recovery_error=runtime.last_recovery_error,
        )

    def _assert_fenced_authority(
        self,
        session: Session,
        lease: ExecutionLease,
        *,
        observed_at: datetime,
    ) -> WorkflowLeaseRow:
        runtime = session.scalar(
            select(WorkflowTaskRuntimeRow)
            .where(
                WorkflowTaskRuntimeRow.tenant_id == lease.tenant_id,
                WorkflowTaskRuntimeRow.task_id == lease.task_id,
            )
            .with_for_update()
        )
        if runtime is None:
            raise LeaseLostError("Worker runtime scope no longer exists")
        if runtime.execution_generation != lease.execution_generation:
            raise StaleExecutionGenerationError("Worker execution generation is stale")
        if runtime.fencing_counter != lease.fencing_token:
            raise StaleFencingTokenError("Worker fencing token is stale")
        task = session.scalar(
            select(WorkflowTaskRow).where(
                WorkflowTaskRow.tenant_id == lease.tenant_id,
                WorkflowTaskRow.task_id == lease.task_id,
            )
        )
        if task is None:
            raise LeaseLostError("Worker Task scope no longer exists")
        state = TaskState.model_validate_json(task.state_json)
        if state.state in _TERMINAL:
            raise TaskAlreadyTerminalError("Terminal Task state cannot be overwritten")
        row = session.scalar(
            select(WorkflowLeaseRow)
            .where(
                WorkflowLeaseRow.tenant_id == lease.tenant_id,
                WorkflowLeaseRow.task_id == lease.task_id,
            )
            .with_for_update()
        )
        if row is None or not _lease_identity_matches(row, lease):
            raise LeaseLostError("Worker no longer owns the current execution lease")
        if observed_at >= _as_utc(row.expires_at):
            raise LeaseExpiredError("Worker execution lease has expired")
        return row

    def _revoke_pending_approvals(
        self,
        session: Session,
        request: CancellationRequest,
        finalized_at: datetime,
    ) -> None:
        rows = tuple(
            session.scalars(
                select(WorkflowApprovalRow)
                .where(
                    WorkflowApprovalRow.tenant_id == request.tenant_id,
                    WorkflowApprovalRow.task_id == request.task_id,
                    WorkflowApprovalRow.status == ApprovalStatus.PENDING.value,
                )
                .with_for_update()
            )
        )
        for row in rows:
            pending = ApprovalRequest.model_validate_json(row.payload_json)
            revoked = pending.model_copy(
                update={
                    "status": ApprovalStatus.REVOKED,
                    "approver": request.requested_by,
                    "decided_at": finalized_at,
                    "resolution_reason": request.reason_code,
                    "version": pending.version + 1,
                }
            )
            row.status = revoked.status.value
            row.version = revoked.version
            row.payload_json = revoked.model_dump_json()
            session.add(
                WorkflowApprovalHistoryRow(
                    approval_id=revoked.approval_id,
                    version=revoked.version,
                    tenant_id=request.tenant_id,
                    payload_json=revoked.model_dump_json(),
                )
            )

    def _cancel_pending_clarifications(
        self,
        session: Session,
        request: CancellationRequest,
        finalized_at: datetime,
    ) -> None:
        rows = tuple(
            session.scalars(
                select(WorkflowClarificationRow)
                .where(
                    WorkflowClarificationRow.tenant_id == request.tenant_id,
                    WorkflowClarificationRow.task_id == request.task_id,
                    WorkflowClarificationRow.status.in_(
                        (
                            ClarificationStatus.PENDING.value,
                            ClarificationStatus.SUBMITTED.value,
                        )
                    ),
                )
                .with_for_update()
            )
        )
        for row in rows:
            pending = TaskClarification.model_validate_json(row.payload_json)
            cancelled = pending.model_copy(
                update={
                    "status": ClarificationStatus.CANCELLED,
                    "resolved_at": finalized_at,
                    "resolution_code": request.reason_code,
                    "version": pending.version + 1,
                }
            )
            row.status = cancelled.status.value
            row.version = cancelled.version
            row.active_task_id = None
            row.payload_json = cancelled.model_dump_json()
            row.resolved_at = finalized_at
            session.add(
                WorkflowClarificationHistoryRow(
                    clarification_id=cancelled.clarification_id,
                    version=cancelled.version,
                    tenant_id=request.tenant_id,
                    payload_json=cancelled.model_dump_json(),
                )
            )

    def _get_by_generation(
        self,
        *,
        tenant_id: str,
        task_id: str,
        execution_generation: int,
    ) -> DispatchRecord:
        with self._database.session() as session:
            row = session.scalar(
                select(TaskDispatchRow).where(
                    TaskDispatchRow.tenant_id == tenant_id,
                    TaskDispatchRow.task_id == task_id,
                    TaskDispatchRow.execution_generation == execution_generation,
                )
            )
            if row is None:
                raise KeyError(task_id)
            return _dispatch_record(row)

    def _load_reused_submission(
        self,
        idempotency: SubmissionIdempotency,
    ) -> TaskSubmissionResponse | None:
        with self._database.session() as session:
            row = self._idempotency_row(session, idempotency)
            return self._reuse_idempotency(row, idempotency) if row is not None else None

    @staticmethod
    def _idempotency_row(
        session: Session,
        idempotency: SubmissionIdempotency,
    ) -> TaskSubmissionIdempotencyRow | None:
        return session.scalar(
            select(TaskSubmissionIdempotencyRow).where(
                TaskSubmissionIdempotencyRow.tenant_id == idempotency.tenant_id,
                TaskSubmissionIdempotencyRow.caller_id == idempotency.caller_id,
                TaskSubmissionIdempotencyRow.idempotency_key == idempotency.idempotency_key,
            )
        )

    @staticmethod
    def _reuse_idempotency(
        row: TaskSubmissionIdempotencyRow,
        idempotency: SubmissionIdempotency,
    ) -> TaskSubmissionResponse:
        if row.request_fingerprint != idempotency.request_fingerprint:
            raise DispatchConflictError(
                "Idempotency-Key is already bound to a different request fingerprint"
            )
        return TaskSubmissionResponse.model_validate_json(row.response_json)

    @staticmethod
    def _validate_submission(
        request: TaskRequest,
        state: TaskState,
        dispatch: TaskDispatch,
        response: TaskSubmissionResponse,
        idempotency: SubmissionIdempotency | None,
    ) -> None:
        if state.state is not TaskStatus.CREATED or state.version != 1:
            raise ValueError("asynchronous submission requires the initial CREATED v1 state")
        if dispatch.task_id != state.task_id or response.task_id != state.task_id:
            raise ValueError("Task, dispatch, and acceptance response identities must match")
        if dispatch.trace_id != response.trace_id:
            raise ValueError("dispatch and acceptance trace identities must match")
        if dispatch.execution_generation != 1 or dispatch.expected_task_version != state.version:
            raise ValueError("initial dispatch must bind CREATED v1 as execution generation 1")
        if dispatch.enqueued_at != response.accepted_at:
            raise ValueError("initial dispatch and acceptance must share the commit intent time")
        if idempotency is not None and idempotency.tenant_id != dispatch.tenant_id:
            raise ValueError("idempotency scope must match dispatch tenant")
        if idempotency is not None and idempotency.caller_id != request.user_id:
            raise ValueError("idempotency caller must match the authenticated Task requester")

    def _now(self, session: Session) -> datetime:
        return _as_utc(self._database_clock(session))


def _pending_record(dispatch: TaskDispatch, created_at: datetime) -> DispatchRecord:
    return DispatchRecord(
        dispatch=dispatch,
        status=DispatchStatus.PENDING,
        attempt_count=0,
        created_at=created_at,
        updated_at=created_at,
    )


def _dispatch_row(record: DispatchRecord) -> TaskDispatchRow:
    dispatch = record.dispatch
    return TaskDispatchRow(
        tenant_id=dispatch.tenant_id,
        dispatch_id=dispatch.dispatch_id,
        task_id=dispatch.task_id,
        execution_generation=dispatch.execution_generation,
        predecessor_execution_generation=dispatch.predecessor_execution_generation,
        resume_checkpoint_id=dispatch.resume_checkpoint_id,
        expected_task_version=dispatch.expected_task_version,
        trace_id=dispatch.trace_id,
        status=record.status.value,
        available_at=dispatch.not_before,
        attempt_count=record.attempt_count,
        last_error_code=record.last_error_code,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _dispatch_record(row: TaskDispatchRow) -> DispatchRecord:
    created_at = _as_utc(row.created_at)
    return DispatchRecord(
        dispatch=TaskDispatch(
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
        ),
        status=DispatchStatus(row.status),
        attempt_count=row.attempt_count,
        created_at=created_at,
        updated_at=_as_utc(row.updated_at),
        last_error_code=row.last_error_code,
    )


def _validate_dispatch_record(record: DispatchRecord) -> None:
    if record.created_at != record.dispatch.enqueued_at:
        raise ValueError("dispatch created_at must equal immutable enqueue-intent time")
    if record.status is DispatchStatus.PENDING and record.attempt_count != 0:
        raise ValueError("new PENDING dispatch cannot have publish attempts")


def _execution_lease(row: WorkflowLeaseRow) -> ExecutionLease:
    return ExecutionLease(
        tenant_id=row.tenant_id,
        task_id=row.task_id,
        dispatch_id=row.dispatch_id,
        execution_generation=row.execution_generation,
        task_version=row.task_version,
        worker_id=row.worker_id,
        lease_id=row.lease_id,
        fencing_token=row.fencing_token,
        acquired_at=_as_utc(row.acquired_at),
        heartbeat_at=_as_utc(row.heartbeat_at),
        expires_at=_as_utc(row.expires_at),
    )


def _lease_identity_matches(row: WorkflowLeaseRow, lease: ExecutionLease) -> bool:
    return (
        row.dispatch_id == lease.dispatch_id
        and row.execution_generation == lease.execution_generation
        and row.worker_id == lease.worker_id
        and row.lease_id == lease.lease_id
        and row.fencing_token == lease.fencing_token
    )


def _lease_result(
    status: LeaseAcquisitionStatus,
    reason_code: str,
    *,
    current_fencing_token: int | None = None,
) -> LeaseAcquisitionResult:
    return LeaseAcquisitionResult(
        status=status,
        reason_code=reason_code,
        current_fencing_token=current_fencing_token,
    )


def _runtime_status_after_release(status: TaskStatus) -> RuntimeStatus:
    if status in _TERMINAL:
        return RuntimeStatus.FINISHED
    if status in {TaskStatus.WAITING_APPROVAL, TaskStatus.WAITING_CLARIFICATION}:
        return RuntimeStatus.SUSPENDED
    return RuntimeStatus.READY


def _runtime_attempt_row(attempt: RuntimeAttempt) -> TaskRuntimeAttemptRow:
    return TaskRuntimeAttemptRow(
        tenant_id=attempt.tenant_id,
        task_id=attempt.task_id,
        dispatch_id=attempt.dispatch_id,
        execution_generation=attempt.execution_generation,
        runtime_attempt=attempt.runtime_attempt,
        status=attempt.status.value,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        error_code=attempt.error_code,
    )


def _runtime_attempt(row: TaskRuntimeAttemptRow) -> RuntimeAttempt:
    return RuntimeAttempt(
        tenant_id=row.tenant_id,
        task_id=row.task_id,
        dispatch_id=row.dispatch_id,
        execution_generation=row.execution_generation,
        runtime_attempt=row.runtime_attempt,
        status=RuntimeAttemptStatus(row.status),
        started_at=_as_utc(row.started_at),
        completed_at=_as_utc(row.completed_at) if row.completed_at else None,
        error_code=row.error_code,
    )


def _cancellation_event_id(request: CancellationRequest) -> str:
    digest = hashlib.sha256(
        f"{request.tenant_id}:{request.task_id}:{request.request_id}".encode()
    ).hexdigest()[:32]
    return f"EVT-CANCEL-{digest}"


def _database_now(session: Session) -> datetime:
    value = session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        raise RuntimeError("database did not return a timestamp")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["AsyncRuntimeRepository", "DatabaseClock"]
