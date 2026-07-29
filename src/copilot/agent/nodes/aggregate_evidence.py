"""Evidence aggregation graph node."""

from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState


def aggregate_evidence(
    state: AgentGraphState, *, node_runtime: GraphNodeRuntime
) -> dict[str, object]:
    return node_runtime.aggregate_evidence(state)
