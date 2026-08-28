"""Additional atomic Artifact Repository safety and query tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from copilot.contracts import ArtifactType
from copilot.persistence.artifact_repository import LocalArtifactRepository
from copilot.services.workflows.ports import ArtifactSizeLimitError

TENANT_ID = "TENANT-DEMO"


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
        tenant_id=TENANT_ID,
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
            tenant_id=TENANT_ID,
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
            tenant_id=TENANT_ID,
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

    def fail_metadata(_artifact: object, *, tenant_id: str) -> None:
        assert tenant_id == TENANT_ID
        raise OSError("controlled metadata failure")

    monkeypatch.setattr(repository, "_save_metadata", fail_metadata)
    with pytest.raises(OSError, match="metadata failure"):
        _write(repository)
    assert not repository.exists("A-001", tenant_id=TENANT_ID)
    assert list(tmp_path.iterdir()) == []


def test_repository_adopts_deterministic_bytes_after_process_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry publishes one metadata row after a crash between bytes and metadata."""
    repository = _repository(tmp_path)

    with monkeypatch.context() as crash:

        def terminate_after_bytes(_artifact: object, *, tenant_id: str) -> None:
            assert tenant_id == TENANT_ID
            raise SystemExit(137)

        crash.setattr(repository, "_save_metadata", terminate_after_bytes)
        with pytest.raises(SystemExit, match="137"):
            _write(repository, artifact_id="A-STABLE-COMMAND")

    orphan = tmp_path / "A-STABLE-COMMAND.json"
    assert orphan.read_bytes() == b"{}"
    assert not repository.exists("A-STABLE-COMMAND", tenant_id=TENANT_ID)

    _write(repository, artifact_id="A-STABLE-COMMAND")
    artifact = repository.get("A-STABLE-COMMAND", tenant_id=TENANT_ID)
    assert repository.path_for(artifact) == orphan.resolve()
    assert repository.list_by_task("T-001", tenant_id=TENANT_ID) == (artifact,)
    assert tuple(tmp_path.iterdir()) == (orphan,)
