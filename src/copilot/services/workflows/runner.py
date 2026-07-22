"""Serial deterministic runner for a pre-built and validated TaskPlan."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from time import sleep

from copilot.contracts import (
    ErrorType,
    JsonObject,
    StepResult,
    StepResultStatus,
    TaskContract,
    TaskError,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskStatus,
    TaskStep,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from copilot.services.workflows.dependency import DependencyChecker
from copilot.services.workflows.errors import PlanValidationError, StepInputError, VerificationError
from copilot.services.workflows.fixed_plan import SUPPLIER_QUALITY_PLAN_ID
from copilot.services.workflows.inputs import StepInputBuilder, summarize_payload
from copilot.services.workflows.models import (
    StepExecutionRecord,
    ToolAttemptSummary,
    WorkflowAuditRecord,
    WorkflowExecution,
    WorkflowExecutionContext,
)
from copilot.services.workflows.ports import (
    ArtifactStore,
    EvidenceReader,
    IdentifierFactory,
    WorkflowAuditSink,
    WorkflowRepository,
)
from copilot.services.workflows.retry import WorkflowRetryPolicy
from copilot.services.workflows.state_machine import TaskStateMachine
from copilot.services.workflows.validation import PlanValidator
from copilot.services.workflows.verification import WorkflowVerifier
from copilot.tools.exceptions import ToolRuntimeError, ToolValidationError
from copilot.tools.executor import ToolExecutor
from copilot.tools.registry import ToolRegistry


class WorkflowRunner:
    """Coordinate state, dependencies, governed calls, evidence, retries, and finalization."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
        registry: ToolRegistry,
        plan_validator: PlanValidator,
        dependency_checker: DependencyChecker,
        input_builder: StepInputBuilder,
        retry_policy: WorkflowRetryPolicy,
        verifier: WorkflowVerifier,
        evidence_reader: EvidenceReader,
        artifact_store: ArtifactStore,
        repository: WorkflowRepository,
        audit_sink: WorkflowAuditSink,
        state_machine: TaskStateMachine,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        sleeper: Callable[[float], None] = sleep,
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

    def run(
        self,
        request: TaskRequest,
        contract: TaskContract,
        plan: TaskPlan,
    ) -> WorkflowExecution:
        """Run a fixed plan serially; the plan is never modified during execution."""
        started_at = self._clock()
        initial = self._state_machine.initial(contract.task_id)
        context = WorkflowExecutionContext(
            task_id=contract.task_id,
            request=request,
            contract=contract,
            plan=plan,
            task_state=initial,
            started_at=started_at,
        )
        self._repository.initialize(request, contract, plan, initial)
        self._emit(context, "workflow_started", status=initial.state.value)
        self._transition(context, "START_UNDERSTANDING", "Authenticated request accepted")
        self._transition(context, "CONTRACT_VALIDATED", "Frozen task contract supplied")
        try:
            self._plan_validator.validate(plan, contract)
        except PlanValidationError as exc:
            self._cancel_unexecuted(context, plan.steps, "PLAN_INVALID", str(exc))
            self._transition(context, "PLAN_INVALID", str(exc))
            return self._finalize_failed(context, started_at, str(exc))
        self._transition(context, "PLAN_APPROVED_BY_POLICY", "Offline mock scope pre-authorized")

        failure: str | None = None
        for index, step in enumerate(plan.steps):
            context.current_step_id = step.step_id
            dependency = self._dependency_checker.check(step, context.step_results)
            self._emit(
                context,
                "step_dependency_checked",
                step=step,
                status="SATISFIED" if dependency.satisfied else "DEPENDENCY_FAILED",
                metadata=JsonObject({"failed_dependencies": list(dependency.failed_dependencies)}),
            )
            if not dependency.satisfied:
                self._save_cancelled_step(
                    context,
                    step,
                    error_code="STEP_DEPENDENCY_FAILED",
                    message=dependency.reason or "Step dependency failed",
                    failed_dependencies=dependency.failed_dependencies,
                )
                failure = dependency.reason
                self._cancel_unexecuted(
                    context,
                    plan.steps[index + 1 :],
                    "STEP_NOT_EXECUTED_UPSTREAM_FAILURE",
                    failure or "A required dependency failed",
                )
                break
            result = self._execute_step(context, step)
            if result.status is not StepResultStatus.SUCCESS:
                failure = result.error.message if result.error is not None else "Step failed"
                self._cancel_unexecuted(
                    context,
                    plan.steps[index + 1 :],
                    "STEP_NOT_EXECUTED_UPSTREAM_FAILURE",
                    failure,
                )
                break
            self._transition(context, "STEP_SUCCEEDED", f"Step {step.step_id} succeeded")

        if failure is not None:
            if context.task_state.state is TaskStatus.EXECUTING:
                self._transition(context, "NON_RECOVERABLE_FAILURE", failure)
            return self._finalize_failed(context, started_at, failure)

        self._transition(
            context,
            "ALL_REQUIRED_STEPS_FINISHED",
            "All required steps and report artifact completed",
        )
        try:
            self._verifier.verify(context)
        except VerificationError as exc:
            self._transition(context, "NON_REPAIRABLE_VERIFICATION_FAILURE", str(exc))
            return self._finalize_failed(context, started_at, str(exc))
        self._transition(context, "VERIFICATION_PASSED", "Evidence and artifact verified")
        task_result = TaskResult(
            task_id=context.task_id,
            final_status=TaskStatus.COMPLETED,
            summary="Supplier quality analysis completed with verified evidence and report.",
            artifacts=tuple(item.artifact_id for item in context.artifacts),
            evidence=tuple(context.evidence),
        )
        self._repository.save_task_result(task_result)
        completed_at = self._clock()
        self._emit(
            context,
            "workflow_completed",
            status=TaskStatus.COMPLETED.value,
            duration_ms=_duration_ms(started_at, completed_at),
        )
        return self._execution(context, task_result, started_at, completed_at)

    def _execute_step(self, context: WorkflowExecutionContext, step: TaskStep) -> StepResult:
        step_started = self._clock()
        self._emit(context, "step_started", step=step, status="EXECUTING")
        try:
            arguments = self._input_builder.build(
                step,
                context.request,
                context.contract,
                context.step_results,
                context.evidence,
            )
        except StepInputError as exc:
            return self._save_input_failure(context, step, step_started, str(exc))
        definition = self._registry.get(step.tool_name).definition
        idempotency_key = _idempotency_key(
            context.task_id, step, definition.tool_version, arguments
        )
        attempts: list[ToolResult] = []
        attempt_number = 1
        while True:
            call = ToolCall(
                tool_call_id=self._ids.new_id("TC"),
                task_id=context.task_id,
                step_id=step.step_id,
                tool_name=definition.tool_name,
                tool_version=definition.tool_version,
                input=arguments,
                idempotency_key=idempotency_key,
                approval_id=None,
                deadline_at=context.contract.constraints.deadline_at,
                tenant_id=context.contract.constraints.tenant_id,
                user_id=context.request.user_id,
            )
            self._emit(
                context,
                "tool_attempt_started",
                step=step,
                attempt=attempt_number,
                status="EXECUTING",
            )
            try:
                tool_result = self._tool_executor.execute(call, attempt=attempt_number)
            except (ToolValidationError, ToolRuntimeError) as exc:
                tool_result = self._exception_result(call, attempt_number, exc)
            self._repository.save_tool_result(tool_result)
            context.tool_results.setdefault(step.step_id, []).append(tool_result)
            attempts.append(tool_result)
            if tool_result.status is ToolResultStatus.SUCCESS:
                self._collect_evidence(context, tool_result)
                self._emit(
                    context,
                    "tool_attempt_succeeded",
                    step=step,
                    attempt=attempt_number,
                    status=tool_result.status.value,
                    duration_ms=tool_result.latency_ms,
                    evidence_ids=tool_result.evidence_ids,
                )
                return self._save_step_success(
                    context, step, arguments, step_started, attempts, tool_result
                )
            self._emit(
                context,
                "tool_attempt_failed",
                step=step,
                attempt=attempt_number,
                status=tool_result.status.value,
                duration_ms=tool_result.latency_ms,
                error_type=(
                    tool_result.error.error_type.value if tool_result.error is not None else None
                ),
            )
            if not self._retry_policy.should_retry(step, definition, tool_result, attempt_number):
                return self._save_step_failure(
                    context, step, arguments, step_started, attempts, tool_result
                )
            self._transition(
                context,
                "TRANSIENT_FAILURE",
                f"Retrying {step.step_id} after attempt {attempt_number}",
            )
            delay = self._retry_policy.delay_for(step, attempt_number)
            self._emit(
                context,
                "tool_retry_scheduled",
                step=step,
                attempt=attempt_number + 1,
                status=TaskStatus.RETRYING.value,
                metadata=JsonObject({"delay_seconds": delay}),
            )
            if delay:
                self._sleeper(delay)
            self._transition(context, "RETRY_READY", "Retry backoff completed")
            attempt_number += 1
            context.retry_counts[step.step_id] = attempt_number - 1

    def _save_step_success(
        self,
        context: WorkflowExecutionContext,
        step: TaskStep,
        arguments: JsonObject,
        started_at: datetime,
        attempts: list[ToolResult],
        final: ToolResult,
    ) -> StepResult:
        completed_at = self._clock()
        result = StepResult(
            step_id=step.step_id,
            status=StepResultStatus.SUCCESS,
            output=final.output,
            evidence=final.evidence_ids,
            error=None,
        )
        record = self._execution_record(
            step, arguments, started_at, completed_at, attempts, final.output, True
        )
        self._save_step(context, result, record, "step_completed")
        if step.tool_name == "report_generator" and final.output is not None:
            artifact_id = final.output.root.get("artifact_id")
            if not isinstance(artifact_id, str):
                raise VerificationError("Report output omitted artifact identifier")
            artifact = self._artifact_store.get(artifact_id)
            context.artifacts.append(artifact)
            self._emit(
                context,
                "artifact_created",
                step=step,
                status="CREATED",
                artifact_id=artifact.artifact_id,
                evidence_ids=artifact.evidence_ids,
            )
        return result

    def _save_step_failure(
        self,
        context: WorkflowExecutionContext,
        step: TaskStep,
        arguments: JsonObject,
        started_at: datetime,
        attempts: list[ToolResult],
        final: ToolResult,
    ) -> StepResult:
        completed_at = self._clock()
        status = StepResultStatus(final.status.value)
        result = StepResult(
            step_id=step.step_id,
            status=status,
            output=final.output,
            evidence=final.evidence_ids,
            error=final.error,
        )
        record = self._execution_record(
            step, arguments, started_at, completed_at, attempts, final.output, True
        )
        self._save_step(context, result, record, "step_failed")
        return result

    def _save_input_failure(
        self,
        context: WorkflowExecutionContext,
        step: TaskStep,
        started_at: datetime,
        message: str,
    ) -> StepResult:
        completed_at = self._clock()
        error = TaskError(
            error_code="STEP_INPUT_INVALID",
            error_type=ErrorType.VALIDATION,
            message=message,
            recoverable=False,
            task_id=context.task_id,
            step_id=step.step_id,
        )
        result = StepResult(
            step_id=step.step_id,
            status=StepResultStatus.TECHNICAL_FAILURE,
            output=None,
            evidence=(),
            error=error,
        )
        record = StepExecutionRecord(
            step_id=step.step_id,
            tool_name=step.tool_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=_duration_ms(started_at, completed_at),
            attempt_count=0,
            executed=False,
            input_summary=JsonObject({}),
            output_summary=JsonObject({}),
        )
        self._save_step(context, result, record, "step_failed")
        return result

    def _save_cancelled_step(
        self,
        context: WorkflowExecutionContext,
        step: TaskStep,
        *,
        error_code: str,
        message: str,
        failed_dependencies: tuple[str, ...] = (),
    ) -> None:
        now = self._clock()
        error = TaskError(
            error_code=error_code,
            error_type=ErrorType.CANCELLATION,
            message=message,
            recoverable=False,
            task_id=context.task_id,
            step_id=step.step_id,
            details=JsonObject({"failed_dependencies": list(failed_dependencies)}),
        )
        result = StepResult(
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
            failed_dependencies=failed_dependencies,
        )
        self._save_step(context, result, record, "step_skipped")

    def _cancel_unexecuted(
        self,
        context: WorkflowExecutionContext,
        steps: tuple[TaskStep, ...],
        error_code: str,
        message: str,
    ) -> None:
        for step in steps:
            if step.step_id not in context.step_results:
                self._save_cancelled_step(
                    context,
                    step,
                    error_code=error_code,
                    message=message,
                    failed_dependencies=step.dependency,
                )

    def _save_step(
        self,
        context: WorkflowExecutionContext,
        result: StepResult,
        record: StepExecutionRecord,
        event: str,
    ) -> None:
        self._repository.save_step_result(result, record)
        context.step_results[result.step_id] = result
        context.step_executions[result.step_id] = record
        step = next(item for item in context.plan.steps if item.step_id == result.step_id)
        self._emit(
            context,
            event,
            step=step,
            status=result.status.value,
            duration_ms=record.duration_ms,
            error_type=result.error.error_type.value if result.error is not None else None,
            evidence_ids=result.evidence,
        )

    def _collect_evidence(self, context: WorkflowExecutionContext, tool_result: ToolResult) -> None:
        for evidence_id in tool_result.evidence_ids:
            if evidence_id not in context.evidence:
                context.evidence[evidence_id] = self._evidence_reader.get(evidence_id)
        if tool_result.evidence_ids:
            step = next(item for item in context.plan.steps if item.step_id == tool_result.step_id)
            self._emit(
                context,
                "evidence_collected",
                step=step,
                status="RECORDED",
                evidence_ids=tool_result.evidence_ids,
            )

    def _transition(self, context: WorkflowExecutionContext, event: str, reason: str) -> None:
        previous = context.task_state
        current, record = self._state_machine.transition(previous, event, reason=reason)
        self._repository.commit_transition(previous, current, record)
        context.task_state = current
        self._emit(
            context,
            "task_status_changed",
            status=current.state.value,
            metadata=JsonObject({"from": previous.state.value, "event": event, "reason": reason}),
        )

    def _finalize_failed(
        self,
        context: WorkflowExecutionContext,
        started_at: datetime,
        reason: str,
    ) -> WorkflowExecution:
        successful = sum(
            result.status is StepResultStatus.SUCCESS for result in context.step_results.values()
        )
        summary = (
            f"Supplier quality analysis failed after {successful} successful step(s); "
            f"committed evidence is retained. Reason: {reason}"
        )
        task_result = TaskResult(
            task_id=context.task_id,
            final_status=TaskStatus.FAILED,
            summary=summary,
            artifacts=(),
            evidence=tuple(context.evidence),
        )
        self._repository.save_task_result(task_result)
        completed_at = self._clock()
        event = "workflow_partially_completed" if successful else "workflow_failed"
        self._emit(
            context,
            event,
            status=TaskStatus.FAILED.value,
            duration_ms=_duration_ms(started_at, completed_at),
        )
        return self._execution(context, task_result, started_at, completed_at)

    def _execution(
        self,
        context: WorkflowExecutionContext,
        task_result: TaskResult,
        started_at: datetime,
        completed_at: datetime,
    ) -> WorkflowExecution:
        ordered_results = tuple(context.step_results[step.step_id] for step in context.plan.steps)
        ordered_records = tuple(
            context.step_executions[step.step_id] for step in context.plan.steps
        )
        return WorkflowExecution(
            task_result=task_result,
            final_state=context.task_state,
            step_results=ordered_results,
            step_executions=ordered_records,
            evidence=tuple(context.evidence.values()),
            artifacts=tuple(context.artifacts),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=_duration_ms(started_at, completed_at),
        )

    def _execution_record(
        self,
        step: TaskStep,
        arguments: JsonObject,
        started_at: datetime,
        completed_at: datetime,
        attempts: list[ToolResult],
        output: JsonObject | None,
        executed: bool,
    ) -> StepExecutionRecord:
        return StepExecutionRecord(
            step_id=step.step_id,
            tool_name=step.tool_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=_duration_ms(started_at, completed_at),
            attempt_count=len(attempts),
            executed=executed,
            input_summary=summarize_payload(arguments),
            output_summary=summarize_payload(output),
            attempts=tuple(
                ToolAttemptSummary(
                    attempt=result.attempt,
                    tool_call_id=result.tool_call_id,
                    status=result.status.value,
                    duration_ms=result.latency_ms or 0,
                    error_code=result.error.error_code if result.error is not None else None,
                )
                for result in attempts
            ),
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

    def _emit(
        self,
        context: WorkflowExecutionContext,
        event: str,
        *,
        step: TaskStep | None = None,
        attempt: int | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        evidence_ids: tuple[str, ...] = (),
        artifact_id: str | None = None,
        metadata: JsonObject | None = None,
    ) -> None:
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=context.task_id,
                plan_id=SUPPLIER_QUALITY_PLAN_ID,
                plan_version=context.plan.planning_version,
                timestamp=self._clock(),
                step_id=step.step_id if step is not None else None,
                tool_name=step.tool_name if step is not None else None,
                attempt=attempt,
                status=status,
                duration_ms=duration_ms,
                error_type=error_type,
                evidence_ids=evidence_ids,
                artifact_id=artifact_id,
                metadata=metadata or JsonObject({}),
            )
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
