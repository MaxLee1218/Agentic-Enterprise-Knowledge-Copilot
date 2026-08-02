"""Thread-safe immutable-version persistence for v1.1 approval requests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

from copilot.contracts import ApprovalRequest, ApprovalStatus


class ApprovalRepository:
    """Persist pending approvals and resolve each one with optimistic concurrency."""

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._approvals: dict[str, ApprovalRequest] = {}
        self._history: dict[str, list[ApprovalRequest]] = {}
        self._lock = RLock()
        self._database = (
            sqlite3.connect(database_path, check_same_thread=False)
            if database_path is not None
            else None
        )
        if self._database is not None:
            try:
                if initialize_schema:
                    self._setup()
                else:
                    self._require_migration()
                self._load()
            except Exception:
                self._database.close()
                self._database = None
                raise

    def create(self, approval: ApprovalRequest) -> None:
        """Create one pending approval exactly once."""
        if approval.status is not ApprovalStatus.PENDING or approval.version != 1:
            raise ValueError("new approval must be pending at version 1")
        stored = _snapshot(approval)
        with self._lock:
            if approval.approval_id in self._approvals:
                raise ValueError("approval already exists")
            if self._database is not None:
                with self._database:
                    self._database.execute(
                        """
                        INSERT INTO workflow_approvals
                        (approval_id, task_id, step_id, status, version, payload_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            approval.approval_id,
                            approval.task_id,
                            approval.step_id,
                            approval.status.value,
                            approval.version,
                            stored.model_dump_json(),
                        ),
                    )
                    self._database.execute(
                        """
                        INSERT INTO workflow_approval_history
                        (approval_id, version, payload_json) VALUES (?, ?, ?)
                        """,
                        (stored.approval_id, stored.version, stored.model_dump_json()),
                    )
            self._approvals[stored.approval_id] = stored
            self._history[stored.approval_id] = [stored]

    def get(self, approval_id: str) -> ApprovalRequest:
        """Return the current immutable approval version."""
        with self._lock:
            try:
                return _snapshot(self._approvals[approval_id])
            except KeyError as exc:
                raise KeyError("approval was not found") from exc

    def get_pending_for_task(self, task_id: str) -> tuple[ApprovalRequest, ...]:
        """Return pending approvals for a task in deterministic creation order."""
        with self._lock:
            return tuple(
                _snapshot(approval)
                for approval in self._approvals.values()
                if approval.task_id == task_id and approval.status is ApprovalStatus.PENDING
            )

    def list_by_task(self, task_id: str) -> tuple[ApprovalRequest, ...]:
        """Return current approval versions for one task."""
        with self._lock:
            return tuple(
                _snapshot(approval)
                for approval in self._approvals.values()
                if approval.task_id == task_id
            )

    def exists(self, approval_id: str) -> bool:
        """Return whether an approval identifier is present."""
        with self._lock:
            return approval_id in self._approvals

    def history(self, approval_id: str) -> tuple[ApprovalRequest, ...]:
        """Return every immutable version from pending through final resolution."""
        with self._lock:
            try:
                return tuple(_snapshot(item) for item in self._history[approval_id])
            except KeyError as exc:
                raise KeyError("approval was not found") from exc

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
            authoritative = self._approvals.get(pending.approval_id)
            if authoritative != pending:
                raise ValueError("approval compare-and-swap conflict")
            if self._database is not None:
                with self._database:
                    updated = self._database.execute(
                        """
                        UPDATE workflow_approvals
                        SET status = ?, version = ?, payload_json = ?
                        WHERE approval_id = ? AND status = ? AND version = ? AND payload_json = ?
                        """,
                        (
                            stored.status.value,
                            stored.version,
                            stored.model_dump_json(),
                            pending.approval_id,
                            ApprovalStatus.PENDING.value,
                            pending.version,
                            pending.model_dump_json(),
                        ),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("approval compare-and-swap conflict")
                    self._database.execute(
                        """
                        INSERT INTO workflow_approval_history
                        (approval_id, version, payload_json) VALUES (?, ?, ?)
                        """,
                        (stored.approval_id, stored.version, stored.model_dump_json()),
                    )
            self._approvals[pending.approval_id] = stored
            self._history[pending.approval_id].append(stored)

    def close(self) -> None:
        """Close the optional durable SQLite connection."""
        with self._lock:
            if self._database is not None:
                self._database.close()
                self._database = None

    def _setup(self) -> None:
        assert self._database is not None
        self._database.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_approvals (
                approval_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_approvals_task_status
                ON workflow_approvals(task_id, status);
            CREATE INDEX IF NOT EXISTS idx_workflow_approvals_task_step
                ON workflow_approvals(task_id, step_id);
            CREATE TABLE IF NOT EXISTS workflow_approval_history (
                approval_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (approval_id, version)
            );
            """
        )
        self._database.commit()

    def _load(self) -> None:
        assert self._database is not None
        for row in self._database.execute(
            "SELECT payload_json FROM workflow_approvals ORDER BY rowid"
        ):
            approval = ApprovalRequest.model_validate_json(row[0])
            self._approvals[approval.approval_id] = approval
        for row in self._database.execute(
            "SELECT payload_json FROM workflow_approval_history ORDER BY approval_id, version"
        ):
            approval = ApprovalRequest.model_validate_json(row[0])
            self._history.setdefault(approval.approval_id, []).append(approval)
        for approval_id, approval in self._approvals.items():
            self._history.setdefault(approval_id, [approval])

    def _require_migration(self) -> None:
        """Fail production startup when migration 0001 has not been applied."""
        assert self._database is not None
        present = {
            str(row[0])
            for row in self._database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
                ("workflow_approvals", "workflow_approval_history"),
            )
        }
        if present != {"workflow_approvals", "workflow_approval_history"}:
            raise RuntimeError(
                "Approval persistence migration 0001_approval_requests.sql is required"
            )


def _snapshot(approval: ApprovalRequest) -> ApprovalRequest:
    """Deep-copy nested JSON so callers cannot mutate persisted approval history."""
    return ApprovalRequest.model_validate_json(approval.model_dump_json())


__all__ = ["ApprovalRepository"]
