"""Pure graph-routing tests."""

from collections.abc import Callable
from typing import cast

import pytest

from copilot.agent.routing import (
    route_after_classification,
    route_after_evidence,
    route_after_plan_creation,
    route_after_plan_repair,
    route_after_plan_validation,
    route_after_policy,
    route_after_replan,
    route_after_report,
    route_after_tool,
    route_after_understanding,
    route_after_validate,
    route_after_verification,
)
from copilot.agent.state import AgentGraphState
from copilot.services.workflows.fixed_plan import GENERATE_REPORT


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("tool_retry", "execute_tool"),
        ("tool_success", "aggregate_evidence"),
        ("tool_failure", "persist_result"),
        ("cancelled", "persist_result"),
        ("deadline_exceeded", "persist_result"),
    ],
)
def test_tool_routes(route: str, expected: str) -> None:
    state = cast(AgentGraphState, {"route": route})
    assert route_after_tool(state) == expected


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("tool_retry", "generate_report"),
        ("tool_success", "aggregate_evidence"),
        ("report_failed", "persist_result"),
    ],
)
def test_report_routes(route: str, expected: str) -> None:
    state = cast(AgentGraphState, {"route": route})
    assert route_after_report(state) == expected


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("continue_execution", "policy_check"),
        ("all_steps_complete", "verify_result"),
        ("evidence_failure", "persist_result"),
        ("replan_required", "persist_result"),
    ],
)
def test_evidence_routes(route: str, expected: str) -> None:
    state = cast(AgentGraphState, {"route": route})
    assert route_after_evidence(state) == expected


def test_policy_routes_report_step_to_report_node() -> None:
    step = type("Step", (), {"step_id": GENERATE_REPORT, "tool_name": "report_generator"})()
    plan = type("Plan", (), {"steps": (step,)})()
    state = cast(
        AgentGraphState,
        {"route": "allowed", "current_step_id": GENERATE_REPORT, "plan": plan},
    )
    assert route_after_policy(state) == "generate_report"


@pytest.mark.parametrize(
    "route",
    ["approval_required", "denied", "dependency_failed", "deadline_exceeded"],
)
def test_policy_stop_routes_go_to_persistence(route: str) -> None:
    state = cast(AgentGraphState, {"route": route})
    assert route_after_policy(state) == "persist_result"


@pytest.mark.parametrize(
    ("router", "route", "expected"),
    [
        (route_after_validate, "valid", "understand_task"),
        (route_after_validate, "invalid_request", "persist_result"),
        (route_after_understanding, "understood", "classify_task"),
        (route_after_understanding, "missing_information", "request_clarification"),
        (route_after_classification, "supported", "create_plan"),
        (route_after_classification, "unsupported", "persist_result"),
        (route_after_plan_creation, "plan_created", "validate_plan"),
        (route_after_plan_creation, "deadline_exceeded", "persist_result"),
        (route_after_plan_validation, "plan_valid", "policy_check"),
        (route_after_plan_validation, "repairable_plan", "repair_plan"),
        (route_after_plan_validation, "invalid_plan", "persist_result"),
        (route_after_plan_repair, "plan_repaired", "validate_plan"),
        (route_after_plan_repair, "repair_exhausted", "persist_result"),
        (route_after_replan, "replan_created", "validate_plan"),
        (route_after_replan, "replan_failed", "persist_result"),
        (route_after_verification, "verification_replan", "replan"),
        (route_after_verification, "verified", "persist_result"),
        (route_after_verification, "verification_failed", "persist_result"),
        (route_after_verification, "completed", "persist_result"),
    ],
)
def test_stage_routes(
    router: Callable[[AgentGraphState], str],
    route: str,
    expected: str,
) -> None:
    state = cast(AgentGraphState, {"route": route})
    assert router(state) == expected
