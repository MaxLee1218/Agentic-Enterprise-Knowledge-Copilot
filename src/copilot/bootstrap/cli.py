"""Composed CLI entry point sharing the API's natural-language Task Service."""

from time import monotonic, sleep

from copilot.bootstrap.container import build_application
from copilot.cli.main import create_app
from copilot.config import ConfigurationError, get_settings
from copilot.contracts.async_runtime import TaskSubmissionResponse
from copilot.security.identity import DemoIdentityProvider
from copilot.services.identity import IdentityRequest
from copilot.services.task_intake import NaturalLanguageTaskCommand, TrustedCallerContext
from copilot.services.task_views import TaskSummaryView


def build_demo_caller() -> TrustedCallerContext:
    """Build the explicitly non-production identity used by local CLI adapters."""
    settings = get_settings()
    return DemoIdentityProvider(settings).resolve(IdentityRequest(headers={}, source="cli"))


def _submit(
    command: NaturalLanguageTaskCommand,
    idempotency_key: str | None,
) -> TaskSubmissionResponse:
    """Durably submit through the same acceptance service as HTTP without inline execution."""
    settings = get_settings()
    if command.metadata.get("cli_demo_identity") is not True:
        raise ConfigurationError(
            "CLI execution requires --demo in development/test; production tasks must use "
            "the authenticated API boundary"
        )
    caller = build_demo_caller()
    with build_application(settings) as container:
        service = container.task_submission_service
        if service is None:
            raise ConfigurationError("Async submission requires authoritative persistence")
        return service.submit(command, caller, idempotency_key=idempotency_key)


def _wait(task_id: str, timeout_seconds: float) -> TaskSummaryView:
    """Poll authoritative state only; execution remains owned by an independent Worker."""
    settings = get_settings()
    caller = build_demo_caller()
    deadline = monotonic() + timeout_seconds
    with build_application(settings) as container:
        while True:
            task = container.task_service.get_task(task_id, caller)
            if task.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                return task
            if monotonic() >= deadline:
                raise TimeoutError(f"Task {task_id} did not reach a terminal state")
            sleep(min(0.5, max(0.0, deadline - monotonic())))


app = create_app(_submit, _wait)

__all__ = ["app", "build_demo_caller"]
