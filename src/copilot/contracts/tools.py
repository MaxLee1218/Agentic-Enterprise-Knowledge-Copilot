"""Governed tool definition, invocation, and result contracts."""

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import RiskLevel, ToolResultStatus
from copilot.contracts.errors import TaskError
from copilot.contracts.validators import validate_identifier, validate_utc_datetime


class ToolTimeout(ImmutableContractModel):
    """Per-attempt and total deadline limits for a registered tool."""

    attempt_seconds: int = Field(description="Maximum duration of one invocation", ge=1)
    overall_seconds: int = Field(description="Maximum duration across all attempts", ge=1)

    @model_validator(mode="after")
    def validate_timeout_order(self) -> "ToolTimeout":
        """Require the overall timeout to cover at least one attempt."""
        if self.overall_seconds < self.attempt_seconds:
            raise ValueError("overall_seconds must be at least attempt_seconds")
        return self


class ToolApprovalPolicy(ImmutableContractModel):
    """Policy binding that determines when and by whom a tool is approved."""

    policy_id: str = Field(description="Stable approval policy identifier", min_length=1)
    trigger_conditions: tuple[str, ...] = Field(
        default_factory=tuple, description="Conditions requiring human approval"
    )
    approver_role: str | None = Field(default=None, description="Role authorized to approve")


class ToolIdempotency(ImmutableContractModel):
    """Idempotency and side-effect declaration for a registered capability."""

    idempotent: bool = Field(description="Whether identical calls may reuse a result")
    key_components: tuple[str, ...] = Field(
        description="Canonical fields composing the idempotency key", min_length=1
    )
    reuse_window_seconds: int = Field(description="Maximum result reuse window", ge=0)
    side_effects: str = Field(description="Explicit side-effect declaration", min_length=1)


class ToolDefinition(ImmutableContractModel):
    """Versioned governed definition of a capability available to the planner."""

    tool_name: str = Field(description="Stable registered tool name", min_length=1)
    tool_version: str = Field(description="Registered tool implementation version", min_length=1)
    description: str = Field(description="Allowed purpose and prohibitions", min_length=1)
    input_schema: JsonObject = Field(description="Strict JSON input Schema")
    output_schema: JsonObject = Field(description="Normalized JSON output Schema")
    risk_level: RiskLevel = Field(description="Governed risk classification")
    timeout: ToolTimeout = Field(description="Invocation and overall timeout limits")
    approval_policy: ToolApprovalPolicy = Field(description="Pre-execution approval policy")
    idempotency: ToolIdempotency = Field(description="Retry and result-reuse contract")


class ToolCall(ImmutableContractModel):
    """Policy-checked invocation envelope for one tool attempt."""

    tool_call_id: str = Field(description="Unique invocation attempt identifier")
    task_id: str = Field(description="Task authorizing the invocation")
    step_id: str = Field(description="Validated plan step being executed")
    tool_name: str = Field(description="Registered tool name")
    tool_version: str = Field(description="Registered tool version")
    input: JsonObject = Field(description="Schema-validated invocation input")
    idempotency_key: str = Field(description="Canonical retry-safe idempotency key")
    approval_id: str | None = Field(default=None, description="Applicable approval identifier")
    deadline_at: datetime = Field(description="UTC deadline not exceeding the task deadline")
    tenant_id: str = Field(description="Trusted tenant identity")
    user_id: str = Field(description="Trusted user identity")

    _validate_ids = field_validator(
        "tool_call_id",
        "task_id",
        "step_id",
        "tool_name",
        "tool_version",
        "idempotency_key",
        "tenant_id",
        "user_id",
    )(validate_identifier)
    _validate_deadline = field_validator("deadline_at")(validate_utc_datetime)


class ToolResult(ImmutableContractModel):
    """Immutable normalized result of one tool invocation attempt."""

    tool_call_id: str = Field(description="Invocation attempt represented by this result")
    task_id: str = Field(description="Associated task identifier")
    step_id: str = Field(description="Associated plan step identifier")
    tool_name: str = Field(description="Tool that produced the result")
    tool_version: str = Field(description="Version of the producing tool")
    status: ToolResultStatus = Field(description="Normalized invocation outcome")
    output: JsonObject | None = Field(description="Schema-validated success or business payload")
    error: TaskError | None = Field(description="Typed error for a non-success outcome")
    started_at: datetime = Field(description="UTC invocation start time")
    completed_at: datetime = Field(description="UTC invocation completion time")
    attempt: int = Field(description="One-based invocation attempt number", ge=1, le=3)
    evidence_ids: tuple[str, ...] = Field(
        default_factory=tuple, description="Evidence identifiers registered from this result"
    )
    latency_ms: int | None = Field(
        default=None,
        description="Elapsed invocation time in whole milliseconds",
        ge=0,
    )

    _validate_ids = field_validator(
        "tool_call_id", "task_id", "step_id", "tool_name", "tool_version"
    )(validate_identifier)
    _validate_timestamps = field_validator("started_at", "completed_at")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_result(self) -> "ToolResult":
        """Validate timing and consistency of status, output, and error."""
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        if self.status is ToolResultStatus.SUCCESS and self.error is not None:
            raise ValueError("successful tool result must not contain an error")
        if self.status is not ToolResultStatus.SUCCESS and self.error is None:
            raise ValueError("non-success tool result must contain an error")
        calculated_latency = round((self.completed_at - self.started_at).total_seconds() * 1000)
        if self.latency_ms is not None and self.latency_ms != calculated_latency:
            raise ValueError("latency_ms must match started_at and completed_at")
        object.__setattr__(self, "latency_ms", calculated_latency)
        return self
