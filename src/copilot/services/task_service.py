"""Unified natural-language task intake used by HTTP and CLI transports."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Protocol

from copilot.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    EvidenceItem,
    JsonObject,
    StepResult,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    TaskStatus,
    ToolResult,
)
from copilot.services.task_intake import (
    IntakeLimits,
    NaturalLanguageTaskCommand,
    TrustedCallerContext,
    TrustedTaskContext,
    merge_execution_constraints,
    sanitize_metadata,
    validate_task_text,
)
from copilot.services.task_views import (
    TaskEvidenceView,
    TaskStepView,
    TaskSummaryView,
)
from copilot.services.workflows.models import (
    StepExecutionRecord,
    TaskStateEvent,
    WorkflowAuditRecord,
    WorkflowExecution,
)
from copilot.services.workflows.ports import IdentifierFactory, WorkflowAuditSink
from copilot.services.workflows.state_machine import TaskStateMachine


class NaturalLanguageWorkflowEngine(Protocol):
    """Graph boundary required by the intake service."""

    def submit(
        self,
        request: TaskRequest,
        intake_context: TrustedTaskContext,
    ) -> WorkflowExecution:
        """Persist and execute one natural-language task."""
        ...


class TaskManagementRepository(Protocol):
    """Read/write task persistence required by the management use cases."""

    def state_for(self, task_id: str) -> TaskState: ...

    def request_for(self, task_id: str) -> TaskRequest: ...

    def contract_for(self, task_id: str) -> TaskContract | None: ...

    def plan_for(self, task_id: str) -> TaskPlan | None: ...

    def task_result_for(self, task_id: str) -> TaskResult | None: ...

    def step_results_for(self, task_id: str) -> tuple[StepResult, ...]: ...

    def step_execution_for(self, step_id: str) -> StepExecutionRecord | None: ...

    def tool_results_for(self, task_id: str) -> tuple[ToolResult, ...]: ...

    def state_events_for(self, task_id: str) -> tuple[TaskStateEvent, ...]: ...

    def commit_transition(
        self,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> None: ...

    def save_task_result(self, result: TaskResult) -> None: ...


class TaskEvidenceReader(Protocol):
    """Task-scoped evidence query port."""

    def list_for_task(self, task_id: str) -> tuple[EvidenceItem, ...]: ...


class TaskArtifactReader(Protocol):
    """Task-scoped Artifact metadata query port."""

    def list_by_task(self, task_id: str) -> tuple[Artifact, ...]: ...


class TaskApprovalRepository(Protocol):
    """Approval persistence needed to invalidate pending grants on cancellation."""

    def get_pending_for_task(self, task_id: str) -> tuple[ApprovalRequest, ...]: ...

    def resolve(self, pending: ApprovalRequest, resolved: ApprovalRequest) -> None: ...


class TaskServiceError(RuntimeError):
    """Safe typed task-management failure mapped centrally by interfaces."""

    def __init__(self, code: str, message: str, *, status_code: int, task_id: str | None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.task_id = task_id


class TaskNotFoundError(TaskServiceError):
    """Raised when a task identifier is unknown."""

    def __init__(self, task_id: str) -> None:
        super().__init__("TASK_NOT_FOUND", "Task was not found.", status_code=404, task_id=task_id)


class TaskPermissionDeniedError(TaskServiceError):
    """Raised when caller identity does not own the task scope."""

    def __init__(self, task_id: str) -> None:
        super().__init__(
            "TASK_PERMISSION_DENIED",
            "Caller is not permitted to access this task.",
            status_code=403,
            task_id=task_id,
        )


class TaskNotCancellableError(TaskServiceError):
    """Raised when cancellation targets a completed or failed terminal task."""

    def __init__(self, task_id: str) -> None:
        super().__init__(
            "TASK_NOT_CANCELLABLE",
            "Task is already in a terminal state and cannot be cancelled.",
            status_code=409,
            task_id=task_id,
        )


class NaturalLanguageTaskService:
    """Validate, constrain, identify, and submit immutable natural-language requests."""

    def __init__(
        self,
        *,
        engine: NaturalLanguageWorkflowEngine,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        limits: IntakeLimits,
        repository: TaskManagementRepository | None = None,
        evidence: TaskEvidenceReader | None = None,
        artifacts: TaskArtifactReader | None = None,
        approvals: TaskApprovalRepository | None = None,
        state_machine: TaskStateMachine | None = None,
        audit_sink: WorkflowAuditSink | None = None,
    ) -> None:
        self._engine = engine
        self._ids = ids
        self._clock = clock
        self._limits = limits
        self._repository = repository
        self._evidence = evidence
        self._artifacts = artifacts
        self._approvals = approvals
        self._state_machine = state_machine
        self._audit_sink = audit_sink

    def submit(
        self,
        command: NaturalLanguageTaskCommand,
        caller: TrustedCallerContext,
    ) -> WorkflowExecution:
        """Create TaskRequest and trusted context, then enter the existing LangGraph."""
        request, context = self.prepare(command, caller)
        return self._engine.submit(request, context)

    def prepare(
        self,
        command: NaturalLanguageTaskCommand,
        caller: TrustedCallerContext,
    ) -> tuple[TaskRequest, TrustedTaskContext]:
        """Validate an intake without executing it; useful for thin CLI dry-runs and tests."""
        task_text = validate_task_text(
            command.task,
            max_length=self._limits.max_task_text_length,
        )
        metadata = sanitize_metadata(
            command.metadata,
            max_bytes=self._limits.max_metadata_bytes,
            max_depth=self._limits.max_metadata_depth,
            max_items=self._limits.max_metadata_items,
        )
        effective = merge_execution_constraints(
            limits=self._limits,
            caller=caller,
            requested_max_steps=command.max_steps,
            requested_read_only=command.read_only,
            requested_approval=command.require_approval,
        )
        now = self._clock()
        task_id = self._ids.new_id("T")
        trace_id = command.trace_id or self._ids.new_id("TRACE")
        session_id = command.session_id or self._ids.new_id("SESSION")
        task_hash = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
        request_metadata = dict(metadata.root)
        request_metadata["intake"] = {
            "request_source": command.source.value,
            "session_id": session_id,
            "trace_id": trace_id,
            "task_text_hash": task_hash,
            "task_text_length": len(task_text),
            "output_format": (
                command.output_format.value if command.output_format is not None else None
            ),
            "effective_max_steps": effective.max_steps,
            "effective_read_only": effective.read_only,
            "effective_require_approval": effective.require_approval,
        }
        request = TaskRequest(
            id=self._ids.new_id("R"),
            user_id=caller.user_id,
            raw_input=task_text,
            created_at=now,
            metadata=JsonObject(request_metadata),
        )
        context = TrustedTaskContext(
            task_id=task_id,
            trace_id=trace_id,
            session_id=session_id,
            user_id=caller.user_id,
            tenant_id=caller.tenant_id,
            data_scope=caller.data_scope,
            authorized_supplier_ids=caller.supplier_ids,
            roles=caller.roles,
            output_format=(
                command.output_format.artifact_type if command.output_format is not None else None
            ),
            max_steps=effective.max_steps,
            read_only=effective.read_only,
            require_approval=effective.require_approval,
            deadline_at=now + timedelta(seconds=self._limits.max_total_execution_seconds),
            request_source=command.source,
            task_text_hash=task_hash,
            task_text_length=len(task_text),
        )
        return request, context

    def get_task(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> TaskSummaryView:
        """Return one authorized, stable task summary."""
        request, state, contract, plan = self._load_authorized(task_id, caller, trace_id=trace_id)
        return self._task_view(request, state, contract, plan)

    def list_task_steps(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> tuple[TaskStepView, ...]:
        """Combine plan steps with persisted results without exposing tool inputs."""
        _request, _state, _contract, plan = self._load_authorized(
            task_id, caller, trace_id=trace_id
        )
        if plan is None:
            return ()
        repository = self._require_repository()
        results = {result.step_id: result for result in repository.step_results_for(task_id)}
        views: list[TaskStepView] = []
        for step in plan.steps:
            result = results.get(step.step_id)
            execution = repository.step_execution_for(step.step_id)
            error = result.error if result is not None else None
            views.append(
                TaskStepView(
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    purpose=_STEP_PURPOSES[step.step_type.value],
                    status=result.status.value if result is not None else "PENDING",
                    depends_on=step.dependency,
                    attempt_count=execution.attempt_count if execution is not None else 0,
                    retry_count=max(
                        (execution.attempt_count if execution is not None else 0) - 1, 0
                    ),
                    started_at=execution.started_at if execution is not None else None,
                    completed_at=execution.completed_at if execution is not None else None,
                    latency_ms=execution.duration_ms if execution is not None else None,
                    evidence_ids=result.evidence if result is not None else (),
                    error_code=error.error_code if error is not None else None,
                    error_message=error.message if error is not None else None,
                )
            )
        return tuple(views)

    def list_task_evidence(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> tuple[TaskEvidenceView, ...]:
        """Return persisted Evidence metadata and lineage without regenerating content."""
        _request, _state, _contract, plan = self._load_authorized(
            task_id, caller, trace_id=trace_id
        )
        if self._evidence is None:
            return ()
        repository = self._require_repository()
        producers = {
            result.tool_call_id: result.tool_name for result in repository.tool_results_for(task_id)
        }
        items = sorted(
            self._evidence.list_for_task(task_id),
            key=lambda item: (item.timestamp, item.evidence_id),
        )
        views = tuple(_evidence_view(item, producers.get(item.tool_call_id)) for item in items)
        self._audit("evidence_viewed", task_id, plan, caller, trace_id)
        return views

    def cancel_task(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> TaskSummaryView:
        """Apply the frozen CANCEL_REQUESTED transition and invalidate old approvals."""
        request, state, contract, plan = self._load_authorized(task_id, caller, trace_id=trace_id)
        if state.state is TaskStatus.CANCELLED:
            return self._task_view(request, state, contract, plan)
        if state.state in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            self._audit("task_cancellation_rejected", task_id, plan, caller, trace_id)
            raise TaskNotCancellableError(task_id)
        if self._state_machine is None:
            raise RuntimeError("Task cancellation is not composed")
        current, event = self._state_machine.transition(
            state,
            "CANCEL_REQUESTED",
            reason="Cancellation requested by an authorized task owner",
        )
        repository = self._require_repository()
        repository.commit_transition(state, current, event)
        if self._approvals is not None:
            for pending in self._approvals.get_pending_for_task(task_id):
                revoked = pending.model_copy(
                    update={
                        "status": ApprovalStatus.REVOKED,
                        "decided_at": self._clock(),
                        "version": pending.version + 1,
                    }
                )
                self._approvals.resolve(pending, revoked)
        if repository.task_result_for(task_id) is None:
            repository.save_task_result(
                TaskResult(
                    task_id=task_id,
                    final_status=TaskStatus.CANCELLED,
                    summary=(
                        "Task was cancelled by an authorized caller; committed evidence "
                        "is retained."
                    ),
                    artifacts=(),
                    evidence=tuple(
                        item.evidence_id
                        for item in (
                            self._evidence.list_for_task(task_id) if self._evidence else ()
                        )
                    ),
                )
            )
        self._audit("task_cancellation_requested", task_id, plan, caller, trace_id)
        self._audit("task_cancelled", task_id, plan, caller, trace_id)
        return self._task_view(request, current, contract, plan)

    def _load_authorized(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str,
    ) -> tuple[TaskRequest, TaskState, TaskContract | None, TaskPlan | None]:
        repository = self._require_repository()
        try:
            request = repository.request_for(task_id)
            state = repository.state_for(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        contract = repository.contract_for(task_id)
        tenant_matches = contract is None or contract.constraints.tenant_id == caller.tenant_id
        if request.user_id != caller.user_id or not tenant_matches:
            self._audit(
                "permission_denied", task_id, repository.plan_for(task_id), caller, trace_id
            )
            raise TaskPermissionDeniedError(task_id)
        return request, state, contract, repository.plan_for(task_id)

    def _task_view(
        self,
        request: TaskRequest,
        state: TaskState,
        contract: TaskContract | None,
        plan: TaskPlan | None,
    ) -> TaskSummaryView:
        repository = self._require_repository()
        task_id = state.task_id
        steps = repository.step_results_for(task_id)
        events = repository.state_events_for(task_id)
        task_result = repository.task_result_for(task_id)
        pending = self._approvals.get_pending_for_task(task_id) if self._approvals else ()
        completed_at = state.updated_at if state.state in _TERMINAL_STATES else None
        current_step = next(
            (
                step.step_id
                for step in (plan.steps if plan is not None else ())
                if all(result.step_id != step.step_id for result in steps)
            ),
            None,
        )
        errors = [result.error for result in steps if result.error is not None]
        error_summary = errors[-1].message if errors else None
        if error_summary is None and task_result is not None and state.state is TaskStatus.FAILED:
            error_summary = task_result.summary
        return TaskSummaryView(
            task_id=task_id,
            trace_id=_trace_id(request),
            status=state.state.value,
            task_type=contract.task_type.value if contract is not None else None,
            created_at=request.created_at,
            started_at=events[0].timestamp if events else request.created_at,
            completed_at=completed_at,
            cancelled_at=state.updated_at if state.state is TaskStatus.CANCELLED else None,
            current_step=current_step,
            task_summary=_task_summary(request.raw_input),
            pending_approval_id=pending[0].approval_id if pending else None,
            step_count=len(plan.steps) if plan is not None else 0,
            evidence_count=(
                len(self._evidence.list_for_task(task_id)) if self._evidence is not None else 0
            ),
            artifact_count=(
                len(self._artifacts.list_by_task(task_id)) if self._artifacts is not None else 0
            ),
            error_summary=error_summary,
        )

    def _require_repository(self) -> TaskManagementRepository:
        if self._repository is None:
            raise RuntimeError("Task management persistence is not composed")
        return self._repository

    def _audit(
        self,
        event: str,
        task_id: str,
        plan: TaskPlan | None,
        caller: TrustedCallerContext,
        trace_id: str,
    ) -> None:
        if self._audit_sink is None:
            return
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=task_id,
                plan_id="supplier-quality-analysis",
                plan_version=plan.planning_version if plan is not None else 0,
                timestamp=self._clock(),
                metadata=JsonObject(
                    {
                        "actor_id": caller.user_id,
                        "tenant_id": caller.tenant_id,
                        "trace_id": trace_id,
                    }
                ),
            )
        )


_TERMINAL_STATES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}

_STEP_PURPOSES = {
    "KNOWLEDGE_SEARCH": "Retrieve approved supplier-quality policy evidence.",
    "DATABASE_QUERY": "Query approved supplier-quality data using a read-only template.",
    "ANALYSIS": "Calculate deterministic supplier-quality metrics.",
    "REPORT_GENERATION": "Generate the governed internal quality-analysis report.",
}


def _trace_id(request: TaskRequest) -> str:
    intake = request.metadata.root.get("intake")
    if isinstance(intake, dict):
        value = intake.get("trace_id")
        if isinstance(value, str) and value:
            return value
    return request.id


def _task_summary(raw_input: str) -> str:
    normalized = " ".join(raw_input.split())
    return normalized if len(normalized) <= 240 else f"{normalized[:237]}..."


def _evidence_view(item: EvidenceItem, producer: str | None) -> TaskEvidenceView:
    reference = item.source_reference.reference.root
    data = item.content.data.root
    query_id = _first_string(reference, "query_id", "query_fingerprint", "query_template_id")
    document = _first_string(reference, "document_id", "document_source", "chunk_id")
    formula = _first_string(reference, "formula", "calculation_formula") or _first_string(
        data, "formula"
    )
    source = query_id or document or _first_string(reference, "engine_version", "algorithm_version")
    fields = ", ".join(sorted(str(key) for key in data)[:8])
    summary = f"{item.source_type.value} evidence"
    if fields:
        summary = f"{summary} with fields: {fields}"
    confidence_value = data.get("confidence")
    confidence = (
        float(confidence_value)
        if isinstance(confidence_value, (int, float)) and not isinstance(confidence_value, bool)
        else None
    )
    return TaskEvidenceView(
        evidence_id=item.evidence_id,
        type=item.source_type.value,
        source=source or item.source_type.value.lower(),
        produced_by=producer or "governed_tool",
        step_id=item.step_id,
        lineage=item.source_reference.input_evidence_ids,
        confidence=confidence,
        created_at=item.timestamp,
        query_id=query_id,
        document_source=document,
        formula=formula,
        input_evidence_ids=item.source_reference.input_evidence_ids,
        content_summary=summary,
    )


def _first_string(values: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = [
    "NaturalLanguageTaskService",
    "NaturalLanguageWorkflowEngine",
    "TaskNotCancellableError",
    "TaskNotFoundError",
    "TaskPermissionDeniedError",
    "TaskServiceError",
]
