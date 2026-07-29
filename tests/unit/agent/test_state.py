"""Graph-state reducer tests."""

from datetime import UTC, datetime
from typing import cast

import pytest

from copilot.agent.routing import route_after_validate
from copilot.agent.state import (
    AgentGraphState,
    checkpoint_serializer,
    initial_graph_state,
    merge_counts,
    merge_errors,
    merge_identifiers,
    merge_step_results,
)
from copilot.contracts import (
    ErrorType,
    JsonObject,
    StepResult,
    StepResultStatus,
    TaskError,
    TaskRequest,
    TaskStatus,
)
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.workflows.state_machine import TaskStateMachine
from tests.unit.domain.helpers import make_contract, make_plan


def test_step_result_reducer_is_idempotent_and_replaces_same_step_snapshot() -> None:
    original = StepResult(
        step_id="S-1",
        status=StepResultStatus.SUCCESS,
        output=JsonObject({"value": 1}),
        evidence=(),
        error=None,
    )
    replayed = original.model_copy(deep=True)
    assert merge_step_results([original], [replayed]) == [original]


def test_error_reducer_suppresses_exact_node_replay() -> None:
    error = TaskError(
        error_code="CONTROLLED",
        error_type=ErrorType.TECHNICAL,
        message="controlled",
        recoverable=False,
        task_id="T-1",
    )
    assert merge_errors([error], [error.model_copy(deep=True)]) == [error]


def test_counter_reducer_never_moves_backwards() -> None:
    assert merge_counts({"S-1": 2}, {"S-1": 1, "S-2": 1}) == {"S-1": 2, "S-2": 1}


def test_evidence_identifier_reducer_deduplicates_replay() -> None:
    assert merge_identifiers(["E-1"], ["E-1", "E-2"]) == ["E-1", "E-2"]


def test_graph_state_round_trip_preserves_contract_enums_and_utc_times() -> None:
    started_at = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    domain_state = TaskStateMachine(
        clock=lambda: started_at,
        ids=SequentialIdentifierFactory(),
    ).initial("T-001")
    state = initial_graph_state(
        request=TaskRequest(
            id="R-001",
            user_id="U-001",
            raw_input="Analyze Q1 supplier quality",
            created_at=started_at,
        ),
        contract=make_contract(),
        plan=make_plan(),
        domain_state=domain_state,
        started_at=started_at,
    )
    serializer = checkpoint_serializer()

    restored = cast(AgentGraphState, serializer.loads_typed(serializer.dumps_typed(state)))

    assert restored == state
    assert restored["domain_state"].state is TaskStatus.CREATED
    assert restored["started_at"] == started_at
    assert restored["started_at"].tzinfo is UTC


def test_missing_required_route_field_fails_closed() -> None:
    with pytest.raises(KeyError, match="route"):
        route_after_validate(cast(AgentGraphState, {}))
