"""Semantic invariants for the broker-neutral asynchronous runtime contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from copilot.contracts import (
    ApprovalStatus,
    CancellationRequest,
    CancellationState,
    CheckpointIdentity,
    DispatchStatus,
    ExecutionLease,
    LeaseExpiredError,
    LeaseLostError,
    LeaseTimingPolicy,
    RecoveryAction,
    RecoveryReason,
    RuntimeRetryPolicy,
    RuntimeStatus,
    StaleExecutionGenerationError,
    StaleFencingTokenError,
    TaskAlreadyTerminalError,
    TaskDispatch,
    TaskRuntimeSnapshot,
    TaskStatus,
    TaskSubmissionResponse,
    assert_commit_authority,
    decide_recovery,
    runtime_retry_delay_seconds,
)

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


def _dispatch() -> TaskDispatch:
    return TaskDispatch(
        tenant_id="TENANT-A",
        task_id="TASK-001",
        trace_id="TRACE-001",
        dispatch_id="DISPATCH-001",
        execution_generation=1,
        expected_task_version=1,
        enqueued_at=NOW,
        not_before=NOW,
    )


def _lease(*, fencing_token: int = 1, expires_at: datetime | None = None) -> ExecutionLease:
    return ExecutionLease(
        tenant_id="TENANT-A",
        task_id="TASK-001",
        dispatch_id="DISPATCH-001",
        execution_generation=1,
        task_version=3,
        worker_id="WORKER-001",
        lease_id="LEASE-001",
        fencing_token=fencing_token,
        acquired_at=NOW,
        heartbeat_at=NOW,
        expires_at=expires_at or NOW + timedelta(seconds=60),
    )


def _checkpoint(**updates: object) -> CheckpointIdentity:
    values: dict[str, object] = {
        "tenant_id": "TENANT-A",
        "task_id": "TASK-001",
        "checkpoint_id": "CHECKPOINT-001",
        "thread_id": "TENANT-A:TASK-001",
        "task_version": 3,
        "plan_version": 1,
        "execution_generation": 1,
        "current_step_id": "STEP-002",
        "successful_step_ids": ("STEP-001",),
    }
    values.update(updates)
    return CheckpointIdentity.model_validate(values)


def _snapshot(**updates: object) -> TaskRuntimeSnapshot:
    values: dict[str, object] = {
        "tenant_id": "TENANT-A",
        "task_id": "TASK-001",
        "task_status": TaskStatus.EXECUTING,
        "task_version": 3,
        "runtime_status": RuntimeStatus.READY,
        "execution_generation": 1,
        "plan_version": 1,
        "current_dispatch_id": "DISPATCH-001",
        "dispatch_status": DispatchStatus.ENQUEUED,
        "successful_step_ids": ("STEP-001",),
    }
    values.update(updates)
    return TaskRuntimeSnapshot.model_validate(values)


def test_submission_response_freezes_202_acceptance_shape_without_storage_paths() -> None:
    response = TaskSubmissionResponse(
        task_id="TASK-001",
        trace_id="TRACE-001",
        task_status=TaskStatus.CREATED,
        runtime_status=RuntimeStatus.READY,
        accepted_at=NOW,
        status_url="/v1/tasks/TASK-001",
        artifacts_url="/v1/tasks/TASK-001/artifacts",
    )

    assert response.model_dump(mode="json")["runtime_status"] == "READY"
    with pytest.raises(ValidationError, match="accepted task_status"):
        TaskSubmissionResponse.model_validate(
            {**response.model_dump(), "task_status": TaskStatus.COMPLETED}
        )
    with pytest.raises(ValidationError, match="controlled /v1/tasks"):
        TaskSubmissionResponse.model_validate(
            {**response.model_dump(), "artifacts_url": "file:///data/artifacts/report.pdf"}
        )


def test_queue_envelope_is_minimal_immutable_and_rejects_payload_or_secrets() -> None:
    dispatch = _dispatch()
    restored = TaskDispatch.model_validate_json(dispatch.model_dump_json())

    assert restored == dispatch
    assert restored.identity == ("TENANT-A", "TASK-001", 1)
    with pytest.raises(ValidationError):
        dispatch.execution_generation = 2
    for forbidden in ("task_state", "task_plan", "credentials", "business_rows", "prompt"):
        with pytest.raises(ValidationError, match="Extra inputs"):
            TaskDispatch.model_validate({**dispatch.model_dump(), forbidden: {"secret": "x"}})
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        TaskDispatch.model_validate({**dispatch.model_dump(), "execution_generation": 0})
    with pytest.raises(ValidationError, match="bound together"):
        TaskDispatch.model_validate(
            {**dispatch.model_dump(), "resume_checkpoint_id": "CHECKPOINT-001"}
        )
    with pytest.raises(ValidationError, match="immediately prior"):
        TaskDispatch.model_validate(
            {
                **dispatch.model_dump(),
                "execution_generation": 3,
                "predecessor_execution_generation": 1,
                "resume_checkpoint_id": "CHECKPOINT-001",
            }
        )


def test_lease_timing_defaults_and_takeover_boundary_use_fake_clock() -> None:
    policy = LeaseTimingPolicy()
    lease = _lease()

    assert policy.heartbeat_interval_seconds == 15
    assert policy.lease_ttl_seconds == 60
    assert lease.is_active_at(NOW + timedelta(seconds=59)) is True
    assert lease.is_active_at(NOW + timedelta(seconds=60)) is False
    with pytest.raises(ValidationError, match="three heartbeat"):
        LeaseTimingPolicy(heartbeat_interval_seconds=20, lease_ttl_seconds=40)


def test_waiting_approval_and_terminal_tasks_cannot_hold_worker_leases() -> None:
    with pytest.raises(ValidationError, match="WAITING_APPROVAL cannot hold"):
        _snapshot(
            task_status=TaskStatus.WAITING_APPROVAL,
            runtime_status=RuntimeStatus.SUSPENDED,
            lease=_lease(),
            pending_approval_id="APPROVAL-001",
            pending_approval_status=ApprovalStatus.PENDING,
        )
    with pytest.raises(ValidationError, match="terminal Task cannot hold"):
        _snapshot(
            task_status=TaskStatus.COMPLETED,
            runtime_status=RuntimeStatus.FINISHED,
            lease=_lease(),
        )
    with pytest.raises(ValidationError, match="requires one pending approval"):
        _snapshot(
            task_status=TaskStatus.WAITING_APPROVAL,
            runtime_status=RuntimeStatus.SUSPENDED,
        )


def test_recovery_waits_for_active_lease_and_approval_without_dispatching() -> None:
    active = _snapshot(runtime_status=RuntimeStatus.LEASED, lease=_lease())
    decision = decide_recovery(
        active,
        observed_at=NOW + timedelta(seconds=30),
        retry_policy=RuntimeRetryPolicy(),
    )
    assert (decision.action, decision.reason) == (
        RecoveryAction.WAIT,
        RecoveryReason.ACTIVE_LEASE,
    )

    approval = _snapshot(
        task_status=TaskStatus.WAITING_APPROVAL,
        runtime_status=RuntimeStatus.SUSPENDED,
        pending_approval_id="APPROVAL-001",
        pending_approval_status=ApprovalStatus.PENDING,
    )
    decision = decide_recovery(approval, observed_at=NOW, retry_policy=RuntimeRetryPolicy())
    assert (decision.action, decision.reason) == (
        RecoveryAction.WAIT,
        RecoveryReason.WAITING_APPROVAL,
    )


def test_expired_lease_requires_valid_checkpoint_and_reuses_generation() -> None:
    expired = _snapshot(
        runtime_status=RuntimeStatus.LEASED,
        lease=_lease(expires_at=NOW + timedelta(seconds=1)),
        checkpoint=_checkpoint(),
    )
    decision = decide_recovery(
        expired,
        observed_at=NOW + timedelta(seconds=1),
        retry_policy=RuntimeRetryPolicy(),
    )

    assert decision.action is RecoveryAction.RESUME
    assert decision.reason is RecoveryReason.EXPIRED_LEASE
    assert decision.next_execution_generation == 1
    assert decision.preserved_step_ids == ("STEP-001",)

    missing = expired.model_copy(update={"checkpoint": None})
    decision = decide_recovery(
        missing,
        observed_at=NOW + timedelta(seconds=1),
        retry_policy=RuntimeRetryPolicy(),
    )
    assert decision.action is RecoveryAction.FAIL_CLOSED
    assert decision.error_code == "CHECKPOINT_REQUIRED_FOR_TAKEOVER"


@pytest.mark.parametrize(
    ("checkpoint", "reason"),
    [
        (_checkpoint(plan_version=2), RecoveryReason.CHECKPOINT_PLAN_MISMATCH),
        (
            _checkpoint(execution_generation=2),
            RecoveryReason.CHECKPOINT_GENERATION_MISMATCH,
        ),
        (_checkpoint(task_version=4), RecoveryReason.CHECKPOINT_AHEAD_OF_TASK_DB),
        (
            _checkpoint(successful_step_ids=("STEP-001", "STEP-UNCOMMITTED")),
            RecoveryReason.CHECKPOINT_AHEAD_OF_TASK_DB,
        ),
    ],
)
def test_checkpoint_mismatch_fails_closed(
    checkpoint: CheckpointIdentity,
    reason: RecoveryReason,
) -> None:
    snapshot = _snapshot(
        runtime_status=RuntimeStatus.LEASED,
        lease=_lease(expires_at=NOW + timedelta(seconds=1)),
        checkpoint=checkpoint,
    )

    decision = decide_recovery(
        snapshot,
        observed_at=NOW + timedelta(seconds=2),
        retry_policy=RuntimeRetryPolicy(),
    )
    assert decision.action is RecoveryAction.FAIL_CLOSED
    assert decision.reason is reason


def test_approval_resume_explicitly_binds_prior_generation_checkpoint() -> None:
    snapshot = _snapshot(
        task_version=4,
        execution_generation=2,
        predecessor_execution_generation=1,
        resume_checkpoint_id="CHECKPOINT-001",
        checkpoint=_checkpoint(),
    )

    decision = decide_recovery(snapshot, observed_at=NOW, retry_policy=RuntimeRetryPolicy())
    assert decision.action is RecoveryAction.RESUME
    assert decision.next_execution_generation == 2


def test_terminal_cancelled_and_poison_tasks_never_reexecute() -> None:
    terminal = _snapshot(
        task_status=TaskStatus.COMPLETED,
        runtime_status=RuntimeStatus.FINISHED,
        current_dispatch_id=None,
        dispatch_status=DispatchStatus.ACKNOWLEDGED,
    )
    decision = decide_recovery(terminal, observed_at=NOW, retry_policy=RuntimeRetryPolicy())
    assert (decision.action, decision.reason) == (
        RecoveryAction.NO_OP,
        RecoveryReason.TERMINAL_TASK,
    )

    cancelled = terminal.model_copy(update={"task_status": TaskStatus.CANCELLED})
    decision = decide_recovery(cancelled, observed_at=NOW, retry_policy=RuntimeRetryPolicy())
    assert (decision.action, decision.reason) == (
        RecoveryAction.NO_OP,
        RecoveryReason.CANCELLED_TASK,
    )

    poison = _snapshot(recovery_attempt_count=3)
    decision = decide_recovery(poison, observed_at=NOW, retry_policy=RuntimeRetryPolicy())
    assert decision.action is RecoveryAction.FAIL_CLOSED
    assert decision.error_code == "RUNTIME_RETRY_EXHAUSTED"


def test_runtime_retry_requires_a_durable_due_time_and_preserves_generation() -> None:
    due_at = NOW + timedelta(seconds=10)
    retrying = _snapshot(
        runtime_status=RuntimeStatus.WAITING_RETRY,
        retry_not_before=due_at,
    )

    waiting = decide_recovery(
        retrying,
        observed_at=due_at - timedelta(microseconds=1),
        retry_policy=RuntimeRetryPolicy(),
    )
    assert (waiting.action, waiting.reason) == (
        RecoveryAction.WAIT,
        RecoveryReason.RETRY_NOT_DUE,
    )

    due = decide_recovery(
        retrying,
        observed_at=due_at,
        retry_policy=RuntimeRetryPolicy(),
    )
    assert (due.action, due.reason, due.next_execution_generation) == (
        RecoveryAction.REDISPATCH,
        RecoveryReason.RETRY_DUE,
        1,
    )
    with pytest.raises(ValidationError, match="recorded together"):
        _snapshot(runtime_status=RuntimeStatus.WAITING_RETRY)


def test_fencing_rejects_old_worker_generation_expiry_terminal_and_cross_tenant() -> None:
    current = _snapshot(runtime_status=RuntimeStatus.LEASED, lease=_lease(fencing_token=7))
    assert_commit_authority(
        current,
        tenant_id="TENANT-A",
        task_id="TASK-001",
        worker_id="WORKER-001",
        lease_id="LEASE-001",
        execution_generation=1,
        fencing_token=7,
        observed_at=NOW + timedelta(seconds=30),
    )
    with pytest.raises(StaleFencingTokenError):
        assert_commit_authority(
            current,
            tenant_id="TENANT-A",
            task_id="TASK-001",
            worker_id="WORKER-001",
            lease_id="LEASE-001",
            execution_generation=1,
            fencing_token=6,
            observed_at=NOW + timedelta(seconds=30),
        )
    with pytest.raises(StaleExecutionGenerationError):
        assert_commit_authority(
            current,
            tenant_id="TENANT-A",
            task_id="TASK-001",
            worker_id="WORKER-001",
            lease_id="LEASE-001",
            execution_generation=2,
            fencing_token=7,
            observed_at=NOW + timedelta(seconds=30),
        )
    with pytest.raises(LeaseExpiredError):
        assert_commit_authority(
            current,
            tenant_id="TENANT-A",
            task_id="TASK-001",
            worker_id="WORKER-001",
            lease_id="LEASE-001",
            execution_generation=1,
            fencing_token=7,
            observed_at=NOW + timedelta(seconds=60),
        )
    with pytest.raises(LeaseLostError):
        assert_commit_authority(
            current,
            tenant_id="TENANT-B",
            task_id="TASK-001",
            worker_id="WORKER-001",
            lease_id="LEASE-001",
            execution_generation=1,
            fencing_token=7,
            observed_at=NOW + timedelta(seconds=30),
        )
    with pytest.raises(LeaseLostError):
        assert_commit_authority(
            current,
            tenant_id="TENANT-A",
            task_id="TASK-001",
            worker_id="WORKER-STALE",
            lease_id="LEASE-STALE",
            execution_generation=1,
            fencing_token=7,
            observed_at=NOW + timedelta(seconds=30),
        )

    terminal = _snapshot(
        task_status=TaskStatus.FAILED,
        runtime_status=RuntimeStatus.FINISHED,
        current_dispatch_id=None,
        dispatch_status=DispatchStatus.DEAD_LETTERED,
    )
    with pytest.raises(TaskAlreadyTerminalError):
        assert_commit_authority(
            terminal,
            tenant_id="TENANT-A",
            task_id="TASK-001",
            worker_id="WORKER-001",
            lease_id="LEASE-001",
            execution_generation=1,
            fencing_token=7,
            observed_at=NOW,
        )


def test_durable_cancellation_does_not_claim_hard_process_termination() -> None:
    request = CancellationRequest(
        tenant_id="TENANT-A",
        task_id="TASK-001",
        request_id="CANCEL-001",
        requested_by="USER-001",
        requested_at=NOW,
        reason_code="USER_REQUESTED",
    )
    state = CancellationState(
        request=request,
        task_finalized_at=NOW,
        worker_observed_at=NOW + timedelta(seconds=2),
        observer_worker_id="WORKER-001",
    )

    assert state.worker_observed_at is not None
    with pytest.raises(ValidationError, match="recorded together"):
        CancellationState(
            request=request,
            task_finalized_at=NOW,
            observer_worker_id="WORKER-001",
        )
    with pytest.raises(ValidationError, match="cannot observe cancellation before"):
        CancellationState(
            request=request,
            task_finalized_at=NOW,
            worker_observed_at=NOW - timedelta(seconds=1),
            observer_worker_id="WORKER-001",
        )


def test_runtime_retry_backoff_is_deterministic_bounded_and_separate() -> None:
    policy = RuntimeRetryPolicy(
        max_recovery_attempts=3,
        initial_backoff_seconds=5,
        maximum_backoff_seconds=12,
    )

    assert [runtime_retry_delay_seconds(policy, attempt) for attempt in (1, 2, 3, 4)] == [
        5,
        10,
        12,
        12,
    ]
    with pytest.raises(ValueError, match="at least one"):
        runtime_retry_delay_seconds(policy, 0)
