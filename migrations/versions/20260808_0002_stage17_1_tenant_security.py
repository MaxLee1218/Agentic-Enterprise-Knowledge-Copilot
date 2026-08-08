"""Add mandatory tenant ownership and tenant-first indexes.

Revision ID: 20260808_0002
Revises: 20260807_0001
Create Date: 2026-08-08
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0002"
down_revision: str | None = "20260807_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TENANT = "TENANT-LEGACY-UNSCOPED"
_TASK_CHILD_TABLES = (
    "workflow_state_events",
    "workflow_tool_results",
    "workflow_step_results",
    "workflow_leases",
    "workflow_plan_history",
    "workflow_verification_history",
    "workflow_evidence",
    "workflow_artifacts",
)
_AUDIT_TABLES = (
    "workflow_tool_audit",
    "workflow_graph_audit",
)
_TASK_TABLES = (*_TASK_CHILD_TABLES, *_AUDIT_TABLES)
_ALL_TABLES = ("workflow_tasks", *_TASK_TABLES, "workflow_approvals", "workflow_approval_history")


def upgrade() -> None:
    """Backfill trustworthy ownership, quarantine unknown legacy rows, then require tenants."""
    for table in _ALL_TABLES:
        op.add_column(
            table,
            sa.Column(
                "tenant_id",
                sa.String(length=200),
                nullable=False,
                server_default=_LEGACY_TENANT,
            ),
        )

    connection = op.get_bind()
    audit_tenants = _audit_tenants(connection)
    approval_tenants = _approval_tenants(connection)
    task_rows = connection.execute(
        sa.text("SELECT task_id, contract_json FROM workflow_tasks")
    ).mappings()
    task_tenants: dict[str, str] = {}
    for row in task_rows:
        task_id = str(row["task_id"])
        tenant_id = (
            _tenant_from_contract(row["contract_json"])
            or audit_tenants.get(task_id)
            or approval_tenants.get(task_id)
            or _LEGACY_TENANT
        )
        task_tenants[task_id] = tenant_id
        connection.execute(
            sa.text("UPDATE workflow_tasks SET tenant_id = :tenant_id WHERE task_id = :task_id"),
            {"tenant_id": tenant_id, "task_id": task_id},
        )
    for table in _TASK_CHILD_TABLES:
        for task_id, tenant_id in task_tenants.items():
            connection.execute(
                sa.text(f"UPDATE {table} SET tenant_id = :tenant_id WHERE task_id = :task_id"),
                {"tenant_id": tenant_id, "task_id": task_id},
            )
    approval_rows = connection.execute(
        sa.text("SELECT approval_id, task_id, payload_json FROM workflow_approvals")
    ).mappings()
    approval_by_id: dict[str, str] = {}
    for row in approval_rows:
        tenant_id = (
            _tenant_from_payload(row["payload_json"])
            or task_tenants.get(str(row["task_id"]))
            or _LEGACY_TENANT
        )
        approval_id = str(row["approval_id"])
        approval_by_id[approval_id] = tenant_id
        connection.execute(
            sa.text(
                "UPDATE workflow_approvals SET tenant_id = :tenant_id "
                "WHERE approval_id = :approval_id"
            ),
            {"tenant_id": tenant_id, "approval_id": approval_id},
        )
    for approval_id, tenant_id in approval_by_id.items():
        connection.execute(
            sa.text(
                "UPDATE workflow_approval_history SET tenant_id = :tenant_id "
                "WHERE approval_id = :approval_id"
            ),
            {"tenant_id": tenant_id, "approval_id": approval_id},
        )

    for table in _ALL_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=200),
                nullable=False,
                server_default=None,
            )

    with op.batch_alter_table("workflow_tasks") as batch:
        batch.create_unique_constraint("uq_workflow_tasks_tenant_task", ["tenant_id", "task_id"])
    with op.batch_alter_table("workflow_approvals") as batch:
        batch.create_unique_constraint(
            "uq_workflow_approvals_tenant_approval",
            ["tenant_id", "approval_id"],
        )
    for table in _TASK_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(
                f"fk_{table}_tenant_task",
                "workflow_tasks",
                ["tenant_id", "task_id"],
                ["tenant_id", "task_id"],
                ondelete="CASCADE",
            )
    with op.batch_alter_table("workflow_approvals") as batch:
        batch.create_foreign_key(
            "fk_workflow_approvals_tenant_task",
            "workflow_tasks",
            ["tenant_id", "task_id"],
            ["tenant_id", "task_id"],
            ondelete="CASCADE",
        )
    with op.batch_alter_table("workflow_approval_history") as batch:
        batch.create_foreign_key(
            "fk_workflow_approval_history_tenant_approval",
            "workflow_approvals",
            ["tenant_id", "approval_id"],
            ["tenant_id", "approval_id"],
            ondelete="CASCADE",
        )
    op.create_index("ix_workflow_tasks_tenant_task", "workflow_tasks", ["tenant_id", "task_id"])
    _replace_sequence_index(
        "workflow_state_events",
        "ix_workflow_state_events_task_sequence",
        "ix_workflow_state_events_tenant_task_sequence",
    )
    _replace_sequence_index(
        "workflow_tool_results",
        "ix_workflow_tool_results_task_sequence",
        "ix_workflow_tool_results_tenant_task_sequence",
    )
    _replace_sequence_index(
        "workflow_step_results",
        "ix_workflow_step_results_task_sequence",
        "ix_workflow_step_results_tenant_task_sequence",
    )
    _replace_sequence_index(
        "workflow_evidence",
        "ix_workflow_evidence_task_sequence",
        "ix_workflow_evidence_tenant_task_sequence",
    )
    _replace_sequence_index(
        "workflow_artifacts",
        "ix_workflow_artifacts_task_sequence",
        "ix_workflow_artifacts_tenant_task_sequence",
    )
    op.create_index("ix_workflow_leases_tenant_task", "workflow_leases", ["tenant_id", "task_id"])
    op.create_index(
        "ix_workflow_plan_history_tenant_task",
        "workflow_plan_history",
        ["tenant_id", "task_id"],
    )
    op.create_index(
        "ix_workflow_verification_history_tenant_task",
        "workflow_verification_history",
        ["tenant_id", "task_id"],
    )
    op.create_index(
        "ix_workflow_approvals_tenant_task",
        "workflow_approvals",
        ["tenant_id", "task_id"],
    )
    op.create_index(
        "ix_workflow_approvals_tenant_approval",
        "workflow_approvals",
        ["tenant_id", "approval_id"],
    )
    op.create_index(
        "ix_workflow_approval_history_tenant_approval",
        "workflow_approval_history",
        ["tenant_id", "approval_id"],
    )
    for table, entity in (
        ("workflow_evidence", "evidence"),
        ("workflow_artifacts", "artifact"),
    ):
        op.create_index(f"ix_{table}_tenant_task", table, ["tenant_id", "task_id"])
        op.create_index(f"ix_{table}_tenant_{entity}", table, ["tenant_id", f"{entity}_id"])
    op.create_index(
        "ix_workflow_tool_audit_tenant_task",
        "workflow_tool_audit",
        ["tenant_id", "task_id"],
    )
    op.create_index(
        "ix_workflow_graph_audit_tenant_task",
        "workflow_graph_audit",
        ["tenant_id", "task_id"],
    )


def downgrade() -> None:
    """Remove Stage 17.1 ownership columns after an explicit backup decision."""
    for name, table in (
        ("ix_workflow_graph_audit_tenant_task", "workflow_graph_audit"),
        ("ix_workflow_tool_audit_tenant_task", "workflow_tool_audit"),
        ("ix_workflow_artifacts_tenant_artifact", "workflow_artifacts"),
        ("ix_workflow_artifacts_tenant_task", "workflow_artifacts"),
        ("ix_workflow_evidence_tenant_evidence", "workflow_evidence"),
        ("ix_workflow_evidence_tenant_task", "workflow_evidence"),
        ("ix_workflow_approval_history_tenant_approval", "workflow_approval_history"),
        ("ix_workflow_approvals_tenant_approval", "workflow_approvals"),
        ("ix_workflow_approvals_tenant_task", "workflow_approvals"),
        (
            "ix_workflow_verification_history_tenant_task",
            "workflow_verification_history",
        ),
        ("ix_workflow_plan_history_tenant_task", "workflow_plan_history"),
        ("ix_workflow_leases_tenant_task", "workflow_leases"),
    ):
        op.drop_index(name, table_name=table)
    _restore_sequence_index(
        "workflow_artifacts",
        "ix_workflow_artifacts_tenant_task_sequence",
        "ix_workflow_artifacts_task_sequence",
    )
    _restore_sequence_index(
        "workflow_evidence",
        "ix_workflow_evidence_tenant_task_sequence",
        "ix_workflow_evidence_task_sequence",
    )
    _restore_sequence_index(
        "workflow_step_results",
        "ix_workflow_step_results_tenant_task_sequence",
        "ix_workflow_step_results_task_sequence",
    )
    _restore_sequence_index(
        "workflow_tool_results",
        "ix_workflow_tool_results_tenant_task_sequence",
        "ix_workflow_tool_results_task_sequence",
    )
    _restore_sequence_index(
        "workflow_state_events",
        "ix_workflow_state_events_tenant_task_sequence",
        "ix_workflow_state_events_task_sequence",
    )
    op.drop_index("ix_workflow_tasks_tenant_task", table_name="workflow_tasks")
    with op.batch_alter_table("workflow_approval_history") as batch:
        batch.drop_constraint(
            "fk_workflow_approval_history_tenant_approval",
            type_="foreignkey",
        )
    with op.batch_alter_table("workflow_approvals") as batch:
        batch.drop_constraint("fk_workflow_approvals_tenant_task", type_="foreignkey")
    for table in reversed(_TASK_CHILD_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_tenant_task", type_="foreignkey")
    with op.batch_alter_table("workflow_approvals") as batch:
        batch.drop_constraint("uq_workflow_approvals_tenant_approval", type_="unique")
    with op.batch_alter_table("workflow_tasks") as batch:
        batch.drop_constraint("uq_workflow_tasks_tenant_task", type_="unique")
    for table in reversed(_ALL_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("tenant_id")


def _replace_sequence_index(table: str, old: str, new: str) -> None:
    op.drop_index(old, table_name=table)
    op.create_index(new, table, ["tenant_id", "task_id", "sequence_id"])


def _restore_sequence_index(table: str, current: str, restored: str) -> None:
    op.drop_index(current, table_name=table)
    op.create_index(restored, table, ["task_id", "sequence_id"])


def _tenant_from_contract(payload: object) -> str | None:
    try:
        raw = json.loads(str(payload))
        value = raw.get("constraints", {}).get("tenant_id")
        return value if isinstance(value, str) and value else None
    except (TypeError, ValueError, AttributeError):
        return None


def _tenant_from_payload(payload: object) -> str | None:
    try:
        raw = json.loads(str(payload))
        direct = raw.get("tenant_id")
        metadata = raw.get("metadata", {})
        value = direct or (metadata.get("tenant_id") if isinstance(metadata, dict) else None)
        return value if isinstance(value, str) and value else None
    except (TypeError, ValueError, AttributeError):
        return None


def _audit_tenants(connection: sa.Connection) -> dict[str, str]:
    values: dict[str, str] = {}
    rows = connection.execute(
        sa.text("SELECT task_id, payload_json FROM workflow_graph_audit")
    ).mappings()
    for row in rows:
        tenant_id = _tenant_from_payload(row["payload_json"])
        if tenant_id is not None:
            values.setdefault(str(row["task_id"]), tenant_id)
    return values


def _approval_tenants(connection: sa.Connection) -> dict[str, str]:
    values: dict[str, str] = {}
    rows = connection.execute(
        sa.text("SELECT task_id, payload_json FROM workflow_approvals")
    ).mappings()
    for row in rows:
        tenant_id = _tenant_from_payload(row["payload_json"])
        if tenant_id is not None:
            values.setdefault(str(row["task_id"]), tenant_id)
    return values
