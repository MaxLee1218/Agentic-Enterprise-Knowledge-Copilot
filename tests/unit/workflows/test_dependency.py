"""Dependency checker behavior for success, failure, missing, and cancelled results."""

import pytest

from copilot.contracts import ErrorType, JsonObject, StepResult, StepResultStatus, TaskError
from copilot.services.workflows.dependency import DependencyChecker
from tests.unit.domain.helpers import make_plan


def _result(step_id: str, status: StepResultStatus) -> StepResult:
    success = status is StepResultStatus.SUCCESS
    return StepResult(
        step_id=step_id,
        status=status,
        output=JsonObject({"ok": True}) if success else None,
        evidence=("E-001",) if success else (),
        error=(
            None
            if success
            else TaskError(
                error_code="TEST_FAILURE",
                error_type=ErrorType.TECHNICAL,
                message="Controlled failure",
                recoverable=False,
            )
        ),
    )


def test_root_step_is_runnable_without_results() -> None:
    plan = make_plan()
    assert DependencyChecker().check(plan.steps[0], {}).satisfied is True


def test_successful_dependency_is_runnable() -> None:
    plan = make_plan()
    decision = DependencyChecker().check(
        plan.steps[1],
        {plan.steps[0].step_id: _result(plan.steps[0].step_id, StepResultStatus.SUCCESS)},
    )
    assert decision.satisfied is True


@pytest.mark.parametrize(
    "status",
    [
        StepResultStatus.TECHNICAL_FAILURE,
        StepResultStatus.BUSINESS_FAILURE,
        StepResultStatus.CANCELLED,
    ],
)
def test_failed_or_cancelled_dependency_blocks_step(status: StepResultStatus) -> None:
    plan = make_plan()
    decision = DependencyChecker().check(
        plan.steps[1], {plan.steps[0].step_id: _result(plan.steps[0].step_id, status)}
    )
    assert decision.satisfied is False
    assert plan.steps[0].step_id in (decision.reason or "")


def test_missing_dependency_result_blocks_step() -> None:
    plan = make_plan()
    decision = DependencyChecker().check(plan.steps[1], {})
    assert decision.satisfied is False
    assert decision.failed_dependencies == (plan.steps[0].step_id,)
