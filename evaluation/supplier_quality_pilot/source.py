"""Seed a private business database and independently query source-only oracle data."""

import sqlite3
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from copilot.tools.database import DatabaseConnection
from copilot.tools.database.models import Base, IncomingInspection, Supplier
from evaluation.supplier_quality_pilot.cases import PilotCase
from evaluation.supplier_quality_pilot.contracts import OracleMetric
from evaluation.supplier_quality_pilot.interfaces import ReportScope, SourceFacts

DEFAULT_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "datasets/fixtures/database/supplier_quality_pilot_v1.json"
)
P01_REQUEST = "Analyze supplier quality for S-100 and S-200 in Q2 2026 and generate a JSON report."
P01_SUPPLIERS = ("S-100", "S-200")


def seed_source(snapshot: SourceFacts, database_url: str) -> None:
    """Prepare synthetic facts before opening the production SELECT-only connection."""
    database = DatabaseConnection(database_url, read_only=False)
    try:
        Base.metadata.create_all(database.engine)
        with database.session() as session:
            suppliers: dict[tuple[str, str], int] = {}
            for row in snapshot.rows:
                key = (row.tenant_id, row.supplier_id)
                if key not in suppliers:
                    supplier = Supplier(
                        id=len(suppliers) + 1,
                        tenant_id=row.tenant_id,
                        supplier_code=row.supplier_id,
                        name=f"Synthetic {row.supplier_id}",
                        country="DE",
                        category="pilot",
                        risk_level="LOW",
                        created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                    session.add(supplier)
                    session.flush()
                    suppliers[key] = supplier.id
                session.add(
                    IncomingInspection(
                        supplier_id=suppliers[key],
                        inspection_date=date.fromisoformat(f"{row.period}-15"),
                        total_quantity=row.inspected_count,
                        rejected_quantity=row.defect_count,
                        accepted_quantity=row.inspected_count - row.defect_count,
                        created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                )
    finally:
        database.dispose()


def source_counts(
    snapshot: SourceFacts, case: ReportScope | None = None
) -> tuple[tuple[str, str, int, int], ...]:
    """Use independent SQL over the frozen source, never the product query templates."""
    case = case or PilotCase(case_id="P01", request=P01_REQUEST)
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE source (tenant, supplier, period, defects, inspected)")
        connection.executemany(
            "INSERT INTO source VALUES (?, ?, ?, ?, ?)",
            [
                (r.tenant_id, r.supplier_id, r.period, r.defect_count, r.inspected_count)
                for r in snapshot.rows
            ],
        )
        return tuple(
            (str(s), str(p), int(d), int(n))
            for s, p, d, n in connection.execute(
                "SELECT supplier, period, SUM(defects), SUM(inspected) FROM source "
                f"WHERE tenant = ? AND supplier IN ({','.join('?' for _ in case.suppliers)}) "
                "AND period BETWEEN ? AND ? "
                "GROUP BY supplier, period ORDER BY supplier, period",
                (
                    snapshot.tenant_id,
                    *case.suppliers,
                    case.start_date.strftime("%Y-%m"),
                    case.end_date.strftime("%Y-%m"),
                ),
            )
        )
    finally:
        connection.close()


def oracle_metrics(
    snapshot: SourceFacts, case: ReportScope | None = None
) -> tuple[OracleMetric, ...]:
    """Derive monthly values and operands using Decimal, without product calculators."""
    output: list[OracleMetric] = []
    previous: dict[str, Decimal | None] = {}
    for supplier, period, defects, inspected in source_counts(snapshot, case):
        rate = ratio(defects, inspected)
        prior = previous.get(supplier)
        trend = None if rate is None or prior is None else rate - prior
        for name, value, numerator, denominator, unit in (
            ("defect_count", Decimal(defects), Decimal(defects), None, "count"),
            ("inspected_count", Decimal(inspected), Decimal(inspected), None, "count"),
            ("defect_rate", rate, Decimal(defects), Decimal(inspected), "ratio"),
            ("period_over_period_trend", trend, rate, prior, "ratio_delta"),
        ):
            output.append(
                OracleMetric(
                    metric=name,
                    supplier_id=supplier,
                    period=period,
                    value=value,
                    numerator=numerator,
                    denominator=denominator,
                    unit=unit,
                )
            )
        previous[supplier] = rate
    return tuple(output)


def ratio(numerator: int, denominator: int) -> Decimal | None:
    """Apply the frozen representation rule independently of the production engine."""
    if denominator == 0:
        return None
    return (Decimal(numerator) / denominator).quantize(Decimal("0.0001"), ROUND_HALF_EVEN)
