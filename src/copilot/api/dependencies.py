"""FastAPI dependency adapters for composed task services and trusted identity."""

from typing import cast

from fastapi import HTTPException, Request

from copilot.services.approval_service import ApprovalService
from copilot.services.artifact_service import ArtifactService
from copilot.services.identity import IdentityProvider, IdentityRequest, IdentityResolutionError
from copilot.services.task_intake import TrustedCallerContext
from copilot.services.task_service import NaturalLanguageTaskService
from copilot.services.task_submission import TaskSubmissionService


def get_task_service(request: Request) -> NaturalLanguageTaskService:
    """Return the application service installed by the composition root."""
    return cast(NaturalLanguageTaskService, request.app.state.task_service)


def get_task_submission_service(request: Request) -> TaskSubmissionService:
    """Return the acceptance-only Task submission service."""
    return cast(TaskSubmissionService, request.app.state.task_submission_service)


def get_approval_service(request: Request) -> ApprovalService:
    """Return the application approval service installed by the composition root."""
    return cast(ApprovalService, request.app.state.approval_service)


def get_artifact_service(request: Request) -> ArtifactService:
    """Return the application Artifact service installed by the composition root."""
    return cast(ArtifactService, request.app.state.artifact_service)


def get_caller_context(request: Request) -> TrustedCallerContext:
    """Resolve every API caller through the explicitly composed authentication boundary."""
    provider = cast(IdentityProvider, request.app.state.identity_provider)
    try:
        return provider.resolve(IdentityRequest(headers=dict(request.headers), source="api"))
    except IdentityResolutionError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc


__all__ = [
    "get_approval_service",
    "get_artifact_service",
    "get_caller_context",
    "get_task_service",
    "get_task_submission_service",
]
