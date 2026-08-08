"""Approval contract, persistence, restart, and optimistic-lock tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from copilot.contracts import (
    ApprovalRequest,
    ApprovalResolutionAction,
    ApprovalStatus,
    JsonObject,
)
from copilot.persistence.approval_repository import ApprovalRepository

NOW = datetime(2026, 8, 2, 8, 0, tzinfo=UTC)


def pending_approval() -> ApprovalRequest:
    """Build one complete frozen v1.1 pending approval."""
    return ApprovalRequest(
        approval_id="AP-001",
        task_id="T-001",
        tenant_id="TENANT-A",
        step_id="T-001:query-supplier-quality-data",
        planning_version=1,
        tool_name="database_query",
        tool_version="1.0.0",
        input_schema_fingerprint="schema-fingerprint",
        original_action_fingerprint="original-fingerprint",
        controlled_scope=("quality.v1",),
        editable_fields=("row_limit",),
        proposed_arguments=JsonObject({"row_limit": 10000}),
        reason="Controlled database access",
        requester="U-001",
        required_role="quality_data_approver",
        status=ApprovalStatus.PENDING,
        policy_version="quality-policy.v1",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def approved(approval: ApprovalRequest) -> ApprovalRequest:
    """Resolve the fixture without changing its arguments."""
    return approval.model_copy(
        update={
            "status": ApprovalStatus.APPROVED,
            "resolution_action": ApprovalResolutionAction.APPROVE,
            "resolved_arguments": approval.proposed_arguments,
            "resolved_action_fingerprint": approval.original_action_fingerprint,
            "approver": "A-001",
            "decided_at": NOW + timedelta(minutes=1),
            "version": 2,
        }
    )


def test_approval_serialization_preserves_complete_pending_and_resolved_versions() -> None:
    pending = pending_approval()
    resolved = approved(pending)

    assert ApprovalRequest.model_validate_json(pending.model_dump_json()) == pending
    assert ApprovalRequest.model_validate_json(resolved.model_dump_json()) == resolved
    assert resolved.action_fingerprint == "original-fingerprint"


def test_repository_persists_and_restores_current_approval_version(tmp_path: Path) -> None:
    database = tmp_path / "approval.db"
    pending = pending_approval()
    first = ApprovalRepository(database)
    first.create(pending)
    first.close()

    second = ApprovalRepository(database)
    try:
        assert second.get("AP-001") == pending
        second.resolve(pending, approved(pending))
        assert second.history("AP-001") == (pending, approved(pending))
    finally:
        second.close()

    restored = ApprovalRepository(database)
    try:
        assert restored.get("AP-001").status is ApprovalStatus.APPROVED
        assert restored.get_pending_for_task("T-001") == ()
        assert restored.history("AP-001") == (pending, approved(pending))
    finally:
        restored.close()


def test_only_one_concurrent_resolution_wins() -> None:
    repository = ApprovalRepository()
    pending = pending_approval()
    repository.create(pending)
    resolved = approved(pending)
    barrier = Barrier(2)

    def attempt() -> str:
        barrier.wait()
        try:
            repository.resolve(pending, resolved)
        except ValueError:
            return "conflict"
        return "resolved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _index: attempt(), range(2)))

    assert sorted(outcomes) == ["conflict", "resolved"]
    assert repository.get("AP-001") == resolved


def test_repository_reads_cannot_mutate_persisted_argument_history() -> None:
    repository = ApprovalRepository()
    pending = pending_approval()
    repository.create(pending)

    returned = repository.get("AP-001")
    returned.proposed_arguments.root["row_limit"] = 1

    assert repository.get("AP-001").proposed_arguments.root["row_limit"] == 10000


def test_production_mode_requires_the_formal_approval_migration(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="persistence migration is required"):
        ApprovalRepository(tmp_path / "unmigrated.db", initialize_schema=False)
