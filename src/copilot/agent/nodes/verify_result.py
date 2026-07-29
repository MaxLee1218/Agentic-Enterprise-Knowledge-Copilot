"""Independent verification graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def verify_result(state: AgentGraphState, *, node_runtime: GraphNodeRuntime) -> dict[str, object]:
    return node_runtime.verify_result(state)
