"""Exact transport retry and nested-retry prevention tests."""

from __future__ import annotations

import errno
from collections.abc import Callable

import httpx
import pytest

from copilot.tools.knowledge import (
    HttpKnowledgeClient,
    RAGInternalError,
    RAGTimeoutError,
    RAGUnavailableError,
)


def success_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "answer": "ok",
            "sources": [],
            "contexts": [],
            "route": "rag",
            "latency_ms": 1,
            "rag_trace_id": "retry-trace",
        },
    )


def client_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_attempts: int = 3,
    delays: list[float] | None = None,
) -> HttpKnowledgeClient:
    recorded_delays = delays if delays is not None else []
    return HttpKnowledgeClient(
        base_url="http://rag.test",
        timeout_seconds=1,
        max_attempts=max_attempts,
        retry_base_delay_seconds=0.2,
        user_agent="test/1",
        trace_header="X-Trace-ID",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=recorded_delays.append,
    )


def test_first_attempt_success_calls_once() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return success_response()

    client_for(handler).ask("q")

    assert calls == 1


def test_timeout_then_success_calls_twice_and_injects_sleep() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return success_response()

    client_for(handler, delays=delays).ask("q")

    assert calls == 2
    assert delays == [0.2]


def test_timeout_exhaustion_has_exact_attempt_count() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(RAGTimeoutError) as captured:
        client_for(handler).ask("q")

    assert calls == 3
    assert captured.value.attempts == 3


@pytest.mark.parametrize("status", [502, 503, 504])
def test_retryable_gateway_status_then_success(status: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, json={})
        return success_response()

    client_for(handler).ask("q")

    assert calls == 2


def test_retryable_status_exhaustion_is_internal_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={})

    with pytest.raises(RAGInternalError) as captured:
        client_for(handler).ask("q")

    assert calls == 3
    assert captured.value.attempts == 3
    assert captured.value.retryable is True


def test_connection_reset_then_success_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                raise ConnectionResetError(errno.ECONNRESET, "reset")
            except ConnectionResetError as cause:
                raise httpx.ConnectError("reset", request=request) from cause
        return success_response()

    client_for(handler).ask("q")

    assert calls == 2


def test_connection_refused_does_not_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        try:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")
        except ConnectionRefusedError as cause:
            raise httpx.ConnectError("refused", request=request) from cause

    with pytest.raises(RAGUnavailableError) as captured:
        client_for(handler).ask("q")

    assert calls == 1
    assert captured.value.retryable is False


def test_max_attempts_one_never_retries() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(504, json={})

    with pytest.raises(RAGInternalError):
        client_for(handler, max_attempts=1).ask("q")

    assert calls == 1
