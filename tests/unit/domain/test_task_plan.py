"""Tests for task-plan identity, dependency, and DAG validation."""

import pytest
from pydantic import ValidationError

from copilot.contracts import StepType, TaskPlan
from tests.unit.domain.helpers import TASK_ID, make_plan, make_step


def test_task_plan_accepts_valid_dependency_graph() -> None:
    """A later step may depend on a known earlier step in the same task."""
    plan = make_plan()

    assert plan.steps[1].dependency == (plan.steps[0].step_id,)


def test_task_plan_rejects_duplicate_step_id() -> None:
    """Step identifiers must be unique within a plan."""
    first = make_step("S-01", StepType.DATABASE_QUERY, "database_query")
    duplicate = make_step("S-01", StepType.ANALYSIS, "analysis_engine")

    with pytest.raises(ValidationError, match="must be unique"):
        TaskPlan(task_id=TASK_ID, steps=(first, duplicate), planning_version=1)


def test_task_plan_rejects_unknown_dependency() -> None:
    """Every dependency edge must reference a step in the same plan."""
    step = make_step("S-AN-01", StepType.ANALYSIS, "analysis_engine", dependency=("S-MISSING",))

    with pytest.raises(ValidationError, match="unknown step dependencies"):
        TaskPlan(task_id=TASK_ID, steps=(step,), planning_version=1)


def test_task_step_rejects_direct_self_dependency() -> None:
    """A step cannot directly depend on itself."""
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        make_step("S-01", StepType.ANALYSIS, "analysis_engine", dependency=("S-01",))


def test_task_plan_rejects_indirect_cycle() -> None:
    """The plan validator must detect cycles spanning multiple steps."""
    first = make_step("S-01", StepType.DATABASE_QUERY, "database_query", ("S-02",))
    second = make_step("S-02", StepType.ANALYSIS, "analysis_engine", ("S-01",))

    with pytest.raises(ValidationError, match="acyclic graph"):
        TaskPlan(task_id=TASK_ID, steps=(first, second), planning_version=1)
