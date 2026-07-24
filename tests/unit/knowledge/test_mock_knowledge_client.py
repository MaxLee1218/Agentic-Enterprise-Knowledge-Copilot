"""Network-free KnowledgeClient test-double behavior."""

from __future__ import annotations

import pytest

from copilot.tools.knowledge import (
    KnowledgeHealthResult,
    KnowledgeResult,
    MockKnowledgeClient,
    RAGAuthenticationError,
    RAGError,
    RAGInternalError,
    RAGInvalidResponseError,
    RAGTimeoutError,
    RAGUnavailableError,
)


def test_mock_returns_fixed_results_and_records_calls() -> None:
    health = KnowledgeHealthResult(
        healthy=True,
        status="ok",
        latency_ms=1,
        rag_trace_id="health",
    )
    answer = KnowledgeResult(
        answer="fixed",
        latency_ms=2,
        rag_trace_id="ask",
    )
    client = MockKnowledgeClient(health_result=health, ask_result=answer)

    assert client.health_check(trace_id="h") is health
    assert client.ask("question", trace_id="a") is answer
    assert client.health_call_count == 1
    assert client.ask_call_count == 1
    assert client.last_question == "question"
    assert client.last_trace_id == "a"


@pytest.mark.parametrize(
    "error",
    [
        RAGTimeoutError("timeout", trace_id="t"),
        RAGUnavailableError("unavailable", trace_id="t"),
        RAGInvalidResponseError("invalid", trace_id="t"),
        RAGAuthenticationError("auth", trace_id="t"),
        RAGInternalError("internal", trace_id="t"),
    ],
)
def test_mock_can_raise_each_rag_error(error: RAGError) -> None:
    client = MockKnowledgeClient(ask_error=error)

    with pytest.raises(type(error)):
        client.ask("question")

    assert client.ask_call_count == 1
