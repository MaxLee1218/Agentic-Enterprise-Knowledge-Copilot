"""SQLAlchemy models for Copilot-owned persistence.

The payload columns intentionally store versioned domain contracts as canonical JSON.  ORM
objects never cross the persistence boundary; repositories deserialize them back into the frozen
Pydantic contracts.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PersistenceBase(DeclarativeBase):
    """Single Alembic metadata source for Copilot-owned business persistence."""


class WorkflowTaskRow(PersistenceBase):
    __tablename__ = "workflow_tasks"

    task_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    contract_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    task_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkflowStateEventRow(PersistenceBase):
    __tablename__ = "workflow_state_events"
    __table_args__ = (Index("ix_workflow_state_events_task_sequence", "task_id", "sequence_id"),)

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowToolResultRow(PersistenceBase):
    __tablename__ = "workflow_tool_results"
    __table_args__ = (Index("ix_workflow_tool_results_task_sequence", "task_id", "sequence_id"),)

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_call_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowStepResultRow(PersistenceBase):
    __tablename__ = "workflow_step_results"
    __table_args__ = (Index("ix_workflow_step_results_task_sequence", "task_id", "sequence_id"),)

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    step_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    execution_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowLeaseRow(PersistenceBase):
    __tablename__ = "workflow_leases"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowPlanHistoryRow(PersistenceBase):
    __tablename__ = "workflow_plan_history"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "planning_version", "plan_json", name="uq_workflow_plan_history"
        ),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    planning_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowVerificationHistoryRow(PersistenceBase):
    __tablename__ = "workflow_verification_history"
    __table_args__ = (
        UniqueConstraint("task_id", "verification_json", name="uq_workflow_verification_history"),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    verification_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowApprovalRow(PersistenceBase):
    __tablename__ = "workflow_approvals"
    __table_args__ = (
        Index("ix_workflow_approvals_task_status", "task_id", "status"),
        Index("ix_workflow_approvals_task_step", "task_id", "step_id"),
    )

    approval_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    step_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowApprovalHistoryRow(PersistenceBase):
    __tablename__ = "workflow_approval_history"

    approval_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowEvidenceRow(PersistenceBase):
    __tablename__ = "workflow_evidence"
    __table_args__ = (
        UniqueConstraint("task_id", "fingerprint", name="uq_workflow_evidence_fingerprint"),
        Index("ix_workflow_evidence_task_sequence", "task_id", "sequence_id"),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evidence_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowArtifactRow(PersistenceBase):
    __tablename__ = "workflow_artifacts"
    __table_args__ = (Index("ix_workflow_artifacts_task_sequence", "task_id", "sequence_id"),)

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowToolAuditRow(PersistenceBase):
    __tablename__ = "workflow_tool_audit"

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowGraphAuditRow(PersistenceBase):
    __tablename__ = "workflow_graph_audit"

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = [
    "PersistenceBase",
    "WorkflowApprovalHistoryRow",
    "WorkflowApprovalRow",
    "WorkflowArtifactRow",
    "WorkflowEvidenceRow",
    "WorkflowGraphAuditRow",
    "WorkflowLeaseRow",
    "WorkflowPlanHistoryRow",
    "WorkflowStateEventRow",
    "WorkflowStepResultRow",
    "WorkflowTaskRow",
    "WorkflowToolAuditRow",
    "WorkflowToolResultRow",
    "WorkflowVerificationHistoryRow",
]
