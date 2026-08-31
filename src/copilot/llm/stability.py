"""Safe observation wrapper used by opt-in planner stability runs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    StructuredLLMResult,
)

TModel = TypeVar("TModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredCallObservation:
    """Non-sensitive metadata for one structured provider invocation."""

    node_name: str
    prompt_version: str
    workflow_attempt: int
    provider_attempts: int
    provider: str | None
    model: str | None
    latency_ms: int | None
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    raw_output_chars: int
    raw_output_hash: str | None
    parse_status: str
    schema_status: str
    error_code: str | None = None


class ObservedLLMProvider(LLMProvider):
    """Delegate provider calls while retaining only bounded operational diagnostics."""

    def __init__(self, delegate: LLMProvider) -> None:
        self._delegate = delegate
        self.observations: list[StructuredCallObservation] = []

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        output_schema: type[TModel],
        context: LLMCallContext,
        options: LLMGenerationOptions,
    ) -> StructuredLLMResult[TModel]:
        """Record safe success/error facts without persisting prompts or raw output."""
        try:
            result = self._delegate.generate_structured(
                messages=messages,
                output_schema=output_schema,
                context=context,
                options=options,
            )
        except LLMProviderError as exc:
            diagnostics = exc.diagnostics
            usage = diagnostics.usage if diagnostics is not None else None
            self.observations.append(
                StructuredCallObservation(
                    node_name=context.node_name,
                    prompt_version=context.prompt_version,
                    workflow_attempt=context.attempt,
                    provider_attempts=exc.attempts,
                    provider=diagnostics.provider if diagnostics is not None else None,
                    model=diagnostics.model if diagnostics is not None else None,
                    latency_ms=diagnostics.latency_ms if diagnostics is not None else None,
                    finish_reason=(diagnostics.finish_reason if diagnostics is not None else None),
                    input_tokens=usage.input_tokens if usage is not None else 0,
                    output_tokens=usage.output_tokens if usage is not None else 0,
                    total_tokens=usage.total_tokens if usage is not None else 0,
                    raw_output_chars=(
                        diagnostics.raw_output_chars if diagnostics is not None else 0
                    ),
                    raw_output_hash=(
                        diagnostics.raw_output_hash if diagnostics is not None else None
                    ),
                    parse_status=(
                        diagnostics.parse_status if diagnostics is not None else "not_attempted"
                    ),
                    schema_status=(
                        diagnostics.schema_status if diagnostics is not None else "not_attempted"
                    ),
                    error_code=exc.code.value,
                )
            )
            raise
        self.observations.append(
            StructuredCallObservation(
                node_name=context.node_name,
                prompt_version=context.prompt_version,
                workflow_attempt=context.attempt,
                provider_attempts=result.attempts,
                provider=result.provider,
                model=result.model,
                latency_ms=result.latency_ms,
                finish_reason=result.finish_reason,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                total_tokens=result.usage.total_tokens,
                raw_output_chars=result.raw_output_chars,
                raw_output_hash=result.raw_output_hash,
                parse_status="passed",
                schema_status="passed",
            )
        )
        return result


__all__ = ["ObservedLLMProvider", "StructuredCallObservation"]
