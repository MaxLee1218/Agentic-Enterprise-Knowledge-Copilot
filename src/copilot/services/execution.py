"""Stable trusted execution context required for every governed tool invocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from copilot.contracts import ToolCall
from copilot.services.task_intake import TrustedTaskContext
from copilot.tools.cancellation import CancellationToken


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Non-model-controlled identity, scope, approval, trace, deadline, and cancellation facts."""

    task_id: str
    trace_id: str
    step_id: str
    user_id: str
    tenant_id: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    data_scope: tuple[str, ...]
    purpose: str
    authentication_source: str
    is_demo_identity: bool
    authenticated: bool
    deadline_at: datetime
    approval_required: bool
    approval_id: str | None
    cancellation: CancellationToken

    @classmethod
    def from_task_context(
        cls,
        trusted: TrustedTaskContext,
        call: ToolCall,
        *,
        approval_required: bool,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionContext:
        """Bind a task-level trusted envelope to one exact invocation."""
        return cls(
            task_id=trusted.task_id,
            trace_id=trusted.trace_id,
            step_id=call.step_id,
            user_id=trusted.user_id,
            tenant_id=trusted.tenant_id,
            roles=trusted.roles,
            scopes=trusted.scopes,
            data_scope=trusted.data_scope,
            purpose=trusted.purpose,
            authentication_source=trusted.authentication_source,
            is_demo_identity=trusted.is_demo_identity,
            authenticated=trusted.authenticated,
            deadline_at=trusted.deadline_at,
            approval_required=approval_required,
            approval_id=call.approval_id,
            cancellation=cancellation or CancellationToken(),
        )


__all__ = ["ExecutionContext"]
