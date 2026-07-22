"""Runtime-only workflow values that supplement rather than replace frozen contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from copilot.contracts import (
    Artifact,
    EvidenceItem,
    JsonObject,
    StepResult,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    ToolResult,
)


@dataclass(frozen=True, slots=True)
class SupplierQualityCommand:
    """Validated interface command for the one supported deterministic scenario."""

    supplier_id: str
    material_id: str
    time_range: str
    user_id: str = "U-DEMO"
    tenant_id: str = "TENANT-DEMO"
    language: str = "en-US"


@dataclass(frozen=True, slots=True)
class ToolAttemptSummary:
    """Safe attempt metadata retained outside the immutable StepResult contract."""

    attempt: int
    tool_call_id: str
    status: str
    duration_ms: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class StepExecutionRecord:
    """Operational envelope for one frozen StepResult."""

    step_id: str
    tool_name: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    attempt_count: int
    executed: bool
    input_summary: JsonObject
    output_summary: JsonObject
    failed_dependencies: tuple[str, ...] = ()
    attempts: tuple[ToolAttemptSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskStateEvent:
    """Immutable state transition event committed with a TaskState snapshot."""

    event_id: str
    task_id: str
    from_state: str
    event: str
    to_state: str
    timestamp: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class WorkflowAuditRecord:
    """Minimal structured workflow event without sensitive payloads."""

    event_id: str
    event: str
    task_id: str
    plan_id: str
    plan_version: int
    timestamp: datetime
    step_id: str | None = None
    tool_name: str | None = None
    attempt: int | None = None
    status: str | None = None
    duration_ms: int | None = None
    error_type: str | None = None
    evidence_ids: tuple[str, ...] = ()
    artifact_id: str | None = None
    metadata: JsonObject = field(default_factory=lambda: JsonObject({}))


@dataclass(slots=True)
class WorkflowExecutionContext:
    """Mutable, task-local aggregation state kept separate from TaskState."""

    task_id: str
    request: TaskRequest
    contract: TaskContract
    plan: TaskPlan
    task_state: TaskState
    started_at: datetime
    current_step_id: str | None = None
    step_results: dict[str, StepResult] = field(default_factory=dict)
    step_executions: dict[str, StepExecutionRecord] = field(default_factory=dict)
    tool_results: dict[str, list[ToolResult]] = field(default_factory=dict)
    evidence: dict[str, EvidenceItem] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowExecution:
    """Read-only execution view returned to interfaces and tests."""

    task_result: TaskResult
    final_state: TaskState
    step_results: tuple[StepResult, ...]
    step_executions: tuple[StepExecutionRecord, ...]
    evidence: tuple[EvidenceItem, ...]
    artifacts: tuple[Artifact, ...]
    started_at: datetime
    completed_at: datetime
    duration_ms: int
