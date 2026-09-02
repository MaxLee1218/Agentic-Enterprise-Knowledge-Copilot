"""Broker-neutral contracts for the future asynchronous task runtime.

These values freeze dispatch, lease, cancellation, retry, and recovery semantics. They do not
implement a broker, dispatcher, worker daemon, recovery scanner, or production API migration.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel
from copilot.contracts.clarifications import ClarificationStatus
from copilot.contracts.enums import ApprovalStatus, TaskStatus
from copilot.contracts.errors import (
    LeaseExpiredError,
    LeaseLostError,
    StaleExecutionGenerationError,
    StaleFencingTokenError,
    TaskAlreadyTerminalError,
)
from copilot.contracts.validators import validate_identifier, validate_utc_datetime

TERMINAL_TASK_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class RuntimeStatus(StrEnum):
    """Execution-host status kept separate from the frozen business TaskStatus."""

    READY = "READY"
    LEASED = "LEASED"
    WAITING_RETRY = "WAITING_RETRY"
    SUSPENDED = "SUSPENDED"
    FINISHED = "FINISHED"


class DispatchStatus(StrEnum):
    """Durable outbox/dispatch lifecycle; broker delivery is never execution ownership."""

    PENDING = "PENDING"
    ENQUEUED = "ENQUEUED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SUPERSEDED = "SUPERSEDED"
    DEAD_LETTERED = "DEAD_LETTERED"


class LeaseAcquisitionStatus(StrEnum):
    """Closed set of outcomes for an atomic lease acquisition attempt."""

    ACQUIRED = "ACQUIRED"
    CONFLICT = "CONFLICT"
    TERMINAL = "TERMINAL"
    CANCELLED = "CANCELLED"
    STALE_DISPATCH = "STALE_DISPATCH"


class RuntimeAttemptStatus(StrEnum):
    """Outcome of one worker hosting attempt, not a business tool attempt."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    SUSPENDED = "SUSPENDED"
    FAILED = "FAILED"
    LOST = "LOST"


class RecoveryAction(StrEnum):
    """Deterministic action selected by runtime reconciliation."""

    NO_OP = "NO_OP"
    WAIT = "WAIT"
    REDISPATCH = "REDISPATCH"
    RESUME = "RESUME"
    FAIL_CLOSED = "FAIL_CLOSED"


class RecoveryReason(StrEnum):
    """Stable reasons emitted by recovery reconciliation and scanning."""

    TERMINAL_TASK = "TERMINAL_TASK"
    CANCELLED_TASK = "CANCELLED_TASK"
    CANCELLATION_INCONSISTENT = "CANCELLATION_INCONSISTENT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    ACTIVE_LEASE = "ACTIVE_LEASE"
    RETRY_NOT_DUE = "RETRY_NOT_DUE"
    READY_WITHOUT_DISPATCH = "READY_WITHOUT_DISPATCH"
    READY_WITH_CHECKPOINT = "READY_WITH_CHECKPOINT"
    EXPIRED_LEASE = "EXPIRED_LEASE"
    RETRY_DUE = "RETRY_DUE"
    CHECKPOINT_REQUIRED = "CHECKPOINT_REQUIRED"
    CHECKPOINT_SCOPE_MISMATCH = "CHECKPOINT_SCOPE_MISMATCH"
    CHECKPOINT_PLAN_MISMATCH = "CHECKPOINT_PLAN_MISMATCH"
    CHECKPOINT_GENERATION_MISMATCH = "CHECKPOINT_GENERATION_MISMATCH"
    CHECKPOINT_AHEAD_OF_TASK_DB = "CHECKPOINT_AHEAD_OF_TASK_DB"
    RUNTIME_RETRY_EXHAUSTED = "RUNTIME_RETRY_EXHAUSTED"


class RuntimeEventName(StrEnum):
    """Provider-independent event names reserved for the future runtime host."""

    TASK_ACCEPTED = "task_accepted"
    DISPATCH_CREATED = "dispatch_created"
    DISPATCH_ENQUEUED = "dispatch_enqueued"
    DISPATCH_RECEIVED = "dispatch_received"
    LEASE_ACQUIRED = "lease_acquired"
    LEASE_HEARTBEAT = "lease_heartbeat"
    LEASE_RELEASED = "lease_released"
    LEASE_EXPIRED = "lease_expired"
    TASK_EXECUTION_STARTED = "task_execution_started"
    TASK_SUSPENDED_FOR_APPROVAL = "task_suspended_for_approval"
    TASK_SUSPENDED_FOR_CLARIFICATION = "task_suspended_for_clarification"
    TASK_RESUMED = "task_resumed"
    TASK_CANCEL_REQUESTED = "task_cancel_requested"
    TASK_CANCEL_OBSERVED = "task_cancel_observed"
    TASK_RECOVERED = "task_recovered"
    RUNTIME_RETRY_SCHEDULED = "runtime_retry_scheduled"
    RUNTIME_RETRY_EXHAUSTED = "runtime_retry_exhausted"
    TASK_FINALIZED = "task_finalized"
    STALE_WORKER_COMMIT_REJECTED = "stale_worker_commit_rejected"


class RuntimeMetricName(StrEnum):
    """Stable runtime metric names; no exporter is selected by this contract."""

    TASK_QUEUE_DEPTH = "task_queue_depth"
    TASK_QUEUE_OLDEST_AGE_SECONDS = "task_queue_oldest_age_seconds"
    ACTIVE_WORKERS = "active_workers"
    ACTIVE_EXECUTION_LEASES = "active_execution_leases"
    LEASE_ACQUIRE_CONFLICTS = "lease_acquire_conflicts"
    LEASE_EXPIRATIONS = "lease_expirations"
    TASK_RECOVERIES = "task_recoveries"
    RECOVERY_FAILURES = "recovery_failures"
    RUNTIME_RETRY_COUNT = "runtime_retry_count"
    TASK_QUEUE_WAIT_SECONDS = "task_queue_wait_seconds"
    TASK_EXECUTION_SECONDS = "task_execution_seconds"
    WAITING_APPROVAL_COUNT = "waiting_approval_count"
    WAITING_CLARIFICATION_COUNT = "waiting_clarification_count"
    CANCEL_LATENCY_SECONDS = "cancel_latency_seconds"


class TaskSubmissionResponse(ImmutableContractModel):
    """Future 202 Accepted response returned after Task and dispatch commit atomically."""

    task_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_status: TaskStatus
    runtime_status: RuntimeStatus
    accepted_at: datetime
    status_url: str = Field(min_length=1)
    artifacts_url: str = Field(min_length=1)

    _validate_ids = field_validator("task_id", "trace_id")(validate_identifier)
    _validate_accepted_at = field_validator("accepted_at")(validate_utc_datetime)

    @field_validator("status_url", "artifacts_url")
    @classmethod
    def validate_public_api_path(cls, value: str) -> str:
        """Expose controlled API paths without leaking storage locations or external URLs."""
        if not value.startswith("/v1/tasks/") or "://" in value or ".." in value or "\\" in value:
            raise ValueError("task links must be controlled /v1/tasks API paths")
        return value

    @model_validator(mode="after")
    def validate_acceptance_state(self) -> TaskSubmissionResponse:
        """Freeze submission as persisted-but-not-yet-executed."""
        if self.task_status is not TaskStatus.CREATED:
            raise ValueError("accepted task_status must be CREATED")
        if self.runtime_status is not RuntimeStatus.READY:
            raise ValueError("accepted runtime_status must be READY")
        expected_prefix = f"/v1/tasks/{self.task_id}"
        if self.status_url != expected_prefix:
            raise ValueError("status_url must identify the accepted task")
        if self.artifacts_url != f"{expected_prefix}/artifacts":
            raise ValueError("artifacts_url must identify the accepted task artifacts")
        return self


class SubmissionIdempotency(ImmutableContractModel):
    """Tenant/caller-scoped Idempotency-Key binding for a canonical request fingerprint."""

    tenant_id: str = Field(min_length=1)
    caller_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    _validate_ids = field_validator("tenant_id", "caller_id", "idempotency_key")(
        validate_identifier
    )


class TaskDispatch(ImmutableContractModel):
    """Minimal immutable execution envelope transported by an at-least-once queue."""

    schema_version: Literal["task-dispatch.v1"] = "task-dispatch.v1"
    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    execution_generation: int = Field(ge=1)
    predecessor_execution_generation: int | None = Field(default=None, ge=1)
    resume_checkpoint_id: str | None = Field(default=None, min_length=1)
    expected_task_version: int = Field(ge=1)
    enqueued_at: datetime
    not_before: datetime

    _validate_ids = field_validator("tenant_id", "task_id", "trace_id", "dispatch_id")(
        validate_identifier
    )
    _validate_resume_id = field_validator("resume_checkpoint_id")(
        lambda value: validate_identifier(value) if value is not None else value
    )
    _validate_times = field_validator("enqueued_at", "not_before")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_delivery_window(self) -> TaskDispatch:
        if self.not_before < self.enqueued_at:
            raise ValueError("not_before must not precede enqueued_at")
        if (self.predecessor_execution_generation is None) != (self.resume_checkpoint_id is None):
            raise ValueError("resume checkpoint and predecessor generation must be bound together")
        if self.predecessor_execution_generation is not None and (
            self.predecessor_execution_generation != self.execution_generation - 1
        ):
            raise ValueError("resume predecessor must be the immediately prior generation")
        return self

    @property
    def identity(self) -> tuple[str, str, int]:
        """Return the idempotent dispatch identity used by durable uniqueness constraints."""
        return (self.tenant_id, self.task_id, self.execution_generation)


class DispatchRecord(ImmutableContractModel):
    """Durable outbox state corresponding to one immutable TaskDispatch."""

    dispatch: TaskDispatch
    status: DispatchStatus
    attempt_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    last_error_code: str | None = Field(default=None, min_length=1)

    _validate_times = field_validator("created_at", "updated_at")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_record_time(self) -> DispatchRecord:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class QueueDelivery(ImmutableContractModel):
    """Broker-neutral delivery receipt used only for ACK/NACK transport operations."""

    delivery_id: str = Field(min_length=1)
    dispatch: TaskDispatch
    received_at: datetime
    delivery_attempt: int = Field(ge=1)

    _validate_delivery_id = field_validator("delivery_id")(validate_identifier)
    _validate_received_at = field_validator("received_at")(validate_utc_datetime)


class WorkerIdentity(ImmutableContractModel):
    """Stable identity of one worker process incarnation."""

    worker_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    started_at: datetime

    _validate_ids = field_validator("worker_id", "deployment_id")(validate_identifier)
    _validate_started_at = field_validator("started_at")(validate_utc_datetime)


class LeaseTimingPolicy(ImmutableContractModel):
    """Validated operational defaults for heartbeat and takeover timing."""

    heartbeat_interval_seconds: int = Field(default=15, ge=1, le=300)
    lease_ttl_seconds: int = Field(default=60, ge=5, le=900)

    @model_validator(mode="after")
    def validate_heartbeat_margin(self) -> LeaseTimingPolicy:
        if self.heartbeat_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError("heartbeat interval must be shorter than lease TTL")
        if self.lease_ttl_seconds < self.heartbeat_interval_seconds * 3:
            raise ValueError("lease TTL must allow at least three heartbeat intervals")
        return self


class ExecutionLease(ImmutableContractModel):
    """Authoritative database lease carrying a monotonic fencing token."""

    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    execution_generation: int = Field(ge=1)
    task_version: int = Field(ge=1)
    worker_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    fencing_token: int = Field(ge=1)
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime

    _validate_ids = field_validator("tenant_id", "task_id", "dispatch_id", "worker_id", "lease_id")(
        validate_identifier
    )
    _validate_times = field_validator("acquired_at", "heartbeat_at", "expires_at")(
        validate_utc_datetime
    )

    @model_validator(mode="after")
    def validate_lease_window(self) -> ExecutionLease:
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat_at must not precede acquired_at")
        if self.expires_at <= self.heartbeat_at:
            raise ValueError("expires_at must be after heartbeat_at")
        return self

    def is_active_at(self, observed_at: datetime) -> bool:
        """Use the database-observed instant; takeover is eligible at or after expiry."""
        validate_utc_datetime(observed_at)
        return observed_at < self.expires_at


class LeaseAcquisitionResult(ImmutableContractModel):
    """Safe result of one atomic conditional lease acquisition."""

    status: LeaseAcquisitionStatus
    lease: ExecutionLease | None = None
    reason_code: str = Field(min_length=1)
    current_fencing_token: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_outcome(self) -> LeaseAcquisitionResult:
        if (self.status is LeaseAcquisitionStatus.ACQUIRED) != (self.lease is not None):
            raise ValueError("only ACQUIRED may contain an execution lease")
        return self


class RuntimeAttempt(ImmutableContractModel):
    """Persisted accounting for one runtime hosting/recovery attempt."""

    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    execution_generation: int = Field(ge=1)
    runtime_attempt: int = Field(ge=1)
    status: RuntimeAttemptStatus
    started_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = Field(default=None, min_length=1)

    _validate_ids = field_validator("tenant_id", "task_id", "dispatch_id")(validate_identifier)
    _validate_started_at = field_validator("started_at")(validate_utc_datetime)
    _validate_completed_at = field_validator("completed_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_attempt_completion(self) -> RuntimeAttempt:
        if self.status is RuntimeAttemptStatus.RUNNING and self.completed_at is not None:
            raise ValueError("running attempt cannot have completed_at")
        if self.status is not RuntimeAttemptStatus.RUNNING and self.completed_at is None:
            raise ValueError("finished attempt requires completed_at")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self


class RuntimeRetryPolicy(ImmutableContractModel):
    """Runtime crash-recovery budget, separate from frozen Graph/Tool retry budgets."""

    max_recovery_attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_seconds: int = Field(default=5, ge=1, le=300)
    maximum_backoff_seconds: int = Field(default=300, ge=1, le=3600)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)

    @model_validator(mode="after")
    def validate_backoff(self) -> RuntimeRetryPolicy:
        if self.maximum_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("maximum backoff must not be smaller than initial backoff")
        return self


class CancellationRequest(ImmutableContractModel):
    """Durable, idempotent cancellation intent written with authoritative Task state."""

    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    requested_at: datetime
    reason_code: str = Field(min_length=1)

    _validate_ids = field_validator(
        "tenant_id", "task_id", "request_id", "requested_by", "reason_code"
    )(validate_identifier)
    _validate_requested_at = field_validator("requested_at")(validate_utc_datetime)


class CancellationState(ImmutableContractModel):
    """Durable cancellation snapshot; worker observation may follow Task finalization."""

    request: CancellationRequest
    task_finalized_at: datetime
    worker_observed_at: datetime | None = None
    observer_worker_id: str | None = Field(default=None, min_length=1)

    _validate_finalized_at = field_validator("task_finalized_at")(validate_utc_datetime)
    _validate_observed_at = field_validator("worker_observed_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )
    _validate_observer = field_validator("observer_worker_id")(
        lambda value: validate_identifier(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_cancellation(self) -> CancellationState:
        if self.task_finalized_at < self.request.requested_at:
            raise ValueError("task cancellation cannot finalize before it is requested")
        if (self.worker_observed_at is None) != (self.observer_worker_id is None):
            raise ValueError("worker observation time and identity must be recorded together")
        if (
            self.worker_observed_at is not None
            and self.worker_observed_at < self.request.requested_at
        ):
            raise ValueError("worker cannot observe cancellation before it is requested")
        return self


class CheckpointIdentity(ImmutableContractModel):
    """Minimized workflow-continuation identity used only for reconciliation."""

    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    task_version: int = Field(ge=1)
    plan_version: int | None = Field(default=None, ge=1)
    execution_generation: int = Field(ge=1)
    current_step_id: str | None = Field(default=None, min_length=1)
    successful_step_ids: tuple[str, ...] = ()

    _validate_ids = field_validator("tenant_id", "task_id", "checkpoint_id", "thread_id")(
        validate_identifier
    )
    _validate_optional_step = field_validator("current_step_id")(
        lambda value: validate_identifier(value) if value is not None else value
    )
    _validate_steps = field_validator("successful_step_ids")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )

    @model_validator(mode="after")
    def validate_checkpoint_identity(self) -> CheckpointIdentity:
        if self.thread_id != f"{self.tenant_id}:{self.task_id}":
            raise ValueError("checkpoint thread_id must be tenant-qualified")
        if len(set(self.successful_step_ids)) != len(self.successful_step_ids):
            raise ValueError("successful_step_ids must be unique")
        return self


class TaskRuntimeSnapshot(ImmutableContractModel):
    """Authoritative facts loaded before every claim, resume, recovery, and commit."""

    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_status: TaskStatus
    task_version: int = Field(ge=1)
    runtime_status: RuntimeStatus
    execution_generation: int = Field(ge=1)
    predecessor_execution_generation: int | None = Field(default=None, ge=1)
    resume_checkpoint_id: str | None = Field(default=None, min_length=1)
    plan_version: int | None = Field(default=None, ge=1)
    current_dispatch_id: str | None = Field(default=None, min_length=1)
    dispatch_status: DispatchStatus | None = None
    retry_not_before: datetime | None = None
    lease: ExecutionLease | None = None
    checkpoint: CheckpointIdentity | None = None
    cancellation: CancellationState | None = None
    pending_approval_id: str | None = Field(default=None, min_length=1)
    pending_approval_status: ApprovalStatus | None = None
    pending_clarification_id: str | None = Field(default=None, min_length=1)
    pending_clarification_status: ClarificationStatus | None = None
    successful_step_ids: tuple[str, ...] = ()
    recovery_attempt_count: int = Field(default=0, ge=0)
    last_recovery_error: str | None = Field(default=None, min_length=1)

    _validate_ids = field_validator("tenant_id", "task_id")(validate_identifier)
    _validate_optional_ids = field_validator(
        "current_dispatch_id",
        "resume_checkpoint_id",
        "pending_approval_id",
        "pending_clarification_id",
    )(lambda value: validate_identifier(value) if value is not None else value)
    _validate_retry_time = field_validator("retry_not_before")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )
    _validate_steps = field_validator("successful_step_ids")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )

    @model_validator(mode="after")
    def validate_snapshot(self) -> TaskRuntimeSnapshot:
        if len(set(self.successful_step_ids)) != len(self.successful_step_ids):
            raise ValueError("successful_step_ids must be unique")
        if self.lease is not None and (
            self.lease.tenant_id != self.tenant_id
            or self.lease.task_id != self.task_id
            or self.lease.execution_generation != self.execution_generation
        ):
            raise ValueError("lease scope or execution generation does not match Task DB")
        if self.checkpoint is not None and (
            self.checkpoint.tenant_id != self.tenant_id or self.checkpoint.task_id != self.task_id
        ):
            raise ValueError("checkpoint scope does not match Task DB")
        if self.cancellation is not None and (
            self.cancellation.request.tenant_id != self.tenant_id
            or self.cancellation.request.task_id != self.task_id
        ):
            raise ValueError("cancellation scope does not match Task DB")
        if (self.predecessor_execution_generation is None) != (self.resume_checkpoint_id is None):
            raise ValueError("resume checkpoint and predecessor generation must be bound together")
        if self.predecessor_execution_generation is not None and (
            self.predecessor_execution_generation != self.execution_generation - 1
        ):
            raise ValueError("resume predecessor must be the immediately prior generation")
        if self.task_status in TERMINAL_TASK_STATUSES:
            if self.runtime_status is not RuntimeStatus.FINISHED:
                raise ValueError("terminal Task must have FINISHED runtime status")
            if self.lease is not None:
                raise ValueError("terminal Task cannot hold an execution lease")
        if self.task_status is TaskStatus.WAITING_APPROVAL:
            if self.runtime_status is not RuntimeStatus.SUSPENDED:
                raise ValueError("WAITING_APPROVAL must suspend the runtime")
            if self.lease is not None:
                raise ValueError("WAITING_APPROVAL cannot hold an execution lease")
            if self.pending_approval_status is not ApprovalStatus.PENDING:
                raise ValueError("WAITING_APPROVAL requires one pending approval")
        if self.task_status is TaskStatus.WAITING_CLARIFICATION:
            if self.runtime_status is not RuntimeStatus.SUSPENDED:
                raise ValueError("WAITING_CLARIFICATION must suspend the runtime")
            if self.lease is not None:
                raise ValueError("WAITING_CLARIFICATION cannot hold an execution lease")
            if self.pending_clarification_status is not ClarificationStatus.PENDING:
                raise ValueError("WAITING_CLARIFICATION requires one pending clarification")
        if self.runtime_status is RuntimeStatus.SUSPENDED and (
            self.task_status not in {TaskStatus.WAITING_APPROVAL, TaskStatus.WAITING_CLARIFICATION}
        ):
            raise ValueError("SUSPENDED runtime status requires an interactive waiting Task")
        if (self.runtime_status is RuntimeStatus.WAITING_RETRY) != (
            self.retry_not_before is not None
        ):
            raise ValueError("WAITING_RETRY and retry_not_before must be recorded together")
        if self.runtime_status is RuntimeStatus.FINISHED and (
            self.task_status not in TERMINAL_TASK_STATUSES
        ):
            raise ValueError("FINISHED runtime status requires a terminal Task")
        if self.runtime_status is RuntimeStatus.LEASED and self.lease is None:
            raise ValueError("LEASED runtime status requires an execution lease")
        if self.runtime_status is not RuntimeStatus.LEASED and self.lease is not None:
            raise ValueError("only LEASED runtime status may hold an execution lease")
        if (self.pending_approval_id is None) != (self.pending_approval_status is None):
            raise ValueError("pending approval identity and status must be recorded together")
        if (self.pending_clarification_id is None) != (self.pending_clarification_status is None):
            raise ValueError("pending clarification identity and status must be recorded together")
        return self


class RecoveryDecision(ImmutableContractModel):
    """Fail-closed result of reconciling Task DB, checkpoint, dispatch, and lease."""

    action: RecoveryAction
    reason: RecoveryReason
    next_execution_generation: int | None = Field(default=None, ge=1)
    preserved_step_ids: tuple[str, ...] = ()
    error_code: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> RecoveryDecision:
        dispatching = self.action in {RecoveryAction.REDISPATCH, RecoveryAction.RESUME}
        if dispatching != (self.next_execution_generation is not None):
            raise ValueError("dispatching decisions require the next execution generation")
        if self.action is RecoveryAction.FAIL_CLOSED and self.error_code is None:
            raise ValueError("fail-closed decision requires a typed error code")
        return self


class StepCommitIdentity(ImmutableContractModel):
    """Unique durable-success identity preventing step replay after redelivery."""

    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    plan_version: int = Field(ge=1)
    step_id: str = Field(min_length=1)
    execution_generation: int = Field(ge=1)
    attempt: int = Field(ge=1)

    _validate_ids = field_validator("tenant_id", "task_id", "step_id")(validate_identifier)


class ArtifactPublicationIdentity(ImmutableContractModel):
    """Stable command identity for one logical Artifact publication."""

    tenant_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    plan_version: int = Field(ge=1)
    step_id: str = Field(min_length=1)
    artifact_command_id: str = Field(min_length=1)
    canonical_input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    _validate_ids = field_validator("tenant_id", "task_id", "step_id", "artifact_command_id")(
        validate_identifier
    )


def runtime_retry_delay_seconds(policy: RuntimeRetryPolicy, completed_attempts: int) -> int:
    """Return deterministic bounded runtime-recovery backoff without sleeping."""
    if completed_attempts < 1:
        raise ValueError("completed_attempts must be at least one")
    delay = policy.initial_backoff_seconds * (policy.backoff_multiplier ** (completed_attempts - 1))
    return min(policy.maximum_backoff_seconds, int(delay))


def decide_recovery(
    snapshot: TaskRuntimeSnapshot,
    *,
    observed_at: datetime,
    retry_policy: RuntimeRetryPolicy,
) -> RecoveryDecision:
    """Reconcile only explicit durable facts; never guess missing workflow state."""
    validate_utc_datetime(observed_at)
    if snapshot.task_status is TaskStatus.CANCELLED:
        return RecoveryDecision(
            action=RecoveryAction.NO_OP,
            reason=RecoveryReason.CANCELLED_TASK,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    if snapshot.task_status in TERMINAL_TASK_STATUSES:
        return RecoveryDecision(
            action=RecoveryAction.NO_OP,
            reason=RecoveryReason.TERMINAL_TASK,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    if snapshot.cancellation is not None:
        return RecoveryDecision(
            action=RecoveryAction.FAIL_CLOSED,
            reason=RecoveryReason.CANCELLATION_INCONSISTENT,
            preserved_step_ids=snapshot.successful_step_ids,
            error_code="CANCELLATION_STATE_MISMATCH",
        )
    if snapshot.task_status is TaskStatus.WAITING_APPROVAL:
        return RecoveryDecision(
            action=RecoveryAction.WAIT,
            reason=RecoveryReason.WAITING_APPROVAL,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    if snapshot.task_status is TaskStatus.WAITING_CLARIFICATION:
        return RecoveryDecision(
            action=RecoveryAction.WAIT,
            reason=RecoveryReason.WAITING_CLARIFICATION,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    if snapshot.lease is not None and snapshot.lease.is_active_at(observed_at):
        return RecoveryDecision(
            action=RecoveryAction.WAIT,
            reason=RecoveryReason.ACTIVE_LEASE,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    if (
        snapshot.runtime_status is RuntimeStatus.WAITING_RETRY
        and snapshot.retry_not_before is not None
        and observed_at < snapshot.retry_not_before
    ):
        return RecoveryDecision(
            action=RecoveryAction.WAIT,
            reason=RecoveryReason.RETRY_NOT_DUE,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    if snapshot.recovery_attempt_count >= retry_policy.max_recovery_attempts:
        return RecoveryDecision(
            action=RecoveryAction.FAIL_CLOSED,
            reason=RecoveryReason.RUNTIME_RETRY_EXHAUSTED,
            preserved_step_ids=snapshot.successful_step_ids,
            error_code="RUNTIME_RETRY_EXHAUSTED",
        )

    checkpoint = snapshot.checkpoint
    if checkpoint is not None:
        if checkpoint.task_version > snapshot.task_version:
            return RecoveryDecision(
                action=RecoveryAction.FAIL_CLOSED,
                reason=RecoveryReason.CHECKPOINT_AHEAD_OF_TASK_DB,
                preserved_step_ids=snapshot.successful_step_ids,
                error_code="CHECKPOINT_AHEAD_OF_TASK_DB",
            )
        if checkpoint.plan_version != snapshot.plan_version:
            return RecoveryDecision(
                action=RecoveryAction.FAIL_CLOSED,
                reason=RecoveryReason.CHECKPOINT_PLAN_MISMATCH,
                preserved_step_ids=snapshot.successful_step_ids,
                error_code="CHECKPOINT_PLAN_MISMATCH",
            )
        current_generation_checkpoint = (
            checkpoint.execution_generation == snapshot.execution_generation
        )
        predecessor_checkpoint = (
            snapshot.predecessor_execution_generation is not None
            and checkpoint.execution_generation == snapshot.predecessor_execution_generation
            and checkpoint.checkpoint_id == snapshot.resume_checkpoint_id
        )
        if not current_generation_checkpoint and not predecessor_checkpoint:
            return RecoveryDecision(
                action=RecoveryAction.FAIL_CLOSED,
                reason=RecoveryReason.CHECKPOINT_GENERATION_MISMATCH,
                preserved_step_ids=snapshot.successful_step_ids,
                error_code="CHECKPOINT_GENERATION_MISMATCH",
            )
        if not set(checkpoint.successful_step_ids).issubset(snapshot.successful_step_ids):
            return RecoveryDecision(
                action=RecoveryAction.FAIL_CLOSED,
                reason=RecoveryReason.CHECKPOINT_AHEAD_OF_TASK_DB,
                preserved_step_ids=snapshot.successful_step_ids,
                error_code="CHECKPOINT_SUCCESS_SET_AHEAD",
            )

    if snapshot.lease is not None:
        if checkpoint is None:
            return RecoveryDecision(
                action=RecoveryAction.FAIL_CLOSED,
                reason=RecoveryReason.CHECKPOINT_REQUIRED,
                preserved_step_ids=snapshot.successful_step_ids,
                error_code="CHECKPOINT_REQUIRED_FOR_TAKEOVER",
            )
        return RecoveryDecision(
            action=RecoveryAction.RESUME,
            reason=RecoveryReason.EXPIRED_LEASE,
            next_execution_generation=snapshot.execution_generation,
            preserved_step_ids=snapshot.successful_step_ids,
        )

    if snapshot.runtime_status is RuntimeStatus.WAITING_RETRY:
        return RecoveryDecision(
            action=RecoveryAction.RESUME if checkpoint is not None else RecoveryAction.REDISPATCH,
            reason=RecoveryReason.RETRY_DUE,
            next_execution_generation=snapshot.execution_generation,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    if checkpoint is not None:
        return RecoveryDecision(
            action=RecoveryAction.RESUME,
            reason=RecoveryReason.READY_WITH_CHECKPOINT,
            next_execution_generation=snapshot.execution_generation,
            preserved_step_ids=snapshot.successful_step_ids,
        )
    return RecoveryDecision(
        action=RecoveryAction.REDISPATCH,
        reason=RecoveryReason.READY_WITHOUT_DISPATCH,
        next_execution_generation=snapshot.execution_generation,
        preserved_step_ids=snapshot.successful_step_ids,
    )


def assert_commit_authority(
    snapshot: TaskRuntimeSnapshot,
    *,
    tenant_id: str,
    task_id: str,
    worker_id: str,
    lease_id: str,
    execution_generation: int,
    fencing_token: int,
    observed_at: datetime,
) -> None:
    """Reject every authoritative mutation from a stale, expired, or cross-scope worker."""
    validate_utc_datetime(observed_at)
    if snapshot.tenant_id != tenant_id or snapshot.task_id != task_id:
        raise LeaseLostError("Worker commit scope does not match authoritative Task scope")
    if snapshot.task_status in TERMINAL_TASK_STATUSES:
        raise TaskAlreadyTerminalError("Terminal Task state cannot be overwritten")
    lease = snapshot.lease
    if lease is None:
        raise LeaseLostError("Worker no longer owns an execution lease")
    if lease.worker_id != worker_id or lease.lease_id != lease_id:
        raise LeaseLostError("Worker commit identity does not match the current execution lease")
    if execution_generation != snapshot.execution_generation:
        raise StaleExecutionGenerationError("Worker execution generation is stale")
    if fencing_token != lease.fencing_token:
        raise StaleFencingTokenError("Worker fencing token is stale")
    if not lease.is_active_at(observed_at):
        raise LeaseExpiredError("Worker execution lease has expired")


__all__ = [
    "ArtifactPublicationIdentity",
    "CancellationRequest",
    "CancellationState",
    "CheckpointIdentity",
    "DispatchRecord",
    "DispatchStatus",
    "ExecutionLease",
    "LeaseAcquisitionResult",
    "LeaseAcquisitionStatus",
    "LeaseTimingPolicy",
    "QueueDelivery",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryReason",
    "RuntimeAttempt",
    "RuntimeAttemptStatus",
    "RuntimeEventName",
    "RuntimeMetricName",
    "RuntimeRetryPolicy",
    "RuntimeStatus",
    "StepCommitIdentity",
    "SubmissionIdempotency",
    "TaskDispatch",
    "TaskRuntimeSnapshot",
    "TaskSubmissionResponse",
    "WorkerIdentity",
    "assert_commit_authority",
    "decide_recovery",
    "runtime_retry_delay_seconds",
]
