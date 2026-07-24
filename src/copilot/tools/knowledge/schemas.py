"""Normalized, immutable contracts at the Enterprise RAG client boundary."""

from __future__ import annotations

from pydantic import Field, field_validator

from copilot.contracts import ImmutableContractModel, JsonObject


class KnowledgeSource(ImmutableContractModel):
    """Exact normalized ``Source`` from the frozen Enterprise RAG contract."""

    index: int | None
    source: str | None
    metadata: JsonObject | None
    text_preview: str | None

    @field_validator("source", "text_preview")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Trim non-null source text without changing nullable contract semantics."""
        if value is None:
            return None
        return value.strip()


class KnowledgeContext(ImmutableContractModel):
    """Exact normalized ``Context`` from the frozen Enterprise RAG contract."""

    content: str
    source: str | None
    chunk_id: str | None
    score: float | None
    metadata: JsonObject | None

    @field_validator("content", "source", "chunk_id")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        """Trim non-null context strings."""
        if value is None:
            return None
        return value.strip()


class KnowledgeResult(ImmutableContractModel):
    """Stable answer result independent of the RAG service's raw JSON."""

    answer: str = Field(min_length=1)
    sources: tuple[KnowledgeSource, ...] = ()
    contexts: tuple[KnowledgeContext, ...] = ()
    route: str = Field(default="unknown", min_length=1)
    latency_ms: float = Field(ge=0)
    rag_trace_id: str = Field(min_length=1)

    @field_validator("answer", "route", "rag_trace_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """Reject blank answers and identifiers before they cross the boundary."""
        clean = value.strip()
        if not clean:
            raise ValueError("required knowledge result text must not be blank")
        return clean


class KnowledgeHealthResult(ImmutableContractModel):
    """Stable health result observed by the Copilot HTTP client."""

    healthy: bool
    status: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    rag_trace_id: str = Field(min_length=1)

    @field_validator("status", "rag_trace_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        """Reject blank health status and trace identifiers."""
        clean = value.strip()
        if not clean:
            raise ValueError("health result text must not be blank")
        return clean


__all__ = [
    "KnowledgeContext",
    "KnowledgeHealthResult",
    "KnowledgeResult",
    "KnowledgeSource",
]
