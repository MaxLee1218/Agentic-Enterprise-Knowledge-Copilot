"""Thread-safe in-memory evidence ledger for the tool runtime foundation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import RLock
from uuid import uuid4

from copilot.contracts import EvidenceItem, EvidenceType, ToolCall
from copilot.contracts.validators import utc_now
from copilot.tools.base import EvidenceDraft


class InMemoryEvidenceLedger:
    """Append-only evidence recorder suitable for tests and local composition.

    Durable deployments can replace this class through the EvidenceRecorder protocol without
    changing the executor.
    """

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: f"E-{uuid4().hex}")
        self._clock = clock or utc_now
        self._items: dict[str, EvidenceItem] = {}
        self._lock = RLock()

    def record(self, call: ToolCall, drafts: tuple[EvidenceDraft, ...]) -> tuple[EvidenceItem, ...]:
        """Atomically bind evidence drafts to a call and reject broken calculation lineage."""
        if not drafts:
            return ()
        with self._lock:
            self._validate_lineage(call, drafts)
            items = tuple(
                EvidenceItem(
                    evidence_id=self._id_factory(),
                    task_id=call.task_id,
                    step_id=call.step_id,
                    tool_call_id=call.tool_call_id,
                    source_type=draft.source_type,
                    source_reference=draft.source_reference,
                    content=draft.content,
                    timestamp=self._clock(),
                )
                for draft in drafts
            )
            identifiers = [item.evidence_id for item in items]
            if len(set(identifiers)) != len(identifiers) or any(
                identifier in self._items for identifier in identifiers
            ):
                raise ValueError("evidence identifier already exists")
            self._items.update((item.evidence_id, item) for item in items)
            return items

    def get(self, evidence_id: str) -> EvidenceItem:
        """Return one immutable evidence item."""
        with self._lock:
            return self._items[evidence_id]

    def list_for_call(self, tool_call_id: str) -> tuple[EvidenceItem, ...]:
        """Return evidence for a call in insertion order."""
        with self._lock:
            return tuple(item for item in self._items.values() if item.tool_call_id == tool_call_id)

    def _validate_lineage(self, call: ToolCall, drafts: tuple[EvidenceDraft, ...]) -> None:
        for draft in drafts:
            if draft.source_type is not EvidenceType.CALCULATION:
                continue
            for evidence_id in draft.source_reference.input_evidence_ids:
                source = self._items.get(evidence_id)
                if source is None or source.task_id != call.task_id:
                    raise ValueError(
                        "calculation evidence input is missing or belongs to another task"
                    )
