"""Safe uniform HTTP error translation."""

from fastapi import Request
from fastapi.responses import JSONResponse

from copilot.api.schemas.tasks import TaskErrorResponse
from copilot.services.task_intake import TaskIntakeValidationError


def _trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", "trace-unavailable"))


async def request_validation_handler(
    request: Request,
    _error: Exception,
) -> JSONResponse:
    """Return a stable error without reflecting unsafe request content."""
    payload = TaskErrorResponse(
        error_code="INVALID_TASK_INPUT",
        message="Task submission failed validation.",
        trace_id=_trace_id(request),
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


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


async def internal_error_handler(request: Request, _error: Exception) -> JSONResponse:
    """Prevent stack traces, paths, prompts, and secrets from crossing the API boundary."""
    payload = TaskErrorResponse(
        error_code="INTERNAL_ERROR",
        message="The task could not be processed.",
        trace_id=_trace_id(request),
    )
    return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))


__all__ = [
    "internal_error_handler",
    "request_validation_handler",
    "task_intake_validation_handler",
]
