"""Scope Workflow StepResult identity to its tenant and task.

Revision ID: 20260812_0004
Revises: 20260809_0003
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0004"
down_revision: str | None = "20260809_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_step_results"
_GLOBAL_COLUMNS = ("step_id",)
_SCOPED_COLUMNS = ("tenant_id", "task_id", "step_id")
_SCOPED_CONSTRAINT = "uq_workflow_step_results_tenant_task_step"
_SQLITE_NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    """Replace global step uniqueness with the frozen tenant/task-scoped identity."""
    connection = op.get_bind()
    _fail_if_duplicates(
        connection,
        _SCOPED_COLUMNS,
        "cannot scope Workflow StepResult identity: duplicate tenant/task/step rows exist",
    )
    global_constraint = _unique_constraint_name(connection, _GLOBAL_COLUMNS)
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(
            _TABLE,
            recreate="always",
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch:
            batch.drop_constraint(
                global_constraint or "uq_workflow_step_results_step_id",
                type_="unique",
            )
            batch.create_unique_constraint(_SCOPED_CONSTRAINT, list(_SCOPED_COLUMNS))
        return
    if global_constraint is None:
        raise RuntimeError("global Workflow StepResult step_id constraint was not found")
    op.drop_constraint(global_constraint, _TABLE, type_="unique")
    op.create_unique_constraint(_SCOPED_CONSTRAINT, _TABLE, list(_SCOPED_COLUMNS))


def downgrade() -> None:
    """Restore global step uniqueness only when all persisted rows remain compatible."""
    connection = op.get_bind()
    _fail_if_duplicates(
        connection,
        _GLOBAL_COLUMNS,
        "cannot restore global Workflow StepResult identity: step_id is reused across tasks",
    )
    scoped_constraint = _unique_constraint_name(connection, _SCOPED_COLUMNS)
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(
            _TABLE,
            recreate="always",
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch:
            batch.drop_constraint(scoped_constraint or _SCOPED_CONSTRAINT, type_="unique")
            batch.create_unique_constraint(
                "uq_workflow_step_results_step_id",
                list(_GLOBAL_COLUMNS),
            )
        return
    if scoped_constraint is None:
        raise RuntimeError("scoped Workflow StepResult identity constraint was not found")
    op.drop_constraint(scoped_constraint, _TABLE, type_="unique")
    op.create_unique_constraint(None, _TABLE, list(_GLOBAL_COLUMNS))


def _fail_if_duplicates(
    connection: sa.engine.Connection,
    columns: tuple[str, ...],
    message: str,
) -> None:
    group = ", ".join(columns)
    duplicate = connection.execute(
        sa.text(f"SELECT 1 FROM {_TABLE} GROUP BY {group} HAVING COUNT(*) > 1 LIMIT 1")
    ).first()
    if duplicate is not None:
        raise RuntimeError(message)


def _unique_constraint_name(
    connection: sa.engine.Connection,
    columns: tuple[str, ...],
) -> str | None:
    for constraint in sa.inspect(connection).get_unique_constraints(_TABLE):
        if tuple(constraint.get("column_names") or ()) == columns:
            name = constraint.get("name")
            return str(name) if name else None
    return None
