"""Artifact path, UTF-8 content, metadata, and integrity tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from copilot.contracts import ArtifactType
from copilot.persistence.artifact_repository import LocalArtifactRepository


def _clock() -> datetime:
    return datetime(2026, 7, 22, 8, 0, tzinfo=UTC)


def test_artifact_repository_creates_directory_and_commits_utf8(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "artifacts"
    repository = LocalArtifactRepository(root, clock=_clock)
    content = "供应商质量报告".encode()
    artifact = repository.write(
        artifact_id="A-001",
        task_id="T-001",
        artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
        filename="supplier-quality-analysis-T-001.json",
        media_type="application/json",
        content=content,
        generator_version="v1",
        evidence_ids=("E-001",),
    )
    assert repository.path_for(artifact).read_text(encoding="utf-8") == "供应商质量报告"
    assert artifact.size_bytes == len(content)
    assert artifact.evidence_ids == ("E-001",)


@pytest.mark.parametrize("filename", ["../escape.json", "/tmp/escape.json", "a/b.json"])
def test_artifact_repository_rejects_path_traversal(tmp_path: Path, filename: str) -> None:
    repository = LocalArtifactRepository(tmp_path, clock=_clock)
    with pytest.raises(ValueError, match="filename"):
        repository.write(
            artifact_id="A-001",
            task_id="T-001",
            artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
            filename=filename,
            media_type="application/json",
            content=b"{}",
            generator_version="v1",
            evidence_ids=("E-001",),
        )
