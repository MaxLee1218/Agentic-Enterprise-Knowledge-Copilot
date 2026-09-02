"""Durable clarification suspension graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def request_clarification(
    state: AgentGraphState,
    *,
    node_runtime: GraphNodeRuntime,
) -> dict[str, object]:
    return node_runtime.request_clarification(state)
