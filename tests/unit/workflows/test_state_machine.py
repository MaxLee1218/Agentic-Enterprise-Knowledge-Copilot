"""Frozen state-machine transition and terminal-state tests."""

from datetime import UTC, datetime

import pytest

from copilot.contracts import TaskStatus
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.workflows.errors import StateTransitionError
from copilot.services.workflows.state_machine import TaskStateMachine


def _clock() -> datetime:
    return datetime(2026, 7, 22, 8, 0, tzinfo=UTC)


def test_success_and_retry_paths_increment_versions() -> None:
    machine = TaskStateMachine(clock=_clock, ids=SequentialIdentifierFactory())
    state = machine.initial("T-001")
    for event in (
        "START_UNDERSTANDING",
        "CONTRACT_VALIDATED",
        "PLAN_APPROVED_BY_POLICY",
        "TRANSIENT_FAILURE",
        "RETRY_READY",
        "ALL_REQUIRED_STEPS_FINISHED",
        "VERIFICATION_PASSED",
    ):
        state, record = machine.transition(state, event, reason="test")
        assert state.last_event_id == record.event_id
    assert state.state is TaskStatus.COMPLETED
    assert state.version == 8


def test_failure_path_is_legal_and_terminal_cannot_restart() -> None:
    machine = TaskStateMachine(clock=_clock, ids=SequentialIdentifierFactory())
    state = machine.initial("T-001")
    for event in (
        "START_UNDERSTANDING",
        "CONTRACT_VALIDATED",
        "PLAN_APPROVED_BY_POLICY",
        "NON_RECOVERABLE_FAILURE",
    ):
        state, _ = machine.transition(state, event, reason="test")
    assert state.state is TaskStatus.FAILED
    with pytest.raises(StateTransitionError, match="Illegal"):
        machine.transition(state, "RETRY_READY", reason="invalid")


def test_created_cannot_jump_directly_to_completed() -> None:
    machine = TaskStateMachine(clock=_clock, ids=SequentialIdentifierFactory())
    with pytest.raises(StateTransitionError):
        machine.transition(machine.initial("T-001"), "VERIFICATION_PASSED", reason="invalid")
