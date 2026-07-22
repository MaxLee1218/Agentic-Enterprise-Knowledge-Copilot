"""Execution-time validation layered on the TaskPlan DAG contract."""

from copilot.contracts import CapabilityName, StepType, TaskContract, TaskPlan
from copilot.services.workflows.errors import PlanValidationError
from copilot.services.workflows.fixed_plan import SUPPLIER_QUALITY_PLAN_VERSION
from copilot.tools.exceptions import ToolRuntimeError
from copilot.tools.registry import ToolRegistry

_EXPECTED_TOOL_TYPES = {
    CapabilityName.KNOWLEDGE_SEARCH.value: StepType.KNOWLEDGE_SEARCH,
    CapabilityName.DATABASE_QUERY.value: StepType.DATABASE_QUERY,
    CapabilityName.ANALYSIS_ENGINE.value: StepType.ANALYSIS,
    CapabilityName.REPORT_GENERATOR.value: StepType.REPORT_GENERATION,
}


class PlanValidator:
    """Fail before execution when tool, schema, capability, or final-step wiring is invalid."""

    def __init__(self, *, registry: ToolRegistry, max_task_steps: int) -> None:
        self._registry = registry
        self._max_task_steps = max_task_steps

    def validate(self, plan: TaskPlan, contract: TaskContract) -> None:
        """Validate the fixed plan against the contract and registry snapshot."""
        if plan.task_id != contract.task_id:
            raise PlanValidationError("Plan and contract task identifiers differ")
        if plan.planning_version != SUPPLIER_QUALITY_PLAN_VERSION:
            raise PlanValidationError("Unsupported fixed plan version")
        if not plan.steps or len(plan.steps) > self._max_task_steps:
            raise PlanValidationError("Plan step count is outside configured bounds")
        required = {item.value for item in contract.required_capabilities}
        planned = {step.tool_name for step in plan.steps}
        if required != planned:
            raise PlanValidationError("Plan capabilities do not exactly satisfy the contract")
        report_steps = [step for step in plan.steps if step.step_type is StepType.REPORT_GENERATION]
        if len(report_steps) != 1 or plan.steps[-1] != report_steps[0]:
            raise PlanValidationError("Plan must end with exactly one report generation step")
        for step in plan.steps:
            try:
                tool = self._registry.get(step.tool_name)
            except ToolRuntimeError as exc:
                raise PlanValidationError(str(exc)) from exc
            expected_type = _EXPECTED_TOOL_TYPES.get(step.tool_name)
            if expected_type is None or step.step_type is not expected_type:
                raise PlanValidationError(f"Step {step.step_id} uses an invalid tool/type pair")
            if (
                step.input_schema != tool.definition.input_schema
                or step.output_schema != tool.definition.output_schema
            ):
                raise PlanValidationError(
                    f"Step {step.step_id} schemas differ from the registered definition"
                )
