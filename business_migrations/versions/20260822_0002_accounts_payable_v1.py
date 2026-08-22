"""Add the tenant-scoped Accounts Payable v1 fact schema.

Revision ID: 20260822_b002
Revises: 20260811_b001
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_b002"
down_revision: str | None = "20260811_b001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preflight the existing supplier master and add AP tables in FK order."""
    _preflight_suppliers()
    with op.batch_alter_table("suppliers") as batch:
        batch.create_unique_constraint("uq_suppliers_tenant_id", ["tenant_id", "id"])

    op.create_table(
        "legal_entities",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("legal_entity_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(base_currency) = 3 AND base_currency = upper(base_currency)",
            name="ck_legal_entities_currency",
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_legal_entities_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_legal_entities_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "legal_entity_code",
            name="uq_legal_entities_tenant_code",
        ),
    )
    op.create_index(
        "ix_legal_entities_tenant_status",
        "legal_entities",
        ["tenant_id", "status"],
    )
    op.create_table(
        "business_units",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("legal_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("business_unit_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_business_units_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legal_entity_id"],
            ["legal_entities.tenant_id", "legal_entities.id"],
            name="fk_business_units_tenant_entity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_business_units_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "legal_entity_id",
            "id",
            name="uq_business_units_tenant_entity_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "legal_entity_id",
            "business_unit_code",
            name="uq_business_units_tenant_entity_code",
        ),
    )
    op.create_index(
        "ix_business_units_tenant_entity_status",
        "business_units",
        ["tenant_id", "legal_entity_id", "status"],
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("po_number", sa.String(length=128), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("legal_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("business_unit_id", sa.BigInteger(), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("approved_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("matching_basis", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("approved_amount >= 0", name="ck_purchase_orders_amount"),
        sa.CheckConstraint(
            "status = 'CANCELLED' OR approved_at IS NOT NULL",
            name="ck_purchase_orders_approved_at",
        ),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_purchase_orders_currency",
        ),
        sa.CheckConstraint(
            "matching_basis IN ('SINGLE_INVOICE', 'MULTI_INVOICE')",
            name="ck_purchase_orders_matching_basis",
        ),
        sa.CheckConstraint(
            "status IN ('APPROVED', 'CLOSED', 'CANCELLED')",
            name="ck_purchase_orders_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legal_entity_id"],
            ["legal_entities.tenant_id", "legal_entities.id"],
            name="fk_purchase_orders_tenant_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legal_entity_id", "business_unit_id"],
            [
                "business_units.tenant_id",
                "business_units.legal_entity_id",
                "business_units.id",
            ],
            name="fk_purchase_orders_tenant_entity_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.id"],
            name="fk_purchase_orders_tenant_supplier",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_purchase_orders_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "legal_entity_id",
            "po_number",
            name="uq_purchase_orders_tenant_entity_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_purchase_orders_tenant_source",
        ),
    )
    op.create_index(
        "ix_purchase_orders_tenant_entity_unit_date",
        "purchase_orders",
        ["tenant_id", "legal_entity_id", "business_unit_id", "order_date"],
    )
    op.create_index(
        "ix_purchase_orders_tenant_number",
        "purchase_orders",
        ["tenant_id", "po_number"],
    )
    op.create_index(
        "ix_purchase_orders_tenant_supplier_date",
        "purchase_orders",
        ["tenant_id", "supplier_id", "order_date"],
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("legal_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("business_unit_id", sa.BigInteger(), nullable=False),
        sa.Column("invoice_number", sa.String(length=128), nullable=False),
        sa.Column("normalized_invoice_number", sa.String(length=128), nullable=False),
        sa.Column("invoice_type", sa.String(length=16), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("net_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("gross_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("no_po_exception_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "no_po_exception_approved",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "net_amount + tax_amount = gross_amount",
            name="ck_invoices_amount_balance",
        ),
        sa.CheckConstraint(
            "net_amount >= 0 AND tax_amount >= 0 AND gross_amount >= 0",
            name="ck_invoices_amounts_nonnegative",
        ),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_invoices_currency",
        ),
        sa.CheckConstraint(
            "no_po_exception_approved = false OR "
            "(no_po_exception_ref IS NOT NULL AND length(trim(no_po_exception_ref)) > 0)",
            name="ck_invoices_no_po_exception",
        ),
        sa.CheckConstraint(
            "length(normalized_invoice_number) > 0",
            name="ck_invoices_normalized_number",
        ),
        sa.CheckConstraint(
            "payment_terms_days BETWEEN 0 AND 365",
            name="ck_invoices_payment_terms",
        ),
        sa.CheckConstraint("status IN ('POSTED', 'PAID', 'VOID')", name="ck_invoices_status"),
        sa.CheckConstraint("invoice_type IN ('STANDARD', 'CREDIT')", name="ck_invoices_type"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legal_entity_id"],
            ["legal_entities.tenant_id", "legal_entities.id"],
            name="fk_invoices_tenant_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legal_entity_id", "business_unit_id"],
            [
                "business_units.tenant_id",
                "business_units.legal_entity_id",
                "business_units.id",
            ],
            name="fk_invoices_tenant_entity_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "purchase_order_id"],
            ["purchase_orders.tenant_id", "purchase_orders.id"],
            name="fk_invoices_tenant_purchase_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.id"],
            name="fk_invoices_tenant_supplier",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_invoices_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_invoices_tenant_source",
        ),
    )
    op.create_index(
        "ix_invoices_duplicate_key",
        "invoices",
        [
            "tenant_id",
            "supplier_id",
            "normalized_invoice_number",
            "invoice_date",
            "currency",
            "gross_amount",
        ],
    )
    op.create_index("ix_invoices_tenant_date", "invoices", ["tenant_id", "invoice_date"])
    op.create_index(
        "ix_invoices_tenant_entity_unit_date",
        "invoices",
        ["tenant_id", "legal_entity_id", "business_unit_id", "invoice_date"],
    )
    op.create_index(
        "ix_invoices_tenant_purchase_order",
        "invoices",
        ["tenant_id", "purchase_order_id"],
    )
    op.create_index(
        "ix_invoices_tenant_supplier_date",
        "invoices",
        ["tenant_id", "supplier_id", "invoice_date"],
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("legal_entity_id", sa.BigInteger(), nullable=False),
        sa.Column("business_unit_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("payment_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("payment_amount > 0", name="ck_payments_amount"),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_payments_currency",
        ),
        sa.CheckConstraint("status IN ('SETTLED', 'VOID', 'REVERSED')", name="ck_payments_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legal_entity_id"],
            ["legal_entities.tenant_id", "legal_entities.id"],
            name="fk_payments_tenant_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legal_entity_id", "business_unit_id"],
            [
                "business_units.tenant_id",
                "business_units.legal_entity_id",
                "business_units.id",
            ],
            name="fk_payments_tenant_entity_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            ["invoices.tenant_id", "invoices.id"],
            name="fk_payments_tenant_invoice",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_payments_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_payments_tenant_source",
        ),
    )
    op.create_index("ix_payments_tenant_date", "payments", ["tenant_id", "payment_date"])
    op.create_index(
        "ix_payments_tenant_entity_unit_date",
        "payments",
        ["tenant_id", "legal_entity_id", "business_unit_id", "payment_date"],
    )
    op.create_index(
        "ix_payments_tenant_invoice_status",
        "payments",
        ["tenant_id", "invoice_id", "status"],
    )


def downgrade() -> None:
    """Remove only AP v1 objects; Quality baseline rows and tables remain."""
    op.drop_index("ix_payments_tenant_invoice_status", table_name="payments")
    op.drop_index("ix_payments_tenant_entity_unit_date", table_name="payments")
    op.drop_index("ix_payments_tenant_date", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_invoices_tenant_supplier_date", table_name="invoices")
    op.drop_index("ix_invoices_tenant_purchase_order", table_name="invoices")
    op.drop_index("ix_invoices_tenant_entity_unit_date", table_name="invoices")
    op.drop_index("ix_invoices_tenant_date", table_name="invoices")
    op.drop_index("ix_invoices_duplicate_key", table_name="invoices")
    op.drop_table("invoices")
    op.drop_index("ix_purchase_orders_tenant_supplier_date", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_tenant_number", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_tenant_entity_unit_date", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index("ix_business_units_tenant_entity_status", table_name="business_units")
    op.drop_table("business_units")
    op.drop_index("ix_legal_entities_tenant_status", table_name="legal_entities")
    op.drop_table("legal_entities")
    with op.batch_alter_table("suppliers") as batch:
        batch.drop_constraint("uq_suppliers_tenant_id", type_="unique")


def _preflight_suppliers() -> None:
    bind = op.get_bind()
    if "suppliers" not in set(sa.inspect(bind).get_table_names()):
        raise RuntimeError("Supplier Quality baseline must exist before AP migration")
    invalid_tenants = bind.execute(
        sa.text("SELECT count(*) FROM suppliers WHERE tenant_id IS NULL OR tenant_id = ''")
    ).scalar_one()
    if int(invalid_tenants) != 0:
        raise RuntimeError("Supplier master contains invalid tenant keys")
    for child_table in (
        "supplier_deviations",
        "incoming_inspections",
        "corrective_actions",
    ):
        orphan_count = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {child_table} child "  # noqa: S608
                "LEFT JOIN suppliers supplier ON supplier.id = child.supplier_id "
                "WHERE supplier.id IS NULL"
            )
        ).scalar_one()
        if int(orphan_count) != 0:
            raise RuntimeError(f"Supplier Quality baseline contains orphan rows in {child_table}")
