"""Bounded runtime-replan node adapter."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def replan(
    state: AgentGraphState,
    *,
    node_runtime: GraphNodeRuntime,
) -> dict[str, object]:
    return node_runtime.replan(state)
