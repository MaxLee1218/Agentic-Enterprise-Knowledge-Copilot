"""Composed CLI entry point sharing the API's natural-language Task Service."""

from copilot.bootstrap.container import build_application
from copilot.cli.main import create_app
from copilot.config import ConfigurationError, get_settings
from copilot.security.identity import DemoIdentityProvider
from copilot.services.identity import IdentityRequest
from copilot.services.task_intake import NaturalLanguageTaskCommand, TrustedCallerContext
from copilot.services.workflows.models import WorkflowExecution


def build_demo_caller() -> TrustedCallerContext:
    """Build the explicitly non-production identity used by local CLI adapters."""
    settings = get_settings()
    return DemoIdentityProvider(settings).resolve(IdentityRequest(headers={}, source="cli"))


def _run(command: NaturalLanguageTaskCommand) -> WorkflowExecution:
    settings = get_settings()
    if command.metadata.get("cli_demo_identity") is not True:
        raise ConfigurationError(
            "CLI execution requires --demo in development/test; production tasks must use "
            "the authenticated API boundary"
        )
    caller = build_demo_caller()
    with build_application(settings) as container:
        return container.task_service.submit(command, caller)


app = create_app(_run)

__all__ = ["app", "build_demo_caller"]
