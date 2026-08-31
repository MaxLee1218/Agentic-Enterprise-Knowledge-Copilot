"""Deterministic structured LLM used by unit, contract, and integration tests."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel

from copilot.llm.structured_output import parse_structured_output, structured_output_fingerprint
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
    LLMProviderError,
    LLMUsage,
    StructuredLLMResult,
)

TModel = TypeVar("TModel", bound=BaseModel)
MockResponse = str | bytes | Mapping[str, object] | BaseModel | LLMProviderError


@dataclass(frozen=True, slots=True)
class MockLLMCall:
    """Captured complete prompt available only to tests."""

    messages: tuple[LLMMessage, ...]
    schema_name: str
    context: LLMCallContext
    options: LLMGenerationOptions


class MockLLM:
    """Return queued or node-specific outcomes while recording every request."""

    def __init__(
        self,
        responses: Sequence[MockResponse] = (),
        *,
        responses_by_node: Mapping[str, Sequence[MockResponse]] | None = None,
        model: str = "mock-structured-v1",
    ) -> None:
        self._responses = deque(responses)
        self._by_node = {name: deque(values) for name, values in (responses_by_node or {}).items()}
        self._node_calls: defaultdict[str, int] = defaultdict(int)
        self._model = model
        self.calls: list[MockLLMCall] = []

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        output_schema: type[TModel],
        context: LLMCallContext,
        options: LLMGenerationOptions,
    ) -> StructuredLLMResult[TModel]:
        """Return the next deterministic result or raise its configured safe error."""
        started = perf_counter()
        self.calls.append(
            MockLLMCall(
                messages=tuple(messages),
                schema_name=output_schema.__name__,
                context=context,
                options=options,
            )
        )
        self._node_calls[context.node_name] += 1
        queue = self._by_node.get(context.node_name)
        if queue:
            response = queue.popleft()
        elif self._responses:
            response = self._responses.popleft()
        else:
            raise AssertionError(
                f"MockLLM has no response for node '{context.node_name}' "
                f"call {self._node_calls[context.node_name]}"
            )
        if isinstance(response, LLMProviderError):
            raise response
        parsed = parse_structured_output(response, output_schema)
        raw_output_chars, raw_output_hash = structured_output_fingerprint(response)
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        return StructuredLLMResult[TModel](
            parsed_output=parsed,
            provider="mock",
            model=self._model,
            latency_ms=latency_ms,
            usage=LLMUsage(),
            finish_reason="stop",
            request_id=f"mock-{len(self.calls)}",
            attempts=1,
            raw_output_chars=raw_output_chars,
            raw_output_hash=raw_output_hash,
        )


__all__ = ["MockLLM", "MockLLMCall", "MockResponse"]
