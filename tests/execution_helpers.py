"""Trusted execution-context builders shared by governed executor tests."""

from copilot.contracts import ToolCall
from copilot.services.execution import ExecutionContext
from copilot.tools.cancellation import CancellationToken


def execution_context(
    call: ToolCall,
    *,
    roles: tuple[str, ...] = ("quality_analyst",),
    scopes: tuple[str, ...] = ("quality.read", "tool.execute"),
    data_scope: tuple[str, ...] = ("quality.v1",),
    purpose: str = "supplier_quality_analysis.v1",
    authenticated: bool = True,
    approval_required: bool = False,
    cancellation: CancellationToken | None = None,
) -> ExecutionContext:
    """Bind server-trusted test identity facts to one exact ToolCall."""
    return ExecutionContext(
        task_id=call.task_id,
        trace_id=f"TRACE-{call.tool_call_id}",
        step_id=call.step_id,
        user_id=call.user_id,
        tenant_id=call.tenant_id,
        roles=roles,
        scopes=scopes,
        data_scope=data_scope,
        purpose=purpose,
        authentication_source="test",
        is_demo_identity=False,
        authenticated=authenticated,
        deadline_at=call.deadline_at,
        approval_required=approval_required,
        approval_id=call.approval_id,
        cancellation=cancellation or CancellationToken(),
    )


__all__ = ["execution_context"]
