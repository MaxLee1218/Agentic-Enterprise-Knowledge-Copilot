"""ArtifactService authorization, filename, and integrity tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from copilot.contracts import Artifact, ArtifactType
from copilot.persistence.artifact_repository import LocalArtifactRepository
from copilot.services.artifact_service import (
    ArtifactNotFoundError,
    ArtifactService,
    ArtifactUnavailableError,
    safe_artifact_filename,
)
from copilot.services.task_intake import TrustedCallerContext
from copilot.services.task_views import TaskSummaryView

NOW = datetime(2026, 8, 2, tzinfo=UTC)


class _TaskAccess:
    def get_task(
        self,
        task_id: str,
        _caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> TaskSummaryView:
        del trace_id
        return TaskSummaryView(
            task_id=task_id,
            trace_id="TRACE-001",
            status="COMPLETED",
            task_type="supplier_quality_analysis.v1",
            created_at=NOW,
            started_at=NOW,
            completed_at=NOW,
            cancelled_at=None,
            current_step=None,
            task_summary="safe",
            pending_approval_id=None,
            step_count=4,
            evidence_count=1,
            artifact_count=1,
            error_summary=None,
        )


def _caller() -> TrustedCallerContext:
    return TrustedCallerContext(
        user_id="U-001",
        tenant_id="TENANT-001",
        data_scope=("quality.v1",),
    )


def _artifact(repository: LocalArtifactRepository, task_id: str = "T-001") -> Artifact:
    return repository.write(
        artifact_id="A-001",
        task_id=task_id,
        artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
        filename="safe.json",
        media_type="application/json",
        content=b"{}",
        generator_version="v1",
        evidence_ids=("E-001",),
    )


def test_service_hides_cross_task_artifact_and_rejects_tampered_bytes(tmp_path: Path) -> None:
    repository = LocalArtifactRepository(tmp_path, clock=lambda: NOW)
    artifact = _artifact(repository)
    service = ArtifactService(repository=repository, tasks=_TaskAccess())
    with pytest.raises(ArtifactNotFoundError):
        service.get_task_artifact("T-OTHER", artifact.artifact_id, _caller())
    repository.path_for(artifact).write_bytes(b"[]")
    with pytest.raises(ArtifactUnavailableError):
        service.get_task_artifact("T-001", artifact.artifact_id, _caller())


def test_safe_filename_removes_path_and_header_control_characters(tmp_path: Path) -> None:
    repository = LocalArtifactRepository(tmp_path, clock=lambda: NOW)
    artifact = _artifact(repository)
    filename = safe_artifact_filename("../unsafe\r\nname", artifact)
    assert filename == "unsafe__name.json"
    assert "/" not in filename
    assert "\r" not in filename
    assert "\n" not in filename
