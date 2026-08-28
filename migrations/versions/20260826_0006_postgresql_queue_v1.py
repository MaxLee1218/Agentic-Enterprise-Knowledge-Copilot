"""Add PostgreSQL-backed Queue v1 transport records.

Revision ID: 20260826_0006
Revises: 20260826_0005
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0006"
down_revision: str | None = "20260826_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create subordinate Queue delivery state without changing Task or lease authority."""
    op.create_table(
        "task_queue_deliveries",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("dispatch_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("receipt_id", sa.String(length=200), nullable=True),
        sa.Column("receipt_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("delivery_attempt >= 0", name="ck_task_queue_delivery_attempt"),
        sa.CheckConstraint(
            "(receipt_id IS NULL AND receipt_expires_at IS NULL) "
            "OR (receipt_id IS NOT NULL AND receipt_expires_at IS NOT NULL)",
            name="ck_task_queue_delivery_receipt_binding",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id", "dispatch_id"],
            [
                "task_dispatches.tenant_id",
                "task_dispatches.task_id",
                "task_dispatches.dispatch_id",
            ],
            name="fk_task_queue_deliveries_dispatch",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "dispatch_id"),
    )
    op.create_index(
        "ix_task_queue_deliveries_due",
        "task_queue_deliveries",
        ["acked_at", "available_at", "receipt_expires_at", "tenant_id"],
    )


def downgrade() -> None:
    """Remove only the Queue transport table."""
    op.drop_index("ix_task_queue_deliveries_due", table_name="task_queue_deliveries")
    op.drop_table("task_queue_deliveries")
