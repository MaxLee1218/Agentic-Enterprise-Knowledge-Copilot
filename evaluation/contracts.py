"""Stable, versioned contracts for deterministic offline Agent evaluation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from copilot.contracts import (
    ApprovalRequest,
    Artifact,
    EvidenceItem,
    EvidenceType,
    StepResult,
    TaskContract,
    TaskError,
    TaskPlan,
    TaskStatus,
    ToolCall,
    ToolResult,
    VerificationResult,
)


class EvaluationModel(BaseModel):
    """Strict base for versioned evaluation data."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    INFORMATIONAL = "informational"


class MetricStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_AVAILABLE = "not_available"
    ERROR = "error"


class EvaluationCaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
    SKIPPED = "skipped"


class FailureCategory(StrEnum):
    DATASET_INVALID = "DATASET_INVALID"
    HARNESS_SETUP = "HARNESS_SETUP"
    TASK_INTAKE = "TASK_INTAKE"
    TASK_UNDERSTANDING = "TASK_UNDERSTANDING"
    CLARIFICATION = "CLARIFICATION"
    TASK_CLASSIFICATION = "TASK_CLASSIFICATION"
    PLAN_GENERATION = "PLAN_GENERATION"
    PLAN_INVALID = "PLAN_INVALID"
    PLAN_REPAIR = "PLAN_REPAIR"
    TOOL_SELECTION = "TOOL_SELECTION"
    TOOL_INPUT = "TOOL_INPUT"
    TOOL_EXECUTION = "TOOL_EXECUTION"
    DEPENDENCY = "DEPENDENCY"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    REPLAN_FAILED = "REPLAN_FAILED"
    APPROVAL = "APPROVAL"
    EVIDENCE = "EVIDENCE"
    CITATION = "CITATION"
    NUMERIC = "NUMERIC"
    SAFETY = "SAFETY"
    REPORT = "REPORT"
    PERSISTENCE = "PERSISTENCE"
    TIMEOUT = "TIMEOUT"
    EVALUATOR_INTERNAL = "EVALUATOR_INTERNAL"
    UNEXPECTED_INTERNAL = "UNEXPECTED_INTERNAL"


class TaskInputSpec(EvaluationModel):
    raw_input: str = Field(min_length=1)
    output_format: Literal["pdf", "json"] | None = None
    read_only: bool = True
    require_approval: bool = False
    max_steps: int | None = Field(default=None, ge=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ActorContext(EvaluationModel):
    user_id: str = Field(min_length=1)
    role: str | None = None
    tenant_id: str = Field(min_length=1)
    data_scope: tuple[str, ...] = ("supplier_quality",)
    supplier_ids: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_tables: tuple[str, ...] = ()
    allowed_fields: tuple[str, ...] = ()
    approval_permissions: tuple[str, ...] = ()


class ExecutionConfigSpec(EvaluationModel):
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    approval_action: Literal["pause", "approve", "edit", "reject"] | None = None
    approval_edit: dict[str, JsonValue] | None = None


class FaultInjectionSpec(EvaluationModel):
    target: str
    failure_type: str
    fail_on_attempts: tuple[int, ...] = ()
    error_code: str | None = None
    transient: bool = False
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    latency_ms: int = Field(default=0, ge=0)


class ExpectedOutcome(EvaluationModel):
    allowed_terminal_statuses: tuple[TaskStatus, ...]
    required_terminal_status: TaskStatus | None = None
    must_generate_artifact: bool = False
    must_request_clarification: bool = False
    must_require_approval: bool = False
    must_not_execute_tools: bool = False
    allowed_error_codes: tuple[str, ...] = ()
    required_warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_required_status(self) -> ExpectedOutcome:
        if (
            self.required_terminal_status is not None
            and self.required_terminal_status not in self.allowed_terminal_statuses
        ):
            raise ValueError("required_terminal_status must be allowed")
        return self


class ExpectedPlan(EvaluationModel):
    plan_required: bool = True
    must_be_valid: bool = True
    required_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    required_dependency_edges: tuple[tuple[str, str], ...] = ()
    max_steps: int | None = Field(default=None, ge=1)
    must_not_have_cycle: bool = True
    must_include_report_step: bool = True


class ExpectedTools(EvaluationModel):
    required_tools: tuple[str, ...] = ()
    optional_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    required_order_constraints: tuple[tuple[str, str], ...] = ()
    max_call_counts: dict[str, int] = Field(default_factory=dict)
    expected_failure_tools: tuple[str, ...] = ()


class ExpectedClaim(EvaluationModel):
    claim_id: str
    evidence_type: EvidenceType
    source_id: str | None = None
    query_required: bool = False
    lineage_required: bool = False


class ExpectedEvidence(EvaluationModel):
    required_evidence_types: tuple[EvidenceType, ...] = ()
    required_source_ids: tuple[str, ...] = ()
    required_query_ids: tuple[str, ...] = ()
    required_lineage_edges: tuple[tuple[str, str], ...] = ()
    claims: tuple[ExpectedClaim, ...] = ()
    minimum_coverage: Decimal = Field(default=Decimal("1"), ge=0, le=1)


class ExpectedCitations(EvaluationModel):
    required: bool = False
    minimum_count: int = Field(default=0, ge=0)


class ExpectedNumericAssertion(EvaluationModel):
    assertion_id: str
    json_path: str
    expected_value: Decimal | None
    absolute_tolerance: Decimal = Field(default=Decimal("0"), ge=0)
    relative_tolerance: Decimal = Field(default=Decimal("0"), ge=0)
    unit: str | None = None
    allow_null: bool = False


class ExpectedSafety(EvaluationModel):
    sensitive: bool = False
    must_block: bool = False
    forbidden_tools: tuple[str, ...] = ()
    forbidden_tables: tuple[str, ...] = ()
    forbidden_fields: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    must_not_generate_artifact: bool = False
    expected_policy_decision: str | None = None
    allowed_error_codes: tuple[str, ...] = ()
    forbidden_content: tuple[str, ...] = ()


class ExpectedRecovery(EvaluationModel):
    required: bool = False
    expected_retry_count: int | None = Field(default=None, ge=0)
    expected_replan_count: int | None = Field(default=None, ge=0)
    max_replan_count: int | None = Field(default=None, ge=0)


class EvaluationCase(EvaluationModel):
    schema_version: str = "evaluation-case.v1"
    case_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    dataset_id: str
    dataset_version: str
    name: str
    description: str
    category: Literal[
        "normal",
        "clarification",
        "empty_data",
        "rag_failure",
        "tool_failure",
        "numeric_edge",
        "approval",
        "security",
        "authorization",
        "validation",
        "plan_repair",
        "registry",
    ]
    tags: tuple[str, ...]
    language: str = "en-US"
    enabled: bool = True
    task_input: TaskInputSpec
    actor_context: ActorContext
    execution_config: ExecutionConfigSpec = Field(default_factory=ExecutionConfigSpec)
    fixture_refs: tuple[str, ...] = ()
    fault_injection: tuple[FaultInjectionSpec, ...] = ()
    expected_outcome: ExpectedOutcome
    expected_plan: ExpectedPlan = Field(default_factory=ExpectedPlan)
    expected_tools: ExpectedTools = Field(default_factory=ExpectedTools)
    expected_deliverables: tuple[str, ...] = ()
    expected_evidence: ExpectedEvidence = Field(default_factory=ExpectedEvidence)
    expected_citations: ExpectedCitations = Field(default_factory=ExpectedCitations)
    expected_numbers: tuple[ExpectedNumericAssertion, ...] = ()
    expected_safety: ExpectedSafety = Field(default_factory=ExpectedSafety)
    expected_recovery: ExpectedRecovery = Field(default_factory=ExpectedRecovery)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("tags must be non-empty and unique")
        return value


class LLMUsageRecord(EvaluationModel):
    node_name: str
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class CapturedExecution(EvaluationModel):
    task_id: str | None = None
    trace_id: str | None = None
    started_at: datetime
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    terminal_task_status: TaskStatus | None = None
    task_request_text: str
    task_contract: TaskContract | None = None
    plan_snapshot: TaskPlan | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    step_results: tuple[StepResult, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    artifact_texts: tuple[str, ...] = ()
    verification_result: VerificationResult | None = None
    approvals: tuple[ApprovalRequest, ...] = ()
    errors: tuple[TaskError, ...] = ()
    warnings: tuple[str, ...] = ()
    workflow_events: tuple[dict[str, JsonValue], ...] = ()
    llm_usage: tuple[LLMUsageRecord, ...] = ()
    retry_count: int = Field(default=0, ge=0)
    replan_count: int = Field(default=0, ge=0)
    plan_repair_count: int = Field(default=0, ge=0)
    interrupted: bool = False
    harness_error: str | None = None


class MetricResult(EvaluationModel):
    metric_name: str
    value: Decimal | None = None
    numerator: Decimal | None = None
    denominator: Decimal | None = None
    unit: str
    direction: MetricDirection
    coverage: Decimal | None = Field(default=None, ge=0, le=1)
    status: MetricStatus
    notes: tuple[str, ...] = ()


class EvaluationCaseResult(EvaluationModel):
    case_id: str
    category: str
    status: EvaluationCaseStatus
    task_id: str | None = None
    trace_id: str | None = None
    started_at: datetime
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    terminal_task_status: TaskStatus | None = None
    task_contract: TaskContract | None = None
    plan_snapshot: TaskPlan | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    step_results: tuple[StepResult, ...] = ()
    evidence_summary: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_summary: dict[str, JsonValue] = Field(default_factory=dict)
    verification_result: VerificationResult | None = None
    approvals: tuple[ApprovalRequest, ...] = ()
    approval_summary: dict[str, JsonValue] = Field(default_factory=dict)
    errors: tuple[TaskError, ...] = ()
    warnings: tuple[str, ...] = ()
    workflow_events: tuple[dict[str, JsonValue], ...] = ()
    metric_results: tuple[MetricResult, ...] = ()
    primary_failure_category: FailureCategory | None = None
    failure_categories: tuple[FailureCategory, ...] = ()
    diagnostics: tuple[str, ...] = ()


class BaselineMetric(EvaluationModel):
    value: Decimal | None
    direction: MetricDirection
    tolerance: Decimal = Decimal("0")
    coverage: Decimal | None = None


class EvaluationBaseline(EvaluationModel):
    schema_version: str = "evaluation-baseline.v1"
    dataset_id: str
    dataset_version: str
    dataset_hash: str
    seed: int
    agent_version: str
    git_commit: str
    metrics: dict[str, BaselineMetric]
    case_outcomes: dict[str, str]
    known_failures: tuple[str, ...] = ()
    created_at: datetime


class BaselineComparison(EvaluationModel):
    baseline_path: str | None = None
    compatible: bool = True
    regressions: tuple[str, ...] = ()
    missing_metrics: tuple[str, ...] = ()


class GateResult(EvaluationModel):
    passed: bool
    reasons: tuple[str, ...] = ()


class EvaluationRunResult(EvaluationModel):
    schema_version: str = "evaluation-run.v1"
    run_id: str
    dataset_id: str
    dataset_version: str
    dataset_hash: str
    config_hash: str
    fixture_hash: str
    seed: int
    mode: Literal["mock", "live"]
    git_commit: str
    python_version: str
    platform: str
    agent_version: str
    provider: str
    model: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    errored_cases: int = Field(ge=0)
    skipped_cases: int = Field(ge=0)
    metrics: tuple[MetricResult, ...]
    category_metrics: dict[str, tuple[MetricResult, ...]]
    case_results: tuple[EvaluationCaseResult, ...]
    failure_summary: dict[str, int]
    baseline_comparison: BaselineComparison = Field(default_factory=BaselineComparison)
    gate_result: GateResult
    warnings: tuple[str, ...] = ()


__all__ = [
    "ActorContext",
    "BaselineComparison",
    "BaselineMetric",
    "CapturedExecution",
    "EvaluationBaseline",
    "EvaluationCase",
    "EvaluationCaseResult",
    "EvaluationCaseStatus",
    "EvaluationRunResult",
    "ExpectedNumericAssertion",
    "FailureCategory",
    "GateResult",
    "LLMUsageRecord",
    "MetricDirection",
    "MetricResult",
    "MetricStatus",
]
