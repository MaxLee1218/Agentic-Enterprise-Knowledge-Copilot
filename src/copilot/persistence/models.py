"""SQLAlchemy models for Copilot-owned persistence.

The payload columns intentionally store versioned domain contracts as canonical JSON.  ORM
objects never cross the persistence boundary; repositories deserialize them back into the frozen
Pydantic contracts.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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


class TaskDispatchRow(PersistenceBase):
    """Durable transactional-outbox record for one immutable execution intent."""

    __tablename__ = "task_dispatches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_task_dispatches_tenant_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "execution_generation",
            name="uq_task_dispatches_tenant_task_generation",
        ),
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "dispatch_id",
            name="uq_task_dispatches_tenant_task_dispatch",
        ),
        CheckConstraint("execution_generation >= 1", name="ck_task_dispatch_generation"),
        CheckConstraint("expected_task_version >= 1", name="ck_task_dispatch_version"),
        CheckConstraint("attempt_count >= 0", name="ck_task_dispatch_attempt_count"),
        CheckConstraint(
            "status IN ('PENDING','ENQUEUED','ACKNOWLEDGED','RETRY_SCHEDULED',"
            "'SUPERSEDED','DEAD_LETTERED')",
            name="ck_task_dispatch_status",
        ),
        CheckConstraint(
            "(predecessor_execution_generation IS NULL AND resume_checkpoint_id IS NULL) "
            "OR (predecessor_execution_generation = execution_generation - 1 "
            "AND resume_checkpoint_id IS NOT NULL)",
            name="ck_task_dispatch_resume_binding",
        ),
        Index("ix_task_dispatches_due", "status", "available_at", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    dispatch_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    execution_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    predecessor_execution_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resume_checkpoint_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expected_task_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskQueueDeliveryRow(PersistenceBase):
    """PostgreSQL Queue v1 transport state subordinate to one durable dispatch."""

    __tablename__ = "task_queue_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id", "dispatch_id"],
            [
                "task_dispatches.tenant_id",
                "task_dispatches.task_id",
                "task_dispatches.dispatch_id",
            ],
            name="fk_task_queue_deliveries_dispatch",
            ondelete="CASCADE",
        ),
        CheckConstraint("delivery_attempt >= 0", name="ck_task_queue_delivery_attempt"),
        CheckConstraint(
            "(receipt_id IS NULL AND receipt_expires_at IS NULL) "
            "OR (receipt_id IS NOT NULL AND receipt_expires_at IS NOT NULL)",
            name="ck_task_queue_delivery_receipt_binding",
        ),
        Index(
            "ix_task_queue_deliveries_due",
            "acked_at",
            "available_at",
            "receipt_expires_at",
            "tenant_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    dispatch_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    receipt_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receipt_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowTaskRuntimeRow(PersistenceBase):
    """One-to-one authoritative runtime projection and monotonic fencing counter."""

    __tablename__ = "workflow_task_runtime"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_task_runtime_tenant_task",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "runtime_status IN ('READY','LEASED','WAITING_RETRY','SUSPENDED','FINISHED')",
            name="ck_workflow_task_runtime_status",
        ),
        CheckConstraint("execution_generation >= 1", name="ck_workflow_task_runtime_generation"),
        CheckConstraint("fencing_counter >= 0", name="ck_workflow_task_runtime_fencing"),
        CheckConstraint(
            "recovery_attempt_count >= 0", name="ck_workflow_task_runtime_recovery_count"
        ),
        CheckConstraint(
            "(runtime_status = 'WAITING_RETRY' AND retry_not_before IS NOT NULL) "
            "OR (runtime_status <> 'WAITING_RETRY' AND retry_not_before IS NULL)",
            name="ck_workflow_task_runtime_retry_binding",
        ),
        CheckConstraint(
            "(predecessor_execution_generation IS NULL AND resume_checkpoint_id IS NULL) "
            "OR (predecessor_execution_generation = execution_generation - 1 "
            "AND resume_checkpoint_id IS NOT NULL)",
            name="ck_workflow_task_runtime_resume_binding",
        ),
        Index("ix_workflow_task_runtime_recovery", "runtime_status", "retry_not_before"),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    runtime_status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    predecessor_execution_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resume_checkpoint_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_dispatch_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fencing_counter: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    recovery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_recovery_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cancellation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskSubmissionIdempotencyRow(PersistenceBase):
    """Tenant/caller/key binding to one canonical submission response."""

    __tablename__ = "task_submission_idempotency"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_task_submission_idempotency_tenant_task",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_task_submission_idempotency_fingerprint",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    caller_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskRuntimeAttemptRow(PersistenceBase):
    """Persistent Worker-host attempt accounting, separate from business Tool attempts."""

    __tablename__ = "task_runtime_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id", "dispatch_id"],
            [
                "task_dispatches.tenant_id",
                "task_dispatches.task_id",
                "task_dispatches.dispatch_id",
            ],
            name="fk_task_runtime_attempts_tenant_task_dispatch",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "execution_generation",
            "runtime_attempt",
            name="uq_task_runtime_attempt_identity",
        ),
        CheckConstraint("execution_generation >= 1", name="ck_task_runtime_attempt_generation"),
        CheckConstraint("runtime_attempt >= 1", name="ck_task_runtime_attempt_number"),
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','SUSPENDED','FAILED','LOST')",
            name="ck_task_runtime_attempt_status",
        ),
        Index("ix_task_runtime_attempts_tenant_task", "tenant_id", "task_id"),
    )

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dispatch_id: Mapped[str] = mapped_column(String(200), nullable=False)
    execution_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runtime_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)


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
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "step_id",
            name="uq_workflow_step_results_tenant_task_step",
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
    step_id: Mapped[str] = mapped_column(String(200), nullable=False)
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
        ForeignKeyConstraint(
            ["tenant_id", "task_id", "dispatch_id"],
            [
                "task_dispatches.tenant_id",
                "task_dispatches.task_id",
                "task_dispatches.dispatch_id",
            ],
            name="fk_workflow_leases_tenant_task_dispatch",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "lease_id", name="uq_workflow_leases_tenant_lease"),
        CheckConstraint("execution_generation >= 1", name="ck_workflow_lease_generation"),
        CheckConstraint("task_version >= 1", name="ck_workflow_lease_task_version"),
        CheckConstraint("fencing_token > 0", name="ck_workflow_lease_fencing"),
        Index("ix_workflow_leases_tenant_task", "tenant_id", "task_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    dispatch_id: Mapped[str] = mapped_column(String(200), nullable=False)
    execution_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
