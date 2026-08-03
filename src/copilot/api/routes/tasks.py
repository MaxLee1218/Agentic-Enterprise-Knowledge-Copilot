"""Natural-language task HTTP route."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from copilot.agent.graph import WorkflowInterrupted
from copilot.api.dependencies import get_caller_context, get_task_service
from copilot.api.mappers import task_evidence_response, task_response, task_step_response
from copilot.api.schemas.tasks import (
    NaturalLanguageTaskSubmission,
    TaskArtifactResponse,
    TaskErrorResponse,
    TaskEvidenceListResponse,
    TaskFailureResponse,
    TaskResponse,
    TaskStepsResponse,
    TaskSubmissionResponse,
)
from copilot.contracts import TaskStatus
from copilot.services.artifact_service import safe_artifact_filename
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)
from copilot.services.task_service import NaturalLanguageTaskService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.post(
    "",
    response_model=TaskSubmissionResponse,
    status_code=201,
    operation_id="create_task",
    responses={
        202: {"model": TaskSubmissionResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
        503: {"model": TaskErrorResponse},
        504: {"model": TaskErrorResponse},
    },
)
def submit_task(
    submission: NaturalLanguageTaskSubmission,
    request: Request,
    response: Response,
    service: Annotated[NaturalLanguageTaskService, Depends(get_task_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> TaskSubmissionResponse:
    """Submit one unmodified natural-language task through the shared application service."""
    try:
        execution = service.submit(
            NaturalLanguageTaskCommand(
                task=submission.task,
                output_format=submission.output_format,
                max_steps=submission.max_steps,
                read_only=submission.read_only,
                require_approval=submission.require_approval,
                session_id=submission.session_id,
                metadata=submission.metadata,
                source=RequestSource.API,
                trace_id=str(request.state.trace_id),
            ),
            caller,
        )
    except WorkflowInterrupted as interrupted:
        response.status_code = 202
        return TaskSubmissionResponse(
            task_id=interrupted.task_id,
            trace_id=interrupted.trace_id,
            status=TaskStatus(interrupted.status),
            created_at=interrupted.created_at or datetime.now(UTC),
            started_at=interrupted.created_at,
            summary=str(interrupted),
            pending_approval_id=interrupted.approval_id,
        )
    missing = tuple(
        error.message
        for error in execution.errors
        if error.error_code == "TASK_INFORMATION_MISSING"
    )
    return TaskSubmissionResponse(
        task_id=execution.task_result.task_id,
        trace_id=execution.trace_id,
        status=execution.task_result.final_status,
        created_at=execution.started_at,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        summary=execution.task_result.summary,
        artifacts=tuple(
            TaskArtifactResponse(
                artifact_id=artifact.artifact_id,
                type=artifact.type,
                filename=safe_artifact_filename(
                    artifact.location.rsplit("/", maxsplit=1)[-1], artifact
                ),
                media_type=artifact.media_type,
                checksum=artifact.checksum,
                size_bytes=artifact.size_bytes,
                created_at=artifact.created_at,
            )
            for artifact in execution.artifacts
        ),
        errors=tuple(
            TaskFailureResponse(
                error_code=error.error_code,
                message=error.message,
                recoverable=error.recoverable,
            )
            for error in execution.errors
        ),
        missing_information=missing,
        clarification_questions=missing,
    )


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    operation_id="get_task",
    responses={
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def get_task(
    task_id: str,
    request: Request,
    service: Annotated[NaturalLanguageTaskService, Depends(get_task_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> TaskResponse:
    """Return an authorized task summary through the application service."""
    view = service.get_task(
        task_id,
        caller,
        trace_id=str(request.state.trace_id),
    )
    return task_response(view)


@router.get(
    "/{task_id}/steps",
    response_model=TaskStepsResponse,
    operation_id="list_task_steps",
    responses={
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def list_task_steps(
    task_id: str,
    request: Request,
    service: Annotated[NaturalLanguageTaskService, Depends(get_task_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> TaskStepsResponse:
    """Return planned steps combined with already persisted results."""
    views = service.list_task_steps(task_id, caller, trace_id=str(request.state.trace_id))
    return TaskStepsResponse(
        task_id=task_id,
        steps=tuple(task_step_response(view) for view in views),
    )


@router.get(
    "/{task_id}/evidence",
    response_model=TaskEvidenceListResponse,
    operation_id="list_task_evidence",
    responses={
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def list_task_evidence(
    task_id: str,
    request: Request,
    service: Annotated[NaturalLanguageTaskService, Depends(get_task_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> TaskEvidenceListResponse:
    """Return only persisted, minimized Evidence metadata and lineage."""
    views = service.list_task_evidence(task_id, caller, trace_id=str(request.state.trace_id))
    return TaskEvidenceListResponse(
        task_id=task_id,
        evidence=tuple(task_evidence_response(view) for view in views),
    )


@router.post(
    "/{task_id}/cancel",
    response_model=TaskResponse,
    operation_id="cancel_task",
    responses={
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        409: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def cancel_task(
    task_id: str,
    request: Request,
    service: Annotated[NaturalLanguageTaskService, Depends(get_task_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> TaskResponse:
    """Request cooperative cancellation through the frozen domain state machine."""
    view = service.cancel_task(task_id, caller, trace_id=str(request.state.trace_id))
    return task_response(view)


__all__ = [
    "cancel_task",
    "get_task",
    "list_task_evidence",
    "list_task_steps",
    "router",
    "submit_task",
]
