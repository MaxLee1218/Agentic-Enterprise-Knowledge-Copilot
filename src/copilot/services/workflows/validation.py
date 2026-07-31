"""Structured execution-time validation layered on the frozen TaskPlan DAG contract."""

from dataclasses import dataclass

from copilot.contracts import CapabilityName, StepType, TaskContract, TaskPlan
from copilot.services.workflows.errors import PlanValidationError
from copilot.tools.exceptions import ToolRuntimeError
from copilot.tools.registry import ToolRegistry

_EXPECTED_TOOL_TYPES = {
    CapabilityName.KNOWLEDGE_SEARCH.value: StepType.KNOWLEDGE_SEARCH,
    CapabilityName.DATABASE_QUERY.value: StepType.DATABASE_QUERY,
    CapabilityName.ANALYSIS_ENGINE.value: StepType.ANALYSIS,
    CapabilityName.REPORT_GENERATOR.value: StepType.REPORT_GENERATION,
}


@dataclass(frozen=True, slots=True)
class PlanValidationIssue:
    """One safe, repair-oriented deterministic validation finding."""

    error_code: str
    message: str
    repair_hint: str
    step_id: str | None = None
    field: str | None = None
    repairable: bool = True


@dataclass(frozen=True, slots=True)
class PlanValidationResult:
    """Complete validation result suitable for routing and bounded repair."""

    errors: tuple[PlanValidationIssue, ...] = ()
    warnings: tuple[PlanValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def is_repairable(self) -> bool:
        return bool(self.errors) and all(issue.repairable for issue in self.errors)


class PlanValidator:
    """Fail before execution when tool, schema, capability, or final-step wiring is invalid."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        max_task_steps: int,
        max_planning_version: int = 3,
    ) -> None:
        self._registry = registry
        self._max_task_steps = max_task_steps
        self._max_planning_version = max_planning_version

    def validate(self, plan: TaskPlan, contract: TaskContract) -> None:
        """Raise the legacy boundary error when structured evaluation finds an issue."""
        result = self.evaluate(plan, contract)
        if result.errors:
            raise PlanValidationError(result.errors[0].message)

    def evaluate(self, plan: TaskPlan, contract: TaskContract) -> PlanValidationResult:
        """Return all safe deterministic findings without invoking any tool."""
        errors: list[PlanValidationIssue] = []
        if plan.task_id != contract.task_id:
            errors.append(
                PlanValidationIssue(
                    "PLAN_TASK_MISMATCH",
                    "Plan and contract task identifiers differ",
                    "Use the immutable TaskContract task_id for the plan and every step",
                    field="task_id",
                    repairable=False,
                )
            )
        if plan.planning_version > self._max_planning_version:
            errors.append(
                PlanValidationIssue(
                    "PLAN_VERSION_LIMIT_EXCEEDED",
                    "Plan version exceeds the configured replan budget",
                    "Keep the version within the trusted configured planning limit",
                    field="planning_version",
                    repairable=False,
                )
            )
        if not plan.steps or len(plan.steps) > self._max_task_steps:
            errors.append(
                PlanValidationIssue(
                    "PLAN_STEP_LIMIT_EXCEEDED",
                    "Plan step count is outside configured bounds",
                    "Remove redundant steps without changing the requested deliverable",
                    field="steps",
                )
            )
        required = {item.value for item in contract.required_capabilities}
        planned = {step.tool_name for step in plan.steps}
        if required != planned:
            errors.append(
                PlanValidationIssue(
                    "PLAN_CAPABILITY_MISMATCH",
                    "Plan capabilities do not exactly satisfy the contract",
                    "Use each required registered capability and no additional capability",
                    field="steps",
                )
            )
        report_steps = [step for step in plan.steps if step.step_type is StepType.REPORT_GENERATION]
        if len(report_steps) != 1 or plan.steps[-1] != report_steps[0]:
            errors.append(
                PlanValidationIssue(
                    "REPORT_STEP_INVALID",
                    "Plan must end with exactly one report generation step",
                    "Add one final report_generator step",
                    field="steps",
                )
            )
        for step in plan.steps:
            try:
                tool = self._registry.get(step.tool_name)
            except ToolRuntimeError as exc:
                errors.append(
                    PlanValidationIssue(
                        "TOOL_NOT_REGISTERED",
                        str(exc),
                        "Replace the tool with one present in the supplied manifest",
                        step_id=step.step_id,
                        field="tool_name",
                    )
                )
                continue
            expected_type = _EXPECTED_TOOL_TYPES.get(step.tool_name)
            if expected_type is None or step.step_type is not expected_type:
                errors.append(
                    PlanValidationIssue(
                        "TOOL_STEP_TYPE_MISMATCH",
                        f"Step {step.step_id} uses an invalid tool/type pair",
                        "Use the manifest capability with its frozen StepType",
                        step_id=step.step_id,
                        field="step_type",
                    )
                )
            if (
                step.input_schema != tool.definition.input_schema
                or step.output_schema != tool.definition.output_schema
            ):
                errors.append(
                    PlanValidationIssue(
                        "TOOL_SCHEMA_MISMATCH",
                        f"Step {step.step_id} schemas differ from the registered definition",
                        "Copy the exact input and output schemas from the manifest",
                        step_id=step.step_id,
                        field="input_schema",
                    )
                )
        errors.extend(self._supplier_quality_rules(plan, contract))
        return PlanValidationResult(errors=tuple(errors))

    @staticmethod
    def _supplier_quality_rules(
        plan: TaskPlan,
        contract: TaskContract,
    ) -> list[PlanValidationIssue]:
        """Apply the frozen vertical-slice wiring outside generic registry checks."""
        if contract.task_type.value != "supplier_quality_analysis.v1":
            return []
        issues: list[PlanValidationIssue] = []
        by_tool = {step.tool_name: step for step in plan.steps}
        database = by_tool.get(CapabilityName.DATABASE_QUERY.value)
        analytics = by_tool.get(CapabilityName.ANALYSIS_ENGINE.value)
        knowledge = by_tool.get(CapabilityName.KNOWLEDGE_SEARCH.value)
        report = by_tool.get(CapabilityName.REPORT_GENERATOR.value)
        if database and analytics and database.step_id not in analytics.dependency:
            issues.append(
                PlanValidationIssue(
                    "ANALYTICS_DATABASE_DEPENDENCY_MISSING",
                    "Analytics must depend on the database evidence step",
                    "Add the database step_id to the analytics dependency list",
                    step_id=analytics.step_id,
                    field="dependency",
                )
            )
        if report and analytics and analytics.step_id not in report.dependency:
            issues.append(
                PlanValidationIssue(
                    "REPORT_ANALYTICS_DEPENDENCY_MISSING",
                    "Report generation must depend on the analytics step",
                    "Add the analytics step_id to the report dependency list",
                    step_id=report.step_id,
                    field="dependency",
                )
            )
        if report and knowledge and knowledge.step_id not in report.dependency:
            issues.append(
                PlanValidationIssue(
                    "REPORT_KNOWLEDGE_DEPENDENCY_MISSING",
                    "Report generation must depend on the knowledge step",
                    "Add the knowledge step_id to the report dependency list",
                    step_id=report.step_id,
                    field="dependency",
                )
            )
        return issues


__all__ = ["PlanValidationIssue", "PlanValidationResult", "PlanValidator"]
