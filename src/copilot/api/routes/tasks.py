"""Natural-language task HTTP route."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from copilot.agent.graph import WorkflowInterrupted
from copilot.api.dependencies import get_caller_context, get_task_service
from copilot.api.schemas.tasks import (
    NaturalLanguageTaskSubmission,
    TaskArtifactResponse,
    TaskFailureResponse,
    TaskSubmissionResponse,
)
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)
from copilot.services.task_service import NaturalLanguageTaskService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskSubmissionResponse, status_code=201)
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
            status=interrupted.status,
            created_at=interrupted.created_at or datetime.now(UTC),
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
        status=execution.task_result.final_status.value,
        created_at=execution.started_at,
        summary=execution.task_result.summary,
        artifacts=tuple(
            TaskArtifactResponse(
                artifact_id=artifact.artifact_id,
                type=artifact.type.value,
                location=artifact.location,
                checksum=artifact.checksum,
                size_bytes=artifact.size_bytes,
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


__all__ = ["router", "submit_task"]
