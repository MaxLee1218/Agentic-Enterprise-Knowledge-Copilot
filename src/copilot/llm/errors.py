"""Public LLM error API backed by the application-owned provider port."""

from copilot.services.llm import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMContextLimitError,
    LLMErrorCode,
    LLMInternalError,
    LLMInvalidResponseError,
    LLMProviderError,
    LLMRateLimitError,
    LLMSchemaValidationError,
    LLMTimeoutError,
    LLMUnavailableError,
)

__all__ = [
    "LLMAuthenticationError",
    "LLMConfigurationError",
    "LLMContextLimitError",
    "LLMErrorCode",
    "LLMInternalError",
    "LLMInvalidResponseError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMSchemaValidationError",
    "LLMTimeoutError",
    "LLMUnavailableError",
]
