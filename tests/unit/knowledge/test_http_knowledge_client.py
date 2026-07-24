"""HTTP contract, validation, trace, and error-mapping tests."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from copilot.tools.knowledge import (
    HttpKnowledgeClient,
    RAGAuthenticationError,
    RAGInternalError,
    RAGInvalidResponseError,
    RAGTimeoutError,
    RAGUnavailableError,
)


def ask_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "answer": "Answer",
        "sources": [],
        "contexts": [],
        "route": "rag",
        "latency_ms": 12,
        "rag_trace_id": "rag-trace",
    }
    payload.update(overrides)
    return payload


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_attempts: int = 3,
) -> HttpKnowledgeClient:
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpKnowledgeClient(
        base_url="http://rag.test/",
        timeout_seconds=5,
        max_attempts=max_attempts,
        retry_base_delay_seconds=0,
        user_agent="copilot-test/1",
        trace_header="X-Trace-ID",
        http_client=http_client,
        sleeper=lambda _delay: None,
    )


def test_health_validates_schema_headers_trace_and_latency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/health"
        assert request.headers["X-Trace-ID"] == "trace-upstream"
        assert request.headers["User-Agent"] == "copilot-test/1"
        return httpx.Response(
            200,
            json={"status": "ok"},
            headers={"X-Trace-ID": "trace-rag"},
        )

    result = make_client(handler).health_check(trace_id="trace-upstream")

    assert result.healthy is True
    assert result.status == "ok"
    assert result.rag_trace_id == "trace-rag"
    assert result.latency_ms >= 0


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(200, text="<html>bad</html>"), RAGInvalidResponseError),
        (httpx.Response(200, json={"status": "starting"}), RAGInvalidResponseError),
        (
            httpx.Response(200, json={"status": "ok", "healthy": False}),
            RAGInvalidResponseError,
        ),
        (httpx.Response(401, json={}), RAGAuthenticationError),
    ],
)
def test_health_rejects_invalid_or_failed_responses(
    response: httpx.Response,
    error_type: type[Exception],
) -> None:
    client = make_client(lambda _request: response)

    with pytest.raises(error_type):
        client.health_check(trace_id="health-error")


def test_health_maps_timeout_and_unavailable() -> None:
    timeout = make_client(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
        max_attempts=1,
    )
    unavailable = make_client(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)),
        max_attempts=1,
    )

    with pytest.raises(RAGTimeoutError):
        timeout.health_check()
    with pytest.raises(RAGUnavailableError):
        unavailable.health_check()


def test_ask_sends_trimmed_question_and_parses_complete_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/ask"
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["X-Trace-ID"] == "ask-trace"
        assert request.headers["User-Agent"] == "copilot-test/1"
        assert request.content == b'{"question":"quality policy"}'
        return httpx.Response(
            200,
            json={
                "answer": "Use the approved deviation procedure.",
                "sources": [
                    {
                        "index": 1,
                        "source": "Procedure.pdf",
                        "metadata": {"chunk_id": "chunk-2"},
                        "text_preview": "Documented containment is required.",
                    },
                ],
                "contexts": [
                    {
                        "content": "Documented containment is required.",
                        "source": "Procedure.pdf",
                        "chunk_id": "chunk-2",
                        "score": 0.9,
                        "metadata": {"chunk_id": "chunk-2"},
                    },
                ],
                "route": "rag",
                "latency_ms": 12.5,
                "rag_trace_id": "body-trace",
            },
        )

    result = make_client(handler).ask("  quality policy  ", trace_id="ask-trace")

    assert result.answer == "Use the approved deviation procedure."
    assert [source.source for source in result.sources] == ["Procedure.pdf"]
    assert result.sources[0].index == 1
    assert result.sources[0].text_preview == "Documented containment is required."
    assert len(result.contexts) == 1
    assert result.contexts[0].chunk_id == "chunk-2"
    assert result.contexts[0].score == 0.9
    assert result.route == "rag"
    assert result.rag_trace_id == "body-trace"
    assert result.latency_ms == 12.5


def test_ask_uses_stable_defaults_and_generated_trace() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["trace"] = request.headers["X-Trace-ID"]
        return httpx.Response(
            200,
            json=ask_payload(rag_trace_id=captured["trace"]),
        )

    result = make_client(handler).ask("question")

    assert result.contexts == ()
    assert result.route == "rag"
    assert result.rag_trace_id == captured["trace"]
    assert captured["trace"]


def test_response_uses_required_body_trace_id() -> None:
    result = make_client(
        lambda _request: httpx.Response(
            200,
            json=ask_payload(rag_trace_id="body"),
            headers={"X-Trace-ID": "header"},
        )
    ).ask("question", trace_id="request")

    assert result.rag_trace_id == "body"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answer": "ok"},
        ask_payload(answer=1),
        ask_payload(answer="  "),
        ask_payload(sources="not-a-list"),
        ask_payload(sources=["invalid"]),
        ask_payload(contexts=["invalid"]),
        ask_payload(route="hybrid"),
        ask_payload(rag_trace_id=None),
        ask_payload(latency_ms=None),
        ask_payload(unexpected=True),
    ],
)
def test_ask_rejects_invalid_schema_without_retry(payload: object) -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json=payload)

    with pytest.raises(RAGInvalidResponseError):
        make_client(handler).ask("question")

    assert count == 1


def test_ask_rejects_non_json_without_retry() -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, text="not json")

    with pytest.raises(RAGInvalidResponseError):
        make_client(handler).ask("question")

    assert count == 1


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_client_contract_statuses_do_not_retry(status: int) -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(status, json={"detail": "safe"})

    with pytest.raises(RAGInvalidResponseError) as captured:
        make_client(handler).ask("question")

    assert captured.value.status_code == status
    assert count == 1


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_statuses_do_not_retry(status: int) -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(status, json={})

    with pytest.raises(RAGAuthenticationError):
        make_client(handler).ask("question")

    assert count == 1


def test_http_500_is_internal_and_not_retried() -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(500, json={})

    with pytest.raises(RAGInternalError):
        make_client(handler).ask("question")

    assert count == 1


def test_blank_question_fails_before_http() -> None:
    count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json=ask_payload(answer="unused"))

    with pytest.raises(ValueError, match="question"):
        make_client(handler).ask("   ")

    assert count == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base_url": "not-a-url"}, "base_url"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"max_attempts": 4}, "max_attempts"),
        ({"retry_base_delay_seconds": -1}, "retry_base_delay_seconds"),
        ({"user_agent": "bad\nagent"}, "user_agent"),
        ({"trace_header": "bad header"}, "trace_header"),
    ],
)
def test_client_rejects_invalid_configuration(
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "base_url": "http://rag.test",
        "timeout_seconds": 1,
        "max_attempts": 1,
        "retry_base_delay_seconds": 0,
        "user_agent": "test/1",
        "trace_header": "X-Trace-ID",
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        HttpKnowledgeClient(**arguments)  # type: ignore[arg-type]
