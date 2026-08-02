"""Unified natural-language task intake used by HTTP and CLI transports."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from copilot.contracts import JsonObject, TaskRequest
from copilot.services.task_intake import (
    IntakeLimits,
    NaturalLanguageTaskCommand,
    TrustedCallerContext,
    TrustedTaskContext,
    merge_execution_constraints,
    sanitize_metadata,
    validate_task_text,
)
from copilot.services.workflows.models import WorkflowExecution
from copilot.services.workflows.ports import IdentifierFactory


class NaturalLanguageWorkflowEngine(Protocol):
    """Graph boundary required by the intake service."""

    def submit(
        self,
        request: TaskRequest,
        intake_context: TrustedTaskContext,
    ) -> WorkflowExecution:
        """Persist and execute one natural-language task."""
        ...


class NaturalLanguageTaskService:
    """Validate, constrain, identify, and submit immutable natural-language requests."""

    def __init__(
        self,
        *,
        engine: NaturalLanguageWorkflowEngine,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        limits: IntakeLimits,
    ) -> None:
        self._engine = engine
        self._ids = ids
        self._clock = clock
        self._limits = limits

    def submit(
        self,
        command: NaturalLanguageTaskCommand,
        caller: TrustedCallerContext,
    ) -> WorkflowExecution:
        """Create TaskRequest and trusted context, then enter the existing LangGraph."""
        request, context = self.prepare(command, caller)
        return self._engine.submit(request, context)

    def prepare(
        self,
        command: NaturalLanguageTaskCommand,
        caller: TrustedCallerContext,
    ) -> tuple[TaskRequest, TrustedTaskContext]:
        """Validate an intake without executing it; useful for thin CLI dry-runs and tests."""
        task_text = validate_task_text(
            command.task,
            max_length=self._limits.max_task_text_length,
        )
        metadata = sanitize_metadata(
            command.metadata,
            max_bytes=self._limits.max_metadata_bytes,
            max_depth=self._limits.max_metadata_depth,
            max_items=self._limits.max_metadata_items,
        )
        effective = merge_execution_constraints(
            limits=self._limits,
            caller=caller,
            requested_max_steps=command.max_steps,
            requested_read_only=command.read_only,
            requested_approval=command.require_approval,
        )
        now = self._clock()
        task_id = self._ids.new_id("T")
        trace_id = command.trace_id or self._ids.new_id("TRACE")
        session_id = command.session_id or self._ids.new_id("SESSION")
        task_hash = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
        request_metadata = dict(metadata.root)
        request_metadata["intake"] = {
            "request_source": command.source.value,
            "session_id": session_id,
            "trace_id": trace_id,
            "task_text_hash": task_hash,
            "task_text_length": len(task_text),
            "output_format": (
                command.output_format.value if command.output_format is not None else None
            ),
            "effective_max_steps": effective.max_steps,
            "effective_read_only": effective.read_only,
            "effective_require_approval": effective.require_approval,
        }
        request = TaskRequest(
            id=self._ids.new_id("R"),
            user_id=caller.user_id,
            raw_input=task_text,
            created_at=now,
            metadata=JsonObject(request_metadata),
        )
        context = TrustedTaskContext(
            task_id=task_id,
            trace_id=trace_id,
            session_id=session_id,
            user_id=caller.user_id,
            tenant_id=caller.tenant_id,
            data_scope=caller.data_scope,
            authorized_supplier_ids=caller.supplier_ids,
            roles=caller.roles,
            output_format=(
                command.output_format.artifact_type if command.output_format is not None else None
            ),
            max_steps=effective.max_steps,
            read_only=effective.read_only,
            require_approval=effective.require_approval,
            deadline_at=now + timedelta(seconds=self._limits.max_total_execution_seconds),
            request_source=command.source,
            task_text_hash=task_hash,
            task_text_length=len(task_text),
        )
        return request, context


__all__ = ["NaturalLanguageTaskService", "NaturalLanguageWorkflowEngine"]
