"""Serializable LangGraph state envelope for the frozen v1.0 domain objects."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, TypedDict, TypeVar

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from copilot.contracts import (
    ApprovalRequirement,
    Artifact,
    ArtifactType,
    CapabilityName,
    ErrorType,
    ExpectedOutput,
    JsonObject,
    ReportLanguage,
    RetryPolicy,
    StepResult,
    StepResultStatus,
    StepType,
    TaskConstraints,
    TaskContract,
    TaskError,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    TaskStatus,
    TaskStep,
    TaskType,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    VerificationCheck,
    VerificationIssue,
    VerificationResult,
    VerificationSeverity,
    VerificationStatus,
)
from copilot.services.workflows.models import StepExecutionRecord, ToolAttemptSummary

T = TypeVar("T")

_CHECKPOINT_ALLOWED_TYPES = (
    ApprovalRequirement,
    Artifact,
    ArtifactType,
    CapabilityName,
    ErrorType,
    ExpectedOutput,
    JsonObject,
    ReportLanguage,
    RetryPolicy,
    StepExecutionRecord,
    StepResult,
    StepResultStatus,
    StepType,
    TaskConstraints,
    TaskContract,
    TaskError,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    TaskStatus,
    TaskStep,
    TaskType,
    ToolAttemptSummary,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    VerificationCheck,
    VerificationIssue,
    VerificationResult,
    VerificationSeverity,
    VerificationStatus,
)
_CHECKPOINT_ALLOWED_NAMES = tuple(
    (checkpoint_type.__module__, checkpoint_type.__name__)
    for checkpoint_type in _CHECKPOINT_ALLOWED_TYPES
)


def checkpoint_serializer() -> JsonPlusSerializer:
    """Return a strict serializer allowlisted to the frozen checkpoint contract types."""
    return JsonPlusSerializer(
        allowed_json_modules=_CHECKPOINT_ALLOWED_NAMES,
        allowed_msgpack_modules=_CHECKPOINT_ALLOWED_NAMES,
    )


def _merge_unique(
    left: list[T],
    right: list[T],
    *,
    key: Callable[[T], str],
) -> list[T]:
    """Append values once while retaining deterministic insertion order."""
    merged = list(left)
    positions = {key(item): index for index, item in enumerate(merged)}
    for item in right:
        item_key = key(item)
        index = positions.get(item_key)
        if index is None:
            positions[item_key] = len(merged)
            merged.append(item)
        elif merged[index] != item:
            merged[index] = item
    return merged


def merge_step_results(left: list[StepResult], right: list[StepResult]) -> list[StepResult]:
    """Merge one final result per frozen step identifier."""
    return _merge_unique(left, right, key=lambda item: item.step_id)


def merge_step_executions(
    left: list[StepExecutionRecord],
    right: list[StepExecutionRecord],
) -> list[StepExecutionRecord]:
    """Merge one operational record per frozen step identifier."""
    return _merge_unique(left, right, key=lambda item: item.step_id)


def merge_tool_calls(left: list[ToolCall], right: list[ToolCall]) -> list[ToolCall]:
    """Append immutable tool attempts once."""
    return _merge_unique(left, right, key=lambda item: item.tool_call_id)


def merge_tool_results(left: list[ToolResult], right: list[ToolResult]) -> list[ToolResult]:
    """Append immutable tool results once."""
    return _merge_unique(left, right, key=lambda item: item.tool_call_id)


def merge_identifiers(left: list[str], right: list[str]) -> list[str]:
    """Append stable business-record identifiers once."""
    return list(dict.fromkeys([*left, *right]))


def merge_artifacts(left: list[Artifact], right: list[Artifact]) -> list[Artifact]:
    """Append immutable Artifact metadata once."""
    return _merge_unique(left, right, key=lambda item: item.artifact_id)


def merge_errors(left: list[TaskError], right: list[TaskError]) -> list[TaskError]:
    """Keep errors in occurrence order while suppressing node replay duplicates."""

    def error_key(item: TaskError) -> str:
        return "|".join(
            (
                item.error_code,
                item.timestamp.isoformat(),
                item.step_id or "",
                item.tool_call_id or "",
            )
        )

    return _merge_unique(left, right, key=error_key)


def merge_counts(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    """Merge counters by taking the latest, never a smaller, value."""
    merged = dict(left)
    for key, value in right.items():
        merged[key] = max(value, merged.get(key, 0))
    return merged


class AgentGraphState(TypedDict):
    """LangGraph envelope; TaskState remains the authoritative lifecycle snapshot."""

    task_id: str
    trace_id: str
    request: TaskRequest
    contract: TaskContract
    plan: TaskPlan
    domain_state: TaskState
    started_at: datetime
    deadline_at: datetime
    current_step_id: str | None
    route: str
    route_reason: str
    last_tool_result: ToolResult | None
    last_arguments: JsonObject | None
    step_results: Annotated[list[StepResult], merge_step_results]
    step_executions: Annotated[list[StepExecutionRecord], merge_step_executions]
    tool_calls: Annotated[list[ToolCall], merge_tool_calls]
    tool_results: Annotated[list[ToolResult], merge_tool_results]
    evidence_ids: Annotated[list[str], merge_identifiers]
    artifacts: Annotated[list[Artifact], merge_artifacts]
    active_artifact: Artifact | None
    errors: Annotated[list[TaskError], merge_errors]
    plan_validation_errors: list[JsonObject]
    retry_counts: Annotated[dict[str, int], merge_counts]
    tool_retry_count: int
    plan_repair_count: int
    replan_count: int
    executed_step_count: int
    resume_count: int
    verification_result: VerificationResult | None
    task_result: TaskResult | None


def initial_graph_state(
    *,
    request: TaskRequest,
    contract: TaskContract,
    plan: TaskPlan,
    domain_state: TaskState,
    started_at: datetime,
) -> AgentGraphState:
    """Create a complete, stable checkpoint input without duplicating large source payloads."""
    return AgentGraphState(
        task_id=contract.task_id,
        trace_id=contract.task_id,
        request=request,
        contract=contract,
        plan=plan,
        domain_state=domain_state,
        started_at=started_at,
        deadline_at=contract.constraints.deadline_at,
        current_step_id=None,
        route="start",
        route_reason="New governed task",
        last_tool_result=None,
        last_arguments=None,
        step_results=[],
        step_executions=[],
        tool_calls=[],
        tool_results=[],
        evidence_ids=[],
        artifacts=[],
        active_artifact=None,
        errors=[],
        plan_validation_errors=[],
        retry_counts={},
        tool_retry_count=0,
        plan_repair_count=0,
        replan_count=0,
        executed_step_count=0,
        resume_count=0,
        verification_result=None,
        task_result=None,
    )


__all__ = [
    "AgentGraphState",
    "checkpoint_serializer",
    "initial_graph_state",
    "merge_artifacts",
    "merge_counts",
    "merge_errors",
    "merge_identifiers",
    "merge_step_executions",
    "merge_step_results",
    "merge_tool_calls",
    "merge_tool_results",
]
