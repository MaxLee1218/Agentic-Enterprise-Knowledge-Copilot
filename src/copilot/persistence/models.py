"""SQLAlchemy models for Copilot-owned persistence.

The payload columns intentionally store versioned domain contracts as canonical JSON.  ORM
objects never cross the persistence boundary; repositories deserialize them back into the frozen
Pydantic contracts.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
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
    __table_args__ = (
        UniqueConstraint("tenant_id", "task_id", name="uq_workflow_tasks_tenant_task"),
        Index("ix_workflow_tasks_tenant_task", "tenant_id", "task_id"),
    )

    task_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    contract_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    task_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkflowStateEventRow(PersistenceBase):
    __tablename__ = "workflow_state_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_state_events_tenant_task",
            ondelete="CASCADE",
        ),
        Index(
            "ix_workflow_state_events_tenant_task_sequence",
            "tenant_id",
            "task_id",
            "sequence_id",
        ),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowToolResultRow(PersistenceBase):
    __tablename__ = "workflow_tool_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_tool_results_tenant_task",
            ondelete="CASCADE",
        ),
        Index(
            "ix_workflow_tool_results_tenant_task_sequence",
            "tenant_id",
            "task_id",
            "sequence_id",
        ),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowStepResultRow(PersistenceBase):
    __tablename__ = "workflow_step_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_step_results_tenant_task",
            ondelete="CASCADE",
        ),
        Index(
            "ix_workflow_step_results_tenant_task_sequence",
            "tenant_id",
            "task_id",
            "sequence_id",
        ),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    step_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    execution_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowLeaseRow(PersistenceBase):
    __tablename__ = "workflow_leases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_leases_tenant_task",
            ondelete="CASCADE",
        ),
        Index("ix_workflow_leases_tenant_task", "tenant_id", "task_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowPlanHistoryRow(PersistenceBase):
    __tablename__ = "workflow_plan_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_plan_history_tenant_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "task_id", "planning_version", "plan_json", name="uq_workflow_plan_history"
        ),
        Index("ix_workflow_plan_history_tenant_task", "tenant_id", "task_id"),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    planning_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowVerificationHistoryRow(PersistenceBase):
    __tablename__ = "workflow_verification_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_verification_history_tenant_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint("task_id", "verification_json", name="uq_workflow_verification_history"),
        Index("ix_workflow_verification_history_tenant_task", "tenant_id", "task_id"),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    verification_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowApprovalRow(PersistenceBase):
    __tablename__ = "workflow_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_approvals_tenant_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "approval_id",
            name="uq_workflow_approvals_tenant_approval",
        ),
        Index("ix_workflow_approvals_task_status", "task_id", "status"),
        Index("ix_workflow_approvals_task_step", "task_id", "step_id"),
        Index("ix_workflow_approvals_tenant_task", "tenant_id", "task_id"),
        Index("ix_workflow_approvals_tenant_approval", "tenant_id", "approval_id"),
    )

    approval_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    step_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowApprovalHistoryRow(PersistenceBase):
    __tablename__ = "workflow_approval_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "approval_id"],
            ["workflow_approvals.tenant_id", "workflow_approvals.approval_id"],
            name="fk_workflow_approval_history_tenant_approval",
            ondelete="CASCADE",
        ),
        Index("ix_workflow_approval_history_tenant_approval", "tenant_id", "approval_id"),
    )

    approval_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowEvidenceRow(PersistenceBase):
    __tablename__ = "workflow_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_evidence_tenant_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint("task_id", "fingerprint", name="uq_workflow_evidence_fingerprint"),
        Index(
            "ix_workflow_evidence_tenant_task_sequence",
            "tenant_id",
            "task_id",
            "sequence_id",
        ),
        Index("ix_workflow_evidence_tenant_task", "tenant_id", "task_id"),
        Index("ix_workflow_evidence_tenant_evidence", "tenant_id", "evidence_id"),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowArtifactRow(PersistenceBase):
    __tablename__ = "workflow_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_artifacts_tenant_task",
            ondelete="CASCADE",
        ),
        Index(
            "ix_workflow_artifacts_tenant_task_sequence",
            "tenant_id",
            "task_id",
            "sequence_id",
        ),
        Index("ix_workflow_artifacts_tenant_task", "tenant_id", "task_id"),
        Index("ix_workflow_artifacts_tenant_artifact", "tenant_id", "artifact_id"),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowToolAuditRow(PersistenceBase):
    """Tenant-scoped audit that may record denial before a Task row exists."""

    __tablename__ = "workflow_tool_audit"
    __table_args__ = (Index("ix_workflow_tool_audit_tenant_task", "tenant_id", "task_id"),)

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowGraphAuditRow(PersistenceBase):
    """Tenant-scoped lifecycle audit retained even when Task persistence fails."""

    __tablename__ = "workflow_graph_audit"
    __table_args__ = (Index("ix_workflow_graph_audit_tenant_task", "tenant_id", "task_id"),)

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class MCPConnectionRow(PersistenceBase):
    """Tenant-scoped non-secret approved MCP connection configuration."""

    __tablename__ = "mcp_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "namespace", name="uq_mcp_connections_tenant_namespace"),
        Index("ix_mcp_connections_tenant_server", "tenant_id", "server_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(200), nullable=False)
    namespace: Mapped[str] = mapped_column(String(200), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MCPSessionRow(PersistenceBase):
    """Per-server, per-tenant negotiated session and recovery snapshot."""

    __tablename__ = "mcp_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["mcp_connections.tenant_id", "mcp_connections.connection_id"],
            name="fk_mcp_sessions_tenant_connection",
            ondelete="CASCADE",
        ),
        Index("ix_mcp_sessions_tenant_connection", "tenant_id", "connection_id"),
        Index("ix_mcp_sessions_tenant_state", "tenant_id", "state"),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(200), nullable=False)
    server_id: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MCPInvocationRow(PersistenceBase):
    """Minimized append-only MCP invocation audit metadata."""

    __tablename__ = "mcp_invocations"
    __table_args__ = (
        Index("ix_mcp_invocations_tenant_task", "tenant_id", "task_id"),
        Index("ix_mcp_invocations_tenant_session", "tenant_id", "session_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    invocation_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "MCPConnectionRow",
    "MCPInvocationRow",
    "MCPSessionRow",
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
