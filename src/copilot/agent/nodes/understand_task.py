"""Deterministic task-understanding graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def understand_task(state: AgentGraphState, *, node_runtime: GraphNodeRuntime) -> dict[str, object]:
    return node_runtime.understand_task(state)
