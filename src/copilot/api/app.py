"""FastAPI application factory composed around the shared task service."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.responses import Response

from copilot.api.error_handlers import (
    approval_service_error_handler,
    internal_error_handler,
    request_validation_handler,
    task_intake_validation_handler,
)
from copilot.api.routes.approvals import router as approvals_router
from copilot.api.routes.tasks import router as tasks_router
from copilot.config import Settings
from copilot.services.approval_service import ApprovalService, ApprovalServiceError
from copilot.services.task_intake import TaskIntakeValidationError
from copilot.services.task_service import NaturalLanguageTaskService


class HealthResponse(BaseModel):
    """Stable response contract for service health checks."""

    status: Literal["ok"]


def create_app(
    *,
    task_service: NaturalLanguageTaskService | None = None,
    approval_service: ApprovalService | None = None,
    settings: Settings | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Create a framework adapter; concrete runtime ownership remains in bootstrap."""
    application = FastAPI(
        title="Agentic Enterprise Knowledge Copilot",
        version="0.1.0",
        lifespan=lifespan,
    )
    if task_service is not None:
        application.state.task_service = task_service
    if approval_service is not None:
        application.state.approval_service = approval_service
    if settings is not None:
        application.state.settings = settings

    @application.middleware("http")
    async def correlation_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.trace_id = f"TRACE-{uuid.uuid4().hex}"
        return await call_next(request)

    @application.get("/health", response_model=HealthResponse, status_code=200)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    application.include_router(tasks_router)
    application.include_router(approvals_router)
    application.add_exception_handler(RequestValidationError, request_validation_handler)
    application.add_exception_handler(TaskIntakeValidationError, task_intake_validation_handler)
    application.add_exception_handler(ApprovalServiceError, approval_service_error_handler)
    application.add_exception_handler(Exception, internal_error_handler)
    return application


app = create_app()

__all__ = ["HealthResponse", "app", "create_app"]
