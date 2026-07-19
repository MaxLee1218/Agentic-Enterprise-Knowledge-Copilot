"""Versioned task plan, execution step, and step-result contracts."""

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import StepResultStatus, StepType
from copilot.contracts.errors import TaskError
from copilot.contracts.validators import utc_now, validate_identifier, validate_utc_datetime


class RetryPolicy(ImmutableContractModel):
    """Bounded deterministic retry policy attached to one plan step."""

    max_attempts: int = Field(description="Total attempts including the first call", ge=1, le=3)
    backoff_seconds: tuple[int, ...] = Field(
        default_factory=tuple, description="Deterministic delay before each retry"
    )
    retryable_error_codes: tuple[str, ...] = Field(
        default_factory=tuple, description="Stable error codes eligible for retry"
    )

    @model_validator(mode="after")
    def validate_backoff_count(self) -> "RetryPolicy":
        """Ensure retry delays cannot exceed the bounded number of retries."""
        if len(self.backoff_seconds) > self.max_attempts - 1:
            raise ValueError("backoff_seconds has more entries than available retries")
        if any(delay < 0 for delay in self.backoff_seconds):
            raise ValueError("backoff_seconds must not contain negative values")
        return self


class TaskStep(ImmutableContractModel):
    """One schema-bound and independently traceable node in a task plan."""

    step_id: str = Field(description="Identifier unique across task plan versions")
    task_id: str = Field(description="Task to which the step belongs")
    step_type: StepType = Field(description="Approved executable step category")
    tool_name: str = Field(description="Registered tool selected for this step", min_length=1)
    input_schema: JsonObject = Field(description="Strict JSON Schema for the step input")
    output_schema: JsonObject = Field(description="Strict JSON Schema for the step output")
    dependency: tuple[str, ...] = Field(
        default_factory=tuple, description="Predecessor step identifiers"
    )
    retry_policy: RetryPolicy = Field(description="Bounded retry policy for this step")

    _validate_ids = field_validator("step_id", "task_id", "tool_name")(validate_identifier)

    @model_validator(mode="after")
    def reject_self_dependency(self) -> "TaskStep":
        """Reject direct self-dependencies and duplicate dependency edges."""
        if self.step_id in self.dependency:
            raise ValueError("a step cannot depend on itself")
        if len(set(self.dependency)) != len(self.dependency):
            raise ValueError("dependency entries must be unique")
        return self


class TaskPlan(ImmutableContractModel):
    """Versioned directed acyclic execution plan for one task contract."""

    task_id: str = Field(description="Task governed by this plan", min_length=1)
    steps: tuple[TaskStep, ...] = Field(description="Non-empty ordered plan steps", min_length=1)
    planning_version: int = Field(description="Monotonically increasing plan version", ge=1)
    created_at: datetime = Field(default_factory=utc_now, description="UTC plan creation time")

    _validate_task_id = field_validator("task_id")(validate_identifier)
    _validate_created_at = field_validator("created_at")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_dag(self) -> "TaskPlan":
        """Require unique steps, local dependencies, task ownership, and an acyclic graph."""
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id values must be unique within a plan")
        known = set(step_ids)
        for step in self.steps:
            if step.task_id != self.task_id:
                raise ValueError("every step must belong to the plan task")
            missing = set(step.dependency) - known
            if missing:
                raise ValueError(f"unknown step dependencies: {sorted(missing)}")
        graph = {step.step_id: step.dependency for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("task plan dependencies must form an acyclic graph")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency_id in graph[step_id]:
                visit(dependency_id)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)
        return self


class StepResult(ImmutableContractModel):
    """Normalized final outcome of a step while attempt history remains append-only."""

    step_id: str = Field(description="Step represented by this result", min_length=1)
    status: StepResultStatus = Field(description="Normalized final step outcome")
    output: JsonObject | None = Field(description="Schema-validated normalized step output")
    evidence: tuple[str, ...] = Field(
        default_factory=tuple, description="Evidence identifiers produced by the step"
    )
    error: TaskError | None = Field(description="Typed error for a non-success outcome")

    _validate_step_id = field_validator("step_id")(validate_identifier)

    @model_validator(mode="after")
    def validate_error_consistency(self) -> "StepResult":
        """Keep success and failure error semantics mutually consistent."""
        if self.status is StepResultStatus.SUCCESS and self.error is not None:
            raise ValueError("successful step result must not contain an error")
        if self.status is not StepResultStatus.SUCCESS and self.error is None:
            raise ValueError("non-success step result must contain an error")
        return self
