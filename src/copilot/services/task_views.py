"""Stable application views shared by HTTP and CLI task-management adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from copilot.contracts import ClarificationQuestion


@dataclass(frozen=True, slots=True)
class TaskListItemView:
    """Lightweight sidebar row that never expands task-scoped detail collections."""

    task_id: str
    task_summary: str
    status: str
    runtime_status: str
    task_type: str | None
    created_at: datetime


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

    items: tuple[TaskListItemView, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class InitialUserMessageView:
    """Authorized display form of the immutable initial Task request."""

    display_text: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClarificationRoundView:
    """One durable clarification question/response round."""

    clarification_id: str
    round: int
    status: str
    questions: tuple[ClarificationQuestion, ...]
    response_display_text: str | None
    created_at: datetime
    submitted_at: datetime | None
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskPhaseEventView:
    """Durable user-useful lifecycle phase transition."""

    phase: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalSummaryView:
    """Minimized approval lifecycle item without proposed tool arguments."""

    approval_id: str
    status: str
    safe_label: str
    resolution_action: str | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskResultSummaryView:
    """Evidence-safe terminal result presentation."""

    final_status: str
    safe_summary: str


@dataclass(frozen=True, slots=True)
class TaskInteractionProjectionView:
    """Versioned read projection reconstructed exclusively from authoritative records."""

    schema_version: str
    initial_user_message: InitialUserMessageView
    clarification_rounds: tuple[ClarificationRoundView, ...]
    phase_events: tuple[TaskPhaseEventView, ...]
    approval_summaries: tuple[ApprovalSummaryView, ...]
    result: TaskResultSummaryView | None


@dataclass(frozen=True, slots=True)
class TaskDetailView(TaskSummaryView):
    """Backward-compatible summary enriched with a refresh-safe interaction projection."""

    interaction_projection: TaskInteractionProjectionView | None = None

    @classmethod
    def from_summary(
        cls,
        summary: TaskSummaryView,
        interaction_projection: TaskInteractionProjectionView,
    ) -> TaskDetailView:
        """Promote the existing application summary without changing its public attributes."""
        return cls(
            task_id=summary.task_id,
            trace_id=summary.trace_id,
            status=summary.status,
            task_type=summary.task_type,
            created_at=summary.created_at,
            started_at=summary.started_at,
            completed_at=summary.completed_at,
            cancelled_at=summary.cancelled_at,
            current_step=summary.current_step,
            task_summary=summary.task_summary,
            pending_approval_id=summary.pending_approval_id,
            step_count=summary.step_count,
            evidence_count=summary.evidence_count,
            artifact_count=summary.artifact_count,
            error_summary=summary.error_summary,
            runtime_status=summary.runtime_status,
            pending_clarification=summary.pending_clarification,
            interaction_projection=interaction_projection,
        )


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
    "ApprovalSummaryView",
    "ClarificationRoundView",
    "InitialUserMessageView",
    "TaskArtifactView",
    "TaskClarificationView",
    "TaskDetailView",
    "TaskEvidenceView",
    "TaskInteractionProjectionView",
    "TaskListItemView",
    "TaskListView",
    "TaskPhaseEventView",
    "TaskResultSummaryView",
    "TaskStepView",
    "TaskSummaryView",
]
