"""Natural-language task HTTP route."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from copilot.api.dependencies import (
    get_caller_context,
    get_task_service,
    get_task_submission_service,
)
from copilot.api.mappers import (
    task_detail_response,
    task_evidence_response,
    task_list_item_response,
    task_response,
    task_step_response,
)
from copilot.api.schemas.tasks import (
    NaturalLanguageTaskSubmission,
    TaskDetailResponse,
    TaskErrorResponse,
    TaskEvidenceListResponse,
    TaskListResponse,
    TaskResponse,
    TaskStepsResponse,
    TaskSubmissionResponse,
)
from copilot.contracts import TaskStatus
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)
from copilot.services.task_service import NaturalLanguageTaskService
from copilot.services.task_submission import TaskSubmissionService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.get(
    "",
    response_model=TaskListResponse,
    operation_id="list_tasks",
    responses={
        403: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def list_tasks(
    service: Annotated[NaturalLanguageTaskService, Depends(get_task_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
    status: TaskStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskListResponse:
    """Return one bounded tenant- and owner-scoped task history page."""
    page = service.list_tasks(caller, status=status, limit=limit, offset=offset)
    return TaskListResponse(
        items=tuple(task_list_item_response(item) for item in page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=TaskSubmissionResponse,
    status_code=202,
    operation_id="create_task",
    responses={
        202: {"model": TaskSubmissionResponse},
        409: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
        503: {"model": TaskErrorResponse},
        504: {"model": TaskErrorResponse},
    },
)
def submit_task(
    submission: NaturalLanguageTaskSubmission,
    request: Request,
    service: Annotated[TaskSubmissionService, Depends(get_task_submission_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskSubmissionResponse:
    """Durably accept one Task without executing LangGraph in the API process."""
    return service.submit(
        NaturalLanguageTaskCommand(
            task=submission.task,
            task_type=submission.task_type,
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
        idempotency_key=idempotency_key,
    )


@router.get(
    "/{task_id}",
    response_model=TaskDetailResponse,
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
) -> TaskDetailResponse:
    """Return authorized task detail and its refresh-safe interaction projection."""
    view = service.get_task(
        task_id,
        caller,
        trace_id=str(request.state.trace_id),
    )
    return task_detail_response(view)


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
    status_code=202,
    operation_id="cancel_task",
    responses={
        202: {"model": TaskResponse},
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
    """Durably accept cancellation; underlying I/O may stop at a later safe boundary."""
    view = service.cancel_task(task_id, caller, trace_id=str(request.state.trace_id))
    return task_response(view)


__all__ = [
    "cancel_task",
    "get_task",
    "list_tasks",
    "list_task_evidence",
    "list_task_steps",
    "router",
    "submit_task",
]
