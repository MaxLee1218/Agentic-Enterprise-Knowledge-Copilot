"""Create the Stage 17 Copilot persistence schema.

Revision ID: 20260807_0001
Revises: None
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all Copilot-owned authoritative tables and indexes."""
    op.create_table(
        "workflow_tasks",
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("task_result_json", sa.Text(), nullable=True),
        sa.Column("verification_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_table(
        "workflow_approvals",
        sa.Column("approval_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("step_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("approval_id"),
    )
    op.create_index(
        "ix_workflow_approvals_task_status",
        "workflow_approvals",
        ["task_id", "status"],
    )
    op.create_index(
        "ix_workflow_approvals_task_step",
        "workflow_approvals",
        ["task_id", "step_id"],
    )
    op.create_table(
        "workflow_approval_history",
        sa.Column("approval_id", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("approval_id", "version"),
    )
    op.create_table(
        "workflow_evidence",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evidence_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("fingerprint", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint("evidence_id"),
        sa.UniqueConstraint("task_id", "fingerprint", name="uq_workflow_evidence_fingerprint"),
    )
    op.create_index(
        "ix_workflow_evidence_task_sequence",
        "workflow_evidence",
        ["task_id", "sequence_id"],
    )
    op.create_table(
        "workflow_artifacts",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint("artifact_id"),
    )
    op.create_index(
        "ix_workflow_artifacts_task_sequence",
        "workflow_artifacts",
        ["task_id", "sequence_id"],
    )
    op.create_table(
        "workflow_tool_audit",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_table(
        "workflow_graph_audit",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_table(
        "workflow_state_events",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["workflow_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        "ix_workflow_state_events_task_sequence",
        "workflow_state_events",
        ["task_id", "sequence_id"],
    )
    op.create_table(
        "workflow_tool_results",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tool_call_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["workflow_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint("tool_call_id"),
    )
    op.create_index(
        "ix_workflow_tool_results_task_sequence",
        "workflow_tool_results",
        ["task_id", "sequence_id"],
    )
    op.create_table(
        "workflow_step_results",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("step_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("execution_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["workflow_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint("step_id"),
    )
    op.create_index(
        "ix_workflow_step_results_task_sequence",
        "workflow_step_results",
        ["task_id", "sequence_id"],
    )
    op.create_table(
        "workflow_leases",
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["workflow_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_table(
        "workflow_plan_history",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("planning_version", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["workflow_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint(
            "task_id", "planning_version", "plan_json", name="uq_workflow_plan_history"
        ),
    )
    op.create_table(
        "workflow_verification_history",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("verification_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["workflow_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint(
            "task_id", "verification_json", name="uq_workflow_verification_history"
        ),
    )


def downgrade() -> None:
    """Drop the isolated Copilot schema; production downgrade requires a backup decision."""
    op.drop_table("workflow_verification_history")
    op.drop_table("workflow_plan_history")
    op.drop_table("workflow_leases")
    op.drop_index("ix_workflow_step_results_task_sequence", table_name="workflow_step_results")
    op.drop_table("workflow_step_results")
    op.drop_index("ix_workflow_tool_results_task_sequence", table_name="workflow_tool_results")
    op.drop_table("workflow_tool_results")
    op.drop_index("ix_workflow_state_events_task_sequence", table_name="workflow_state_events")
    op.drop_table("workflow_state_events")
    op.drop_table("workflow_graph_audit")
    op.drop_table("workflow_tool_audit")
    op.drop_index("ix_workflow_artifacts_task_sequence", table_name="workflow_artifacts")
    op.drop_table("workflow_artifacts")
    op.drop_index("ix_workflow_evidence_task_sequence", table_name="workflow_evidence")
    op.drop_table("workflow_evidence")
    op.drop_table("workflow_approval_history")
    op.drop_index("ix_workflow_approvals_task_step", table_name="workflow_approvals")
    op.drop_index("ix_workflow_approvals_task_status", table_name="workflow_approvals")
    op.drop_table("workflow_approvals")
    op.drop_table("workflow_tasks")
