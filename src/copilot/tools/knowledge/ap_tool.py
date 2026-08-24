"""Controlled-corpus knowledge adapter for Accounts Payable policy v1."""

from __future__ import annotations

import hashlib
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
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
from copilot.tools.exceptions import ToolPermissionError, ToolValidationError
from copilot.tools.knowledge.policy_bundle import LoadedAPPolicyBundle
from copilot.tools.knowledge.tool import KNOWLEDGE_INPUT_SCHEMA, KNOWLEDGE_OUTPUT_SCHEMA

AP_KNOWLEDGE_TOOL_VERSION = "2.0.0-controlled"


class AccountsPayablePolicyTool:
    """Retrieve only checksum-validated chunks from one loaded AP policy bundle."""

    definition = ToolDefinition(
        tool_name="knowledge_search",
        tool_version=AP_KNOWLEDGE_TOOL_VERSION,
        description=(
            "Retrieve the controlled, checksum-bound Accounts Payable v1 policy corpus for "
            "deterministic rule lineage; read-only with no arbitrary collection selection"
        ),
        input_schema=JsonObject(KNOWLEDGE_INPUT_SCHEMA),
        output_schema=JsonObject(KNOWLEDGE_OUTPUT_SCHEMA),
        risk_level=RiskLevel.LOW,
        timeout=ToolTimeout(attempt_seconds=5, overall_seconds=10),
        approval_policy=ToolApprovalPolicy(
            policy_id="accounts-payable-policy-search-v1",
            trigger_conditions=(),
            approver_role=None,
            editable_fields=(),
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=("bundle_checksum", "index_snapshot_id", "normalized_input"),
            reuse_window_seconds=300,
            side_effects="None; local read-only controlled policy retrieval",
        ),
    )

    def __init__(self, bundle: LoadedAPPolicyBundle) -> None:
        self.bundle = bundle
        checksum = bundle.rule_manifest.manifest_checksum.removeprefix("sha256:")[:24]
        self.index_snapshot_id = f"ap-policy-{checksum}"
        self.call_count = 0

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Validate tenant/collection/snapshot bindings and emit exact Document Evidence."""
        context.cancellation.raise_if_requested()
        self.call_count += 1
        root = arguments.root
        if root.get("tenant_id") != self.bundle.corpus.tenant_id:
            raise ToolPermissionError(
                error_code="KNOWLEDGE_ACCESS_DENIED",
                message="Accounts Payable policy tenant scope was denied",
            )
        if root.get("collection_ids") != [self.bundle.corpus.collection_id]:
            raise ToolPermissionError(
                error_code="KNOWLEDGE_COLLECTION_DENIED",
                message="Accounts Payable policy collection is not approved",
            )
        if root.get("index_snapshot_id") != self.index_snapshot_id:
            raise ToolValidationError("Accounts Payable policy snapshot binding is invalid")
        top_k = root.get("top_k")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
            raise ToolValidationError("top_k must be between 1 and 20")
        query = root.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolValidationError("query must be a non-blank string")
        # Only rule-bound chunks may enter the executable/report lineage. General
        # explanatory chunks remain in the corpus but are not task Evidence.
        selected = tuple(
            item
            for item in self.bundle.rag_payload
            if cast(dict[str, object], item["metadata"])["bound_rule_ids"]
        )[:top_k]
        matches: list[JsonMapping] = []
        drafts: list[EvidenceDraft] = []
        for index, item in enumerate(selected):
            content = str(item["content"])
            metadata = cast(dict[str, object], item["metadata"])
            checksum = str(metadata["checksum"])
            matches.append(
                {
                    "document_id": str(metadata["document_id"]),
                    "document_version": str(metadata["document_version"]),
                    "chunk_id": str(item["chunk_id"]),
                    "excerpt": content[:4000],
                    "score": max(0.0, 1.0 - (index * 0.01)),
                    "classification": str(metadata["classification"]),
                    "checksum": checksum,
                }
            )
            reference: JsonMapping = {
                "source": str(item["source"]),
                "document_id": str(metadata["document_id"]),
                "document_version": str(metadata["document_version"]),
                "chunk_id": str(item["chunk_id"]),
                "page": cast(int, metadata["page"]),
                "collection_id": str(metadata["collection_id"]),
                "effective_from": str(metadata["effective_from"]),
                "effective_to": str(metadata["effective_to"]),
                "policy_rule_set_version": str(metadata["policy_rule_set_version"]),
                "bound_rule_ids": cast(list[JsonValue], metadata["bound_rule_ids"]),
                "document_checksum": str(metadata["document_checksum"]),
                "excerpt_checksum": checksum,
                "index_snapshot_id": self.index_snapshot_id,
                "retrieval_trace_id": context.trace_id or context.call.tool_call_id,
                "retrieval_score": max(0.0, 1.0 - (index * 0.01)),
                "classification": str(metadata["classification"]),
            }
            drafts.append(
                EvidenceDraft(
                    source_type=EvidenceType.DOCUMENT,
                    source_reference=EvidenceSourceReference(reference=JsonObject(reference)),
                    content=EvidenceContent(
                        data=JsonObject(
                            {
                                "excerpt": content[:4000],
                                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                            }
                        ),
                        classification=str(metadata["classification"]),
                        checksum=checksum,
                    ),
                )
            )
        context.cancellation.raise_if_requested()
        return ToolExecutionOutput(
            output=JsonObject(
                {
                    "matches": cast(JsonValue, matches),
                    "match_count": len(matches),
                    "index_snapshot_id": self.index_snapshot_id,
                    "empty_result": not matches,
                }
            ),
            evidence=tuple(drafts),
        )


__all__ = ["AP_KNOWLEDGE_TOOL_VERSION", "AccountsPayablePolicyTool"]
