"""Structured execution-time validation layered on the frozen TaskPlan DAG contract."""

from dataclasses import dataclass

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    CapabilityName,
    StepType,
    TaskContract,
    TaskPlan,
)
from copilot.services.domains import (
    DomainCapabilityManifestRegistry,
    DomainManifestError,
    builtin_domain_manifest_registry,
)
from copilot.services.workflows.accounts_payable_plan import (
    AGGREGATE_EXCEPTION_SUMMARY,
    AGGREGATE_SUPPLIER_RATE,
    AP_POPULATION_SUFFIX,
    RETRIEVE_AP_POLICY,
    ap_report_step_id,
    ap_step_id,
    selected_ap_detection_bindings,
)
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
        domain_manifests: DomainCapabilityManifestRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._max_task_steps = max_task_steps
        self._max_planning_version = max_planning_version
        self._domain_manifests = domain_manifests or builtin_domain_manifest_registry()

    def validate(self, plan: TaskPlan, contract: TaskContract) -> None:
        """Raise the legacy boundary error when structured evaluation finds an issue."""
        result = self.evaluate(plan, contract)
        if result.errors:
            raise PlanValidationError(result.errors[0].message)

    def evaluate(self, plan: TaskPlan, contract: TaskContract) -> PlanValidationResult:
        """Return all safe deterministic findings without invoking any tool."""
        errors: list[PlanValidationIssue] = []
        try:
            domain_manifest = self._domain_manifests.require_execution(contract)
        except DomainManifestError as exc:
            errors.append(
                PlanValidationIssue(
                    exc.code,
                    str(exc),
                    "Use an enabled domain manifest matching the validated TaskContract",
                    field="task_type",
                    repairable=False,
                )
            )
            return PlanValidationResult(errors=tuple(errors))
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
        allowed = {item.value for item in domain_manifest.capabilities}
        capability_mismatch = required != planned or not planned.issubset(allowed)
        if capability_mismatch:
            errors.append(
                PlanValidationIssue(
                    "PLAN_CAPABILITY_MISMATCH",
                    "Plan capabilities do not satisfy the contract and domain allowlist",
                    "Include every required capability and no capability outside the manifest",
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
                expected_profile = domain_manifest.profile_for(step.tool_name)
                if step.contract_profile != expected_profile:
                    raise DomainManifestError(
                        "TOOL_PROFILE_MISMATCH",
                        f"Step {step.step_id} uses a profile outside its domain manifest",
                    )
                tool = self._registry.get_profile(
                    step.tool_name,
                    step.tool_version,
                    step.contract_profile,
                )
            except (DomainManifestError, ToolRuntimeError) as exc:
                errors.append(
                    PlanValidationIssue(
                        getattr(exc, "code", "TOOL_PROFILE_NOT_REGISTERED"),
                        str(exc),
                        "Use the exact tool version and profile present in the supplied manifest",
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
        if domain_manifest.plan_profile == "supplier_quality_plan.v1":
            errors.extend(self._supplier_quality_rules(plan, contract))
        elif domain_manifest.plan_profile == "accounts_payable_plan.v1":
            errors.extend(self._accounts_payable_rules(plan, contract))
        return PlanValidationResult(errors=tuple(errors))

    @staticmethod
    def _accounts_payable_rules(
        plan: TaskPlan,
        contract: TaskContract,
    ) -> list[PlanValidationIssue]:
        """Reject any AP template/operation escape or incomplete deterministic wiring."""
        if not isinstance(contract.constraints, AccountsPayableConstraintsV1):
            return [
                PlanValidationIssue(
                    "AP_CONSTRAINT_PROFILE_MISMATCH",
                    "Accounts Payable planning requires AP constraints",
                    "Keep the validated Accounts Payable Contract unchanged",
                    field="contract.constraints",
                    repairable=False,
                )
            ]
        task_id = contract.task_id
        selected = selected_ap_detection_bindings(contract.constraints)
        expected_ids = {
            ap_step_id(task_id, RETRIEVE_AP_POLICY),
            ap_step_id(task_id, AP_POPULATION_SUFFIX),
            ap_step_id(task_id, AGGREGATE_EXCEPTION_SUMMARY),
            ap_step_id(task_id, AGGREGATE_SUPPLIER_RATE),
            ap_report_step_id(task_id, plan.planning_version),
            *(ap_step_id(task_id, binding.database_suffix) for binding in selected),
            *(ap_step_id(task_id, binding.analysis_suffix) for binding in selected),
        }
        actual_ids = {step.step_id for step in plan.steps}
        issues: list[PlanValidationIssue] = []
        if actual_ids != expected_ids:
            issues.append(
                PlanValidationIssue(
                    "AP_PLAN_OPERATION_SET_MISMATCH",
                    "AP Plan does not exactly match the requested frozen templates and operations",
                    "Use only the canonical AP steps mapped from Contract exception_types",
                    field="steps",
                )
            )
            return issues
        by_id = {step.step_id: step for step in plan.steps}
        population_id = ap_step_id(task_id, AP_POPULATION_SUFFIX)
        detection_ids: list[str] = []
        for binding in selected:
            database_id = ap_step_id(task_id, binding.database_suffix)
            analysis_id = ap_step_id(task_id, binding.analysis_suffix)
            detection_ids.append(analysis_id)
            expected_dependencies = {population_id, database_id}
            if set(by_id[analysis_id].dependency) != expected_dependencies:
                issues.append(
                    PlanValidationIssue(
                        "AP_DETECTION_DEPENDENCY_MISMATCH",
                        f"AP detection {analysis_id} lacks its exact governed datasets",
                        "Depend on the common population and mapped dedicated database step",
                        step_id=analysis_id,
                        field="dependency",
                    )
                )
        aggregate_dependencies = {population_id, *detection_ids}
        for suffix in (AGGREGATE_EXCEPTION_SUMMARY, AGGREGATE_SUPPLIER_RATE):
            aggregate_id = ap_step_id(task_id, suffix)
            if set(by_id[aggregate_id].dependency) != aggregate_dependencies:
                issues.append(
                    PlanValidationIssue(
                        "AP_AGGREGATION_DEPENDENCY_MISMATCH",
                        f"AP aggregation {aggregate_id} lacks the complete detection lineage",
                        "Depend on the population and every requested detection step",
                        step_id=aggregate_id,
                        field="dependency",
                    )
                )
        report_id = ap_report_step_id(task_id, plan.planning_version)
        expected_report_dependencies = {
            ap_step_id(task_id, RETRIEVE_AP_POLICY),
            ap_step_id(task_id, AGGREGATE_EXCEPTION_SUMMARY),
            ap_step_id(task_id, AGGREGATE_SUPPLIER_RATE),
        }
        if set(by_id[report_id].dependency) != expected_report_dependencies:
            issues.append(
                PlanValidationIssue(
                    "AP_REPORT_DEPENDENCY_MISMATCH",
                    "AP report lacks policy, exception summary, or supplier-rate lineage",
                    "Depend on the policy and both frozen AP aggregation steps",
                    step_id=report_id,
                    field="dependency",
                )
            )
        if plan.steps[-1].step_id != report_id:
            issues.append(
                PlanValidationIssue(
                    "AP_REPORT_NOT_FINAL",
                    "The canonical AP report must be the final Plan step",
                    "Move the canonical AP report step to the end",
                    step_id=report_id,
                    field="steps",
                )
            )
        return issues

    @staticmethod
    def _supplier_quality_rules(
        plan: TaskPlan,
        contract: TaskContract,
    ) -> list[PlanValidationIssue]:
        """Apply the frozen vertical-slice wiring outside generic registry checks."""
        if contract.task_type.value != "supplier_quality_analysis.v1":
            return []
        issues: list[PlanValidationIssue] = []
        tool_names = [step.tool_name for step in plan.steps]
        if len(tool_names) != len(set(tool_names)):
            issues.append(
                PlanValidationIssue(
                    "SUPPLIER_CAPABILITY_DUPLICATED",
                    "Supplier Quality permits at most one step for each capability",
                    "Use one semantic step per selected capability",
                    field="steps",
                )
            )
            return issues
        by_tool = {step.tool_name: step for step in plan.steps}
        database = by_tool.get(CapabilityName.DATABASE_QUERY.value)
        analytics = by_tool.get(CapabilityName.ANALYSIS_ENGINE.value)
        knowledge = by_tool.get(CapabilityName.KNOWLEDGE_SEARCH.value)
        report = by_tool.get(CapabilityName.REPORT_GENERATOR.value)
        if database is None or analytics is None or report is None:
            issues.append(
                PlanValidationIssue(
                    "SUPPLIER_REPORT_CHAIN_INCOMPLETE",
                    "Supplier Quality requires database, analysis, and report capabilities",
                    "Include the complete governed report chain",
                    field="steps",
                )
            )
            return issues
        for root in (database, knowledge):
            if root is not None and root.dependency:
                issues.append(
                    PlanValidationIssue(
                        "SUPPLIER_ROOT_DEPENDENCY_INVALID",
                        f"Supplier root step {root.step_id} must not have dependencies",
                        "Keep knowledge and database retrieval as root steps",
                        step_id=root.step_id,
                        field="dependency",
                    )
                )
        if set(analytics.dependency) != {database.step_id}:
            issues.append(
                PlanValidationIssue(
                    "ANALYTICS_DATABASE_DEPENDENCY_MISSING",
                    "Analytics must depend only on the database evidence step",
                    "Use the database step_id as the analytics dependency",
                    step_id=analytics.step_id,
                    field="dependency",
                )
            )
        expected_report_dependencies = {analytics.step_id}
        if knowledge is not None:
            expected_report_dependencies.add(knowledge.step_id)
        if set(report.dependency) != expected_report_dependencies:
            issues.append(
                PlanValidationIssue(
                    "REPORT_DEPENDENCY_MISMATCH",
                    "Report dependencies do not match the selected evidence-producing steps",
                    "Depend on analysis and, when selected, knowledge search",
                    step_id=report.step_id,
                    field="dependency",
                )
            )
        return issues


__all__ = ["PlanValidationIssue", "PlanValidationResult", "PlanValidator"]
