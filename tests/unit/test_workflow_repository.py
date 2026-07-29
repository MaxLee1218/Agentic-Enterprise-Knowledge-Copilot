"""Durable workflow repository concurrency tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from copilot.contracts import TaskRequest
from copilot.persistence.identifiers import UuidIdentifierFactory
from copilot.persistence.task_repository import InMemoryWorkflowRepository
from copilot.services.workflows.state_machine import TaskStateMachine
from tests.unit.domain.helpers import make_contract, make_plan

NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)


def test_sql_compare_and_swap_rejects_stale_cross_process_state(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    first_machine = TaskStateMachine(clock=lambda: NOW, ids=UuidIdentifierFactory())
    initial = first_machine.initial("T-001")
    request = TaskRequest(
        id="R-001",
        user_id="U-001",
        raw_input="Analyze supplier quality",
        created_at=NOW,
    )
    first = InMemoryWorkflowRepository(database_path)
    first.initialize(request, make_contract(), make_plan(), initial)
    stale = InMemoryWorkflowRepository(database_path)
    try:
        first_state, first_event = first_machine.transition(
            initial,
            "START_UNDERSTANDING",
            reason="first writer",
        )
        stale_state, stale_event = TaskStateMachine(
            clock=lambda: NOW,
            ids=UuidIdentifierFactory(),
        ).transition(
            initial,
            "START_UNDERSTANDING",
            reason="stale writer",
        )
        first.commit_transition(initial, first_state, first_event)

        with pytest.raises(ValueError, match="compare-and-swap"):
            stale.commit_transition(initial, stale_state, stale_event)

        assert first.state_for("T-001") == first_state
        reloaded = InMemoryWorkflowRepository(database_path)
        try:
            assert reloaded.state_for("T-001") == first_state
        finally:
            reloaded.close()
    finally:
        stale.close()
        first.close()
