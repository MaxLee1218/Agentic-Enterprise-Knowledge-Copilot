"""Final persistence graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def persist_result(state: AgentGraphState, *, node_runtime: GraphNodeRuntime) -> dict[str, object]:
    return node_runtime.persist_result(state)
