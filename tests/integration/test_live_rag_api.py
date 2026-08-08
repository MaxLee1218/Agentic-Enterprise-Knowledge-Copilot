"""Opt-in live Enterprise RAG contract and Knowledge Tool verification."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from copilot.bootstrap.knowledge import build_http_knowledge_client
from copilot.config import get_settings
from copilot.contracts import JsonObject, ToolCall, ToolResultStatus
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import InMemoryToolAuditRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.knowledge import KnowledgeTool
from tests.execution_helpers import execution_context

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_rag,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_RAG_TESTS") != "1",
        reason="Set RUN_LIVE_RAG_TESTS=1 to call a live Enterprise RAG Engine",
    ),
]


def test_live_health_ask_and_governed_evidence() -> None:
    settings = get_settings()
    client = build_http_knowledge_client(settings)
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
    call = ToolCall(
        tool_call_id="TC-LIVE-RAG",
        task_id="T-LIVE-RAG",
        step_id="S-LIVE-RAG",
        tool_name=tool.definition.tool_name,
        tool_version=tool.definition.tool_version,
        input=JsonObject(
            {
                "query": "What is the supplier quality deviation procedure?",
                "tenant_id": "live-verification",
                "collection_ids": ["live-verification"],
                "supplier_ids": [],
                "date_range": {"start": "1970-01-01", "end": "9999-12-31"},
                "top_k": 10,
                "index_snapshot_id": "live-rag",
            }
        ),
        idempotency_key="live-rag",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=25),
        tenant_id="live-verification",
        user_id="live-verification",
    )
    try:
        health = client.health_check(trace_id="live-health")
        answer = client.ask(
            "What is the supplier quality deviation procedure?",
            trace_id="live-ask",
        )
        tool_result = executor.execute(call, execution_context(call))
    finally:
        executor.close()
        client.close()

    assert health.healthy is True
    assert health.rag_trace_id
    assert answer.answer.strip()
    assert answer.rag_trace_id
    assert tool_result.status is ToolResultStatus.SUCCESS
    assert len(tool_result.evidence_ids) == len(
        ledger.list_for_task("T-LIVE-RAG", tenant_id="live-verification")
    )
