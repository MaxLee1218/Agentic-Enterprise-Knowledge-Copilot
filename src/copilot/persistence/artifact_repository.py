"""Controlled local artifact persistence for the deterministic offline workflow."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock

from copilot.contracts import Artifact, ArtifactType

_EXTENSION_BY_TYPE = {
    ArtifactType.QUALITY_ANALYSIS_REPORT_JSON: ".json",
    ArtifactType.QUALITY_ANALYSIS_REPORT_PDF: ".pdf",
}


@dataclass(frozen=True, slots=True)
class WrittenArtifact:
    """Final file facts returned by the atomic writer."""

    path: Path
    checksum: str
    size_bytes: int


class AtomicArtifactWriter:
    """Safely commit bounded immutable bytes beneath one configured root."""

    def __init__(self, root: Path, *, max_size_bytes: int) -> None:
        if max_size_bytes < 1:
            raise ValueError("max_size_bytes must be positive")
        self.root = root.resolve()
        self.max_size_bytes = max_size_bytes

    def write(self, filename: str, content: bytes) -> WrittenArtifact:
        """Write via a same-directory temporary file and atomic rename."""
        if not content:
            raise ValueError("artifact content must not be empty")
        if len(content) > self.max_size_bytes:
            raise ValueError("artifact content exceeds the configured size limit")
        candidate = Path(filename)
        if candidate.name != filename or candidate.is_absolute() or filename in {".", ".."}:
            raise ValueError("artifact filename must be one safe path component")
        self.root.mkdir(parents=True, exist_ok=True)
        target = (self.root / filename).resolve()
        if target.parent != self.root:
            raise ValueError("artifact path escaped the configured root")
        if target.exists():
            raise FileExistsError("artifact target already exists")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".artifact-", suffix=".tmp", dir=self.root
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        size = target.stat().st_size
        if not target.is_file() or size != len(content):
            target.unlink(missing_ok=True)
            raise OSError("artifact commit verification failed")
        disk_content = target.read_bytes()
        checksum = f"sha256:{hashlib.sha256(disk_content).hexdigest()}"
        if disk_content != content:
            target.unlink(missing_ok=True)
            raise OSError("artifact content verification failed")
        return WrittenArtifact(path=target, checksum=checksum, size_bytes=size)

    def delete(self, path: Path) -> None:
        """Remove one known governed file as a compensation action."""
        resolved = path.resolve()
        if resolved.parent != self.root:
            raise ValueError("artifact path escaped the configured root")
        resolved.unlink(missing_ok=True)


class LocalArtifactRepository:
    """Atomically persist immutable artifacts beneath one configured root."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime],
        max_size_bytes: int = 10 * 1024 * 1024,
        database_path: Path | None = None,
    ) -> None:
        self._root = root.resolve()
        self._clock = clock
        self._writer = AtomicArtifactWriter(self._root, max_size_bytes=max_size_bytes)
        self._artifacts: dict[str, Artifact] = {}
        self._lock = RLock()
        self._database = (
            sqlite3.connect(database_path, check_same_thread=False)
            if database_path is not None
            else None
        )
        if self._database is not None:
            self._database.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            self._database.commit()
            for row in self._database.execute(
                "SELECT payload_json FROM workflow_artifacts ORDER BY rowid"
            ):
                artifact = Artifact.model_validate_json(row[0])
                self._artifacts[artifact.artifact_id] = artifact

    def write(
        self,
        *,
        artifact_id: str,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        media_type: str,
        content: bytes,
        generator_version: str,
        evidence_ids: tuple[str, ...],
    ) -> Artifact:
        """Validate a safe filename and atomically commit non-empty UTF-8/report bytes."""
        if not content:
            raise ValueError("artifact content must not be empty")
        if not evidence_ids:
            raise ValueError("artifact must cite evidence")
        checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        expected_extension = _EXTENSION_BY_TYPE[artifact_type]
        if Path(filename).suffix.lower() != expected_extension:
            raise ValueError("artifact extension does not match its type")
        with self._lock:
            existing = self._artifacts.get(artifact_id)
            if existing is not None:
                if existing.checksum != checksum:
                    raise ValueError("artifact identifier already exists with different content")
                return existing
            written = self._writer.write(filename, content)
            artifact = Artifact(
                artifact_id=artifact_id,
                task_id=task_id,
                type=artifact_type,
                location=str(written.path),
                media_type=media_type,
                checksum=written.checksum,
                size_bytes=written.size_bytes,
                generator_version=generator_version,
                evidence_ids=evidence_ids,
                created_at=self._clock(),
            )
            try:
                self._save_metadata(artifact)
            except Exception:
                self._writer.delete(written.path)
                raise
            return artifact

    def get(self, artifact_id: str) -> Artifact:
        """Return one committed artifact."""
        with self._lock:
            return self._artifacts[artifact_id]

    def get_by_id(self, artifact_id: str) -> Artifact:
        """Return one Artifact using the repository-style query name."""
        return self.get(artifact_id)

    def list_by_task(self, task_id: str) -> tuple[Artifact, ...]:
        """List Task-owned metadata in deterministic creation/identifier order."""
        with self._lock:
            return tuple(
                sorted(
                    (
                        artifact
                        for artifact in self._artifacts.values()
                        if artifact.task_id == task_id
                    ),
                    key=lambda artifact: (artifact.created_at, artifact.artifact_id),
                )
            )

    def exists(self, artifact_id: str) -> bool:
        """Report whether metadata has been committed for an identifier."""
        with self._lock:
            return artifact_id in self._artifacts

    def path_for(self, artifact: Artifact) -> Path:
        """Resolve and revalidate a committed artifact path beneath the configured root."""
        path = Path(artifact.location).resolve()
        if path.parent != self._root:
            raise ValueError("artifact location escaped the configured root")
        return path

    def delete(self, artifact_id: str) -> None:
        """Compensate an invalid Artifact while it is not published to TaskResult."""
        with self._lock:
            artifact = self._artifacts.pop(artifact_id)
            self._writer.delete(Path(artifact.location))
            if self._database is not None:
                self._database.execute(
                    "DELETE FROM workflow_artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                )
                self._database.commit()

    def _save_metadata(self, artifact: Artifact) -> None:
        """Commit metadata after final bytes have been verified."""
        if artifact.artifact_id in self._artifacts:
            raise ValueError("artifact identifier already exists")
        if self._database is not None:
            try:
                self._database.execute(
                    "INSERT INTO workflow_artifacts VALUES (?, ?, ?)",
                    (artifact.artifact_id, artifact.task_id, artifact.model_dump_json()),
                )
                self._database.commit()
            except Exception:
                self._database.rollback()
                raise
        self._artifacts[artifact.artifact_id] = artifact

    def close(self) -> None:
        """Close the optional durable Artifact metadata connection."""
        with self._lock:
            if self._database is not None:
                self._database.close()
                self._database = None


__all__ = ["AtomicArtifactWriter", "LocalArtifactRepository", "WrittenArtifact"]
