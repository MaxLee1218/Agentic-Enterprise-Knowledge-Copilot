"""Stable application views shared by HTTP and CLI task-management adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from copilot.contracts import ClarificationQuestion


@dataclass(frozen=True, slots=True)
class TaskClarificationView:
    """Safe pending interaction summary embedded in Task details."""

    clarification_id: str
    round: int
    questions: tuple[ClarificationQuestion, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaskSummaryView:
    """Safe task lifecycle summary without Graph or persistence internals."""

    task_id: str
    trace_id: str
    status: str
    task_type: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    current_step: str | None
    task_summary: str
    pending_approval_id: str | None
    step_count: int
    evidence_count: int
    artifact_count: int
    error_summary: str | None
    runtime_status: str = "READY"
    pending_clarification: TaskClarificationView | None = None


@dataclass(frozen=True, slots=True)
class TaskListView:
    """Bounded task history for one authenticated owner and tenant."""

    items: tuple[TaskSummaryView, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class TaskStepView:
    """Safe combined view of a planned step and its persisted result."""

    step_id: str
    tool_name: str
    purpose: str
    status: str
    depends_on: tuple[str, ...]
    attempt_count: int
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    latency_ms: int | None
    evidence_ids: tuple[str, ...]
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class TaskEvidenceView:
    """Minimized evidence metadata and lineage safe for an external caller."""

    evidence_id: str
    type: str
    source: str
    produced_by: str
    step_id: str
    lineage: tuple[str, ...]
    confidence: float | None
    created_at: datetime
    query_id: str | None
    document_source: str | None
    formula: str | None
    input_evidence_ids: tuple[str, ...]
    content_summary: str


@dataclass(frozen=True, slots=True)
class TaskArtifactView:
    """Artifact metadata that intentionally omits the governed storage location."""

    artifact_id: str
    task_id: str
    format: str
    filename: str
    media_type: str
    checksum: str
    size_bytes: int
    created_at: datetime


__all__ = [
    "TaskArtifactView",
    "TaskClarificationView",
    "TaskEvidenceView",
    "TaskListView",
    "TaskStepView",
    "TaskSummaryView",
]
