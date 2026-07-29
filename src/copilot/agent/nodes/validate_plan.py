"""Plan-validation graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def validate_plan(state: AgentGraphState, *, node_runtime: GraphNodeRuntime) -> dict[str, object]:
    return node_runtime.validate_plan(state)
