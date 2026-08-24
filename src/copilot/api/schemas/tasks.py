"""HTTP-only request and response schemas for natural-language task submission."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from copilot.contracts import ArtifactType, EvidenceType, TaskStatus, TaskType
from copilot.services.task_intake import TaskOutputFormat


class PublicStepStatus(StrEnum):
    """Stable public status for planned steps with or without a StepResult."""

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    BUSINESS_FAILURE = "BUSINESS_FAILURE"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CANCELLED = "CANCELLED"


class NaturalLanguageTaskSubmission(BaseModel):
    """Public request: natural-language task plus tightening-only execution options."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1)
    task_type: TaskType | None = None
    output_format: TaskOutputFormat | None = None
    max_steps: int | None = Field(default=None, ge=1)
    read_only: bool | None = None
    require_approval: bool | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def validate_task(cls, value: str) -> str:
        """Reject blank and control-bearing text before the LLM boundary."""
        if not value.strip():
            raise ValueError("Task text must not be empty.")
        for character in value:
            if character in {"\n", "\r", "\t"}:
                continue
            if character == "\x00" or unicodedata.category(character) in {"Cc", "Cs"}:
                raise ValueError("Task text contains a disallowed control character.")
        return value


class TaskArtifactResponse(BaseModel):
    """Safe Artifact reference returned by task submission."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    type: ArtifactType
    filename: str
    media_type: str
    checksum: str
    size_bytes: int
    created_at: datetime


class TaskFailureResponse(BaseModel):
    """One safe typed workflow error."""

    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str
    recoverable: bool


class TaskSubmissionResponse(BaseModel):
    """Stable synchronous task-creation response."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    status: TaskStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str
    artifacts: tuple[TaskArtifactResponse, ...] = ()
    errors: tuple[TaskFailureResponse, ...] = ()
    missing_information: tuple[str, ...] = ()
    clarification_questions: tuple[str, ...] = ()
    pending_approval_id: str | None = None


class TaskErrorResponse(BaseModel):
    """Uniform transport error returned before a workflow result exists."""

    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str
    task_id: str | None = None
    trace_id: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    """Stable public task-management summary."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    status: TaskStatus
    task_type: TaskType | None
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


class TaskListResponse(BaseModel):
    """Bounded current-user task history ordered newest-first."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[TaskResponse, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class TaskStepResponse(BaseModel):
    """Public planned-step and persisted-result view."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool_name: str
    purpose: str
    status: PublicStepStatus
    depends_on: tuple[str, ...]
    attempt_count: int
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    latency_ms: int | None
    evidence_ids: tuple[str, ...]
    error_code: str | None
    error_message: str | None


class TaskStepsResponse(BaseModel):
    """Deterministically ordered step collection."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    steps: tuple[TaskStepResponse, ...]


class TaskEvidenceResponse(BaseModel):
    """Minimized Evidence metadata with retained lineage."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    type: EvidenceType
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


class TaskEvidenceListResponse(BaseModel):
    """Deterministically ordered task Evidence collection."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    evidence: tuple[TaskEvidenceResponse, ...]


__all__ = [
    "NaturalLanguageTaskSubmission",
    "PublicStepStatus",
    "TaskArtifactResponse",
    "TaskEvidenceListResponse",
    "TaskEvidenceResponse",
    "TaskErrorResponse",
    "TaskFailureResponse",
    "TaskListResponse",
    "TaskResponse",
    "TaskStepResponse",
    "TaskStepsResponse",
    "TaskSubmissionResponse",
]
