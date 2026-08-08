"""Truthful cooperative and non-cancellable invocation semantics."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import sleep

from copilot.contracts import JsonObject, ToolCall, ToolDefinition, ToolResult, ToolResultStatus
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import ToolAuditRepository
from copilot.services.execution import ExecutionContext
from copilot.tools import ToolExecutionContext, ToolExecutionOutput, ToolExecutor, ToolRegistry
from copilot.tools.cancellation import (
    CancellationPhase,
    CancellationToken,
    InvocationCancellationRegistry,
)
from copilot.tools.registry import ToolCancellationMode
from tests.execution_helpers import execution_context
from tests.mocks.mock_tools import MockKnowledgeTool


class _AllowAuthorizer:
    def authorize_with_context(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        context: ExecutionContext,
    ) -> None:
        assert call.tool_name == definition.tool_name
        assert context.authenticated


class _CooperativeTool(MockKnowledgeTool):
    def __init__(self, started: Event) -> None:
        super().__init__()
        self._started = started

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        self._started.set()
        while True:
            context.cancellation.raise_if_requested()
            sleep(0.001)


class _NonCancellableTool(MockKnowledgeTool):
    def __init__(self, started: Event, release: Event) -> None:
        super().__init__()
        self._started = started
        self._release = release

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        self._started.set()
        self._release.wait(timeout=2)
        return super().execute(arguments, context)


def _runtime(
    tool: MockKnowledgeTool,
    registry: InvocationCancellationRegistry,
    *,
    mode: ToolCancellationMode,
) -> ToolExecutor:
    tools = ToolRegistry()
    tools.register(tool, cancellation_mode=mode)
    return ToolExecutor(
        registry=tools,
        authorizer=_AllowAuthorizer(),
        evidence_recorder=InMemoryEvidenceLedger(),
        audit_sink=ToolAuditRepository(),
        cancellation_registry=registry,
    )


def _call(call_id: str) -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        task_id="T-CANCELLATION",
        step_id="S-CANCELLATION",
        tool_name=MockKnowledgeTool.definition.tool_name,
        tool_version=MockKnowledgeTool.definition.tool_version,
        input=JsonObject({"query": "quality policy"}),
        idempotency_key=f"IDEMPOTENCY-{call_id}",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=5),
        tenant_id="TENANT-A",
        user_id="U-001",
    )


def test_cancel_before_execution_is_idempotent_and_never_calls_adapter() -> None:
    started = Event()
    token = CancellationToken()
    assert token.request("cancel before start") is True
    assert token.request("duplicate") is False
    registry = InvocationCancellationRegistry()
    tool = _CooperativeTool(started)
    executor = _runtime(tool, registry, mode=ToolCancellationMode.COOPERATIVE)
    call = _call("TC-CANCEL-BEFORE")
    try:
        result = executor.execute(call, execution_context(call, cancellation=token))
    finally:
        executor.close()

    assert result.status is ToolResultStatus.TECHNICAL_FAILURE
    assert result.error is not None and result.error.error_code == "TOOL_CANCELLED"
    assert token.phase.value == CancellationPhase.CANCELLED.value
    assert started.is_set() is False
    assert registry.snapshot() == ()


def test_cancel_during_cooperative_execution_stops_adapter_and_cleans_resources() -> None:
    started = Event()
    token = CancellationToken()
    registry = InvocationCancellationRegistry()
    executor = _runtime(_CooperativeTool(started), registry, mode=ToolCancellationMode.COOPERATIVE)
    call = _call("TC-CANCEL-DURING")
    with ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(
            executor.execute,
            call,
            execution_context(call, cancellation=token),
        )
        assert started.wait(timeout=1)
        assert registry.cancel_task(call.task_id, reason="task cancellation") == 1
        assert registry.cancel_task(call.task_id, reason="duplicate") == 0
        result = future.result(timeout=2)
    executor.close()

    assert result.error is not None and result.error.error_code == "TOOL_CANCELLED"
    assert token.phase.value == CancellationPhase.CANCELLED.value
    assert registry.snapshot() == ()


def test_non_cancellable_adapter_exposes_requested_until_underlying_work_returns() -> None:
    started = Event()
    release = Event()
    token = CancellationToken()
    registry = InvocationCancellationRegistry()
    executor = _runtime(
        _NonCancellableTool(started, release),
        registry,
        mode=ToolCancellationMode.NON_CANCELLABLE,
    )
    call = _call("TC-NON-CANCELLABLE")
    with ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(
            executor.execute,
            call,
            execution_context(call, cancellation=token),
        )
        assert started.wait(timeout=1)
        assert registry.cancel_task(call.task_id, reason="cannot interrupt thread") == 1
        assert token.phase is CancellationPhase.CANCELLATION_REQUESTED
        assert future.done() is False
        release.set()
        result: ToolResult = future.result(timeout=2)
    executor.close()

    assert result.error is not None and result.error.error_code == "TOOL_CANCELLED"
    assert result.evidence_ids == ()
    assert token.phase.value == CancellationPhase.CANCELLED.value


def test_completed_invocation_cannot_be_relabelled_cancelled_and_shutdown_signals_active_work() -> (
    None
):
    completed_token = CancellationToken()
    assert completed_token.mark_completed() is True
    assert completed_token.request("too late") is False
    assert completed_token.phase is CancellationPhase.COMPLETED

    started = Event()
    token = CancellationToken()
    registry = InvocationCancellationRegistry()
    executor = _runtime(_CooperativeTool(started), registry, mode=ToolCancellationMode.COOPERATIVE)
    call = _call("TC-SHUTDOWN-CANCEL")
    with ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(
            executor.execute,
            call,
            execution_context(call, cancellation=token),
        )
        assert started.wait(timeout=1)
        executor.close()
        result = future.result(timeout=2)

    assert result.error is not None and result.error.error_code == "TOOL_CANCELLED"
    assert token.phase is CancellationPhase.CANCELLED
    assert registry.snapshot() == ()
