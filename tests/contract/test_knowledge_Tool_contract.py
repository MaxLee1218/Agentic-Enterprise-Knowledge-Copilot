"""Enterprise RAG HTTP contract tests for the Knowledge Tool infrastructure."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from copilot.tools.knowledge import (
    HttpKnowledgeClient,
    RAGInternalError,
    RAGInvalidResponseError,
    RAGTimeoutError,
)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpKnowledgeClient:
    """Create a client backed by HTTPX's in-process mock HTTP server."""
    return HttpKnowledgeClient(
        base_url="http://rag-contract.test",
        timeout_seconds=1,
        max_attempts=1,
        retry_base_delay_seconds=0,
        user_agent="contract-test/1",
        trace_header="X-Trace-ID",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _delay: None,
    )


def _valid_response() -> dict[str, object]:
    return {
        "answer": "Retrieval-augmented generation uses retrieved evidence.",
        "sources": [
            {
                "index": 1,
                "source": "docs/rag.md",
                "metadata": {"chunk_id": "rag-1"},
                "text_preview": "Retrieval-augmented generation...",
            }
        ],
        "contexts": [
            {
                "content": "Retrieval-augmented generation...",
                "source": "docs/rag.md",
                "chunk_id": "rag-1",
                "score": 0.92,
                "metadata": {"chunk_id": "rag-1"},
            }
        ],
        "route": "rag",
        "latency_ms": 12,
        "rag_trace_id": "copilot-request-123",
    }


def test_normal_response_is_parsed_exactly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/ask"
        assert request.headers["X-Trace-ID"] == "copilot-request-123"
        assert json.loads(request.content) == {
            "question": "What is retrieval-augmented generation?"
        }
        return httpx.Response(200, json=_valid_response())

    result = _client(handler).ask(
        "What is retrieval-augmented generation?",
        trace_id="copilot-request-123",
    )

    assert result.answer == "Retrieval-augmented generation uses retrieved evidence."
    assert result.sources[0].index == 1
    assert result.sources[0].source == "docs/rag.md"
    assert result.sources[0].metadata is not None
    assert result.sources[0].metadata.root["chunk_id"] == "rag-1"
    assert result.sources[0].text_preview == "Retrieval-augmented generation..."
    assert result.contexts[0].content == "Retrieval-augmented generation..."
    assert result.contexts[0].chunk_id == "rag-1"
    assert result.contexts[0].score == 0.92
    assert result.route == "rag"
    assert result.latency_ms == 12
    assert result.rag_trace_id == "copilot-request-123"


def test_missing_answer_is_invalid_response() -> None:
    payload = _valid_response()
    del payload["answer"]

    with pytest.raises(RAGInvalidResponseError):
        _client(lambda _request: httpx.Response(200, json=payload)).ask("question")


def test_invalid_json_is_invalid_response() -> None:
    with pytest.raises(RAGInvalidResponseError):
        _client(lambda _request: httpx.Response(200, text="{invalid")).ask("question")


def test_http_500_is_internal_error() -> None:
    with pytest.raises(RAGInternalError) as captured:
        _client(lambda _request: httpx.Response(500, json={"detail": "internal"})).ask("question")

    assert captured.value.status_code == 500
    assert captured.value.attempts == 1


def test_timeout_is_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("contract timeout", request=request)

    with pytest.raises(RAGTimeoutError) as captured:
        _client(handler).ask("question")

    assert captured.value.attempts == 1
    assert captured.value.retryable is True
