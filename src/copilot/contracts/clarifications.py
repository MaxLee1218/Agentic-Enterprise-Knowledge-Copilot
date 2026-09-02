"""Durable contracts for interactive task clarification and checkpoint resume."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.validators import validate_identifier, validate_utc_datetime


class ClarificationStatus(StrEnum):
    """One-round clarification lifecycle; only ``PENDING`` accepts a response."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ClarificationInputType(StrEnum):
    """Frontend-renderable answer controls supported by the v1 contract."""

    TEXT = "text"
    DATE = "date"
    DATE_RANGE = "date_range"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"


class ClarificationQuestion(ImmutableContractModel):
    """One deterministic, field-bound request for missing business information."""

    field: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    reason: str = Field(min_length=1, max_length=1000)
    prompt: str = Field(min_length=1, max_length=2000)
    input_type: ClarificationInputType
    required: bool = True
    allowed_values: tuple[str, ...] = ()
    constraints: JsonObject = Field(default_factory=lambda: JsonObject({}))

    @model_validator(mode="after")
    def validate_input_contract(self) -> ClarificationQuestion:
        if len(set(self.allowed_values)) != len(self.allowed_values):
            raise ValueError("allowed_values must be unique")
        selectable = self.input_type in {
            ClarificationInputType.SINGLE_SELECT,
            ClarificationInputType.MULTI_SELECT,
        }
        if selectable != bool(self.allowed_values):
            raise ValueError("select questions require non-empty allowed_values only")
        return self


class ClarificationAnswer(ImmutableContractModel):
    """One optional structured answer retained independently from free-form text."""

    field: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    value: JsonValue


class ClarificationContext(ImmutableContractModel):
    """Accumulated, validated facts supplied beside—not inside—the original request."""

    schema_version: Literal["clarification-context.v1"] = "clarification-context.v1"
    values: JsonObject = Field(default_factory=lambda: JsonObject({}))


class ClarificationResponse(ImmutableContractModel):
    """Bounded human response accepted by the public API."""

    answers: JsonObject = Field(default_factory=lambda: JsonObject({}))
    message: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_content(self) -> ClarificationResponse:
        if not self.answers.root and self.message is None:
            raise ValueError("clarification response requires answers or message")
        return self


class TaskClarification(ImmutableContractModel):
    """Versioned durable interaction record for one clarification round."""

    schema_version: Literal["task-clarification.v1"] = "task-clarification.v1"
    clarification_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    round: int = Field(ge=1)
    status: ClarificationStatus
    questions: tuple[ClarificationQuestion, ...] = Field(min_length=1)
    context: ClarificationContext = Field(default_factory=ClarificationContext)
    response: ClarificationResponse | None = None
    response_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    resume_context: JsonObject | None = None
    submitted_by: str | None = Field(default=None, min_length=1)
    created_at: datetime
    submitted_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_code: str | None = Field(default=None, min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)

    _validate_ids = field_validator("clarification_id", "task_id", "tenant_id")(validate_identifier)
    _validate_optional_id = field_validator("submitted_by")(
        lambda value: validate_identifier(value) if value is not None else value
    )
    _validate_created = field_validator("created_at")(validate_utc_datetime)
    _validate_optional_times = field_validator("submitted_at", "resolved_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_lifecycle(self) -> TaskClarification:
        fields = tuple(question.field for question in self.questions)
        if len(set(fields)) != len(fields):
            raise ValueError("clarification question fields must be unique")
        submission_values = (
            self.response,
            self.response_fingerprint,
            self.resume_context,
            self.submitted_by,
            self.submitted_at,
        )
        submitted = self.status in {
            ClarificationStatus.SUBMITTED,
            ClarificationStatus.RESOLVED,
            ClarificationStatus.REJECTED,
        }
        if submitted and not all(value is not None for value in submission_values):
            raise ValueError("submitted clarification fields must be recorded together")
        if self.status is ClarificationStatus.PENDING and any(
            value is not None for value in submission_values
        ):
            raise ValueError("pending clarification cannot contain response fields")
        if (
            self.status is ClarificationStatus.CANCELLED
            and any(value is not None for value in submission_values)
            and not all(value is not None for value in submission_values)
        ):
            raise ValueError("cancelled response fields must be complete when present")
        finalized = self.status in {
            ClarificationStatus.RESOLVED,
            ClarificationStatus.REJECTED,
            ClarificationStatus.CANCELLED,
        }
        if finalized != (self.resolved_at is not None):
            raise ValueError("finalized clarification requires resolved_at")
        if self.submitted_at is not None and self.submitted_at < self.created_at:
            raise ValueError("submitted_at must not precede created_at")
        if self.resolved_at is not None and self.resolved_at < self.created_at:
            raise ValueError("resolved_at must not precede created_at")
        return self


__all__ = [
    "ClarificationAnswer",
    "ClarificationContext",
    "ClarificationInputType",
    "ClarificationQuestion",
    "ClarificationResponse",
    "ClarificationStatus",
    "TaskClarification",
]
