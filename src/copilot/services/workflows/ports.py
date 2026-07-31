"""Application-owned ports for deterministic workflow persistence and time."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from copilot.contracts import (
    Artifact,
    ArtifactType,
    EvidenceItem,
    StepResult,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    ToolResult,
    VerificationResult,
)
from copilot.services.workflows.models import (
    StepExecutionRecord,
    TaskStateEvent,
    WorkflowAuditRecord,
    WorkflowExecutionContext,
)


class IdentifierFactory(Protocol):
    """Create collision-resistant identifiers with a stable semantic prefix."""

    def new_id(self, prefix: str) -> str:
        """Return a new identifier."""
        ...


class EvidenceReader(Protocol):
    """Read immutable evidence already committed by ToolExecutor."""

    def get(self, evidence_id: str) -> EvidenceItem:
        """Return one evidence item."""
        ...


class WorkflowVerificationService(Protocol):
    """Application port for deterministic final verification."""

    def verify(self, context: WorkflowExecutionContext) -> VerificationResult:
        """Return a persisted-ready structured verification result."""
        ...


class ArtifactStore(Protocol):
    """Governed local artifact persistence boundary."""

    def write(
        self,
        *,
        artifact_id: str,
        task_id: str,
        artifact_type: ArtifactType,
        filename: str,
        media_type: str,
        content: bytes,
        generator_version: str,
        evidence_ids: tuple[str, ...],
    ) -> Artifact:
        """Atomically commit immutable artifact bytes and metadata."""
        ...

    def get(self, artifact_id: str) -> Artifact:
        """Return committed artifact metadata."""
        ...

    def path_for(self, artifact: Artifact) -> Path:
        """Resolve an artifact to a controlled local path."""
        ...

    def delete(self, artifact_id: str) -> None:
        """Compensate one invalid, unpublished Artifact."""
        ...


class WorkflowRepository(Protocol):
    """Persistence port for task snapshots and append-only execution results."""

    def initialize(
        self,
        request: TaskRequest,
        contract: TaskContract,
        plan: TaskPlan,
        state: TaskState,
    ) -> None:
        """Persist the initial immutable workflow objects."""
        ...

    def commit_transition(
        self,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> None:
        """Compare-and-swap a state and append its event atomically."""
        ...

    def save_contract(self, contract: TaskContract) -> None:
        """Persist a validated understanding result before planning."""
        ...

    def save_plan(self, plan: TaskPlan) -> None:
        """Persist the current validated candidate while retaining prior versions."""
        ...

    def save_tool_result(self, result: ToolResult) -> None:
        """Append one immutable tool attempt result."""
        ...

    def save_step_result(self, result: StepResult, execution: StepExecutionRecord) -> None:
        """Save the final step result and its operational envelope."""
        ...

    def save_task_result(self, result: TaskResult) -> None:
        """Save the one terminal task result."""
        ...

    def save_verification_result(self, result: VerificationResult) -> None:
        """Append the task's deterministic verification result."""
        ...

    def state_for(self, task_id: str) -> TaskState:
        """Return the authoritative persisted domain-state snapshot."""
        ...

    def acquire_execution(self, task_id: str, owner_id: str) -> None:
        """Acquire the single-task execution lease or raise on a conflict."""
        ...

    def release_execution(self, task_id: str, owner_id: str) -> None:
        """Release a lease owned by the current workflow engine."""
        ...


class WorkflowAuditSink(Protocol):
    """Fail-closed append-only structured workflow event sink."""

    def append(self, record: WorkflowAuditRecord) -> None:
        """Append one event or raise when durability cannot be guaranteed."""
        ...
