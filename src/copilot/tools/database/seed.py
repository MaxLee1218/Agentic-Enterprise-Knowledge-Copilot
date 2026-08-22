"""Deterministic enterprise-style Supplier Quality demo dataset initialization."""

from __future__ import annotations

import calendar
import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from copilot.tools.database.connection import DatabaseConnection
from copilot.tools.database.migrations import upgrade_business_schema
from copilot.tools.database.models import (
    Base,
    CorrectiveAction,
    IncomingInspection,
    PurchaseOrder,
    Supplier,
    SupplierDeviation,
)

DEMO_YEAR = 2026
DEFAULT_RANDOM_SEED = 20_260_811
TARGET_SUPPLIER_COUNT = 17
TARGET_INSPECTION_COUNT = 5_000
SEED_TIMESTAMP = datetime(2027, 1, 1, tzinfo=UTC)

_PRIMARY_TENANT = "TENANT-DEMO"
_ISOLATION_TENANT = "TENANT-A"
_MONTHS = tuple(range(1, 13))


@dataclass(frozen=True, slots=True)
class SupplierProfile:
    """Seed-only business behavior; this is not a production domain model or table."""

    supplier_id: int
    tenant_id: str
    supplier_code: str
    name: str
    country: str
    category: str
    risk_level: str
    annual_record_count: int
    average_lot_size: int
    lot_variation: float
    monthly_defect_rates: tuple[float, ...]
    monthly_volume_factors: tuple[float, ...]
    rate_volatility: float
    patterns: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.monthly_defect_rates) != 12:
            raise ValueError("SupplierProfile must define 12 monthly defect rates")
        if len(self.monthly_volume_factors) != 12:
            raise ValueError("SupplierProfile must define 12 monthly volume factors")
        if self.annual_record_count < 12:
            raise ValueError("SupplierProfile must have at least one record per month")


@dataclass(frozen=True, slots=True)
class SupplierAggregate:
    """Final-database annual aggregate for one tenant-scoped supplier."""

    tenant_id: str
    supplier_code: str
    record_count: int
    inspected_count: int
    defect_count: int
    defect_rate: float


@dataclass(frozen=True, slots=True)
class PeriodAggregate:
    """Final-database quarterly aggregate for one tenant-scoped supplier."""

    tenant_id: str
    supplier_code: str
    quarter: str
    record_count: int
    inspected_count: int
    defect_count: int
    defect_rate: float
    quarter_over_quarter_delta: float | None


@dataclass(frozen=True, slots=True)
class DatasetProfile:
    """Deterministic profile calculated back from committed-shaped database rows."""

    seed: int
    dataset_checksum: str
    supplier_count: int
    inspection_record_count: int
    tenant_count: int
    start_date: date
    end_date: date
    months_covered: tuple[str, ...]
    records_per_month: tuple[tuple[str, int], ...]
    supplier_quality: tuple[SupplierAggregate, ...]
    quarterly_quality: tuple[PeriodAggregate, ...]

    def as_dict(self) -> dict[str, object]:
        """Return one JSON-compatible deterministic dataset profile."""
        return {
            "seed": self.seed,
            "dataset_checksum": self.dataset_checksum,
            "supplier_count": self.supplier_count,
            "inspection_record_count": self.inspection_record_count,
            "tenant_count": self.tenant_count,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "months_covered": list(self.months_covered),
            "records_per_month": dict(self.records_per_month),
            "supplier_quality": [
                {
                    "tenant_id": item.tenant_id,
                    "supplier_code": item.supplier_code,
                    "record_count": item.record_count,
                    "inspected_count": item.inspected_count,
                    "defect_count": item.defect_count,
                    "defect_rate": item.defect_rate,
                }
                for item in self.supplier_quality
            ],
            "quarterly_quality": [
                {
                    "tenant_id": item.tenant_id,
                    "supplier_code": item.supplier_code,
                    "quarter": item.quarter,
                    "record_count": item.record_count,
                    "inspected_count": item.inspected_count,
                    "defect_count": item.defect_count,
                    "defect_rate": item.defect_rate,
                    "quarter_over_quarter_delta": item.quarter_over_quarter_delta,
                }
                for item in self.quarterly_quality
            ],
            "business_pattern_oracle": {
                supplier_code: list(patterns)
                for supplier_code, patterns in BUSINESS_PATTERN_ORACLE.items()
            },
        }


@dataclass(frozen=True, slots=True)
class SeedReport:
    """Safe summary returned by the reusable database initializer."""

    database_name: str
    supplier_count: int
    deviation_count: int
    inspection_count: int
    corrective_action_count: int
    tenant_count: int
    start_date: date
    end_date: date
    months_covered: int
    dataset_checksum: str
    profile: DatasetProfile


class SeedValidationError(RuntimeError):
    """Raised before commit when generated business data violates the seed contract."""


def _constant_rates(rate: float) -> tuple[float, ...]:
    return (rate,) * 12


_BALANCED_VOLUME = (0.96, 0.93, 0.99, 1.01, 1.03, 1.05, 1.08, 1.10, 1.02, 0.98, 0.94, 0.91)
_SUMMER_RAMP = (0.90, 0.88, 0.93, 0.98, 1.04, 1.13, 1.24, 1.27, 1.14, 1.00, 0.96, 0.93)
_YEAR_END_PRESSURE = (
    0.91,
    0.88,
    0.94,
    0.98,
    1.00,
    1.03,
    1.04,
    1.02,
    1.05,
    1.10,
    1.18,
    1.23,
)
_NEW_PRODUCT_RAMP = (0.82, 0.86, 0.92, 1.02, 1.10, 1.17, 1.20, 1.16, 1.08, 1.03, 0.96, 0.88)


def _supplier_profiles() -> tuple[SupplierProfile, ...]:
    """Return the reviewed seed profiles whose record allocations sum to exactly 5,000."""
    return (
        SupplierProfile(
            1,
            _PRIMARY_TENANT,
            "SUP-001",
            "Asteron Precision Works",
            "CN",
            "Machined Components",
            "LOW",
            620,
            175,
            0.18,
            _constant_rates(0.007),
            _BALANCED_VOLUME,
            0.0008,
            ("stable_good",),
        ),
        SupplierProfile(
            2,
            _PRIMARY_TENANT,
            "SUP-002",
            "Northbridge Circuitry",
            "MY",
            "Electronics",
            "HIGH",
            360,
            165,
            0.20,
            _constant_rates(0.048),
            _YEAR_END_PRESSURE,
            0.0020,
            ("persistent_poor",),
        ),
        SupplierProfile(
            3,
            _PRIMARY_TENANT,
            "SUP-003",
            "Morrow Vale Packaging",
            "JP",
            "Packaging",
            "LOW",
            300,
            145,
            0.16,
            _constant_rates(0.006),
            _YEAR_END_PRESSURE,
            0.0007,
            ("stable_good",),
        ),
        SupplierProfile(
            4,
            _PRIMARY_TENANT,
            "SUP-004",
            "Caldera Sensor Systems",
            "MX",
            "Sensors",
            "HIGH",
            330,
            150,
            0.22,
            _constant_rates(0.041),
            _SUMMER_RAMP,
            0.0022,
            ("persistent_poor",),
        ),
        SupplierProfile(
            5,
            _PRIMARY_TENANT,
            "SUP-005",
            "Bluehaven Polymer Labs",
            "TH",
            "Plastic Components",
            "HIGH",
            330,
            155,
            0.20,
            (0.009, 0.010, 0.009, 0.010, 0.011, 0.010, 0.036, 0.040, 0.043, 0.042, 0.044, 0.045),
            _SUMMER_RAMP,
            0.0012,
            ("deteriorating",),
        ),
        SupplierProfile(
            6,
            _PRIMARY_TENANT,
            "SUP-006",
            "Redwood Alloy Forming",
            "IN",
            "Raw Materials",
            "MEDIUM",
            320,
            180,
            0.19,
            (0.044, 0.042, 0.039, 0.030, 0.027, 0.024, 0.018, 0.015, 0.013, 0.011, 0.009, 0.008),
            _BALANCED_VOLUME,
            0.0010,
            ("improving",),
        ),
        SupplierProfile(
            7,
            _PRIMARY_TENANT,
            "SUP-007",
            "Silverline Tooling Group",
            "PL",
            "Precision Parts",
            "MEDIUM",
            300,
            165,
            0.18,
            (0.010, 0.009, 0.010, 0.011, 0.010, 0.010, 0.011, 0.075, 0.012, 0.010, 0.009, 0.010),
            _SUMMER_RAMP,
            0.0009,
            ("incident_spike",),
        ),
        SupplierProfile(
            8,
            _PRIMARY_TENANT,
            "SUP-008",
            "Granite Peak Industrial",
            "VN",
            "Strategic Assemblies",
            "MEDIUM",
            650,
            305,
            0.21,
            _constant_rates(0.018),
            _NEW_PRODUCT_RAMP,
            0.0013,
            ("high_volume", "moderate_quality"),
        ),
        SupplierProfile(
            9,
            _PRIMARY_TENANT,
            "SUP-009",
            "Juniper Microcast",
            "KR",
            "Specialty Castings",
            "HIGH",
            140,
            75,
            0.23,
            _constant_rates(0.058),
            _BALANCED_VOLUME,
            0.0025,
            ("low_volume_high_rate",),
        ),
        SupplierProfile(
            10,
            _PRIMARY_TENANT,
            "SUP-010",
            "Orchard Gate Fasteners",
            "CZ",
            "Fasteners",
            "LOW",
            270,
            130,
            0.17,
            _constant_rates(0.008),
            _YEAR_END_PRESSURE,
            0.0008,
            ("stable_good",),
        ),
        SupplierProfile(
            11,
            _PRIMARY_TENANT,
            "SUP-011",
            "Cobalt Ridge Controls",
            "PH",
            "Electronic Controls",
            "MEDIUM",
            260,
            160,
            0.20,
            (0.014, 0.014, 0.015, 0.015, 0.016, 0.018, 0.020, 0.021, 0.018, 0.016, 0.016, 0.017),
            _SUMMER_RAMP,
            0.0010,
            ("seasonal_average",),
        ),
        SupplierProfile(
            12,
            _PRIMARY_TENANT,
            "SUP-012",
            "Willowbrook Elastomers",
            "ID",
            "Sealing Components",
            "LOW",
            250,
            125,
            0.16,
            _constant_rates(0.0055),
            _BALANCED_VOLUME,
            0.0006,
            ("stable_good",),
        ),
        SupplierProfile(
            13,
            _PRIMARY_TENANT,
            "SUP-013",
            "Harborstone Composites",
            "TR",
            "Composite Materials",
            "MEDIUM",
            200,
            170,
            0.19,
            (0.016, 0.016, 0.017, 0.017, 0.018, 0.018, 0.019, 0.019, 0.020, 0.021, 0.024, 0.025),
            _YEAR_END_PRESSURE,
            0.0010,
            ("seasonal_average",),
        ),
        SupplierProfile(
            14,
            _PRIMARY_TENANT,
            "SUP-014",
            "Pinecrest Optical Parts",
            "TW",
            "Optical Components",
            "LOW",
            160,
            115,
            0.17,
            _constant_rates(0.009),
            _NEW_PRODUCT_RAMP,
            0.0008,
            ("stable_good",),
        ),
        SupplierProfile(
            15,
            _PRIMARY_TENANT,
            "SUP-015",
            "Meadowlark Label Works",
            "SG",
            "Industrial Labels",
            "LOW",
            130,
            90,
            0.18,
            _constant_rates(0.0065),
            _YEAR_END_PRESSURE,
            0.0007,
            ("stable_good",),
        ),
        SupplierProfile(
            16,
            _ISOLATION_TENANT,
            "S-100",
            "Walkthrough Precision",
            "DE",
            "Precision Parts",
            "HIGH",
            200,
            235,
            0.18,
            _constant_rates(0.015),
            _BALANCED_VOLUME,
            0.0008,
            ("walkthrough_control",),
        ),
        SupplierProfile(
            17,
            _ISOLATION_TENANT,
            "S-200",
            "Walkthrough Standard",
            "US",
            "Standard Components",
            "MEDIUM",
            180,
            220,
            0.18,
            _constant_rates(0.008),
            _BALANCED_VOLUME,
            0.0007,
            ("walkthrough_control",),
        ),
    )


BUSINESS_PATTERN_ORACLE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {profile.supplier_code: profile.patterns for profile in _supplier_profiles()}
)

_WALKTHROUGH_MONTH_TARGETS: Mapping[tuple[str, int], tuple[int, int]] = MappingProxyType(
    {
        ("S-100", 1): (4_000, 50),
        ("S-100", 2): (4_000, 60),
        ("S-100", 3): (4_000, 70),
        ("S-200", 1): (3_000, 20),
        ("S-200", 2): (3_500, 25),
        ("S-200", 3): (3_500, 35),
    }
)


def seed_demo_database(
    database_url: str,
    *,
    base_directory: Path | None = None,
    random_seed: int = DEFAULT_RANDOM_SEED,
    reset: bool = True,
) -> SeedReport:
    """Migrate the business schema and atomically replace Supplier Quality demo rows."""
    connection = DatabaseConnection(
        database_url,
        read_only=False,
        base_directory=base_directory,
    )
    try:
        if connection.database_path is not None:
            connection.database_path.parent.mkdir(parents=True, exist_ok=True)
        migration_url = connection.engine.url.render_as_string(hide_password=False)
    finally:
        connection.dispose()

    upgrade_business_schema(migration_url)
    connection = DatabaseConnection(
        database_url,
        read_only=False,
        base_directory=base_directory,
    )
    try:
        profiles = _supplier_profiles()
        inspections = _generate_inspections(profiles, random_seed=random_seed)
        with connection.session() as session:
            preserve_supplier_master = _count(session, PurchaseOrder) > 0
            if reset:
                _delete_existing_rows(
                    session,
                    preserve_supplier_master=preserve_supplier_master,
                )
            elif _count(session, Supplier) > 0:
                raise SeedValidationError(
                    "Demo business tables already contain rows; rerun with reset enabled"
                )
            if preserve_supplier_master:
                _synchronize_supplier_master(session, profiles)
            else:
                session.add_all(_suppliers(profiles))
            session.add_all(_deviations())
            session.add_all(inspections)
            session.add_all(_corrective_actions())
            session.flush()
            profile = _build_dataset_profile(session, random_seed=random_seed)
            _validate_seed(session, profile)
            report = SeedReport(
                database_name=connection.database_name,
                supplier_count=_count(session, Supplier),
                deviation_count=_count(session, SupplierDeviation),
                inspection_count=_count(session, IncomingInspection),
                corrective_action_count=_count(session, CorrectiveAction),
                tenant_count=profile.tenant_count,
                start_date=profile.start_date,
                end_date=profile.end_date,
                months_covered=len(profile.months_covered),
                dataset_checksum=profile.dataset_checksum,
                profile=profile,
            )
        return report
    finally:
        connection.dispose()


def _suppliers(profiles: Sequence[SupplierProfile]) -> list[Supplier]:
    return [
        Supplier(
            id=profile.supplier_id,
            tenant_id=profile.tenant_id,
            supplier_code=profile.supplier_code,
            name=profile.name,
            country=profile.country,
            category=profile.category,
            risk_level=profile.risk_level,
            created_at=SEED_TIMESTAMP,
        )
        for profile in profiles
    ]


def _generate_inspections(
    profiles: Sequence[SupplierProfile],
    *,
    random_seed: int,
) -> list[IncomingInspection]:
    rng = random.Random(random_seed)
    inspections: list[IncomingInspection] = []
    identifier = 1
    for profile in profiles:
        monthly_counts = _allocate_integer(
            profile.annual_record_count,
            profile.monthly_volume_factors,
            minimum_each=1,
        )
        for month, record_count in zip(_MONTHS, monthly_counts, strict=True):
            special_target = _WALKTHROUGH_MONTH_TARGETS.get((profile.supplier_code, month))
            if special_target is None:
                quantities = _natural_lot_quantities(profile, record_count, rng)
                target_rate = max(
                    0.0001,
                    profile.monthly_defect_rates[month - 1]
                    + rng.triangular(
                        -profile.rate_volatility,
                        profile.rate_volatility,
                        0.0,
                    ),
                )
                target_defects = round(sum(quantities) * target_rate)
            else:
                target_quantity, target_defects = special_target
                quantities = _quantities_for_exact_total(target_quantity, record_count, rng)
            defects = _allocate_defects(quantities, target_defects, rng)
            inspection_dates = _distributed_workdays(DEMO_YEAR, month, record_count, rng)
            for inspection_date, total_quantity, rejected_quantity in zip(
                inspection_dates,
                quantities,
                defects,
                strict=True,
            ):
                inspections.append(
                    IncomingInspection(
                        id=identifier,
                        supplier_id=profile.supplier_id,
                        inspection_date=inspection_date,
                        total_quantity=total_quantity,
                        accepted_quantity=total_quantity - rejected_quantity,
                        rejected_quantity=rejected_quantity,
                        created_at=SEED_TIMESTAMP,
                    )
                )
                identifier += 1
    if len(inspections) != TARGET_INSPECTION_COUNT:
        raise SeedValidationError(
            f"Generator produced {len(inspections)} inspections, expected {TARGET_INSPECTION_COUNT}"
        )
    return inspections


def _allocate_integer(
    total: int,
    weights: Sequence[float],
    *,
    minimum_each: int,
) -> tuple[int, ...]:
    if not weights or total < minimum_each * len(weights) or any(weight <= 0 for weight in weights):
        raise SeedValidationError("Integer allocation inputs are invalid")
    remaining = total - minimum_each * len(weights)
    weight_sum = sum(weights)
    quotas = [remaining * weight / weight_sum for weight in weights]
    floors = [int(quota) for quota in quotas]
    allocated = [minimum_each + value for value in floors]
    remainder = total - sum(allocated)
    order = sorted(
        range(len(weights)),
        key=lambda index: (quotas[index] - floors[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return tuple(allocated)


def _natural_lot_quantities(
    profile: SupplierProfile,
    record_count: int,
    rng: random.Random,
) -> tuple[int, ...]:
    return tuple(
        max(
            10,
            round(
                profile.average_lot_size
                * rng.triangular(
                    1 - profile.lot_variation,
                    1 + profile.lot_variation,
                    1.0,
                )
            ),
        )
        for _ in range(record_count)
    )


def _quantities_for_exact_total(
    target_total: int,
    record_count: int,
    rng: random.Random,
) -> tuple[int, ...]:
    weights = tuple(rng.triangular(0.78, 1.22, 1.0) for _ in range(record_count))
    return _allocate_integer(target_total, weights, minimum_each=1)


def _allocate_defects(
    quantities: Sequence[int],
    target_defects: int,
    rng: random.Random,
) -> tuple[int, ...]:
    total_quantity = sum(quantities)
    if target_defects < 0 or target_defects > total_quantity:
        raise SeedValidationError("Monthly target defects violate quantity bounds")
    if total_quantity == 0:
        return (0,) * len(quantities)
    exact = [quantity * target_defects / total_quantity for quantity in quantities]
    allocated = [int(value) for value in exact]
    remainder = target_defects - sum(allocated)
    order = sorted(
        range(len(quantities)),
        key=lambda index: (exact[index] - allocated[index] + rng.random() * 0.0001, -index),
        reverse=True,
    )
    for index in order[:remainder]:
        allocated[index] += 1
    if any(defect > quantity for defect, quantity in zip(allocated, quantities, strict=True)):
        raise SeedValidationError("Allocated defects exceed an inspection quantity")
    return tuple(allocated)


def _distributed_workdays(
    year: int,
    month: int,
    record_count: int,
    rng: random.Random,
) -> tuple[date, ...]:
    last_day = calendar.monthrange(year, month)[1]
    workdays = [
        date(year, month, day)
        for day in range(1, last_day + 1)
        if date(year, month, day).weekday() < 5
    ]
    if record_count == 1:
        return (workdays[len(workdays) // 2],)
    positions: list[int] = []
    for index in range(record_count):
        position = round(index * (len(workdays) - 1) / (record_count - 1))
        if index not in {0, record_count - 1}:
            position = min(
                len(workdays) - 1,
                max(0, position + rng.choice((-1, 0, 0, 0, 1))),
            )
        positions.append(position)
    return tuple(workdays[position] for position in sorted(positions))


def _delete_existing_rows(
    session: Session,
    *,
    preserve_supplier_master: bool,
) -> None:
    for model in (CorrectiveAction, IncomingInspection, SupplierDeviation):
        session.execute(delete(model))
    if not preserve_supplier_master:
        session.execute(delete(Supplier))


def _synchronize_supplier_master(
    session: Session,
    profiles: Sequence[SupplierProfile],
) -> None:
    expected_by_id = {profile.supplier_id: profile for profile in profiles}
    existing_by_id = {supplier.id: supplier for supplier in session.scalars(select(Supplier))}
    if set(existing_by_id) != set(expected_by_id):
        raise SeedValidationError(
            "Referenced Supplier Quality master does not match the frozen supplier set"
        )
    for identifier, supplier in existing_by_id.items():
        profile = expected_by_id[identifier]
        if supplier.tenant_id != profile.tenant_id:
            raise SeedValidationError(
                "Referenced Supplier Quality master has a conflicting tenant binding"
            )
        supplier.supplier_code = profile.supplier_code
        supplier.name = profile.name
        supplier.country = profile.country
        supplier.category = profile.category
        supplier.risk_level = profile.risk_level
        supplier.created_at = SEED_TIMESTAMP


def _deviations() -> list[SupplierDeviation]:
    """Retain the small legacy compatibility fixtures; active templates do not read them."""
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
            supplier_id=16,
            deviation_date=date(2026, 3, 15),
            deviation_type="DIMENSIONAL",
            severity="MAJOR",
            defect_quantity=70,
            description="Same-day deviation and incoming inspection boundary case.",
            created_at=SEED_TIMESTAMP,
        ),
        SupplierDeviation(
            id=4,
            supplier_id=17,
            deviation_date=date(2026, 3, 20),
            deviation_type="COSMETIC",
            severity="MINOR",
            defect_quantity=35,
            description="Cosmetic deviation under documented review.",
            created_at=SEED_TIMESTAMP,
        ),
    ]


def _corrective_actions() -> list[CorrectiveAction]:
    """Retain the small legacy compatibility fixtures; active templates do not read them."""
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
            supplier_id=16,
            opened_date=date(2026, 3, 16),
            due_date=date(2026, 4, 15),
            closed_date=None,
            status="IN_PROGRESS",
            description="Major dimensional deviation corrective action.",
            created_at=SEED_TIMESTAMP,
        ),
        CorrectiveAction(
            id=4,
            supplier_id=17,
            opened_date=date(2026, 3, 21),
            due_date=date(2026, 4, 20),
            closed_date=date(2026, 4, 10),
            status="CLOSED",
            description="Cosmetic deviation review completed.",
            created_at=SEED_TIMESTAMP,
        ),
    ]


def _build_dataset_profile(session: Session, *, random_seed: int) -> DatasetProfile:
    suppliers = tuple(session.scalars(select(Supplier).order_by(Supplier.id)))
    inspections = tuple(session.scalars(select(IncomingInspection).order_by(IncomingInspection.id)))
    supplier_by_id = {supplier.id: supplier for supplier in suppliers}
    annual: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    quarterly: dict[tuple[str, str, int], list[int]] = defaultdict(lambda: [0, 0, 0])
    records_per_month: dict[str, int] = defaultdict(int)
    dates: list[date] = []
    for inspection in inspections:
        supplier = supplier_by_id[inspection.supplier_id]
        annual_key = (supplier.tenant_id, supplier.supplier_code)
        annual[annual_key][0] += 1
        annual[annual_key][1] += inspection.total_quantity
        annual[annual_key][2] += inspection.rejected_quantity
        quarter = (inspection.inspection_date.month - 1) // 3 + 1
        quarter_key = (supplier.tenant_id, supplier.supplier_code, quarter)
        quarterly[quarter_key][0] += 1
        quarterly[quarter_key][1] += inspection.total_quantity
        quarterly[quarter_key][2] += inspection.rejected_quantity
        period = inspection.inspection_date.strftime("%Y-%m")
        records_per_month[period] += 1
        dates.append(inspection.inspection_date)

    supplier_quality = tuple(
        SupplierAggregate(
            tenant_id=key[0],
            supplier_code=key[1],
            record_count=values[0],
            inspected_count=values[1],
            defect_count=values[2],
            defect_rate=_ratio(values[2], values[1]),
        )
        for key, values in sorted(annual.items())
    )
    quarterly_quality: list[PeriodAggregate] = []
    for tenant_id, supplier_code in sorted(annual):
        previous_rate: float | None = None
        for quarter in range(1, 5):
            values = quarterly[(tenant_id, supplier_code, quarter)]
            rate = _ratio(values[2], values[1])
            delta = None if previous_rate is None else round(rate - previous_rate, 6)
            quarterly_quality.append(
                PeriodAggregate(
                    tenant_id=tenant_id,
                    supplier_code=supplier_code,
                    quarter=f"{DEMO_YEAR}-Q{quarter}",
                    record_count=values[0],
                    inspected_count=values[1],
                    defect_count=values[2],
                    defect_rate=rate,
                    quarter_over_quarter_delta=delta,
                )
            )
            previous_rate = rate

    return DatasetProfile(
        seed=random_seed,
        dataset_checksum=_dataset_checksum(suppliers, inspections),
        supplier_count=len(suppliers),
        inspection_record_count=len(inspections),
        tenant_count=len({supplier.tenant_id for supplier in suppliers}),
        start_date=min(dates),
        end_date=max(dates),
        months_covered=tuple(sorted(records_per_month)),
        records_per_month=tuple(sorted(records_per_month.items())),
        supplier_quality=supplier_quality,
        quarterly_quality=tuple(quarterly_quality),
    )


def _dataset_checksum(
    suppliers: Sequence[Supplier],
    inspections: Sequence[IncomingInspection],
) -> str:
    payload = {
        "suppliers": [
            [
                supplier.id,
                supplier.tenant_id,
                supplier.supplier_code,
                supplier.name,
                supplier.country,
                supplier.category,
                supplier.risk_level,
            ]
            for supplier in suppliers
        ],
        "incoming_inspections": [
            [
                inspection.id,
                inspection.supplier_id,
                inspection.inspection_date.isoformat(),
                inspection.total_quantity,
                inspection.accepted_quantity,
                inspection.rejected_quantity,
            ]
            for inspection in inspections
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_seed(session: Session, profile: DatasetProfile) -> None:
    if profile.supplier_count != TARGET_SUPPLIER_COUNT:
        raise SeedValidationError("Supplier count does not match the reviewed seed contract")
    if profile.inspection_record_count != TARGET_INSPECTION_COUNT:
        raise SeedValidationError("Inspection count does not match the reviewed seed contract")
    if profile.start_date != date(DEMO_YEAR, 1, 1):
        raise SeedValidationError("Dataset does not begin on the first day of the demo year")
    if profile.end_date != date(DEMO_YEAR, 12, 31):
        raise SeedValidationError("Dataset does not end on the last day of the demo year")
    expected_months = tuple(f"{DEMO_YEAR}-{month:02d}" for month in _MONTHS)
    if profile.months_covered != expected_months:
        raise SeedValidationError("Dataset does not continuously cover all 12 calendar months")
    invalid_quantities = session.scalar(
        select(func.count())
        .select_from(IncomingInspection)
        .where(
            (IncomingInspection.total_quantity <= 0)
            | (IncomingInspection.accepted_quantity < 0)
            | (IncomingInspection.rejected_quantity < 0)
            | (IncomingInspection.rejected_quantity > IncomingInspection.total_quantity)
            | (
                IncomingInspection.accepted_quantity + IncomingInspection.rejected_quantity
                != IncomingInspection.total_quantity
            )
        )
    )
    if int(invalid_quantities or 0) != 0:
        raise SeedValidationError("Inspection quantity integrity validation failed")
    _validate_walkthrough_totals(session)
    _validate_business_patterns(profile)


def _validate_walkthrough_totals(session: Session) -> None:
    rows = session.execute(
        select(
            Supplier.supplier_code,
            func.sum(IncomingInspection.total_quantity),
            func.sum(IncomingInspection.rejected_quantity),
        )
        .join(IncomingInspection, IncomingInspection.supplier_id == Supplier.id)
        .where(
            Supplier.tenant_id == _ISOLATION_TENANT,
            IncomingInspection.inspection_date >= date(2026, 1, 1),
            IncomingInspection.inspection_date <= date(2026, 3, 31),
        )
        .group_by(Supplier.supplier_code)
    ).all()
    totals = {code: (int(inspected or 0), int(defects or 0)) for code, inspected, defects in rows}
    if totals != {"S-100": (12_000, 180), "S-200": (10_000, 80)}:
        raise SeedValidationError("Frozen walkthrough Q1 totals were not preserved")


def _validate_business_patterns(profile: DatasetProfile) -> None:
    annual = {item.supplier_code: item for item in profile.supplier_quality}
    quarters = {(item.supplier_code, item.quarter): item for item in profile.quarterly_quality}
    stable_rates = [
        quarters[("SUP-001", f"{DEMO_YEAR}-Q{quarter}")].defect_rate for quarter in range(1, 5)
    ]
    if max(stable_rates) > 0.012 or max(stable_rates) - min(stable_rates) > 0.004:
        raise SeedValidationError("Stable-good pattern is not visible in final aggregates")
    poor_rates = [
        quarters[("SUP-002", f"{DEMO_YEAR}-Q{quarter}")].defect_rate for quarter in range(1, 5)
    ]
    if min(poor_rates) < 0.035:
        raise SeedValidationError("Persistent-poor pattern is not visible in every quarter")
    deteriorating = [
        quarters[("SUP-005", f"{DEMO_YEAR}-Q{quarter}")].defect_rate for quarter in range(1, 5)
    ]
    if not (max(deteriorating[:2]) < 0.015 and min(deteriorating[2:]) > 0.03):
        raise SeedValidationError("Deteriorating pattern is not visible in quarterly aggregates")
    improving = [
        quarters[("SUP-006", f"{DEMO_YEAR}-Q{quarter}")].defect_rate for quarter in range(1, 5)
    ]
    if not all(
        current > following for current, following in zip(improving, improving[1:], strict=False)
    ):
        raise SeedValidationError("Improving pattern is not monotonic in quarterly aggregates")
    if annual["SUP-008"].inspected_count != max(item.inspected_count for item in annual.values()):
        raise SeedValidationError("High-volume supplier is not highest by inspected quantity")
    if annual["SUP-008"].defect_count != max(item.defect_count for item in annual.values()):
        raise SeedValidationError("High-volume supplier is not highest by absolute defect count")
    if not (
        annual["SUP-009"].record_count < 200
        and annual["SUP-009"].defect_rate > 0.045
        and annual["SUP-009"].defect_count < annual["SUP-008"].defect_count
    ):
        raise SeedValidationError("Low-volume/high-rate pattern is not visible in final aggregates")


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _count(session: Session, model: type[Base]) -> int:
    scalar = session.scalar(select(func.count()).select_from(model))
    return int(scalar or 0)


__all__ = [
    "BUSINESS_PATTERN_ORACLE",
    "DEFAULT_RANDOM_SEED",
    "DEMO_YEAR",
    "SEED_TIMESTAMP",
    "TARGET_INSPECTION_COUNT",
    "TARGET_SUPPLIER_COUNT",
    "DatasetProfile",
    "SeedReport",
    "SeedValidationError",
    "SupplierProfile",
    "seed_demo_database",
]
