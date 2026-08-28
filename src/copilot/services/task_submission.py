"""Acceptance-only asynchronous Task submission application service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from copilot.contracts import TaskRequest
from copilot.contracts.async_runtime import (
    RuntimeStatus,
    SubmissionIdempotency,
    TaskDispatch,
    TaskSubmissionResponse,
)
from copilot.contracts.errors import DispatchConflictError, RuntimeCapacityError
from copilot.services.async_runtime import TaskSubmissionRepository
from copilot.services.observability import EventName, NoopObservability, ObservabilityPort
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    TrustedCallerContext,
    TrustedTaskContext,
)
from copilot.services.task_service import NaturalLanguageTaskService, TaskServiceError
from copilot.services.workflows.ports import IdentifierFactory
from copilot.services.workflows.state_machine import TaskStateMachine


class TaskSubmissionConflictError(TaskServiceError):
    """Raised when an Idempotency-Key is bound to different normalized content."""

    def __init__(self) -> None:
        super().__init__(
            "IDEMPOTENCY_CONFLICT",
            "Idempotency-Key is already bound to a different task submission.",
            status_code=409,
            task_id=None,
        )


class TaskSubmissionBackpressureError(TaskServiceError):
    """Raised when configured durable backlog limits reject new acceptance."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__(
            "TASK_QUEUE_CAPACITY_EXCEEDED",
            "Task acceptance is temporarily at capacity; retry later.",
            status_code=503,
            task_id=None,
        )
        self.retry_after_seconds = retry_after_seconds


class TaskSubmissionService:
    """Validate intake and atomically persist a Task plus initial dispatch intent."""

    def __init__(
        self,
        *,
        intake: NaturalLanguageTaskService,
        repository: TaskSubmissionRepository,
        state_machine: TaskStateMachine,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        observability: ObservabilityPort | None = None,
    ) -> None:
        self._intake = intake
        self._repository = repository
        self._state_machine = state_machine
        self._ids = ids
        self._clock = clock
        self._observability = observability or NoopObservability()

    def submit(
        self,
        command: NaturalLanguageTaskCommand,
        caller: TrustedCallerContext,
        *,
        idempotency_key: str | None,
    ) -> TaskSubmissionResponse:
        """Commit the authoritative Task and PENDING dispatch without running the Graph."""
        request, context = self._intake.prepare(command, caller)
        state = self._state_machine.initial(context.task_id)
        accepted_at = self._clock()
        dispatch = TaskDispatch(
            tenant_id=context.tenant_id,
            task_id=context.task_id,
            trace_id=context.trace_id,
            dispatch_id=self._ids.new_id("DISPATCH"),
            execution_generation=1,
            expected_task_version=state.version,
            enqueued_at=accepted_at,
            not_before=accepted_at,
        )
        response = TaskSubmissionResponse(
            task_id=context.task_id,
            trace_id=context.trace_id,
            task_status=state.state,
            runtime_status=RuntimeStatus.READY,
            accepted_at=accepted_at,
            status_url=f"/v1/tasks/{context.task_id}",
            artifacts_url=f"/v1/tasks/{context.task_id}/artifacts",
        )
        idempotency = (
            SubmissionIdempotency(
                tenant_id=context.tenant_id,
                caller_id=context.user_id,
                idempotency_key=_validate_idempotency_key(idempotency_key),
                request_fingerprint=_submission_fingerprint(command, request, context),
            )
            if idempotency_key is not None
            else None
        )
        try:
            persisted, reused = self._repository.persist_task_and_dispatch(
                request,
                state,
                dispatch,
                response,
                idempotency=idempotency,
            )
        except DispatchConflictError as exc:
            raise TaskSubmissionConflictError() from exc
        except RuntimeCapacityError as exc:
            raise TaskSubmissionBackpressureError(
                retry_after_seconds=exc.retry_after_seconds
            ) from exc
        with self._observability.bind_context(
            task_id=persisted.task_id,
            trace_id=persisted.trace_id,
            request_id=request.id,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=context.session_id,
        ):
            self._observability.emit(
                EventName.TASK_CREATED,
                fields={
                    "status": persisted.task_status.value,
                    "runtime_status": persisted.runtime_status.value,
                    "idempotency_reused": reused,
                },
            )
        return persisted


def trusted_context_from_request(request: TaskRequest) -> TrustedTaskContext:
    """Load the protected execution context stored by trusted intake, never by the Queue."""
    payload = request.metadata.root.get("_runtime_context")
    if not isinstance(payload, dict):
        raise ValueError("trusted runtime context is missing from the authoritative Task")
    context = TrustedTaskContext.model_validate(payload)
    if context.user_id != request.user_id:
        raise ValueError("trusted runtime context caller does not match the Task requester")
    return context


def _submission_fingerprint(
    command: NaturalLanguageTaskCommand,
    request: TaskRequest,
    context: TrustedTaskContext,
) -> str:
    payload = {
        "task": request.raw_input,
        "task_type": context.task_type.value,
        "output_format": context.output_format.value if context.output_format else None,
        "max_steps": context.max_steps,
        "read_only": context.read_only,
        "require_approval": context.require_approval,
        "session_id": command.session_id,
        "metadata": command.metadata,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise TaskServiceError(
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must be a non-empty value of at most 200 characters.",
            status_code=422,
            task_id=None,
        )
    return normalized


__all__ = [
    "TaskSubmissionConflictError",
    "TaskSubmissionBackpressureError",
    "TaskSubmissionService",
    "trusted_context_from_request",
]
