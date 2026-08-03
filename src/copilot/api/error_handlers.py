"""Safe uniform HTTP error translation."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from copilot.api.schemas.tasks import TaskErrorResponse
from copilot.services.approval_service import ApprovalServiceError
from copilot.services.artifact_service import ArtifactServiceError
from copilot.services.task_intake import TaskIntakeValidationError
from copilot.services.task_service import TaskServiceError


def _trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", "trace-unavailable"))


async def request_validation_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    """Return a stable error without reflecting unsafe request content."""
    if "/approvals/" in request.url.path:
        validation = error if isinstance(error, RequestValidationError) else None
        invalid_action = validation is not None and any(
            tuple(item.get("loc", ())) == ("body", "action") for item in validation.errors()
        )
        payload = TaskErrorResponse(
            error_code=(
                "INVALID_APPROVAL_ACTION" if invalid_action else "INVALID_APPROVAL_REQUEST"
            ),
            message="Approval request failed validation.",
            trace_id=_trace_id(request),
        )
        return JSONResponse(
            status_code=400 if invalid_action else 422,
            content=payload.model_dump(mode="json"),
        )
    payload = TaskErrorResponse(
        error_code="INVALID_TASK_INPUT",
        message="Task submission failed validation.",
        trace_id=_trace_id(request),
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


async def approval_service_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    """Map safe typed approval failures without exposing arguments or internals."""
    approval_error = (
        error
        if isinstance(error, ApprovalServiceError)
        else ApprovalServiceError("Approval could not be processed")
    )
    payload = TaskErrorResponse(
        error_code=approval_error.code,
        message=str(approval_error),
        trace_id=_trace_id(request),
    )
    return JSONResponse(
        status_code=approval_error.status_code,
        content=payload.model_dump(mode="json"),
    )


async def task_intake_validation_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    """Translate deterministic intake failures before model execution."""
    intake_error = (
        error
        if isinstance(error, TaskIntakeValidationError)
        else TaskIntakeValidationError("INVALID_TASK_INPUT", "Task submission failed validation.")
    )
    payload = TaskErrorResponse(
        error_code=intake_error.code,
        message=str(intake_error),
        trace_id=_trace_id(request),
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


async def pydantic_validation_handler(request: Request, _error: Exception) -> JSONResponse:
    """Normalize boundary model validation without reflecting rejected values."""
    payload = TaskErrorResponse(
        error_code="INVALID_REQUEST_MODEL",
        message="Request model validation failed.",
        trace_id=_trace_id(request),
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


async def task_service_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Map authorized task-management failures to the uniform response."""
    task_error = (
        error
        if isinstance(error, TaskServiceError)
        else TaskServiceError(
            "TASK_SERVICE_ERROR",
            "Task operation failed.",
            status_code=500,
            task_id=None,
        )
    )
    payload = TaskErrorResponse(
        error_code=task_error.code,
        message=str(task_error),
        task_id=task_error.task_id,
        trace_id=_trace_id(request),
    )
    return JSONResponse(
        status_code=task_error.status_code,
        content=payload.model_dump(mode="json"),
    )


async def artifact_service_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Map controlled Artifact lookup and availability failures."""
    artifact_error = (
        error
        if isinstance(error, ArtifactServiceError)
        else ArtifactServiceError(
            "ARTIFACT_SERVICE_ERROR",
            "Artifact operation failed.",
            status_code=500,
            task_id="unknown",
        )
    )
    payload = TaskErrorResponse(
        error_code=artifact_error.code,
        message=str(artifact_error),
        task_id=artifact_error.task_id,
        trace_id=_trace_id(request),
    )
    return JSONResponse(
        status_code=artifact_error.status_code,
        content=payload.model_dump(mode="json"),
    )


async def internal_error_handler(request: Request, _error: Exception) -> JSONResponse:
    """Prevent stack traces, paths, prompts, and secrets from crossing the API boundary."""
    payload = TaskErrorResponse(
        error_code="INTERNAL_ERROR",
        message="The task could not be processed.",
        trace_id=_trace_id(request),
    )
    return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))


__all__ = [
    "approval_service_error_handler",
    "artifact_service_error_handler",
    "internal_error_handler",
    "pydantic_validation_handler",
    "request_validation_handler",
    "task_service_error_handler",
    "task_intake_validation_handler",
]
