"""Pure conditional routing for the deterministic LangGraph workflow."""

from copilot.agent.state import AgentGraphState


def route_after_validate(state: AgentGraphState) -> str:
    return "understand_task" if state["route"] == "valid" else "persist_result"


def route_after_understanding(state: AgentGraphState) -> str:
    return "classify_task" if state["route"] == "understood" else "persist_result"


def route_after_classification(state: AgentGraphState) -> str:
    return "create_plan" if state["route"] == "supported" else "persist_result"


def route_after_plan_creation(state: AgentGraphState) -> str:
    return "validate_plan" if state["route"] == "plan_created" else "persist_result"


def route_after_plan_validation(state: AgentGraphState) -> str:
    return "policy_check" if state["route"] == "plan_valid" else "persist_result"


def route_after_policy(state: AgentGraphState) -> str:
    if state["route"] == "allowed":
        step = next(
            item for item in state["plan"].steps if item.step_id == state["current_step_id"]
        )
        return "generate_report" if step.tool_name == "report_generator" else "execute_tool"
    return "persist_result"


def route_after_tool(state: AgentGraphState) -> str:
    if state["route"] == "tool_retry":
        return "execute_tool"
    if state["route"] == "tool_success":
        return "aggregate_evidence"
    return "persist_result"


def route_after_report(state: AgentGraphState) -> str:
    if state["route"] == "tool_retry":
        return "generate_report"
    return "aggregate_evidence" if state["route"] == "tool_success" else "persist_result"


def route_after_evidence(state: AgentGraphState) -> str:
    if state["route"] == "all_steps_complete":
        return "verify_result"
    if state["route"] == "continue_execution":
        return "policy_check"
    return "persist_result"


def route_after_verification(state: AgentGraphState) -> str:
    return "persist_result"


__all__ = [
    "route_after_classification",
    "route_after_evidence",
    "route_after_plan_creation",
    "route_after_plan_validation",
    "route_after_policy",
    "route_after_report",
    "route_after_tool",
    "route_after_understanding",
    "route_after_validate",
    "route_after_verification",
]
