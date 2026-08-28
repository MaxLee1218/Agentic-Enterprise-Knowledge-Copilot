"""Test-only drivers for exercising accepted Tasks without a production Queue process."""

from __future__ import annotations

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import WorkflowContainer
from copilot.services.task_submission import trusted_context_from_request


def execute_accepted_task(
    container: WorkflowContainer,
    task_id: str,
    *,
    tenant_id: str,
) -> WorkflowInterrupted | None:
    """Drive one accepted Task outside the HTTP request for hermetic SQLite regressions.

    PostgreSQL Queue/Worker integration tests use the real independent Worker. Older API tests use
    this explicit test-only boundary so they can retain deterministic SQLite fixtures without
    making the production submission route execute LangGraph inline.
    """
    request = container.repository.request_for(task_id, tenant_id=tenant_id)
    context = trusted_context_from_request(request)
    try:
        container.engine.execute_dispatched(request, context, execution_generation=1)
    except WorkflowInterrupted as interrupted:
        return interrupted
    return None


__all__ = ["execute_accepted_task"]
