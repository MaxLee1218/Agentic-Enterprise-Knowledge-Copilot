"""Establish or adopt the Supplier Quality business-schema baseline.

Revision ID: 20260811_b001
Revises: None
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_b001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASELINE_TABLES = {
    "suppliers",
    "supplier_deviations",
    "incoming_inspections",
    "corrective_actions",
}


def upgrade() -> None:
    """Create a fresh Quality baseline or adopt the exact existing table set."""
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    present = existing.intersection(_BASELINE_TABLES)
    if present:
        if present != _BASELINE_TABLES:
            missing = sorted(_BASELINE_TABLES - present)
            raise RuntimeError(f"Business schema baseline is partial; missing tables: {missing}")
        _validate_existing_baseline()
        return

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("supplier_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_suppliers_risk_level",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "supplier_code", name="uq_supplier_tenant_code"),
    )
    op.create_index("ix_suppliers_tenant_risk", "suppliers", ["tenant_id", "risk_level"])
    op.create_table(
        "supplier_deviations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("deviation_date", sa.Date(), nullable=False),
        sa.Column("deviation_type", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("defect_quantity", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("defect_quantity >= 0", name="ck_deviation_defect_quantity"),
        sa.CheckConstraint(
            "severity IN ('MINOR', 'MAJOR', 'CRITICAL')",
            name="ck_deviation_severity",
        ),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_supplier_deviations_supplier_date",
        "supplier_deviations",
        ["supplier_id", "deviation_date"],
    )
    op.create_table(
        "incoming_inspections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("inspection_date", sa.Date(), nullable=False),
        sa.Column("total_quantity", sa.Integer(), nullable=False),
        sa.Column("accepted_quantity", sa.Integer(), nullable=False),
        sa.Column("rejected_quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total_quantity >= 0", name="ck_inspection_total_quantity"),
        sa.CheckConstraint("accepted_quantity >= 0", name="ck_inspection_accepted_quantity"),
        sa.CheckConstraint("rejected_quantity >= 0", name="ck_inspection_rejected_quantity"),
        sa.CheckConstraint(
            "accepted_quantity + rejected_quantity = total_quantity",
            name="ck_inspection_quantity_balance",
        ),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incoming_inspections_supplier_date",
        "incoming_inspections",
        ["supplier_id", "inspection_date"],
    )
    op.create_table(
        "corrective_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("opened_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("closed_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')",
            name="ck_corrective_action_status",
        ),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_corrective_actions_supplier_status",
        "corrective_actions",
        ["supplier_id", "status"],
    )
    op.create_index("ix_corrective_actions_due_date", "corrective_actions", ["due_date"])


def downgrade() -> None:
    """Drop a fresh isolated baseline; production use requires explicit data review."""
    op.drop_index("ix_corrective_actions_due_date", table_name="corrective_actions")
    op.drop_index("ix_corrective_actions_supplier_status", table_name="corrective_actions")
    op.drop_table("corrective_actions")
    op.drop_index("ix_incoming_inspections_supplier_date", table_name="incoming_inspections")
    op.drop_table("incoming_inspections")
    op.drop_index("ix_supplier_deviations_supplier_date", table_name="supplier_deviations")
    op.drop_table("supplier_deviations")
    op.drop_index("ix_suppliers_tenant_risk", table_name="suppliers")
    op.drop_table("suppliers")


def _validate_existing_baseline() -> None:
    inspector = sa.inspect(op.get_bind())
    required_supplier_columns = {
        "id",
        "tenant_id",
        "supplier_code",
        "name",
        "country",
        "category",
        "risk_level",
        "created_at",
    }
    actual = {column["name"] for column in inspector.get_columns("suppliers")}
    if actual != required_supplier_columns:
        raise RuntimeError("Existing suppliers table does not match the Quality baseline")
    null_tenant_count = (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM suppliers WHERE tenant_id IS NULL OR tenant_id = ''")
        )
        .scalar_one()
    )
    if int(null_tenant_count) != 0:
        raise RuntimeError("Existing supplier rows contain an invalid tenant key")
