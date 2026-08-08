"""Cross-boundary trace propagation and parallel-isolation security tests."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock

import httpx

from copilot.contracts import JsonObject, ToolCall, ToolDefinition, ToolResultStatus
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.observability import (
    InMemoryObservability,
    InMemoryTracer,
    MetricsRegistry,
    ObservabilityContextManager,
    PerformanceAnalyzer,
    PerformanceLimits,
    StructuredEventLogger,
)
from copilot.persistence.audit_repository import ToolAuditRepository
from copilot.services.execution import ExecutionContext
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.knowledge import HttpKnowledgeClient, KnowledgeTool
from tests.execution_helpers import execution_context

NOW = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)


class _AllowAuthorizer:
    def authorize_with_context(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        execution_context: ExecutionContext,
    ) -> None:
        del call, definition, execution_context


def _arguments(tenant_id: str, query: str) -> JsonObject:
    return JsonObject(
        {
            "query": query,
            "tenant_id": tenant_id,
            "collection_ids": ["quality"],
            "supplier_ids": ["SUP-001"],
            "date_range": {"start": "2026-01-01", "end": "2026-03-31"},
            "top_k": 5,
            "index_snapshot_id": "snapshot-1",
        }
    )


def _call(label: str) -> ToolCall:
    return ToolCall(
        tool_call_id=f"TC-{label}",
        task_id=f"T-{label}",
        step_id="S-KNOWLEDGE",
        tool_name=KnowledgeTool.definition.tool_name,
        tool_version=KnowledgeTool.definition.tool_version,
        input=_arguments(f"TENANT-{label}", f"question-{label}"),
        idempotency_key=f"IDEMPOTENCY-{label}",
        approval_id=None,
        deadline_at=NOW + timedelta(minutes=5),
        tenant_id=f"TENANT-{label}",
        user_id=f"U-{label}",
    )


def _observability(context: ObservabilityContextManager) -> InMemoryObservability:
    logger = logging.getLogger("copilot.test.stage17.trace")
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    return InMemoryObservability(
        context=context,
        tracer=InMemoryTracer(context=context),
        metrics=MetricsRegistry(),
        analyzer=PerformanceAnalyzer(PerformanceLimits(30, 5, 100, 100, 1024, 100)),
        logger=StructuredEventLogger(logger),
        max_step_duration_seconds=5,
    )


def test_executor_thread_http_trace_propagation_and_parallel_isolation() -> None:
    context = ObservabilityContextManager()
    context.clear()
    captured: dict[str, tuple[str, str | None, str | None, str | None]] = {}
    captured_lock = Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        query = str(json.loads(request.content)["question"])
        current = context.current
        with captured_lock:
            captured[query] = (
                request.headers["X-Trace-ID"],
                current.trace_id,
                current.task_id,
                current.tenant_id,
            )
        return httpx.Response(
            200,
            json={
                "answer": "safe answer",
                "sources": [],
                "contexts": [],
                "route": "rag",
                "latency_ms": 1,
                "rag_trace_id": request.headers["X-Trace-ID"],
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    knowledge_client = HttpKnowledgeClient(
        base_url="http://rag.test",
        timeout_seconds=2,
        max_attempts=1,
        retry_base_delay_seconds=0,
        user_agent="copilot-security-test/1",
        trace_header="X-Trace-ID",
        http_client=http_client,
    )
    registry = ToolRegistry()
    registry.register(KnowledgeTool(knowledge_client))
    executor = ToolExecutor(
        registry=registry,
        authorizer=_AllowAuthorizer(),
        evidence_recorder=InMemoryEvidenceLedger(clock=lambda: NOW),
        audit_sink=ToolAuditRepository(),
        observability=_observability(context),
        clock=lambda: NOW,
    )
    calls = (_call("A"), _call("B"))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(executor.execute, call, execution_context(call)) for call in calls
            ]
            results = [future.result(timeout=5) for future in futures]
    finally:
        executor.close()
        http_client.close()

    assert all(result.status is ToolResultStatus.SUCCESS for result in results)
    assert captured == {
        "question-A": ("TRACE-TC-A", "TRACE-TC-A", "T-A", "TENANT-A"),
        "question-B": ("TRACE-TC-B", "TRACE-TC-B", "T-B", "TENANT-B"),
    }
    assert context.current.trace_id is None
