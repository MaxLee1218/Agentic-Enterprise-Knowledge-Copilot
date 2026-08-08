"""Thread-safe immutable-version persistence for v1.1 approval requests."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from copilot.contracts import ApprovalRequest, ApprovalStatus
from copilot.persistence.database import PersistenceDatabase, coerce_database
from copilot.persistence.models import WorkflowApprovalHistoryRow, WorkflowApprovalRow


class ApprovalRepository:
    """Persist pending approvals and resolve each one with optimistic concurrency."""

    def __init__(
        self,
        database_path: PersistenceDatabase | Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._approvals: dict[str, ApprovalRequest] = {}
        self._history: dict[str, list[ApprovalRequest]] = {}
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path,
            initialize_schema=initialize_schema,
        )

    def create(self, approval: ApprovalRequest) -> None:
        """Create one pending approval exactly once."""
        if approval.status is not ApprovalStatus.PENDING or approval.version != 1:
            raise ValueError("new approval must be pending at version 1")
        stored = _snapshot(approval)
        with self._lock:
            if self._database is None:
                if approval.approval_id in self._approvals:
                    raise ValueError("approval already exists")
                self._approvals[stored.approval_id] = stored
                self._history[stored.approval_id] = [stored]
                return
            try:
                with self._database.session() as session:
                    session.add(
                        WorkflowApprovalRow(
                            approval_id=stored.approval_id,
                            task_id=stored.task_id,
                            step_id=stored.step_id,
                            status=stored.status.value,
                            version=stored.version,
                            payload_json=stored.model_dump_json(),
                        )
                    )
                    session.add(
                        WorkflowApprovalHistoryRow(
                            approval_id=stored.approval_id,
                            version=stored.version,
                            payload_json=stored.model_dump_json(),
                        )
                    )
            except IntegrityError as exc:
                raise ValueError("approval already exists") from exc

    def get(self, approval_id: str) -> ApprovalRequest:
        """Return the current immutable approval version."""
        with self._lock:
            if self._database is None:
                try:
                    return _snapshot(self._approvals[approval_id])
                except KeyError as exc:
                    raise KeyError("approval was not found") from exc
            with self._database.session() as session:
                row = session.get(WorkflowApprovalRow, approval_id)
                if row is None:
                    raise KeyError("approval was not found")
                return ApprovalRequest.model_validate_json(row.payload_json)

    def get_pending_for_task(self, task_id: str) -> tuple[ApprovalRequest, ...]:
        """Return pending approvals for a task in deterministic creation order."""
        return tuple(
            approval
            for approval in self.list_by_task(task_id)
            if approval.status is ApprovalStatus.PENDING
        )

    def list_by_task(self, task_id: str) -> tuple[ApprovalRequest, ...]:
        """Return current approval versions for one task."""
        with self._lock:
            if self._database is None:
                return tuple(
                    _snapshot(approval)
                    for approval in self._approvals.values()
                    if approval.task_id == task_id
                )
            with self._database.session() as session:
                payloads = session.scalars(
                    select(WorkflowApprovalRow.payload_json)
                    .where(WorkflowApprovalRow.task_id == task_id)
                    .order_by(WorkflowApprovalRow.approval_id)
                )
                return tuple(ApprovalRequest.model_validate_json(item) for item in payloads)

    def exists(self, approval_id: str) -> bool:
        """Return whether an approval identifier is present."""
        try:
            self.get(approval_id)
        except KeyError:
            return False
        return True

    def history(self, approval_id: str) -> tuple[ApprovalRequest, ...]:
        """Return every immutable version from pending through final resolution."""
        with self._lock:
            if self._database is None:
                try:
                    return tuple(_snapshot(item) for item in self._history[approval_id])
                except KeyError as exc:
                    raise KeyError("approval was not found") from exc
            with self._database.session() as session:
                payloads = tuple(
                    session.scalars(
                        select(WorkflowApprovalHistoryRow.payload_json)
                        .where(WorkflowApprovalHistoryRow.approval_id == approval_id)
                        .order_by(WorkflowApprovalHistoryRow.version)
                    )
                )
                if not payloads:
                    raise KeyError("approval was not found")
                return tuple(ApprovalRequest.model_validate_json(item) for item in payloads)

    def resolve(self, pending: ApprovalRequest, resolved: ApprovalRequest) -> None:
        """Compare-and-swap one pending version into exactly one resolved version."""
        if pending.approval_id != resolved.approval_id or resolved.version != pending.version + 1:
            raise ValueError("approval resolution version is invalid")
        if (
            pending.status is not ApprovalStatus.PENDING
            or resolved.status is ApprovalStatus.PENDING
        ):
            raise ValueError("approval resolution requires pending to terminal transition")
        stored = _snapshot(resolved)
        with self._lock:
            if self._database is None:
                authoritative = self._approvals.get(pending.approval_id)
                if authoritative != pending:
                    raise ValueError("approval compare-and-swap conflict")
                self._approvals[pending.approval_id] = stored
                self._history[pending.approval_id].append(stored)
                return
            try:
                with self._database.session() as session:
                    result = cast(
                        CursorResult[Any],
                        session.execute(
                            update(WorkflowApprovalRow)
                            .where(
                                WorkflowApprovalRow.approval_id == pending.approval_id,
                                WorkflowApprovalRow.status == ApprovalStatus.PENDING.value,
                                WorkflowApprovalRow.version == pending.version,
                                WorkflowApprovalRow.payload_json == pending.model_dump_json(),
                            )
                            .values(
                                status=stored.status.value,
                                version=stored.version,
                                payload_json=stored.model_dump_json(),
                            )
                        ),
                    )
                    if result.rowcount != 1:
                        raise ValueError("approval compare-and-swap conflict")
                    session.add(
                        WorkflowApprovalHistoryRow(
                            approval_id=stored.approval_id,
                            version=stored.version,
                            payload_json=stored.model_dump_json(),
                        )
                    )
            except IntegrityError as exc:
                raise ValueError("approval compare-and-swap conflict") from exc

    def close(self) -> None:
        """Dispose only compatibility databases owned by this repository."""
        if self._owns_database and self._database is not None:
            self._database.dispose()
            self._database = None


def _snapshot(approval: ApprovalRequest) -> ApprovalRequest:
    """Deep-copy nested JSON so callers cannot mutate persisted approval history."""
    return ApprovalRequest.model_validate_json(approval.model_dump_json())


__all__ = ["ApprovalRepository"]
