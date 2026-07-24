"""Governed Knowledge Tool adapter for the frozen v1.0 retrieval contract."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    ErrorType,
    EvidenceContent,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    RiskLevel,
    ToolApprovalPolicy,
    ToolDefinition,
    ToolIdempotency,
    ToolTimeout,
)
from copilot.contracts.base import JsonMapping
from copilot.tools.base import EvidenceDraft, ToolExecutionContext, ToolExecutionOutput
from copilot.tools.exceptions import (
    ToolExecutionError,
    ToolPermissionError,
    ToolRuntimeError,
    ToolValidationError,
)
from copilot.tools.knowledge.client import KnowledgeClient
from copilot.tools.knowledge.errors import (
    RAGAuthenticationError,
    RAGInternalError,
    RAGInvalidResponseError,
    RAGTimeoutError,
    RAGUnavailableError,
)
from copilot.tools.knowledge.schemas import (
    KnowledgeContext,
    KnowledgeResult,
    KnowledgeSource,
)

LOGGER = logging.getLogger(__name__)
_ALLOWED_CLASSIFICATIONS = frozenset({"INTERNAL", "CONFIDENTIAL", "RESTRICTED"})

KNOWLEDGE_INPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "query",
        "tenant_id",
        "collection_ids",
        "supplier_ids",
        "date_range",
        "top_k",
        "index_snapshot_id",
    ],
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 1000},
        "tenant_id": {"type": "string", "minLength": 1},
        "collection_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "supplier_ids": {
            "type": "array",
            "maxItems": 100,
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "date_range": {
            "type": "object",
            "additionalProperties": False,
            "required": ["start", "end"],
            "properties": {
                "start": {"type": "string", "format": "date"},
                "end": {"type": "string", "format": "date"},
            },
        },
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
        "index_snapshot_id": {"type": "string", "minLength": 1},
    },
}

KNOWLEDGE_OUTPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matches", "match_count", "index_snapshot_id", "empty_result"],
    "properties": {
        "matches": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "document_id",
                    "document_version",
                    "chunk_id",
                    "excerpt",
                    "score",
                    "classification",
                    "checksum",
                ],
                "properties": {
                    "document_id": {"type": "string"},
                    "document_version": {"type": "string"},
                    "chunk_id": {"type": "string"},
                    "excerpt": {"type": "string", "maxLength": 4000},
                    "score": {"type": "number", "minimum": 0},
                    "classification": {
                        "type": "string",
                        "enum": ["INTERNAL", "CONFIDENTIAL", "RESTRICTED"],
                    },
                    "checksum": {"type": "string"},
                },
            },
        },
        "match_count": {"type": "integer", "minimum": 0},
        "index_snapshot_id": {"type": "string"},
        "empty_result": {"type": "boolean"},
    },
}


class KnowledgeTool:
    """Translate normalized RAG results into the frozen ``knowledge_search`` contract."""

    definition = ToolDefinition(
        tool_name="knowledge_search",
        tool_version="1.0.0-http",
        description=(
            "Retrieve supplier-quality policy sources through the approved Enterprise RAG "
            "service; read-only and restricted to the authorized v1.0 scope"
        ),
        input_schema=JsonObject(KNOWLEDGE_INPUT_SCHEMA),
        output_schema=JsonObject(KNOWLEDGE_OUTPUT_SCHEMA),
        risk_level=RiskLevel.LOW,
        timeout=ToolTimeout(attempt_seconds=10, overall_seconds=25),
        approval_policy=ToolApprovalPolicy(
            policy_id="knowledge-search-v1-policy",
            trigger_conditions=("restricted_scope",),
            approver_role="quality_data_approver",
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=("normalized_input", "tool_version", "index_snapshot_id"),
            reuse_window_seconds=300,
            side_effects="Read-only Enterprise RAG retrieval",
        ),
    )

    def __init__(self, client: KnowledgeClient) -> None:
        self._client = client
        self.call_count = 0

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Execute one authorized retrieval without exposing HTTP implementation details."""
        self.call_count += 1
        query = _required_text(arguments, "query")
        index_snapshot_id = _required_text(arguments, "index_snapshot_id")
        top_k = _required_int(arguments, "top_k")
        raw_trace_id = context.metadata.root.get("trace_id")
        trace_id = (
            raw_trace_id
            if isinstance(raw_trace_id, str) and raw_trace_id.strip()
            else context.call.tool_call_id
        )
        try:
            result = self._client.ask(query, trace_id=trace_id)
        except RAGTimeoutError as exc:
            raise ToolRuntimeError(
                error_code="KNOWLEDGE_TIMEOUT",
                error_type=ErrorType.TIMEOUT,
                message="Enterprise knowledge retrieval timed out",
                recoverable=False,
            ) from exc
        except RAGUnavailableError as exc:
            raise ToolExecutionError(
                error_code="KNOWLEDGE_UNAVAILABLE",
                message="Enterprise knowledge retrieval is unavailable",
                recoverable=False,
            ) from exc
        except RAGAuthenticationError as exc:
            raise ToolPermissionError(
                error_code="KNOWLEDGE_ACCESS_DENIED",
                message="Enterprise knowledge access was denied",
            ) from exc
        except RAGInvalidResponseError as exc:
            raise ToolExecutionError(
                error_code="KNOWLEDGE_INVALID_RESPONSE",
                message="Enterprise knowledge response violated its contract",
                recoverable=False,
            ) from exc
        except RAGInternalError as exc:
            raise ToolExecutionError(
                error_code="KNOWLEDGE_UNAVAILABLE",
                message="Enterprise knowledge service failed",
                recoverable=False,
            ) from exc

        matches, evidence = _to_frozen_matches(
            result,
            query=query,
            index_snapshot_id=index_snapshot_id,
            top_k=top_k,
        )
        LOGGER.info(
            "Knowledge evidence prepared",
            extra={
                "event": "knowledge_evidence_prepared",
                "tool_name": self.definition.tool_name,
                "trace_id": trace_id,
                "rag_trace_id": result.rag_trace_id,
                "source_count": len(result.sources),
                "evidence_count": len(evidence),
                "latency_ms": result.latency_ms,
                "route": result.route,
            },
        )
        return ToolExecutionOutput(
            output=JsonObject(
                {
                    "matches": cast(JsonValue, matches),
                    "match_count": len(matches),
                    "index_snapshot_id": index_snapshot_id,
                    "empty_result": not matches,
                }
            ),
            evidence=evidence,
        )


def _to_frozen_matches(
    result: KnowledgeResult,
    *,
    query: str,
    index_snapshot_id: str,
    top_k: int,
) -> tuple[list[JsonMapping], tuple[EvidenceDraft, ...]]:
    matches: list[JsonMapping] = []
    drafts: list[EvidenceDraft] = []
    for source in result.sources[:top_k]:
        if source.source is None or not source.source:
            continue
        metadata = source.metadata.root if source.metadata is not None else {}
        excerpt = _excerpt_for(source, result.contexts)
        document_id = source.source
        raw_version = metadata.get("document_version", metadata.get("version"))
        document_version = raw_version if isinstance(raw_version, str) else "unknown"
        raw_chunk_id = metadata.get("chunk_id")
        chunk_id = (
            raw_chunk_id
            if isinstance(raw_chunk_id, str)
            else _context_chunk_id(source, result.contexts)
        )
        chunk_id = chunk_id or "unknown"
        raw_classification = metadata.get("classification")
        classification = (
            raw_classification
            if isinstance(raw_classification, str)
            and raw_classification in _ALLOWED_CLASSIFICATIONS
            else "INTERNAL"
        )
        raw_checksum = metadata.get("checksum")
        checksum = (
            raw_checksum
            if isinstance(raw_checksum, str)
            else _checksum(
                {
                    "source": source.source,
                    "document_id": document_id,
                    "document_version": document_version,
                    "chunk_id": chunk_id,
                    "excerpt": excerpt,
                }
            )
        )
        score = _context_score(source, result.contexts)
        match: JsonMapping = {
            "document_id": document_id,
            "document_version": document_version,
            "chunk_id": chunk_id,
            "excerpt": excerpt[:4000],
            "score": score,
            "classification": classification,
            "checksum": checksum,
        }
        matches.append(match)
        reference: JsonMapping = {
            "source": source.source,
            "document_id": document_id,
            "document_version": document_version,
            "chunk_id": chunk_id,
            "index_snapshot_id": index_snapshot_id,
            "rag_trace_id": result.rag_trace_id,
        }
        raw_page = metadata.get("page")
        if isinstance(raw_page, int) and not isinstance(raw_page, bool):
            reference["page"] = raw_page
        drafts.append(
            EvidenceDraft(
                source_type=EvidenceType.DOCUMENT,
                source_reference=EvidenceSourceReference(reference=JsonObject(reference)),
                content=EvidenceContent(
                    data=JsonObject(
                        {
                            "excerpt": excerpt[:4000],
                            "query_sha256": _sha256_text(query),
                        }
                    ),
                    classification=classification,
                    checksum=checksum,
                ),
            )
        )
    return matches, tuple(drafts)


def _excerpt_for(source: KnowledgeSource, contexts: tuple[KnowledgeContext, ...]) -> str:
    if source.text_preview is not None and source.text_preview:
        return source.text_preview
    for context in contexts:
        source_matches = context.source is not None and context.source == source.source
        if source_matches:
            return context.content
    return source.source or ""


def _context_chunk_id(
    source: KnowledgeSource, contexts: tuple[KnowledgeContext, ...]
) -> str | None:
    for context in contexts:
        if context.source == source.source and context.chunk_id:
            return context.chunk_id
    return None


def _context_score(source: KnowledgeSource, contexts: tuple[KnowledgeContext, ...]) -> float:
    for context in contexts:
        if context.source == source.source and context.score is not None:
            return context.score
    return 0.0


def _required_text(arguments: JsonObject, name: str) -> str:
    value = arguments.root.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolValidationError(f"{name} must be a non-blank string")
    return value.strip()


def _required_int(arguments: JsonObject, name: str) -> int:
    value = arguments.root.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolValidationError(f"{name} must be an integer")
    return value


def _checksum(value: JsonMapping) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = [
    "KNOWLEDGE_INPUT_SCHEMA",
    "KNOWLEDGE_OUTPUT_SCHEMA",
    "KnowledgeTool",
]
