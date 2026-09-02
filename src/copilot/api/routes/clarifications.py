"""Thin HTTP adapter for persisted interactive clarifications."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from copilot.api.dependencies import get_caller_context, get_clarification_service
from copilot.api.schemas.clarifications import (
    ClarificationDetailResponse,
    ClarificationQuestionResponse,
    ClarificationSubmissionRequest,
    ClarificationSubmissionResponse,
)
from copilot.api.schemas.tasks import TaskErrorResponse
from copilot.contracts import (
    ClarificationResponse,
    JsonObject,
    RuntimeStatus,
    TaskClarification,
)
from copilot.services.clarification_service import ClarificationService
from copilot.services.task_intake import TrustedCallerContext

router = APIRouter(prefix="/v1/tasks", tags=["clarifications"])


def _detail(clarification: TaskClarification) -> ClarificationDetailResponse:
    return ClarificationDetailResponse(
        clarification_id=clarification.clarification_id,
        task_id=clarification.task_id,
        status=clarification.status,
        round=clarification.round,
        questions=tuple(
            ClarificationQuestionResponse(
                field=question.field,
                reason=question.reason,
                prompt=question.prompt,
                input_type=question.input_type,
                required=question.required,
                allowed_values=question.allowed_values,
                constraints=dict(question.constraints.root),
            )
            for question in clarification.questions
        ),
        created_at=clarification.created_at,
        submitted_at=clarification.submitted_at,
        resolved_at=clarification.resolved_at,
    )


@router.get(
    "/{task_id}/clarifications/{clarification_id}",
    response_model=ClarificationDetailResponse,
    operation_id="get_task_clarification",
    responses={
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        409: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
    },
)
def get_clarification(
    task_id: str,
    clarification_id: str,
    request: Request,
    service: Annotated[ClarificationService, Depends(get_clarification_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> ClarificationDetailResponse:
    item = service.get(
        task_id,
        clarification_id,
        caller,
        trace_id=str(request.state.trace_id),
    )
    return _detail(item)


@router.post(
    "/{task_id}/clarifications/{clarification_id}",
    response_model=ClarificationSubmissionResponse,
    status_code=202,
    operation_id="submit_task_clarification",
    responses={
        202: {"model": ClarificationSubmissionResponse},
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        409: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
    },
)
def submit_clarification(
    task_id: str,
    clarification_id: str,
    submission: ClarificationSubmissionRequest,
    request: Request,
    service: Annotated[ClarificationService, Depends(get_clarification_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> ClarificationSubmissionResponse:
    result = service.respond(
        task_id,
        clarification_id,
        ClarificationResponse(
            answers=JsonObject(submission.answers),
            message=submission.message,
        ),
        caller,
        trace_id=str(request.state.trace_id),
    )
    accepted_at = result.clarification.submitted_at
    assert accepted_at is not None
    return ClarificationSubmissionResponse(
        clarification_id=result.clarification.clarification_id,
        clarification_status=result.clarification.status,
        task_id=result.clarification.task_id,
        task_status=result.task_status,
        runtime_status=RuntimeStatus.READY,
        status_url=f"/v1/tasks/{result.clarification.task_id}",
        accepted_at=accepted_at,
        trace_id=result.trace_id,
        reused=result.reused,
    )


__all__ = ["get_clarification", "router", "submit_clarification"]
