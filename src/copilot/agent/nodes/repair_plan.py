"""Bounded plan-repair node adapter."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def repair_plan(
    state: AgentGraphState,
    *,
    node_runtime: GraphNodeRuntime,
) -> dict[str, object]:
    return node_runtime.repair_plan(state)
