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
)
from copilot.services.workflows.models import (
    StepExecutionRecord,
    TaskStateEvent,
    WorkflowAuditRecord,
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

    def save_tool_result(self, result: ToolResult) -> None:
        """Append one immutable tool attempt result."""
        ...

    def save_step_result(self, result: StepResult, execution: StepExecutionRecord) -> None:
        """Save the final step result and its operational envelope."""
        ...

    def save_task_result(self, result: TaskResult) -> None:
        """Save the one terminal task result."""
        ...


class WorkflowAuditSink(Protocol):
    """Fail-closed append-only structured workflow event sink."""

    def append(self, record: WorkflowAuditRecord) -> None:
        """Append one event or raise when durability cannot be guaranteed."""
        ...
