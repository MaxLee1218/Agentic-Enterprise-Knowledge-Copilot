"""Injected deterministic node runtime used by the LangGraph builder."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from copilot.agent.state import AgentGraphState
from copilot.contracts import (
    ErrorType,
    EvidenceItem,
    JsonObject,
    StepResult,
    StepResultStatus,
    TaskError,
    TaskResult,
    TaskState,
    TaskStatus,
    TaskStep,
    TaskType,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    VerificationStatus,
)
from copilot.services.workflows.dependency import DependencyChecker
from copilot.services.workflows.errors import PlanValidationError, StepInputError
from copilot.services.workflows.fixed_plan import SUPPLIER_QUALITY_PLAN_ID
from copilot.services.workflows.inputs import StepInputBuilder, summarize_payload
from copilot.services.workflows.models import (
    StepExecutionRecord,
    ToolAttemptSummary,
    WorkflowAuditRecord,
    WorkflowExecutionContext,
)
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
from copilot.services.workflows.validation import PlanValidator
from copilot.tools.exceptions import ToolRuntimeError, ToolValidationError
from copilot.tools.executor import ToolExecutor
from copilot.tools.registry import ToolRegistry


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
        max_task_steps: int,
        max_replan_count: int,
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
        self._max_replan_count = max_replan_count

    def validate_request(self, state: AgentGraphState) -> dict[str, object]:
        """Reject inconsistent, terminal, cancelled, expired, or over-budget input."""
        started = self._clock()
        self._emit(state, "workflow_started", status=state["domain_state"].state.value)
        error = self._guard(state, "validate_request")
        if error is None and (
            state["request"].user_id.strip()
            and state["request"].raw_input.strip()
            and state["request"].created_at <= state["deadline_at"]
            and len(state["plan"].steps) <= self._max_task_steps
        ):
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
        """Use the existing deterministic TaskContract and enter UNDERSTANDING."""
        started = self._clock()
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
        constraints = state["contract"].constraints
        if not constraints.tenant_id or not constraints.data_scope:
            error = self._error(
                state,
                "TASK_INFORMATION_MISSING",
                ErrorType.VALIDATION,
                "Required authenticated scope information is missing",
                recoverable=True,
            )
            domain_state = self._transition(
                state, domain_state, "UNDERSTANDING_FAILED", error.message
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
        return self._node_result(
            state,
            "understand_task",
            started,
            "understood",
            "Frozen deterministic contract available",
            domain_state=domain_state,
        )

    def classify_task(self, state: AgentGraphState) -> dict[str, object]:
        """Accept only the frozen Supplier Quality Analysis task type."""
        started = self._clock()
        guarded = self._guard_node(state, "classify_task", started)
        if guarded is not None:
            return guarded
        if state["contract"].task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1:
            return self._node_result(
                state, "classify_task", started, "supported", "Supported frozen task type"
            )
        error = self._error(
            state,
            "UNSUPPORTED_TASK_TYPE",
            ErrorType.BUSINESS,
            "The requested task type is not supported",
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

    def create_plan(self, state: AgentGraphState) -> dict[str, object]:
        """Expose the already-created fixed plan as an explicit orchestration step."""
        started = self._clock()
        guarded = self._guard_node(state, "create_plan", started)
        if guarded is not None:
            return guarded
        domain_state = self._transition(
            state,
            state["domain_state"],
            "CONTRACT_VALIDATED",
            "Frozen task contract and deterministic plan supplied",
        )
        return self._node_result(
            state,
            "create_plan",
            started,
            "plan_created",
            "Fixed Supplier Quality plan created",
            domain_state=domain_state,
        )

    def validate_plan(self, state: AgentGraphState) -> dict[str, object]:
        """Apply existing deterministic DAG, registry, schema, and budget checks."""
        started = self._clock()
        guarded = self._guard_node(state, "validate_plan", started)
        if guarded is not None:
            return guarded
        try:
            self._plan_validator.validate(state["plan"], state["contract"])
        except PlanValidationError as exc:
            error = self._error(
                state, "PLAN_INVALID", ErrorType.VALIDATION, str(exc), recoverable=False
            )
            domain_state = self._transition(
                state, state["domain_state"], "PLAN_INVALID", error.message
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
        return self._node_result(
            state, "validate_plan", started, "plan_valid", "Plan validation passed"
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
            if state["contract"].approval_requirement.required:
                domain_state = self._transition(
                    state,
                    state["domain_state"],
                    "APPROVAL_REQUIRED",
                    "Contract requires a bound human approval",
                )
                return self._node_result(
                    state,
                    "policy_check",
                    started,
                    "approval_required",
                    "Human approval is required before execution",
                    domain_state=domain_state,
                    current_step_id=step.step_id,
                )
            domain_state = self._transition(
                state,
                state["domain_state"],
                "PLAN_APPROVED_BY_POLICY",
                "Offline v1 scope is pre-authorized",
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
        return self._node_result(
            state,
            "policy_check",
            started,
            "allowed",
            f"Step {step.step_id} is ready",
            domain_state=domain_state,
            current_step_id=step.step_id,
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
            self._evidence_reader.get(evidence_id)
        result = StepResult(
            step_id=step.step_id,
            status=StepResultStatus.SUCCESS,
            output=final.output,
            evidence=final.evidence_ids,
            error=None,
        )
        attempts = [item for item in state["tool_results"] if item.step_id == step.step_id]
        record = self._execution_record(state, step, arguments, attempts, final)
        self._repository.save_step_result(result, record)
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
            artifacts.append(self._artifact_store.get(artifact_id))
            self._emit(state, "artifact_created", status="CREATED")
        projected_results = [*state["step_results"], result]
        if len(projected_results) == len(state["plan"].steps):
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
        context = self._context(state)
        result = self._verifier.verify(context)
        self._repository.save_verification_result(result)
        self._emit(state, "verification_completed", status=result.status.value)
        if result.status is VerificationStatus.FAILED:
            reason = result.issues[0].code if result.issues else "VERIFICATION_FAILED"
            domain_state = self._transition(
                state,
                state["domain_state"],
                "NON_REPAIRABLE_VERIFICATION_FAILURE",
                reason,
            )
            return self._node_result(
                state,
                "verify_result",
                started,
                "verification_failed",
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

    def persist_result(self, state: AgentGraphState) -> dict[str, object]:
        """Idempotently commit a terminal TaskResult, or preserve an approval interruption."""
        started = self._clock()
        if state["domain_state"].state is TaskStatus.WAITING_APPROVAL:
            updates = self._node_result(
                state,
                "persist_result",
                started,
                "interrupted",
                "Task is checkpointed while waiting for approval",
            )
            self._emit(state, "task_interrupted", status=TaskStatus.WAITING_APPROVAL.value)
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
            for step in state["plan"].steps:
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
                self._repository.save_step_result(step_result, record)
                cancelled_results.append(step_result)
                cancelled_records.append(record)
        successful = sum(item.status is StepResultStatus.SUCCESS for item in state["step_results"])
        result = TaskResult(
            task_id=state["task_id"],
            final_status=domain_state.state,
            summary=(
                "Supplier quality analysis completed with verified evidence and report."
                if completed
                else (
                    f"Supplier quality analysis failed after {successful} successful step(s); "
                    "committed evidence is retained."
                )
            ),
            artifacts=tuple(item.artifact_id for item in state["artifacts"]) if completed else (),
            evidence=tuple(state["evidence_ids"]),
        )
        self._repository.save_task_result(result)
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
        if state["executed_step_count"] >= self._max_task_steps:
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
        prior = {item.step_id: item for item in state["step_results"]}
        evidence = self._load_evidence(state)
        try:
            arguments = self._input_builder.build(
                step, state["request"], state["contract"], prior, evidence
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
                node_name,
                started,
                "tool_failure",
                error.message,
                domain_state=domain_state,
                errors=[error],
            )
        definition = self._registry.get(step.tool_name).definition
        attempt = state["retry_counts"].get(step.step_id, 0) + 1
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
            approval_id=None,
            deadline_at=state["deadline_at"],
            tenant_id=state["contract"].constraints.tenant_id,
            user_id=state["request"].user_id,
        )
        try:
            result = self._tool_executor.execute(call, attempt=attempt)
        except (ToolValidationError, ToolRuntimeError) as exc:
            result = self._exception_result(call, attempt, exc)
        self._repository.save_tool_result(result)
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
        self._repository.save_step_result(step_result, record)
        reason = result.error.message if result.error is not None else "Tool execution failed"
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
        context = WorkflowExecutionContext(
            task_id=state["task_id"],
            request=state["request"],
            contract=state["contract"],
            plan=state["plan"],
            task_state=state["domain_state"],
            started_at=state["started_at"],
            current_step_id=state["current_step_id"],
            step_results={item.step_id: item for item in state["step_results"]},
            step_executions={item.step_id: item for item in state["step_executions"]},
            tool_results={},
            tool_calls=list(state["tool_calls"]),
            evidence=self._load_evidence(state),
            artifacts=list(state["artifacts"]),
            retry_counts=dict(state["retry_counts"]),
            metadata={"registered_tools": tuple(self._registry.list())},
            verification_result=state["verification_result"],
        )
        for result in state["tool_results"]:
            context.tool_results.setdefault(result.step_id, []).append(result)
        return context

    def _load_evidence(self, state: AgentGraphState) -> dict[str, EvidenceItem]:
        return {
            evidence_id: self._evidence_reader.get(evidence_id)
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

    def _transition(
        self,
        state: AgentGraphState,
        previous: TaskState,
        event: str,
        reason: str,
    ) -> TaskState:
        current, record = self._state_machine.transition(previous, event, reason=reason)
        self._repository.commit_transition(previous, current, record)
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
        self._emit(
            state,
            "node_completed",
            status=route,
            duration_ms=_duration_ms(started_at, completed_at),
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
        metadata: JsonObject | None = None,
    ) -> None:
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=state["task_id"],
                plan_id=SUPPLIER_QUALITY_PLAN_ID,
                plan_version=state["plan"].planning_version,
                timestamp=self._clock(),
                step_id=state["current_step_id"],
                status=status,
                duration_ms=duration_ms,
                metadata=metadata or JsonObject({}),
            )
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
    ) -> TaskError:
        return TaskError(
            error_code=code,
            error_type=error_type,
            message=message,
            recoverable=recoverable,
            timestamp=self._clock(),
            task_id=state["task_id"],
            step_id=step_id,
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


__all__ = ["GraphNodeRuntime"]
