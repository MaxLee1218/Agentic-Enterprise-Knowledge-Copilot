"""Safe, transport-independent failures for the Enterprise RAG boundary."""

from __future__ import annotations

from typing import Any


class RAGError(Exception):
    """Base error carrying bounded, non-sensitive RAG diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        trace_id: str,
        status_code: int | None = None,
        attempts: int = 1,
        retryable: bool = False,
        safe_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.trace_id = trace_id
        self.status_code = status_code
        self.attempts = attempts
        self.retryable = retryable
        self.safe_details = dict(safe_details or {})


class RAGUnavailableError(RAGError):
    """The Enterprise RAG service could not be reached."""


class RAGTimeoutError(RAGError):
    """The Enterprise RAG request exceeded its configured timeout."""


class RAGInvalidResponseError(RAGError):
    """The RAG response violated its bounded compatibility contract."""


class RAGAuthenticationError(RAGError):
    """The RAG service rejected authentication or authorization."""


class RAGInternalError(RAGError):
    """The RAG service returned an internal server failure."""


__all__ = [
    "RAGAuthenticationError",
    "RAGError",
    "RAGInternalError",
    "RAGInvalidResponseError",
    "RAGTimeoutError",
    "RAGUnavailableError",
]
