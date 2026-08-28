"""Serializable LangGraph state envelope for the frozen v1.1 domain objects."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, TypedDict, TypeVar, cast

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    APExceptionType,
    ApprovalRequirement,
    Artifact,
    ArtifactType,
    CapabilityName,
    ContractSchemaVersion,
    DateRange,
    ErrorType,
    ExpectedOutput,
    JsonObject,
    MoneyThreshold,
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
from copilot.contracts.serialization import (
    upcast_legacy_task_step_payload,
    upcast_task_contract_payload,
    upgrade_checkpoint_value,
)
from copilot.services.task_intake import RequestSource, TrustedTaskContext
from copilot.services.workflows.models import StepExecutionRecord, ToolAttemptSummary

T = TypeVar("T")

_CHECKPOINT_ALLOWED_TYPES = (
    APExceptionType,
    AccountsPayableConstraintsV1,
    ApprovalRequirement,
    Artifact,
    ArtifactType,
    CapabilityName,
    ContractSchemaVersion,
    DateRange,
    ErrorType,
    ExpectedOutput,
    JsonObject,
    MoneyThreshold,
    ReportLanguage,
    RequestSource,
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
    TrustedTaskContext,
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


class VersionedCheckpointSerializer(JsonPlusSerializer):
    """Strict LangGraph serializer with exact legacy contract/step upcasting."""

    def _revive_lc2(self, value: dict[str, object]) -> object:
        identifier = value.get("id")
        kwargs = value.get("kwargs")
        if isinstance(identifier, list) and isinstance(kwargs, dict):
            target = ".".join(str(item) for item in identifier)
            if target == "copilot.contracts.tasks.TaskContract":
                return TaskContract.model_validate(upcast_task_contract_payload(kwargs))
            if target == "copilot.contracts.plans.TaskStep":
                if {"tool_version", "contract_profile"}.issubset(kwargs):
                    return TaskStep.model_validate(kwargs)
                return TaskStep.model_validate(upcast_legacy_task_step_payload(kwargs))
        return super()._revive_lc2(value)

    def loads_typed(self, data: tuple[str, bytes]) -> object:
        """Upgrade legacy values after both JSON and msgpack safe decoding."""
        return upgrade_checkpoint_value(super().loads_typed(data))


def checkpoint_serializer() -> JsonPlusSerializer:
    """Return a strict serializer allowlisted to the frozen checkpoint contract types."""
    return VersionedCheckpointSerializer(
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
    execution_generation: int
    intake_context: TrustedTaskContext
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
    approval_id: str | None
    approval_step_id: str | None
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
    domain_state: TaskState,
    started_at: datetime,
    intake_context: TrustedTaskContext | None = None,
    contract: TaskContract | None = None,
    plan: TaskPlan | None = None,
    execution_generation: int = 1,
) -> AgentGraphState:
    """Create a complete, stable checkpoint input without duplicating large source payloads."""
    if intake_context is None:
        if contract is None:
            raise ValueError("intake_context is required when no prepared contract is supplied")
        ap_scope = (
            contract.constraints
            if isinstance(contract.constraints, AccountsPayableConstraintsV1)
            else None
        )
        intake_context = TrustedTaskContext(
            task_id=contract.task_id,
            trace_id=contract.task_id,
            session_id=contract.task_id,
            user_id=request.user_id,
            tenant_id=contract.constraints.tenant_id,
            data_scope=contract.constraints.data_scope,
            authorized_supplier_ids=contract.constraints.supplier_ids,
            authorized_legal_entity_ids=(ap_scope.legal_entity_ids if ap_scope else ()),
            authorized_business_unit_ids=(ap_scope.business_unit_ids if ap_scope else ()),
            authorized_currency_scope=(ap_scope.currency_scope if ap_scope else ()),
            policy_rule_set_id=(ap_scope.policy_rule_set_id if ap_scope else None),
            policy_rule_set_version=(ap_scope.policy_rule_set_version if ap_scope else None),
            policy_manifest_checksum=(ap_scope.policy_manifest_checksum if ap_scope else None),
            policy_materiality=(ap_scope.effective_materiality if ap_scope else ()),
            policy_snapshot_at=(ap_scope.snapshot_at if ap_scope else None),
            roles=(
                ("quality_analyst",)
                if contract.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
                else ("finance_analyst",)
            ),
            scopes=(
                ("task:execute", "data:quality.v1")
                if contract.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
                else (
                    "task:execute",
                    "finance:ap.detail",
                    "finance:ap.artifact:download",
                    "artifact.write",
                )
            ),
            authentication_source="legacy_internal_adapter",
            authenticated=True,
            is_demo_identity=True,
            purpose=contract.task_type.value,
            task_type=contract.task_type,
            output_format=contract.expected_output.artifact_type,
            max_steps=max(1, len(plan.steps)) if plan is not None else 1,
            read_only=ap_scope.read_only if ap_scope else True,
            require_approval=contract.approval_requirement.required,
            deadline_at=contract.constraints.deadline_at,
            request_source=RequestSource.INTERNAL,
            task_text_hash=hashlib.sha256(request.raw_input.encode("utf-8")).hexdigest(),
            task_text_length=len(request.raw_input),
        )
    if contract is not None and (
        intake_context.task_type is not contract.task_type
        or intake_context.purpose != contract.task_type.value
    ):
        raise ValueError("trusted task type and purpose must match the TaskContract")
    return AgentGraphState(
        task_id=intake_context.task_id,
        trace_id=intake_context.trace_id,
        execution_generation=execution_generation,
        intake_context=intake_context,
        request=request,
        contract=cast(TaskContract, contract),
        plan=cast(TaskPlan, plan),
        domain_state=domain_state,
        started_at=started_at,
        deadline_at=intake_context.deadline_at,
        current_step_id=None,
        route="start",
        route_reason="New governed task",
        last_tool_result=None,
        last_arguments=None,
        approval_id=None,
        approval_step_id=None,
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
