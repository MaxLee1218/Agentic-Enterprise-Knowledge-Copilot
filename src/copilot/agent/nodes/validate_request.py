"""Request-validation graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def validate_request(
    state: AgentGraphState, *, node_runtime: GraphNodeRuntime
) -> dict[str, object]:
    return node_runtime.validate_request(state)
