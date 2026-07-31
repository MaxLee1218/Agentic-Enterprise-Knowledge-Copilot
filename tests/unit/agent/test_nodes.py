"""Independent coverage for every explicit LangGraph node adapter."""

from collections.abc import Callable
from typing import cast
from unittest.mock import Mock

import pytest

from copilot.agent import nodes
from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


@pytest.mark.parametrize(
    ("node", "runtime_method"),
    [
        (nodes.validate_request, "validate_request"),
        (nodes.understand_task, "understand_task"),
        (nodes.classify_task, "classify_task"),
        (nodes.create_plan, "create_plan"),
        (nodes.validate_plan, "validate_plan"),
        (nodes.repair_plan, "repair_plan"),
        (nodes.replan, "replan"),
        (nodes.policy_check, "policy_check"),
        (nodes.execute_tool, "execute_tool"),
        (nodes.aggregate_evidence, "aggregate_evidence"),
        (nodes.generate_report, "generate_report"),
        (nodes.verify_result, "verify_result"),
        (nodes.persist_result, "persist_result"),
    ],
)
def test_node_delegates_only_to_its_runtime_boundary(
    node: Callable[..., dict[str, object]],
    runtime_method: str,
) -> None:
    runtime = Mock(spec=GraphNodeRuntime)
    state = cast(AgentGraphState, {"route": "test"})
    expected: dict[str, object] = {"route": "delegated"}
    getattr(runtime, runtime_method).return_value = expected

    result = node(state, node_runtime=cast(GraphNodeRuntime, runtime))

    assert result == expected
    getattr(runtime, runtime_method).assert_called_once_with(state)
