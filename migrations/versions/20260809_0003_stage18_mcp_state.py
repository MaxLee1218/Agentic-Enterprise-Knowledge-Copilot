"""Add tenant-isolated non-secret MCP state and invocation metadata.

Revision ID: 20260809_0003
Revises: 20260808_0002
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0003"
down_revision: str | None = "20260808_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create MCP connection, session/recovery, and minimized invocation tables."""
    op.create_table(
        "mcp_connections",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("connection_id", sa.String(length=200), nullable=False),
        sa.Column("server_id", sa.String(length=200), nullable=False),
        sa.Column("namespace", sa.String(length=200), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "connection_id"),
        sa.UniqueConstraint("tenant_id", "namespace", name="uq_mcp_connections_tenant_namespace"),
    )
    op.create_index(
        "ix_mcp_connections_tenant_server",
        "mcp_connections",
        ["tenant_id", "server_id"],
    )
    op.create_table(
        "mcp_sessions",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("connection_id", sa.String(length=200), nullable=False),
        sa.Column("server_id", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["mcp_connections.tenant_id", "mcp_connections.connection_id"],
            name="fk_mcp_sessions_tenant_connection",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "session_id"),
    )
    op.create_index(
        "ix_mcp_sessions_tenant_connection",
        "mcp_sessions",
        ["tenant_id", "connection_id"],
    )
    op.create_index("ix_mcp_sessions_tenant_state", "mcp_sessions", ["tenant_id", "state"])
    op.create_table(
        "mcp_invocations",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("invocation_id", sa.String(length=200), nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("trace_id", sa.String(length=200), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "invocation_id"),
    )
    op.create_index(
        "ix_mcp_invocations_tenant_task",
        "mcp_invocations",
        ["tenant_id", "task_id"],
    )
    op.create_index(
        "ix_mcp_invocations_tenant_session",
        "mcp_invocations",
        ["tenant_id", "session_id"],
    )


def downgrade() -> None:
    """Drop Stage 18 state after an explicit backup/rollback decision."""
    op.drop_index("ix_mcp_invocations_tenant_session", table_name="mcp_invocations")
    op.drop_index("ix_mcp_invocations_tenant_task", table_name="mcp_invocations")
    op.drop_table("mcp_invocations")
    op.drop_index("ix_mcp_sessions_tenant_state", table_name="mcp_sessions")
    op.drop_index("ix_mcp_sessions_tenant_connection", table_name="mcp_sessions")
    op.drop_table("mcp_sessions")
    op.drop_index("ix_mcp_connections_tenant_server", table_name="mcp_connections")
    op.drop_table("mcp_connections")
