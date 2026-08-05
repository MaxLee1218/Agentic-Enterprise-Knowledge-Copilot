"""Authorized Artifact metadata and controlled local download application service."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from copilot.contracts import Artifact, JsonObject
from copilot.policies.permissions import AuthorizationRequest, Permission, PermissionMatrix
from copilot.services.task_intake import TrustedCallerContext
from copilot.services.task_views import TaskArtifactView, TaskSummaryView
from copilot.services.workflows.models import WorkflowAuditRecord
from copilot.services.workflows.ports import IdentifierFactory, WorkflowAuditSink


class ArtifactRepositoryPort(Protocol):
    """Governed Artifact repository operations used by this service."""

    def get_by_id(self, artifact_id: str) -> Artifact: ...

    def list_by_task(self, task_id: str) -> tuple[Artifact, ...]: ...

    def path_for(self, artifact: Artifact) -> Path: ...


class TaskAccessService(Protocol):
    """Task authorization boundary reused by Artifact operations."""

    def get_task(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> TaskSummaryView: ...


class ArtifactServiceError(RuntimeError):
    """Safe typed Artifact failure for centralized transport mapping."""

    def __init__(self, code: str, message: str, *, status_code: int, task_id: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.task_id = task_id


class ArtifactNotFoundError(ArtifactServiceError):
    """Raised for unknown or cross-task Artifact identifiers."""

    def __init__(self, task_id: str) -> None:
        super().__init__(
            "ARTIFACT_NOT_FOUND",
            "Artifact was not found.",
            status_code=404,
            task_id=task_id,
        )


class ArtifactUnavailableError(ArtifactServiceError):
    """Raised when metadata exists but controlled bytes are missing or unsafe."""

    def __init__(self, task_id: str) -> None:
        super().__init__(
            "ARTIFACT_UNAVAILABLE",
            "Artifact metadata exists, but the file is unavailable.",
            status_code=410,
            task_id=task_id,
        )


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    """Internal stream descriptor; the governed path is never serialized."""

    artifact_id: str
    path: Path
    filename: str
    media_type: str
    checksum: str
    size_bytes: int


class ArtifactService:
    """Authorize Artifact reads and resolve only repository-controlled files."""

    def __init__(
        self,
        *,
        repository: ArtifactRepositoryPort,
        tasks: TaskAccessService,
        audit_sink: WorkflowAuditSink | None = None,
        ids: IdentifierFactory | None = None,
        clock: Callable[[], datetime] | None = None,
        permission_matrix: PermissionMatrix | None = None,
    ) -> None:
        self._repository = repository
        self._tasks = tasks
        self._audit_sink = audit_sink
        self._ids = ids
        self._clock = clock
        self._permission_matrix = permission_matrix or PermissionMatrix()

    def list_task_artifacts(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> tuple[TaskArtifactView, ...]:
        """Return authorized Artifact metadata in stable order without locations."""
        self._authorize_read(task_id, caller, trace_id=trace_id)
        try:
            task = self._tasks.get_task(task_id, caller, trace_id=trace_id)
        except RuntimeError:
            self._audit_denied(task_id, caller, trace_id)
            raise
        artifacts = self._repository.list_by_task(task_id)
        published = getattr(self._tasks, "is_artifact_published", None)
        if callable(published):
            artifacts = tuple(
                artifact for artifact in artifacts if published(task_id, artifact.artifact_id)
            )
        self._audit("artifact_listed", task, caller, trace_id)
        return tuple(_artifact_view(artifact) for artifact in artifacts)

    def get_task_artifact(
        self,
        task_id: str,
        artifact_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str = "",
    ) -> ArtifactDownload:
        """Return a stream descriptor after task ownership and path containment checks."""
        self._authorize_read(task_id, caller, trace_id=trace_id)
        try:
            task = self._tasks.get_task(task_id, caller, trace_id=trace_id)
        except RuntimeError:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise
        try:
            artifact = self._repository.get_by_id(artifact_id)
        except KeyError as exc:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactNotFoundError(task_id) from exc
        if artifact.task_id != task_id:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactNotFoundError(task_id)
        published = getattr(self._tasks, "is_artifact_published", None)
        if callable(published) and not published(task_id, artifact_id):
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactNotFoundError(task_id)
        try:
            path = self._repository.path_for(artifact)
        except (OSError, ValueError) as exc:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactUnavailableError(task_id) from exc
        try:
            stat = path.stat()
        except OSError as exc:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactUnavailableError(task_id) from exc
        if not path.is_file() or stat.st_size != artifact.size_bytes:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactUnavailableError(task_id)
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactUnavailableError(task_id) from exc
        if f"sha256:{digest.hexdigest()}" != artifact.checksum:
            self._audit_denied(task_id, caller, trace_id, artifact_id=artifact_id)
            raise ArtifactUnavailableError(task_id)
        self._audit("artifact_downloaded", task, caller, trace_id, artifact_id=artifact_id)
        return ArtifactDownload(
            artifact_id=artifact.artifact_id,
            path=path,
            filename=safe_artifact_filename(Path(artifact.location).name, artifact),
            media_type=artifact.media_type,
            checksum=artifact.checksum,
            size_bytes=artifact.size_bytes,
        )

    def _authorize_read(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        *,
        trace_id: str,
    ) -> None:
        decision = self._permission_matrix.evaluate(
            AuthorizationRequest(
                action=Permission.READ_ARTIFACT,
                roles=caller.roles,
                resource_type="artifact",
                task_id=task_id,
                purpose=caller.purpose,
                is_demo_identity=caller.is_demo_identity,
            )
        )
        if not decision.allowed:
            self._audit_denied(task_id, caller, trace_id)
            raise ArtifactNotFoundError(task_id)

    def _audit_denied(
        self,
        task_id: str,
        caller: TrustedCallerContext,
        trace_id: str,
        *,
        artifact_id: str | None = None,
    ) -> None:
        if self._audit_sink is None or self._ids is None or self._clock is None:
            return
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event="artifact_read_denied",
                task_id=task_id,
                plan_id="supplier-quality-analysis",
                plan_version=0,
                timestamp=self._clock(),
                artifact_id=artifact_id,
                status="DENIED",
                metadata=JsonObject(
                    {
                        "actor_id": caller.user_id,
                        "tenant_id": caller.tenant_id,
                        "trace_id": trace_id,
                        "reason_code": "ARTIFACT_ACCESS_DENIED",
                    }
                ),
            )
        )

    def _audit(
        self,
        event: str,
        task: TaskSummaryView,
        caller: TrustedCallerContext,
        trace_id: str,
        *,
        artifact_id: str | None = None,
    ) -> None:
        if self._audit_sink is None or self._ids is None or self._clock is None:
            return
        self._audit_sink.append(
            WorkflowAuditRecord(
                event_id=self._ids.new_id("AUD"),
                event=event,
                task_id=task.task_id,
                plan_id="supplier-quality-analysis",
                plan_version=0,
                timestamp=self._clock(),
                artifact_id=artifact_id,
                metadata=JsonObject(
                    {
                        "actor_id": caller.user_id,
                        "tenant_id": caller.tenant_id,
                        "trace_id": trace_id,
                    }
                ),
            )
        )


def safe_artifact_filename(filename: str, artifact: Artifact) -> str:
    """Return one bounded Content-Disposition filename with no path components."""
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
    suffix = ".pdf" if artifact.media_type == "application/pdf" else ".json"
    if not name:
        name = f"{artifact.artifact_id}{suffix}"
    if not name.lower().endswith(suffix):
        name = f"{name[: 180 - len(suffix)]}{suffix}"
    return name[:180]


def _artifact_view(artifact: Artifact) -> TaskArtifactView:
    return TaskArtifactView(
        artifact_id=artifact.artifact_id,
        task_id=artifact.task_id,
        format="PDF" if artifact.media_type == "application/pdf" else "JSON",
        filename=safe_artifact_filename(Path(artifact.location).name, artifact),
        media_type=artifact.media_type,
        checksum=artifact.checksum,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at,
    )


__all__ = [
    "ArtifactDownload",
    "ArtifactNotFoundError",
    "ArtifactService",
    "ArtifactServiceError",
    "ArtifactUnavailableError",
    "safe_artifact_filename",
]
