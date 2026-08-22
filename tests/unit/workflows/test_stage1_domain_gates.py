"""Stage 1 must represent AP contracts without enabling AP execution."""

import pytest

from copilot.contracts import TaskRequest
from copilot.services.workflows.errors import StepInputError
from copilot.services.workflows.inputs import StepInputBuilder
from copilot.services.workflows.validation import PlanValidator
from copilot.tools.registry import ToolRegistry
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_plan


def test_plan_validation_denies_disabled_ap_manifest_before_tool_lookup() -> None:
    result = PlanValidator(registry=ToolRegistry(), max_task_steps=20).evaluate(
        make_plan(), make_ap_contract()
    )

    assert result.is_valid is False
    assert result.errors[0].error_code == "DOMAIN_EXECUTION_NOT_ENABLED"
    assert result.errors[0].repairable is False


def test_step_input_builder_denies_ap_before_constructing_quality_arguments() -> None:
    plan = make_plan()

    with pytest.raises(StepInputError, match="DOMAIN_EXECUTION_NOT_ENABLED"):
        StepInputBuilder().build(
            plan.steps[0],
            request=TaskRequest(
                id="R-AP-001",
                user_id="U-AP-001",
                raw_input="Analyze AP exceptions",
            ),
            contract=make_ap_contract(),
            prior_results={},
            evidence={},
        )
