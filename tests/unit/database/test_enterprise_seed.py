"""Enterprise business data count, integrity, determinism, and pattern tests."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select

from copilot.tools.database import DatabaseConnection
from copilot.tools.database.models import IncomingInspection, Supplier
from copilot.tools.database.seed import (
    BUSINESS_PATTERN_ORACLE,
    DEFAULT_RANDOM_SEED,
    TARGET_INSPECTION_COUNT,
    TARGET_SUPPLIER_COUNT,
    DatasetProfile,
    SeedValidationError,
    seed_demo_database,
)


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'enterprise-seed.db'}"


def _supplier(profile: DatasetProfile, supplier_code: str) -> object:
    return next(item for item in profile.supplier_quality if item.supplier_code == supplier_code)


def _quarter_rates(profile: DatasetProfile, supplier_code: str) -> list[float]:
    return [
        item.defect_rate
        for item in profile.quarterly_quality
        if item.supplier_code == supplier_code
    ]


def test_schema_columns_keys_nullability_and_checks_match_orm_contract(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)
    seed_demo_database(database_url)
    connection = DatabaseConnection(database_url, read_only=True)
    try:
        inspector = inspect(connection.engine)
        supplier_columns = {item["name"]: item for item in inspector.get_columns("suppliers")}
        inspection_columns = {
            item["name"]: item for item in inspector.get_columns("incoming_inspections")
        }
        assert set(supplier_columns) == {
            "id",
            "tenant_id",
            "supplier_code",
            "name",
            "country",
            "category",
            "risk_level",
            "created_at",
        }
        assert set(inspection_columns) == {
            "id",
            "supplier_id",
            "inspection_date",
            "total_quantity",
            "accepted_quantity",
            "rejected_quantity",
            "created_at",
        }
        assert inspector.get_pk_constraint("suppliers")["constrained_columns"] == ["id"]
        assert inspector.get_pk_constraint("incoming_inspections")["constrained_columns"] == ["id"]
        assert all(item["nullable"] is False for item in inspection_columns.values())
        foreign_key = inspector.get_foreign_keys("incoming_inspections")[0]
        assert foreign_key["constrained_columns"] == ["supplier_id"]
        assert foreign_key["referred_table"] == "suppliers"
        checks = {item["name"] for item in inspector.get_check_constraints("incoming_inspections")}
        assert checks == {
            "ck_inspection_accepted_quantity",
            "ck_inspection_quantity_balance",
            "ck_inspection_rejected_quantity",
            "ck_inspection_total_quantity",
        }
    finally:
        connection.dispose()


def test_final_counts_time_coverage_referential_and_numeric_integrity(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)
    report = seed_demo_database(database_url)

    assert report.supplier_count == TARGET_SUPPLIER_COUNT == 17
    assert report.inspection_count == TARGET_INSPECTION_COUNT == 5000
    assert report.tenant_count == 2
    assert report.start_date == date(2026, 1, 1)
    assert report.end_date == date(2026, 12, 31)
    assert report.months_covered == 12
    assert all(count >= 300 for _month, count in report.profile.records_per_month)

    connection = DatabaseConnection(database_url, read_only=True)
    try:
        with connection.session() as session:
            invalid = session.scalar(
                select(func.count())
                .select_from(IncomingInspection)
                .where(
                    (IncomingInspection.total_quantity <= 0)
                    | (IncomingInspection.rejected_quantity < 0)
                    | (IncomingInspection.rejected_quantity > IncomingInspection.total_quantity)
                    | (
                        IncomingInspection.accepted_quantity + IncomingInspection.rejected_quantity
                        != IncomingInspection.total_quantity
                    )
                )
            )
            orphan = session.scalar(
                select(func.count())
                .select_from(IncomingInspection)
                .outerjoin(Supplier, IncomingInspection.supplier_id == Supplier.id)
                .where(Supplier.id.is_(None))
            )
            assert int(invalid or 0) == 0
            assert int(orphan or 0) == 0
    finally:
        connection.dispose()


def test_same_seed_is_repeatable_and_different_seed_changes_checksum(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)

    first = seed_demo_database(database_url, random_seed=DEFAULT_RANDOM_SEED)
    second = seed_demo_database(database_url, random_seed=DEFAULT_RANDOM_SEED)
    different = seed_demo_database(database_url, random_seed=DEFAULT_RANDOM_SEED + 1)

    assert first.dataset_checksum == second.dataset_checksum
    assert first.profile.as_dict() == second.profile.as_dict()
    assert different.dataset_checksum != first.dataset_checksum
    assert different.inspection_count == TARGET_INSPECTION_COUNT


def test_reset_is_one_transaction_and_validation_failure_preserves_prior_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copilot.tools.database import seed as seed_module

    database_url = _database_url(tmp_path)
    original = seed_demo_database(database_url)

    def fail_validation(_session: object, _profile: object) -> None:
        raise SeedValidationError("controlled post-insert validation failure")

    monkeypatch.setattr(seed_module, "_validate_seed", fail_validation)
    with pytest.raises(SeedValidationError, match="controlled"):
        seed_demo_database(database_url, random_seed=DEFAULT_RANDOM_SEED + 1)

    connection = DatabaseConnection(database_url, read_only=True)
    try:
        with connection.session() as session:
            supplier_count = session.scalar(select(func.count()).select_from(Supplier))
            inspection_count = session.scalar(select(func.count()).select_from(IncomingInspection))
            first_supplier = session.scalar(select(Supplier).order_by(Supplier.id).limit(1))
            assert int(supplier_count or 0) == original.supplier_count
            assert int(inspection_count or 0) == original.inspection_count
            assert first_supplier is not None
            assert first_supplier.name == "Asteron Precision Works"
    finally:
        connection.dispose()


def test_business_patterns_are_visible_in_final_database_aggregates(tmp_path: Path) -> None:
    profile = seed_demo_database(_database_url(tmp_path)).profile
    annual = {item.supplier_code: item for item in profile.supplier_quality}

    stable = _quarter_rates(profile, "SUP-001")
    persistent_poor = _quarter_rates(profile, "SUP-002")
    deteriorating = _quarter_rates(profile, "SUP-005")
    improving = _quarter_rates(profile, "SUP-006")

    assert max(stable) <= 0.012
    assert max(stable) - min(stable) <= 0.004
    assert min(persistent_poor) >= 0.035
    assert max(deteriorating[:2]) < 0.015
    assert min(deteriorating[2:]) > 0.03
    assert improving[0] > improving[1] > improving[2] > improving[3]
    assert annual["SUP-008"].record_count == max(item.record_count for item in annual.values())
    assert annual["SUP-008"].inspected_count == max(
        item.inspected_count for item in annual.values()
    )
    assert annual["SUP-008"].defect_count == max(item.defect_count for item in annual.values())
    assert annual["SUP-009"].record_count < 200
    assert annual["SUP-009"].defect_rate > 0.045
    assert annual["SUP-009"].defect_count < annual["SUP-008"].defect_count
    assert BUSINESS_PATTERN_ORACLE["SUP-005"] == ("deteriorating",)
    assert BUSINESS_PATTERN_ORACLE["SUP-007"] == ("incident_spike",)


def test_incident_spike_and_seasonality_exist_in_persisted_monthly_data(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)
    report = seed_demo_database(database_url)
    connection = DatabaseConnection(database_url, read_only=True)
    try:
        with connection.session() as session:
            rows = session.execute(
                select(
                    IncomingInspection.inspection_date,
                    IncomingInspection.total_quantity,
                    IncomingInspection.rejected_quantity,
                )
                .join(Supplier, IncomingInspection.supplier_id == Supplier.id)
                .where(
                    Supplier.tenant_id == "TENANT-DEMO",
                    Supplier.supplier_code == "SUP-007",
                )
            ).all()
    finally:
        connection.dispose()

    monthly: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for inspection_date, inspected, defects in rows:
        monthly[inspection_date.month][0] += inspected
        monthly[inspection_date.month][1] += defects
    rates = {month: defects / inspected for month, (inspected, defects) in monthly.items()}
    assert rates[8] > rates[7] * 5
    assert rates[8] > rates[9] * 5

    records = dict(report.profile.records_per_month)
    assert len(set(records.values())) > 6
    assert records["2026-07"] > records["2026-02"]


def test_walkthrough_totals_and_secondary_tenant_are_preserved(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)
    seed_demo_database(database_url)
    connection = DatabaseConnection(database_url, read_only=True)
    try:
        with connection.session() as session:
            rows = session.execute(
                select(
                    Supplier.supplier_code,
                    func.sum(IncomingInspection.total_quantity),
                    func.sum(IncomingInspection.rejected_quantity),
                )
                .join(IncomingInspection, IncomingInspection.supplier_id == Supplier.id)
                .where(
                    Supplier.tenant_id == "TENANT-A",
                    IncomingInspection.inspection_date >= date(2026, 1, 1),
                    IncomingInspection.inspection_date <= date(2026, 3, 31),
                )
                .group_by(Supplier.supplier_code)
            ).all()
    finally:
        connection.dispose()

    totals = {code: (int(inspected), int(defects)) for code, inspected, defects in rows}
    assert totals == {"S-100": (12_000, 180), "S-200": (10_000, 80)}
