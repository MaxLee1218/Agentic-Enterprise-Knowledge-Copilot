"""Add durable interactive clarification records.

Revision ID: 20260831_0007
Revises: 20260826_0006
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0007"
down_revision: str | None = "20260826_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create current/history clarification state with one-active-round enforcement."""
    op.create_table(
        "workflow_clarifications",
        sa.Column("clarification_id", sa.String(length=200), nullable=False),
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active_task_id", sa.String(length=200), nullable=True),
        sa.Column("response_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("resume_dispatch_id", sa.String(length=200), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("round >= 1", name="ck_workflow_clarification_round"),
        sa.CheckConstraint("version >= 1", name="ck_workflow_clarification_version"),
        sa.CheckConstraint(
            "status IN ('PENDING','SUBMITTED','RESOLVED','REJECTED','CANCELLED')",
            name="ck_workflow_clarification_status",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND active_task_id = task_id) "
            "OR (status <> 'PENDING' AND active_task_id IS NULL)",
            name="ck_workflow_clarification_active_binding",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_clarifications_tenant_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("clarification_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "clarification_id",
            name="uq_workflow_clarifications_tenant_clarification",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "round",
            name="uq_workflow_clarifications_tenant_task_round",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "active_task_id",
            name="uq_workflow_clarifications_one_active",
        ),
    )
    op.create_index(
        "ix_workflow_clarifications_tenant_task",
        "workflow_clarifications",
        ["tenant_id", "task_id"],
    )
    op.create_index(
        "ix_workflow_clarifications_task_status",
        "workflow_clarifications",
        ["task_id", "status"],
    )
    op.create_table(
        "workflow_clarification_history",
        sa.Column("clarification_id", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "clarification_id"],
            ["workflow_clarifications.tenant_id", "workflow_clarifications.clarification_id"],
            name="fk_workflow_clarification_history_tenant_clarification",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("clarification_id", "version"),
    )
    op.create_index(
        "ix_workflow_clarification_history_tenant_clarification",
        "workflow_clarification_history",
        ["tenant_id", "clarification_id"],
    )


def downgrade() -> None:
    """Remove clarification history before current snapshots."""
    op.drop_index(
        "ix_workflow_clarification_history_tenant_clarification",
        table_name="workflow_clarification_history",
    )
    op.drop_table("workflow_clarification_history")
    op.drop_index(
        "ix_workflow_clarifications_task_status",
        table_name="workflow_clarifications",
    )
    op.drop_index(
        "ix_workflow_clarifications_tenant_task",
        table_name="workflow_clarifications",
    )
    op.drop_table("workflow_clarifications")
