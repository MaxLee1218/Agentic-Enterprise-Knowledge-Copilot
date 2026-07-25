"""SQLAlchemy persistence models for the deterministic enterprise demo database."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from copilot.contracts.validators import utc_now


class Base(DeclarativeBase):
    """Declarative metadata root kept inside the database adapter boundary."""


class Supplier(Base):
    """Supplier master data; ORM instances never cross the adapter boundary."""

    __tablename__ = "suppliers"
    __table_args__ = (
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


__all__ = [
    "Base",
    "CorrectiveAction",
    "IncomingInspection",
    "Supplier",
    "SupplierDeviation",
]
