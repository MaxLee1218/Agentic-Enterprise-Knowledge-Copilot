"""DeepSeek transport, retry, error mapping, and secret-safety tests."""

import json

import httpx
import pytest
from pydantic import BaseModel

from copilot.llm.deepseek import DeepSeekProvider
from copilot.services.llm import (
    LLMAuthenticationError,
    LLMCallContext,
    LLMGenerationOptions,
    LLMInvalidResponseError,
    LLMMessage,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)


class Output(BaseModel):
    answer: str


def _context() -> LLMCallContext:
    return LLMCallContext(
        task_id="T-1",
        trace_id="TRACE-1",
        node_name="create_plan",
        prompt_version="planner-v1",
        schema_version="task-plan-v1",
    )


def _call(provider: DeepSeekProvider) -> Output:
    return provider.generate_structured(
        messages=[LLMMessage(role="user", content="untrusted task data")],
        output_schema=Output,
        context=_context(),
        options=LLMGenerationOptions(),
    ).parsed_output


def test_deepseek_decodes_usage_and_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Trace-ID"] == "TRACE-1"
        return httpx.Response(
            200,
            json={
                "id": "REQ-1",
                "model": "deepseek-chat",
                "choices": [
                    {
                        "message": {"content": '{"answer":"ok"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = DeepSeekProvider(api_key="secret", model="deepseek-chat", client=client)

    assert _call(provider) == Output(answer="ok")


def test_deepseek_retries_429_then_succeeds() -> None:
    statuses = iter((429, 200))
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        if status == 429:
            return httpx.Response(status, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {},
            },
        )

    provider = DeepSeekProvider(
        api_key="secret",
        model="deepseek-chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
        sleeper=lambda _delay: None,
    )

    assert _call(provider).answer == "ok"
    assert calls == 2


def test_deepseek_does_not_retry_authentication_or_expose_secret() -> None:
    calls = 0
    secret = "never-log-this-key"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": secret}})

    provider = DeepSeekProvider(
        api_key=secret,
        model="deepseek-chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=2,
    )

    with pytest.raises(LLMAuthenticationError) as raised:
        _call(provider)

    assert calls == 1
    assert secret not in str(raised.value)


def test_deepseek_reports_invalid_provider_json() -> None:
    provider = DeepSeekProvider(
        api_key="secret",
        model="deepseek-chat",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not-json"))
        ),
    )

    with pytest.raises(LLMInvalidResponseError):
        _call(provider)


def test_deepseek_rate_limit_exhaustion_is_typed() -> None:
    provider = DeepSeekProvider(
        api_key="secret",
        model="deepseek-chat",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(429, content=json.dumps({}))
            )
        ),
        max_retries=0,
    )

    with pytest.raises(LLMRateLimitError):
        _call(provider)


@pytest.mark.parametrize("status_code", [502, 503, 504])
def test_deepseek_retries_transient_gateway_statuses(status_code: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={})

    provider = DeepSeekProvider(
        api_key="secret",
        model="deepseek-chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
        sleeper=lambda _delay: None,
    )

    with pytest.raises(LLMUnavailableError):
        _call(provider)

    assert calls == 2


def test_deepseek_retries_timeout_then_exhausts() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    provider = DeepSeekProvider(
        api_key="secret",
        model="deepseek-chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
        sleeper=lambda _delay: None,
    )

    with pytest.raises(LLMTimeoutError):
        _call(provider)

    assert calls == 2
