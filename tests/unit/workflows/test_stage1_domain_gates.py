"""Stage 8 keeps AP execution fail-closed when its governed dependencies are absent."""

import pytest

from copilot.contracts import TaskRequest
from copilot.services.workflows.errors import StepInputError
from copilot.services.workflows.inputs import StepInputBuilder
from copilot.services.workflows.validation import PlanValidator
from copilot.tools.registry import ToolRegistry
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_plan


def test_plan_validation_denies_ap_when_exact_profile_tools_are_not_registered() -> None:
    result = PlanValidator(registry=ToolRegistry(), max_task_steps=20).evaluate(
        make_plan(), make_ap_contract()
    )

    assert result.is_valid is False
    assert any(issue.error_code == "TOOL_PROFILE_MISMATCH" for issue in result.errors)


def test_step_input_builder_denies_ap_without_controlled_policy_bundle() -> None:
    plan = make_plan()

    with pytest.raises(StepInputError, match="controlled policy bundle is unavailable"):
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
