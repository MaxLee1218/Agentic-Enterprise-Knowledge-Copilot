"""Fixed-plan creation graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def create_plan(state: AgentGraphState, *, node_runtime: GraphNodeRuntime) -> dict[str, object]:
    return node_runtime.create_plan(state)
