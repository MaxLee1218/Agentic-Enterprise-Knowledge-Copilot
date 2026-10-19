"""Frozen synthetic index behind the production Knowledge Tool, with normal governance."""

import hashlib
from pathlib import Path
from typing import Literal

from copilot.contracts import JsonObject
from copilot.tools.knowledge.schemas import (
    KnowledgeContext,
    KnowledgeHealthResult,
    KnowledgeResult,
    KnowledgeSource,
)
from evaluation.contracts import EvaluationModel

POLICY_PATH = (
    Path(__file__).parents[1] / "datasets/fixtures/knowledge/supplier_quality_pilot_policy_v1.json"
)


class PolicySnapshot(EvaluationModel):
    """Source-only policy, containing no example answer, threshold or expected score."""

    version: Literal["supplier-quality-pilot-policy.v1"]
    index_snapshot_id: str
    document_id: str
    document_version: str
    chunk_id: str
    excerpt: str

    @property
    def checksum(self) -> str:
        """Content checksum used by the approved frozen index entry."""
        return "sha256:" + hashlib.sha256(self.excerpt.encode()).hexdigest()


class SnapshotKnowledgeClient:
    """One read-only preapproved synthetic document; this is not a retrieval benchmark."""

    def __init__(self, snapshot: PolicySnapshot) -> None:
        self.snapshot = snapshot

    def ask(self, question: str, *, trace_id: str | None = None) -> KnowledgeResult:
        """Return the fixed calculation-method source through the normal Knowledge Tool."""
        p = self.snapshot
        metadata = JsonObject(
            {
                "document_version": p.document_version,
                "chunk_id": p.chunk_id,
                "checksum": p.checksum,
                "classification": "INTERNAL",
            }
        )
        return KnowledgeResult(
            answer=p.excerpt,
            sources=(
                KnowledgeSource(
                    index=1, source=p.document_id, metadata=metadata, text_preview=p.excerpt
                ),
            ),
            contexts=(
                KnowledgeContext(
                    content=p.excerpt,
                    source=p.document_id,
                    chunk_id=p.chunk_id,
                    score=1,
                    metadata=metadata,
                ),
            ),
            route="fixed-synthetic-index",
            latency_ms=0,
            rag_trace_id=trace_id or "pilot-source",
        )

    def health_check(self, *, trace_id: str | None = None) -> KnowledgeHealthResult:
        """Report the in-process snapshot's availability without outbound calls."""
        return KnowledgeHealthResult(
            healthy=True, status="ok", latency_ms=0, rag_trace_id=trace_id or "pilot-source"
        )

    def close(self) -> None:
        """No owned resources."""
