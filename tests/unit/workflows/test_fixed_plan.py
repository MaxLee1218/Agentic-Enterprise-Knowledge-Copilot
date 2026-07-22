"""Fixed-plan construction and execution validation tests."""

import pytest

from copilot.contracts import TaskRequest
from copilot.services.workflows.errors import PlanValidationError
from copilot.services.workflows.fixed_plan import (
    ANALYZE_QUALITY,
    GENERATE_REPORT,
    QUERY_QUALITY_DATA,
    RETRIEVE_POLICY,
    SUPPLIER_QUALITY_PLAN_VERSION,
    SupplierQualityAnalysisPlanFactory,
    step_id,
)
from copilot.services.workflows.validation import PlanValidator
from copilot.tools.registry import ToolRegistry
from tests.mocks.mock_tools import (
    MockAnalyticsTool,
    MockDatabaseTool,
    MockKnowledgeTool,
    MockReportTool,
)
from tests.unit.domain.helpers import STARTED_AT, make_contract


def test_fixed_plan_has_stable_order_tools_and_dependencies() -> None:
    registry = ToolRegistry()
    for tool in (
        MockKnowledgeTool(),
        MockDatabaseTool(),
        MockAnalyticsTool(),
        MockReportTool(),
    ):
        registry.register(tool)
    contract = make_contract()
    request = TaskRequest(
        id="R-001",
        user_id="U-001",
        raw_input="Analyze supplier quality in Q1 2026",
        created_at=STARTED_AT,
    )

    plan = SupplierQualityAnalysisPlanFactory(registry).create(request, contract)

    expected = (
        step_id(contract.task_id, RETRIEVE_POLICY),
        step_id(contract.task_id, QUERY_QUALITY_DATA),
        step_id(contract.task_id, ANALYZE_QUALITY),
        step_id(contract.task_id, GENERATE_REPORT),
    )
    assert tuple(item.step_id for item in plan.steps) == expected
    assert tuple(item.tool_name for item in plan.steps) == (
        "knowledge_search",
        "database_query",
        "analysis_engine",
        "report_generator",
    )
    assert plan.steps[0].dependency == ()
    assert plan.steps[1].dependency == ()
    assert plan.steps[2].dependency == (expected[1],)
    assert plan.steps[3].dependency == (expected[0], expected[2])
    assert plan.planning_version == SUPPLIER_QUALITY_PLAN_VERSION
    PlanValidator(registry=registry, max_task_steps=10).validate(plan, contract)

    registry.unregister("report_generator")
    with pytest.raises(PlanValidationError, match="not registered"):
        PlanValidator(registry=registry, max_task_steps=10).validate(plan, contract)
