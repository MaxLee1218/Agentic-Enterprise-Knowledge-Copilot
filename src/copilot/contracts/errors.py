"""Safe typed errors for contract, node, and tool boundaries."""

from datetime import datetime

from pydantic import Field, field_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import ErrorType
from copilot.contracts.validators import utc_now, validate_identifier, validate_utc_datetime


class TaskError(ImmutableContractModel):
    """Serializable error record safe to persist and expose at domain boundaries."""

    error_code: str = Field(description="Stable machine-readable error code", min_length=1)
    error_type: ErrorType = Field(description="Normalized error category")
    message: str = Field(description="Safe actionable error explanation", min_length=1)
    recoverable: bool = Field(description="Whether recovery is eligible within current budgets")
    timestamp: datetime = Field(default_factory=utc_now, description="UTC error occurrence time")
    task_id: str | None = Field(default=None, description="Associated task identifier")
    step_id: str | None = Field(default=None, description="Associated step identifier")
    tool_call_id: str | None = Field(
        default=None, description="Associated tool invocation identifier"
    )
    cause_error_id: str | None = Field(default=None, description="Identifier of a causal error")
    details: JsonObject = Field(
        default_factory=lambda: JsonObject({}),
        description="Non-sensitive structured diagnostic details",
    )

    _validate_timestamp = field_validator("timestamp")(validate_utc_datetime)
    _validate_ids = field_validator("task_id", "step_id", "tool_call_id", "cause_error_id")(
        lambda value: validate_identifier(value) if value is not None else value
    )


class DomainError(Exception):
    """Runtime exception carrying a serializable TaskError record."""

    def __init__(self, error: TaskError) -> None:
        super().__init__(error.message)
        self.error = error
