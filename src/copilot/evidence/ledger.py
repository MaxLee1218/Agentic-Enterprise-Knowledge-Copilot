"""Task-isolated, append-only evidence ledger with deterministic lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from copilot.contracts import (
    ErrorType,
    EvidenceAddResult,
    EvidenceItem,
    EvidenceLedgerSnapshot,
    EvidenceType,
    JsonObject,
    LineageEdge,
    LineageIssue,
    LineageTrace,
    TaskError,
    ToolCall,
)
from copilot.contracts.errors import DomainError
from copilot.contracts.validators import utc_now
from copilot.tools.base import EvidenceDraft


class EvidenceLedgerError(DomainError):
    """Typed evidence storage or reference failure."""


class EvidenceNotFoundError(EvidenceLedgerError):
    """Raised when a scoped evidence reference cannot be resolved."""


class EvidenceValidationError(EvidenceLedgerError):
    """Raised when evidence cannot be appended without corrupting the ledger."""


class InMemoryEvidenceLedger:
    """Thread-safe authoritative local ledger for immutable task evidence.

    Items are copied at the boundary because Pydantic's frozen models do not recursively freeze
    dictionaries nested inside JSON values. Logical deduplication is scoped by task and uses a
    stable SHA-256 fingerprint rather than process-randomized ``hash()``.
    """

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        database_path: Path | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: f"E-{uuid4().hex}")
        self._clock = clock or utc_now
        self._items: dict[str, EvidenceItem] = {}
        self._fingerprints: dict[tuple[str, str], str] = {}
        self._lock = RLock()
        self._database = (
            sqlite3.connect(database_path, check_same_thread=False)
            if database_path is not None
            else None
        )
        if self._database is not None:
            self._database.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            self._database.commit()
            for row in self._database.execute(
                "SELECT payload_json FROM workflow_evidence ORDER BY rowid"
            ):
                item = EvidenceItem.model_validate_json(row[0])
                self._items[item.evidence_id] = item
                self._fingerprints[(item.task_id, evidence_fingerprint(item))] = item.evidence_id

    def record(self, call: ToolCall, drafts: tuple[EvidenceDraft, ...]) -> tuple[EvidenceItem, ...]:
        """Bind drafts to one governed call and atomically append canonical evidence."""
        if not drafts:
            return ()
        with self._lock:
            pending: list[EvidenceItem] = []
            generated_ids: set[str] = set()
            for draft in drafts:
                evidence_id = self._id_factory()
                if evidence_id in generated_ids or evidence_id in self._items:
                    raise self._validation_error(
                        call.task_id,
                        "EVIDENCE_ID_CONFLICT",
                        "Evidence identifier already exists",
                    )
                generated_ids.add(evidence_id)
                pending.append(
                    EvidenceItem(
                        evidence_id=evidence_id,
                        task_id=call.task_id,
                        step_id=call.step_id,
                        tool_call_id=call.tool_call_id,
                        source_type=draft.source_type,
                        source_reference=deepcopy(draft.source_reference),
                        content=deepcopy(draft.content),
                        timestamp=self._clock(),
                    )
                )
            results = self._add_many_locked(tuple(pending), call.task_id)
            try:
                self._persist_created(results)
            except Exception:
                self._rollback_created(results)
                raise
            return tuple(_copy_item(result.evidence) for result in results)

    def add(self, evidence: EvidenceItem) -> EvidenceAddResult:
        """Append one item or return the existing canonical logical duplicate."""
        with self._lock:
            result = self._add_locked(_copy_item(evidence))
            try:
                self._persist_created((result,))
            except Exception:
                self._rollback_created((result,))
                raise
            return result.model_copy(deep=True)

    def get(self, evidence_id: str, *, task_id: str | None = None) -> EvidenceItem:
        """Return a detached immutable item, optionally enforcing task ownership."""
        with self._lock:
            item = self._items.get(evidence_id)
            if item is None or (task_id is not None and item.task_id != task_id):
                raise self._not_found_error(task_id, evidence_id)
            return _copy_item(item)

    def list(self, task_id: str) -> tuple[EvidenceItem, ...]:
        """Return a detached task-scoped snapshot in append order."""
        with self._lock:
            return tuple(
                _copy_item(item) for item in self._items.values() if item.task_id == task_id
            )

    def list_for_task(self, task_id: str) -> tuple[EvidenceItem, ...]:
        """Backward-compatible alias for task-scoped listing."""
        return self.list(task_id)

    def list_for_call(self, tool_call_id: str) -> tuple[EvidenceItem, ...]:
        """Return detached evidence for a call in insertion order."""
        with self._lock:
            return tuple(
                _copy_item(item)
                for item in self._items.values()
                if item.tool_call_id == tool_call_id
            )

    def find_by_step(self, task_id: str, step_id: str) -> tuple[EvidenceItem, ...]:
        """Return task-local evidence produced by one step."""
        with self._lock:
            return tuple(
                _copy_item(item)
                for item in self._items.values()
                if item.task_id == task_id and item.step_id == step_id
            )

    def find_by_type(
        self,
        task_id: str,
        source_type: EvidenceType,
    ) -> tuple[EvidenceItem, ...]:
        """Return task-local evidence of one frozen source type."""
        with self._lock:
            return tuple(
                _copy_item(item)
                for item in self._items.values()
                if item.task_id == task_id and item.source_type is source_type
            )

    def validate_reference(self, task_id: str, evidence_id: str) -> bool:
        """Return whether an evidence identifier exists and belongs to the task."""
        with self._lock:
            item = self._items.get(evidence_id)
            return item is not None and item.task_id == task_id

    def trace_lineage(self, task_id: str, evidence_id: str) -> LineageTrace:
        """Return a deterministic root-first graph and all safe structural issues."""
        with self._lock:
            root = self._items.get(evidence_id)
            if root is None or root.task_id != task_id:
                raise self._not_found_error(task_id, evidence_id)

            nodes: dict[str, EvidenceItem] = {}
            edges: list[LineageEdge] = []
            issues: list[LineageIssue] = []
            ordered: list[str] = []
            visited: set[str] = set()
            active: set[str] = set()
            seen_edges: set[tuple[str, str]] = set()

            def visit(current_id: str) -> None:
                current = self._items[current_id]
                if current_id not in visited:
                    nodes[current_id] = current
                    ordered.append(current_id)
                    visited.add(current_id)
                active.add(current_id)
                raw_parent_ids = current.source_reference.input_evidence_ids
                if current.source_type is EvidenceType.CALCULATION and not raw_parent_ids:
                    issues.append(
                        LineageIssue(
                            code="CALCULATION_LINEAGE_MISSING",
                            message="Calculation evidence has no input evidence",
                            evidence_id=current_id,
                        )
                    )
                if len(set(raw_parent_ids)) != len(raw_parent_ids):
                    issues.append(
                        LineageIssue(
                            code="LINEAGE_DUPLICATE_EDGE",
                            message="Evidence lineage contains a duplicate parent edge",
                            evidence_id=current_id,
                        )
                    )
                for parent_id in sorted(set(raw_parent_ids)):
                    if not parent_id:
                        issues.append(
                            LineageIssue(
                                code="LINEAGE_EMPTY_REFERENCE",
                                message="Evidence lineage contains an empty parent identifier",
                                evidence_id=current_id,
                            )
                        )
                        continue
                    edge_key = (parent_id, current_id)
                    if edge_key not in seen_edges:
                        edges.append(
                            LineageEdge(
                                parent_evidence_id=parent_id,
                                child_evidence_id=current_id,
                            )
                        )
                        seen_edges.add(edge_key)
                    parent = self._items.get(parent_id)
                    if parent is None:
                        issues.append(
                            LineageIssue(
                                code="LINEAGE_PARENT_NOT_FOUND",
                                message="Evidence lineage parent does not exist",
                                evidence_id=current_id,
                                parent_evidence_id=parent_id,
                            )
                        )
                        continue
                    if parent.task_id != task_id:
                        issues.append(
                            LineageIssue(
                                code="LINEAGE_CROSS_TASK_REFERENCE",
                                message="Evidence lineage parent belongs to another task",
                                evidence_id=current_id,
                                parent_evidence_id=parent_id,
                            )
                        )
                        continue
                    if parent_id == current_id:
                        issues.append(
                            LineageIssue(
                                code="LINEAGE_SELF_REFERENCE",
                                message="Evidence lineage cannot reference itself",
                                evidence_id=current_id,
                                parent_evidence_id=parent_id,
                            )
                        )
                        continue
                    if parent_id in active:
                        issues.append(
                            LineageIssue(
                                code="LINEAGE_CYCLE",
                                message="Evidence lineage contains a cycle",
                                evidence_id=current_id,
                                parent_evidence_id=parent_id,
                            )
                        )
                        continue
                    if parent_id not in visited:
                        visit(parent_id)
                active.remove(current_id)

            visit(evidence_id)
            return LineageTrace(
                task_id=task_id,
                root_evidence_id=evidence_id,
                nodes=tuple(_copy_item(nodes[item_id]) for item_id in ordered),
                edges=tuple(edges),
                ordered_evidence_ids=tuple(ordered),
                is_complete=not issues,
                issues=tuple(issues),
            )

    def snapshot(self, task_id: str | None = None) -> EvidenceLedgerSnapshot:
        """Create a detached serializable ledger snapshot, optionally task-scoped."""
        with self._lock:
            items = tuple(
                _copy_item(item)
                for item in self._items.values()
                if task_id is None or item.task_id == task_id
            )
            return EvidenceLedgerSnapshot(items=items)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: EvidenceLedgerSnapshot,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> InMemoryEvidenceLedger:
        """Restore and fully validate a serialized append-order snapshot."""
        if snapshot.schema_version != "evidence-ledger.v1":
            raise ValueError("unsupported evidence ledger snapshot version")
        ledger = cls(id_factory=id_factory, clock=clock)
        with ledger._lock:
            for item in snapshot.items:
                stored = _copy_item(item)
                if stored.evidence_id in ledger._items:
                    raise ledger._validation_error(
                        stored.task_id,
                        "EVIDENCE_ID_CONFLICT",
                        "Evidence snapshot contains duplicate identifiers",
                    )
                fingerprint = evidence_fingerprint(stored)
                key = (stored.task_id, fingerprint)
                if key in ledger._fingerprints:
                    raise ledger._validation_error(
                        stored.task_id,
                        "EVIDENCE_SNAPSHOT_DUPLICATE",
                        "Evidence snapshot contains duplicate logical records",
                    )
                ledger._items[stored.evidence_id] = stored
                ledger._fingerprints[key] = stored.evidence_id
            ledger._validate_restored_lineage()
        return ledger

    def _add_many_locked(
        self,
        items: tuple[EvidenceItem, ...],
        task_id: str,
    ) -> tuple[EvidenceAddResult, ...]:
        """Validate a batch before committing any newly created logical records."""
        pending_fingerprints: dict[tuple[str, str], EvidenceItem] = {}
        results: list[EvidenceAddResult] = []
        for item in items:
            self._validate_new_item(item)
            fingerprint = evidence_fingerprint(item)
            key = (item.task_id, fingerprint)
            duplicate_id = self._fingerprints.get(key)
            if duplicate_id is not None:
                canonical = self._items[duplicate_id]
                results.append(
                    EvidenceAddResult(
                        evidence=_copy_item(canonical),
                        created=False,
                        duplicate_of=canonical.evidence_id,
                    )
                )
                continue
            pending_duplicate = pending_fingerprints.get(key)
            if pending_duplicate is not None:
                results.append(
                    EvidenceAddResult(
                        evidence=_copy_item(pending_duplicate),
                        created=False,
                        duplicate_of=pending_duplicate.evidence_id,
                    )
                )
                continue
            pending_fingerprints[key] = item
            results.append(EvidenceAddResult(evidence=_copy_item(item), created=True))
        if any(item.task_id != task_id for item in items):
            raise self._validation_error(
                task_id,
                "EVIDENCE_BATCH_CROSS_TASK",
                "A tool call cannot record evidence for another task",
            )
        for key, item in pending_fingerprints.items():
            stored = _copy_item(item)
            self._items[stored.evidence_id] = stored
            self._fingerprints[key] = stored.evidence_id
        return tuple(results)

    def close(self) -> None:
        """Close the optional durable Evidence connection."""
        with self._lock:
            if self._database is not None:
                self._database.close()
                self._database = None

    def _persist_created(self, results: tuple[EvidenceAddResult, ...]) -> None:
        if self._database is None:
            return
        try:
            for result in results:
                if result.created:
                    self._database.execute(
                        "INSERT INTO workflow_evidence VALUES (?, ?, ?)",
                        (
                            result.evidence.evidence_id,
                            result.evidence.task_id,
                            result.evidence.model_dump_json(),
                        ),
                    )
            self._database.commit()
        except Exception:
            self._database.rollback()
            raise

    def _rollback_created(self, results: tuple[EvidenceAddResult, ...]) -> None:
        for result in results:
            if not result.created:
                continue
            item = self._items.pop(result.evidence.evidence_id, None)
            if item is not None:
                self._fingerprints.pop((item.task_id, evidence_fingerprint(item)), None)

    def _add_locked(self, item: EvidenceItem) -> EvidenceAddResult:
        if item.evidence_id in self._items:
            raise self._validation_error(
                item.task_id,
                "EVIDENCE_ID_CONFLICT",
                "Evidence identifier already exists",
            )
        self._validate_new_item(item)
        fingerprint = evidence_fingerprint(item)
        key = (item.task_id, fingerprint)
        duplicate_id = self._fingerprints.get(key)
        if duplicate_id is not None:
            canonical = self._items[duplicate_id]
            return EvidenceAddResult(
                evidence=_copy_item(canonical),
                created=False,
                duplicate_of=canonical.evidence_id,
            )
        stored = _copy_item(item)
        self._items[stored.evidence_id] = stored
        self._fingerprints[key] = stored.evidence_id
        return EvidenceAddResult(evidence=_copy_item(stored), created=True)

    def _validate_new_item(self, item: EvidenceItem) -> None:
        parent_ids = item.source_reference.input_evidence_ids
        if len(set(parent_ids)) != len(parent_ids):
            raise self._validation_error(
                item.task_id,
                "LINEAGE_DUPLICATE_EDGE",
                "Evidence lineage contains duplicate parent identifiers",
            )
        if item.evidence_id in parent_ids:
            raise self._validation_error(
                item.task_id,
                "LINEAGE_SELF_REFERENCE",
                "Evidence cannot reference itself",
            )
        for parent_id in parent_ids:
            parent = self._items.get(parent_id)
            if parent is None:
                raise self._validation_error(
                    item.task_id,
                    "LINEAGE_PARENT_NOT_FOUND",
                    "Evidence lineage parent does not exist",
                )
            if parent.task_id != item.task_id:
                raise self._validation_error(
                    item.task_id,
                    "LINEAGE_CROSS_TASK_REFERENCE",
                    "Evidence lineage cannot cross task boundaries",
                )

    def _validate_restored_lineage(self) -> None:
        for item in self._items.values():
            parent_ids = item.source_reference.input_evidence_ids
            if len(set(parent_ids)) != len(parent_ids):
                raise self._validation_error(
                    item.task_id,
                    "LINEAGE_DUPLICATE_EDGE",
                    "Evidence snapshot contains duplicate parent edges",
                )
            for parent_id in parent_ids:
                parent = self._items.get(parent_id)
                if parent is None:
                    raise self._validation_error(
                        item.task_id,
                        "LINEAGE_PARENT_NOT_FOUND",
                        "Evidence snapshot references a missing parent",
                    )
                if parent.task_id != item.task_id:
                    raise self._validation_error(
                        item.task_id,
                        "LINEAGE_CROSS_TASK_REFERENCE",
                        "Evidence snapshot lineage crosses task boundaries",
                    )
                if parent_id == item.evidence_id:
                    raise self._validation_error(
                        item.task_id,
                        "LINEAGE_SELF_REFERENCE",
                        "Evidence snapshot contains a self-reference",
                    )

        visited: set[str] = set()
        active: set[str] = set()

        def visit(evidence_id: str) -> None:
            if evidence_id in active:
                task_id = self._items[evidence_id].task_id
                raise self._validation_error(
                    task_id,
                    "LINEAGE_CYCLE",
                    "Evidence snapshot lineage contains a cycle",
                )
            if evidence_id in visited:
                return
            active.add(evidence_id)
            item = self._items[evidence_id]
            for parent_id in sorted(item.source_reference.input_evidence_ids):
                visit(parent_id)
            active.remove(evidence_id)
            visited.add(evidence_id)

        for evidence_id in sorted(self._items):
            visit(evidence_id)

    @staticmethod
    def _not_found_error(task_id: str | None, evidence_id: str) -> EvidenceNotFoundError:
        return EvidenceNotFoundError(
            TaskError(
                error_code="EVIDENCE_NOT_FOUND",
                error_type=ErrorType.VALIDATION,
                message="Evidence reference does not exist in the requested task",
                recoverable=False,
                task_id=task_id,
                details=_safe_details({"evidence_id": evidence_id}),
            )
        )

    @staticmethod
    def _validation_error(task_id: str, code: str, message: str) -> EvidenceValidationError:
        return EvidenceValidationError(
            TaskError(
                error_code=code,
                error_type=ErrorType.VALIDATION,
                message=message,
                recoverable=False,
                task_id=task_id,
            )
        )


def evidence_fingerprint(item: EvidenceItem) -> str:
    """Return a stable type-aware SHA-256 logical evidence fingerprint."""
    reference = deepcopy(item.source_reference.reference.root)
    content_hash = _sha256_json(
        {
            "data": item.content.data.root,
            "classification": item.content.classification,
        }
    )
    base: dict[str, JsonValue] = {
        "task_id": item.task_id,
        "step_id": item.step_id,
        "source_type": item.source_type.value,
        "content_hash": content_hash,
    }
    if item.source_type is EvidenceType.DOCUMENT:
        base["source"] = {
            key: reference[key]
            for key in (
                "source",
                "document_id",
                "document_version",
                "chunk_id",
                "page",
                "index_snapshot_id",
            )
            if key in reference
        }
    elif item.source_type is EvidenceType.DATABASE:
        base["query_id"] = reference.get("query_id") or reference.get("query_fingerprint")
        base["query_fingerprint"] = reference.get("query_fingerprint")
        base["table_names"] = cast(
            JsonValue,
            sorted(str(value) for value in cast(list[object], reference.get("table_names", []))),
        )
    else:
        formulas = reference.get("formulas", reference.get("formula", ""))
        if isinstance(formulas, str):
            formulas = formulas.strip()
        elif isinstance(formulas, dict):
            formulas = {str(key): str(value).strip() for key, value in sorted(formulas.items())}
        base["formula"] = formulas
        base["input_evidence_ids"] = cast(
            JsonValue, sorted(item.source_reference.input_evidence_ids)
        )
        for key in ("group_by", "aggregation", "operation", "engine_version"):
            if key in reference:
                base[key] = reference[key]
    return _sha256_json(base)


def _sha256_json(value: object) -> str:
    normalized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _copy_item(item: EvidenceItem) -> EvidenceItem:
    return item.model_copy(deep=True)


def _safe_details(value: dict[str, str]) -> JsonObject:
    return JsonObject(cast(dict[str, JsonValue], value))


__all__ = [
    "EvidenceLedgerError",
    "EvidenceNotFoundError",
    "EvidenceValidationError",
    "InMemoryEvidenceLedger",
    "evidence_fingerprint",
]
