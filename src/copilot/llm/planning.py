"""LLM task-understanding, planning, repair, and constrained replan adapter."""

from __future__ import annotations

import json
import logging
from calendar import monthrange
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    APExceptionType,
    ApprovalRequirement,
    ArtifactType,
    CapabilityName,
    ClarificationContext,
    ClarificationInputType,
    ClarificationQuestion,
    ClarificationResponse,
    ContractSchemaVersion,
    DateRange,
    ExpectedOutput,
    JsonObject,
    MoneyThreshold,
    ProposedPlan,
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
from copilot.llm.schemas import (
    APTaskUnderstandingOutput,
    PlannerCapabilityManifest,
    TaskUnderstandingOutput,
)
from copilot.services.domains import (
    DomainCapabilityManifestRegistry,
    builtin_domain_manifest_registry,
)
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMInvalidResponseError,
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMSchemaValidationError,
    LLMTimeoutError,
    LLMTokenBudgetExceededError,
    StructuredLLMResult,
)
from copilot.services.task_intake import TrustedTaskContext
from copilot.services.workflows.errors import (
    PlannerCompilationError,
    PlannerError,
    PlannerInvalidJsonError,
    PlannerProviderError,
    PlannerRepairExhaustedError,
    PlannerSchemaValidationError,
    PlannerTimeoutError,
    PlannerUnsupportedCapabilityError,
)
from copilot.services.workflows.plan_compiler import PlanCompiler
from copilot.services.workflows.planning import (
    PlanGenerationOutcome,
    PlannerModelCall,
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
LOGGER = logging.getLogger(__name__)
_PROPOSED_PLAN_SCHEMA_VERSION = "proposed-plan.v1"

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
        max_structured_output_retries: int = 1,
        domain_manifests: DomainCapabilityManifestRegistry | None = None,
        compiler: PlanCompiler | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_plan_repair_attempts < 0:
            raise ValueError("max_plan_repair_attempts must not be negative")
        if max_structured_output_retries < 0:
            raise ValueError("max_structured_output_retries must not be negative")
        self._provider = provider
        self._manifest_builder = manifest_builder
        self._validator = validator
        self._options = options or LLMGenerationOptions()
        self._max_repairs = max_plan_repair_attempts
        self._max_structured_retries = max_structured_output_retries
        self._domain_manifests = domain_manifests or builtin_domain_manifest_registry()
        self._compiler = compiler or PlanCompiler(
            manifest_builder.registry,
            domain_manifests=self._domain_manifests,
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def understand(
        self,
        *,
        request: TaskRequest,
        trusted_context: TrustedTaskContext,
        trace_id: str,
        max_steps: int,
        clarification_context: ClarificationContext | None = None,
        clarification_response: ClarificationResponse | None = None,
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
                clarification_context=clarification_context,
                clarification_response=clarification_response,
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
                clarification_context=clarification_context,
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
            context_values = dict(
                clarification_context.values.root if clarification_context is not None else {}
            )
            if candidate.time_range.year is not None and candidate.time_range.quarter is not None:
                context_values.update(
                    year=candidate.time_range.year,
                    quarter=candidate.time_range.quarter,
                )
            return TaskUnderstandingOutcome(
                contract=None,
                missing_information=tuple(dict.fromkeys(missing)),
                questions=(
                    ClarificationQuestion(
                        field="time_range",
                        reason="Supplier Quality analysis requires an explicit reporting quarter.",
                        prompt="Which year and quarter should be analyzed (for example, Q2 2026)?",
                        input_type=ClarificationInputType.TEXT,
                        constraints=JsonObject({"format": "Q[1-4] YYYY"}),
                    ),
                ),
                clarification_context=ClarificationContext(values=JsonObject(context_values)),
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
        return TaskUnderstandingOutcome(
            contract=contract,
            clarification_context=ClarificationContext(
                values=JsonObject(
                    {"year": candidate.time_range.year, "quarter": candidate.time_range.quarter}
                )
            ),
        )

    @staticmethod
    def _accounts_payable_contract(
        *,
        request: TaskRequest,
        trusted_context: TrustedTaskContext,
        candidate: APTaskUnderstandingOutput,
        clarification_context: ClarificationContext | None = None,
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
            context_values = dict(
                clarification_context.values.root if clarification_context is not None else {}
            )
            questions: list[ClarificationQuestion] = []
            if candidate.time_range.start_date is not None:
                assert candidate.time_range.end_date is not None
                context_values["start_date"] = candidate.time_range.start_date.isoformat()
                context_values["end_date"] = candidate.time_range.end_date.isoformat()
            else:
                questions.append(
                    ClarificationQuestion(
                        field="time_range",
                        reason=(
                            "Accounts Payable analysis requires an explicit inclusive date range."
                        ),
                        prompt="What exact start and end dates should be analyzed?",
                        input_type=ClarificationInputType.DATE_RANGE,
                        constraints=JsonObject({"format": "YYYY-MM-DD"}),
                    )
                )
            if legal_entities:
                context_values["legal_entity_ids"] = list(legal_entities)
            else:
                questions.append(
                    ClarificationQuestion(
                        field="legal_entity_ids",
                        reason="A legal entity must be selected within the caller's current scope.",
                        prompt="Which authorized legal entity should be analyzed?",
                        input_type=ClarificationInputType.SINGLE_SELECT,
                        allowed_values=trusted_context.authorized_legal_entity_ids,
                    )
                )
            return TaskUnderstandingOutcome(
                contract=None,
                missing_information=tuple(dict.fromkeys(missing)),
                questions=tuple(questions),
                clarification_context=ClarificationContext(values=JsonObject(context_values)),
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
            ),
            clarification_context=ClarificationContext(
                values=JsonObject(
                    {
                        "start_date": candidate.time_range.start_date.isoformat(),
                        "end_date": candidate.time_range.end_date.isoformat(),
                        "legal_entity_ids": list(legal_entities),
                    }
                )
            ),
        )

    def create_validated_plan(
        self,
        *,
        request: TaskRequest,
        contract: TaskContract,
        trace_id: str,
        max_steps: int,
    ) -> PlanGenerationOutcome:
        """Generate, compile, and fully validate through the layered repair boundary."""
        del request
        return self.create_plan(
            contract=contract,
            trace_id=trace_id,
            max_steps=max_steps,
        )

    def create_plan(
        self,
        *,
        contract: TaskContract,
        trace_id: str,
        max_steps: int,
    ) -> PlanGenerationOutcome:
        """Generate a lightweight suggestion and compile one canonical TaskPlan."""
        domain_manifest = self._domain_manifests.require_execution(contract)
        manifest = self._manifest_builder.build(domain_manifest)
        return self._generate_and_compile(
            contract=contract,
            trace_id=trace_id,
            manifest=manifest,
            messages=planner_messages(
                contract=contract,
                manifest=manifest,
                max_steps=max_steps,
            ),
            node_name="create_plan",
            prompt_version=PLANNER_PROMPT_VERSION,
            planning_version=1,
            max_steps=max_steps,
            targeted_repair_budget=self._max_repairs,
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
        """Repair a rare compiler/validator escape without re-sending executable schemas."""
        domain_manifest = self._domain_manifests.require_execution(contract)
        manifest = self._manifest_builder.build(domain_manifest)
        return self._generate_and_compile(
            contract=contract,
            trace_id=trace_id,
            manifest=manifest,
            messages=plan_repair_messages(
                contract=contract,
                manifest=manifest,
                invalid_candidate=_task_plan_summary(invalid_plan),
                errors=_plan_validation_error_dicts(errors),
                max_steps=max_steps,
            ),
            node_name="repair_plan",
            prompt_version=PLAN_REPAIR_PROMPT_VERSION,
            planning_version=invalid_plan.planning_version,
            max_steps=max_steps,
            targeted_repair_budget=0,
            initial_repair_type="targeted_plan_repair",
            reported_repair_attempts=attempt,
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
        outcome = self._generate_and_compile(
            contract=contract,
            trace_id=trace_id,
            manifest=manifest,
            messages=replan_messages(
                contract=contract,
                current_plan=current_plan,
                manifest=manifest,
                execution_summary=summary,
                remaining_steps=remaining_steps,
                next_version=next_version,
            ),
            node_name="replan",
            prompt_version=REPLAN_PROMPT_VERSION,
            planning_version=next_version,
            max_steps=len(current_plan.steps) + remaining_steps,
            targeted_repair_budget=self._max_repairs,
        )
        plan = outcome.plan
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
            raise PlannerCompilationError(
                "Replan changed or removed an already successful non-report step"
            )
        new_work = [step for step in plan.steps if step.step_id not in protected]
        if len(new_work) > remaining_steps:
            raise PlannerCompilationError("Replan exceeded the remaining step budget")
        return outcome

    def _generate_and_compile(
        self,
        *,
        contract: TaskContract,
        trace_id: str,
        manifest: PlannerCapabilityManifest,
        messages: Sequence[LLMMessage],
        node_name: str,
        prompt_version: str,
        planning_version: int,
        max_steps: int,
        targeted_repair_budget: int,
        initial_repair_type: str | None = None,
        reported_repair_attempts: int = 0,
    ) -> PlanGenerationOutcome:
        """Apply syntax retry, targeted semantic repair, compilation, and validation."""
        current_messages = tuple(messages)
        current_node = node_name
        current_prompt_version = prompt_version
        repair_type = initial_repair_type
        structured_retries = 0
        targeted_repairs = 0
        calls: list[PlannerModelCall] = []
        while True:
            call_number = len(calls) + 1
            prompt_chars = sum(len(message.content) for message in current_messages)
            context = LLMCallContext(
                task_id=contract.task_id,
                trace_id=trace_id,
                node_name=current_node,
                attempt=call_number,
                prompt_version=current_prompt_version,
                schema_version=_PROPOSED_PLAN_SCHEMA_VERSION,
            )
            try:
                result = self._provider.generate_structured(
                    messages=current_messages,
                    output_schema=ProposedPlan,
                    context=context,
                    options=self._options,
                )
                _enforce_token_budget(
                    result.usage.output_tokens,
                    self._options.max_output_tokens,
                    attempts=result.attempts,
                )
            except LLMInvalidResponseError as exc:
                calls.append(
                    _failed_model_call(
                        exc,
                        context=context,
                        prompt_chars=prompt_chars,
                        repair_type=repair_type,
                    )
                )
                if structured_retries < self._max_structured_retries:
                    structured_retries += 1
                    repair_type = "structured_output_retry"
                    self._log_planner_stage(
                        contract,
                        trace_id,
                        repair_type=repair_type,
                        attempt=call_number,
                        error_code=exc.code.value,
                    )
                    continue
                if targeted_repairs:
                    raise PlannerRepairExhaustedError(
                        "Planner repair budget was exhausted after invalid JSON",
                        attempts=call_number,
                    ) from exc
                raise PlannerInvalidJsonError(
                    "Planner did not return complete JSON within the structured-output budget",
                    attempts=call_number,
                ) from exc
            except LLMSchemaValidationError as exc:
                calls.append(
                    _failed_model_call(
                        exc,
                        context=context,
                        prompt_chars=prompt_chars,
                        repair_type=repair_type,
                    )
                )
                if targeted_repairs < targeted_repair_budget:
                    targeted_repairs += 1
                    current_messages = plan_repair_messages(
                        contract=contract,
                        manifest=manifest,
                        invalid_candidate=_invalid_candidate(exc),
                        errors=_llm_validation_error_dicts(exc),
                        max_steps=max_steps,
                    )
                    current_node = "repair_plan"
                    current_prompt_version = PLAN_REPAIR_PROMPT_VERSION
                    repair_type = "targeted_plan_repair"
                    self._log_planner_stage(
                        contract,
                        trace_id,
                        repair_type=repair_type,
                        attempt=call_number,
                        error_code=exc.code.value,
                    )
                    continue
                error_type: type[PlannerError] = (
                    PlannerRepairExhaustedError
                    if targeted_repairs
                    else PlannerSchemaValidationError
                )
                raise error_type(
                    "Planner ProposedPlan failed schema validation",
                    attempts=call_number,
                ) from exc
            except LLMTimeoutError as exc:
                calls.append(
                    _failed_model_call(
                        exc,
                        context=context,
                        prompt_chars=prompt_chars,
                        repair_type=repair_type,
                    )
                )
                raise PlannerTimeoutError(
                    "Planner provider timed out after its bounded retry budget",
                    attempts=exc.attempts,
                ) from exc
            except LLMProviderError as exc:
                calls.append(
                    _failed_model_call(
                        exc,
                        context=context,
                        prompt_chars=prompt_chars,
                        repair_type=repair_type,
                    )
                )
                raise PlannerProviderError(
                    "Planner provider failed before a proposal was available",
                    attempts=exc.attempts,
                ) from exc

            calls.append(
                _successful_model_call(
                    result,
                    context=context,
                    prompt_chars=prompt_chars,
                    repair_type=repair_type,
                )
            )
            proposed = result.parsed_output
            try:
                compilation = self._compiler.compile(
                    proposed,
                    contract,
                    planning_version=planning_version,
                    max_steps=max_steps,
                    created_at=self._clock(),
                )
            except (PlannerCompilationError, PlannerUnsupportedCapabilityError) as exc:
                if targeted_repairs < targeted_repair_budget:
                    targeted_repairs += 1
                    current_messages = plan_repair_messages(
                        contract=contract,
                        manifest=manifest,
                        invalid_candidate=proposed.model_dump(mode="json"),
                        errors=[
                            {
                                "error_type": exc.code.value,
                                "field_path": "steps",
                                "message": str(exc),
                            }
                        ],
                        max_steps=max_steps,
                    )
                    current_node = "repair_plan"
                    current_prompt_version = PLAN_REPAIR_PROMPT_VERSION
                    repair_type = "targeted_plan_repair"
                    self._log_planner_stage(
                        contract,
                        trace_id,
                        repair_type=repair_type,
                        attempt=call_number,
                        error_code=exc.code.value,
                    )
                    continue
                if targeted_repairs:
                    raise PlannerRepairExhaustedError(
                        "Planner repair budget was exhausted during deterministic compilation",
                        attempts=call_number,
                    ) from exc
                raise

            validation = self._validator.evaluate(compilation.plan, contract)
            if not validation.is_valid and targeted_repairs < targeted_repair_budget:
                targeted_repairs += 1
                current_messages = plan_repair_messages(
                    contract=contract,
                    manifest=manifest,
                    invalid_candidate=proposed.model_dump(mode="json"),
                    errors=_plan_validation_error_dicts(validation.errors),
                    max_steps=max_steps,
                )
                current_node = "repair_plan"
                current_prompt_version = PLAN_REPAIR_PROMPT_VERSION
                repair_type = "targeted_plan_repair"
                self._log_planner_stage(
                    contract,
                    trace_id,
                    repair_type=repair_type,
                    attempt=call_number,
                    error_code=validation.errors[0].error_code,
                )
                continue
            if not validation.is_valid and targeted_repairs:
                raise PlannerRepairExhaustedError(
                    "Planner repair budget was exhausted after final plan validation",
                    attempts=call_number,
                )
            self._log_planner_stage(
                contract,
                trace_id,
                repair_type=repair_type,
                attempt=call_number,
                compile_status="passed",
            )
            return PlanGenerationOutcome(
                plan=compilation.plan,
                validation=validation,
                repair_attempts=reported_repair_attempts + targeted_repairs,
                structured_output_retries=structured_retries,
                compilation_diagnostics=compilation.diagnostics,
                model_calls=tuple(calls),
            )

    @staticmethod
    def _log_planner_stage(
        contract: TaskContract,
        trace_id: str,
        *,
        repair_type: str | None,
        attempt: int,
        error_code: str | None = None,
        compile_status: str | None = None,
    ) -> None:
        LOGGER.info(
            "planner_stage",
            extra={
                "planning": {
                    "task_id": contract.task_id,
                    "trace_id": trace_id,
                    "operation": "planning",
                    "attempt": attempt,
                    "repair_type": repair_type,
                    "error_code": error_code,
                    "compile_status": compile_status,
                }
            },
        )


def _successful_model_call(
    result: StructuredLLMResult[ProposedPlan],
    *,
    context: LLMCallContext,
    prompt_chars: int,
    repair_type: str | None,
) -> PlannerModelCall:
    return PlannerModelCall(
        node_name=context.node_name,
        attempt=context.attempt,
        provider_attempts=result.attempts,
        prompt_chars=prompt_chars,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        finish_reason=result.finish_reason,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        total_tokens=result.usage.total_tokens,
        raw_output_chars=result.raw_output_chars,
        raw_output_hash=result.raw_output_hash,
        parse_status="passed",
        schema_status="passed",
        repair_type=repair_type,
    )


def _failed_model_call(
    error: LLMProviderError,
    *,
    context: LLMCallContext,
    prompt_chars: int,
    repair_type: str | None,
) -> PlannerModelCall:
    diagnostics = error.diagnostics
    usage = diagnostics.usage if diagnostics is not None else None
    return PlannerModelCall(
        node_name=context.node_name,
        attempt=context.attempt,
        provider_attempts=error.attempts,
        prompt_chars=prompt_chars,
        provider=diagnostics.provider if diagnostics is not None else None,
        model=diagnostics.model if diagnostics is not None else None,
        latency_ms=diagnostics.latency_ms if diagnostics is not None else None,
        finish_reason=diagnostics.finish_reason if diagnostics is not None else None,
        input_tokens=usage.input_tokens if usage is not None else 0,
        output_tokens=usage.output_tokens if usage is not None else 0,
        total_tokens=usage.total_tokens if usage is not None else 0,
        raw_output_chars=diagnostics.raw_output_chars if diagnostics is not None else 0,
        raw_output_hash=diagnostics.raw_output_hash if diagnostics is not None else None,
        parse_status=diagnostics.parse_status if diagnostics is not None else "not_attempted",
        schema_status=diagnostics.schema_status if diagnostics is not None else "not_attempted",
        error_code=error.code.value,
        repair_type=repair_type,
    )


def _invalid_candidate(error: LLMSchemaValidationError) -> object:
    if error.raw_output is None:
        return {"candidate": "unavailable"}
    try:
        return json.loads(error.raw_output)
    except json.JSONDecodeError:
        return {
            "bounded_raw_candidate": error.raw_output,
            "representation": "untrusted_text",
        }


def _llm_validation_error_dicts(
    error: LLMSchemaValidationError,
) -> list[dict[str, object]]:
    diagnostics = error.diagnostics
    if diagnostics is None or not diagnostics.validation_errors:
        return [{"error_type": error.code.value, "field_path": "root"}]
    return [item.model_dump(mode="json") for item in diagnostics.validation_errors[:8]]


def _plan_validation_error_dicts(
    errors: Sequence[PlanValidationIssue],
) -> list[dict[str, object]]:
    return [
        {
            "error_type": issue.error_code,
            "field_path": issue.field or "root",
            "step_id": issue.step_id,
            "message": issue.message,
            "repair_hint": issue.repair_hint,
        }
        for issue in errors[:8]
    ]


def _task_plan_summary(plan: TaskPlan) -> dict[str, object]:
    return {
        "planning_version": plan.planning_version,
        "steps": [
            {
                "step_id": step.step_id,
                "capability": step.tool_name,
                "depends_on": list(step.dependency),
            }
            for step in plan.steps
        ],
    }


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
