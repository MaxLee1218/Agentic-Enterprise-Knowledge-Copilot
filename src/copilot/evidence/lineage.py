"""Pure helpers for inspecting structured evidence-lineage traces."""

from __future__ import annotations

from copilot.contracts import EvidenceType, LineageTrace


def contains_source_type(trace: LineageTrace, source_type: EvidenceType) -> bool:
    """Return whether a complete or partial trace contains an evidence source type."""
    return any(item.source_type is source_type for item in trace.nodes)


def parent_map(trace: LineageTrace) -> dict[str, tuple[str, ...]]:
    """Return a detached, deterministically ordered child-to-parent adjacency map."""
    parents: dict[str, list[str]] = {evidence_id: [] for evidence_id in trace.ordered_evidence_ids}
    for edge in trace.edges:
        parents.setdefault(edge.child_evidence_id, []).append(edge.parent_evidence_id)
    return {evidence_id: tuple(sorted(parent_ids)) for evidence_id, parent_ids in parents.items()}


__all__ = ["contains_source_type", "parent_map"]
