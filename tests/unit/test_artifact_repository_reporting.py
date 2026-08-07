"""Additional atomic Artifact Repository safety and query tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from copilot.contracts import ArtifactType
from copilot.persistence.artifact_repository import LocalArtifactRepository
from copilot.services.workflows.ports import ArtifactSizeLimitError


def _repository(tmp_path: Path, *, max_size_bytes: int = 1024) -> LocalArtifactRepository:
    return LocalArtifactRepository(
        tmp_path,
        clock=lambda: datetime(2026, 7, 26, tzinfo=UTC),
        max_size_bytes=max_size_bytes,
    )


def _write(repository: LocalArtifactRepository, *, artifact_id: str = "A-001") -> None:
    repository.write(
        artifact_id=artifact_id,
        task_id="T-001",
        artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
        filename=f"{artifact_id}.json",
        media_type="application/json",
        content=b"{}",
        generator_version="report_generator.v1",
        evidence_ids=("E-001",),
    )


def test_repository_enforces_extension_and_size_limit(tmp_path: Path) -> None:
    repository = _repository(tmp_path, max_size_bytes=2)
    with pytest.raises(ValueError, match="extension"):
        repository.write(
            artifact_id="A-001",
            task_id="T-001",
            artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
            filename="report.pdf",
            media_type="application/json",
            content=b"{}",
            generator_version="report_generator.v1",
            evidence_ids=("E-001",),
        )
    with pytest.raises(ArtifactSizeLimitError, match="size"):
        repository.write(
            artifact_id="A-002",
            task_id="T-001",
            artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
            filename="report.json",
            media_type="application/json",
            content=b"123",
            generator_version="report_generator.v1",
            evidence_ids=("E-001",),
        )


def test_repository_compensates_metadata_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)

    def fail_metadata(_artifact: object) -> None:
        raise OSError("controlled metadata failure")

    monkeypatch.setattr(repository, "_save_metadata", fail_metadata)
    with pytest.raises(OSError, match="metadata failure"):
        _write(repository)
    assert not repository.exists("A-001")
    assert list(tmp_path.iterdir()) == []
