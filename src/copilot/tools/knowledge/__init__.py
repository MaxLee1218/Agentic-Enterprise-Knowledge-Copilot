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
from copilot.tools.knowledge.policy_bundle import (
    APPolicyBundleError,
    LoadedAPPolicyBundle,
    load_ap_policy_bundle,
    publish_ap_policy_bundle,
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
    "APPolicyBundleError",
    "KnowledgeClient",
    "KnowledgeContext",
    "KnowledgeHealthResult",
    "KnowledgeResult",
    "KnowledgeSource",
    "KnowledgeTool",
    "LoadedAPPolicyBundle",
    "MockKnowledgeClient",
    "RAGAuthenticationError",
    "RAGError",
    "RAGInternalError",
    "RAGInvalidResponseError",
    "RAGTimeoutError",
    "RAGUnavailableError",
    "load_ap_policy_bundle",
    "publish_ap_policy_bundle",
]
