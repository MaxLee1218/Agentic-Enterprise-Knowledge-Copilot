"""FastAPI dependency adapters for composed task services and trusted identity."""

from typing import cast

from fastapi import Request

from copilot.config import Settings
from copilot.services.task_intake import TrustedCallerContext
from copilot.services.task_service import NaturalLanguageTaskService


def get_task_service(request: Request) -> NaturalLanguageTaskService:
    """Return the application service installed by the composition root."""
    return cast(NaturalLanguageTaskService, request.app.state.task_service)


def get_caller_context(request: Request) -> TrustedCallerContext:
    """Return server-owned demo identity until an authentication adapter is installed."""
    settings: Settings = request.app.state.settings
    return TrustedCallerContext(
        user_id=settings.demo_user_id,
        tenant_id=settings.demo_tenant_id,
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
        policy_forces_read_only=settings.task_force_read_only,
        policy_requires_approval=settings.task_require_approval_by_default,
    )


__all__ = ["get_caller_context", "get_task_service"]
