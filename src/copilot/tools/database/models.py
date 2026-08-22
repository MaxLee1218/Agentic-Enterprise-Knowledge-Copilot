"""SQLAlchemy persistence models for the deterministic enterprise demo database."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from copilot.contracts.validators import utc_now


class Base(DeclarativeBase):
    """Declarative metadata root kept inside the database adapter boundary."""


_BUSINESS_ID = BigInteger().with_variant(Integer, "sqlite")


class Supplier(Base):
    """Supplier master data; ORM instances never cross the adapter boundary."""

    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_suppliers_tenant_id"),
        UniqueConstraint("tenant_id", "supplier_code", name="uq_supplier_tenant_code"),
        Index("ix_suppliers_tenant_risk", "tenant_id", "risk_level"),
        CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_suppliers_risk_level",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    deviations: Mapped[list[SupplierDeviation]] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
    )
    incoming_inspections: Mapped[list[IncomingInspection]] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
    )
    corrective_actions: Mapped[list[CorrectiveAction]] = relationship(
        back_populates="supplier",
        cascade="all, delete-orphan",
    )


class SupplierDeviation(Base):
    """Quality deviation facts associated with one supplier."""

    __tablename__ = "supplier_deviations"
    __table_args__ = (
        Index("ix_supplier_deviations_supplier_date", "supplier_id", "deviation_date"),
        CheckConstraint("defect_quantity >= 0", name="ck_deviation_defect_quantity"),
        CheckConstraint(
            "severity IN ('MINOR', 'MAJOR', 'CRITICAL')",
            name="ck_deviation_severity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    deviation_date: Mapped[date] = mapped_column(Date, nullable=False)
    deviation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    defect_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    supplier: Mapped[Supplier] = relationship(back_populates="deviations")


class IncomingInspection(Base):
    """Incoming inspection quantities used by approved quality templates."""

    __tablename__ = "incoming_inspections"
    __table_args__ = (
        Index("ix_incoming_inspections_supplier_date", "supplier_id", "inspection_date"),
        CheckConstraint("total_quantity >= 0", name="ck_inspection_total_quantity"),
        CheckConstraint("accepted_quantity >= 0", name="ck_inspection_accepted_quantity"),
        CheckConstraint("rejected_quantity >= 0", name="ck_inspection_rejected_quantity"),
        CheckConstraint(
            "accepted_quantity + rejected_quantity = total_quantity",
            name="ck_inspection_quantity_balance",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    inspection_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    supplier: Mapped[Supplier] = relationship(back_populates="incoming_inspections")


class CorrectiveAction(Base):
    """Supplier corrective-action tracking data."""

    __tablename__ = "corrective_actions"
    __table_args__ = (
        Index("ix_corrective_actions_supplier_status", "supplier_id", "status"),
        Index("ix_corrective_actions_due_date", "due_date"),
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')",
            name="ck_corrective_action_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    opened_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    closed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    supplier: Mapped[Supplier] = relationship(back_populates="corrective_actions")


class LegalEntity(Base):
    """Tenant-owned legal entity used as an AP authorization dimension."""

    __tablename__ = "legal_entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_legal_entities_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "legal_entity_code",
            name="uq_legal_entities_tenant_code",
        ),
        Index("ix_legal_entities_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="ck_legal_entities_status",
        ),
        CheckConstraint(
            "length(base_currency) = 3 AND base_currency = upper(base_currency)",
            name="ck_legal_entities_currency",
        ),
    )

    id: Mapped[int] = mapped_column(_BUSINESS_ID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    legal_entity_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class BusinessUnit(Base):
    """Tenant and legal-entity-scoped AP business unit."""

    __tablename__ = "business_units"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_business_units_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "legal_entity_id",
            "id",
            name="uq_business_units_tenant_entity_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "legal_entity_id",
            "business_unit_code",
            name="uq_business_units_tenant_entity_code",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "legal_entity_id"),
            ("legal_entities.tenant_id", "legal_entities.id"),
            name="fk_business_units_tenant_entity",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_business_units_tenant_entity_status",
            "tenant_id",
            "legal_entity_id",
            "status",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')",
            name="ck_business_units_status",
        ),
    )

    id: Mapped[int] = mapped_column(_BUSINESS_ID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    legal_entity_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    business_unit_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PurchaseOrder(Base):
    """Read-only AP purchase-order header fact for v1 single-invoice matching."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_purchase_orders_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_purchase_orders_tenant_source",
        ),
        UniqueConstraint(
            "tenant_id",
            "legal_entity_id",
            "po_number",
            name="uq_purchase_orders_tenant_entity_number",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "supplier_id"),
            ("suppliers.tenant_id", "suppliers.id"),
            name="fk_purchase_orders_tenant_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "legal_entity_id"),
            ("legal_entities.tenant_id", "legal_entities.id"),
            name="fk_purchase_orders_tenant_entity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "legal_entity_id", "business_unit_id"),
            (
                "business_units.tenant_id",
                "business_units.legal_entity_id",
                "business_units.id",
            ),
            name="fk_purchase_orders_tenant_entity_unit",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_purchase_orders_tenant_supplier_date",
            "tenant_id",
            "supplier_id",
            "order_date",
        ),
        Index(
            "ix_purchase_orders_tenant_entity_unit_date",
            "tenant_id",
            "legal_entity_id",
            "business_unit_id",
            "order_date",
        ),
        Index("ix_purchase_orders_tenant_number", "tenant_id", "po_number"),
        CheckConstraint("approved_amount >= 0", name="ck_purchase_orders_amount"),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_purchase_orders_currency",
        ),
        CheckConstraint(
            "matching_basis IN ('SINGLE_INVOICE', 'MULTI_INVOICE')",
            name="ck_purchase_orders_matching_basis",
        ),
        CheckConstraint(
            "status IN ('APPROVED', 'CLOSED', 'CANCELLED')",
            name="ck_purchase_orders_status",
        ),
        CheckConstraint(
            "status = 'CANCELLED' OR approved_at IS NOT NULL",
            name="ck_purchase_orders_approved_at",
        ),
    )

    id: Mapped[int] = mapped_column(_BUSINESS_ID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    po_number: Mapped[str] = mapped_column(String(128), nullable=False)
    supplier_id: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_entity_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    business_unit_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    approved_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    matching_basis: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Invoice(Base):
    """Tenant-scoped AP invoice fact retaining exact duplicate business conditions."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_invoices_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_invoices_tenant_source",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "supplier_id"),
            ("suppliers.tenant_id", "suppliers.id"),
            name="fk_invoices_tenant_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "legal_entity_id"),
            ("legal_entities.tenant_id", "legal_entities.id"),
            name="fk_invoices_tenant_entity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "legal_entity_id", "business_unit_id"),
            (
                "business_units.tenant_id",
                "business_units.legal_entity_id",
                "business_units.id",
            ),
            name="fk_invoices_tenant_entity_unit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "purchase_order_id"),
            ("purchase_orders.tenant_id", "purchase_orders.id"),
            name="fk_invoices_tenant_purchase_order",
            ondelete="RESTRICT",
        ),
        Index("ix_invoices_tenant_date", "tenant_id", "invoice_date"),
        Index(
            "ix_invoices_tenant_supplier_date",
            "tenant_id",
            "supplier_id",
            "invoice_date",
        ),
        Index(
            "ix_invoices_tenant_entity_unit_date",
            "tenant_id",
            "legal_entity_id",
            "business_unit_id",
            "invoice_date",
        ),
        Index(
            "ix_invoices_duplicate_key",
            "tenant_id",
            "supplier_id",
            "normalized_invoice_number",
            "invoice_date",
            "currency",
            "gross_amount",
        ),
        Index("ix_invoices_tenant_purchase_order", "tenant_id", "purchase_order_id"),
        CheckConstraint(
            "invoice_type IN ('STANDARD', 'CREDIT')",
            name="ck_invoices_type",
        ),
        CheckConstraint(
            "status IN ('POSTED', 'PAID', 'VOID')",
            name="ck_invoices_status",
        ),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_invoices_currency",
        ),
        CheckConstraint(
            "net_amount >= 0 AND tax_amount >= 0 AND gross_amount >= 0",
            name="ck_invoices_amounts_nonnegative",
        ),
        CheckConstraint(
            "net_amount + tax_amount = gross_amount",
            name="ck_invoices_amount_balance",
        ),
        CheckConstraint(
            "payment_terms_days BETWEEN 0 AND 365",
            name="ck_invoices_payment_terms",
        ),
        CheckConstraint(
            "length(normalized_invoice_number) > 0",
            name="ck_invoices_normalized_number",
        ),
        CheckConstraint(
            "no_po_exception_approved = false OR "
            "(no_po_exception_ref IS NOT NULL AND length(trim(no_po_exception_ref)) > 0)",
            name="ck_invoices_no_po_exception",
        ),
    )

    id: Mapped[int] = mapped_column(_BUSINESS_ID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    supplier_id: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_entity_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    business_unit_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_invoice_number: Mapped[str] = mapped_column(String(128), nullable=False)
    invoice_type: Mapped[str] = mapped_column(String(16), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    purchase_order_id: Mapped[int | None] = mapped_column(_BUSINESS_ID, nullable=True)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    no_po_exception_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    no_po_exception_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Payment(Base):
    """AP payment fact without bank or payment-reference data."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_payments_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_record_id",
            name="uq_payments_tenant_source",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "invoice_id"),
            ("invoices.tenant_id", "invoices.id"),
            name="fk_payments_tenant_invoice",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "legal_entity_id"),
            ("legal_entities.tenant_id", "legal_entities.id"),
            name="fk_payments_tenant_entity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "legal_entity_id", "business_unit_id"),
            (
                "business_units.tenant_id",
                "business_units.legal_entity_id",
                "business_units.id",
            ),
            name="fk_payments_tenant_entity_unit",
            ondelete="RESTRICT",
        ),
        Index("ix_payments_tenant_invoice_status", "tenant_id", "invoice_id", "status"),
        Index("ix_payments_tenant_date", "tenant_id", "payment_date"),
        Index(
            "ix_payments_tenant_entity_unit_date",
            "tenant_id",
            "legal_entity_id",
            "business_unit_id",
            "payment_date",
        ),
        CheckConstraint("payment_amount > 0", name="ck_payments_amount"),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_payments_currency",
        ),
        CheckConstraint(
            "status IN ('SETTLED', 'VOID', 'REVERSED')",
            name="ck_payments_status",
        ),
    )

    id: Mapped[int] = mapped_column(_BUSINESS_ID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    invoice_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    legal_entity_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    business_unit_id: Mapped[int] = mapped_column(_BUSINESS_ID, nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


__all__ = [
    "Base",
    "BusinessUnit",
    "CorrectiveAction",
    "IncomingInspection",
    "Invoice",
    "LegalEntity",
    "Payment",
    "PurchaseOrder",
    "Supplier",
    "SupplierDeviation",
]
