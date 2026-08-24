"""Task-isolated, append-only evidence ledger with deterministic lineage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import cast
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import select

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
from copilot.persistence.database import PersistenceDatabase, coerce_database
from copilot.persistence.models import WorkflowEvidenceRow
from copilot.security import (
    ContentSourceType,
    OutputDisposition,
    OutputGuard,
    PromptInjectionDetector,
    TrustLevel,
)
from copilot.tools.base import EvidenceDraft


class EvidenceLedgerError(DomainError):
    """Typed evidence storage or reference failure."""


class EvidenceNotFoundError(EvidenceLedgerError, LookupError):
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
        database_path: PersistenceDatabase | Path | None = None,
        output_guard: OutputGuard | None = None,
        injection_detector: PromptInjectionDetector | None = None,
        max_items_per_task: int = 500,
        initialize_schema: bool = True,
    ) -> None:
        if max_items_per_task < 1:
            raise ValueError("max_items_per_task must be positive")
        self._id_factory = id_factory or (lambda: f"E-{uuid4().hex}")
        self._clock = clock or utc_now
        self._output_guard = output_guard or OutputGuard()
        self._injection_detector = injection_detector or PromptInjectionDetector()
        self._max_items_per_task = max_items_per_task
        self._items: dict[str, EvidenceItem] = {}
        self._item_tenants: dict[str, str] = {}
        self._fingerprints: dict[tuple[str, str, str], str] = {}
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path,
            initialize_schema=initialize_schema,
        )

    def record(self, call: ToolCall, drafts: tuple[EvidenceDraft, ...]) -> tuple[EvidenceItem, ...]:
        """Bind drafts to one governed call and atomically append canonical evidence."""
        if not drafts:
            return ()
        with self._lock:
            if self._database is not None:
                self._refresh_database_items(call.tenant_id)
            existing_count = sum(
                item.task_id == call.task_id
                and self._item_tenants.get(item.evidence_id) == call.tenant_id
                for item in self._items.values()
            )
            if existing_count + len(drafts) > self._max_items_per_task:
                raise self._validation_error(
                    call.task_id,
                    "EVIDENCE_LIMIT_EXCEEDED",
                    "Task evidence exceeds the configured item limit",
                )
            pending: list[EvidenceItem] = []
            generated_ids: set[str] = set()
            for draft in drafts:
                draft = self._secure_draft(call, draft)
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
            results = self._add_many_locked(tuple(pending), call.task_id, call.tenant_id)
            try:
                self._persist_created(results, tenant_id=call.tenant_id)
            except Exception:
                self._rollback_created(results, tenant_id=call.tenant_id)
                raise
            return tuple(_copy_item(result.evidence) for result in results)

    def _secure_draft(self, call: ToolCall, draft: EvidenceDraft) -> EvidenceDraft:
        """Minimize secrets and mark trust/injection metadata before Evidence persistence."""
        data = deepcopy(draft.content.data.root)
        reference = deepcopy(draft.source_reference.reference.root)
        findings: list[JsonValue] = []
        trust_level = TrustLevel.UNTRUSTED
        quarantined = False
        if draft.source_type is EvidenceType.DOCUMENT:
            excerpt = data.get("excerpt")
            if isinstance(excerpt, str):
                scan = self._injection_detector.scan(
                    excerpt,
                    source_type=ContentSourceType.RETRIEVED_DOCUMENT,
                    source_id=f"{call.tool_call_id}:{reference.get('chunk_id', 'document')}",
                )
                # Preserve byte-exact benign source wording so controlled document
                # checksums remain reproducible; replace content only when a rule fired.
                if scan.findings:
                    data["excerpt"] = scan.content
                findings = [
                    cast(
                        JsonValue,
                        {
                            "finding_id": finding.finding_id,
                            "category": finding.category,
                            "severity": finding.severity.value,
                            "matched_rule": finding.matched_rule,
                            "content_hash": finding.content_hash,
                        },
                    )
                    for finding in scan.findings
                ]
                trust_level = scan.trust_level
                quarantined = scan.quarantined
        guarded = self._output_guard.guard(
            cast(JsonValue, data),
            source_type=(
                ContentSourceType.RETRIEVED_DOCUMENT
                if draft.source_type is EvidenceType.DOCUMENT
                else ContentSourceType.DATABASE_RESULT
                if draft.source_type is EvidenceType.DATABASE
                else ContentSourceType.TOOL_OUTPUT
            ),
            source_id=call.tool_call_id,
            target="evidence",
        )
        guarded_reference = self._output_guard.guard(
            cast(JsonValue, reference),
            source_type=ContentSourceType.TOOL_OUTPUT,
            source_id=call.tool_call_id,
            target="evidence",
        )
        if (
            guarded.disposition is OutputDisposition.BLOCKED
            or guarded.content is None
            or guarded_reference.disposition is OutputDisposition.BLOCKED
            or guarded_reference.content is None
        ):
            raise self._validation_error(
                call.task_id,
                "SECRET_DETECTED",
                "Evidence content was blocked by the security policy",
            )
        safe_data = cast(dict[str, JsonValue], guarded.content)
        reference = cast(dict[str, JsonValue], guarded_reference.content)
        content_changed = safe_data != draft.content.data.root
        checksum = (
            hashlib.sha256(
                json.dumps(
                    safe_data,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if content_changed
            else draft.content.checksum
        )
        reference.update(
            {
                "content_source_type": (
                    ContentSourceType.RETRIEVED_DOCUMENT.value
                    if draft.source_type is EvidenceType.DOCUMENT
                    else ContentSourceType.DATABASE_RESULT.value
                    if draft.source_type is EvidenceType.DATABASE
                    else ContentSourceType.TOOL_OUTPUT.value
                ),
                "trust_level": trust_level.value,
                "produced_by": call.tool_name,
                "content_hash": checksum,
                "injection_findings": findings,
                "quarantined": quarantined,
                "redacted": bool(guarded.redactions or content_changed),
            }
        )
        return EvidenceDraft(
            source_type=draft.source_type,
            source_reference=draft.source_reference.model_copy(
                update={"reference": JsonObject(reference)}
            ),
            content=draft.content.model_copy(
                update={"data": JsonObject(safe_data), "checksum": checksum}
            ),
        )

    def add(self, evidence: EvidenceItem, *, tenant_id: str) -> EvidenceAddResult:
        """Append one item or return the existing canonical logical duplicate."""
        with self._lock:
            if self._database is not None:
                self._refresh_database_items(tenant_id)
            detached = _copy_item(evidence)
            fingerprint = evidence_fingerprint(detached)
            existing_count = sum(
                item.task_id == detached.task_id
                and self._item_tenants.get(item.evidence_id) == tenant_id
                for item in self._items.values()
            )
            if (
                existing_count >= self._max_items_per_task
                and (tenant_id, detached.task_id, fingerprint) not in self._fingerprints
            ):
                raise self._validation_error(
                    detached.task_id,
                    "EVIDENCE_LIMIT_EXCEEDED",
                    "Task evidence exceeds the configured item limit",
                )
            result = self._add_locked(detached, tenant_id=tenant_id)
            try:
                self._persist_created((result,), tenant_id=tenant_id)
            except Exception:
                self._rollback_created((result,), tenant_id=tenant_id)
                raise
            return result.model_copy(deep=True)

    def get(self, evidence_id: str, *, task_id: str, tenant_id: str) -> EvidenceItem:
        """Return a detached immutable item only in the exact task and tenant scope."""
        with self._lock:
            if self._database is not None:
                self._refresh_database_items(tenant_id)
            item = self._items.get(evidence_id)
            if (
                item is None
                or item.task_id != task_id
                or self._item_tenants.get(evidence_id) != tenant_id
            ):
                raise self._not_found_error(task_id, evidence_id)
            return _copy_item(item)

    def list(self, task_id: str, *, tenant_id: str) -> tuple[EvidenceItem, ...]:
        """Return a detached task-scoped snapshot in append order."""
        with self._lock:
            if self._database is not None:
                self._refresh_database_items(tenant_id)
            return tuple(
                _copy_item(item)
                for item in self._items.values()
                if item.task_id == task_id and self._item_tenants.get(item.evidence_id) == tenant_id
            )

    def list_for_task(self, task_id: str, *, tenant_id: str) -> tuple[EvidenceItem, ...]:
        """Backward-compatible alias for task-scoped listing."""
        return self.list(task_id, tenant_id=tenant_id)

    def list_for_call(
        self, tool_call_id: str, *, task_id: str, tenant_id: str
    ) -> tuple[EvidenceItem, ...]:
        """Return detached evidence for a call in insertion order."""
        with self._lock:
            if self._database is not None:
                self._refresh_database_items(tenant_id)
            return tuple(
                _copy_item(item)
                for item in self._items.values()
                if item.tool_call_id == tool_call_id
                and item.task_id == task_id
                and self._item_tenants.get(item.evidence_id) == tenant_id
            )

    def find_by_step(
        self, task_id: str, step_id: str, *, tenant_id: str
    ) -> tuple[EvidenceItem, ...]:
        """Return task-local evidence produced by one step."""
        with self._lock:
            return tuple(
                _copy_item(item)
                for item in self._items.values()
                if item.task_id == task_id
                and item.step_id == step_id
                and self._item_tenants.get(item.evidence_id) == tenant_id
            )

    def find_by_type(
        self,
        task_id: str,
        source_type: EvidenceType,
        *,
        tenant_id: str,
    ) -> tuple[EvidenceItem, ...]:
        """Return task-local evidence of one frozen source type."""
        with self._lock:
            return tuple(
                _copy_item(item)
                for item in self._items.values()
                if item.task_id == task_id
                and item.source_type is source_type
                and self._item_tenants.get(item.evidence_id) == tenant_id
            )

    def validate_reference(self, task_id: str, evidence_id: str, *, tenant_id: str) -> bool:
        """Return whether an evidence identifier exists and belongs to the task."""
        with self._lock:
            item = self._items.get(evidence_id)
            return (
                item is not None
                and item.task_id == task_id
                and self._item_tenants.get(evidence_id) == tenant_id
            )

    def trace_lineage(self, task_id: str, evidence_id: str, *, tenant_id: str) -> LineageTrace:
        """Return a deterministic root-first graph and all safe structural issues."""
        with self._lock:
            root = self._items.get(evidence_id)
            if (
                root is None
                or root.task_id != task_id
                or self._item_tenants.get(evidence_id) != tenant_id
            ):
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
                    if parent.task_id != task_id or self._item_tenants.get(parent_id) != tenant_id:
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

    def snapshot(self, *, tenant_id: str, task_id: str | None = None) -> EvidenceLedgerSnapshot:
        """Create a detached serializable ledger snapshot, optionally task-scoped."""
        with self._lock:
            if self._database is not None:
                self._refresh_database_items(tenant_id)
            items = tuple(
                _copy_item(item)
                for item in self._items.values()
                if self._item_tenants.get(item.evidence_id) == tenant_id
                and (task_id is None or item.task_id == task_id)
            )
            return EvidenceLedgerSnapshot(items=items)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: EvidenceLedgerSnapshot,
        *,
        tenant_id: str,
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
                key = (tenant_id, stored.task_id, fingerprint)
                if key in ledger._fingerprints:
                    raise ledger._validation_error(
                        stored.task_id,
                        "EVIDENCE_SNAPSHOT_DUPLICATE",
                        "Evidence snapshot contains duplicate logical records",
                    )
                ledger._items[stored.evidence_id] = stored
                ledger._item_tenants[stored.evidence_id] = tenant_id
                ledger._fingerprints[key] = stored.evidence_id
            ledger._validate_restored_lineage()
        return ledger

    def _add_many_locked(
        self,
        items: tuple[EvidenceItem, ...],
        task_id: str,
        tenant_id: str,
    ) -> tuple[EvidenceAddResult, ...]:
        """Validate a batch before committing any newly created logical records."""
        pending_fingerprints: dict[tuple[str, str, str], EvidenceItem] = {}
        results: list[EvidenceAddResult] = []
        for item in items:
            self._validate_new_item(item, tenant_id=tenant_id)
            fingerprint = evidence_fingerprint(item)
            key = (tenant_id, item.task_id, fingerprint)
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
            self._item_tenants[stored.evidence_id] = tenant_id
            self._fingerprints[key] = stored.evidence_id
        return tuple(results)

    def close(self) -> None:
        """Close the optional durable Evidence connection."""
        with self._lock:
            if self._owns_database and self._database is not None:
                self._database.dispose()
                self._database = None

    def _persist_created(self, results: tuple[EvidenceAddResult, ...], *, tenant_id: str) -> None:
        if self._database is None:
            return
        with self._database.session() as session:
            for result in results:
                if result.created:
                    item = result.evidence
                    session.add(
                        WorkflowEvidenceRow(
                            evidence_id=item.evidence_id,
                            task_id=item.task_id,
                            tenant_id=tenant_id,
                            fingerprint=evidence_fingerprint(item),
                            payload_json=item.model_dump_json(),
                        )
                    )

    def _refresh_database_items(self, tenant_id: str) -> None:
        assert self._database is not None
        stale_ids = [
            evidence_id for evidence_id, owner in self._item_tenants.items() if owner == tenant_id
        ]
        for evidence_id in stale_ids:
            item = self._items.pop(evidence_id)
            self._item_tenants.pop(evidence_id, None)
            self._fingerprints.pop(
                (tenant_id, item.task_id, evidence_fingerprint(item)),
                None,
            )
        with self._database.session() as session:
            payloads = session.scalars(
                select(WorkflowEvidenceRow.payload_json)
                .where(WorkflowEvidenceRow.tenant_id == tenant_id)
                .order_by(WorkflowEvidenceRow.sequence_id)
            )
            for payload in payloads:
                item = EvidenceItem.model_validate_json(payload)
                self._items[item.evidence_id] = item
                self._item_tenants[item.evidence_id] = tenant_id
                self._fingerprints[(tenant_id, item.task_id, evidence_fingerprint(item))] = (
                    item.evidence_id
                )

    def _rollback_created(self, results: tuple[EvidenceAddResult, ...], *, tenant_id: str) -> None:
        for result in results:
            if not result.created:
                continue
            item = self._items.pop(result.evidence.evidence_id, None)
            if item is not None:
                self._item_tenants.pop(item.evidence_id, None)
                self._fingerprints.pop((tenant_id, item.task_id, evidence_fingerprint(item)), None)

    def _add_locked(self, item: EvidenceItem, *, tenant_id: str) -> EvidenceAddResult:
        if item.evidence_id in self._items:
            raise self._validation_error(
                item.task_id,
                "EVIDENCE_ID_CONFLICT",
                "Evidence identifier already exists",
            )
        self._validate_new_item(item, tenant_id=tenant_id)
        fingerprint = evidence_fingerprint(item)
        key = (tenant_id, item.task_id, fingerprint)
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
        self._item_tenants[stored.evidence_id] = tenant_id
        self._fingerprints[key] = stored.evidence_id
        return EvidenceAddResult(evidence=_copy_item(stored), created=True)

    def _validate_new_item(self, item: EvidenceItem, *, tenant_id: str) -> None:
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
            if parent.task_id != item.task_id or self._item_tenants.get(parent_id) != tenant_id:
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
