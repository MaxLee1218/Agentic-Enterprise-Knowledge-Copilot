"""Composed CLI entry point sharing the API's natural-language Task Service."""

from copilot.bootstrap.container import build_application
from copilot.cli.main import create_app
from copilot.config import get_settings
from copilot.services.task_intake import NaturalLanguageTaskCommand, TrustedCallerContext
from copilot.services.workflows.models import WorkflowExecution


def build_demo_caller() -> TrustedCallerContext:
    """Build the explicitly non-production identity used by local CLI adapters."""
    settings = get_settings()
    return TrustedCallerContext(
        user_id=settings.demo_user_id,
        tenant_id=settings.demo_tenant_id,
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
        roles=settings.demo_approval_roles,
        policy_forces_read_only=settings.task_force_read_only,
        policy_requires_approval=settings.task_require_approval_by_default,
    )


def _run(command: NaturalLanguageTaskCommand) -> WorkflowExecution:
    settings = get_settings()
    caller = build_demo_caller()
    with build_application(settings) as container:
        return container.task_service.submit(command, caller)


app = create_app(_run)

__all__ = ["app", "build_demo_caller"]
