"""Semantic plan validity metrics independent of step prose and identifiers."""

from copilot.contracts import TaskPlan, TaskStep
from evaluation.contracts import CapturedExecution, EvaluationCase, ExpectedPlan, MetricResult
from evaluation.evaluators.base import ratio_metric


class PlanQualityEvaluator:
    name = "plan_quality"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        expected = case.expected_plan
        plan = execution.plan_snapshot
        if not expected.plan_required:
            valid = plan is None or _valid(plan, expected)
        else:
            valid = plan is not None and _valid(plan, expected)
        initial_valid = valid and execution.plan_repair_count == 0
        repair_success = execution.plan_repair_count > 0 and valid
        return (
            ratio_metric("initial_plan_validity", int(initial_valid), int(expected.plan_required)),
            ratio_metric("final_plan_validity", int(valid), int(expected.plan_required)),
            ratio_metric(
                "plan_repair_success",
                int(repair_success),
                int(execution.plan_repair_count > 0),
            ),
        )


def _valid(plan: TaskPlan, expected: ExpectedPlan) -> bool:
    steps = plan.steps
    tools = [step.tool_name for step in steps]
    required = set(expected.required_tools)
    allowed = set(expected.allowed_tools)
    forbidden = set(expected.forbidden_tools)
    maximum = expected.max_steps
    return (
        required.issubset(tools)
        and (not allowed or set(tools).issubset(allowed))
        and not forbidden.intersection(tools)
        and (maximum is None or len(steps) <= maximum)
        and (not expected.must_include_report_step or "report_generator" in tools)
        and _acyclic(steps)
    )


def _acyclic(steps: tuple[TaskStep, ...]) -> bool:
    graph = {step.step_id: step.dependency for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        if any(parent not in graph or not visit(parent) for parent in graph[node]):
            return False
        visiting.remove(node)
        visited.add(node)
        return True

    return all(visit(node) for node in graph)


__all__ = ["PlanQualityEvaluator"]
