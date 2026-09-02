"""FastAPI application factory composed around the shared task service."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from time import monotonic
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.responses import Response

from copilot.api.error_handlers import (
    approval_service_error_handler,
    artifact_service_error_handler,
    clarification_service_error_handler,
    internal_error_handler,
    pydantic_validation_handler,
    request_validation_handler,
    task_intake_validation_handler,
    task_service_error_handler,
)
from copilot.api.routes.approvals import router as approvals_router
from copilot.api.routes.artifacts import router as artifacts_router
from copilot.api.routes.clarifications import router as clarifications_router
from copilot.api.routes.tasks import router as tasks_router
from copilot.config import Settings
from copilot.contracts import SpanKind, SpanStatus
from copilot.services.approval_service import ApprovalService, ApprovalServiceError
from copilot.services.artifact_service import ArtifactService, ArtifactServiceError
from copilot.services.clarification_service import ClarificationService, ClarificationServiceError
from copilot.services.health import ReadinessService
from copilot.services.identity import IdentityProvider
from copilot.services.observability import (
    EventName,
    NoopObservability,
    ObservabilityPort,
    validate_correlation_id,
)
from copilot.services.task_intake import TaskIntakeValidationError
from copilot.services.task_service import NaturalLanguageTaskService, TaskServiceError
from copilot.services.task_submission import TaskSubmissionService


class HealthResponse(BaseModel):
    """Stable response contract for service health checks."""

    status: Literal["ok"]


class LivenessResponse(BaseModel):
    """Process-only health contract."""

    status: Literal["live"]


class ReadinessResponse(BaseModel):
    """Dependency-aware task acceptance contract."""

    status: Literal["ready", "degraded", "not_ready"]
    accepts_tasks: bool
    dependencies: dict[str, Literal["ok", "unavailable", "not_configured"]]


def create_app(
    *,
    task_service: NaturalLanguageTaskService | None = None,
    task_submission_service: TaskSubmissionService | None = None,
    approval_service: ApprovalService | None = None,
    artifact_service: ArtifactService | None = None,
    clarification_service: ClarificationService | None = None,
    settings: Settings | None = None,
    observability: ObservabilityPort | None = None,
    readiness: ReadinessService | None = None,
    identity_provider: IdentityProvider | None = None,
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
    if task_submission_service is not None:
        application.state.task_submission_service = task_submission_service
    if approval_service is not None:
        application.state.approval_service = approval_service
    if artifact_service is not None:
        application.state.artifact_service = artifact_service
    if clarification_service is not None:
        application.state.clarification_service = clarification_service
    if settings is not None:
        application.state.settings = settings
    if identity_provider is not None:
        application.state.identity_provider = identity_provider
    application.state.observability = observability or NoopObservability()
    application.state.readiness = readiness

    @application.middleware("http")
    async def correlation_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        telemetry = application.state.observability
        trace_id = validate_correlation_id(request.headers.get("X-Trace-ID"))
        request_id = validate_correlation_id(request.headers.get("X-Request-ID"))
        request.state.trace_id = trace_id or f"TRACE-{uuid.uuid4().hex}"
        request.state.request_id = request_id or f"REQUEST-{uuid.uuid4().hex}"
        started = monotonic()
        with telemetry.bind_context(
            trace_id=str(request.state.trace_id),
            request_id=str(request.state.request_id),
        ):
            telemetry.emit(
                EventName.REQUEST_RECEIVED,
                fields={"http_method": request.method, "status": "RUNNING"},
            )
            with telemetry.span(
                "request.http",
                SpanKind.EXTERNAL_SERVICE,
                attributes={"http_method": request.method},
            ) as span:
                try:
                    response = await call_next(request)
                except BaseException as exc:
                    latency_ms = max(0.0, (monotonic() - started) * 1000)
                    span.set_status(SpanStatus.FAILED, error_type=type(exc).__name__)
                    telemetry.emit(
                        EventName.REQUEST_FAILED,
                        level=logging.ERROR,
                        fields={
                            "http_method": request.method,
                            "status": "FAILED",
                            "latency_ms": latency_ms,
                            "error_type": type(exc).__name__,
                        },
                    )
                    raise
                latency_ms = max(0.0, (monotonic() - started) * 1000)
                route = request.scope.get("route")
                route_template = getattr(route, "path", "/unmatched")
                route_template = route_template if isinstance(route_template, str) else "/unmatched"
                failed = response.status_code >= 400
                span.set_attribute("http_status", response.status_code)
                span.set_status(
                    SpanStatus.FAILED if failed else SpanStatus.SUCCEEDED,
                    error_type=f"HTTP_{response.status_code}" if failed else None,
                )
                labels = {
                    "http_method": request.method,
                    "http_status": str(response.status_code),
                    "route_template": route_template,
                }
                telemetry.increment("requests_total", labels=labels)
                telemetry.observe("request_latency_ms", latency_ms, labels=labels)
                telemetry.emit(
                    EventName.REQUEST_FAILED if failed else EventName.REQUEST_COMPLETED,
                    level=logging.WARNING if failed else logging.INFO,
                    fields={
                        "http_method": request.method,
                        "http_status": response.status_code,
                        "route_template": route_template,
                        "latency_ms": latency_ms,
                        "response_size": response.headers.get("content-length"),
                    },
                )
                response.headers["X-Trace-ID"] = str(request.state.trace_id)
                response.headers["X-Request-ID"] = str(request.state.request_id)
                return response

    @application.get("/health", response_model=HealthResponse, status_code=200)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/health/live", response_model=LivenessResponse, status_code=200)
    async def liveness() -> LivenessResponse:
        return LivenessResponse(status="live")

    @application.get(
        "/health/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
    )
    def readiness_status(response: Response) -> ReadinessResponse:
        service: ReadinessService | None = application.state.readiness
        if service is None:
            response.status_code = 503
            return ReadinessResponse(
                status="not_ready",
                accepts_tasks=False,
                dependencies={"database": "not_configured"},
            )
        snapshot = service.check()
        response.status_code = 200 if snapshot.accepts_tasks else 503
        return ReadinessResponse(
            status=snapshot.status,
            accepts_tasks=snapshot.accepts_tasks,
            dependencies=dict(snapshot.dependencies),
        )

    application.include_router(tasks_router)
    application.include_router(artifacts_router)
    application.include_router(approvals_router)
    application.include_router(clarifications_router)
    application.add_exception_handler(RequestValidationError, request_validation_handler)
    application.add_exception_handler(ValidationError, pydantic_validation_handler)
    application.add_exception_handler(TaskIntakeValidationError, task_intake_validation_handler)
    application.add_exception_handler(ApprovalServiceError, approval_service_error_handler)
    application.add_exception_handler(
        ClarificationServiceError,
        clarification_service_error_handler,
    )
    application.add_exception_handler(TaskServiceError, task_service_error_handler)
    application.add_exception_handler(ArtifactServiceError, artifact_service_error_handler)
    application.add_exception_handler(Exception, internal_error_handler)
    return application


app = create_app()

__all__ = [
    "HealthResponse",
    "LivenessResponse",
    "ReadinessResponse",
    "app",
    "create_app",
]
