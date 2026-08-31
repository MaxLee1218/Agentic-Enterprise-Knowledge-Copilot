"""Application-owned ports and values for structured LLM calls."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

TModel = TypeVar("TModel", bound=BaseModel)


class LLMMessage(BaseModel):
    """One trusted system or untrusted data message sent to a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class LLMCallContext(BaseModel):
    """Non-secret correlation and version metadata for one model call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    node_name: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)


class LLMGenerationOptions(BaseModel):
    """Bounded generation controls that model output cannot override."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float = Field(default=0, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1, le=65536)


class LLMUsage(BaseModel):
    """Normalized token accounting returned by every provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class LLMValidationIssue(BaseModel):
    """Bounded schema failure detail that never contains the rejected value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_path: str = Field(min_length=1, max_length=500)
    error_type: str = Field(min_length=1, max_length=100)
    expected: str | None = Field(default=None, max_length=200)
    received_type: str | None = Field(default=None, max_length=100)


class LLMResponseDiagnostics(BaseModel):
    """Safe provider-response forensics with hashes instead of raw content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    latency_ms: int | None = Field(default=None, ge=0)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    finish_reason: str | None = Field(default=None, max_length=100)
    request_id: str | None = Field(default=None, max_length=500)
    raw_output_chars: int = Field(default=0, ge=0)
    raw_output_hash: str | None = Field(default=None, max_length=100)
    parse_status: Literal["not_attempted", "passed", "failed"] = "not_attempted"
    schema_status: Literal["not_attempted", "passed", "failed"] = "not_attempted"
    validation_errors: tuple[LLMValidationIssue, ...] = ()


class LLMProviderMetadata(BaseModel):
    """Stable provider identity without credentials or transport details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class StructuredLLMResult(BaseModel, Generic[TModel]):
    """Schema-validated model output plus safe operational metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parsed_output: TModel
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    finish_reason: str | None = None
    request_id: str | None = None
    attempts: int = Field(default=1, ge=1)
    raw_output_chars: int = Field(default=0, ge=0)
    raw_output_hash: str | None = Field(default=None, max_length=100)


class LLMErrorCode(StrEnum):
    """Stable error reasons used by retry and workflow routing."""

    CONFIGURATION = "LLM_CONFIGURATION_ERROR"
    AUTHENTICATION = "LLM_AUTHENTICATION_ERROR"
    RATE_LIMIT = "LLM_RATE_LIMIT_ERROR"
    TIMEOUT = "LLM_TIMEOUT_ERROR"
    UNAVAILABLE = "LLM_UNAVAILABLE_ERROR"
    INVALID_RESPONSE = "LLM_INVALID_RESPONSE_ERROR"
    SCHEMA_VALIDATION = "LLM_SCHEMA_VALIDATION_ERROR"
    CONTEXT_LIMIT = "LLM_CONTEXT_LIMIT_ERROR"
    TOKEN_BUDGET = "LLM_TOKEN_BUDGET_EXCEEDED"
    INTERNAL = "LLM_INTERNAL_ERROR"


class LLMProviderError(RuntimeError):
    """Safe provider failure with an explicit retry classification."""

    code = LLMErrorCode.INTERNAL
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        attempts: int = 1,
        diagnostics: LLMResponseDiagnostics | None = None,
        raw_output: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.diagnostics = diagnostics
        # This bounded in-memory value exists only for targeted repair. Providers and loggers must
        # never serialize it; durable diagnostics retain only length/hash and validation summaries.
        self.raw_output = raw_output

    def attach_diagnostics(self, diagnostics: LLMResponseDiagnostics) -> None:
        """Attach safe provider metadata before the error crosses the infrastructure boundary."""
        self.diagnostics = diagnostics


class LLMConfigurationError(LLMProviderError):
    code = LLMErrorCode.CONFIGURATION


class LLMAuthenticationError(LLMProviderError):
    code = LLMErrorCode.AUTHENTICATION


class LLMRateLimitError(LLMProviderError):
    code = LLMErrorCode.RATE_LIMIT
    retryable = True


class LLMTimeoutError(LLMProviderError):
    code = LLMErrorCode.TIMEOUT
    retryable = True


class LLMUnavailableError(LLMProviderError):
    code = LLMErrorCode.UNAVAILABLE
    retryable = True


class LLMInvalidResponseError(LLMProviderError):
    code = LLMErrorCode.INVALID_RESPONSE


class LLMSchemaValidationError(LLMProviderError):
    code = LLMErrorCode.SCHEMA_VALIDATION


class LLMContextLimitError(LLMProviderError):
    code = LLMErrorCode.CONTEXT_LIMIT


class LLMTokenBudgetExceededError(LLMProviderError):
    """Provider usage exceeded the deterministic configured output-token ceiling."""

    code = LLMErrorCode.TOKEN_BUDGET


class LLMInternalError(LLMProviderError):
    code = LLMErrorCode.INTERNAL


class LLMProvider(Protocol):
    """Replaceable synchronous provider port used by injected planning services."""

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        output_schema: type[TModel],
        context: LLMCallContext,
        options: LLMGenerationOptions,
    ) -> StructuredLLMResult[TModel]:
        """Return only after provider output passes the requested Pydantic schema."""
        ...
