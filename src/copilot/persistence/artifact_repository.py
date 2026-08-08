"""Controlled local artifact persistence for the deterministic offline workflow."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from copilot.contracts import Artifact, ArtifactType
from copilot.persistence.database import PersistenceDatabase, coerce_database
from copilot.persistence.models import WorkflowArtifactRow
from copilot.security import (
    ContentSourceType,
    OutputDisposition,
    OutputGuard,
    OutputGuardBlockedError,
)
from copilot.services.workflows.ports import ArtifactSizeLimitError

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
            raise ArtifactSizeLimitError("artifact content exceeds the configured size limit")
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
        database_path: PersistenceDatabase | Path | None = None,
        output_guard: OutputGuard | None = None,
        initialize_schema: bool = True,
    ) -> None:
        self._root = root.resolve()
        self._clock = clock
        self._writer = AtomicArtifactWriter(self._root, max_size_bytes=max_size_bytes)
        self._output_guard = output_guard or OutputGuard()
        self._artifacts: dict[str, Artifact] = {}
        self._artifact_tenants: dict[str, str] = {}
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path,
            initialize_schema=initialize_schema,
        )

    def write(
        self,
        *,
        artifact_id: str,
        task_id: str,
        tenant_id: str,
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
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not evidence_ids:
            raise ValueError("artifact must cite evidence")
        guard = self._output_guard.guard_bytes(
            content,
            source_type=ContentSourceType.TOOL_OUTPUT,
            source_id=artifact_id,
            media_type=media_type,
        )
        if guard.disposition is OutputDisposition.BLOCKED or guard.content is None:
            raise OutputGuardBlockedError(
                "artifact content was blocked by the output safety policy"
            )
        if guard.disposition is OutputDisposition.ALLOWED_WITH_REDACTIONS:
            if media_type != "application/json":
                raise OutputGuardBlockedError("binary artifact requires unsafe content redaction")
            content = json.dumps(
                guard.content,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        expected_extension = _EXTENSION_BY_TYPE[artifact_type]
        if Path(filename).suffix.lower() != expected_extension:
            raise ValueError("artifact extension does not match its type")
        with self._lock:
            try:
                existing = self.get(artifact_id, tenant_id=tenant_id)
            except KeyError:
                existing = None
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
                self._save_metadata(artifact, tenant_id=tenant_id)
            except Exception:
                self._writer.delete(written.path)
                raise
            return artifact

    def get(self, artifact_id: str, *, tenant_id: str) -> Artifact:
        """Return one committed artifact."""
        with self._lock:
            if self._database is not None:
                with self._database.session() as session:
                    payload = session.scalar(
                        select(WorkflowArtifactRow.payload_json).where(
                            WorkflowArtifactRow.artifact_id == artifact_id,
                            WorkflowArtifactRow.tenant_id == tenant_id,
                        )
                    )
                    if payload is None:
                        raise KeyError(artifact_id)
                    return Artifact.model_validate_json(payload)
            if self._artifact_tenants.get(artifact_id) != tenant_id:
                raise KeyError(artifact_id)
            return self._artifacts[artifact_id]

    def get_by_id(self, artifact_id: str, *, tenant_id: str) -> Artifact:
        """Return one Artifact using the repository-style query name."""
        return self.get(artifact_id, tenant_id=tenant_id)

    def list_by_task(self, task_id: str, *, tenant_id: str) -> tuple[Artifact, ...]:
        """List Task-owned metadata in deterministic creation/identifier order."""
        with self._lock:
            if self._database is not None:
                with self._database.session() as session:
                    payloads = session.scalars(
                        select(WorkflowArtifactRow.payload_json)
                        .where(
                            WorkflowArtifactRow.task_id == task_id,
                            WorkflowArtifactRow.tenant_id == tenant_id,
                        )
                        .order_by(WorkflowArtifactRow.sequence_id)
                    )
                    artifacts = tuple(Artifact.model_validate_json(item) for item in payloads)
                    return tuple(
                        sorted(
                            artifacts,
                            key=lambda artifact: (artifact.created_at, artifact.artifact_id),
                        )
                    )
            return tuple(
                sorted(
                    (
                        artifact
                        for artifact in self._artifacts.values()
                        if artifact.task_id == task_id
                        and self._artifact_tenants.get(artifact.artifact_id) == tenant_id
                    ),
                    key=lambda artifact: (artifact.created_at, artifact.artifact_id),
                )
            )

    def exists(self, artifact_id: str, *, tenant_id: str) -> bool:
        """Report whether metadata has been committed for an identifier."""
        try:
            self.get(artifact_id, tenant_id=tenant_id)
        except KeyError:
            return False
        return True

    def path_for(self, artifact: Artifact) -> Path:
        """Resolve and revalidate a committed artifact path beneath the configured root."""
        path = Path(artifact.location).resolve()
        if path.parent != self._root:
            raise ValueError("artifact location escaped the configured root")
        return path

    def check_ready(self) -> bool:
        """Verify that the configured root can accept an atomic temporary file."""
        self._root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".readiness-", dir=self._root)
        os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        return True

    def delete(self, artifact_id: str, *, tenant_id: str) -> None:
        """Compensate an invalid Artifact while it is not published to TaskResult."""
        with self._lock:
            artifact = self.get(artifact_id, tenant_id=tenant_id)
            self._writer.delete(Path(artifact.location))
            if self._database is not None:
                with self._database.session() as session:
                    session.execute(
                        delete(WorkflowArtifactRow).where(
                            WorkflowArtifactRow.artifact_id == artifact_id,
                            WorkflowArtifactRow.tenant_id == tenant_id,
                        )
                    )
            else:
                self._artifacts.pop(artifact_id)
                self._artifact_tenants.pop(artifact_id, None)

    def _save_metadata(self, artifact: Artifact, *, tenant_id: str) -> None:
        """Commit metadata after final bytes have been verified."""
        if self._database is not None:
            try:
                with self._database.session() as session:
                    session.add(
                        WorkflowArtifactRow(
                            artifact_id=artifact.artifact_id,
                            task_id=artifact.task_id,
                            tenant_id=tenant_id,
                            payload_json=artifact.model_dump_json(),
                        )
                    )
            except IntegrityError as exc:
                raise ValueError("artifact identifier already exists") from exc
            return
        if artifact.artifact_id in self._artifacts:
            raise ValueError("artifact identifier already exists")
        self._artifacts[artifact.artifact_id] = artifact
        self._artifact_tenants[artifact.artifact_id] = tenant_id

    def close(self) -> None:
        """Close the optional durable Artifact metadata connection."""
        with self._lock:
            if self._owns_database and self._database is not None:
                self._database.dispose()
                self._database = None


__all__ = ["AtomicArtifactWriter", "LocalArtifactRepository", "WrittenArtifact"]
