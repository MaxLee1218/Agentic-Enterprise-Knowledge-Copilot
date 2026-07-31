"""LLM task-understanding, planning, repair, and constrained replan adapter."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from copilot.contracts import (
    ExpectedOutput,
    StepResult,
    StepResultStatus,
    StepType,
    TaskConstraints,
    TaskContract,
    TaskPlan,
    TaskRequest,
)
from copilot.llm.manifest import PlannerToolManifestBuilder
from copilot.llm.prompts import (
    PLAN_REPAIR_PROMPT_VERSION,
    PLANNER_PROMPT_VERSION,
    REPLAN_PROMPT_VERSION,
    TASK_UNDERSTANDING_PROMPT_VERSION,
    plan_repair_messages,
    planner_messages,
    replan_messages,
    task_understanding_messages,
)
from copilot.llm.schemas import TaskUnderstandingOutput
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMProvider,
    LLMSchemaValidationError,
)
from copilot.services.workflows.planning import (
    PlanGenerationOutcome,
    TaskUnderstandingOutcome,
)
from copilot.services.workflows.validation import (
    PlanValidationIssue,
    PlanValidator,
)

_UNDERSTANDING_SCHEMA_VERSION = "task-understanding-schema-v1"
_PLAN_SCHEMA_VERSION = "task-plan-v1"
_ALLOWED_REPLAN_REASONS = {
    "PLAN_NO_LONGER_EXECUTABLE",
    "REPAIRABLE_VERIFICATION_FAILURE",
    "TOOL_DATA_INSUFFICIENT",
    "KNOWLEDGE_EVIDENCE_INSUFFICIENT",
    "RECOVERABLE_TOOL_FAILURE_EXHAUSTED",
}


class LLMPlanningService:
    """Compose prompts around an injected provider and deterministic plan validator."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        manifest_builder: PlannerToolManifestBuilder,
        validator: PlanValidator,
        options: LLMGenerationOptions | None = None,
        max_plan_repair_attempts: int = 2,
    ) -> None:
        if max_plan_repair_attempts < 0:
            raise ValueError("max_plan_repair_attempts must not be negative")
        self._provider = provider
        self._manifest_builder = manifest_builder
        self._validator = validator
        self._options = options or LLMGenerationOptions()
        self._max_repairs = max_plan_repair_attempts

    def understand(
        self,
        *,
        request: TaskRequest,
        trusted_contract: TaskContract,
        trace_id: str,
        max_steps: int,
    ) -> TaskUnderstandingOutcome:
        """Produce a frozen contract while preserving trusted tenant and policy fields."""
        context = LLMCallContext(
            task_id=trusted_contract.task_id,
            trace_id=trace_id,
            node_name="understand_task",
            attempt=1,
            prompt_version=TASK_UNDERSTANDING_PROMPT_VERSION,
            schema_version=_UNDERSTANDING_SCHEMA_VERSION,
        )
        result = self._provider.generate_structured(
            messages=task_understanding_messages(
                request=request,
                trusted_context={
                    "reference_time_utc": request.created_at.isoformat(),
                    "tenant_id": trusted_contract.constraints.tenant_id,
                    "authorized_data_scope": list(trusted_contract.constraints.data_scope),
                    "authorized_supplier_scope": list(trusted_contract.constraints.supplier_ids),
                    "system_max_steps": max_steps,
                    "read_only": True,
                },
                output_schema=TaskUnderstandingOutput,
            ),
            output_schema=TaskUnderstandingOutput,
            context=context,
            options=self._options,
        )
        candidate = result.parsed_output
        missing = list(candidate.missing_information)
        if candidate.time_range.year is None or candidate.time_range.quarter is None:
            missing.append("An explicit year and quarter are required")
        if missing:
            return TaskUnderstandingOutcome(
                contract=None,
                missing_information=tuple(dict.fromkeys(missing)),
            )
        assert candidate.time_range.year is not None
        assert candidate.time_range.quarter is not None
        constraints = trusted_contract.constraints
        if (
            candidate.time_range.year != constraints.year
            or candidate.time_range.quarter != constraints.quarter
        ):
            raise LLMSchemaValidationError(
                "LLM time range conflicts with the deterministically validated request scope"
            )
        if (
            candidate.entities.supplier_ids
            and constraints.supplier_ids
            and not set(candidate.entities.supplier_ids).issubset(constraints.supplier_ids)
        ):
            raise LLMSchemaValidationError(
                "LLM supplier entities exceed the trusted authorized request scope"
            )
        if candidate.constraints.max_steps > max_steps or not candidate.constraints.read_only:
            raise LLMSchemaValidationError("LLM output attempted to relax trusted execution limits")
        if (
            candidate.deliverable.artifact_type != trusted_contract.expected_output.artifact_type
            or candidate.deliverable.language != trusted_contract.expected_output.language
        ):
            raise LLMSchemaValidationError(
                "LLM deliverable conflicts with the validated interface request"
            )
        start_date, end_date = _quarter_dates(
            candidate.time_range.year, candidate.time_range.quarter
        )
        updated_constraints = TaskConstraints(
            **{
                **constraints.model_dump(),
                "start_date": start_date,
                "end_date": end_date,
                "metrics": candidate.constraints.metrics,
            }
        )
        contract = TaskContract(
            **{
                **trusted_contract.model_dump(),
                "task_type": candidate.task_type,
                "expected_output": ExpectedOutput(
                    artifact_type=candidate.deliverable.artifact_type,
                    required_sections=candidate.deliverable.required_sections,
                    language=candidate.deliverable.language,
                    citations_required=True,
                ),
                "constraints": updated_constraints,
            }
        )
        return TaskUnderstandingOutcome(contract=contract)

    def create_validated_plan(
        self,
        *,
        request: TaskRequest,
        contract: TaskContract,
        trace_id: str,
        max_steps: int,
    ) -> PlanGenerationOutcome:
        """Generate then repair only parseable deterministic validation failures."""
        del request
        outcome = self.create_plan(
            contract=contract,
            trace_id=trace_id,
            max_steps=max_steps,
        )
        plan = outcome.plan
        validation = outcome.validation
        repairs = 0
        while not validation.is_valid and validation.is_repairable and repairs < self._max_repairs:
            repairs += 1
            outcome = self.repair_plan(
                contract=contract,
                invalid_plan=plan,
                errors=validation.errors,
                trace_id=trace_id,
                max_steps=max_steps,
                attempt=repairs,
            )
            plan = outcome.plan
            validation = outcome.validation
        return PlanGenerationOutcome(
            plan=plan,
            validation=validation,
            repair_attempts=repairs,
        )

    def create_plan(
        self,
        *,
        contract: TaskContract,
        trace_id: str,
        max_steps: int,
    ) -> PlanGenerationOutcome:
        """Generate exactly one candidate so LangGraph can checkpoint it."""
        manifest = self._manifest_builder.build()
        result = self._provider.generate_structured(
            messages=planner_messages(
                contract=contract,
                manifest=manifest,
                max_steps=max_steps,
            ),
            output_schema=TaskPlan,
            context=LLMCallContext(
                task_id=contract.task_id,
                trace_id=trace_id,
                node_name="create_plan",
                attempt=1,
                prompt_version=PLANNER_PROMPT_VERSION,
                schema_version=_PLAN_SCHEMA_VERSION,
            ),
            options=self._options,
        )
        plan = result.parsed_output
        validation = self._validator.evaluate(plan, contract)
        return PlanGenerationOutcome(
            plan=plan,
            validation=validation,
        )

    def repair_plan(
        self,
        *,
        contract: TaskContract,
        invalid_plan: TaskPlan,
        errors: tuple[PlanValidationIssue, ...],
        trace_id: str,
        max_steps: int,
        attempt: int,
    ) -> PlanGenerationOutcome:
        """Perform one model repair and immediately run the complete validator."""
        manifest = self._manifest_builder.build()
        repaired = self._provider.generate_structured(
            messages=plan_repair_messages(
                contract=contract,
                manifest=manifest,
                invalid_plan=invalid_plan,
                errors=errors,
                max_steps=max_steps,
            ),
            output_schema=TaskPlan,
            context=LLMCallContext(
                task_id=contract.task_id,
                trace_id=trace_id,
                node_name="repair_plan",
                attempt=attempt,
                prompt_version=PLAN_REPAIR_PROMPT_VERSION,
                schema_version=_PLAN_SCHEMA_VERSION,
            ),
            options=self._options,
        )
        plan = repaired.parsed_output
        return PlanGenerationOutcome(
            plan=plan,
            validation=self._validator.evaluate(plan, contract),
            repair_attempts=attempt,
        )

    def replan(
        self,
        *,
        contract: TaskContract,
        current_plan: TaskPlan,
        step_results: tuple[StepResult, ...],
        evidence_ids: tuple[str, ...],
        reason: str,
        trace_id: str,
        remaining_steps: int,
    ) -> PlanGenerationOutcome:
        """Create one higher-version candidate for an allowlisted recoverable reason."""
        if reason not in _ALLOWED_REPLAN_REASONS:
            raise ValueError("replan reason is not recoverable or allowlisted")
        if remaining_steps < 1:
            raise ValueError("no remaining step budget is available")
        manifest = self._manifest_builder.build()
        next_version = current_plan.planning_version + 1
        summary: dict[str, object] = {
            "reason": reason,
            "completed_steps": [
                item.step_id for item in step_results if item.status.value == "SUCCESS"
            ],
            "evidence_ids": list(evidence_ids),
        }
        generated = self._provider.generate_structured(
            messages=replan_messages(
                contract=contract,
                current_plan=current_plan,
                manifest=manifest,
                execution_summary=summary,
                remaining_steps=remaining_steps,
                next_version=next_version,
            ),
            output_schema=TaskPlan,
            context=LLMCallContext(
                task_id=contract.task_id,
                trace_id=trace_id,
                node_name="replan",
                attempt=1,
                prompt_version=REPLAN_PROMPT_VERSION,
                schema_version=_PLAN_SCHEMA_VERSION,
            ),
            options=self._options,
        )
        plan = generated.parsed_output
        if plan.planning_version != next_version:
            raise LLMSchemaValidationError("Replan returned an invalid planning_version")
        successful_ids = {
            item.step_id for item in step_results if item.status is StepResultStatus.SUCCESS
        }
        protected = {
            step.step_id: step
            for step in current_plan.steps
            if step.step_id in successful_ids and step.step_type is not StepType.REPORT_GENERATION
        }
        revised = {step.step_id: step for step in plan.steps}
        if any(revised.get(step_id) != step for step_id, step in protected.items()):
            raise LLMSchemaValidationError(
                "Replan changed or removed an already successful non-report step"
            )
        new_work = [step for step in plan.steps if step.step_id not in protected]
        if len(new_work) > remaining_steps:
            raise LLMSchemaValidationError("Replan exceeded the remaining step budget")
        validation = self._validator.evaluate(plan, contract)
        return PlanGenerationOutcome(plan=plan, validation=validation)


def _quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    return date(year, start_month, 1), date(year, end_month, monthrange(year, end_month)[1])


__all__ = ["LLMPlanningService"]
