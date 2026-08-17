"""Frozen Knowledge Tool conversion, Evidence, and executor tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from pydantic import JsonValue

from copilot.contracts import (
    EvidenceType,
    JsonObject,
    RiskLevel,
    ToolCall,
    ToolResultStatus,
)
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import InMemoryToolAuditRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.tools import ToolExecutionContext, ToolExecutor, ToolRegistry
from copilot.tools.exceptions import ToolRuntimeError
from copilot.tools.knowledge import (
    HttpKnowledgeClient,
    KnowledgeContext,
    KnowledgeResult,
    KnowledgeSource,
    KnowledgeTool,
    MockKnowledgeClient,
    RAGAuthenticationError,
    RAGInternalError,
    RAGInvalidResponseError,
    RAGTimeoutError,
    RAGUnavailableError,
)
from tests.execution_helpers import execution_context


def input_payload(query: str = "quality policy") -> JsonObject:
    return JsonObject(
        {
            "query": query,
            "tenant_id": "TENANT-A",
            "collection_ids": ["quality"],
            "supplier_ids": ["S-100"],
            "date_range": {"start": "2026-01-01", "end": "2026-03-31"},
            "top_k": 10,
            "index_snapshot_id": "snapshot-1",
        }
    )


def make_call(tool: KnowledgeTool) -> ToolCall:
    return ToolCall(
        tool_call_id="TC-KB-HTTP",
        task_id="T-HTTP",
        step_id="S-KB-HTTP",
        tool_name=tool.definition.tool_name,
        tool_version=tool.definition.tool_version,
        input=input_payload(),
        idempotency_key="idempotency-http",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
        tenant_id="TENANT-A",
        user_id="U-1",
    )


def result_with_sources() -> KnowledgeResult:
    return KnowledgeResult(
        answer="Follow the documented procedure.",
        sources=(
            KnowledgeSource(
                index=1,
                source="Manual.pdf",
                metadata=None,
                text_preview=None,
            ),
            KnowledgeSource(
                index=2,
                source="Procedure.pdf",
                metadata=JsonObject(
                    {
                        "document_version": "v2",
                        "page": 24,
                        "chunk_id": "chunk-2",
                        "classification": "CONFIDENTIAL",
                    }
                ),
                text_preview=None,
            ),
        ),
        contexts=(
            KnowledgeContext(
                content="Contain and document the deviation.",
                source="Procedure.pdf",
                chunk_id="chunk-2",
                score=0.91,
                metadata=JsonObject({"chunk_id": "chunk-2"}),
            ),
        ),
        route="rag",
        latency_ms=12,
        rag_trace_id="rag-trace",
    )


def test_tool_preserves_frozen_definition_metadata() -> None:
    tool = KnowledgeTool(MockKnowledgeClient())

    assert tool.definition.tool_name == "knowledge_search"
    assert tool.definition.risk_level is RiskLevel.LOW
    assert tool.definition.timeout.attempt_seconds == 10
    assert tool.definition.timeout.overall_seconds == 25
    assert tool.definition.idempotency.idempotent is True
    assert tool.definition.approval_policy.approver_role == "quality_data_approver"


def test_tool_calls_client_with_query_trace_and_creates_document_evidence() -> None:
    client = MockKnowledgeClient(ask_result=result_with_sources())
    tool = KnowledgeTool(client)
    context = ToolExecutionContext(
        call=make_call(tool),
        trace_id="upstream-trace",
        tenant_id="TENANT-A",
        user_id="U-1",
    )

    execution = tool.execute(input_payload(), context)

    assert client.ask_call_count == 1
    assert client.last_question == "quality policy"
    assert client.last_trace_id == "upstream-trace"
    assert execution.output.root["match_count"] == 2
    assert execution.output.root["empty_result"] is False
    matches = execution.output.root["matches"]
    assert isinstance(matches, list)
    first = cast(dict[str, JsonValue], matches[0])
    second = cast(dict[str, JsonValue], matches[1])
    assert first["document_id"] == "Manual.pdf"
    assert first["document_version"] == "unknown"
    assert second["chunk_id"] == "chunk-2"
    assert second["excerpt"] == "Contain and document the deviation."
    assert len(execution.evidence) == 2
    assert all(item.source_type is EvidenceType.DOCUMENT for item in execution.evidence)
    reference = execution.evidence[1].source_reference.reference.root
    assert reference["page"] == 24
    assert reference["chunk_id"] == "chunk-2"
    assert reference["rag_trace_id"] == "rag-trace"


def test_empty_sources_succeed_without_fabricated_evidence() -> None:
    client = MockKnowledgeClient(
        ask_result=KnowledgeResult(
            answer="No sources",
            latency_ms=1,
            rag_trace_id="trace",
        )
    )
    tool = KnowledgeTool(client)

    execution = tool.execute(
        input_payload(),
        ToolExecutionContext(call=make_call(tool)),
    )

    assert execution.output.root["matches"] == []
    assert execution.output.root["empty_result"] is True
    assert execution.evidence == ()


@pytest.mark.parametrize(
    ("error", "error_code", "recoverable"),
    [
        (RAGTimeoutError("timeout", trace_id="t"), "KNOWLEDGE_TIMEOUT", True),
        (RAGUnavailableError("down", trace_id="t"), "KNOWLEDGE_UNAVAILABLE", True),
        (RAGAuthenticationError("denied", trace_id="t"), "KNOWLEDGE_ACCESS_DENIED", False),
        (RAGInvalidResponseError("invalid", trace_id="t"), "KNOWLEDGE_INVALID_RESPONSE", False),
        (RAGInternalError("internal", trace_id="t"), "KNOWLEDGE_UNAVAILABLE", False),
    ],
)
def test_tool_maps_rag_errors_to_safe_tool_semantics(
    error: Exception,
    error_code: str,
    recoverable: bool,
) -> None:
    tool = KnowledgeTool(MockKnowledgeClient(ask_error=error))  # type: ignore[arg-type]

    with pytest.raises(ToolRuntimeError) as captured:
        tool.execute(input_payload(), ToolExecutionContext(call=make_call(tool)))

    assert captured.value.error.error_code == error_code
    assert captured.value.error.recoverable is recoverable


def test_registry_executor_records_evidence_and_does_not_amplify_client_retry() -> None:
    client = MockKnowledgeClient(
        ask_error=RAGTimeoutError(
            "exhausted",
            trace_id="trace",
            attempts=3,
            retryable=True,
        )
    )
    tool = KnowledgeTool(client)
    registry = ToolRegistry()
    registry.register(tool)
    ledger = InMemoryEvidenceLedger()
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=InMemoryToolAuditRepository(),
    )

    try:
        call = make_call(tool)
        result = executor.execute(call, execution_context(call))
    finally:
        executor.close()

    assert result.status is ToolResultStatus.TIMEOUT
    assert result.error is not None
    assert result.error.error_code == "KNOWLEDGE_TIMEOUT"
    assert result.error.recoverable is True
    assert client.ask_call_count == 1


def test_executor_with_http_client_stops_after_configured_transport_attempts() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ReadTimeout("slow", request=request)

    client = HttpKnowledgeClient(
        base_url="http://rag.test",
        timeout_seconds=1,
        max_attempts=3,
        retry_base_delay_seconds=0,
        user_agent="test/1",
        trace_header="X-Trace-ID",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _delay: None,
    )
    tool = KnowledgeTool(client)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=InMemoryEvidenceLedger(),
        audit_sink=InMemoryToolAuditRepository(),
    )

    try:
        call = make_call(tool)
        result = executor.execute(call, execution_context(call))
    finally:
        executor.close()

    assert request_count == 3
    assert result.status is ToolResultStatus.TIMEOUT
    assert result.error is not None
    assert result.error.recoverable is True


def test_registry_executor_success_makes_evidence_available_to_workflow_reader() -> None:
    tool = KnowledgeTool(MockKnowledgeClient(ask_result=result_with_sources()))
    registry = ToolRegistry()
    registry.register(tool)
    ledger = InMemoryEvidenceLedger()
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=InMemoryToolAuditRepository(),
    )

    try:
        call = make_call(tool)
        result = executor.execute(call, execution_context(call))
    finally:
        executor.close()

    assert result.status is ToolResultStatus.SUCCESS
    assert len(result.evidence_ids) == 2
    assert len(ledger.list_for_task("T-HTTP", tenant_id="TENANT-A")) == 2
    assert result.latency_ms is not None
    assert result.latency_ms >= 0
