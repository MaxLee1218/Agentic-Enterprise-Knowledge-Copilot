"""Injected deterministic node runtime used by the LangGraph builder."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from copilot.agent.state import AgentGraphState
from copilot.contracts import (
    ClarificationContext,
    ClarificationInputType,
    ClarificationQuestion,
    ClarificationStatus,
    ErrorType,
    EvidenceItem,
    JsonObject,
    StepResult,
    StepResultStatus,
    TaskClarification,
    TaskError,
    TaskResult,
    TaskState,
    TaskStatus,
    TaskStep,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    VerificationStatus,
)
from copilot.policies.approval import PolicyOutcome, SupplierQualityApprovalPolicy
from copilot.policies.permissions import AuthorizationRequest, Permission, PermissionMatrix
from copilot.services.approval_service import ApprovalGateService, ApprovalRepositoryPort
from copilot.services.clarification_service import ClarificationRepositoryPort
from copilot.services.domains import (
    DomainCapabilityManifestRegistry,
    DomainManifestError,
    builtin_domain_manifest_registry,
)
from copilot.services.execution import ExecutionContext
from copilot.services.llm import LLMErrorCode, LLMProviderError
from copilot.services.observability import EventName, NoopObservability, ObservabilityPort
from copilot.services.workflows.deadlines import tool_attempt_deadline
from copilot.services.workflows.dependency import DependencyChecker
from copilot.services.workflows.errors import PlannerError, PlannerErrorCode, StepInputError
from copilot.services.workflows.inputs import StepInputBuilder, summarize_payload
from copilot.services.workflows.models import (
    StepExecutionRecord,
    ToolAttemptSummary,
    WorkflowAuditRecord,
    WorkflowExecutionContext,
)
from copilot.services.workflows.planning import PlanningService
from copilot.services.workflows.ports import (
    ArtifactStore,
    EvidenceReader,
    IdentifierFactory,
    WorkflowAuditSink,
    WorkflowRepository,
    WorkflowVerificationService,
)
from copilot.services.workflows.retry import WorkflowRetryPolicy
from copilot.services.workflows.state_machine import TaskStateMachine
from copilot.services.workflows.validation import PlanValidationIssue, PlanValidator
from copilot.tools.exceptions import ToolRuntimeError, ToolValidationError
from copilot.tools.executor import ToolExecutor
from copilot.tools.registry import ToolRegistry

_REPAIRABLE_VERIFICATION_CODES = {
    "AP_NUMERIC_CLAIM_MISMATCH",
    "ARTIFACT_CHECKSUM_MISMATCH",
    "ARTIFACT_CITATION_COVERAGE_INCOMPLETE",
    "ARTIFACT_REPORT_MODEL_INVALID",
    "CITATION_REQUIRED",
    "CITATION_REFERENCE_INVALID",
    "NUMERIC_CLAIM_MISMATCH",
    "NUMERIC_PRECISION_MISMATCH",
    "NUMERIC_RANKING_MISMATCH",
    "NUMERIC_UNIT_MISMATCH",
    "NUMERIC_VALUE_MISMATCH",
}


class GraphNodeRuntime:
    """Run small graph nodes without owning graph routing or infrastructure construction."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
        registry: ToolRegistry,
        plan_validator: PlanValidator,
        dependency_checker: DependencyChecker,
        input_builder: StepInputBuilder,
        retry_policy: WorkflowRetryPolicy,
        verifier: WorkflowVerificationService,
        evidence_reader: EvidenceReader,
        artifact_store: ArtifactStore,
        repository: WorkflowRepository,
        audit_sink: WorkflowAuditSink,
        state_machine: TaskStateMachine,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        sleeper: Callable[[float], None],
        approval_gate: ApprovalGateService,
        approval_repository: ApprovalRepositoryPort,
        approval_policy: SupplierQualityApprovalPolicy,
        clarification_repository: ClarificationRepositoryPort,
        max_clarification_rounds: int,
        max_task_steps: int,
        max_replan_count: int,
        max_execution_attempts: int | None = None,
        max_plan_repair_attempts: int = 2,
        planning_service: PlanningService | None = None,
        permission_matrix: PermissionMatrix | None = None,
        observability: ObservabilityPort | None = None,
        domain_manifests: DomainCapabilityManifestRegistry | None = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._registry = registry
        self._plan_validator = plan_validator
        self._dependency_checker = dependency_checker
        self._input_builder = input_builder
        self._retry_policy = retry_policy
        self._verifier = verifier
        self._evidence_reader = evidence_reader
        self._artifact_store = artifact_store
        self._repository = repository
        self._audit_sink = audit_sink
        self._state_machine = state_machine
        self._ids = ids
        self._clock = clock
        self._sleeper = sleeper
        self._max_task_steps = max_task_steps
        self._max_execution_attempts = max_execution_attempts or max_task_steps
        self._max_replan_count = max_replan_count
        self._max_plan_repairs = max_plan_repair_attempts
        self._planning_service = planning_service
        self._approval_gate = approval_gate
        self._approval_repository = approval_repository
        self._approval_policy = approval_policy
        self._clarification_repository = clarification_repository
        self._max_clarification_rounds = max_clarification_rounds
        self._permission_matrix = permission_matrix or PermissionMatrix()
        self._observability = observability or NoopObservability()
        self._domain_manifests = domain_manifests or builtin_domain_manifest_registry()

    def validate_request(self, state: AgentGraphState) -> dict[str, object]:
        """Reject inconsistent, terminal, cancelled, expired, or over-budget input."""
        started = self._clock()
        self._emit(state, "workflow_started", status=state["domain_state"].state.value)
        error = self._guard(state, "validate_request")
        if error is None and (
            state["request"].user_id.strip()
            and state["request"].raw_input.strip()
            and state["request"].created_at <= state["deadline_at"]
            and state["intake_context"].max_steps <= self._max_task_steps
        ):
            self._emit(state, "TASK_VALIDATED", status="VALID")
            return self._node_result(state, "validate_request", started, "valid", "Request valid")
        error = error or self._error(
            state,
            "TASK_REQUEST_INVALID",
            ErrorType.VALIDATION,
            "Task request failed deterministic validation",
        )
        return self._node_result(
            state,
            "validate_request",
            started,
            "invalid_request",
            error.message,
            errors=[error],
        )

    def understand_task(self, state: AgentGraphState) -> dict[str, object]:
        """Interpret the request through an optional injected LLM planning boundary."""
        started = self._clock()
        self._emit(state, "TASK_UNDERSTANDING_STARTED", status="STARTED")
        error = self._guard(state, "understand_task")
        if error is not None:
            return self._node_result(
                state,
                "understand_task",
                started,
                "deadline_exceeded",
                error.message,
                errors=[error],
            )
        domain_state = state["domain_state"]
        if domain_state.state is TaskStatus.CREATED:
            domain_state = self._transition(
                state, domain_state, "START_UNDERSTANDING", "Authenticated request accepted"
            )
        contract = state.get("contract")
        completed_clarification_context = state["clarification_context"]
        if self._planning_service is not None:
            try:
                understanding_arguments: dict[str, object] = {
                    "request": state["request"],
                    "trusted_context": state["intake_context"],
                    "trace_id": state["trace_id"],
                    "max_steps": state["intake_context"].max_steps,
                }
                if (
                    state["clarification_context"].values.root
                    or state["clarification_response"] is not None
                ):
                    understanding_arguments.update(
                        clarification_context=state["clarification_context"],
                        clarification_response=state["clarification_response"],
                    )
                outcome = self._planning_service.understand(**understanding_arguments)  # type: ignore[arg-type]
            except DomainManifestError as exc:
                error = self._error(
                    state,
                    exc.code,
                    ErrorType.VALIDATION,
                    str(exc),
                    recoverable=False,
                )
                domain_state = self._fail_clarification_or_understanding(
                    state, domain_state, error.message, resolution_code=exc.code
                )
                self._emit(state, "TASK_UNDERSTANDING_FAILED", status=exc.code)
                return self._node_result(
                    state,
                    "understand_task",
                    started,
                    "domain_denied",
                    error.message,
                    domain_state=domain_state,
                    errors=[error],
                )
            except LLMProviderError as exc:
                error = self._llm_error(state, exc, "understand_task")
                domain_state = self._fail_clarification_or_understanding(
                    state,
                    domain_state,
                    error.message,
                    resolution_code=exc.code.value,
                )
                self._emit(
                    state,
                    "TASK_UNDERSTANDING_FAILED",
                    status=exc.code.value,
                )
                return self._node_result(
                    state,
                    "understand_task",
                    started,
                    "llm_failure",
                    error.message,
                    domain_state=domain_state,
                    errors=[error],
                )
            if outcome.contract is None:
                message = "; ".join(outcome.missing_information)
                if state["clarification_round"] >= self._max_clarification_rounds:
                    error = self._error(
                        state,
                        "CLARIFICATION_LIMIT_EXCEEDED",
                        ErrorType.VALIDATION,
                        "Maximum clarification rounds were reached",
                        recoverable=False,
                    )
                    current, event = self._state_machine.transition(
                        domain_state,
                        "CLARIFICATION_EXHAUSTED",
                        reason=error.message,
                    )
                    submitted = self._submitted_clarification(state)
                    if submitted is not None:
                        rejected = submitted.model_copy(
                            update={
                                "status": ClarificationStatus.REJECTED,
                                "context": outcome.clarification_context,
                                "resolved_at": self._clock(),
                                "resolution_code": error.error_code,
                                "version": submitted.version + 1,
                            }
                        )
                        self._clarification_repository.resolve_submitted_and_transition(
                            submitted,
                            rejected,
                            domain_state,
                            current,
                            event,
                        )
                    else:
                        self._repository.commit_transition(
                            domain_state,
                            current,
                            event,
                            tenant_id=state["intake_context"].tenant_id,
                        )
                    self._observability.increment("clarification_failures_total")
                    self._observability.increment("clarification_exhausted_count")
                    self._emit(
                        state,
                        "TASK_CLARIFICATION_EXHAUSTED",
                        status=current.state.value,
                        error_code=error.error_code,
                        failure_reason=error.message,
                        metadata=JsonObject({"clarification_round": state["clarification_round"]}),
                    )
                    self._observability.emit(
                        EventName.CLARIFICATION_FAILED,
                        fields={
                            "reason_code": error.error_code,
                            "clarification_round": state["clarification_round"],
                        },
                    )
                    return self._node_result(
                        state,
                        "understand_task",
                        started,
                        "clarification_exhausted",
                        error.message,
                        domain_state=current,
                        clarification_context=outcome.clarification_context,
                        errors=[error],
                    )
                return self._node_result(
                    state,
                    "understand_task",
                    started,
                    "missing_information",
                    message or "Required task information is missing",
                    domain_state=domain_state,
                    clarification_questions=list(
                        outcome.questions
                        or (
                            ClarificationQuestion(
                                field="details",
                                reason="Additional information is required to validate the task.",
                                prompt=message or "Please provide the missing task information.",
                                input_type=ClarificationInputType.TEXT,
                            ),
                        )
                    ),
                    clarification_context=outcome.clarification_context,
                )
            contract = outcome.contract
            completed_clarification_context = outcome.clarification_context
        elif contract is None:
            error = self._error(
                state,
                LLMErrorCode.UNAVAILABLE.value,
                ErrorType.TECHNICAL,
                "Natural-language task understanding is unavailable",
            )
            domain_state = self._fail_clarification_or_understanding(
                state,
                domain_state,
                error.message,
                resolution_code=error.error_code,
            )
            self._emit(state, "TASK_UNDERSTANDING_FAILED", status=error.error_code)
            return self._node_result(
                state,
                "understand_task",
                started,
                "llm_failure",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        assert contract is not None
        try:
            manifest = self._domain_manifests.require_execution(contract)
            if (
                state["intake_context"].task_type is not contract.task_type
                or state["intake_context"].purpose != manifest.permission_purpose
            ):
                raise DomainManifestError(
                    "DOMAIN_CONTEXT_MISMATCH",
                    "Trusted task type or purpose does not match the validated contract",
                )
        except DomainManifestError as exc:
            error = self._error(
                state,
                exc.code,
                ErrorType.VALIDATION,
                str(exc),
                recoverable=False,
            )
            domain_state = self._fail_clarification_or_understanding(
                state, domain_state, error.message, resolution_code=exc.code
            )
            return self._node_result(
                state,
                "understand_task",
                started,
                "domain_denied",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        if self._planning_service is not None:
            self._repository.save_contract(
                contract,
                tenant_id=state["intake_context"].tenant_id,
            )
        constraints = contract.constraints
        if not constraints.tenant_id or not constraints.data_scope:
            error = self._error(
                state,
                "TASK_INFORMATION_MISSING",
                ErrorType.VALIDATION,
                "Required authenticated scope information is missing",
                recoverable=True,
            )
            domain_state = self._fail_clarification_or_understanding(
                state,
                domain_state,
                error.message,
                resolution_code=error.error_code,
            )
            return self._node_result(
                state,
                "understand_task",
                started,
                "missing_information",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        if self._planning_service is not None:
            self._resolve_submitted_clarification(
                state,
                completed_clarification_context,
                resolution_code="CONTRACT_COMPLETED",
            )
        self._emit(state, "TASK_UNDERSTANDING_COMPLETED", status="COMPLETED")
        return self._node_result(
            state,
            "understand_task",
            started,
            "understood",
            "Frozen deterministic contract available",
            domain_state=domain_state,
            contract=contract,
        )

    def request_clarification(self, state: AgentGraphState) -> dict[str, object]:
        """Persist one interaction round and enter a durable suspended state."""
        started = self._clock()
        previous = state["domain_state"]
        next_round = state["clarification_round"] + 1
        submitted = self._submitted_clarification(state)
        if next_round > self._max_clarification_rounds:
            error = self._error(
                state,
                "CLARIFICATION_LIMIT_EXCEEDED",
                ErrorType.VALIDATION,
                "Maximum clarification rounds were reached",
                recoverable=False,
            )
            current, event = self._state_machine.transition(
                previous,
                "CLARIFICATION_EXHAUSTED",
                reason=error.message,
            )
            if submitted is not None:
                resolved = submitted.model_copy(
                    update={
                        "status": ClarificationStatus.REJECTED,
                        "context": state["clarification_context"],
                        "resolved_at": self._clock(),
                        "resolution_code": error.error_code,
                        "version": submitted.version + 1,
                    }
                )
                self._clarification_repository.resolve_submitted_and_transition(
                    submitted,
                    resolved,
                    previous,
                    current,
                    event,
                )
            else:
                self._repository.commit_transition(
                    previous,
                    current,
                    event,
                    tenant_id=state["intake_context"].tenant_id,
                )
            self._observability.increment("clarification_failures_total")
            self._observability.increment("clarification_exhausted_count")
            if submitted is not None:
                self._emit(
                    state,
                    "TASK_CLARIFICATION_REJECTED",
                    status=ClarificationStatus.REJECTED.value,
                    error_code=error.error_code,
                    failure_reason=error.message,
                    metadata=JsonObject(
                        {
                            "clarification_id": submitted.clarification_id,
                            "round": submitted.round,
                            "question_fields": [question.field for question in submitted.questions],
                        }
                    ),
                )
            self._emit(
                state,
                "TASK_CLARIFICATION_EXHAUSTED",
                status=current.state.value,
                error_code=error.error_code,
                failure_reason=error.message,
                metadata=JsonObject({"clarification_round": next_round}),
            )
            self._observability.emit(
                EventName.CLARIFICATION_FAILED,
                fields={"reason_code": error.error_code, "clarification_round": next_round},
            )
            return self._node_result(
                state,
                "request_clarification",
                started,
                "clarification_exhausted",
                error.message,
                domain_state=current,
                errors=[error],
            )
        questions = tuple(state["clarification_questions"])
        pending = TaskClarification(
            clarification_id=self._ids.new_id("CLAR"),
            task_id=state["task_id"],
            tenant_id=state["intake_context"].tenant_id,
            round=next_round,
            status=ClarificationStatus.PENDING,
            questions=questions,
            context=state["clarification_context"],
            created_at=self._clock(),
        )
        existing = self._clarification_repository.get_pending_for_task(
            state["task_id"],
            tenant_id=state["intake_context"].tenant_id,
        )
        created = existing is None
        if existing is not None:
            if (
                existing.round != next_round
                or existing.questions != pending.questions
                or existing.context != pending.context
            ):
                raise ValueError("Pending clarification does not match replayed graph state")
            current = self._repository.state_for(
                state["task_id"],
                tenant_id=state["intake_context"].tenant_id,
            )
            if current.state is not TaskStatus.WAITING_CLARIFICATION:
                raise ValueError("Pending clarification Task is not suspended")
            pending = existing
        else:
            current, event = self._state_machine.transition(
                previous,
                "CLARIFICATION_REQUIRED",
                reason=f"Clarification round {next_round} requires human input",
            )
            if submitted is not None:
                resolved = submitted.model_copy(
                    update={
                        "status": ClarificationStatus.RESOLVED,
                        "context": state["clarification_context"],
                        "resolved_at": self._clock(),
                        "resolution_code": "PARTIAL_RESPONSE_ACCEPTED",
                        "version": submitted.version + 1,
                    }
                )
                self._clarification_repository.replace_submitted_with_pending(
                    submitted,
                    resolved,
                    pending,
                    previous,
                    current,
                    event,
                )
                self._observability.increment("clarification_resumes_total")
                self._observability.increment("clarification_resolved_count")
                self._observability.observe("clarification_rounds", float(submitted.round))
                self._observability.emit(
                    EventName.CLARIFICATION_RESUMED,
                    fields={"clarification_round": submitted.round},
                )
                self._emit(
                    state,
                    "TASK_CLARIFICATION_RESOLVED",
                    status=ClarificationStatus.RESOLVED.value,
                    metadata=JsonObject(
                        {
                            "clarification_id": submitted.clarification_id,
                            "round": submitted.round,
                            "question_fields": [question.field for question in submitted.questions],
                            "resolution_code": "PARTIAL_RESPONSE_ACCEPTED",
                        }
                    ),
                )
            else:
                self._clarification_repository.create_pending_and_transition(
                    pending,
                    previous,
                    current,
                    event,
                )
        if created:
            self._emit(
                state,
                "TASK_CLARIFICATION_REQUIRED",
                status=TaskStatus.WAITING_CLARIFICATION.value,
                metadata=JsonObject(
                    {
                        "clarification_id": pending.clarification_id,
                        "round": pending.round,
                        "question_fields": [question.field for question in pending.questions],
                    }
                ),
            )
            self._observability.increment("clarification_requests_total")
            self._observability.increment("clarification_required_count")
            self._observability.gauge_add("waiting_clarification_count", 1)
            self._observability.emit(
                EventName.CLARIFICATION_REQUESTED,
                fields={"clarification_round": pending.round},
            )
        return self._node_result(
            state,
            "request_clarification",
            started,
            "clarification_requested",
            "Task is checkpointed while waiting for clarification",
            domain_state=current,
            clarification_id=pending.clarification_id,
            clarification_round=pending.round,
            clarification_questions=list(pending.questions),
            clarification_response=None,
        )

    def classify_task(self, state: AgentGraphState) -> dict[str, object]:
        """Accept only an enabled manifest matching the trusted task context."""
        started = self._clock()
        guarded = self._guard_node(state, "classify_task", started)
        if guarded is not None:
            return guarded
        try:
            manifest = self._domain_manifests.require_execution(state["contract"])
            if (
                state["intake_context"].task_type is not manifest.task_type
                or state["intake_context"].purpose != manifest.permission_purpose
            ):
                raise DomainManifestError(
                    "DOMAIN_CONTEXT_MISMATCH",
                    "Trusted task type or purpose does not match the selected domain manifest",
                )
        except DomainManifestError as exc:
            error = self._error(
                state,
                exc.code,
                ErrorType.BUSINESS,
                str(exc),
            )
            domain_state = self._transition(
                state, state["domain_state"], "UNDERSTANDING_FAILED", error.message
            )
            return self._node_result(
                state,
                "classify_task",
                started,
                "unsupported",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        if manifest.execution_enabled:
            return self._node_result(
                state,
                "classify_task",
                started,
                "supported",
                "Supported governed task type",
            )
        raise AssertionError("require_execution returned a disabled domain manifest")

    def create_plan(self, state: AgentGraphState) -> dict[str, object]:
        """Generate a candidate plan or expose the injected deterministic fallback."""
        started = self._clock()
        guarded = self._guard_node(state, "create_plan", started)
        if guarded is not None:
            return guarded
        domain_state = self._transition(
            state,
            state["domain_state"],
            "CONTRACT_VALIDATED",
            "Frozen task contract validated",
        )
        plan = state.get("plan")
        repair_count = state.get("plan_repair_count", 0)
        if self._planning_service is not None:
            self._emit(state, "PLAN_GENERATION_STARTED", status="STARTED")
            try:
                outcome = self._planning_service.create_plan(
                    contract=state["contract"],
                    trace_id=state["trace_id"],
                    max_steps=state["intake_context"].max_steps,
                )
            except (LLMProviderError, PlannerError) as exc:
                error = (
                    self._planner_error(state, exc, "create_plan")
                    if isinstance(exc, PlannerError)
                    else self._llm_error(state, exc, "create_plan")
                )
                domain_state = self._transition(state, domain_state, "PLAN_INVALID", error.message)
                self._emit(state, "PLAN_REPAIR_EXHAUSTED", status=error.error_code)
                return self._node_result(
                    state,
                    "create_plan",
                    started,
                    "llm_failure",
                    error.message,
                    domain_state=domain_state,
                    errors=[error],
                )
            plan = outcome.plan
            repair_count = outcome.repair_attempts
            self._repository.save_plan(
                plan,
                tenant_id=state["intake_context"].tenant_id,
            )
            self._emit(state, "PLAN_GENERATED", status="GENERATED")
            if repair_count:
                self._emit(state, "PLAN_REPAIRED", status="REPAIRED")
        elif plan is None:
            error = self._error(
                state,
                "PLANNER_UNAVAILABLE",
                ErrorType.TECHNICAL,
                "Natural-language task planning is unavailable",
            )
            domain_state = self._transition(state, domain_state, "PLAN_INVALID", error.message)
            return self._node_result(
                state,
                "create_plan",
                started,
                "llm_failure",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        assert plan is not None
        return self._node_result(
            state,
            "create_plan",
            started,
            "plan_created",
            "Governed domain candidate plan created",
            domain_state=domain_state,
            plan=plan,
            plan_repair_count=repair_count,
        )

    def validate_plan(self, state: AgentGraphState) -> dict[str, object]:
        """Apply existing deterministic DAG, registry, schema, and budget checks."""
        started = self._clock()
        guarded = self._guard_node(state, "validate_plan", started)
        if guarded is not None:
            return guarded
        validation = self._plan_validator.evaluate(state["plan"], state["contract"])
        if not validation.is_valid:
            first = validation.errors[0]
            if (
                self._planning_service is not None
                and state["domain_state"].state is TaskStatus.PLANNING
                and validation.is_repairable
                and state.get("plan_repair_count", 0) < self._max_plan_repairs
            ):
                self._emit(state, "PLAN_VALIDATION_FAILED", status=first.error_code)
                return self._node_result(
                    state,
                    "validate_plan",
                    started,
                    "repairable_plan",
                    first.message,
                    plan_validation_errors=[
                        JsonObject(
                            {
                                "error_code": issue.error_code,
                                "message": issue.message,
                                "repair_hint": issue.repair_hint,
                                "step_id": issue.step_id,
                                "field": issue.field,
                                "repairable": issue.repairable,
                            }
                        )
                        for issue in validation.errors
                    ],
                )
            error = self._error(
                state,
                "PLAN_INVALID",
                ErrorType.VALIDATION,
                first.message,
                recoverable=False,
            )
            event = (
                "REPLAN_FAILED"
                if state["domain_state"].state is TaskStatus.REPLANNING
                else "PLAN_INVALID"
            )
            domain_state = self._transition(state, state["domain_state"], event, error.message)
            self._emit(
                state,
                "REPLAN_EXHAUSTED" if event == "REPLAN_FAILED" else "PLAN_REPAIR_EXHAUSTED",
                status=first.error_code,
            )
            return self._node_result(
                state,
                "validate_plan",
                started,
                "invalid_plan",
                error.message,
                domain_state=domain_state,
                errors=[error],
                plan_validation_errors=[
                    JsonObject(
                        {
                            "error_code": issue.error_code,
                            "message": issue.message,
                            "repair_hint": issue.repair_hint,
                            "step_id": issue.step_id,
                            "field": issue.field,
                            "repairable": issue.repairable,
                        }
                    )
                    for issue in validation.errors
                ],
            )
        plan_permissions = (
            self._permission_matrix.evaluate(
                AuthorizationRequest(
                    action=Permission.EXECUTE_TOOL,
                    roles=state["intake_context"].roles,
                    resource_type="tool",
                    resource_name=step.tool_name,
                    tool_name=step.tool_name,
                    task_id=state["task_id"],
                    purpose=state["intake_context"].purpose,
                    is_demo_identity=state["intake_context"].is_demo_identity,
                )
            )
            for step in state["plan"].steps
        )
        permission_denial = next(
            (decision for decision in plan_permissions if not decision.allowed), None
        )
        if permission_denial is not None and not permission_denial.allowed:
            error = self._error(
                state,
                permission_denial.reason_code,
                ErrorType.PERMISSION,
                permission_denial.reason,
            )
            event = (
                "REPLAN_FAILED"
                if state["domain_state"].state is TaskStatus.REPLANNING
                else "PLAN_INVALID"
            )
            domain_state = self._transition(state, state["domain_state"], event, error.message)
            self._emit(
                state,
                "permission_denied",
                status=permission_denial.reason_code,
                metadata=JsonObject({"reason_code": permission_denial.reason_code}),
            )
            return self._node_result(
                state,
                "validate_plan",
                started,
                "invalid_plan",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        domain_state = state["domain_state"]
        if domain_state.state is TaskStatus.REPLANNING:
            domain_state = self._transition(
                state,
                domain_state,
                "REVISED_PLAN_VALID",
                "Replanned DAG passed complete deterministic validation",
            )
        return self._node_result(
            state,
            "validate_plan",
            started,
            "plan_valid",
            "Plan validation passed",
            domain_state=domain_state,
            plan_validation_errors=[],
        )

    def repair_plan(self, state: AgentGraphState) -> dict[str, object]:
        """Run one bounded repair attempt and checkpoint before full revalidation."""
        started = self._clock()
        guarded = self._guard_node(state, "repair_plan", started)
        if guarded is not None:
            return guarded
        if self._planning_service is None:
            error = self._error(
                state,
                "PLAN_REPAIR_UNAVAILABLE",
                ErrorType.VALIDATION,
                "No planning service is configured for plan repair",
            )
            domain_state = self._transition(
                state, state["domain_state"], "PLAN_INVALID", error.message
            )
            return self._node_result(
                state,
                "repair_plan",
                started,
                "repair_exhausted",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        attempt = state.get("plan_repair_count", 0) + 1
        if attempt > self._max_plan_repairs:
            error = self._error(
                state,
                "PLAN_REPAIR_ATTEMPTS_EXHAUSTED",
                ErrorType.VALIDATION,
                "Maximum plan repair attempts were exhausted",
            )
            domain_state = self._transition(
                state, state["domain_state"], "PLAN_INVALID", error.message
            )
            self._emit(state, "PLAN_REPAIR_EXHAUSTED", status=error.error_code)
            return self._node_result(
                state,
                "repair_plan",
                started,
                "repair_exhausted",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        issues = tuple(
            PlanValidationIssue(
                error_code=str(item.root["error_code"]),
                message=str(item.root["message"]),
                repair_hint=str(item.root["repair_hint"]),
                step_id=_optional_string(item.root.get("step_id")),
                field=_optional_string(item.root.get("field")),
                repairable=bool(item.root.get("repairable", True)),
            )
            for item in state.get("plan_validation_errors", [])
        )
        self._emit(state, "PLAN_REPAIR_STARTED", status=f"ATTEMPT_{attempt}")
        try:
            outcome = self._planning_service.repair_plan(
                contract=state["contract"],
                invalid_plan=state["plan"],
                errors=issues,
                trace_id=state["trace_id"],
                max_steps=self._max_task_steps,
                attempt=attempt,
            )
        except (LLMProviderError, PlannerError) as exc:
            error = (
                self._planner_error(state, exc, "repair_plan")
                if isinstance(exc, PlannerError)
                else self._llm_error(state, exc, "repair_plan")
            )
            domain_state = self._transition(
                state, state["domain_state"], "PLAN_INVALID", error.message
            )
            self._emit(state, "PLAN_REPAIR_EXHAUSTED", status=error.error_code)
            return self._node_result(
                state,
                "repair_plan",
                started,
                "repair_exhausted",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        self._repository.save_plan(
            outcome.plan,
            tenant_id=state["intake_context"].tenant_id,
        )
        self._emit(state, "PLAN_REPAIRED", status=f"ATTEMPT_{attempt}")
        return self._node_result(
            state,
            "repair_plan",
            started,
            "plan_repaired",
            "Candidate plan repaired and queued for full validation",
            plan=outcome.plan,
            plan_repair_count=attempt,
            plan_validation_errors=[],
        )

    def replan(self, state: AgentGraphState) -> dict[str, object]:
        """Generate one constrained higher-version plan after a frozen eligible event."""
        started = self._clock()
        if state["domain_state"].state is not TaskStatus.REPLANNING:
            error = self._error(
                state,
                "REPLAN_STATE_INVALID",
                ErrorType.VALIDATION,
                "Replan is only allowed in the frozen REPLANNING state",
            )
            return self._node_result(
                state, "replan", started, "replan_failed", error.message, errors=[error]
            )
        if self._planning_service is None or state["replan_count"] >= self._max_replan_count:
            error = self._error(
                state,
                "REPLAN_ATTEMPTS_EXHAUSTED",
                ErrorType.VALIDATION,
                "No LLM replan service or replan budget remains",
            )
            domain_state = self._transition(
                state, state["domain_state"], "REPLAN_FAILED", error.message
            )
            self._emit(state, "REPLAN_EXHAUSTED", status=error.error_code)
            return self._node_result(
                state,
                "replan",
                started,
                "replan_failed",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        remaining_steps = self._execution_step_limit(state) - state["executed_step_count"]
        self._emit(state, "REPLAN_STARTED", status=f"ATTEMPT_{state['replan_count'] + 1}")
        try:
            outcome = self._planning_service.replan(
                contract=state["contract"],
                current_plan=state["plan"],
                step_results=tuple(state["step_results"]),
                evidence_ids=tuple(state["evidence_ids"]),
                reason="REPAIRABLE_VERIFICATION_FAILURE",
                trace_id=state["trace_id"],
                remaining_steps=remaining_steps,
            )
        except (LLMProviderError, PlannerError, ValueError) as exc:
            error = (
                self._planner_error(state, exc, "replan")
                if isinstance(exc, PlannerError)
                else self._llm_error(state, exc, "replan")
                if isinstance(exc, LLMProviderError)
                else self._error(
                    state,
                    "REPLAN_INVALID",
                    ErrorType.VALIDATION,
                    "Replan violated a deterministic invariant",
                )
            )
            domain_state = self._transition(
                state, state["domain_state"], "REPLAN_FAILED", error.message
            )
            self._emit(state, "REPLAN_EXHAUSTED", status=error.error_code)
            return self._node_result(
                state,
                "replan",
                started,
                "replan_failed",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        self._repository.save_plan(
            outcome.plan,
            tenant_id=state["intake_context"].tenant_id,
        )
        count = state["replan_count"] + 1
        self._emit(state, "REPLAN_COMPLETED", status=f"ATTEMPT_{count}")
        return self._node_result(
            state,
            "replan",
            started,
            "replan_created",
            "Replan candidate created and queued for deterministic validation",
            plan=outcome.plan,
            replan_count=count,
            plan_validation_errors=[],
        )

    def policy_check(self, state: AgentGraphState) -> dict[str, object]:
        """Select the next deterministic ready step and enforce the contract approval gate."""
        started = self._clock()
        error = self._guard(state, "policy_check")
        if error is not None:
            domain_state = self._fail_from_execution(state, error.message)
            return self._node_result(
                state,
                "policy_check",
                started,
                "deadline_exceeded",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        step = self._next_step(state)
        if step is None:
            error = self._error(
                state,
                "PLAN_NO_RUNNABLE_STEP",
                ErrorType.VALIDATION,
                "No dependency-satisfied plan step is available",
            )
            domain_state = self._fail_from_execution(state, error.message)
            return self._node_result(
                state,
                "policy_check",
                started,
                "denied",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        if state["domain_state"].state is TaskStatus.PLANNING:
            domain_state = self._transition(
                state,
                state["domain_state"],
                "PLAN_APPROVED_BY_POLICY",
                "Plan is valid; exact actions remain subject to pre-execution policy",
            )
        else:
            domain_state = state["domain_state"]
        dependency = self._dependency_checker.check(
            step, {item.step_id: item for item in state["step_results"]}
        )
        if not dependency.satisfied:
            error = self._error(
                state,
                "STEP_DEPENDENCY_FAILED",
                ErrorType.VALIDATION,
                dependency.reason or "Step dependency failed",
                step_id=step.step_id,
            )
            domain_state = self._fail_from_execution(
                {**state, "domain_state": domain_state}, error.message
            )
            return self._node_result(
                state,
                "policy_check",
                started,
                "dependency_failed",
                error.message,
                domain_state=domain_state,
                current_step_id=step.step_id,
                errors=[error],
            )
        try:
            arguments = self._input_builder.build(
                step,
                state["request"],
                state["contract"],
                {item.step_id: item for item in state["step_results"]},
                self._load_evidence(state),
                state["intake_context"],
            )
        except StepInputError as exc:
            error = self._error(
                state,
                "STEP_INPUT_INVALID",
                ErrorType.VALIDATION,
                str(exc),
                step_id=step.step_id,
            )
            domain_state = self._fail_from_execution(
                {**state, "domain_state": domain_state}, error.message
            )
            return self._node_result(
                state,
                "policy_check",
                started,
                "denied",
                error.message,
                domain_state=domain_state,
                current_step_id=step.step_id,
                errors=[error],
            )
        definition = self._registry.get_profile(
            step.tool_name, step.tool_version, step.contract_profile
        ).definition
        tool_permission = self._permission_matrix.evaluate(
            AuthorizationRequest(
                action=Permission.EXECUTE_TOOL,
                roles=state["intake_context"].roles,
                resource_type="tool",
                resource_name=step.tool_name,
                tool_name=step.tool_name,
                task_id=state["task_id"],
                purpose=state["intake_context"].purpose,
                is_demo_identity=state["intake_context"].is_demo_identity,
            )
        )
        if not tool_permission.allowed:
            error = self._error(
                state,
                tool_permission.reason_code,
                ErrorType.PERMISSION,
                tool_permission.reason,
                step_id=step.step_id,
            )
            domain_state = self._fail_from_execution(
                {**state, "domain_state": domain_state}, error.message
            )
            self._emit(
                state,
                "permission_denied",
                status=tool_permission.reason_code,
                metadata=JsonObject(
                    {"reason_code": tool_permission.reason_code, "tool_name": step.tool_name}
                ),
            )
            return self._node_result(
                state,
                "policy_check",
                started,
                "denied",
                error.message,
                domain_state=domain_state,
                current_step_id=step.step_id,
                last_arguments=arguments,
                errors=[error],
            )
        has_current_plan_approval = any(
            approval.status.value == "APPROVED"
            and approval.planning_version == state["plan"].planning_version
            for approval in self._approval_repository.list_by_task(
                state["task_id"],
                tenant_id=state["contract"].constraints.tenant_id,
            )
        )
        decision = self._approval_policy.evaluate(
            contract=state["contract"],
            step=step,
            definition=definition,
            arguments=arguments,
            has_current_plan_approval=has_current_plan_approval,
        )
        if decision.outcome is PolicyOutcome.DENY:
            error = self._error(
                state,
                "TOOL_POLICY_DENIED",
                ErrorType.PERMISSION,
                decision.reason,
                step_id=step.step_id,
            )
            domain_state = self._fail_from_execution(
                {**state, "domain_state": domain_state}, error.message
            )
            return self._node_result(
                state,
                "policy_check",
                started,
                "denied",
                error.message,
                domain_state=domain_state,
                current_step_id=step.step_id,
                last_arguments=arguments,
                errors=[error],
            )
        if decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            approval = self._approval_gate.require(
                trace_id=state["trace_id"],
                requester=state["request"].user_id,
                contract=state["contract"],
                plan=state["plan"],
                step=step,
                definition=definition,
                arguments=arguments,
                decision=decision,
            )
            event = (
                "APPROVAL_REQUIRED"
                if domain_state.state is TaskStatus.PLANNING
                else "LATE_APPROVAL_REQUIRED"
            )
            domain_state = self._transition(
                state,
                domain_state,
                event,
                "Exact tool action requires bound human approval",
            )
            return self._node_result(
                state,
                "policy_check",
                started,
                "approval_required",
                "Task is waiting for approval before controlled database access",
                domain_state=domain_state,
                current_step_id=step.step_id,
                last_arguments=arguments,
                approval_id=approval.approval_id,
                approval_step_id=step.step_id,
            )
        return self._node_result(
            state,
            "policy_check",
            started,
            "allowed",
            f"Step {step.step_id} is ready",
            domain_state=domain_state,
            current_step_id=step.step_id,
            last_arguments=arguments,
        )

    def execute_tool(self, state: AgentGraphState) -> dict[str, object]:
        """Execute one non-report tool attempt through the governed ToolExecutor."""
        return self._execute_attempt(state, "execute_tool")

    def generate_report(self, state: AgentGraphState) -> dict[str, object]:
        """Execute the frozen report step through the same governed ToolExecutor."""
        return self._execute_attempt(state, "generate_report")

    def aggregate_evidence(self, state: AgentGraphState) -> dict[str, object]:
        """Commit the successful StepResult and attach authoritative Evidence/Artifact metadata."""
        started = self._clock()
        guarded = self._guard_node(state, "aggregate_evidence", started)
        if guarded is not None:
            return guarded
        final = state["last_tool_result"]
        arguments = state["last_arguments"]
        if final is None or arguments is None or final.status is not ToolResultStatus.SUCCESS:
            error = self._error(
                state,
                "EVIDENCE_AGGREGATION_INPUT_INVALID",
                ErrorType.VALIDATION,
                "Successful tool output is required for evidence aggregation",
            )
            domain_state = self._fail_from_execution(state, error.message)
            return self._node_result(
                state,
                "aggregate_evidence",
                started,
                "evidence_failure",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        step = self._current_step(state)
        for evidence_id in final.evidence_ids:
            self._evidence_reader.get(
                evidence_id,
                task_id=state["task_id"],
                tenant_id=state["contract"].constraints.tenant_id,
            )
        result = StepResult(
            step_id=step.step_id,
            status=StepResultStatus.SUCCESS,
            output=final.output,
            evidence=final.evidence_ids,
            error=None,
        )
        attempts = [item for item in state["tool_results"] if item.step_id == step.step_id]
        record = self._execution_record(state, step, arguments, attempts, final)
        self._repository.save_step_result(
            state["task_id"],
            result,
            record,
            tenant_id=state["intake_context"].tenant_id,
        )
        artifacts = []
        if step.tool_name == "report_generator" and final.output is not None:
            artifact_id = final.output.root.get("artifact_id")
            if not isinstance(artifact_id, str):
                error = self._error(
                    state,
                    "REPORT_ARTIFACT_MISSING",
                    ErrorType.VALIDATION,
                    "Report output omitted its Artifact identifier",
                    step_id=step.step_id,
                )
                domain_state = self._fail_from_execution(state, error.message)
                return self._node_result(
                    state,
                    "aggregate_evidence",
                    started,
                    "evidence_failure",
                    error.message,
                    domain_state=domain_state,
                    errors=[error],
                )
            artifacts.append(
                self._artifact_store.get(
                    artifact_id,
                    tenant_id=state["contract"].constraints.tenant_id,
                )
            )
            self._emit(state, "artifact_created", status="CREATED")
        projected_results = [*state["step_results"], result]
        current_step_ids = {item.step_id for item in state["plan"].steps}
        completed_current = {
            item.step_id for item in projected_results if item.step_id in current_step_ids
        }
        if len(completed_current) == len(current_step_ids):
            domain_state = self._transition(
                state,
                state["domain_state"],
                "ALL_REQUIRED_STEPS_FINISHED",
                "All required steps and report Artifact completed",
            )
            route = "all_steps_complete"
        else:
            domain_state = self._transition(
                state,
                state["domain_state"],
                "STEP_SUCCEEDED",
                f"Step {step.step_id} succeeded",
            )
            route = "continue_execution"
        return self._node_result(
            state,
            "aggregate_evidence",
            started,
            route,
            f"Step {step.step_id} committed",
            domain_state=domain_state,
            current_step_id=None,
            last_tool_result=None,
            last_arguments=None,
            step_results=[result],
            step_executions=[record],
            evidence_ids=list(final.evidence_ids),
            artifacts=artifacts,
            active_artifact=artifacts[0] if artifacts else state.get("active_artifact"),
        )

    def verify_result(self, state: AgentGraphState) -> dict[str, object]:
        """Run the existing deterministic verifier after the report Artifact exists."""
        started = self._clock()
        error = self._guard(state, "verify_result")
        if error is not None:
            domain_state = self._transition(
                state,
                state["domain_state"],
                "NON_REPAIRABLE_VERIFICATION_FAILURE",
                error.message,
            )
            return self._node_result(
                state,
                "verify_result",
                started,
                "verification_failed",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        try:
            self._domain_manifests.require_execution(state["contract"])
        except DomainManifestError as exc:
            error = self._error(
                state,
                exc.code,
                ErrorType.VALIDATION,
                str(exc),
                recoverable=False,
            )
            domain_state = self._transition(
                state,
                state["domain_state"],
                "NON_REPAIRABLE_VERIFICATION_FAILURE",
                error.message,
            )
            return self._node_result(
                state,
                "verify_result",
                started,
                "verification_failed",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        context = self._context(state)
        result = self._verifier.verify(context)
        self._repository.save_verification_result(
            result,
            tenant_id=state["intake_context"].tenant_id,
        )
        self._emit(state, "verification_completed", status=result.status.value)
        if result.status is VerificationStatus.FAILED:
            reason = result.issues[0].code if result.issues else "VERIFICATION_FAILED"
            repairable = (
                self._planning_service is not None
                and state["replan_count"] < self._max_replan_count
                and bool(result.issues)
                and all(issue.code in _REPAIRABLE_VERIFICATION_CODES for issue in result.issues)
            )
            event = (
                "REPAIRABLE_VERIFICATION_FAILURE"
                if repairable
                else "NON_REPAIRABLE_VERIFICATION_FAILURE"
            )
            domain_state = self._transition(state, state["domain_state"], event, reason)
            return self._node_result(
                state,
                "verify_result",
                started,
                "verification_replan" if repairable else "verification_failed",
                reason,
                domain_state=domain_state,
                verification_result=result,
            )
        domain_state = self._transition(
            state, state["domain_state"], "VERIFICATION_PASSED", "Evidence and Artifact verified"
        )
        return self._node_result(
            state,
            "verify_result",
            started,
            "verification_passed",
            "Independent verification passed",
            domain_state=domain_state,
            verification_result=result,
        )

    def _llm_error(
        self,
        state: AgentGraphState,
        error: LLMProviderError,
        node_name: str,
    ) -> TaskError:
        error_type = (
            ErrorType.TIMEOUT
            if error.code is LLMErrorCode.TIMEOUT
            else ErrorType.VALIDATION
            if error.code
            in {
                LLMErrorCode.INVALID_RESPONSE,
                LLMErrorCode.SCHEMA_VALIDATION,
                LLMErrorCode.CONTEXT_LIMIT,
                LLMErrorCode.TOKEN_BUDGET,
                LLMErrorCode.CONFIGURATION,
            }
            else ErrorType.PERMISSION
            if error.code is LLMErrorCode.AUTHENTICATION
            else ErrorType.TECHNICAL
        )
        return self._error(
            state,
            error.code.value,
            error_type,
            f"Structured LLM call failed in {node_name}",
            recoverable=False,
        )

    def _planner_error(
        self,
        state: AgentGraphState,
        error: PlannerError,
        node_name: str,
    ) -> TaskError:
        error_type = (
            ErrorType.TIMEOUT
            if error.code is PlannerErrorCode.TIMEOUT
            else ErrorType.TECHNICAL
            if error.code is PlannerErrorCode.PROVIDER
            else ErrorType.VALIDATION
        )
        public_code = {
            PlannerErrorCode.INVALID_JSON: LLMErrorCode.INVALID_RESPONSE.value,
            PlannerErrorCode.SCHEMA_VALIDATION: LLMErrorCode.SCHEMA_VALIDATION.value,
            PlannerErrorCode.TIMEOUT: LLMErrorCode.TIMEOUT.value,
        }.get(error.code, "PLAN_INVALID")
        return self._error(
            state,
            public_code,
            error_type,
            f"Planner failed in {node_name}",
            recoverable=False,
            details=JsonObject(
                {
                    "planner_error_code": error.code.value,
                    "planning_attempts": error.attempts,
                    "node_name": node_name,
                }
            ),
        )

    def persist_result(self, state: AgentGraphState) -> dict[str, object]:
        """Idempotently commit a terminal TaskResult, or preserve an approval interruption."""
        started = self._clock()
        if state["domain_state"].state in {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_CLARIFICATION,
        }:
            waiting_status = state["domain_state"].state
            waiting_reason = (
                "Task is waiting for approval before controlled database access"
                if waiting_status is TaskStatus.WAITING_APPROVAL
                else "Task is checkpointed while waiting for clarification"
            )
            updates = self._node_result(
                state,
                "persist_result",
                started,
                "interrupted",
                waiting_reason,
            )
            self._emit(state, "task_interrupted", status=waiting_status.value)
            return updates
        domain_state = state["domain_state"]
        if domain_state.state not in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            domain_state = self._fail_from_current(state, "Workflow ended without a valid result")
        completed = domain_state.state is TaskStatus.COMPLETED
        cancelled_results: list[StepResult] = []
        cancelled_records: list[StepExecutionRecord] = []
        if not completed:
            existing = {item.step_id for item in state["step_results"]}
            plan = state.get("plan")
            for step in plan.steps if plan is not None else ():
                if step.step_id in existing:
                    continue
                now = self._clock()
                error = self._error(
                    state,
                    "STEP_NOT_EXECUTED_UPSTREAM_FAILURE",
                    ErrorType.CANCELLATION,
                    "Step was not executed because the workflow terminated upstream",
                    step_id=step.step_id,
                )
                step_result = StepResult(
                    step_id=step.step_id,
                    status=StepResultStatus.CANCELLED,
                    output=None,
                    evidence=(),
                    error=error,
                )
                record = StepExecutionRecord(
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    started_at=now,
                    completed_at=now,
                    duration_ms=0,
                    attempt_count=0,
                    executed=False,
                    input_summary=JsonObject({}),
                    output_summary=JsonObject({}),
                    failed_dependencies=step.dependency,
                )
                self._repository.save_step_result(
                    state["task_id"],
                    step_result,
                    record,
                    tenant_id=state["intake_context"].tenant_id,
                )
                cancelled_results.append(step_result)
                cancelled_records.append(record)
        successful = sum(item.status is StepResultStatus.SUCCESS for item in state["step_results"])
        active_artifact = state.get("active_artifact")
        contract = state.get("contract")
        task_type = (
            contract.task_type if contract is not None else state["intake_context"].task_type
        )
        domain_label = (
            "Accounts Payable analysis"
            if task_type.value == "accounts_payable_analysis.v1"
            else "Supplier quality analysis"
        )
        result = TaskResult(
            task_id=state["task_id"],
            final_status=domain_state.state,
            summary=(
                f"{domain_label} completed with verified evidence and report."
                if completed
                else (
                    f"{domain_label} failed after {successful} successful step(s); "
                    "committed evidence is retained."
                )
            ),
            artifacts=(
                (active_artifact.artifact_id,) if completed and active_artifact is not None else ()
            ),
            evidence=tuple(state["evidence_ids"]),
        )
        self._repository.save_task_result(
            result,
            tenant_id=state["intake_context"].tenant_id,
        )
        updates = self._node_result(
            state,
            "persist_result",
            started,
            "completed" if completed else "failed",
            result.summary,
            domain_state=domain_state,
            task_result=result,
            step_results=cancelled_results,
            step_executions=cancelled_records,
        )
        self._emit(
            state,
            "workflow_completed" if completed else "workflow_failed",
            status=result.final_status.value,
        )
        return updates

    def _execute_attempt(self, state: AgentGraphState, node_name: str) -> dict[str, object]:
        started = self._clock()
        error = self._guard(state, node_name)
        if error is not None:
            domain_state = self._fail_from_execution(state, error.message)
            return self._node_result(
                state,
                node_name,
                started,
                "deadline_exceeded",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        domain_state = state["domain_state"]
        if domain_state.state is TaskStatus.RETRYING:
            domain_state = self._transition(
                state, domain_state, "RETRY_READY", "Retry remains within task budget"
            )
        step = self._current_step(state)
        if state["executed_step_count"] >= self._execution_step_limit(state):
            error = self._error(
                state,
                "MAX_TASK_STEPS_EXCEEDED",
                ErrorType.VALIDATION,
                "Maximum task step execution count was reached",
                step_id=step.step_id,
            )
            domain_state = self._fail_from_execution(
                {**state, "domain_state": domain_state}, error.message
            )
            return self._node_result(
                state,
                node_name,
                started,
                "tool_failure",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        arguments = state["last_arguments"]
        if arguments is None:
            error = self._error(
                state,
                "POLICY_ARGUMENT_BINDING_MISSING",
                ErrorType.VALIDATION,
                "Tool input was not bound by the policy node",
                step_id=step.step_id,
            )
            domain_state = self._fail_from_execution(
                {**state, "domain_state": domain_state}, error.message
            )
            return self._node_result(
                state,
                node_name,
                started,
                "tool_failure",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        definition = self._registry.get_profile(
            step.tool_name, step.tool_version, step.contract_profile
        ).definition
        attempt = state["retry_counts"].get(step.step_id, 0) + 1
        prior_results = tuple(
            result for result in state["tool_results"] if result.step_id == step.step_id
        )
        call = ToolCall(
            tool_call_id=self._ids.new_id("TC"),
            task_id=state["task_id"],
            step_id=step.step_id,
            tool_name=definition.tool_name,
            tool_version=definition.tool_version,
            input=arguments,
            idempotency_key=_idempotency_key(
                state["task_id"], step, definition.tool_version, arguments
            ),
            approval_id=(
                state["approval_id"] if state["approval_step_id"] == step.step_id else None
            ),
            deadline_at=tool_attempt_deadline(
                task_deadline=state["deadline_at"],
                attempt_started_at=started,
                overall_seconds=definition.timeout.overall_seconds,
                prior_results=prior_results,
            ),
            tenant_id=state["contract"].constraints.tenant_id,
            user_id=state["request"].user_id,
        )
        try:
            result = self._tool_executor.execute(
                call,
                ExecutionContext.from_task_context(
                    state["intake_context"],
                    call,
                    approval_required=(state["approval_step_id"] == step.step_id),
                ),
                attempt=attempt,
            )
        except (ToolValidationError, ToolRuntimeError) as exc:
            result = self._exception_result(call, attempt, exc)
        self._repository.save_tool_result(
            result,
            tenant_id=state["intake_context"].tenant_id,
        )
        updates: dict[str, object] = {
            "domain_state": domain_state,
            "last_tool_result": result,
            "last_arguments": arguments,
            "tool_calls": [call],
            "tool_results": [result],
            "executed_step_count": state["executed_step_count"] + (1 if attempt == 1 else 0),
        }
        if result.status is ToolResultStatus.SUCCESS:
            return self._node_result(
                state,
                node_name,
                started,
                "tool_success",
                f"Tool attempt {attempt} succeeded",
                **updates,
            )
        self._emit(
            state,
            "tool_attempt_failed",
            status=result.status.value,
            error_type=(result.error.error_type.value if result.error is not None else None),
            error_code=result.error.error_code if result.error is not None else None,
            failure_reason=result.error.message if result.error is not None else None,
            metadata=JsonObject(
                {
                    "tool_name": result.tool_name,
                    "attempt": attempt,
                }
            ),
        )
        if self._retry_policy.should_retry(step, definition, result, attempt):
            domain_state = self._transition(
                state,
                domain_state,
                "TRANSIENT_FAILURE",
                f"Retrying {step.step_id} after attempt {attempt}",
            )
            delay = self._retry_policy.delay_for(step, attempt)
            self._emit(
                state,
                "tool_retry_scheduled",
                status=TaskStatus.RETRYING.value,
                metadata=JsonObject(
                    {
                        "step_id": step.step_id,
                        "attempt": attempt + 1,
                        "delay_seconds": delay,
                    }
                ),
            )
            if delay:
                self._sleeper(delay)
            return self._node_result(
                state,
                node_name,
                started,
                "tool_retry",
                f"Transient failure; retry {attempt + 1} is eligible",
                domain_state=domain_state,
                retry_counts={step.step_id: attempt},
                tool_retry_count=state["tool_retry_count"] + 1,
                **{key: value for key, value in updates.items() if key != "domain_state"},
            )
        step_result = StepResult(
            step_id=step.step_id,
            status=StepResultStatus(result.status.value),
            output=result.output,
            evidence=result.evidence_ids,
            error=result.error,
        )
        attempts = [
            *[item for item in state["tool_results"] if item.step_id == step.step_id],
            result,
        ]
        record = self._execution_record(state, step, arguments, attempts, result)
        self._repository.save_step_result(
            state["task_id"],
            step_result,
            record,
            tenant_id=state["intake_context"].tenant_id,
        )
        reason = result.error.message if result.error is not None else "Tool execution failed"
        error_code = (
            result.error.error_code if result.error is not None else "TOOL_EXECUTION_FAILED"
        )
        if result.status is ToolResultStatus.PERMISSION_DENIED:
            self._emit(
                state,
                "permission_denied",
                status=error_code,
                metadata=JsonObject({"reason_code": error_code, "tool_name": result.tool_name}),
            )
        if error_code in {"SENSITIVE_OUTPUT_BLOCKED", "SECRET_DETECTED", "UNSAFE_TOOL_OUTPUT"}:
            self._emit(
                state,
                "artifact_creation_blocked"
                if step.tool_name == "report_generator"
                else "output_blocked",
                status=error_code,
                metadata=JsonObject({"reason_code": error_code}),
            )
        domain_state = self._fail_from_execution({**state, "domain_state": domain_state}, reason)
        return self._node_result(
            state,
            node_name,
            started,
            "tool_failure",
            reason,
            domain_state=domain_state,
            step_results=[step_result],
            step_executions=[record],
            errors=[result.error] if result.error is not None else [],
            **{key: value for key, value in updates.items() if key != "domain_state"},
        )

    def _next_step(self, state: AgentGraphState) -> TaskStep | None:
        completed = {item.step_id: item for item in state["step_results"]}
        for step in state["plan"].steps:
            if step.step_id in completed:
                continue
            if self._dependency_checker.check(step, completed).satisfied:
                return step
        return None

    @staticmethod
    def _current_step(state: AgentGraphState) -> TaskStep:
        step_id = state["current_step_id"]
        return next(item for item in state["plan"].steps if item.step_id == step_id)

    def _context(self, state: AgentGraphState) -> WorkflowExecutionContext:
        current_step_ids = {item.step_id for item in state["plan"].steps}
        active_artifact = state.get("active_artifact")
        context = WorkflowExecutionContext(
            task_id=state["task_id"],
            request=state["request"],
            contract=state["contract"],
            plan=state["plan"],
            task_state=state["domain_state"],
            started_at=state["started_at"],
            current_step_id=state["current_step_id"],
            step_results={
                item.step_id: item
                for item in state["step_results"]
                if item.step_id in current_step_ids
            },
            step_executions={
                item.step_id: item
                for item in state["step_executions"]
                if item.step_id in current_step_ids
            },
            tool_results={},
            tool_calls=list(state["tool_calls"]),
            evidence=self._load_evidence(state),
            artifacts=[active_artifact] if active_artifact is not None else [],
            retry_counts=dict(state["retry_counts"]),
            metadata={
                "registered_tools": tuple(
                    self._registry.get_profile(
                        step.tool_name,
                        step.tool_version,
                        step.contract_profile,
                    ).definition
                    for step in state["plan"].steps
                )
            },
            verification_result=state["verification_result"],
            approvals=self._approval_repository.list_by_task(
                state["task_id"],
                tenant_id=state["contract"].constraints.tenant_id,
            ),
        )
        for result in state["tool_results"]:
            context.tool_results.setdefault(result.step_id, []).append(result)
        return context

    def _load_evidence(self, state: AgentGraphState) -> dict[str, EvidenceItem]:
        return {
            evidence_id: self._evidence_reader.get(
                evidence_id,
                task_id=state["task_id"],
                tenant_id=state["contract"].constraints.tenant_id,
            )
            for evidence_id in state["evidence_ids"]
        }

    def _execution_record(
        self,
        state: AgentGraphState,
        step: TaskStep,
        arguments: JsonObject,
        attempts: list[ToolResult],
        final: ToolResult,
    ) -> StepExecutionRecord:
        started = attempts[0].started_at if attempts else self._clock()
        completed = final.completed_at
        return StepExecutionRecord(
            step_id=step.step_id,
            tool_name=step.tool_name,
            started_at=started,
            completed_at=completed,
            duration_ms=_duration_ms(started, completed),
            attempt_count=len(attempts),
            executed=True,
            input_summary=summarize_payload(arguments),
            output_summary=summarize_payload(final.output),
            attempts=tuple(
                ToolAttemptSummary(
                    attempt=item.attempt,
                    tool_call_id=item.tool_call_id,
                    status=item.status.value,
                    duration_ms=item.latency_ms or 0,
                    error_code=item.error.error_code if item.error is not None else None,
                )
                for item in attempts
            ),
        )

    def _guard(self, state: AgentGraphState, node_name: str) -> TaskError | None:
        if state["domain_state"].state in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            return self._error(
                state,
                "TASK_ALREADY_TERMINAL",
                ErrorType.VALIDATION,
                f"Terminal task cannot enter {node_name}",
            )
        if self._clock() >= state["deadline_at"]:
            return self._error(
                state,
                "TASK_DEADLINE_EXCEEDED",
                ErrorType.TIMEOUT,
                "Task execution deadline was exceeded",
            )
        if state["replan_count"] > self._max_replan_count:
            return self._error(
                state,
                "MAX_REPLAN_COUNT_EXCEEDED",
                ErrorType.VALIDATION,
                "Maximum replan count was exceeded",
            )
        return None

    def _execution_step_limit(self, state: AgentGraphState) -> int:
        """Keep the Supplier limit exact while reserving bounded AP report-replan capacity."""
        if state["contract"].task_type.value == "accounts_payable_analysis.v1":
            return self._max_execution_attempts
        return self._max_task_steps

    def _guard_node(
        self,
        state: AgentGraphState,
        node_name: str,
        started_at: datetime,
    ) -> dict[str, object] | None:
        error = self._guard(state, node_name)
        if error is None:
            return None
        domain_state = self._fail_from_current(state, error.message)
        return self._node_result(
            state,
            node_name,
            started_at,
            "deadline_exceeded",
            error.message,
            domain_state=domain_state,
            errors=[error],
        )

    def _fail_from_execution(self, state: AgentGraphState, reason: str) -> TaskState:
        domain_state = state["domain_state"]
        if domain_state.state is TaskStatus.RETRYING:
            return self._transition(state, domain_state, "RETRY_BUDGET_EXHAUSTED", reason)
        if domain_state.state is TaskStatus.EXECUTING:
            return self._transition(state, domain_state, "NON_RECOVERABLE_FAILURE", reason)
        return domain_state

    def _fail_from_current(self, state: AgentGraphState, reason: str) -> TaskState:
        domain_state = state["domain_state"]
        event = {
            TaskStatus.UNDERSTANDING: "UNDERSTANDING_FAILED",
            TaskStatus.PLANNING: "PLAN_INVALID",
            TaskStatus.EXECUTING: "NON_RECOVERABLE_FAILURE",
            TaskStatus.RETRYING: "RETRY_BUDGET_EXHAUSTED",
            TaskStatus.VERIFYING: "NON_REPAIRABLE_VERIFICATION_FAILURE",
        }.get(domain_state.state)
        return self._transition(state, domain_state, event, reason) if event else domain_state

    def _submitted_clarification(
        self,
        state: AgentGraphState,
    ) -> TaskClarification | None:
        clarification_id = state["clarification_id"]
        if clarification_id is None:
            return None
        clarification = self._clarification_repository.get(
            clarification_id,
            tenant_id=state["intake_context"].tenant_id,
        )
        return clarification if clarification.status is ClarificationStatus.SUBMITTED else None

    def _resolve_submitted_clarification(
        self,
        state: AgentGraphState,
        context: ClarificationContext,
        *,
        resolution_code: str,
    ) -> None:
        submitted = self._submitted_clarification(state)
        if submitted is None:
            return
        resolved = submitted.model_copy(
            update={
                "status": ClarificationStatus.RESOLVED,
                "context": context,
                "resolved_at": self._clock(),
                "resolution_code": resolution_code,
                "version": submitted.version + 1,
            }
        )
        self._clarification_repository.resolve_submitted(submitted, resolved)
        self._observability.increment("clarification_resumes_total")
        self._observability.increment("clarification_resolved_count")
        self._observability.observe("clarification_rounds", float(submitted.round))
        self._observability.emit(
            EventName.CLARIFICATION_RESUMED,
            fields={"clarification_round": submitted.round},
        )
        self._emit(
            state,
            "TASK_CLARIFICATION_RESOLVED",
            status=ClarificationStatus.RESOLVED.value,
            metadata=JsonObject(
                {
                    "clarification_id": submitted.clarification_id,
                    "round": submitted.round,
                    "question_fields": [question.field for question in submitted.questions],
                }
            ),
        )

    def _fail_clarification_or_understanding(
        self,
        state: AgentGraphState,
        previous: TaskState,
        reason: str,
        *,
        resolution_code: str,
    ) -> TaskState:
        current, event = self._state_machine.transition(
            previous,
            "UNDERSTANDING_FAILED",
            reason=reason,
        )
        submitted = self._submitted_clarification(state)
        if submitted is not None:
            rejected = submitted.model_copy(
                update={
                    "status": ClarificationStatus.REJECTED,
                    "context": state["clarification_context"],
                    "resolved_at": self._clock(),
                    "resolution_code": resolution_code,
                    "version": submitted.version + 1,
                }
            )
            self._clarification_repository.resolve_submitted_and_transition(
                submitted,
                rejected,
                previous,
                current,
                event,
            )
            self._observability.increment("clarification_failures_total")
            self._observability.emit(
                EventName.CLARIFICATION_FAILED,
                fields={
                    "reason_code": resolution_code,
                    "clarification_round": submitted.round,
                },
            )
            self._emit(
                state,
                "TASK_CLARIFICATION_REJECTED",
                status=ClarificationStatus.REJECTED.value,
                error_code=resolution_code,
                failure_reason=reason,
                metadata=JsonObject(
                    {
                        "clarification_id": submitted.clarification_id,
                        "round": submitted.round,
                        "question_fields": [question.field for question in submitted.questions],
                    }
                ),
            )
        else:
            self._repository.commit_transition(
                previous,
                current,
                event,
                tenant_id=state["intake_context"].tenant_id,
            )
        self._emit(state, "task_status_changed", status=current.state.value)
        return current

    def _transition(
        self,
        state: AgentGraphState,
        previous: TaskState,
        event: str,
        reason: str,
    ) -> TaskState:
        current, record = self._state_machine.transition(previous, event, reason=reason)
        self._repository.commit_transition(
            previous,
            current,
            record,
            tenant_id=state["intake_context"].tenant_id,
        )
        self._emit(state, "task_status_changed", status=current.state.value)
        return current

    def _node_result(
        self,
        state: AgentGraphState,
        node_name: str,
        started_at: datetime,
        route: str,
        reason: str,
        **updates: object,
    ) -> dict[str, object]:
        completed_at = self._clock()
        node_errors = updates.get("errors")
        error = (
            node_errors[0]
            if isinstance(node_errors, (list, tuple))
            and node_errors
            and isinstance(node_errors[0], TaskError)
            else None
        )
        self._emit(
            state,
            "node_completed",
            status=route,
            duration_ms=_duration_ms(started_at, completed_at),
            error_type=error.error_type.value if error is not None else None,
            error_code=error.error_code if error is not None else None,
            failure_reason=error.message if error is not None else None,
            metadata=JsonObject({"node_name": node_name, "route": route}),
        )
        return {"route": route, "route_reason": reason, **updates}

    def _emit(
        self,
        state: AgentGraphState,
        event: str,
        *,
        status: str | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        failure_reason: str | None = None,
        metadata: JsonObject | None = None,
    ) -> None:
        safe_metadata = metadata or JsonObject({})
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=state["task_id"],
                plan_id=self._domain_manifests.resolve(
                    state["intake_context"].task_type
                ).plan_profile,
                plan_version=(
                    state["plan"].planning_version if state.get("plan") is not None else 0
                ),
                timestamp=self._clock(),
                tenant_id=state["intake_context"].tenant_id,
                trace_id=state["trace_id"],
                actor_id=state["intake_context"].user_id,
                scopes=state["intake_context"].scopes,
                step_id=state["current_step_id"],
                status=status,
                duration_ms=duration_ms,
                error_type=error_type,
                error_code=error_code,
                failure_reason=failure_reason,
                metadata=safe_metadata,
            )
        )
        self._observability.record_workflow_event(
            event,
            status=status,
            fields=safe_metadata.root,
        )

    def record_submission(self, state: AgentGraphState) -> None:
        """Write a minimized audit event after persistence and before graph execution."""
        context = state["intake_context"]
        intake = state["request"].metadata.root.get("intake")
        resolution = intake.get("domain_resolution") if isinstance(intake, dict) else None
        self._emit(
            state,
            "TASK_SUBMITTED",
            status=TaskStatus.CREATED.value,
            metadata=JsonObject(
                {
                    "trace_id": context.trace_id,
                    "session_id": context.session_id,
                    "request_source": context.request_source.value,
                    "task_text_hash": context.task_text_hash,
                    "task_text_length": context.task_text_length,
                    "effective_max_steps": context.max_steps,
                    "effective_read_only": context.read_only,
                    "effective_require_approval": context.require_approval,
                    "output_format": (
                        context.output_format.value if context.output_format is not None else None
                    ),
                    "output_format_source": (
                        intake.get("output_format_source") if isinstance(intake, dict) else None
                    ),
                    "task_type": context.task_type.value,
                    "domain_resolution_reason": (
                        resolution.get("reason_code") if isinstance(resolution, dict) else "UNKNOWN"
                    ),
                }
            ),
        )

    def _error(
        self,
        state: AgentGraphState,
        code: str,
        error_type: ErrorType,
        message: str,
        *,
        recoverable: bool = False,
        step_id: str | None = None,
        details: JsonObject | None = None,
    ) -> TaskError:
        return TaskError(
            error_code=code,
            error_type=error_type,
            message=message,
            recoverable=recoverable,
            timestamp=self._clock(),
            task_id=state["task_id"],
            step_id=step_id,
            details=details or JsonObject({}),
        )

    def _exception_result(self, call: ToolCall, attempt: int, exc: ToolRuntimeError) -> ToolResult:
        now = self._clock()
        error = exc.error.model_copy(
            update={
                "task_id": call.task_id,
                "step_id": call.step_id,
                "tool_call_id": call.tool_call_id,
            }
        )
        return ToolResult(
            tool_call_id=call.tool_call_id,
            task_id=call.task_id,
            step_id=call.step_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            status=ToolResultStatus.BUSINESS_FAILURE,
            output=None,
            error=error,
            started_at=now,
            completed_at=now,
            attempt=attempt,
        )


def _idempotency_key(
    task_id: str,
    step: TaskStep,
    tool_version: str,
    arguments: JsonObject,
) -> str:
    normalized = json.dumps(arguments.root, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{task_id}:{step.step_id}:{tool_version}:{digest}"


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    return max(0, round((completed_at - started_at).total_seconds() * 1000))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


__all__ = ["GraphNodeRuntime"]
