"""Deterministic MockLLM behavior and failure injection."""

import pytest
from pydantic import BaseModel

from copilot.llm.mock import MockLLM
from copilot.services.llm import (
    LLMAuthenticationError,
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
)


class Output(BaseModel):
    value: str


def context(node_name: str) -> LLMCallContext:
    return LLMCallContext(
        task_id="T-1",
        trace_id="TRACE-1",
        node_name=node_name,
        prompt_version="prompt-v1",
        schema_version="schema-v1",
    )


def test_mock_supports_sequence_and_node_specific_responses() -> None:
    provider = MockLLM(
        responses=[{"value": "fallback"}],
        responses_by_node={"planner": [{"value": "planned"}]},
    )
    messages = [LLMMessage(role="system", content="return JSON")]

    first = provider.generate_structured(
        messages=messages,
        output_schema=Output,
        context=context("planner"),
        options=LLMGenerationOptions(),
    )
    second = provider.generate_structured(
        messages=messages,
        output_schema=Output,
        context=context("other"),
        options=LLMGenerationOptions(),
    )

    assert first.parsed_output.value == "planned"
    assert second.parsed_output.value == "fallback"
    assert [call.schema_name for call in provider.calls] == ["Output", "Output"]


def test_mock_raises_configured_provider_error() -> None:
    provider = MockLLM([LLMAuthenticationError("authentication failed")])

    with pytest.raises(LLMAuthenticationError):
        provider.generate_structured(
            messages=[LLMMessage(role="system", content="return JSON")],
            output_schema=Output,
            context=context("planner"),
            options=LLMGenerationOptions(),
        )


def test_mock_fails_loudly_when_no_response_remains() -> None:
    provider = MockLLM()

    with pytest.raises(AssertionError, match="no response"):
        provider.generate_structured(
            messages=[LLMMessage(role="system", content="return JSON")],
            output_schema=Output,
            context=context("planner"),
            options=LLMGenerationOptions(),
        )
