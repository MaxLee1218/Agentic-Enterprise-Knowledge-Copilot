"""DeepSeek chat-completions adapter with bounded retry and structured output."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from time import perf_counter, sleep
from typing import TypeVar

import httpx
from pydantic import BaseModel

from copilot.llm.structured_output import parse_structured_output, structured_output_fingerprint
from copilot.services.llm import (
    LLMAuthenticationError,
    LLMCallContext,
    LLMContextLimitError,
    LLMGenerationOptions,
    LLMInternalError,
    LLMInvalidResponseError,
    LLMMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseDiagnostics,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMUsage,
    StructuredLLMResult,
)

TModel = TypeVar("TModel", bound=BaseModel)
_RETRYABLE_STATUS = {429, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


class DeepSeekProvider:
    """Synchronous provider adapter suitable for the synchronous LangGraph runtime."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
        connect_timeout_seconds: float = 10,
        read_timeout_seconds: float = 60,
        max_retries: int = 2,
        retry_base_delay_seconds: float = 0.2,
        user_agent: str = "agentic-enterprise-knowledge-copilot/0.1.0",
        trace_header: str = "X-Trace-ID",
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        clean_key = api_key.strip()
        if not clean_key:
            from copilot.services.llm import LLMConfigurationError

            raise LLMConfigurationError("DeepSeek API key is not configured")
        if not model.strip() or not base_url.strip():
            from copilot.services.llm import LLMConfigurationError

            raise LLMConfigurationError("DeepSeek model and base URL must be configured")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self._model = model
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay_seconds
        self._trace_header = trace_header
        self._sleeper = sleeper
        self._owned_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds,
                read=read_timeout_seconds,
                write=read_timeout_seconds,
                pool=connect_timeout_seconds,
            ),
            headers={
                "Authorization": f"Bearer {clean_key}",
                "User-Agent": user_agent,
                "Content-Type": "application/json",
            },
        )

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        output_schema: type[TModel],
        context: LLMCallContext,
        options: LLMGenerationOptions,
    ) -> StructuredLLMResult[TModel]:
        """Call DeepSeek and validate the returned JSON against ``output_schema``."""
        if not messages:
            raise ValueError("messages must not be empty")
        payload = {
            "model": self._model,
            "messages": [message.model_dump(mode="json") for message in messages],
            "temperature": options.temperature,
            "max_tokens": options.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        started = perf_counter()
        last_error: LLMProviderError | None = None
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.post(
                    self._endpoint,
                    json=payload,
                    headers={self._trace_header: context.trace_id},
                )
                response.raise_for_status()
                result = self._decode(response, output_schema, attempt, started)
                self._log(context, result=result)
                return result
            except httpx.TimeoutException as exc:
                last_error = LLMTimeoutError("DeepSeek request timed out", attempts=attempt)
                cause: Exception = exc
            except httpx.TransportError as exc:
                last_error = LLMUnavailableError(
                    "DeepSeek transport is unavailable", attempts=attempt
                )
                cause = exc
            except httpx.HTTPStatusError as exc:
                last_error = self._http_error(exc.response.status_code, attempt)
                cause = exc
            except LLMProviderError as exc:
                exc.attempts = attempt
                if exc.diagnostics is None:
                    exc.attach_diagnostics(
                        LLMResponseDiagnostics(
                            provider="deepseek",
                            model=self._model,
                            latency_ms=max(0, round((perf_counter() - started) * 1000)),
                        )
                    )
                self._log(context, error=exc)
                raise
            if not last_error.retryable or attempt >= attempts:
                last_error.attach_diagnostics(
                    LLMResponseDiagnostics(
                        provider="deepseek",
                        model=self._model,
                        latency_ms=max(0, round((perf_counter() - started) * 1000)),
                    )
                )
                self._log(context, error=last_error)
                raise last_error from cause
            delay = self._retry_base_delay * (2 ** (attempt - 1))
            if delay:
                self._sleeper(delay)
        raise LLMInternalError("DeepSeek retry loop terminated unexpectedly")

    def close(self) -> None:
        """Close only a client owned by this provider."""
        if self._owned_client:
            self._client.close()

    def __enter__(self) -> DeepSeekProvider:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _decode(
        self,
        response: httpx.Response,
        output_schema: type[TModel],
        attempts: int,
        started: float,
    ) -> StructuredLLMResult[TModel]:
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMInvalidResponseError("DeepSeek returned invalid JSON") from exc
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError(
                "DeepSeek response omitted the structured message"
            ) from exc
        if not isinstance(content, str):
            raise LLMInvalidResponseError("DeepSeek structured message was not text")
        usage_raw = body.get("usage", {})
        if not isinstance(usage_raw, dict):
            usage_raw = {}
        usage = LLMUsage(
            input_tokens=_safe_int(usage_raw.get("prompt_tokens")),
            output_tokens=_safe_int(usage_raw.get("completion_tokens")),
            total_tokens=_safe_int(usage_raw.get("total_tokens")),
        )
        finish_reason = (
            str(choice["finish_reason"]) if choice.get("finish_reason") is not None else None
        )
        request_id = str(body["id"]) if body.get("id") is not None else None
        model = str(body.get("model") or self._model)
        raw_output_chars, raw_output_hash = structured_output_fingerprint(content)
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        try:
            parsed = parse_structured_output(content, output_schema)
        except LLMProviderError as exc:
            parser_diagnostics = exc.diagnostics
            exc.attach_diagnostics(
                LLMResponseDiagnostics(
                    provider="deepseek",
                    model=model,
                    latency_ms=latency_ms,
                    usage=usage,
                    finish_reason=finish_reason,
                    request_id=request_id,
                    raw_output_chars=raw_output_chars,
                    raw_output_hash=raw_output_hash,
                    parse_status=(
                        parser_diagnostics.parse_status
                        if parser_diagnostics is not None
                        else "failed"
                    ),
                    schema_status=(
                        parser_diagnostics.schema_status
                        if parser_diagnostics is not None
                        else "not_attempted"
                    ),
                    validation_errors=(
                        parser_diagnostics.validation_errors
                        if parser_diagnostics is not None
                        else ()
                    ),
                )
            )
            raise
        return StructuredLLMResult[TModel](
            parsed_output=parsed,
            provider="deepseek",
            model=model,
            latency_ms=latency_ms,
            usage=usage,
            finish_reason=finish_reason,
            request_id=request_id,
            attempts=attempts,
            raw_output_chars=raw_output_chars,
            raw_output_hash=raw_output_hash,
        )

    @staticmethod
    def _http_error(status_code: int, attempts: int) -> LLMProviderError:
        if status_code in {401, 403}:
            return LLMAuthenticationError(
                "DeepSeek authentication or authorization failed", attempts=attempts
            )
        if status_code == 429:
            return LLMRateLimitError("DeepSeek rate limit was reached", attempts=attempts)
        if status_code in {502, 503, 504}:
            return LLMUnavailableError("DeepSeek service is unavailable", attempts=attempts)
        if status_code == 400:
            return LLMContextLimitError(
                "DeepSeek rejected the request or context size", attempts=attempts
            )
        return LLMInvalidResponseError(
            f"DeepSeek returned HTTP status {status_code}", attempts=attempts
        )

    @staticmethod
    def _log(
        context: LLMCallContext,
        *,
        result: StructuredLLMResult[TModel] | None = None,
        error: LLMProviderError | None = None,
    ) -> None:
        diagnostics = error.diagnostics if error is not None else None
        fields = {
            "task_id": context.task_id,
            "trace_id": context.trace_id,
            "node_name": context.node_name,
            "prompt_version": context.prompt_version,
            "schema_version": context.schema_version,
            "attempt": result.attempts if result else error.attempts if error else context.attempt,
            "workflow_attempt": context.attempt,
            "status": "error" if error else "success",
            "error_type": error.code.value if error else None,
            "provider": result.provider if result else "deepseek",
            "model": result.model if result else diagnostics.model if diagnostics else None,
            "latency_ms": (
                result.latency_ms if result else diagnostics.latency_ms if diagnostics else None
            ),
            "finish_reason": (
                result.finish_reason
                if result
                else diagnostics.finish_reason
                if diagnostics
                else None
            ),
            "token_usage": (
                result.usage.model_dump()
                if result
                else diagnostics.usage.model_dump()
                if diagnostics
                else None
            ),
            "raw_output_chars": (
                result.raw_output_chars
                if result
                else diagnostics.raw_output_chars
                if diagnostics
                else 0
            ),
            "raw_output_hash": (
                result.raw_output_hash
                if result
                else diagnostics.raw_output_hash
                if diagnostics
                else None
            ),
            "parse_status": (
                "passed" if result else diagnostics.parse_status if diagnostics else "not_attempted"
            ),
            "schema_status": (
                "passed"
                if result
                else diagnostics.schema_status
                if diagnostics
                else "not_attempted"
            ),
            "validation_errors": (
                [item.model_dump(mode="json") for item in diagnostics.validation_errors]
                if diagnostics
                else []
            ),
        }
        LOGGER.info("structured_llm_call", extra={"llm": fields})


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


__all__ = ["DeepSeekProvider"]
