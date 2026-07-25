"""Deterministic, repeatable demo dataset initialization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from copilot.tools.database.connection import DatabaseConnection
from copilot.tools.database.models import (
    Base,
    CorrectiveAction,
    IncomingInspection,
    Supplier,
    SupplierDeviation,
)

SEED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SeedReport:
    """Safe summary returned by the reusable database initializer."""

    database_name: str
    supplier_count: int
    deviation_count: int
    inspection_count: int
    corrective_action_count: int


def seed_demo_database(
    database_url: str,
    *,
    base_directory: Path | None = None,
) -> SeedReport:
    """Drop and recreate only the four demo tables, then insert fixed records."""
    connection = DatabaseConnection(
        database_url,
        read_only=False,
        base_directory=base_directory,
    )
    try:
        if connection.database_path is not None:
            connection.database_path.parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.drop_all(connection.engine)
        Base.metadata.create_all(connection.engine)
        with connection.session() as session:
            session.add_all(_suppliers())
            session.add_all(_deviations())
            session.add_all(_inspections())
            session.add_all(_corrective_actions())
        with connection.session() as session:
            return SeedReport(
                database_name=connection.database_name,
                supplier_count=_count(session, Supplier),
                deviation_count=_count(session, SupplierDeviation),
                inspection_count=_count(session, IncomingInspection),
                corrective_action_count=_count(session, CorrectiveAction),
            )
    finally:
        connection.dispose()


def _suppliers() -> list[Supplier]:
    return [
        Supplier(
            id=1,
            tenant_id="TENANT-DEMO",
            supplier_code="SUP-001",
            name="Reliable Components",
            country="CN",
            category="Mechanical",
            risk_level="LOW",
            created_at=SEED_TIMESTAMP,
        ),
        Supplier(
            id=2,
            tenant_id="TENANT-DEMO",
            supplier_code="SUP-002",
            name="High Risk Electronics",
            country="MY",
            category="Electronics",
            risk_level="HIGH",
            created_at=SEED_TIMESTAMP,
        ),
        Supplier(
            id=3,
            tenant_id="TENANT-DEMO",
            supplier_code="SUP-003",
            name="New Zero Defect Supplier",
            country="JP",
            category="Packaging",
            risk_level="LOW",
            created_at=SEED_TIMESTAMP,
        ),
        Supplier(
            id=4,
            tenant_id="TENANT-A",
            supplier_code="S-100",
            name="Walkthrough Precision",
            country="DE",
            category="Precision",
            risk_level="HIGH",
            created_at=SEED_TIMESTAMP,
        ),
        Supplier(
            id=5,
            tenant_id="TENANT-A",
            supplier_code="S-200",
            name="Walkthrough Standard",
            country="US",
            category="Standard",
            risk_level="MEDIUM",
            created_at=SEED_TIMESTAMP,
        ),
    ]


def _deviations() -> list[SupplierDeviation]:
    return [
        SupplierDeviation(
            id=1,
            supplier_id=1,
            deviation_date=date(2026, 1, 15),
            deviation_type="DIMENSIONAL",
            severity="MINOR",
            defect_quantity=10,
            description="Minor dimensional deviation contained at receiving.",
            created_at=SEED_TIMESTAMP,
        ),
        SupplierDeviation(
            id=2,
            supplier_id=2,
            deviation_date=date(2026, 2, 10),
            deviation_type="FUNCTIONAL",
            severity="CRITICAL",
            defect_quantity=150,
            description="Functional failure requires supplier containment.",
            created_at=SEED_TIMESTAMP,
        ),
        SupplierDeviation(
            id=3,
            supplier_id=4,
            deviation_date=date(2026, 3, 15),
            deviation_type="DIMENSIONAL",
            severity="MAJOR",
            defect_quantity=70,
            description="Same-day deviation and incoming inspection boundary case.",
            created_at=SEED_TIMESTAMP,
        ),
        SupplierDeviation(
            id=4,
            supplier_id=5,
            deviation_date=date(2026, 3, 20),
            deviation_type="COSMETIC",
            severity="MINOR",
            defect_quantity=35,
            description="Cosmetic deviation under documented review.",
            created_at=SEED_TIMESTAMP,
        ),
    ]


def _inspections() -> list[IncomingInspection]:
    specs = (
        (1, 1, date(2026, 1, 15), 1000, 10),
        (2, 1, date(2026, 2, 15), 1000, 15),
        (3, 1, date(2026, 3, 15), 1000, 20),
        (4, 2, date(2026, 1, 10), 1000, 120),
        (5, 2, date(2026, 2, 10), 1000, 150),
        (6, 2, date(2026, 3, 10), 1000, 180),
        (7, 3, date(2026, 3, 31), 500, 0),
        (8, 4, date(2026, 1, 15), 4000, 50),
        (9, 4, date(2026, 2, 15), 4000, 60),
        (10, 4, date(2026, 3, 15), 4000, 70),
        (11, 5, date(2026, 1, 20), 3000, 20),
        (12, 5, date(2026, 2, 20), 3500, 25),
        (13, 5, date(2026, 3, 20), 3500, 35),
    )
    return [
        IncomingInspection(
            id=identifier,
            supplier_id=supplier_id,
            inspection_date=inspection_date,
            total_quantity=total_quantity,
            accepted_quantity=total_quantity - rejected_quantity,
            rejected_quantity=rejected_quantity,
            created_at=SEED_TIMESTAMP,
        )
        for identifier, supplier_id, inspection_date, total_quantity, rejected_quantity in specs
    ]


def _corrective_actions() -> list[CorrectiveAction]:
    return [
        CorrectiveAction(
            id=1,
            supplier_id=1,
            opened_date=date(2026, 1, 16),
            due_date=date(2026, 2, 15),
            closed_date=date(2026, 2, 10),
            status="CLOSED",
            description="Completed dimensional containment and process update.",
            created_at=SEED_TIMESTAMP,
        ),
        CorrectiveAction(
            id=2,
            supplier_id=2,
            opened_date=date(2026, 2, 11),
            due_date=date(2026, 3, 1),
            closed_date=None,
            status="OPEN",
            description="Overdue corrective action for functional failures.",
            created_at=SEED_TIMESTAMP,
        ),
        CorrectiveAction(
            id=3,
            supplier_id=4,
            opened_date=date(2026, 3, 16),
            due_date=date(2026, 4, 15),
            closed_date=None,
            status="IN_PROGRESS",
            description="Major dimensional deviation corrective action.",
            created_at=SEED_TIMESTAMP,
        ),
        CorrectiveAction(
            id=4,
            supplier_id=5,
            opened_date=date(2026, 3, 21),
            due_date=date(2026, 4, 20),
            closed_date=date(2026, 4, 10),
            status="CLOSED",
            description="Cosmetic deviation review completed.",
            created_at=SEED_TIMESTAMP,
        ),
    ]


def _count(session: Session, model: type[Base]) -> int:
    scalar = session.scalar(select(func.count()).select_from(model))
    return int(scalar or 0)


__all__ = ["SEED_TIMESTAMP", "SeedReport", "seed_demo_database"]
