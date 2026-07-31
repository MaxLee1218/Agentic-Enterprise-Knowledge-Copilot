"""All LLM providers expose the same structured result and error semantics."""

import httpx
from pydantic import BaseModel

from copilot.llm.deepseek import DeepSeekProvider
from copilot.llm.mock import MockLLM
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
    StructuredLLMResult,
)


class Output(BaseModel):
    value: int


def _invoke(provider: MockLLM | DeepSeekProvider) -> StructuredLLMResult[Output]:
    return provider.generate_structured(
        messages=[LLMMessage(role="user", content="untrusted data")],
        output_schema=Output,
        context=LLMCallContext(
            task_id="T-1",
            trace_id="TRACE-1",
            node_name="contract",
            prompt_version="prompt-v1",
            schema_version="schema-v1",
        ),
        options=LLMGenerationOptions(),
    )


def test_mock_and_deepseek_return_the_same_internal_result_shape() -> None:
    mock = MockLLM([{"value": 1}])
    deepseek = DeepSeekProvider(
        api_key="secret",
        model="deepseek-chat",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "id": "REQ-1",
                        "model": "deepseek-chat",
                        "choices": [{"message": {"content": '{"value":1}'}}],
                        "usage": {},
                    },
                )
            )
        ),
    )

    mock_result = _invoke(mock)
    deepseek_result = _invoke(deepseek)

    assert mock_result.parsed_output == deepseek_result.parsed_output
    assert set(mock_result.model_dump()) == set(deepseek_result.model_dump())
