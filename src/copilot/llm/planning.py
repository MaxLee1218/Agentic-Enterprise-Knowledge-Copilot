"""LLM task-understanding, planning, repair, and constrained replan adapter."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    APExceptionType,
    ApprovalRequirement,
    ArtifactType,
    CapabilityName,
    ContractSchemaVersion,
    DateRange,
    ExpectedOutput,
    MoneyThreshold,
    StepResult,
    StepResultStatus,
    StepType,
    TaskConstraints,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskType,
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
from copilot.llm.schemas import APTaskUnderstandingOutput, TaskUnderstandingOutput
from copilot.services.domains import (
    DomainCapabilityManifestRegistry,
    builtin_domain_manifest_registry,
)
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMProvider,
    LLMSchemaValidationError,
    LLMTokenBudgetExceededError,
)
from copilot.services.task_intake import TrustedTaskContext
from copilot.services.workflows.planning import (
    PlanGenerationOutcome,
    TaskUnderstandingOutcome,
)
from copilot.services.workflows.validation import (
    PlanValidationIssue,
    PlanValidator,
)

_ALLOWED_REPLAN_REASONS = {
    "PLAN_NO_LONGER_EXECUTABLE",
    "REPAIRABLE_VERIFICATION_FAILURE",
    "TOOL_DATA_INSUFFICIENT",
    "KNOWLEDGE_EVIDENCE_INSUFFICIENT",
    "RECOVERABLE_TOOL_FAILURE_EXHAUSTED",
}

_AP_REQUIRED_SECTIONS = (
    "scope",
    "data_overview",
    "applicable_policies",
    "exception_summary",
    "duplicate_invoice_findings",
    "po_compliance_findings",
    "payment_findings",
    "supplier_summary",
    "risk_observations",
    "recommended_actions",
    "limitations",
    "evidence",
    "execution_trace",
)


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
        domain_manifests: DomainCapabilityManifestRegistry | None = None,
    ) -> None:
        if max_plan_repair_attempts < 0:
            raise ValueError("max_plan_repair_attempts must not be negative")
        self._provider = provider
        self._manifest_builder = manifest_builder
        self._validator = validator
        self._options = options or LLMGenerationOptions()
        self._max_repairs = max_plan_repair_attempts
        self._domain_manifests = domain_manifests or builtin_domain_manifest_registry()

    def understand(
        self,
        *,
        request: TaskRequest,
        trusted_context: TrustedTaskContext,
        trace_id: str,
        max_steps: int,
    ) -> TaskUnderstandingOutcome:
        """Produce a frozen contract while preserving trusted tenant and policy fields."""
        domain_manifest = self._domain_manifests.require_execution_for_type(
            trusted_context.task_type
        )
        context = LLMCallContext(
            task_id=trusted_context.task_id,
            trace_id=trace_id,
            node_name="understand_task",
            attempt=1,
            prompt_version=TASK_UNDERSTANDING_PROMPT_VERSION,
            schema_version=domain_manifest.understanding_profile,
        )
        understanding_schema = (
            APTaskUnderstandingOutput
            if domain_manifest.understanding_profile == "accounts_payable_understanding.v1"
            else TaskUnderstandingOutput
        )
        supported_outputs = list(domain_manifest.artifact_types)
        result = self._provider.generate_structured(
            messages=task_understanding_messages(
                request=request,
                trusted_context={
                    "reference_time_utc": request.created_at.isoformat(),
                    "task_type": trusted_context.task_type.value,
                    "tenant_id": trusted_context.tenant_id,
                    "authorized_data_scope": list(trusted_context.data_scope),
                    "authorized_supplier_scope": list(trusted_context.authorized_supplier_ids),
                    "authorized_legal_entity_scope": list(
                        trusted_context.authorized_legal_entity_ids
                    ),
                    "authorized_business_unit_scope": list(
                        trusted_context.authorized_business_unit_ids
                    ),
                    "authorized_currency_scope": list(trusted_context.authorized_currency_scope),
                    "system_max_steps": max_steps,
                    "read_only": trusted_context.read_only,
                    "output_format": (
                        trusted_context.output_format.value
                        if trusted_context.output_format is not None
                        else None
                    ),
                    "supported_output_formats": [item.value for item in supported_outputs],
                },
                output_schema=understanding_schema,
            ),
            output_schema=understanding_schema,
            context=context,
            options=self._options,
        )
        _enforce_token_budget(
            result.usage.output_tokens,
            self._options.max_output_tokens,
            attempts=result.attempts,
        )
        candidate = result.parsed_output
        if isinstance(candidate, APTaskUnderstandingOutput):
            return self._accounts_payable_contract(
                request=request,
                trusted_context=trusted_context,
                candidate=candidate,
            )
        assert isinstance(candidate, TaskUnderstandingOutput)
        if candidate.task_type is not trusted_context.task_type:
            raise LLMSchemaValidationError(
                "LLM task type conflicts with the trusted domain selection"
            )
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
        if (
            candidate.entities.supplier_ids
            and trusted_context.authorized_supplier_ids
            and not set(candidate.entities.supplier_ids).issubset(
                trusted_context.authorized_supplier_ids
            )
        ):
            raise LLMSchemaValidationError(
                "LLM supplier entities exceed the trusted authorized request scope"
            )
        if (
            candidate.constraints.max_steps > max_steps
            or not candidate.constraints.read_only
            or not trusted_context.read_only
        ):
            raise LLMSchemaValidationError("LLM output attempted to relax trusted execution limits")
        if (
            trusted_context.output_format is not None
            and candidate.deliverable.artifact_type != trusted_context.output_format
        ):
            raise LLMSchemaValidationError(
                "LLM deliverable conflicts with the validated interface request"
            )
        start_date, end_date = _quarter_dates(
            candidate.time_range.year, candidate.time_range.quarter
        )
        supplier_ids = (
            candidate.entities.supplier_ids
            if candidate.entities.supplier_ids
            else trusted_context.authorized_supplier_ids
        )
        updated_constraints = TaskConstraints(
            year=candidate.time_range.year,
            quarter=candidate.time_range.quarter,
            start_date=start_date,
            end_date=end_date,
            supplier_ids=supplier_ids,
            tenant_id=trusted_context.tenant_id,
            data_scope=trusted_context.data_scope,
            metrics=candidate.constraints.metrics,
            deadline_at=trusted_context.deadline_at,
        )
        contract = TaskContract(
            contract_schema_version=ContractSchemaVersion.TASK_CONTRACT_V1,
            task_id=trusted_context.task_id,
            contract_version=1,
            task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
            goal=candidate.goal,
            required_capabilities=tuple(CapabilityName),
            expected_output=ExpectedOutput(
                artifact_type=(
                    trusted_context.output_format or candidate.deliverable.artifact_type
                ),
                required_sections=candidate.deliverable.required_sections,
                language=candidate.deliverable.language,
                citations_required=True,
            ),
            constraints=updated_constraints,
            approval_requirement=ApprovalRequirement(
                required=trusted_context.require_approval,
                policy_id=("task-intake-approval-v1" if trusted_context.require_approval else None),
                approver_role=(
                    "quality_data_approver" if trusted_context.require_approval else None
                ),
                controlled_scope=(
                    trusted_context.data_scope if trusted_context.require_approval else ()
                ),
            ),
            created_at=request.created_at,
        )
        return TaskUnderstandingOutcome(contract=contract)

    @staticmethod
    def _accounts_payable_contract(
        *,
        request: TaskRequest,
        trusted_context: TrustedTaskContext,
        candidate: APTaskUnderstandingOutput,
    ) -> TaskUnderstandingOutcome:
        """Merge an untrusted AP candidate into explicit trusted scope and policy facts."""
        if candidate.task_type is not trusted_context.task_type:
            raise LLMSchemaValidationError(
                "LLM task type conflicts with the trusted domain selection"
            )
        missing = list(candidate.missing_information)
        if candidate.time_range.start_date is None or candidate.time_range.end_date is None:
            missing.append("An explicit Accounts Payable date range is required")
        legal_entities = _bounded_scope(
            "legal entity",
            candidate.requested_legal_entity_ids,
            trusted_context.authorized_legal_entity_ids,
        )
        if not legal_entities:
            if len(trusted_context.authorized_legal_entity_ids) == 1:
                legal_entities = trusted_context.authorized_legal_entity_ids
            else:
                missing.append("An explicit authorized legal entity is required")
        if missing:
            return TaskUnderstandingOutcome(
                contract=None,
                missing_information=tuple(dict.fromkeys(missing)),
            )
        assert candidate.time_range.start_date is not None
        assert candidate.time_range.end_date is not None
        supplier_ids = _bounded_scope(
            "supplier",
            candidate.requested_supplier_ids,
            trusted_context.authorized_supplier_ids,
            omitted_default=True,
        )
        business_units = _bounded_scope(
            "business unit",
            candidate.requested_business_unit_ids,
            trusted_context.authorized_business_unit_ids,
            omitted_default=True,
        )
        currencies = _bounded_scope(
            "currency",
            candidate.currency_scope,
            trusted_context.authorized_currency_scope,
            omitted_default=True,
        )
        if (
            trusted_context.policy_rule_set_id is None
            or trusted_context.policy_rule_set_version is None
            or trusted_context.policy_manifest_checksum is None
            or trusted_context.policy_snapshot_at is None
            or not trusted_context.policy_materiality
        ):
            raise LLMSchemaValidationError("Trusted Accounts Payable policy context is incomplete")
        policy_thresholds = {
            item.currency: item.amount for item in trusted_context.policy_materiality
        }
        selected_currency_set = set(currencies) or set(policy_thresholds)
        if not selected_currency_set.issubset(policy_thresholds):
            raise LLMSchemaValidationError("Requested currency has no trusted policy materiality")
        requested_thresholds = {
            item.currency: item.amount for item in candidate.requested_materiality
        }
        if not set(requested_thresholds).issubset(selected_currency_set):
            raise LLMSchemaValidationError(
                "Requested materiality currency is outside the authorized scope"
            )
        if any(
            amount > policy_thresholds[currency]
            for currency, amount in requested_thresholds.items()
        ):
            raise LLMSchemaValidationError(
                "Requested materiality attempted to relax a trusted policy threshold"
            )
        effective_materiality = tuple(
            MoneyThreshold(
                currency=currency,
                amount=requested_thresholds.get(currency, policy_thresholds[currency]),
            )
            for currency in sorted(selected_currency_set)
        )
        requested_materiality = tuple(
            MoneyThreshold(currency=currency, amount=requested_thresholds[currency])
            for currency in sorted(requested_thresholds)
        )
        artifact_type = trusted_context.output_format or candidate.deliverable.artifact_type
        if artifact_type not in {
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF,
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON,
        }:
            raise LLMSchemaValidationError("Unsupported Accounts Payable deliverable")
        deadline_at = min(
            trusted_context.deadline_at,
            request.created_at + timedelta(seconds=180),
        )
        constraints = AccountsPayableConstraintsV1(
            time_range=DateRange(
                start_date=candidate.time_range.start_date,
                end_date=candidate.time_range.end_date,
            ),
            supplier_ids=supplier_ids,
            legal_entity_ids=legal_entities,
            business_unit_ids=business_units,
            currency_scope=currencies,
            exception_types=candidate.exception_types or tuple(APExceptionType),
            requested_materiality=requested_materiality,
            effective_materiality=effective_materiality,
            include_policy_comparison=candidate.include_policy_comparison,
            tenant_id=trusted_context.tenant_id,
            data_scope=trusted_context.data_scope,
            policy_rule_set_id=trusted_context.policy_rule_set_id,
            policy_rule_set_version=trusted_context.policy_rule_set_version,
            policy_manifest_checksum=trusted_context.policy_manifest_checksum,
            snapshot_at=trusted_context.policy_snapshot_at,
            deadline_at=deadline_at,
            read_only=True,
        )
        return TaskUnderstandingOutcome(
            contract=TaskContract(
                contract_schema_version=ContractSchemaVersion.TASK_CONTRACT_V2,
                task_id=trusted_context.task_id,
                contract_version=1,
                task_type=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
                goal=candidate.goal,
                required_capabilities=tuple(CapabilityName),
                expected_output=ExpectedOutput(
                    artifact_type=artifact_type,
                    required_sections=_AP_REQUIRED_SECTIONS,
                    language=candidate.deliverable.language,
                    citations_required=True,
                ),
                constraints=constraints,
                approval_requirement=ApprovalRequirement(
                    required=trusted_context.require_approval,
                    policy_id=(
                        "accounts-payable-approval-v1" if trusted_context.require_approval else None
                    ),
                    approver_role=(
                        "finance_approver" if trusted_context.require_approval else None
                    ),
                    controlled_scope=(
                        trusted_context.data_scope if trusted_context.require_approval else ()
                    ),
                ),
                created_at=request.created_at,
            )
        )

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
        domain_manifest = self._domain_manifests.require_execution(contract)
        manifest = self._manifest_builder.build(domain_manifest)
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
                schema_version=domain_manifest.plan_profile,
            ),
            options=self._options,
        )
        _enforce_token_budget(
            result.usage.output_tokens,
            self._options.max_output_tokens,
            attempts=result.attempts,
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
        domain_manifest = self._domain_manifests.require_execution(contract)
        manifest = self._manifest_builder.build(domain_manifest)
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
                schema_version=domain_manifest.plan_profile,
            ),
            options=self._options,
        )
        _enforce_token_budget(
            repaired.usage.output_tokens,
            self._options.max_output_tokens,
            attempts=repaired.attempts,
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
        domain_manifest = self._domain_manifests.require_execution(contract)
        manifest = self._manifest_builder.build(domain_manifest)
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
                schema_version=domain_manifest.plan_profile,
            ),
            options=self._options,
        )
        _enforce_token_budget(
            generated.usage.output_tokens,
            self._options.max_output_tokens,
            attempts=generated.attempts,
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


def _bounded_scope(
    label: str,
    requested: tuple[str, ...],
    authorized: tuple[str, ...],
    *,
    omitted_default: bool = False,
) -> tuple[str, ...]:
    if requested and authorized and not set(requested).issubset(authorized):
        raise LLMSchemaValidationError(f"Requested {label} scope exceeds trusted authorization")
    if requested:
        return requested
    return authorized if omitted_default else ()


def _enforce_token_budget(output_tokens: int, maximum: int, *, attempts: int) -> None:
    if output_tokens > maximum:
        raise LLMTokenBudgetExceededError(
            "Structured LLM output exceeded the configured token budget",
            attempts=attempts,
        )


__all__ = ["LLMPlanningService"]
