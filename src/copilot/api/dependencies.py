"""FastAPI dependency adapters for composed task services and trusted identity."""

from typing import cast

from fastapi import Request

from copilot.config import Settings
from copilot.services.approval_service import ApprovalService
from copilot.services.artifact_service import ArtifactService
from copilot.services.task_intake import TrustedCallerContext
from copilot.services.task_service import NaturalLanguageTaskService


def get_task_service(request: Request) -> NaturalLanguageTaskService:
    """Return the application service installed by the composition root."""
    return cast(NaturalLanguageTaskService, request.app.state.task_service)


def get_approval_service(request: Request) -> ApprovalService:
    """Return the application approval service installed by the composition root."""
    return cast(ApprovalService, request.app.state.approval_service)


def get_artifact_service(request: Request) -> ArtifactService:
    """Return the application Artifact service installed by the composition root."""
    return cast(ArtifactService, request.app.state.artifact_service)


def get_caller_context(request: Request) -> TrustedCallerContext:
    """Return server-owned demo identity until an authentication adapter is installed."""
    settings: Settings = request.app.state.settings
    return TrustedCallerContext(
        user_id=settings.demo_user_id,
        tenant_id=settings.demo_tenant_id,
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
        roles=settings.demo_approval_roles,
        policy_forces_read_only=settings.task_force_read_only,
        policy_requires_approval=settings.task_require_approval_by_default,
    )


__all__ = [
    "get_approval_service",
    "get_artifact_service",
    "get_caller_context",
    "get_task_service",
]
