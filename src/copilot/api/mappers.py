"""Central mappings from application views to stable HTTP response schemas."""

from copilot.api.schemas.artifacts import ArtifactMetadataResponse
from copilot.api.schemas.tasks import (
    PublicStepStatus,
    TaskEvidenceResponse,
    TaskResponse,
    TaskStepResponse,
)
from copilot.contracts import EvidenceType, TaskStatus, TaskType
from copilot.services.task_views import (
    TaskArtifactView,
    TaskEvidenceView,
    TaskStepView,
    TaskSummaryView,
)


def task_response(view: TaskSummaryView) -> TaskResponse:
    """Map one application task summary to its public HTTP DTO."""
    return TaskResponse(
        task_id=view.task_id,
        trace_id=view.trace_id,
        status=TaskStatus(view.status),
        task_type=TaskType(view.task_type) if view.task_type is not None else None,
        created_at=view.created_at,
        started_at=view.started_at,
        completed_at=view.completed_at,
        cancelled_at=view.cancelled_at,
        current_step=view.current_step,
        task_summary=view.task_summary,
        pending_approval_id=view.pending_approval_id,
        step_count=view.step_count,
        evidence_count=view.evidence_count,
        artifact_count=view.artifact_count,
        error_summary=view.error_summary,
    )


def task_step_response(view: TaskStepView) -> TaskStepResponse:
    """Map one safe step view to its HTTP DTO."""
    return TaskStepResponse(
        step_id=view.step_id,
        tool_name=view.tool_name,
        purpose=view.purpose,
        status=PublicStepStatus(view.status),
        depends_on=view.depends_on,
        attempt_count=view.attempt_count,
        retry_count=view.retry_count,
        started_at=view.started_at,
        completed_at=view.completed_at,
        latency_ms=view.latency_ms,
        evidence_ids=view.evidence_ids,
        error_code=view.error_code,
        error_message=view.error_message,
    )


def task_evidence_response(view: TaskEvidenceView) -> TaskEvidenceResponse:
    """Map one minimized Evidence view to its HTTP DTO."""
    return TaskEvidenceResponse(
        evidence_id=view.evidence_id,
        type=EvidenceType(view.type),
        source=view.source,
        produced_by=view.produced_by,
        step_id=view.step_id,
        lineage=view.lineage,
        confidence=view.confidence,
        created_at=view.created_at,
        query_id=view.query_id,
        document_source=view.document_source,
        formula=view.formula,
        input_evidence_ids=view.input_evidence_ids,
        content_summary=view.content_summary,
    )


def artifact_metadata_response(view: TaskArtifactView) -> ArtifactMetadataResponse:
    """Map safe Artifact metadata without a storage path."""
    return ArtifactMetadataResponse(
        artifact_id=view.artifact_id,
        task_id=view.task_id,
        format="PDF" if view.format == "PDF" else "JSON",
        filename=view.filename,
        media_type=view.media_type,
        checksum=view.checksum,
        size_bytes=view.size_bytes,
        created_at=view.created_at,
    )


__all__ = [
    "artifact_metadata_response",
    "task_evidence_response",
    "task_response",
    "task_step_response",
]
