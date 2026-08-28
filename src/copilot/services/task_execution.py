"""Independent Worker execution boundary for durable Task dispatches."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from threading import Event, Lock, Thread
from typing import Protocol

from copilot.agent.graph import LangGraphWorkflowEngine, WorkflowInterrupted
from copilot.contracts import ApprovalRequest, ApprovalStatus, TaskStatus
from copilot.contracts.async_runtime import (
    DispatchStatus,
    ExecutionLease,
    LeaseAcquisitionStatus,
    LeaseTimingPolicy,
    QueueDelivery,
    RuntimeAttemptStatus,
    RuntimeRetryPolicy,
    TaskRuntimeSnapshot,
    WorkerIdentity,
    runtime_retry_delay_seconds,
)
from copilot.contracts.errors import (
    LeaseLostError,
    RuntimeContractError,
    TaskAlreadyTerminalError,
)
from copilot.services.async_runtime import TaskQueue, WorkerRuntimeRepository
from copilot.services.execution_authority import bind_execution_authority
from copilot.services.task_submission import trusted_context_from_request
from copilot.services.workflows.ports import WorkflowRepository
from copilot.tools.cancellation import InvocationCancellationRegistry

LOGGER = logging.getLogger(__name__)
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class ApprovalReader(Protocol):
    """Resolved approval read boundary used only for an approval-resume dispatch."""

    def get(self, approval_id: str, *, tenant_id: str) -> ApprovalRequest: ...


class TaskExecutionService:
    """Host one dispatch using repository authority, lease, heartbeat, and fencing."""

    def __init__(
        self,
        *,
        runtime: WorkerRuntimeRepository,
        tasks: WorkflowRepository,
        approvals: ApprovalReader,
        queue: TaskQueue,
        engine: LangGraphWorkflowEngine,
        worker: WorkerIdentity,
        cancellations: InvocationCancellationRegistry,
        clock: Callable[[], datetime],
        lease_timing: LeaseTimingPolicy | None = None,
        retry_policy: RuntimeRetryPolicy | None = None,
    ) -> None:
        self._runtime = runtime
        self._tasks = tasks
        self._approvals = approvals
        self._queue = queue
        self._engine = engine
        self._worker = worker
        self._cancellations = cancellations
        self._clock = clock
        self._lease_timing = lease_timing or LeaseTimingPolicy()
        self._retry_policy = retry_policy or RuntimeRetryPolicy()

    def process(self, delivery: QueueDelivery) -> str:
        """Process one Queue receipt and return a stable operational disposition."""
        dispatch = delivery.dispatch
        try:
            snapshot = self._runtime.snapshot(dispatch.task_id, tenant_id=dispatch.tenant_id)
        except KeyError:
            LOGGER.warning(
                "Cross-scope or missing Task dispatch rejected",
                extra={
                    "event": "duplicate_dispatch_ignored",
                    "tenant_id": dispatch.tenant_id,
                    "task_id": dispatch.task_id,
                    "dispatch_id": dispatch.dispatch_id,
                    "worker_id": self._worker.worker_id,
                    "status": "REJECTED",
                    "error_code": "TASK_NOT_FOUND",
                },
            )
            return "REJECTED"
        if snapshot.task_status in _TERMINAL or snapshot.cancellation is not None:
            self._acknowledge_noop(delivery, snapshot, reason="TASK_TERMINAL")
            return "NO_OP_TERMINAL"
        if snapshot.task_status is TaskStatus.WAITING_APPROVAL:
            self._acknowledge_noop(delivery, snapshot, reason="TASK_SUSPENDED")
            return "NO_OP_SUSPENDED"
        if (
            snapshot.current_dispatch_id != dispatch.dispatch_id
            or snapshot.execution_generation != dispatch.execution_generation
        ):
            if snapshot.dispatch_status is DispatchStatus.ENQUEUED:
                self._runtime.supersede_dispatch(dispatch, reason_code="DISPATCH_NOT_CURRENT")
            self._queue.ack(delivery)
            return "NO_OP_STALE"

        acquisition = self._runtime.try_acquire_lease(
            dispatch,
            self._worker,
            timing=self._lease_timing,
        )
        if acquisition.status is not LeaseAcquisitionStatus.ACQUIRED:
            if acquisition.status is LeaseAcquisitionStatus.CONFLICT:
                self._queue.ack(delivery)
                return "NO_OP_LEASE_CONFLICT"
            if acquisition.status in {
                LeaseAcquisitionStatus.TERMINAL,
                LeaseAcquisitionStatus.CANCELLED,
                LeaseAcquisitionStatus.STALE_DISPATCH,
            }:
                self._queue.ack(delivery)
                return f"NO_OP_{acquisition.status.value}"
            self._queue.nack(delivery, retry_at=None, reason_code=acquisition.reason_code)
            return "RETRY_LEASE"
        lease = acquisition.lease
        assert lease is not None
        attempt = self._runtime.start_runtime_attempt(lease)
        heartbeat = LeaseHeartbeat(
            runtime=self._runtime,
            lease=lease,
            timing=self._lease_timing,
            cancellations=self._cancellations,
        )
        LOGGER.info(
            "Worker execution started",
            extra=self._fields(delivery, lease, attempt.runtime_attempt, "RUNNING"),
        )
        heartbeat.start()
        try:
            with bind_execution_authority(lease):
                self._execute_reconciled(snapshot, dispatch.execution_generation)
            heartbeat.stop()
            if heartbeat.authority_lost:
                raise LeaseLostError("Heartbeat lost execution authority")
        except WorkflowInterrupted:
            heartbeat.stop()
            current_lease = heartbeat.lease
            self._runtime.finish_runtime_attempt(
                attempt,
                status=RuntimeAttemptStatus.SUSPENDED,
            )
            self._runtime.release(current_lease)
            self._runtime.acknowledge_dispatch(dispatch)
            self._queue.ack(delivery)
            LOGGER.info(
                "Task suspended for approval",
                extra=self._fields(delivery, current_lease, attempt.runtime_attempt, "SUSPENDED"),
            )
            return "SUSPENDED"
        except LeaseLostError:
            heartbeat.stop()
            self._runtime.finish_runtime_attempt(
                attempt,
                status=RuntimeAttemptStatus.LOST,
                error_code="LEASE_LOST",
            )
            if self._terminal_after_authority_loss(delivery, lease=heartbeat.lease):
                return "NO_OP_TERMINAL"
            LOGGER.warning(
                "Worker execution authority lost",
                extra=self._fields(delivery, heartbeat.lease, attempt.runtime_attempt, "LOST"),
            )
            return "LEASE_LOST"
        except Exception as exc:
            heartbeat.stop()
            current_lease = heartbeat.lease
            error_code = _runtime_error_code(exc)
            self._runtime.finish_runtime_attempt(
                attempt,
                status=RuntimeAttemptStatus.FAILED,
                error_code=error_code,
            )
            if self._terminal_after_authority_loss(delivery, lease=current_lease):
                return "NO_OP_TERMINAL"
            completed_attempts = max(1, snapshot.recovery_attempt_count + 1)
            delay = runtime_retry_delay_seconds(self._retry_policy, completed_attempts)
            self._runtime.schedule_runtime_retry(
                current_lease,
                retry_at=self._clock() + timedelta(seconds=delay),
                error_code=error_code,
            )
            self._queue.ack(delivery)
            LOGGER.exception(
                "Worker execution scheduled for runtime retry",
                extra=self._fields(delivery, current_lease, attempt.runtime_attempt, "RETRYING"),
            )
            return "RETRY_SCHEDULED"

        current_lease = heartbeat.lease
        self._runtime.finish_runtime_attempt(attempt, status=RuntimeAttemptStatus.SUCCEEDED)
        self._runtime.release(current_lease)
        self._runtime.acknowledge_dispatch(dispatch)
        self._queue.ack(delivery)
        LOGGER.info(
            "Worker execution ended",
            extra=self._fields(delivery, current_lease, attempt.runtime_attempt, "SUCCEEDED"),
        )
        return "SUCCEEDED"

    def _terminal_after_authority_loss(
        self,
        delivery: QueueDelivery,
        *,
        lease: ExecutionLease,
    ) -> bool:
        """Turn cancellation/terminal races into durable no-ops without scheduling recovery."""
        dispatch = delivery.dispatch
        latest = self._tasks.state_for(dispatch.task_id, tenant_id=dispatch.tenant_id)
        if latest.state not in _TERMINAL:
            return False
        if latest.state is TaskStatus.CANCELLED:
            self._runtime.observe_cancellation(
                dispatch.task_id,
                tenant_id=dispatch.tenant_id,
                worker_id=self._worker.worker_id,
            )
        # A heartbeat can observe the terminal Task after the Graph commits its result but
        # before the normal success path releases the lease. Release only the exact fenced
        # identity here; a replacement lease or cancellation-owned deletion remains untouched.
        self._runtime.release(lease)
        self._runtime.acknowledge_dispatch(dispatch)
        self._queue.ack(delivery)
        LOGGER.info(
            "Terminal Task won the Worker execution race",
            extra={
                "event": (
                    "cancel_observed" if latest.state is TaskStatus.CANCELLED else "task_finalized"
                ),
                "tenant_id": dispatch.tenant_id,
                "task_id": dispatch.task_id,
                "trace_id": dispatch.trace_id,
                "dispatch_id": dispatch.dispatch_id,
                "worker_id": self._worker.worker_id,
                "status": "NO_OP",
            },
        )
        return True

    def _execute_reconciled(self, original: TaskRuntimeSnapshot, generation: int) -> None:
        task_id = original.task_id
        tenant_id = original.tenant_id
        current = self._runtime.snapshot(task_id, tenant_id=tenant_id)
        checkpoint = self._engine.checkpoint_identity(task_id, tenant_id)
        if checkpoint is None:
            if current.task_status is not TaskStatus.CREATED or current.successful_step_ids:
                raise RuntimeError("CHECKPOINT_REQUIRED_FOR_TAKEOVER")
            request = self._tasks.request_for(task_id, tenant_id=tenant_id)
            context = trusted_context_from_request(request)
            if context.task_id != task_id or context.tenant_id != tenant_id:
                raise RuntimeError("TRUSTED_CONTEXT_SCOPE_MISMATCH")
            self._engine.execute_dispatched(
                request,
                context,
                execution_generation=generation,
            )
            return
        if checkpoint.task_version > current.task_version:
            raise RuntimeError("CHECKPOINT_AHEAD_OF_TASK_DB")
        if checkpoint.plan_version != current.plan_version:
            raise RuntimeError("CHECKPOINT_PLAN_MISMATCH")
        if not set(checkpoint.successful_step_ids).issubset(current.successful_step_ids):
            raise RuntimeError("CHECKPOINT_SUCCESS_SET_AHEAD")
        dispatch = self._runtime.get(
            current.current_dispatch_id or "", tenant_id=tenant_id
        ).dispatch
        if dispatch.resume_checkpoint_id is not None:
            if (
                checkpoint.checkpoint_id != dispatch.resume_checkpoint_id
                or checkpoint.execution_generation != dispatch.predecessor_execution_generation
                or generation != checkpoint.execution_generation + 1
            ):
                raise RuntimeError("CHECKPOINT_GENERATION_MISMATCH")
            approval_state = self._engine.approval_state(task_id, tenant_id)
            approval_id = approval_state.get("approval_id")
            if not isinstance(approval_id, str):
                raise RuntimeError("APPROVAL_BINDING_MISSING")
            approval = self._approvals.get(approval_id, tenant_id=tenant_id)
            if approval.status is not ApprovalStatus.APPROVED:
                raise RuntimeError("APPROVAL_NOT_APPROVED")
            self._engine.resume_approval_dispatched(
                approval,
                tenant_id,
                execution_generation=generation,
            )
            return
        if checkpoint.execution_generation != generation:
            raise RuntimeError("CHECKPOINT_GENERATION_MISMATCH")
        self._engine.resume_dispatched(
            task_id,
            tenant_id,
            execution_generation=generation,
        )

    def _acknowledge_noop(
        self,
        delivery: QueueDelivery,
        snapshot: TaskRuntimeSnapshot,
        *,
        reason: str,
    ) -> None:
        if snapshot.dispatch_status is DispatchStatus.ENQUEUED:
            self._runtime.acknowledge_dispatch(delivery.dispatch)
        self._queue.ack(delivery)
        LOGGER.info(
            "Duplicate or terminal dispatch ignored",
            extra={
                "event": "duplicate_dispatch_ignored",
                "tenant_id": delivery.dispatch.tenant_id,
                "task_id": delivery.dispatch.task_id,
                "trace_id": delivery.dispatch.trace_id,
                "dispatch_id": delivery.dispatch.dispatch_id,
                "worker_id": self._worker.worker_id,
                "execution_generation": delivery.dispatch.execution_generation,
                "status": "NO_OP",
                "error_code": reason,
            },
        )

    def _fields(
        self,
        delivery: QueueDelivery,
        lease: ExecutionLease,
        runtime_attempt: int,
        status: str,
    ) -> dict[str, object]:
        return {
            "event": "worker_execution_end" if status != "RUNNING" else "worker_execution_start",
            "tenant_id": delivery.dispatch.tenant_id,
            "task_id": delivery.dispatch.task_id,
            "trace_id": delivery.dispatch.trace_id,
            "dispatch_id": delivery.dispatch.dispatch_id,
            "worker_id": self._worker.worker_id,
            "lease_id": lease.lease_id,
            "fencing_token": lease.fencing_token,
            "execution_generation": lease.execution_generation,
            "runtime_attempt": runtime_attempt,
            "status": status,
        }


class LeaseHeartbeat:
    """Renew one exact lease until stopped; signal local cancellation on authority loss."""

    def __init__(
        self,
        *,
        runtime: WorkerRuntimeRepository,
        lease: ExecutionLease,
        timing: LeaseTimingPolicy,
        cancellations: InvocationCancellationRegistry,
    ) -> None:
        self._runtime = runtime
        self._lease = lease
        self._timing = timing
        self._cancellations = cancellations
        self._stop = Event()
        self._lost = Event()
        self._lock = Lock()
        self._thread = Thread(
            target=self._run,
            name=f"lease-heartbeat-{lease.task_id}",
            daemon=True,
        )

    @property
    def lease(self) -> ExecutionLease:
        with self._lock:
            return self._lease

    @property
    def authority_lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1, self._timing.heartbeat_interval_seconds))

    def _run(self) -> None:
        while not self._stop.wait(self._timing.heartbeat_interval_seconds):
            try:
                renewed = self._runtime.heartbeat(self.lease, timing=self._timing)
            except TaskAlreadyTerminalError:
                self._lost.set()
                self._cancellations.cancel_task(
                    self.lease.task_id,
                    reason="Authoritative Task reached a terminal state",
                )
                return
            except Exception:
                self._lost.set()
                self._cancellations.cancel_task(
                    self.lease.task_id,
                    reason="Execution lease heartbeat was rejected",
                )
                return
            with self._lock:
                self._lease = renewed


def _runtime_error_code(error: Exception) -> str:
    if isinstance(error, RuntimeContractError):
        return error.code
    message = str(error)
    if message and message.replace("_", "").isalnum() and message.upper() == message:
        return message[:200]
    return type(error).__name__.upper()[:200]


__all__ = ["LeaseHeartbeat", "TaskExecutionService"]
