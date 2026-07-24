"""Enterprise RAG client and governed Knowledge Tool adapter."""

from copilot.tools.knowledge.client import (
    HttpKnowledgeClient,
    KnowledgeClient,
    MockKnowledgeClient,
)
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
from copilot.tools.knowledge.tool import KnowledgeTool

__all__ = [
    "HttpKnowledgeClient",
    "KnowledgeClient",
    "KnowledgeContext",
    "KnowledgeHealthResult",
    "KnowledgeResult",
    "KnowledgeSource",
    "KnowledgeTool",
    "MockKnowledgeClient",
    "RAGAuthenticationError",
    "RAGError",
    "RAGInternalError",
    "RAGInvalidResponseError",
    "RAGTimeoutError",
    "RAGUnavailableError",
]
