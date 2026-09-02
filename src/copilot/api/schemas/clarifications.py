"""HTTP DTOs for interactive clarification reads and responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from copilot.contracts import (
    ClarificationInputType,
    ClarificationStatus,
    RuntimeStatus,
    TaskStatus,
)


class ClarificationQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str
    prompt: str
    input_type: ClarificationInputType
    required: bool
    allowed_values: tuple[str, ...]
    constraints: dict[str, JsonValue]


class ClarificationDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str
    task_id: str
    status: ClarificationStatus
    round: int = Field(ge=1)
    questions: tuple[ClarificationQuestionResponse, ...]
    created_at: datetime
    submitted_at: datetime | None
    resolved_at: datetime | None


class ClarificationSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, JsonValue] = Field(default_factory=dict)
    message: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_content(self) -> ClarificationSubmissionRequest:
        if not self.answers and self.message is None:
            raise ValueError("clarification response requires answers or message")
        return self


class ClarificationSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str
    clarification_status: ClarificationStatus
    task_id: str
    task_status: TaskStatus
    runtime_status: RuntimeStatus
    status_url: str
    accepted_at: datetime
    trace_id: str
    reused: bool = False


__all__ = [
    "ClarificationDetailResponse",
    "ClarificationQuestionResponse",
    "ClarificationSubmissionRequest",
    "ClarificationSubmissionResponse",
]
