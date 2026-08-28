"""Add the frozen asynchronous runtime persistence and evolve workflow leases.

Revision ID: 20260826_0005
Revises: 20260812_0004
Create Date: 2026-08-26
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0005"
down_revision: str | None = "20260812_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISPATCH_STATUSES = (
    "'PENDING','ENQUEUED','ACKNOWLEDGED','RETRY_SCHEDULED','SUPERSEDED','DEAD_LETTERED'"
)


def upgrade() -> None:
    """Create outbox/runtime/idempotency state and replace the legacy lease shape safely."""
    _create_task_dispatches()
    _create_workflow_task_runtime()
    _create_submission_idempotency()
    _create_runtime_attempts()
    _backfill_runtime_rows()
    _replace_legacy_leases()


def downgrade() -> None:
    """Restore the legacy lease shape before removing Stage B runtime tables."""
    connection = op.get_bind()
    op.create_table(
        "workflow_leases_legacy_restore",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_leases_tenant_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )
    connection.execute(
        sa.text(
            "INSERT INTO workflow_leases_legacy_restore "
            "(tenant_id, task_id, owner_id, expires_at) "
            "SELECT tenant_id, task_id, worker_id, expires_at FROM workflow_leases"
        )
    )
    op.drop_index("ix_workflow_leases_tenant_task", table_name="workflow_leases")
    op.drop_table("workflow_leases")
    op.rename_table("workflow_leases_legacy_restore", "workflow_leases")
    op.create_index(
        "ix_workflow_leases_tenant_task",
        "workflow_leases",
        ["tenant_id", "task_id"],
    )
    op.drop_index("ix_task_runtime_attempts_tenant_task", table_name="task_runtime_attempts")
    op.drop_table("task_runtime_attempts")
    op.drop_table("task_submission_idempotency")
    op.drop_index("ix_workflow_task_runtime_recovery", table_name="workflow_task_runtime")
    op.drop_table("workflow_task_runtime")
    op.drop_index("ix_task_dispatches_due", table_name="task_dispatches")
    op.drop_table("task_dispatches")


def _create_task_dispatches() -> None:
    op.create_table(
        "task_dispatches",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("dispatch_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("execution_generation", sa.BigInteger(), nullable=False),
        sa.Column("predecessor_execution_generation", sa.BigInteger(), nullable=True),
        sa.Column("resume_checkpoint_id", sa.String(length=200), nullable=True),
        sa.Column("expected_task_version", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("execution_generation >= 1", name="ck_task_dispatch_generation"),
        sa.CheckConstraint("expected_task_version >= 1", name="ck_task_dispatch_version"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_task_dispatch_attempt_count"),
        sa.CheckConstraint(
            f"status IN ({_DISPATCH_STATUSES})",
            name="ck_task_dispatch_status",
        ),
        sa.CheckConstraint(
            "(predecessor_execution_generation IS NULL AND resume_checkpoint_id IS NULL) "
            "OR (predecessor_execution_generation = execution_generation - 1 "
            "AND resume_checkpoint_id IS NOT NULL)",
            name="ck_task_dispatch_resume_binding",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_task_dispatches_tenant_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "dispatch_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "execution_generation",
            name="uq_task_dispatches_tenant_task_generation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "dispatch_id",
            name="uq_task_dispatches_tenant_task_dispatch",
        ),
    )
    op.create_index(
        "ix_task_dispatches_due",
        "task_dispatches",
        ["status", "available_at", "tenant_id"],
    )


def _create_workflow_task_runtime() -> None:
    op.create_table(
        "workflow_task_runtime",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("runtime_status", sa.String(length=32), nullable=False),
        sa.Column("execution_generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("predecessor_execution_generation", sa.BigInteger(), nullable=True),
        sa.Column("resume_checkpoint_id", sa.String(length=200), nullable=True),
        sa.Column("current_dispatch_id", sa.String(length=200), nullable=True),
        sa.Column("fencing_counter", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recovery_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_recovery_error", sa.String(length=200), nullable=True),
        sa.Column("cancellation_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "runtime_status IN ('READY','LEASED','WAITING_RETRY','SUSPENDED','FINISHED')",
            name="ck_workflow_task_runtime_status",
        ),
        sa.CheckConstraint("execution_generation >= 1", name="ck_workflow_task_runtime_generation"),
        sa.CheckConstraint("fencing_counter >= 0", name="ck_workflow_task_runtime_fencing"),
        sa.CheckConstraint(
            "recovery_attempt_count >= 0", name="ck_workflow_task_runtime_recovery_count"
        ),
        sa.CheckConstraint(
            "(runtime_status = 'WAITING_RETRY' AND retry_not_before IS NOT NULL) "
            "OR (runtime_status <> 'WAITING_RETRY' AND retry_not_before IS NULL)",
            name="ck_workflow_task_runtime_retry_binding",
        ),
        sa.CheckConstraint(
            "(predecessor_execution_generation IS NULL AND resume_checkpoint_id IS NULL) "
            "OR (predecessor_execution_generation = execution_generation - 1 "
            "AND resume_checkpoint_id IS NOT NULL)",
            name="ck_workflow_task_runtime_resume_binding",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_task_runtime_tenant_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "task_id"),
    )
    op.create_index(
        "ix_workflow_task_runtime_recovery",
        "workflow_task_runtime",
        ["runtime_status", "retry_not_before"],
    )


def _create_submission_idempotency() -> None:
    op.create_table(
        "task_submission_idempotency",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("caller_id", sa.String(length=200), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_task_submission_idempotency_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_task_submission_idempotency_tenant_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "caller_id", "idempotency_key"),
    )


def _create_runtime_attempts() -> None:
    op.create_table(
        "task_runtime_attempts",
        sa.Column("sequence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("dispatch_id", sa.String(length=200), nullable=False),
        sa.Column("execution_generation", sa.BigInteger(), nullable=False),
        sa.Column("runtime_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=200), nullable=True),
        sa.CheckConstraint("execution_generation >= 1", name="ck_task_runtime_attempt_generation"),
        sa.CheckConstraint("runtime_attempt >= 1", name="ck_task_runtime_attempt_number"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','SUSPENDED','FAILED','LOST')",
            name="ck_task_runtime_attempt_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id", "dispatch_id"],
            [
                "task_dispatches.tenant_id",
                "task_dispatches.task_id",
                "task_dispatches.dispatch_id",
            ],
            name="fk_task_runtime_attempts_tenant_task_dispatch",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sequence_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "execution_generation",
            "runtime_attempt",
            name="uq_task_runtime_attempt_identity",
        ),
    )
    op.create_index(
        "ix_task_runtime_attempts_tenant_task",
        "task_runtime_attempts",
        ["tenant_id", "task_id"],
    )


def _backfill_runtime_rows() -> None:
    connection = op.get_bind()
    now = _database_now(connection)
    rows = connection.execute(
        sa.text("SELECT tenant_id, task_id, state_json, task_result_json FROM workflow_tasks")
    ).mappings()
    for row in rows:
        state = _state_payload(str(row["state_json"]))
        task_status = str(state.get("state", "CREATED"))
        if row["task_result_json"] is not None or task_status in {
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }:
            runtime_status = "FINISHED"
        elif task_status == "WAITING_APPROVAL":
            runtime_status = "SUSPENDED"
        else:
            runtime_status = "READY"
        connection.execute(
            sa.text(
                "INSERT INTO workflow_task_runtime "
                "(tenant_id, task_id, runtime_status, execution_generation, fencing_counter, "
                "recovery_attempt_count, created_at, updated_at) "
                "VALUES (:tenant_id, :task_id, :runtime_status, 1, 0, 0, :now, :now)"
            ),
            {
                "tenant_id": row["tenant_id"],
                "task_id": row["task_id"],
                "runtime_status": runtime_status,
                "now": now,
            },
        )


def _replace_legacy_leases() -> None:
    connection = op.get_bind()
    legacy_rows = tuple(
        connection.execute(
            sa.text("SELECT tenant_id, task_id, owner_id, expires_at FROM workflow_leases")
        ).mappings()
    )
    op.drop_index("ix_workflow_leases_tenant_task", table_name="workflow_leases")
    op.rename_table("workflow_leases", "workflow_leases_stage_b_legacy")
    _create_runtime_lease_table()

    for row in legacy_rows:
        task = (
            connection.execute(
                sa.text(
                    "SELECT request_json, state_json FROM workflow_tasks "
                    "WHERE tenant_id = :tenant_id AND task_id = :task_id"
                ),
                {"tenant_id": row["tenant_id"], "task_id": row["task_id"]},
            )
            .mappings()
            .one()
        )
        state = _state_payload(str(task["state_json"]))
        request = _state_payload(str(task["request_json"]))
        task_version = max(1, int(state.get("version", 1)))
        expires_at = _as_utc(row["expires_at"])
        acquired_at = expires_at - timedelta(minutes=10)
        suffix = hashlib.sha256(f"{row['tenant_id']}:{row['task_id']}".encode()).hexdigest()[:24]
        dispatch_id = f"D-MIGRATED-{suffix}"
        lease_id = f"L-MIGRATED-{suffix}"
        trace_id = str(request.get("id") or f"TRACE-MIGRATED-{suffix}")
        connection.execute(
            sa.text(
                "INSERT INTO task_dispatches "
                "(tenant_id, dispatch_id, task_id, execution_generation, "
                "expected_task_version, trace_id, status, available_at, attempt_count, "
                "created_at, updated_at) "
                "VALUES (:tenant_id, :dispatch_id, :task_id, 1, :task_version, :trace_id, "
                "'ENQUEUED', :acquired_at, 1, :acquired_at, :acquired_at)"
            ),
            {
                "tenant_id": row["tenant_id"],
                "dispatch_id": dispatch_id,
                "task_id": row["task_id"],
                "task_version": task_version,
                "trace_id": trace_id,
                "acquired_at": acquired_at,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE workflow_task_runtime SET runtime_status = 'LEASED', "
                "current_dispatch_id = :dispatch_id, fencing_counter = 1, updated_at = :now "
                "WHERE tenant_id = :tenant_id AND task_id = :task_id"
            ),
            {
                "tenant_id": row["tenant_id"],
                "task_id": row["task_id"],
                "dispatch_id": dispatch_id,
                "now": acquired_at,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO workflow_leases "
                "(tenant_id, task_id, dispatch_id, execution_generation, task_version, "
                "worker_id, lease_id, fencing_token, acquired_at, heartbeat_at, expires_at) "
                "VALUES (:tenant_id, :task_id, :dispatch_id, 1, :task_version, :worker_id, "
                ":lease_id, 1, :acquired_at, :acquired_at, :expires_at)"
            ),
            {
                "tenant_id": row["tenant_id"],
                "task_id": row["task_id"],
                "dispatch_id": dispatch_id,
                "task_version": task_version,
                "worker_id": row["owner_id"],
                "lease_id": lease_id,
                "acquired_at": acquired_at,
                "expires_at": expires_at,
            },
        )
    op.drop_table("workflow_leases_stage_b_legacy")


def _create_runtime_lease_table() -> None:
    op.create_table(
        "workflow_leases",
        sa.Column("tenant_id", sa.String(length=200), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=False),
        sa.Column("dispatch_id", sa.String(length=200), nullable=False),
        sa.Column("execution_generation", sa.BigInteger(), nullable=False),
        sa.Column("task_version", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(length=200), nullable=False),
        sa.Column("lease_id", sa.String(length=200), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("execution_generation >= 1", name="ck_workflow_lease_generation"),
        sa.CheckConstraint("task_version >= 1", name="ck_workflow_lease_task_version"),
        sa.CheckConstraint("fencing_token > 0", name="ck_workflow_lease_fencing"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["workflow_tasks.tenant_id", "workflow_tasks.task_id"],
            name="fk_workflow_leases_tenant_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id", "dispatch_id"],
            [
                "task_dispatches.tenant_id",
                "task_dispatches.task_id",
                "task_dispatches.dispatch_id",
            ],
            name="fk_workflow_leases_tenant_task_dispatch",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "task_id"),
        sa.UniqueConstraint("tenant_id", "lease_id", name="uq_workflow_leases_tenant_lease"),
    )
    op.create_index(
        "ix_workflow_leases_tenant_task",
        "workflow_leases",
        ["tenant_id", "task_id"],
    )


def _database_now(connection: sa.engine.Connection) -> datetime:
    return _as_utc(connection.execute(sa.select(sa.func.now())).scalar_one())


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _state_payload(payload: str) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
