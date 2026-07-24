"""Synchronous Enterprise RAG client with bounded validation and transport retry."""

from __future__ import annotations

import errno
import logging
import re
from collections.abc import Callable, Mapping
from time import monotonic, sleep
from typing import Any, Literal, Protocol
from uuid import uuid4

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from copilot.contracts import JsonObject
from copilot.tools.knowledge.errors import (
    RAGAuthenticationError,
    RAGError,
    RAGInternalError,
    RAGInvalidResponseError,
    RAGTimeoutError,
    RAGUnavailableError,
)
from copilot.tools.knowledge.schemas import (
    KnowledgeContext,
    KnowledgeHealthResult,
    KnowledgeResult,
    KnowledgeSource,
)

LOGGER = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_RESET_ERRNOS = frozenset(
    value
    for value in (
        errno.ECONNRESET,
        getattr(errno, "WSAECONNRESET", None),
        10054,
    )
    if value is not None
)


class KnowledgeClient(Protocol):
    """Port consumed by the governed Knowledge Tool."""

    def health_check(self, *, trace_id: str | None = None) -> KnowledgeHealthResult:
        """Return validated Enterprise RAG health."""
        ...

    def ask(
        self,
        question: str,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeResult:
        """Return one validated normalized knowledge answer."""
        ...

    def close(self) -> None:
        """Release owned resources."""
        ...


class _SourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: StrictInt | None
    source: StrictStr | None
    metadata: dict[str, JsonValue] | None
    text_preview: StrictStr | None


class _ContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: StrictStr
    source: StrictStr | None
    chunk_id: StrictStr | None
    score: StrictInt | StrictFloat | None
    metadata: dict[str, JsonValue] | None


class _AskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: StrictStr
    sources: list[_SourcePayload]
    contexts: list[_ContextPayload]
    route: Literal["rag"]
    latency_ms: StrictInt | StrictFloat = Field(ge=0)
    rag_trace_id: StrictStr


class _HealthPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StrictStr | None = None
    healthy: StrictBool | None = None
    trace_id: StrictStr | None = None
    rag_trace_id: StrictStr | None = None
    request_id: StrictStr | None = None
    service: StrictStr | None = None
    version: StrictStr | None = None

    @model_validator(mode="after")
    def validate_health(self) -> _HealthPayload:
        """Accept only the two documented health response families."""
        status_healthy = self.status is not None and self.status.strip().lower() in {
            "ok",
            "healthy",
        }
        if self.healthy is False or (self.healthy is not True and not status_healthy):
            raise ValueError("health response does not report a healthy service")
        return self


class HttpKnowledgeClient:
    """HTTPX implementation of the synchronous KnowledgeClient port."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_attempts: int,
        retry_base_delay_seconds: float,
        user_agent: str,
        trace_header: str,
        http_client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        clean_base_url = base_url.strip().rstrip("/")
        if not clean_base_url:
            raise ValueError("base_url must not be blank")
        parsed_base_url = httpx.URL(clean_base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.host:
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between one and three")
        if retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must be non-negative")
        if not user_agent.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in user_agent
        ):
            raise ValueError("user_agent is invalid")
        if not _HTTP_HEADER_NAME.fullmatch(trace_header.strip()):
            raise ValueError("trace_header is invalid")
        self.base_url = clean_base_url
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.user_agent = user_agent.strip()
        self.trace_header = trace_header.strip()
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None
        self._sleeper = sleeper
        self._clock = clock

    def health_check(self, *, trace_id: str | None = None) -> KnowledgeHealthResult:
        """Call and validate ``GET /health`` without creating business Evidence."""
        request_trace_id = _trace_id(trace_id)
        started = self._clock()
        LOGGER.info(
            "RAG health check started",
            extra={
                "event": "rag_health_started",
                "rag_base_url": self.base_url,
                "trace_id": request_trace_id,
            },
        )
        try:
            response, attempts = self._request("GET", "/health", request_trace_id)
            payload = self._parse_health(response, request_trace_id, attempts)
            latency_ms = _elapsed_ms(started, self._clock())
            rag_trace_id = self._response_trace_id(response, payload, request_trace_id)
            result = KnowledgeHealthResult(
                healthy=True,
                status=(payload.status or "healthy").strip().lower(),
                latency_ms=latency_ms,
                rag_trace_id=rag_trace_id,
            )
        except RAGError as error:
            LOGGER.warning(
                "RAG health check failed",
                extra={
                    "event": "rag_health_failed",
                    "rag_base_url": self.base_url,
                    "trace_id": error.trace_id,
                    "status_code": error.status_code,
                    "attempt": error.attempts,
                    "error_type": type(error).__name__,
                    "retryable": error.retryable,
                },
            )
            raise
        LOGGER.info(
            "RAG health check succeeded",
            extra={
                "event": "rag_health_succeeded",
                "rag_base_url": self.base_url,
                "trace_id": request_trace_id,
                "rag_trace_id": result.rag_trace_id,
                "latency_ms": result.latency_ms,
            },
        )
        return result

    def ask(
        self,
        question: str,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeResult:
        """Call and validate ``POST /ask`` using the documented minimum request."""
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("question must not be blank")
        request_trace_id = _trace_id(trace_id)
        started = self._clock()
        LOGGER.info(
            "RAG ask started",
            extra={
                "event": "rag_ask_started",
                "rag_base_url": self.base_url,
                "trace_id": request_trace_id,
                "question_length": len(clean_question),
            },
        )
        try:
            response, attempts = self._request(
                "POST",
                "/ask",
                request_trace_id,
                json={"question": clean_question},
            )
            payload = self._parse_ask(response, request_trace_id, attempts)
            client_latency_ms = _elapsed_ms(started, self._clock())
            try:
                result = KnowledgeResult(
                    answer=payload.answer,
                    sources=tuple(_normalize_source(item) for item in payload.sources),
                    contexts=tuple(_normalize_context(item) for item in payload.contexts),
                    route=payload.route,
                    latency_ms=payload.latency_ms,
                    rag_trace_id=payload.rag_trace_id,
                )
            except (ValidationError, ValueError) as exc:
                raise RAGInvalidResponseError(
                    "Enterprise RAG returned semantically invalid knowledge data",
                    trace_id=request_trace_id,
                    status_code=response.status_code,
                    attempts=attempts,
                ) from exc
        except RAGError as error:
            LOGGER.warning(
                "RAG ask failed",
                extra={
                    "event": "rag_ask_failed",
                    "rag_base_url": self.base_url,
                    "trace_id": error.trace_id,
                    "status_code": error.status_code,
                    "attempt": error.attempts,
                    "error_type": type(error).__name__,
                    "retryable": error.retryable,
                    "question_length": len(clean_question),
                },
            )
            raise
        LOGGER.info(
            "RAG ask succeeded",
            extra={
                "event": "rag_ask_succeeded",
                "rag_base_url": self.base_url,
                "trace_id": request_trace_id,
                "rag_trace_id": result.rag_trace_id,
                "latency_ms": result.latency_ms,
                "client_latency_ms": client_latency_ms,
                "route": result.route,
                "source_count": len(result.sources),
            },
        )
        return result

    def close(self) -> None:
        """Close the internally created HTTPX client."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpKnowledgeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        trace_id: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> tuple[httpx.Response, int]:
        headers = {
            self.trace_header: trace_id,
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    timeout=self.timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                if attempt < self.max_attempts:
                    self._retry(attempt, trace_id, type(exc).__name__)
                    continue
                raise RAGTimeoutError(
                    "Enterprise RAG request timed out",
                    trace_id=trace_id,
                    attempts=attempt,
                    retryable=True,
                ) from exc
            except httpx.NetworkError as exc:
                reset = _is_connection_reset(exc)
                if reset and attempt < self.max_attempts:
                    self._retry(attempt, trace_id, "connection_reset")
                    continue
                raise RAGUnavailableError(
                    "Enterprise RAG service is unavailable",
                    trace_id=trace_id,
                    attempts=attempt,
                    retryable=reset,
                    safe_details={"connection_reset": reset},
                ) from exc

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < self.max_attempts:
                    self._retry(attempt, trace_id, f"http_{response.status_code}")
                    continue
                raise RAGInternalError(
                    "Enterprise RAG service returned a temporary server error",
                    trace_id=trace_id,
                    status_code=response.status_code,
                    attempts=attempt,
                    retryable=True,
                )
            self._raise_for_status(response, trace_id, attempt)
            return response, attempt
        raise AssertionError("bounded request loop terminated unexpectedly")

    def _retry(self, attempt: int, trace_id: str, error_type: str) -> None:
        delay = self.retry_base_delay_seconds * (2 ** (attempt - 1))
        LOGGER.warning(
            "RAG request will retry",
            extra={
                "event": "rag_retry_scheduled",
                "trace_id": trace_id,
                "attempt": attempt,
                "max_attempts": self.max_attempts,
                "error_type": error_type,
                "retryable": True,
            },
        )
        self._sleeper(delay)

    @staticmethod
    def _raise_for_status(response: httpx.Response, trace_id: str, attempt: int) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise RAGAuthenticationError(
                "Enterprise RAG authentication or authorization failed",
                trace_id=trace_id,
                status_code=status,
                attempts=attempt,
            )
        if 400 <= status < 500:
            raise RAGInvalidResponseError(
                "Enterprise RAG rejected the request contract",
                trace_id=trace_id,
                status_code=status,
                attempts=attempt,
            )
        if status >= 500:
            raise RAGInternalError(
                "Enterprise RAG returned an internal server error",
                trace_id=trace_id,
                status_code=status,
                attempts=attempt,
            )
        raise RAGInvalidResponseError(
            "Enterprise RAG returned an unsupported HTTP status",
            trace_id=trace_id,
            status_code=status,
            attempts=attempt,
        )

    @staticmethod
    def _parse_ask(response: httpx.Response, trace_id: str, attempts: int) -> _AskPayload:
        raw = _response_json(response, trace_id, attempts)
        try:
            payload = _AskPayload.model_validate(raw)
            if not payload.answer.strip():
                raise ValueError("answer must not be blank")
            return payload
        except (ValidationError, ValueError) as exc:
            LOGGER.warning(
                "RAG ask response schema is invalid",
                extra={
                    "event": "rag_response_invalid",
                    "trace_id": trace_id,
                    "status_code": response.status_code,
                },
            )
            raise RAGInvalidResponseError(
                "Enterprise RAG returned an invalid ask response",
                trace_id=trace_id,
                status_code=response.status_code,
                attempts=attempts,
            ) from exc

    @staticmethod
    def _parse_health(response: httpx.Response, trace_id: str, attempts: int) -> _HealthPayload:
        raw = _response_json(response, trace_id, attempts)
        try:
            return _HealthPayload.model_validate(raw)
        except ValidationError as exc:
            raise RAGInvalidResponseError(
                "Enterprise RAG returned an invalid health response",
                trace_id=trace_id,
                status_code=response.status_code,
                attempts=attempts,
            ) from exc

    def _response_trace_id(
        self,
        response: httpx.Response,
        payload: _HealthPayload,
        fallback: str,
    ) -> str:
        header_trace = response.headers.get(self.trace_header)
        body_trace = payload.rag_trace_id or payload.trace_id or payload.request_id
        for candidate in (header_trace, body_trace, fallback):
            if candidate is not None and candidate.strip():
                return candidate.strip()
        return fallback


class MockKnowledgeClient:
    """Configurable, network-free KnowledgeClient test double."""

    def __init__(
        self,
        *,
        health_result: KnowledgeHealthResult | None = None,
        ask_result: KnowledgeResult | None = None,
        health_error: RAGError | None = None,
        ask_error: RAGError | None = None,
    ) -> None:
        self.health_result = health_result or KnowledgeHealthResult(
            healthy=True,
            status="ok",
            latency_ms=0,
            rag_trace_id="mock-health-trace",
        )
        self.ask_result = ask_result or KnowledgeResult(
            answer="Mock knowledge answer",
            sources=(),
            contexts=(),
            route="mock",
            latency_ms=0,
            rag_trace_id="mock-ask-trace",
        )
        self.health_error = health_error
        self.ask_error = ask_error
        self.health_call_count = 0
        self.ask_call_count = 0
        self.last_question: str | None = None
        self.last_trace_id: str | None = None

    def health_check(self, *, trace_id: str | None = None) -> KnowledgeHealthResult:
        self.health_call_count += 1
        self.last_trace_id = trace_id
        if self.health_error is not None:
            raise self.health_error
        return self.health_result

    def ask(
        self,
        question: str,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeResult:
        self.ask_call_count += 1
        self.last_question = question
        self.last_trace_id = trace_id
        if self.ask_error is not None:
            raise self.ask_error
        return self.ask_result

    def close(self) -> None:
        """No-op because the mock owns no network resources."""


def _response_json(response: httpx.Response, trace_id: str, attempts: int) -> object:
    try:
        raw = response.json()
    except ValueError as exc:
        raise RAGInvalidResponseError(
            "Enterprise RAG returned invalid JSON",
            trace_id=trace_id,
            status_code=response.status_code,
            attempts=attempts,
        ) from exc
    if not isinstance(raw, dict):
        raise RAGInvalidResponseError(
            "Enterprise RAG response must be a JSON object",
            trace_id=trace_id,
            status_code=response.status_code,
            attempts=attempts,
        )
    return raw


def _normalize_source(item: _SourcePayload) -> KnowledgeSource:
    return KnowledgeSource(
        index=item.index,
        source=item.source,
        metadata=JsonObject(item.metadata) if item.metadata is not None else None,
        text_preview=item.text_preview,
    )


def _normalize_context(item: _ContextPayload) -> KnowledgeContext:
    return KnowledgeContext(
        content=item.content,
        source=item.source,
        chunk_id=item.chunk_id,
        score=item.score,
        metadata=JsonObject(item.metadata) if item.metadata is not None else None,
    )


def _trace_id(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return str(uuid4())


def _elapsed_ms(started: float, completed: float) -> int:
    return max(0, round((completed - started) * 1000))


def _is_connection_reset(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError):
            error_number = current.errno
            if error_number in _RESET_ERRNOS:
                return True
            if isinstance(current, ConnectionResetError):
                return True
        current = current.__cause__ or current.__context__
    return False


__all__ = [
    "HttpKnowledgeClient",
    "KnowledgeClient",
    "MockKnowledgeClient",
]
