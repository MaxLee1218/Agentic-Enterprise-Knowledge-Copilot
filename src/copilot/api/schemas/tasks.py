"""HTTP-only request and response schemas for natural-language task submission."""

from __future__ import annotations

import unicodedata
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from copilot.services.task_intake import TaskOutputFormat


class NaturalLanguageTaskSubmission(BaseModel):
    """Public request: natural-language task plus tightening-only execution options."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1)
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
    type: str
    location: str
    checksum: str
    size_bytes: int


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
    status: str
    created_at: datetime
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


__all__ = [
    "NaturalLanguageTaskSubmission",
    "TaskArtifactResponse",
    "TaskErrorResponse",
    "TaskFailureResponse",
    "TaskSubmissionResponse",
]
