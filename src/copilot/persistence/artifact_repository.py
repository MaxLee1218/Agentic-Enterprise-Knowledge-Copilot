"""Controlled local artifact persistence for the deterministic offline workflow."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock

from copilot.contracts import Artifact, ArtifactType


class LocalArtifactRepository:
    """Atomically persist immutable artifacts beneath one configured root."""

    def __init__(self, root: Path, *, clock: Callable[[], datetime]) -> None:
        self._root = root.resolve()
        self._clock = clock
        self._artifacts: dict[str, Artifact] = {}
        self._lock = RLock()

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
        candidate = Path(filename)
        if candidate.name != filename or candidate.is_absolute() or filename in {".", ".."}:
            raise ValueError("artifact filename must be one safe path component")
        self._root.mkdir(parents=True, exist_ok=True)
        target = (self._root / filename).resolve()
        if target.parent != self._root:
            raise ValueError("artifact path escaped the configured root")
        checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        with self._lock:
            existing = self._artifacts.get(artifact_id)
            if existing is not None:
                if existing.checksum != checksum:
                    raise ValueError("artifact identifier already exists with different content")
                return existing
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".artifact-", suffix=".tmp", dir=self._root
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
            if not target.is_file() or target.stat().st_size != len(content):
                raise OSError("artifact commit verification failed")
            artifact = Artifact(
                artifact_id=artifact_id,
                task_id=task_id,
                type=artifact_type,
                location=str(target),
                media_type=media_type,
                checksum=checksum,
                size_bytes=len(content),
                generator_version=generator_version,
                evidence_ids=evidence_ids,
                created_at=self._clock(),
            )
            self._artifacts[artifact_id] = artifact
            return artifact

    def get(self, artifact_id: str) -> Artifact:
        """Return one committed artifact."""
        with self._lock:
            return self._artifacts[artifact_id]

    def path_for(self, artifact: Artifact) -> Path:
        """Resolve and revalidate a committed artifact path beneath the configured root."""
        path = Path(artifact.location).resolve()
        if path.parent != self._root:
            raise ValueError("artifact location escaped the configured root")
        return path
