"""Application services for creating, resolving, and resuming v1.1 approvals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from copilot.contracts import (
    ApprovalRequest,
    ApprovalResolutionAction,
    ApprovalStatus,
    JsonObject,
    TaskContract,
    TaskPlan,
    TaskStatus,
    TaskStep,
    ToolDefinition,
)
from copilot.policies.approval import (
    ApprovalPolicyDecision,
    action_fingerprint,
    arguments_fingerprint,
    changed_top_level_fields,
    schema_fingerprint,
)
from copilot.policies.permissions import AuthorizationRequest, Permission, PermissionMatrix
from copilot.security.redaction import redact_text
from copilot.services.observability import EventName, NoopObservability, ObservabilityPort
from copilot.services.task_intake import TrustedCallerContext
from copilot.services.workflows.fixed_plan import SUPPLIER_QUALITY_PLAN_ID
from copilot.services.workflows.models import WorkflowAuditRecord, WorkflowExecution
from copilot.services.workflows.ports import IdentifierFactory, WorkflowAuditSink
from copilot.tools.exceptions import ToolRuntimeError, ToolValidationError
from copilot.tools.registry import ToolRegistry
from copilot.tools.schema import validate_payload


class ApprovalRepositoryPort(Protocol):
    """Persistence operations required by approval application services."""

    def create(self, approval: ApprovalRequest, *, tenant_id: str) -> None: ...

    def get(self, approval_id: str, *, tenant_id: str) -> ApprovalRequest: ...

    def get_pending_for_task(
        self, task_id: str, *, tenant_id: str
    ) -> tuple[ApprovalRequest, ...]: ...

    def list_by_task(self, task_id: str, *, tenant_id: str) -> tuple[ApprovalRequest, ...]: ...

    def resolve(
        self, pending: ApprovalRequest, resolved: ApprovalRequest, *, tenant_id: str
    ) -> None: ...


class ApprovalWorkflowEngine(Protocol):
    """Checkpoint operations required after a durable human decision."""

    def approval_state(self, task_id: str, tenant_id: str) -> dict[str, object]:
        """Return a minimized trusted checkpoint view for resolution validation."""
        ...

    def resume_approval(
        self, approval: ApprovalRequest, tenant_id: str
    ) -> WorkflowExecution | None:
        """Apply the decision to TaskState and resume or cancel the checkpoint."""
        ...


class ApprovalServiceError(RuntimeError):
    """Safe stable approval failure exposed through the API adapter."""

    code = "APPROVAL_ERROR"
    status_code = 409


class ApprovalNotFoundError(ApprovalServiceError):
    code = "APPROVAL_NOT_FOUND"
    status_code = 404


class ApprovalAlreadyResolvedError(ApprovalServiceError):
    code = "APPROVAL_ALREADY_RESOLVED"


class ApprovalPermissionDeniedError(ApprovalServiceError):
    code = "APPROVAL_PERMISSION_DENIED"
    status_code = 403


class ApprovalStateConflictError(ApprovalServiceError):
    code = "APPROVAL_STATE_CONFLICT"


class ApprovalArgumentsInvalidError(ApprovalServiceError):
    code = "APPROVAL_ARGUMENTS_INVALID"
    status_code = 422


class ApprovalExpiredError(ApprovalServiceError):
    code = "APPROVAL_EXPIRED"


@dataclass(frozen=True, slots=True)
class ApprovalResolutionCommand:
    """Framework-independent human approval decision command."""

    task_id: str
    approval_id: str
    action: ApprovalResolutionAction
    reason: str | None = None
    edited_arguments: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class ApprovalResolutionResult:
    """Resolved approval plus the latest workflow outcome, if terminal."""

    approval: ApprovalRequest
    task_status: TaskStatus
    trace_id: str
    execution: WorkflowExecution | None


class ApprovalGateService:
    """Create one exact pending approval from a deterministic graph policy decision."""

    def __init__(
        self,
        *,
        repository: ApprovalRepositoryPort,
        audit_sink: WorkflowAuditSink,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._audit_sink = audit_sink
        self._ids = ids
        self._clock = clock
        self._ttl_seconds = ttl_seconds

    def require(
        self,
        *,
        trace_id: str,
        requester: str,
        contract: TaskContract,
        plan: TaskPlan,
        step: TaskStep,
        definition: ToolDefinition,
        arguments: JsonObject,
        decision: ApprovalPolicyDecision,
    ) -> ApprovalRequest:
        """Return an identical pending request on graph replay or create it once."""
        if decision.required_role is None or not decision.controlled_scope:
            raise ApprovalStateConflictError("Approval policy omitted role or controlled scope")
        schema_digest = schema_fingerprint(definition)
        fingerprint = action_fingerprint(
            task_id=contract.task_id,
            planning_version=plan.planning_version,
            step_id=step.step_id,
            tool_name=definition.tool_name,
            tool_version=definition.tool_version,
            input_schema_fingerprint=schema_digest,
            controlled_scope=decision.controlled_scope,
            arguments=arguments,
        )
        existing = self._repository.get_pending_for_task(
            contract.task_id,
            tenant_id=contract.constraints.tenant_id,
        )
        for approval in existing:
            if approval.step_id != step.step_id:
                continue
            if approval.original_action_fingerprint != fingerprint:
                raise ApprovalStateConflictError("Pending approval action no longer matches")
            return approval
        now = self._clock()
        approval = ApprovalRequest(
            approval_id=self._ids.new_id("AP"),
            task_id=contract.task_id,
            tenant_id=contract.constraints.tenant_id,
            step_id=step.step_id,
            planning_version=plan.planning_version,
            tool_name=definition.tool_name,
            tool_version=definition.tool_version,
            input_schema_fingerprint=schema_digest,
            original_action_fingerprint=fingerprint,
            controlled_scope=decision.controlled_scope,
            editable_fields=decision.editable_fields,
            proposed_arguments=arguments,
            reason=decision.reason,
            requester=requester,
            required_role=decision.required_role,
            status=ApprovalStatus.PENDING,
            policy_version=decision.policy_id,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        self._repository.create(approval, tenant_id=approval.tenant_id)
        self._append_audit(
            approval,
            "APPROVAL_REQUESTED",
            trace_id=trace_id,
            status=ApprovalStatus.PENDING.value,
        )
        return approval

    def _append_audit(
        self,
        approval: ApprovalRequest,
        event: str,
        *,
        trace_id: str,
        status: str,
    ) -> None:
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=approval.task_id,
                plan_id=(
                    "accounts-payable-analysis"
                    if _approval_purpose(approval) == "accounts_payable_analysis.v1"
                    else SUPPLIER_QUALITY_PLAN_ID
                ),
                plan_version=approval.planning_version,
                timestamp=self._clock(),
                tenant_id=approval.tenant_id,
                trace_id=trace_id,
                actor_id=approval.requester,
                approval_id=approval.approval_id,
                arguments_hash=arguments_fingerprint(approval.proposed_arguments),
                step_id=approval.step_id,
                tool_name=approval.tool_name,
                status=status,
                metadata=JsonObject(
                    {
                        "approval_id": approval.approval_id,
                        "actor_id": approval.requester,
                        "tenant_id": approval.tenant_id,
                        "trace_id": trace_id,
                        "decision": None,
                        "reason": approval.reason,
                        "original_arguments_hash": arguments_fingerprint(
                            approval.proposed_arguments
                        ),
                        "resolved_arguments_hash": None,
                        "outcome": status,
                    }
                ),
            )
        )


class ApprovalService:
    """Authorize, atomically resolve, audit, and resume one pending approval."""

    def __init__(
        self,
        *,
        repository: ApprovalRepositoryPort,
        engine: ApprovalWorkflowEngine,
        registry: ToolRegistry,
        audit_sink: WorkflowAuditSink,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        permission_matrix: PermissionMatrix | None = None,
        observability: ObservabilityPort | None = None,
    ) -> None:
        self._repository = repository
        self._engine = engine
        self._registry = registry
        self._audit_sink = audit_sink
        self._ids = ids
        self._clock = clock
        self._permission_matrix = permission_matrix or PermissionMatrix()
        self._observability = observability or NoopObservability()

    def get(
        self,
        task_id: str,
        approval_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> ApprovalRequest:
        """Return one tenant- and role-authorized approval view for an approval client."""
        approval = self._load(approval_id, tenant_id=caller.tenant_id)
        if approval.task_id != task_id:
            not_found_error = ApprovalNotFoundError(
                "Approval does not belong to the requested task"
            )
            self._append_audit(
                approval,
                not_found_error.code,
                trace_id=trace_id,
                caller=caller,
                error=not_found_error,
            )
            raise not_found_error
        if approval.tenant_id != caller.tenant_id:
            tenant_error = ApprovalPermissionDeniedError("Approval tenant does not match caller")
            self._append_audit(
                approval,
                tenant_error.code,
                trace_id=trace_id,
                caller=caller,
                error=tenant_error,
            )
            raise tenant_error
        self._authorize_approval_permission(approval, caller, trace_id=trace_id)
        if approval.required_role not in caller.roles:
            role_error = ApprovalPermissionDeniedError("Caller lacks the required approval role")
            self._append_audit(
                approval,
                role_error.code,
                trace_id=trace_id,
                caller=caller,
                error=role_error,
            )
            raise role_error
        return approval

    def resolve(
        self,
        command: ApprovalResolutionCommand,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> ApprovalResolutionResult:
        """Resolve one pending decision and resume only through the normal graph path."""
        pending = self._load(command.approval_id, tenant_id=caller.tenant_id)
        if pending.status is not ApprovalStatus.PENDING:
            resolved_error = ApprovalAlreadyResolvedError("Approval has already been resolved")
            self._append_audit(
                pending,
                resolved_error.code,
                trace_id=trace_id,
                caller=caller,
                error=resolved_error,
            )
            raise resolved_error
        if pending.task_id != command.task_id:
            task_error = ApprovalNotFoundError("Approval does not belong to the requested task")
            self._append_audit(
                pending,
                task_error.code,
                trace_id=trace_id,
                caller=caller,
                error=task_error,
            )
            raise task_error
        if pending.tenant_id != caller.tenant_id:
            tenant_error = ApprovalPermissionDeniedError("Approval tenant does not match caller")
            self._append_audit(
                pending,
                tenant_error.code,
                trace_id=trace_id,
                caller=caller,
                error=tenant_error,
            )
            raise tenant_error
        self._authorize_approval_permission(pending, caller, trace_id=trace_id)
        try:
            state = self._engine.approval_state(command.task_id, caller.tenant_id)
            trace_id = str(state["trace_id"])
            self._validate_authority(pending, caller, state)
        except ApprovalPermissionDeniedError as exc:
            self._append_audit(
                pending,
                exc.code,
                trace_id=trace_id,
                caller=caller,
                error=exc,
            )
            raise
        except (ApprovalStateConflictError, ValueError) as exc:
            state_error = (
                exc
                if isinstance(exc, ApprovalStateConflictError)
                else ApprovalStateConflictError("Approval checkpoint is unavailable")
            )
            self._append_audit(
                pending,
                state_error.code,
                trace_id=trace_id,
                caller=caller,
                error=state_error,
            )
            if state_error is exc:
                raise
            raise state_error from exc
        now = self._clock()
        if pending.expires_at <= now:
            expired = pending.model_copy(
                update={
                    "status": ApprovalStatus.EXPIRED,
                    "decided_at": now,
                    "version": pending.version + 1,
                }
            )
            self._resolve_once(pending, expired)
            expired_error = ApprovalExpiredError("Approval has expired")
            self._append_audit(
                expired,
                expired_error.code,
                trace_id=trace_id,
                caller=caller,
                error=expired_error,
            )
            self._engine.resume_approval(expired, caller.tenant_id)
            raise expired_error
        try:
            resolved = self._decision(pending, command, caller.user_id, now)
        except ApprovalArgumentsInvalidError as exc:
            self._append_audit(
                pending,
                exc.code,
                trace_id=trace_id,
                caller=caller,
                error=exc,
            )
            raise
        except ApprovalStateConflictError as exc:
            self._append_audit(
                pending,
                exc.code,
                trace_id=trace_id,
                caller=caller,
                error=exc,
            )
            raise
        try:
            self._resolve_once(pending, resolved)
        except ApprovalStateConflictError as exc:
            self._append_audit(
                pending,
                exc.code,
                trace_id=trace_id,
                caller=caller,
                error=exc,
            )
            raise
        event = {
            ApprovalResolutionAction.APPROVE: "APPROVAL_APPROVED",
            ApprovalResolutionAction.EDIT: "APPROVAL_EDITED",
            ApprovalResolutionAction.REJECT: "APPROVAL_REJECTED",
        }[command.action]
        self._append_audit(resolved, event, trace_id=trace_id, caller=caller)
        with self._observability.bind_context(
            task_id=command.task_id,
            trace_id=trace_id,
            tenant_id=caller.tenant_id,
            user_id=caller.user_id,
        ):
            if resolved.status is ApprovalStatus.APPROVED:
                self._observability.increment("approvals_approved_total")
                self._observability.emit(
                    EventName.APPROVAL_APPROVED,
                    fields={"approval_status": resolved.status.value},
                )
            else:
                self._observability.increment("approvals_rejected_total")
                self._observability.emit(
                    EventName.APPROVAL_REJECTED,
                    fields={"approval_status": resolved.status.value},
                )
        self._append_audit(
            resolved,
            "APPROVAL_RESUME_STARTED",
            trace_id=trace_id,
            caller=caller,
        )
        try:
            execution = self._engine.resume_approval(resolved, caller.tenant_id)
        except Exception:
            resume_error = ApprovalServiceError("Approval workflow resume failed")
            self._append_audit(
                resolved,
                "APPROVAL_RESUME_FAILED",
                trace_id=trace_id,
                caller=caller,
                error=resume_error,
            )
            raise
        self._append_audit(
            resolved,
            "APPROVAL_RESUME_SUCCEEDED",
            trace_id=trace_id,
            caller=caller,
        )
        latest = self._engine.approval_state(command.task_id, caller.tenant_id)
        return ApprovalResolutionResult(
            approval=resolved,
            task_status=TaskStatus(str(latest["task_status"])),
            trace_id=str(latest["trace_id"]),
            execution=execution,
        )

    def _authorize_approval_permission(
        self,
        approval: ApprovalRequest,
        caller: TrustedCallerContext,
        *,
        trace_id: str,
    ) -> None:
        purpose = _approval_purpose(approval)
        decision = self._permission_matrix.evaluate(
            AuthorizationRequest(
                action=Permission.APPROVE_ACTION,
                roles=caller.roles,
                resource_type="approval",
                resource_name=approval.approval_id,
                task_id=approval.task_id,
                purpose=purpose,
                scopes=caller.scopes,
                is_demo_identity=caller.is_demo_identity,
            )
        )
        task_type_allowed = purpose in {task_type.value for task_type in caller.allowed_task_types}
        if not task_type_allowed or not decision.allowed:
            error = ApprovalPermissionDeniedError("Caller lacks approval permission")
            self._append_audit(
                approval,
                decision.reason_code if task_type_allowed else "APPROVAL_PERMISSION_DENIED",
                trace_id=trace_id,
                caller=caller,
                error=error,
            )
            raise error

    def _load(self, approval_id: str, *, tenant_id: str) -> ApprovalRequest:
        try:
            return self._repository.get(approval_id, tenant_id=tenant_id)
        except KeyError as exc:
            raise ApprovalNotFoundError("Approval was not found") from exc

    @staticmethod
    def _validate_authority(
        pending: ApprovalRequest,
        caller: TrustedCallerContext,
        state: dict[str, object],
    ) -> None:
        if str(state["tenant_id"]) != caller.tenant_id:
            raise ApprovalPermissionDeniedError("Approval tenant does not match caller")
        if pending.required_role not in caller.roles:
            raise ApprovalPermissionDeniedError("Caller lacks the required approval role")
        if state["task_status"] != TaskStatus.WAITING_APPROVAL.value:
            raise ApprovalStateConflictError("Task is not waiting for approval")
        if state["approval_id"] != pending.approval_id:
            raise ApprovalStateConflictError("Checkpoint is waiting for another approval")
        if state["step_id"] != pending.step_id:
            raise ApprovalStateConflictError("Approval does not match the checkpoint step")
        if state["planning_version"] != pending.planning_version:
            raise ApprovalStateConflictError("Approval plan version is stale")
        if bool(state["target_executed"]):
            raise ApprovalStateConflictError("Target tool has already been called")

    def _decision(
        self,
        pending: ApprovalRequest,
        command: ApprovalResolutionCommand,
        approver: str,
        now: datetime,
    ) -> ApprovalRequest:
        if command.action is ApprovalResolutionAction.APPROVE:
            if command.edited_arguments is not None:
                raise ApprovalArgumentsInvalidError("APPROVE cannot include edited arguments")
            return pending.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "resolution_action": ApprovalResolutionAction.APPROVE,
                    "resolved_arguments": pending.proposed_arguments,
                    "resolved_action_fingerprint": pending.original_action_fingerprint,
                    "resolution_reason": command.reason,
                    "approver": approver,
                    "decided_at": now,
                    "version": pending.version + 1,
                }
            )
        if command.action is ApprovalResolutionAction.REJECT:
            if command.edited_arguments is not None:
                raise ApprovalArgumentsInvalidError("REJECT cannot include edited arguments")
            if not command.reason or not command.reason.strip():
                raise ApprovalArgumentsInvalidError("REJECT requires a reason")
            return pending.model_copy(
                update={
                    "status": ApprovalStatus.REJECTED,
                    "resolution_action": ApprovalResolutionAction.REJECT,
                    "resolution_reason": command.reason.strip(),
                    "approver": approver,
                    "decided_at": now,
                    "version": pending.version + 1,
                }
            )
        if command.edited_arguments is None:
            raise ApprovalArgumentsInvalidError("EDIT requires complete edited arguments")
        if not command.reason or not command.reason.strip():
            raise ApprovalArgumentsInvalidError("EDIT requires a reason")
        try:
            definition = self._registry.get_version(
                pending.tool_name, pending.tool_version
            ).definition
        except ToolRuntimeError as exc:
            raise ApprovalStateConflictError("Registered approval tool is unavailable") from exc
        if (
            definition.tool_version != pending.tool_version
            or schema_fingerprint(definition) != pending.input_schema_fingerprint
        ):
            raise ApprovalStateConflictError("Registered tool or input schema changed")
        try:
            validate_payload(
                command.edited_arguments, definition.input_schema.root, "edited arguments"
            )
        except ToolValidationError as exc:
            raise ApprovalArgumentsInvalidError(
                "Edited arguments failed the bound tool schema"
            ) from exc
        changed = changed_top_level_fields(pending.proposed_arguments, command.edited_arguments)
        if not changed or not changed.issubset(pending.editable_fields):
            raise ApprovalArgumentsInvalidError("Edited arguments changed a non-editable field")
        self._validate_narrowing(pending, command.edited_arguments, changed)
        fingerprint = action_fingerprint(
            task_id=pending.task_id,
            planning_version=pending.planning_version,
            step_id=pending.step_id,
            tool_name=pending.tool_name,
            tool_version=pending.tool_version,
            input_schema_fingerprint=pending.input_schema_fingerprint,
            controlled_scope=pending.controlled_scope,
            arguments=command.edited_arguments,
        )
        return pending.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "resolution_action": ApprovalResolutionAction.EDIT,
                "resolved_arguments": command.edited_arguments,
                "resolved_action_fingerprint": fingerprint,
                "resolution_reason": command.reason.strip(),
                "approver": approver,
                "decided_at": now,
                "version": pending.version + 1,
            }
        )

    @staticmethod
    def _validate_narrowing(
        pending: ApprovalRequest,
        replacement: JsonObject,
        changed: frozenset[str],
    ) -> None:
        allowed = {"knowledge_search": "top_k", "database_query": "row_limit"}
        expected = allowed.get(pending.tool_name)
        if expected is None or changed != {expected}:
            raise ApprovalArgumentsInvalidError("v1.1 edit allowlist was violated")
        original_value = pending.proposed_arguments.root.get(expected)
        replacement_value = replacement.root.get(expected)
        if (
            not isinstance(original_value, int)
            or isinstance(original_value, bool)
            or not isinstance(replacement_value, int)
            or isinstance(replacement_value, bool)
            or replacement_value >= original_value
        ):
            raise ApprovalArgumentsInvalidError("Editable limit may only be reduced")

    def _resolve_once(self, pending: ApprovalRequest, resolved: ApprovalRequest) -> None:
        try:
            self._repository.resolve(
                pending,
                resolved,
                tenant_id=pending.tenant_id,
            )
        except ValueError as exc:
            raise ApprovalStateConflictError("Approval resolution lost a concurrency race") from exc

    def _append_audit(
        self,
        approval: ApprovalRequest,
        event: str,
        *,
        trace_id: str,
        caller: TrustedCallerContext | None = None,
        error: ApprovalServiceError | None = None,
    ) -> None:
        actor_id = caller.user_id if caller is not None else approval.approver or approval.requester
        reason = (
            redact_text(str(error))
            if error is not None
            else (approval.resolution_reason or approval.reason)
        )
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=approval.task_id,
                plan_id=SUPPLIER_QUALITY_PLAN_ID,
                plan_version=approval.planning_version,
                timestamp=self._clock(),
                tenant_id=approval.tenant_id,
                trace_id=trace_id,
                actor_id=actor_id,
                scopes=caller.scopes if caller is not None else (),
                approval_id=approval.approval_id,
                arguments_hash=arguments_fingerprint(
                    approval.resolved_arguments or approval.proposed_arguments
                ),
                step_id=approval.step_id,
                tool_name=approval.tool_name,
                status=approval.status.value,
                error_type=_approval_error_type(error) if error is not None else None,
                error_code=error.code if error is not None else None,
                failure_reason=reason if error is not None else None,
                metadata=JsonObject(
                    {
                        "approval_id": approval.approval_id,
                        "actor_id": actor_id,
                        "tenant_id": approval.tenant_id,
                        "trace_id": trace_id,
                        "resolution_action": (
                            approval.resolution_action.value
                            if approval.resolution_action is not None
                            else None
                        ),
                        "decision": (
                            approval.resolution_action.value
                            if approval.resolution_action is not None
                            else None
                        ),
                        "reason": reason,
                        "error_code": error.code if error is not None else None,
                        "failure_reason": reason if error is not None else None,
                        "original_arguments_hash": arguments_fingerprint(
                            approval.proposed_arguments
                        ),
                        "resolved_arguments_hash": (
                            arguments_fingerprint(approval.resolved_arguments)
                            if approval.resolved_arguments is not None
                            else None
                        ),
                        "resolved_action_fingerprint": approval.resolved_action_fingerprint,
                        "outcome": event,
                    }
                ),
            )
        )


def _approval_error_type(error: ApprovalServiceError) -> str:
    if isinstance(error, ApprovalPermissionDeniedError):
        return "PERMISSION"
    if isinstance(error, (ApprovalNotFoundError, ApprovalArgumentsInvalidError)):
        return "VALIDATION"
    if isinstance(
        error,
        (ApprovalAlreadyResolvedError, ApprovalStateConflictError, ApprovalExpiredError),
    ):
        return "BUSINESS"
    return "TECHNICAL"


def _approval_purpose(approval: ApprovalRequest) -> str:
    return (
        "accounts_payable_analysis.v1"
        if approval.required_role == "finance_approver"
        else "supplier_quality_analysis.v1"
    )


__all__ = [
    "ApprovalAlreadyResolvedError",
    "ApprovalArgumentsInvalidError",
    "ApprovalExpiredError",
    "ApprovalGateService",
    "ApprovalNotFoundError",
    "ApprovalPermissionDeniedError",
    "ApprovalRepositoryPort",
    "ApprovalResolutionCommand",
    "ApprovalResolutionResult",
    "ApprovalService",
    "ApprovalServiceError",
    "ApprovalStateConflictError",
    "ApprovalWorkflowEngine",
]
