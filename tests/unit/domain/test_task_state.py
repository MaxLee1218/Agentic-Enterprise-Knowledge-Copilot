"""Tests for authoritative lifecycle snapshots and terminal results."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from copilot.contracts import TaskResult, TaskState, TaskStatus
from tests.unit.domain.helpers import TASK_ID


def test_task_state_json_round_trip_preserves_snapshot() -> None:
    """A lifecycle snapshot should survive JSON persistence without information loss."""
    state = TaskState(
        task_id=TASK_ID,
        state=TaskStatus.EXECUTING,
        version=4,
        updated_at=datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
        last_event_id="EV-004",
    )

    restored = TaskState.model_validate_json(state.model_dump_json())

    assert restored == state
    assert restored.updated_at.tzinfo is not None


def test_task_state_rejects_invalid_status_and_version() -> None:
    """Unknown lifecycle strings and non-positive versions must fail validation."""
    values = {
        "task_id": TASK_ID,
        "state": "RUNNING",
        "version": 0,
        "updated_at": "2026-07-19T08:00:00Z",
        "last_event_id": "EV-001",
    }

    with pytest.raises(ValidationError):
        TaskState.model_validate(values)


def test_task_result_requires_terminal_status() -> None:
    """A final result cannot be emitted before the state machine reaches a terminal state."""
    with pytest.raises(ValidationError, match="terminal task state"):
        TaskResult(
            task_id=TASK_ID,
            final_status=TaskStatus.VERIFYING,
            summary="Verification is still running",
        )
