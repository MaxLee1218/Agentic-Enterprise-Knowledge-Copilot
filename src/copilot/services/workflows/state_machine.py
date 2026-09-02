"""Explicit implementation of the frozen Supplier Quality task state machine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from copilot.contracts import TaskState, TaskStatus
from copilot.services.workflows.errors import StateTransitionError
from copilot.services.workflows.models import TaskStateEvent
from copilot.services.workflows.ports import IdentifierFactory

_TRANSITIONS: dict[tuple[TaskStatus, str], TaskStatus] = {
    (TaskStatus.CREATED, "START_UNDERSTANDING"): TaskStatus.UNDERSTANDING,
    (TaskStatus.UNDERSTANDING, "CLARIFICATION_REQUIRED"): TaskStatus.WAITING_CLARIFICATION,
    (TaskStatus.UNDERSTANDING, "CLARIFICATION_EXHAUSTED"): TaskStatus.FAILED,
    (TaskStatus.UNDERSTANDING, "CONTRACT_VALIDATED"): TaskStatus.PLANNING,
    (TaskStatus.UNDERSTANDING, "UNDERSTANDING_FAILED"): TaskStatus.FAILED,
    (TaskStatus.PLANNING, "PLAN_APPROVED_BY_POLICY"): TaskStatus.EXECUTING,
    (TaskStatus.PLANNING, "APPROVAL_REQUIRED"): TaskStatus.WAITING_APPROVAL,
    (TaskStatus.PLANNING, "PLAN_INVALID"): TaskStatus.FAILED,
    (TaskStatus.WAITING_APPROVAL, "APPROVAL_GRANTED"): TaskStatus.EXECUTING,
    (TaskStatus.WAITING_APPROVAL, "APPROVAL_EDITED"): TaskStatus.EXECUTING,
    (TaskStatus.WAITING_APPROVAL, "APPROVAL_REJECTED"): TaskStatus.CANCELLED,
    (TaskStatus.WAITING_APPROVAL, "APPROVAL_EXPIRED"): TaskStatus.CANCELLED,
    (TaskStatus.WAITING_APPROVAL, "APPROVAL_REVOKED"): TaskStatus.CANCELLED,
    (TaskStatus.WAITING_CLARIFICATION, "CLARIFICATION_SUBMITTED"): TaskStatus.UNDERSTANDING,
    (TaskStatus.WAITING_CLARIFICATION, "CLARIFICATION_REJECTED"): TaskStatus.FAILED,
    (TaskStatus.WAITING_CLARIFICATION, "CLARIFICATION_EXHAUSTED"): TaskStatus.FAILED,
    (TaskStatus.EXECUTING, "STEP_SUCCEEDED"): TaskStatus.EXECUTING,
    (TaskStatus.EXECUTING, "TRANSIENT_FAILURE"): TaskStatus.RETRYING,
    (TaskStatus.RETRYING, "RETRY_READY"): TaskStatus.EXECUTING,
    (TaskStatus.RETRYING, "RETRY_BUDGET_EXHAUSTED"): TaskStatus.FAILED,
    (TaskStatus.EXECUTING, "NON_RECOVERABLE_FAILURE"): TaskStatus.FAILED,
    (TaskStatus.EXECUTING, "PLAN_NO_LONGER_EXECUTABLE"): TaskStatus.REPLANNING,
    (TaskStatus.EXECUTING, "LATE_APPROVAL_REQUIRED"): TaskStatus.WAITING_APPROVAL,
    (TaskStatus.EXECUTING, "ALL_REQUIRED_STEPS_FINISHED"): TaskStatus.VERIFYING,
    (TaskStatus.REPLANNING, "REVISED_PLAN_VALID"): TaskStatus.EXECUTING,
    (TaskStatus.REPLANNING, "REVISED_PLAN_REQUIRES_APPROVAL"): TaskStatus.WAITING_APPROVAL,
    (TaskStatus.REPLANNING, "REPLAN_FAILED"): TaskStatus.FAILED,
    (TaskStatus.VERIFYING, "VERIFICATION_PASSED"): TaskStatus.COMPLETED,
    (TaskStatus.VERIFYING, "REPAIRABLE_VERIFICATION_FAILURE"): TaskStatus.REPLANNING,
    (TaskStatus.VERIFYING, "NON_REPAIRABLE_VERIFICATION_FAILURE"): TaskStatus.FAILED,
    **{
        (status, "CANCEL_REQUESTED"): TaskStatus.CANCELLED
        for status in (
            TaskStatus.CREATED,
            TaskStatus.UNDERSTANDING,
            TaskStatus.WAITING_CLARIFICATION,
            TaskStatus.PLANNING,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.EXECUTING,
            TaskStatus.RETRYING,
            TaskStatus.REPLANNING,
            TaskStatus.VERIFYING,
        )
    },
}


class TaskStateMachine:
    """Create versioned state snapshots only for allowlisted transitions."""

    def __init__(self, *, clock: Callable[[], datetime], ids: IdentifierFactory) -> None:
        self._clock = clock
        self._ids = ids

    def initial(self, task_id: str) -> TaskState:
        """Create the authoritative CREATED snapshot."""
        return TaskState(
            task_id=task_id,
            state=TaskStatus.CREATED,
            version=1,
            updated_at=self._clock(),
            last_event_id=self._ids.new_id("EVT"),
        )

    def transition(
        self,
        state: TaskState,
        event: str,
        *,
        reason: str,
    ) -> tuple[TaskState, TaskStateEvent]:
        """Return the next state and immutable event or reject an illegal transition."""
        try:
            target = _TRANSITIONS[(state.state, event)]
        except KeyError as exc:
            raise StateTransitionError(
                f"Illegal task transition {state.state.value} + {event}"
            ) from exc
        now = self._clock()
        event_id = self._ids.new_id("EVT")
        current = TaskState(
            task_id=state.task_id,
            state=target,
            version=state.version + 1,
            updated_at=now,
            last_event_id=event_id,
        )
        record = TaskStateEvent(
            event_id=event_id,
            task_id=state.task_id,
            from_state=state.state.value,
            event=event,
            to_state=target.value,
            timestamp=now,
            reason=reason,
        )
        return current, record
