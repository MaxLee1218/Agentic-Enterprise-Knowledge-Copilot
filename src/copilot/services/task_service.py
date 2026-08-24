"""Unified natural-language task intake used by HTTP and CLI transports."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import JsonValue

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
    TaskType,
    ToolResult,
)
from copilot.policies.permissions import AuthorizationRequest, Permission, PermissionMatrix
from copilot.security import (
    ContentSourceType,
    OutputDisposition,
    OutputGuard,
    PromptInjectionDetector,
)
from copilot.security.redaction import redact_text
from copilot.services.observability import (
    EventName,
    NoopObservability,
    ObservabilityPort,
    validate_correlation_id,
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
    TaskListView,
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
from copilot.tools.cancellation import InvocationCancellationRegistry


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

    def state_for(self, task_id: str, *, tenant_id: str) -> TaskState: ...

    def list_task_ids(
        self,
        *,
        tenant_id: str,
        user_id: str,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[str, ...], int]: ...

    def request_for(self, task_id: str, *, tenant_id: str) -> TaskRequest: ...

    def contract_for(self, task_id: str, *, tenant_id: str) -> TaskContract | None: ...

    def plan_for(self, task_id: str, *, tenant_id: str) -> TaskPlan | None: ...

    def task_result_for(self, task_id: str, *, tenant_id: str) -> TaskResult | None: ...

    def step_results_for(self, task_id: str, *, tenant_id: str) -> tuple[StepResult, ...]: ...

    def step_execution_for(
        self, task_id: str, step_id: str, *, tenant_id: str
    ) -> StepExecutionRecord | None: ...

    def tool_results_for(self, task_id: str, *, tenant_id: str) -> tuple[ToolResult, ...]: ...

    def state_events_for(self, task_id: str, *, tenant_id: str) -> tuple[TaskStateEvent, ...]: ...

    def commit_transition(
        self,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
        *,
        tenant_id: str,
    ) -> None: ...

    def save_task_result(self, result: TaskResult, *, tenant_id: str) -> None: ...


class TaskEvidenceReader(Protocol):
    """Task-scoped evidence query port."""

    def list_for_task(self, task_id: str, *, tenant_id: str) -> tuple[EvidenceItem, ...]: ...


class TaskArtifactReader(Protocol):
    """Task-scoped Artifact metadata query port."""

    def list_by_task(self, task_id: str, *, tenant_id: str) -> tuple[Artifact, ...]: ...


class TaskApprovalRepository(Protocol):
    """Approval persistence needed to invalidate pending grants on cancellation."""

    def get_pending_for_task(
        self, task_id: str, *, tenant_id: str
    ) -> tuple[ApprovalRequest, ...]: ...

    def resolve(
        self, pending: ApprovalRequest, resolved: ApprovalRequest, *, tenant_id: str
    ) -> None: ...


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
        injection_detector: PromptInjectionDetector | None = None,
        output_guard: OutputGuard | None = None,
        permission_matrix: PermissionMatrix | None = None,
        observability: ObservabilityPort | None = None,
        cancellations: InvocationCancellationRegistry | None = None,
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
        self._injection_detector = injection_detector or PromptInjectionDetector()
        self._output_guard = output_guard or OutputGuard()
        self._permission_matrix = permission_matrix or PermissionMatrix()
        self._observability = observability or NoopObservability()
        self._cancellations = cancellations or InvocationCancellationRegistry()

    def submit(
        self,
        command: NaturalLanguageTaskCommand,
        caller: TrustedCallerContext,
    ) -> WorkflowExecution:
        """Create TaskRequest and trusted context, then enter the existing LangGraph."""
        if not caller.authenticated:
            raise TaskPermissionDeniedError("TASK-UNAUTHENTICATED")
        request, context = self.prepare(command, caller)
        with self._observability.bind_context(
            task_id=context.task_id,
            trace_id=context.trace_id,
            request_id=request.id,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=context.session_id,
        ):
            self._observability.emit(
                EventName.TASK_CREATED,
                fields={
                    "status": TaskStatus.CREATED.value,
                    "request_source": context.request_source.value,
                    "input_size": context.task_text_length,
                    "input_hash": context.task_text_hash,
                },
            )
            security = request.metadata.root.get("security")
            if isinstance(security, dict) and security.get("finding_count"):
                self._audit_security_finding(request, context, caller, security)
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
        trace_id = validate_correlation_id(command.trace_id) or self._ids.new_id("TRACE")
        session_id = command.session_id or self._ids.new_id("SESSION")
        task_hash = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
        injection_scan = self._injection_detector.scan(
            task_text,
            source_type=ContentSourceType.USER_INPUT,
            source_id=task_id,
        )
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
        request_metadata["security"] = {
            "finding_count": len(injection_scan.findings),
            "finding_ids": [finding.finding_id for finding in injection_scan.findings],
            "categories": list(
                dict.fromkeys(finding.category for finding in injection_scan.findings)
            ),
            "maximum_severity": self._injection_detector.maximum_severity(
                injection_scan.findings
            ).value,
            "content_hash": task_hash,
        }
        request = TaskRequest(
            id=self._ids.new_id("R"),
            user_id=caller.user_id,
            raw_input=task_text,
            created_at=now,
            metadata=JsonObject(request_metadata),
        )
        task_type = TaskType(caller.purpose)
        context = TrustedTaskContext(
            task_id=task_id,
            trace_id=trace_id,
            session_id=session_id,
            user_id=caller.user_id,
            tenant_id=caller.tenant_id,
            data_scope=caller.data_scope,
            authorized_supplier_ids=caller.supplier_ids,
            authorized_legal_entity_ids=caller.legal_entity_ids,
            authorized_business_unit_ids=caller.business_unit_ids,
            authorized_currency_scope=caller.currency_scope,
            policy_rule_set_id=caller.policy_rule_set_id,
            policy_rule_set_version=caller.policy_rule_set_version,
            policy_manifest_checksum=caller.policy_manifest_checksum,
            policy_materiality=caller.policy_materiality,
            policy_snapshot_at=caller.policy_snapshot_at,
            roles=caller.roles,
            scopes=caller.scopes,
            authentication_source=caller.authentication_source,
            authenticated=caller.authenticated,
            is_demo_identity=caller.is_demo_identity,
            purpose=caller.purpose,
            task_type=task_type,
            output_format=(
                command.output_format.artifact_type_for(task_type)
                if command.output_format is not None
                else None
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
        request, state, contract, plan = self._load_authorized(
            task_id, caller, trace_id=trace_id, permission=Permission.READ_TASK
        )
        return self._task_view(request, state, contract, plan, tenant_id=caller.tenant_id)

    def list_tasks(
        self,
        caller: TrustedCallerContext,
        *,
        status: TaskStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TaskListView:
        """Return bounded newest-first task history for the authenticated owner."""
        decision = self._permission_matrix.evaluate(
            AuthorizationRequest(
                action=Permission.READ_TASK,
                roles=caller.roles,
                resource_type="task_collection",
                purpose=caller.purpose,
                is_demo_identity=caller.is_demo_identity,
            )
        )
        if not caller.authenticated or not decision.allowed:
            raise TaskPermissionDeniedError("TASK-COLLECTION")
        repository = self._require_repository()
        task_ids, total = repository.list_task_ids(
            tenant_id=caller.tenant_id,
            user_id=caller.user_id,
            status=status.value if status is not None else None,
            limit=limit,
            offset=offset,
        )
        items = tuple(
            self._task_view(
                repository.request_for(task_id, tenant_id=caller.tenant_id),
                repository.state_for(task_id, tenant_id=caller.tenant_id),
                repository.contract_for(task_id, tenant_id=caller.tenant_id),
                repository.plan_for(task_id, tenant_id=caller.tenant_id),
                tenant_id=caller.tenant_id,
            )
            for task_id in task_ids
        )
        return TaskListView(items=items, total=total, limit=limit, offset=offset)

    def list_task_steps(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> tuple[TaskStepView, ...]:
        """Combine plan steps with persisted results without exposing tool inputs."""
        _request, _state, _contract, plan = self._load_authorized(
            task_id, caller, trace_id=trace_id, permission=Permission.READ_TASK
        )
        if plan is None:
            return ()
        repository = self._require_repository()
        results = {
            result.step_id: result
            for result in repository.step_results_for(task_id, tenant_id=caller.tenant_id)
        }
        views: list[TaskStepView] = []
        for step in plan.steps:
            result = results.get(step.step_id)
            execution = repository.step_execution_for(
                task_id,
                step.step_id,
                tenant_id=caller.tenant_id,
            )
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
            task_id, caller, trace_id=trace_id, permission=Permission.READ_EVIDENCE
        )
        if self._evidence is None:
            return ()
        repository = self._require_repository()
        producers = {
            result.tool_call_id: result.tool_name
            for result in repository.tool_results_for(task_id, tenant_id=caller.tenant_id)
        }
        items = sorted(
            self._evidence.list_for_task(task_id, tenant_id=caller.tenant_id),
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
        request, state, contract, plan = self._load_authorized(
            task_id, caller, trace_id=trace_id, permission=Permission.CANCEL_TASK
        )
        if state.state is TaskStatus.CANCELLED:
            return self._task_view(request, state, contract, plan, tenant_id=caller.tenant_id)
        if state.state in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            self._audit("task_cancellation_rejected", task_id, plan, caller, trace_id)
            raise TaskNotCancellableError(task_id)
        self._cancellations.cancel_task(
            task_id,
            reason="Cancellation requested by an authorized task owner",
        )
        if self._state_machine is None:
            raise RuntimeError("Task cancellation is not composed")
        current, event = self._state_machine.transition(
            state,
            "CANCEL_REQUESTED",
            reason="Cancellation requested by an authorized task owner",
        )
        repository = self._require_repository()
        repository.commit_transition(
            state,
            current,
            event,
            tenant_id=caller.tenant_id,
        )
        if self._approvals is not None:
            for pending in self._approvals.get_pending_for_task(
                task_id, tenant_id=caller.tenant_id
            ):
                revoked = pending.model_copy(
                    update={
                        "status": ApprovalStatus.REVOKED,
                        "decided_at": self._clock(),
                        "version": pending.version + 1,
                    }
                )
                self._approvals.resolve(
                    pending,
                    revoked,
                    tenant_id=caller.tenant_id,
                )
        if repository.task_result_for(task_id, tenant_id=caller.tenant_id) is None:
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
                            self._evidence.list_for_task(task_id, tenant_id=caller.tenant_id)
                            if self._evidence
                            else ()
                        )
                    ),
                ),
                tenant_id=caller.tenant_id,
            )
        self._audit("task_cancellation_requested", task_id, plan, caller, trace_id)
        self._audit("task_cancelled", task_id, plan, caller, trace_id)
        return self._task_view(request, current, contract, plan, tenant_id=caller.tenant_id)

    def _load_authorized(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str,
        permission: Permission,
    ) -> tuple[TaskRequest, TaskState, TaskContract | None, TaskPlan | None]:
        repository = self._require_repository()
        try:
            request = repository.request_for(task_id, tenant_id=caller.tenant_id)
            state = repository.state_for(task_id, tenant_id=caller.tenant_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        contract = repository.contract_for(task_id, tenant_id=caller.tenant_id)
        decision = self._permission_matrix.evaluate(
            AuthorizationRequest(
                action=permission,
                roles=caller.roles,
                resource_type="task",
                resource_name=task_id,
                task_id=task_id,
                purpose=caller.purpose,
                is_demo_identity=caller.is_demo_identity,
            )
        )
        if request.user_id != caller.user_id or not decision.allowed:
            self._audit(
                "permission_denied",
                task_id,
                repository.plan_for(task_id, tenant_id=caller.tenant_id),
                caller,
                trace_id,
            )
            raise TaskPermissionDeniedError(task_id)
        return (
            request,
            state,
            contract,
            repository.plan_for(task_id, tenant_id=caller.tenant_id),
        )

    def is_artifact_published(self, task_id: str, artifact_id: str, *, tenant_id: str) -> bool:
        """Return whether finalization explicitly published an Artifact for a completed Task."""
        repository = self._require_repository()
        result = repository.task_result_for(task_id, tenant_id=tenant_id)
        return (
            result is not None
            and result.final_status is TaskStatus.COMPLETED
            and artifact_id in result.artifacts
        )

    def _task_view(
        self,
        request: TaskRequest,
        state: TaskState,
        contract: TaskContract | None,
        plan: TaskPlan | None,
        *,
        tenant_id: str,
    ) -> TaskSummaryView:
        repository = self._require_repository()
        task_id = state.task_id
        steps = repository.step_results_for(task_id, tenant_id=tenant_id)
        events = repository.state_events_for(task_id, tenant_id=tenant_id)
        task_result = repository.task_result_for(task_id, tenant_id=tenant_id)
        pending = (
            self._approvals.get_pending_for_task(task_id, tenant_id=tenant_id)
            if self._approvals
            else ()
        )
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
        root_errors = [
            error for error in errors if error.error_code != "STEP_NOT_EXECUTED_UPSTREAM_FAILURE"
        ]
        error_summary = (root_errors[0] if root_errors else errors[0]).message if errors else None
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
            task_summary=_task_summary(request.raw_input, self._output_guard),
            pending_approval_id=pending[0].approval_id if pending else None,
            step_count=len(plan.steps) if plan is not None else 0,
            evidence_count=(
                len(self._evidence.list_for_task(task_id, tenant_id=tenant_id))
                if self._evidence is not None
                else 0
            ),
            artifact_count=(
                len(self._artifacts.list_by_task(task_id, tenant_id=tenant_id))
                if self._artifacts is not None
                else 0
            ),
            error_summary=(redact_text(error_summary) if error_summary is not None else None),
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
                tenant_id=caller.tenant_id,
                trace_id=trace_id,
                actor_id=caller.user_id,
                scopes=caller.scopes,
                metadata=JsonObject(
                    {
                        "actor_id": caller.user_id,
                        "tenant_id": caller.tenant_id,
                        "trace_id": trace_id,
                    }
                ),
            )
        )

    def _audit_security_finding(
        self,
        request: TaskRequest,
        context: TrustedTaskContext,
        caller: TrustedCallerContext,
        security: Mapping[str, JsonValue],
    ) -> None:
        if self._audit_sink is None:
            return
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event="prompt_injection_finding",
                task_id=context.task_id,
                plan_id="supplier-quality-analysis",
                plan_version=0,
                timestamp=self._clock(),
                tenant_id=context.tenant_id,
                trace_id=context.trace_id,
                actor_id=caller.user_id,
                scopes=caller.scopes,
                status=str(security.get("maximum_severity", "NONE")),
                metadata=JsonObject(
                    {
                        "actor_id": caller.user_id,
                        "tenant_id": caller.tenant_id,
                        "trace_id": context.trace_id,
                        "source_type": ContentSourceType.USER_INPUT.value,
                        "finding_count": security.get("finding_count", 0),
                        "finding_ids": security.get("finding_ids", []),
                        "categories": security.get("categories", []),
                        "content_hash": security.get("content_hash", ""),
                        "request_id": request.id,
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


def _task_summary(raw_input: str, output_guard: OutputGuard) -> str:
    normalized = " ".join(raw_input.split())
    guarded = output_guard.guard(
        normalized,
        source_type=ContentSourceType.USER_INPUT,
        source_id="task-summary",
        target="api",
    )
    if guarded.disposition is OutputDisposition.BLOCKED or not isinstance(guarded.content, str):
        return "Task input was withheld by the output safety policy."
    normalized = guarded.content
    return normalized if len(normalized) <= 240 else f"{normalized[:237]}..."


def _evidence_view(item: EvidenceItem, producer: str | None) -> TaskEvidenceView:
    reference = item.source_reference.reference.root
    data = item.content.data.root
    query_id = _first_string(reference, "query_id", "query_fingerprint", "query_template_id")
    document = _first_string(reference, "document_id", "document_source", "chunk_id")
    formula = _first_string(reference, "formula", "calculation_formula") or _first_string(
        data, "formula"
    )
    if formula is None:
        formulas = reference.get("formulas")
        if isinstance(formulas, dict):
            normalized_formulas = [
                f"{key}: {value}"
                for key, value in sorted(formulas.items())
                if isinstance(key, str) and isinstance(value, str) and value
            ]
            formula = "; ".join(normalized_formulas) or None
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
