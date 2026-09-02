"""Application service for authorized clarification reads and asynchronous responses."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from copilot.contracts import (
    ClarificationInputType,
    ClarificationResponse,
    ClarificationStatus,
    JsonObject,
    TaskClarification,
    TaskDispatch,
    TaskRequest,
    TaskState,
    TaskStatus,
)
from copilot.contracts.async_runtime import CheckpointIdentity
from copilot.contracts.errors import DispatchConflictError
from copilot.policies.permissions import AuthorizationRequest, Permission, PermissionMatrix
from copilot.services.observability import EventName, NoopObservability, ObservabilityPort
from copilot.services.task_intake import (
    TaskIntakeValidationError,
    TrustedCallerContext,
    TrustedTaskContext,
    sanitize_metadata,
)
from copilot.services.task_submission import trusted_context_from_request
from copilot.services.workflows.models import TaskStateEvent, WorkflowAuditRecord
from copilot.services.workflows.ports import IdentifierFactory, WorkflowAuditSink
from copilot.services.workflows.state_machine import TaskStateMachine


class ClarificationRepositoryPort(Protocol):
    """Persistence operations shared by the Graph, API, and Worker."""

    def get(self, clarification_id: str, *, tenant_id: str) -> TaskClarification: ...

    def list_by_task(self, task_id: str, *, tenant_id: str) -> tuple[TaskClarification, ...]: ...

    def get_pending_for_task(self, task_id: str, *, tenant_id: str) -> TaskClarification | None: ...

    def get_submitted_for_task(
        self, task_id: str, *, tenant_id: str
    ) -> TaskClarification | None: ...

    def create_pending_and_transition(
        self,
        clarification: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> TaskClarification: ...

    def resolve_submitted(
        self, submitted: TaskClarification, resolved: TaskClarification
    ) -> None: ...

    def replace_submitted_with_pending(
        self,
        submitted: TaskClarification,
        resolved: TaskClarification,
        pending: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> None: ...

    def resolve_submitted_and_transition(
        self,
        submitted: TaskClarification,
        resolved: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> None: ...

    def submit_response_and_dispatch(
        self,
        pending: TaskClarification,
        submitted: TaskClarification,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
        dispatch: TaskDispatch,
    ) -> None: ...


class ClarificationTaskRepository(Protocol):
    def request_for(self, task_id: str, *, tenant_id: str) -> TaskRequest: ...

    def state_for(self, task_id: str, *, tenant_id: str) -> TaskState: ...


class ClarificationWorkflowEngine(Protocol):
    def clarification_state(self, task_id: str, tenant_id: str) -> dict[str, object]: ...

    def checkpoint_identity(self, task_id: str, tenant_id: str) -> CheckpointIdentity | None: ...


class ClarificationServiceError(RuntimeError):
    code = "CLARIFICATION_ERROR"
    status_code = 409


class ClarificationNotFoundError(ClarificationServiceError):
    code = "CLARIFICATION_NOT_FOUND"
    status_code = 404


class ClarificationPermissionDeniedError(ClarificationServiceError):
    code = "CLARIFICATION_ACCESS_DENIED"
    status_code = 403


class ClarificationAlreadyResolvedError(ClarificationServiceError):
    code = "CLARIFICATION_ALREADY_RESOLVED"


class ClarificationStateConflictError(ClarificationServiceError):
    code = "CLARIFICATION_STALE"


class ClarificationInvalidResponseError(ClarificationServiceError):
    code = "CLARIFICATION_INPUT_INVALID"
    status_code = 422


@dataclass(frozen=True, slots=True)
class ClarificationSubmissionResult:
    clarification: TaskClarification
    task_status: TaskStatus
    trace_id: str
    dispatch_id: str
    reused: bool = False


class ClarificationService:
    """Authorize, validate, persist, and schedule one clarification response."""

    def __init__(
        self,
        *,
        repository: ClarificationRepositoryPort,
        tasks: ClarificationTaskRepository,
        engine: ClarificationWorkflowEngine,
        state_machine: TaskStateMachine,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        audit_sink: WorkflowAuditSink,
        permission_matrix: PermissionMatrix | None = None,
        observability: ObservabilityPort | None = None,
        max_response_metadata_bytes: int = 16_384,
        max_response_metadata_depth: int = 5,
        max_response_metadata_items: int = 100,
    ) -> None:
        self._repository = repository
        self._tasks = tasks
        self._engine = engine
        self._state_machine = state_machine
        self._ids = ids
        self._clock = clock
        self._audit_sink = audit_sink
        self._permission_matrix = permission_matrix or PermissionMatrix()
        self._observability = observability or NoopObservability()
        self._max_response_metadata_bytes = max_response_metadata_bytes
        self._max_response_metadata_depth = max_response_metadata_depth
        self._max_response_metadata_items = max_response_metadata_items

    def get(
        self,
        task_id: str,
        clarification_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> TaskClarification:
        """Return one interaction after task-owner and current-permission checks."""
        clarification = self._load(clarification_id, tenant_id=caller.tenant_id)
        request = self._authorize_task(
            task_id,
            clarification,
            caller,
            permission=Permission.READ_TASK,
        )
        del request
        self._audit("CLARIFICATION_VIEWED", clarification, caller, trace_id)
        return clarification

    def respond(
        self,
        task_id: str,
        clarification_id: str,
        response: ClarificationResponse,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> ClarificationSubmissionResult:
        """Persist a response and next dispatch without executing LangGraph inline."""
        clarification = self._load(clarification_id, tenant_id=caller.tenant_id)
        request = self._authorize_task(
            task_id,
            clarification,
            caller,
            permission=Permission.RESPOND_CLARIFICATION,
        )
        normalized = self._validate_response(clarification, response, caller)
        fingerprint = _response_fingerprint(normalized, caller.user_id)
        if clarification.status is ClarificationStatus.SUBMITTED:
            if (
                clarification.response_fingerprint == fingerprint
                and clarification.submitted_by == caller.user_id
            ):
                state = self._tasks.state_for(task_id, tenant_id=caller.tenant_id)
                checkpoint = self._engine.clarification_state(task_id, caller.tenant_id)
                return ClarificationSubmissionResult(
                    clarification=clarification,
                    task_status=state.state,
                    trace_id=str(checkpoint["trace_id"]),
                    dispatch_id=str(checkpoint.get("current_dispatch_id", "reused")),
                    reused=True,
                )
            raise ClarificationAlreadyResolvedError(
                "Clarification has already received a different response"
            )
        if clarification.status is not ClarificationStatus.PENDING:
            raise ClarificationAlreadyResolvedError("Clarification has already been resolved")
        state = self._tasks.state_for(task_id, tenant_id=caller.tenant_id)
        if state.state is not TaskStatus.WAITING_CLARIFICATION:
            raise ClarificationStateConflictError("Task is not waiting for clarification")
        checkpoint_state = self._engine.clarification_state(task_id, caller.tenant_id)
        identity = self._engine.checkpoint_identity(task_id, caller.tenant_id)
        if (
            identity is None
            or checkpoint_state.get("clarification_id") != clarification.clarification_id
            or checkpoint_state.get("clarification_round") != clarification.round
            or checkpoint_state.get("task_status") != TaskStatus.WAITING_CLARIFICATION.value
            or checkpoint_state.get("contract_present") is True
            or checkpoint_state.get("plan_present") is True
        ):
            raise ClarificationStateConflictError(
                "Clarification does not match the suspended checkpoint"
            )
        refreshed_context = _refresh_context(request, caller)
        now = self._clock()
        submitted = clarification.model_copy(
            update={
                "status": ClarificationStatus.SUBMITTED,
                "response": normalized,
                "response_fingerprint": fingerprint,
                "resume_context": JsonObject(refreshed_context.model_dump(mode="json")),
                "submitted_by": caller.user_id,
                "submitted_at": now,
                "version": clarification.version + 1,
            }
        )
        current, event = self._state_machine.transition(
            state,
            "CLARIFICATION_SUBMITTED",
            reason=f"Clarification {clarification.clarification_id} response accepted",
        )
        dispatch = TaskDispatch(
            tenant_id=caller.tenant_id,
            task_id=task_id,
            trace_id=str(checkpoint_state["trace_id"]),
            dispatch_id=self._ids.new_id("DISPATCH"),
            execution_generation=identity.execution_generation + 1,
            predecessor_execution_generation=identity.execution_generation,
            resume_checkpoint_id=identity.checkpoint_id,
            expected_task_version=current.version,
            enqueued_at=now,
            not_before=now,
        )
        try:
            self._repository.submit_response_and_dispatch(
                clarification,
                submitted,
                state,
                current,
                event,
                dispatch,
            )
        except (ValueError, DispatchConflictError) as exc:
            latest = self._load(clarification_id, tenant_id=caller.tenant_id)
            if (
                latest.status is ClarificationStatus.SUBMITTED
                and latest.response_fingerprint == fingerprint
                and latest.submitted_by == caller.user_id
            ):
                return ClarificationSubmissionResult(
                    clarification=latest,
                    task_status=TaskStatus.UNDERSTANDING,
                    trace_id=dispatch.trace_id,
                    dispatch_id=dispatch.dispatch_id,
                    reused=True,
                )
            raise ClarificationStateConflictError(
                "Clarification response lost a concurrency race"
            ) from exc
        self._audit("TASK_CLARIFICATION_SUBMITTED", submitted, caller, dispatch.trace_id)
        with self._observability.bind_context(
            task_id=task_id,
            trace_id=dispatch.trace_id,
            tenant_id=caller.tenant_id,
            user_id=caller.user_id,
        ):
            self._observability.increment("clarification_responses_total")
            self._observability.increment("clarification_response_count")
            self._observability.gauge_add("waiting_clarification_count", -1)
            self._observability.emit(
                EventName.CLARIFICATION_ANSWERED,
                fields={"clarification_round": clarification.round},
            )
        return ClarificationSubmissionResult(
            clarification=submitted,
            task_status=current.state,
            trace_id=dispatch.trace_id,
            dispatch_id=dispatch.dispatch_id,
        )

    def _load(self, clarification_id: str, *, tenant_id: str) -> TaskClarification:
        try:
            return self._repository.get(clarification_id, tenant_id=tenant_id)
        except KeyError as exc:
            raise ClarificationNotFoundError("Clarification was not found") from exc

    def _authorize_task(
        self,
        task_id: str,
        clarification: TaskClarification,
        caller: TrustedCallerContext,
        *,
        permission: Permission,
    ) -> TaskRequest:
        if clarification.task_id != task_id or clarification.tenant_id != caller.tenant_id:
            raise ClarificationNotFoundError("Clarification does not belong to the requested task")
        try:
            request = self._tasks.request_for(task_id, tenant_id=caller.tenant_id)
        except KeyError as exc:
            raise ClarificationNotFoundError("Task was not found") from exc
        original = trusted_context_from_request(request)
        decision = self._permission_matrix.evaluate(
            AuthorizationRequest(
                action=permission,
                roles=caller.roles,
                resource_type="clarification",
                resource_name=clarification.clarification_id,
                task_id=task_id,
                purpose=original.task_type.value,
                scopes=caller.scopes,
                is_demo_identity=caller.is_demo_identity,
            )
        )
        if (
            request.user_id != caller.user_id
            or original.task_type not in caller.allowed_task_types
            or not decision.allowed
        ):
            raise ClarificationPermissionDeniedError(
                "Caller is not permitted to respond to this clarification"
            )
        return request

    def _validate_response(
        self,
        clarification: TaskClarification,
        response: ClarificationResponse,
        caller: TrustedCallerContext,
    ) -> ClarificationResponse:
        try:
            answers = sanitize_metadata(
                dict(response.answers.root),
                max_bytes=self._max_response_metadata_bytes,
                max_depth=self._max_response_metadata_depth,
                max_items=self._max_response_metadata_items,
            )
        except TaskIntakeValidationError as exc:
            raise ClarificationInvalidResponseError(str(exc)) from exc
        questions = {question.field: question for question in clarification.questions}
        unknown = set(answers.root) - set(questions)
        if unknown:
            raise ClarificationInvalidResponseError(
                "Response contains a field that was not requested"
            )
        for field, value in answers.root.items():
            question = questions[field]
            _validate_answer_value(question.input_type, value)
            if question.allowed_values:
                values = value if isinstance(value, list) else [value]
                if any(item not in question.allowed_values for item in values):
                    raise ClarificationInvalidResponseError(
                        "Response contains a value outside the allowed scope"
                    )
                if field == "legal_entity_ids" and any(
                    item not in caller.legal_entity_ids for item in values
                ):
                    raise ClarificationPermissionDeniedError(
                        "Legal entity is outside the caller's current scope"
                    )
        message = response.message
        if message is not None:
            for character in message:
                if character in {"\n", "\r", "\t"}:
                    continue
                if character == "\x00" or unicodedata.category(character) in {"Cc", "Cs"}:
                    raise ClarificationInvalidResponseError(
                        "Clarification message contains a disallowed control character"
                    )
        return ClarificationResponse(answers=answers, message=message)

    def _audit(
        self,
        event: str,
        clarification: TaskClarification,
        caller: TrustedCallerContext,
        trace_id: str,
    ) -> None:
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=clarification.task_id,
                plan_id="interactive-clarification",
                plan_version=clarification.round,
                timestamp=self._clock(),
                tenant_id=clarification.tenant_id,
                trace_id=trace_id or "TRACE-UNAVAILABLE",
                actor_id=caller.user_id,
                status=clarification.status.value,
                metadata=JsonObject(
                    {
                        "clarification_id": clarification.clarification_id,
                        "round": clarification.round,
                        "question_fields": [q.field for q in clarification.questions],
                    }
                ),
            )
        )


def _refresh_context(
    request: TaskRequest,
    caller: TrustedCallerContext,
) -> TrustedTaskContext:
    original = trusted_context_from_request(request)
    if original.task_type not in caller.allowed_task_types:
        raise ClarificationPermissionDeniedError("Task type is no longer authorized")
    return original.model_copy(
        update={
            "data_scope": caller.data_scope,
            "authorized_supplier_ids": caller.supplier_ids,
            "authorized_legal_entity_ids": caller.legal_entity_ids,
            "authorized_business_unit_ids": caller.business_unit_ids,
            "authorized_currency_scope": caller.currency_scope,
            "policy_rule_set_id": caller.policy_rule_set_id,
            "policy_rule_set_version": caller.policy_rule_set_version,
            "policy_manifest_checksum": caller.policy_manifest_checksum,
            "policy_materiality": caller.policy_materiality,
            "policy_snapshot_at": caller.policy_snapshot_at,
            "roles": caller.roles,
            "scopes": caller.scopes,
            "authentication_source": caller.authentication_source,
            "authenticated": caller.authenticated,
            "is_demo_identity": caller.is_demo_identity,
            "purpose": original.task_type.value,
            "read_only": original.read_only or caller.policy_forces_read_only,
            "require_approval": original.require_approval or caller.policy_requires_approval,
        }
    )


def _validate_answer_value(input_type: ClarificationInputType, value: object) -> None:
    try:
        if input_type is ClarificationInputType.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise ValueError
        elif input_type is ClarificationInputType.DATE:
            if not isinstance(value, str):
                raise ValueError
            date.fromisoformat(value)
        elif input_type is ClarificationInputType.DATE_RANGE:
            if not isinstance(value, dict):
                raise ValueError
            start = value.get("start_date")
            end = value.get("end_date")
            if not isinstance(start, str) or not isinstance(end, str):
                raise ValueError
            if date.fromisoformat(end) < date.fromisoformat(start):
                raise ValueError
        elif input_type is ClarificationInputType.SINGLE_SELECT:
            if not isinstance(value, str):
                raise ValueError
        elif (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ClarificationInvalidResponseError(
            "Response value does not match the requested input type"
        ) from exc


def _response_fingerprint(response: ClarificationResponse, user_id: str) -> str:
    encoded = json.dumps(
        {"response": response.model_dump(mode="json"), "submitted_by": user_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ClarificationAlreadyResolvedError",
    "ClarificationInvalidResponseError",
    "ClarificationNotFoundError",
    "ClarificationPermissionDeniedError",
    "ClarificationRepositoryPort",
    "ClarificationService",
    "ClarificationServiceError",
    "ClarificationStateConflictError",
    "ClarificationSubmissionResult",
]
