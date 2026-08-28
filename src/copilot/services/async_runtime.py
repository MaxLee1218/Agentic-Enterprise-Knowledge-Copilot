"""Application-owned ports for the future asynchronous task runtime.

The interfaces are intentionally broker- and database-vendor-neutral. No implementation in this
module starts a thread, consumes a queue, or executes a Task.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from copilot.contracts.async_runtime import (
    CancellationRequest,
    CancellationState,
    DispatchRecord,
    DispatchStatus,
    ExecutionLease,
    LeaseAcquisitionResult,
    LeaseTimingPolicy,
    QueueDelivery,
    RecoveryDecision,
    RuntimeAttempt,
    RuntimeAttemptStatus,
    SubmissionIdempotency,
    TaskDispatch,
    TaskRuntimeSnapshot,
    TaskSubmissionResponse,
    WorkerIdentity,
)
from copilot.contracts.tasks import TaskRequest, TaskState


class TaskQueue(Protocol):
    """At-least-once dispatch transport; queue receipt never grants execution ownership."""

    def enqueue(self, dispatch: TaskDispatch) -> None:
        """Publish one immutable dispatch envelope, possibly more than once."""
        ...

    def receive(
        self,
        *,
        max_messages: int,
        visibility_timeout_seconds: int,
    ) -> tuple[QueueDelivery, ...]:
        """Receive bounded deliveries without loading authoritative Task content."""
        ...

    def ack(self, delivery: QueueDelivery) -> None:
        """Acknowledge only after a durable outcome or an authoritative no-op."""
        ...

    def nack(
        self,
        delivery: QueueDelivery,
        *,
        retry_at: datetime | None,
        reason_code: str,
    ) -> None:
        """Release or delay transport delivery without deciding business retry semantics."""
        ...

    def health(self) -> bool:
        """Return whether the durable Queue dependency is reachable."""
        ...

    def shutdown(self) -> None:
        """Stop new Queue operations without disposing shared persistence."""
        ...


class TaskSubmissionRepository(Protocol):
    """Atomic Task plus initial outbox persistence and idempotency boundary."""

    def persist_task_and_dispatch(
        self,
        request: TaskRequest,
        state: TaskState,
        dispatch: TaskDispatch,
        response: TaskSubmissionResponse,
        *,
        idempotency: SubmissionIdempotency | None,
    ) -> tuple[TaskSubmissionResponse, bool]:
        """Commit Task and dispatch together; return response plus whether it was reused."""
        ...


class DispatchRepository(Protocol):
    """Durable outbox and dispatch state with compare-and-set transitions."""

    def create(self, record: DispatchRecord) -> DispatchRecord:
        """Create idempotently by tenant/task/execution generation."""
        ...

    def get(self, dispatch_id: str, *, tenant_id: str) -> DispatchRecord:
        """Load one tenant-scoped dispatch record."""
        ...

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
        """Apply one legal durable dispatch transition or fail on a race."""
        ...


class LeaseRepository(Protocol):
    """Authoritative atomic lease, heartbeat, release, and fencing boundary."""

    def try_acquire_lease(
        self,
        dispatch: TaskDispatch,
        worker: WorkerIdentity,
        *,
        timing: LeaseTimingPolicy,
    ) -> LeaseAcquisitionResult:
        """Use database time; allow one winner and increment fencing on takeover."""
        ...

    def heartbeat(
        self,
        lease: ExecutionLease,
        *,
        timing: LeaseTimingPolicy,
    ) -> ExecutionLease:
        """Renew from database time only for the exact lease/generation/fencing token."""
        ...

    def release(self, lease: ExecutionLease) -> bool:
        """Idempotently release only the exact current lease; stale release returns false."""
        ...


class RuntimeRepository(Protocol):
    """Authoritative runtime snapshot, cancellation, and fenced mutation boundary."""

    def snapshot(self, task_id: str, *, tenant_id: str) -> TaskRuntimeSnapshot:
        """Load all durable facts required for claim or recovery reconciliation."""
        ...

    def request_cancellation(self, request: CancellationRequest) -> CancellationState:
        """Atomically persist cancellation and terminal Task state; duplicate requests are safe."""
        ...

    def record_recovery_decision(
        self,
        task_id: str,
        decision: RecoveryDecision,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> None:
        """Persist recovery accounting and audit correlation before dispatching."""
        ...


class WorkerRuntimeRepository(LeaseRepository, RuntimeRepository, DispatchRepository, Protocol):
    """Complete authoritative runtime port consumed by one Worker execution host."""

    def start_runtime_attempt(self, lease: ExecutionLease) -> RuntimeAttempt: ...

    def finish_runtime_attempt(
        self,
        attempt: RuntimeAttempt,
        *,
        status: RuntimeAttemptStatus,
        error_code: str | None = None,
    ) -> RuntimeAttempt: ...

    def acknowledge_dispatch(self, dispatch: TaskDispatch) -> DispatchRecord: ...

    def supersede_dispatch(
        self,
        dispatch: TaskDispatch,
        *,
        reason_code: str,
    ) -> DispatchRecord: ...

    def schedule_runtime_retry(
        self,
        lease: ExecutionLease,
        *,
        retry_at: datetime,
        error_code: str,
    ) -> int: ...

    def observe_cancellation(
        self,
        task_id: str,
        *,
        tenant_id: str,
        worker_id: str,
    ) -> CancellationState | None: ...


class RecoveryRepository(Protocol):
    """Bounded candidate scan port; scanner policy stays in the application layer."""

    def list_recovery_candidates(
        self,
        *,
        observed_at: datetime,
        limit: int,
    ) -> tuple[TaskRuntimeSnapshot, ...]:
        """Return READY/orphaned, expired-lease, and due-retry candidates only."""
        ...


__all__ = [
    "DispatchRepository",
    "LeaseRepository",
    "RecoveryRepository",
    "RuntimeRepository",
    "TaskQueue",
    "TaskSubmissionRepository",
    "WorkerRuntimeRepository",
]
