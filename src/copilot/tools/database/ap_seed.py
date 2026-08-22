"""Deterministic Accounts Payable v1 synthetic business dataset."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import Session

from copilot.tools.database.connection import DatabaseConnection
from copilot.tools.database.migrations import upgrade_business_schema
from copilot.tools.database.models import (
    BusinessUnit,
    Invoice,
    LegalEntity,
    Payment,
    PurchaseOrder,
    Supplier,
)
from copilot.tools.database.normalization import (
    INVOICE_NUMBER_NORMALIZATION_VERSION,
    normalize_invoice_number,
)

AP_DATASET_NAME = "accounts-payable-v1"
AP_SCHEMA_VERSION = "accounts_payable.v1"
AP_SEED_PROFILE_VERSION = "ap-demo-dataset.v1"
DEFAULT_AP_RANDOM_SEED = 42
AP_SEED_TIMESTAMP = datetime(2026, 10, 1, tzinfo=UTC)
_SOURCE_SYSTEM = "ERP-DEMO"
_FIXTURE_VARIANCE_RATE = Decimal("0.05")
_FIXTURE_PO_REQUIRED_AMOUNT = Decimal("1000.0000")
_FIXTURE_MATERIAL_EARLY_DAYS = 10
_FIXTURE_OVERPAYMENT_TOLERANCE = Decimal("5.0000")

logger = logging.getLogger(__name__)

AP_EXCEPTION_ORACLE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "EXACT_DUPLICATE_INVOICE": ("INV-DUP-001-B",),
        "PO_AMOUNT_VARIANCE": ("INV-VAR-ABOVE",),
        "MISSING_REQUIRED_PO": ("INV-NOPO-ABOVE",),
        "LATE_PAYMENT": ("INV-LATE",),
        "MATERIAL_EARLY_PAYMENT": ("INV-EARLY-BOUNDARY", "INV-EARLY-MATERIAL"),
        "OVERPAYMENT": ("INV-OVER-ABOVE",),
    }
)

AP_EXCLUSION_ORACLE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "PO_AMOUNT_ZERO": ("INV-PO-ZERO",),
        "AP_CURRENCY_MISMATCH_EXCLUDED": ("INV-CURRENCY-MISMATCH",),
        "MULTI_INVOICE_MATCHING_UNSUPPORTED": ("INV-MULTI-PO",),
        "UNPAID_INVOICE": (
            "INV-CURRENCY-MISMATCH",
            "INV-DUP-001-A",
            "INV-DUP-001-B",
            "INV-MULTI-PO",
            "INV-NOPO-ABOVE",
            "INV-NOPO-APPROVED",
            "INV-NOPO-BELOW",
            "INV-PO-ZERO",
            "INV-Q1-CONTROL",
            "INV-Q3-CONTROL",
            "INV-SAME-NUMBER-DIFFERENT",
            "INV-TENANT-A",
            "INV-TENANT-A-SECOND",
            "INV-UNPAID",
            "INV-VAR-ABOVE",
            "INV-VAR-BOUNDARY",
            "INV-VAR-WITHIN",
        ),
        "MULTIPLE_PAYMENT_EXCLUSION": ("INV-MULTI-PAY",),
    }
)

AP_SCENARIO_LABELS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "INV-CLEAN-001": ("clean_invoice", "on_time_payment"),
        "INV-DUP-001-A": ("exact_duplicate_group", "canonical_member"),
        "INV-DUP-001-B": ("exact_duplicate_group", "noncanonical_member"),
        "INV-SAME-NUMBER-DIFFERENT": ("same_number_nonduplicate",),
        "INV-VAR-WITHIN": ("po_variance_within_tolerance",),
        "INV-VAR-BOUNDARY": ("po_variance_boundary",),
        "INV-VAR-ABOVE": ("po_variance_above_tolerance",),
        "INV-NOPO-BELOW": ("missing_po_below_threshold",),
        "INV-NOPO-ABOVE": ("missing_po_above_threshold",),
        "INV-NOPO-APPROVED": ("approved_no_po_exception",),
        "INV-ONTIME": ("on_time_payment",),
        "INV-LATE": ("late_payment",),
        "INV-EARLY-WITHIN": ("early_payment_below_boundary",),
        "INV-EARLY-BOUNDARY": ("material_early_boundary",),
        "INV-EARLY-MATERIAL": ("material_early_payment",),
        "INV-OVER-EXACT": ("payment_equals_invoice",),
        "INV-OVER-BOUNDARY": ("overpayment_tolerance_boundary",),
        "INV-OVER-ABOVE": ("overpayment_above_tolerance",),
        "INV-PO-ZERO": ("zero_po_amount_exclusion",),
        "INV-CURRENCY-MISMATCH": ("currency_mismatch_exclusion",),
        "INV-UNPAID": ("unpaid_invoice_exclusion",),
        "INV-MULTI-PAY": ("multiple_payment_exclusion",),
        "INV-MULTI-PO": ("multi_invoice_po_exclusion",),
        "INV-Q1-CONTROL": ("outside_q2_control",),
        "INV-Q3-CONTROL": ("outside_q2_control",),
        "INV-TENANT-A": ("secondary_tenant_control",),
        "INV-TENANT-A-SECOND": ("sixth_supplier_control",),
    }
)


@dataclass(frozen=True, slots=True)
class APSeedProfile:
    """Versioned AP fixture profile calculated from committed-shaped rows."""

    dataset_name: str
    profile_version: str
    schema_version: str
    random_seed: int
    normalization_version: str
    dataset_checksum: str
    row_counts: tuple[tuple[str, int], ...]
    tenant_count: int
    referenced_supplier_count: int
    start_date: date
    end_date: date
    expected_exception_records: tuple[tuple[str, tuple[str, ...]], ...]
    expected_exclusion_records: tuple[tuple[str, tuple[str, ...]], ...]
    scenario_labels: tuple[tuple[str, tuple[str, ...]], ...]

    def as_dict(self) -> dict[str, object]:
        """Return deterministic JSON suitable for a reviewed seed artifact."""
        return {
            "dataset_name": self.dataset_name,
            "profile_version": self.profile_version,
            "schema_version": self.schema_version,
            "random_seed": self.random_seed,
            "normalization_version": self.normalization_version,
            "dataset_checksum": self.dataset_checksum,
            "row_counts": dict(self.row_counts),
            "tenant_count": self.tenant_count,
            "referenced_supplier_count": self.referenced_supplier_count,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "expected_exception_records": {
                name: list(records) for name, records in self.expected_exception_records
            },
            "expected_exception_counts": {
                name: len(records) for name, records in self.expected_exception_records
            },
            "expected_exclusion_records": {
                name: list(records) for name, records in self.expected_exclusion_records
            },
            "expected_exclusion_counts": {
                name: len(records) for name, records in self.expected_exclusion_records
            },
            "scenario_labels": {
                record_id: list(labels) for record_id, labels in self.scenario_labels
            },
        }


@dataclass(frozen=True, slots=True)
class APSeedReport:
    """Safe AP seed result returned by the reusable package API."""

    database_name: str
    legal_entity_count: int
    business_unit_count: int
    purchase_order_count: int
    invoice_count: int
    payment_count: int
    dataset_checksum: str
    profile: APSeedProfile


class APSeedValidationError(RuntimeError):
    """Raised before commit when AP fixture rows violate the frozen seed contract."""


@dataclass(frozen=True, slots=True)
class _InvoiceSpec:
    id: int
    source_record_id: str
    supplier_id: int
    legal_entity_id: int
    business_unit_id: int
    invoice_number: str
    invoice_date: date
    gross_amount: str
    currency: str
    purchase_order_id: int | None
    status: str = "POSTED"
    no_po_exception_ref: str | None = None
    no_po_exception_approved: bool = False


@dataclass(frozen=True, slots=True)
class _PaymentSpec:
    id: int
    source_record_id: str
    invoice_id: int
    legal_entity_id: int
    business_unit_id: int
    payment_date: date
    payment_amount: str
    currency: str
    status: str = "SETTLED"


def seed_accounts_payable_demo_database(
    database_url: str,
    *,
    base_directory: Path | None = None,
    random_seed: int = DEFAULT_AP_RANDOM_SEED,
    reset: bool = True,
    bootstrap_quality_if_empty: bool = True,
) -> APSeedReport:
    """Migrate and atomically replace only AP rows, preserving all Quality rows."""
    if random_seed != DEFAULT_AP_RANDOM_SEED:
        raise APSeedValidationError(
            f"AP v1 uses the frozen synthetic seed {DEFAULT_AP_RANDOM_SEED}"
        )
    logger.info(
        "accounts_payable_seed_started",
        extra={"dataset_name": AP_DATASET_NAME, "random_seed": random_seed},
    )
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
    _bootstrap_quality_if_required(
        database_url,
        base_directory=base_directory,
        enabled=bootstrap_quality_if_empty,
    )

    connection = DatabaseConnection(
        database_url,
        read_only=False,
        base_directory=base_directory,
    )
    try:
        with connection.session() as session:
            _require_seed_suppliers(session)
            if reset:
                _delete_ap_rows(session)
            elif _count(session, LegalEntity) > 0:
                raise APSeedValidationError(
                    "AP demo tables already contain rows; rerun with reset enabled"
                )
            session.add_all(_legal_entities())
            session.flush()
            session.add_all(_business_units())
            session.flush()
            session.add_all(_purchase_orders())
            session.flush()
            session.add_all(_invoices())
            session.flush()
            session.add_all(_payments())
            session.flush()
            profile = _build_profile(session, random_seed=random_seed)
            _validate_seed(session, profile)
            report = APSeedReport(
                database_name=connection.database_name,
                legal_entity_count=_count(session, LegalEntity),
                business_unit_count=_count(session, BusinessUnit),
                purchase_order_count=_count(session, PurchaseOrder),
                invoice_count=_count(session, Invoice),
                payment_count=_count(session, Payment),
                dataset_checksum=profile.dataset_checksum,
                profile=profile,
            )
        logger.info(
            "accounts_payable_seed_completed",
            extra={
                "dataset_name": AP_DATASET_NAME,
                "database_name": report.database_name,
                "dataset_checksum": report.dataset_checksum,
                "invoice_count": report.invoice_count,
            },
        )
        return report
    finally:
        connection.dispose()


def _bootstrap_quality_if_required(
    database_url: str,
    *,
    base_directory: Path | None,
    enabled: bool,
) -> None:
    connection = DatabaseConnection(database_url, read_only=True, base_directory=base_directory)
    try:
        with connection.session() as session:
            supplier_count = _count(session, Supplier)
    finally:
        connection.dispose()
    if supplier_count == 0 and enabled:
        from copilot.tools.database.seed import seed_demo_database

        seed_demo_database(
            database_url,
            base_directory=base_directory,
            reset=False,
        )


def _require_seed_suppliers(session: Session) -> None:
    required = {
        ("TENANT-DEMO", 1),
        ("TENANT-DEMO", 2),
        ("TENANT-DEMO", 3),
        ("TENANT-DEMO", 4),
        ("TENANT-A", 16),
        ("TENANT-A", 17),
    }
    actual = set(
        session.execute(
            select(Supplier.tenant_id, Supplier.id).where(Supplier.id.in_({1, 2, 3, 4, 16, 17}))
        ).all()
    )
    if actual != required:
        raise APSeedValidationError(
            "AP seed requires the reviewed six tenant-scoped Supplier Quality suppliers"
        )


def _legal_entities() -> list[LegalEntity]:
    return [
        LegalEntity(
            id=1001,
            tenant_id="TENANT-DEMO",
            legal_entity_code="LE-CN-01",
            name="Demo Manufacturing China",
            base_currency="CNY",
            status="ACTIVE",
            created_at=AP_SEED_TIMESTAMP,
        ),
        LegalEntity(
            id=1002,
            tenant_id="TENANT-DEMO",
            legal_entity_code="LE-US-01",
            name="Demo Manufacturing USA",
            base_currency="USD",
            status="ACTIVE",
            created_at=AP_SEED_TIMESTAMP,
        ),
        LegalEntity(
            id=2001,
            tenant_id="TENANT-A",
            legal_entity_code="LE-DE-01",
            name="Walkthrough Manufacturing GmbH",
            base_currency="EUR",
            status="ACTIVE",
            created_at=AP_SEED_TIMESTAMP,
        ),
    ]


def _business_units() -> list[BusinessUnit]:
    return [
        BusinessUnit(
            id=1101,
            tenant_id="TENANT-DEMO",
            legal_entity_id=1001,
            business_unit_code="BU-CN-PROC",
            name="China Procurement",
            status="ACTIVE",
            created_at=AP_SEED_TIMESTAMP,
        ),
        BusinessUnit(
            id=1102,
            tenant_id="TENANT-DEMO",
            legal_entity_id=1001,
            business_unit_code="BU-CN-MFG",
            name="China Manufacturing",
            status="ACTIVE",
            created_at=AP_SEED_TIMESTAMP,
        ),
        BusinessUnit(
            id=1201,
            tenant_id="TENANT-DEMO",
            legal_entity_id=1002,
            business_unit_code="BU-US-PROC",
            name="US Procurement",
            status="ACTIVE",
            created_at=AP_SEED_TIMESTAMP,
        ),
        BusinessUnit(
            id=2101,
            tenant_id="TENANT-A",
            legal_entity_id=2001,
            business_unit_code="BU-DE-PROC",
            name="Germany Procurement",
            status="ACTIVE",
            created_at=AP_SEED_TIMESTAMP,
        ),
    ]


def _purchase_orders() -> list[PurchaseOrder]:
    definitions = (
        (10001, 1, 1001, 1101, "10000.0000", "CNY", "SINGLE_INVOICE"),
        (10002, 1, 1001, 1101, "5000.0000", "CNY", "SINGLE_INVOICE"),
        (10003, 1, 1001, 1101, "5000.0000", "CNY", "SINGLE_INVOICE"),
        (10004, 1, 1001, 1101, "5100.0000", "CNY", "SINGLE_INVOICE"),
        (10005, 2, 1002, 1201, "1000.0000", "USD", "SINGLE_INVOICE"),
        (10006, 2, 1002, 1201, "1000.0000", "USD", "SINGLE_INVOICE"),
        (10007, 2, 1002, 1201, "1000.0000", "USD", "SINGLE_INVOICE"),
        (10008, 2, 1002, 1201, "0.0000", "USD", "SINGLE_INVOICE"),
        (10009, 1, 1001, 1101, "2000.0000", "USD", "SINGLE_INVOICE"),
        (10010, 3, 1002, 1201, "3000.0000", "USD", "MULTI_INVOICE"),
        (10011, 3, 1002, 1201, "800.0000", "USD", "SINGLE_INVOICE"),
        (10012, 3, 1002, 1201, "900.0000", "USD", "SINGLE_INVOICE"),
        (10013, 3, 1002, 1201, "700.0000", "USD", "SINGLE_INVOICE"),
        (10014, 3, 1002, 1201, "700.0000", "USD", "SINGLE_INVOICE"),
        (10015, 3, 1002, 1201, "700.0000", "USD", "SINGLE_INVOICE"),
        (10016, 4, 1002, 1201, "600.0000", "USD", "SINGLE_INVOICE"),
        (10017, 4, 1002, 1201, "600.0000", "USD", "SINGLE_INVOICE"),
        (10018, 4, 1002, 1201, "600.0000", "USD", "SINGLE_INVOICE"),
        (10019, 4, 1002, 1201, "500.0000", "USD", "SINGLE_INVOICE"),
        (10020, 4, 1002, 1201, "500.0000", "USD", "SINGLE_INVOICE"),
        (10021, 1, 1001, 1102, "1200.0000", "CNY", "SINGLE_INVOICE"),
        (10022, 1, 1001, 1102, "1300.0000", "CNY", "SINGLE_INVOICE"),
        (20001, 16, 2001, 2101, "2000.0000", "EUR", "SINGLE_INVOICE"),
        (20002, 17, 2001, 2101, "2100.0000", "EUR", "SINGLE_INVOICE"),
    )
    return [
        PurchaseOrder(
            id=identifier,
            tenant_id="TENANT-A" if identifier >= 20000 else "TENANT-DEMO",
            source_system=_SOURCE_SYSTEM,
            source_record_id=f"PO-{identifier}",
            po_number=f"PO-{identifier}",
            supplier_id=supplier_id,
            legal_entity_id=legal_entity_id,
            business_unit_id=business_unit_id,
            order_date=date(2026, 1, 5),
            approved_amount=Decimal(amount),
            currency=currency,
            matching_basis=matching_basis,
            status="APPROVED",
            approved_at=datetime(2026, 1, 6, tzinfo=UTC),
            created_at=AP_SEED_TIMESTAMP,
        )
        for (
            identifier,
            supplier_id,
            legal_entity_id,
            business_unit_id,
            amount,
            currency,
            matching_basis,
        ) in definitions
    ]


def _invoice_specs() -> tuple[_InvoiceSpec, ...]:
    q2 = date(2026, 4, 15)
    return (
        _InvoiceSpec(
            20001, "INV-CLEAN-001", 1, 1001, 1101, "CLEAN-001", q2, "10000", "CNY", 10001, "PAID"
        ),
        _InvoiceSpec(20002, "INV-DUP-001-A", 1, 1001, 1101, "DUP-100", q2, "5000", "CNY", 10002),
        _InvoiceSpec(
            20003, "INV-DUP-001-B", 1, 1001, 1101, " dup / 100 ", q2, "5000", "CNY", 10003
        ),
        _InvoiceSpec(
            20004, "INV-SAME-NUMBER-DIFFERENT", 1, 1001, 1101, "DUP-100", q2, "5100", "CNY", 10004
        ),
        _InvoiceSpec(
            20005,
            "INV-VAR-WITHIN",
            2,
            1002,
            1201,
            "VAR-001",
            date(2026, 5, 1),
            "1040",
            "USD",
            10005,
        ),
        _InvoiceSpec(
            20006,
            "INV-VAR-BOUNDARY",
            2,
            1002,
            1201,
            "VAR-002",
            date(2026, 5, 2),
            "1050",
            "USD",
            10006,
        ),
        _InvoiceSpec(
            20007, "INV-VAR-ABOVE", 2, 1002, 1201, "VAR-003", date(2026, 5, 3), "1060", "USD", 10007
        ),
        _InvoiceSpec(
            20008, "INV-NOPO-BELOW", 2, 1002, 1201, "NOPO-001", date(2026, 5, 4), "900", "USD", None
        ),
        _InvoiceSpec(
            20009,
            "INV-NOPO-ABOVE",
            2,
            1002,
            1201,
            "NOPO-002",
            date(2026, 5, 5),
            "1500",
            "USD",
            None,
        ),
        _InvoiceSpec(
            20010,
            "INV-NOPO-APPROVED",
            2,
            1002,
            1201,
            "NOPO-003",
            date(2026, 5, 6),
            "1500",
            "USD",
            None,
            no_po_exception_ref="AP-EXC-001",
            no_po_exception_approved=True,
        ),
        _InvoiceSpec(
            20011,
            "INV-ONTIME",
            3,
            1002,
            1201,
            "PAY-001",
            date(2026, 6, 1),
            "800",
            "USD",
            10011,
            "PAID",
        ),
        _InvoiceSpec(
            20012,
            "INV-LATE",
            3,
            1002,
            1201,
            "PAY-002",
            date(2026, 6, 2),
            "900",
            "USD",
            10012,
            "PAID",
        ),
        _InvoiceSpec(
            20013,
            "INV-EARLY-WITHIN",
            3,
            1002,
            1201,
            "PAY-003",
            date(2026, 6, 3),
            "700",
            "USD",
            10013,
            "PAID",
        ),
        _InvoiceSpec(
            20014,
            "INV-EARLY-BOUNDARY",
            3,
            1002,
            1201,
            "PAY-004",
            date(2026, 6, 4),
            "700",
            "USD",
            10014,
            "PAID",
        ),
        _InvoiceSpec(
            20015,
            "INV-EARLY-MATERIAL",
            3,
            1002,
            1201,
            "PAY-005",
            date(2026, 6, 5),
            "700",
            "USD",
            10015,
            "PAID",
        ),
        _InvoiceSpec(
            20016,
            "INV-OVER-EXACT",
            4,
            1002,
            1201,
            "OVER-001",
            date(2026, 6, 6),
            "600",
            "USD",
            10016,
            "PAID",
        ),
        _InvoiceSpec(
            20017,
            "INV-OVER-BOUNDARY",
            4,
            1002,
            1201,
            "OVER-002",
            date(2026, 6, 7),
            "600",
            "USD",
            10017,
            "PAID",
        ),
        _InvoiceSpec(
            20018,
            "INV-OVER-ABOVE",
            4,
            1002,
            1201,
            "OVER-003",
            date(2026, 6, 8),
            "600",
            "USD",
            10018,
            "PAID",
        ),
        _InvoiceSpec(
            20019, "INV-PO-ZERO", 2, 1002, 1201, "ZERO-PO", date(2026, 6, 9), "100", "USD", 10008
        ),
        _InvoiceSpec(
            20020,
            "INV-CURRENCY-MISMATCH",
            1,
            1001,
            1101,
            "CUR-001",
            date(2026, 6, 10),
            "2000",
            "CNY",
            10009,
        ),
        _InvoiceSpec(
            20021, "INV-UNPAID", 4, 1002, 1201, "UNPAID-001", date(2026, 6, 11), "500", "USD", 10019
        ),
        _InvoiceSpec(
            20022,
            "INV-MULTI-PAY",
            4,
            1002,
            1201,
            "MULTI-PAY",
            date(2026, 6, 12),
            "500",
            "USD",
            10020,
            "PAID",
        ),
        _InvoiceSpec(
            20023,
            "INV-MULTI-PO",
            3,
            1002,
            1201,
            "MULTI-PO",
            date(2026, 6, 13),
            "3000",
            "USD",
            10010,
        ),
        _InvoiceSpec(
            20024,
            "INV-Q1-CONTROL",
            1,
            1001,
            1102,
            "Q1-001",
            date(2026, 2, 10),
            "1200",
            "CNY",
            10021,
        ),
        _InvoiceSpec(
            20025,
            "INV-Q3-CONTROL",
            1,
            1001,
            1102,
            "Q3-001",
            date(2026, 8, 10),
            "1300",
            "CNY",
            10022,
        ),
        _InvoiceSpec(
            30001,
            "INV-TENANT-A",
            16,
            2001,
            2101,
            "TEN-A-001",
            date(2026, 5, 10),
            "2000",
            "EUR",
            20001,
        ),
        _InvoiceSpec(
            30002,
            "INV-TENANT-A-SECOND",
            17,
            2001,
            2101,
            "TEN-A-002",
            date(2026, 5, 11),
            "2100",
            "EUR",
            20002,
        ),
    )


def _invoices() -> list[Invoice]:
    invoices: list[Invoice] = []
    for spec in _invoice_specs():
        gross = Decimal(spec.gross_amount).quantize(Decimal("0.0001"))
        tax = (gross * Decimal("0.1000")).quantize(Decimal("0.0001"))
        invoice_date = spec.invoice_date
        invoices.append(
            Invoice(
                id=spec.id,
                tenant_id="TENANT-A" if spec.id >= 30000 else "TENANT-DEMO",
                source_system=_SOURCE_SYSTEM,
                source_record_id=spec.source_record_id,
                supplier_id=spec.supplier_id,
                legal_entity_id=spec.legal_entity_id,
                business_unit_id=spec.business_unit_id,
                invoice_number=spec.invoice_number,
                normalized_invoice_number=normalize_invoice_number(spec.invoice_number),
                invoice_type="STANDARD",
                invoice_date=invoice_date,
                posting_date=invoice_date + timedelta(days=1),
                currency=spec.currency,
                net_amount=gross - tax,
                tax_amount=tax,
                gross_amount=gross,
                purchase_order_id=spec.purchase_order_id,
                payment_terms_days=30,
                due_date=invoice_date + timedelta(days=30),
                no_po_exception_ref=spec.no_po_exception_ref,
                no_po_exception_approved=spec.no_po_exception_approved,
                status=spec.status,
                created_at=AP_SEED_TIMESTAMP,
            )
        )
    return invoices


def _payment_specs() -> tuple[_PaymentSpec, ...]:
    invoice_dates = {spec.id: spec.invoice_date for spec in _invoice_specs()}

    def due(invoice_id: int) -> date:
        return invoice_dates[invoice_id] + timedelta(days=30)

    return (
        _PaymentSpec(40001, "PAY-CLEAN-001", 20001, 1001, 1101, due(20001), "10000", "CNY"),
        _PaymentSpec(40002, "PAY-ONTIME", 20011, 1002, 1201, due(20011), "800", "USD"),
        _PaymentSpec(
            40003, "PAY-LATE", 20012, 1002, 1201, due(20012) + timedelta(days=5), "900", "USD"
        ),
        _PaymentSpec(
            40004,
            "PAY-EARLY-WITHIN",
            20013,
            1002,
            1201,
            due(20013) - timedelta(days=9),
            "700",
            "USD",
        ),
        _PaymentSpec(
            40005,
            "PAY-EARLY-BOUNDARY",
            20014,
            1002,
            1201,
            due(20014) - timedelta(days=10),
            "700",
            "USD",
        ),
        _PaymentSpec(
            40006,
            "PAY-EARLY-MATERIAL",
            20015,
            1002,
            1201,
            due(20015) - timedelta(days=15),
            "700",
            "USD",
        ),
        _PaymentSpec(40007, "PAY-OVER-EXACT", 20016, 1002, 1201, due(20016), "600", "USD"),
        _PaymentSpec(40008, "PAY-OVER-BOUNDARY", 20017, 1002, 1201, due(20017), "605", "USD"),
        _PaymentSpec(40009, "PAY-OVER-ABOVE", 20018, 1002, 1201, due(20018), "605.01", "USD"),
        _PaymentSpec(40010, "PAY-MULTI-A", 20022, 1002, 1201, due(20022), "250", "USD"),
        _PaymentSpec(
            40011, "PAY-MULTI-B", 20022, 1002, 1201, due(20022) + timedelta(days=1), "250", "USD"
        ),
    )


def _payments() -> list[Payment]:
    return [
        Payment(
            id=spec.id,
            tenant_id="TENANT-DEMO",
            source_system=_SOURCE_SYSTEM,
            source_record_id=spec.source_record_id,
            invoice_id=spec.invoice_id,
            legal_entity_id=spec.legal_entity_id,
            business_unit_id=spec.business_unit_id,
            payment_date=spec.payment_date,
            payment_amount=Decimal(spec.payment_amount).quantize(Decimal("0.0001")),
            currency=spec.currency,
            status=spec.status,
            created_at=AP_SEED_TIMESTAMP,
        )
        for spec in _payment_specs()
    ]


def _build_profile(session: Session, *, random_seed: int) -> APSeedProfile:
    entities = tuple(session.scalars(select(LegalEntity).order_by(LegalEntity.id)))
    units = tuple(session.scalars(select(BusinessUnit).order_by(BusinessUnit.id)))
    purchase_orders = tuple(session.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id)))
    invoices = tuple(session.scalars(select(Invoice).order_by(Invoice.id)))
    payments = tuple(session.scalars(select(Payment).order_by(Payment.id)))
    row_counts = (
        ("legal_entities", len(entities)),
        ("business_units", len(units)),
        ("purchase_orders", len(purchase_orders)),
        ("invoices", len(invoices)),
        ("payments", len(payments)),
    )
    return APSeedProfile(
        dataset_name=AP_DATASET_NAME,
        profile_version=AP_SEED_PROFILE_VERSION,
        schema_version=AP_SCHEMA_VERSION,
        random_seed=random_seed,
        normalization_version=INVOICE_NUMBER_NORMALIZATION_VERSION,
        dataset_checksum=_dataset_checksum(entities, units, purchase_orders, invoices, payments),
        row_counts=row_counts,
        tenant_count=len({invoice.tenant_id for invoice in invoices}),
        referenced_supplier_count=len(
            {(invoice.tenant_id, invoice.supplier_id) for invoice in invoices}
        ),
        start_date=min(invoice.invoice_date for invoice in invoices),
        end_date=max(invoice.invoice_date for invoice in invoices),
        expected_exception_records=tuple(sorted(AP_EXCEPTION_ORACLE.items())),
        expected_exclusion_records=tuple(sorted(AP_EXCLUSION_ORACLE.items())),
        scenario_labels=tuple(sorted(AP_SCENARIO_LABELS.items())),
    )


def _dataset_checksum(
    entities: Sequence[LegalEntity],
    units: Sequence[BusinessUnit],
    purchase_orders: Sequence[PurchaseOrder],
    invoices: Sequence[Invoice],
    payments: Sequence[Payment],
) -> str:
    payload = {
        "legal_entities": [
            [
                item.id,
                item.tenant_id,
                item.legal_entity_code,
                item.name,
                item.base_currency,
                item.status,
            ]
            for item in entities
        ],
        "business_units": [
            [
                item.id,
                item.tenant_id,
                item.legal_entity_id,
                item.business_unit_code,
                item.name,
                item.status,
            ]
            for item in units
        ],
        "purchase_orders": [
            [
                item.id,
                item.tenant_id,
                item.source_record_id,
                item.po_number,
                item.supplier_id,
                item.legal_entity_id,
                item.business_unit_id,
                item.order_date.isoformat(),
                _decimal(item.approved_amount),
                item.currency,
                item.matching_basis,
                item.status,
            ]
            for item in purchase_orders
        ],
        "invoices": [
            [
                item.id,
                item.tenant_id,
                item.source_record_id,
                item.supplier_id,
                item.legal_entity_id,
                item.business_unit_id,
                item.invoice_number,
                item.normalized_invoice_number,
                item.invoice_type,
                item.invoice_date.isoformat(),
                item.posting_date.isoformat(),
                item.currency,
                _decimal(item.net_amount),
                _decimal(item.tax_amount),
                _decimal(item.gross_amount),
                item.purchase_order_id,
                item.payment_terms_days,
                item.due_date.isoformat(),
                item.no_po_exception_ref,
                item.no_po_exception_approved,
                item.status,
            ]
            for item in invoices
        ],
        "payments": [
            [
                item.id,
                item.tenant_id,
                item.source_record_id,
                item.invoice_id,
                item.legal_entity_id,
                item.business_unit_id,
                item.payment_date.isoformat(),
                _decimal(item.payment_amount),
                item.currency,
                item.status,
            ]
            for item in payments
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_seed(session: Session, profile: APSeedProfile) -> None:
    if dict(profile.row_counts) != {
        "legal_entities": 3,
        "business_units": 4,
        "purchase_orders": 24,
        "invoices": 27,
        "payments": 11,
    }:
        raise APSeedValidationError("AP row counts do not match the reviewed seed contract")
    if profile.tenant_count != 2 or profile.referenced_supplier_count != 6:
        raise APSeedValidationError("AP tenant or supplier coverage is incomplete")
    if profile.start_date != date(2026, 2, 10) or profile.end_date != date(2026, 8, 10):
        raise APSeedValidationError("AP fixture must cover Q1 through Q3 2026")
    normalized_mismatch = sum(
        invoice.normalized_invoice_number != normalize_invoice_number(invoice.invoice_number)
        for invoice in session.scalars(select(Invoice))
    )
    if normalized_mismatch:
        raise APSeedValidationError("Invoice normalization output drifted")
    record_ids = set(session.scalars(select(Invoice.source_record_id)))
    expected_records = set(AP_SCENARIO_LABELS)
    if record_ids != expected_records:
        raise APSeedValidationError("AP scenario record set drifted")
    payment_counts = Counter(session.scalars(select(Payment.invoice_id)))
    if payment_counts[20022] != 2 or payment_counts[20021] != 0:
        raise APSeedValidationError("Payment-cardinality exclusion fixtures drifted")
    _validate_parent_consistency(session)
    exceptions, exclusions = _recompute_seed_oracles(session)
    if exceptions != AP_EXCEPTION_ORACLE:
        raise APSeedValidationError("AP exception oracle does not match independent queries")
    if exclusions != AP_EXCLUSION_ORACLE:
        raise APSeedValidationError("AP exclusion oracle does not match independent queries")


def _recompute_seed_oracles(
    session: Session,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Recompute fixture labels from persisted facts without using the declared oracles."""
    invoices = tuple(session.scalars(select(Invoice).order_by(Invoice.id)))
    purchase_orders = {item.id: item for item in session.scalars(select(PurchaseOrder))}
    payments_by_invoice: defaultdict[int, list[Payment]] = defaultdict(list)
    for payment in session.scalars(select(Payment).order_by(Payment.id)):
        if payment.status != "VOID":
            payments_by_invoice[payment.invoice_id].append(payment)

    duplicate_members: defaultdict[tuple[object, ...], list[Invoice]] = defaultdict(list)
    for invoice in invoices:
        if (
            invoice.invoice_type == "STANDARD"
            and invoice.status in {"POSTED", "PAID"}
            and invoice.gross_amount > 0
            and invoice.normalized_invoice_number
        ):
            duplicate_members[
                (
                    invoice.tenant_id,
                    invoice.supplier_id,
                    invoice.normalized_invoice_number,
                    invoice.gross_amount,
                    invoice.currency,
                    invoice.invoice_date,
                )
            ].append(invoice)
    duplicates = sorted(
        member.source_record_id
        for members in duplicate_members.values()
        if len(members) >= 2
        for member in sorted(members, key=lambda item: item.source_record_id)[1:]
    )

    variances: list[str] = []
    missing_purchase_orders: list[str] = []
    late_payments: list[str] = []
    early_payments: list[str] = []
    overpayments: list[str] = []
    zero_purchase_orders: list[str] = []
    currency_mismatches: list[str] = []
    multi_invoice_purchase_orders: list[str] = []
    unpaid_invoices: list[str] = []
    multiple_payments: list[str] = []

    for invoice in invoices:
        if invoice.purchase_order_id is None:
            valid_exception = bool(
                invoice.no_po_exception_approved
                and invoice.no_po_exception_ref
                and invoice.no_po_exception_ref.strip()
            )
            if (
                invoice.invoice_type == "STANDARD"
                and invoice.gross_amount >= _FIXTURE_PO_REQUIRED_AMOUNT
                and not valid_exception
            ):
                missing_purchase_orders.append(invoice.source_record_id)
        else:
            purchase_order = purchase_orders[invoice.purchase_order_id]
            if purchase_order.approved_amount == 0:
                zero_purchase_orders.append(invoice.source_record_id)
            elif invoice.currency != purchase_order.currency:
                currency_mismatches.append(invoice.source_record_id)
            elif purchase_order.matching_basis != "SINGLE_INVOICE":
                multi_invoice_purchase_orders.append(invoice.source_record_id)
            elif invoice.invoice_type == "STANDARD":
                absolute_variance = abs(invoice.gross_amount - purchase_order.approved_amount)
                variance_rate = absolute_variance / purchase_order.approved_amount
                if variance_rate > _FIXTURE_VARIANCE_RATE:
                    variances.append(invoice.source_record_id)

        relevant_payments = payments_by_invoice[invoice.id]
        settled_payments = [payment for payment in relevant_payments if payment.status == "SETTLED"]
        if not relevant_payments:
            if invoice.invoice_type == "STANDARD" and invoice.status in {"POSTED", "PAID"}:
                unpaid_invoices.append(invoice.source_record_id)
            continue
        if len(relevant_payments) != 1 or len(settled_payments) != 1:
            multiple_payments.append(invoice.source_record_id)
            continue
        payment = settled_payments[0]
        if payment.currency != invoice.currency:
            continue
        delta_days = (payment.payment_date - invoice.due_date).days
        if delta_days > 0:
            late_payments.append(invoice.source_record_id)
        if -delta_days >= _FIXTURE_MATERIAL_EARLY_DAYS:
            early_payments.append(invoice.source_record_id)
        if payment.payment_amount - invoice.gross_amount > _FIXTURE_OVERPAYMENT_TOLERANCE:
            overpayments.append(invoice.source_record_id)

    exceptions = {
        "EXACT_DUPLICATE_INVOICE": tuple(duplicates),
        "PO_AMOUNT_VARIANCE": tuple(sorted(variances)),
        "MISSING_REQUIRED_PO": tuple(sorted(missing_purchase_orders)),
        "LATE_PAYMENT": tuple(sorted(late_payments)),
        "MATERIAL_EARLY_PAYMENT": tuple(sorted(early_payments)),
        "OVERPAYMENT": tuple(sorted(overpayments)),
    }
    exclusions = {
        "PO_AMOUNT_ZERO": tuple(sorted(zero_purchase_orders)),
        "AP_CURRENCY_MISMATCH_EXCLUDED": tuple(sorted(currency_mismatches)),
        "MULTI_INVOICE_MATCHING_UNSUPPORTED": tuple(sorted(multi_invoice_purchase_orders)),
        "UNPAID_INVOICE": tuple(sorted(unpaid_invoices)),
        "MULTIPLE_PAYMENT_EXCLUSION": tuple(sorted(multiple_payments)),
    }
    return exceptions, exclusions


def _validate_parent_consistency(session: Session) -> None:
    po_by_id = {item.id: item for item in session.scalars(select(PurchaseOrder))}
    invoice_by_id = {item.id: item for item in session.scalars(select(Invoice))}
    for invoice in invoice_by_id.values():
        if invoice.purchase_order_id is None:
            continue
        purchase_order = po_by_id[invoice.purchase_order_id]
        if (
            invoice.tenant_id != purchase_order.tenant_id
            or invoice.supplier_id != purchase_order.supplier_id
            or invoice.legal_entity_id != purchase_order.legal_entity_id
            or invoice.business_unit_id != purchase_order.business_unit_id
        ):
            raise APSeedValidationError("Invoice and purchase-order scope is inconsistent")
    for payment in session.scalars(select(Payment)):
        invoice = invoice_by_id[payment.invoice_id]
        if (
            payment.tenant_id != invoice.tenant_id
            or payment.legal_entity_id != invoice.legal_entity_id
            or payment.business_unit_id != invoice.business_unit_id
        ):
            raise APSeedValidationError("Payment and invoice scope is inconsistent")


def _delete_ap_rows(session: Session) -> None:
    for model in (Payment, Invoice, PurchaseOrder, BusinessUnit, LegalEntity):
        session.execute(delete(model))


def _decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")), "f")


def _count(session: Session, model: type[Any]) -> int:
    scalar = session.scalar(select(func.count()).select_from(model))
    return int(scalar or 0)


def ap_schema_has_no_sensitive_payment_columns(database_url: str) -> bool:
    """Return whether the migrated v1 payment table excludes forbidden bank identifiers."""
    connection = DatabaseConnection(database_url, read_only=True)
    try:
        columns = {item["name"] for item in inspect(connection.engine).get_columns("payments")}
    finally:
        connection.dispose()
    forbidden = {"payment_reference", "bank_account", "iban", "swift", "tax_id"}
    return columns.isdisjoint(forbidden)


__all__ = [
    "AP_DATASET_NAME",
    "AP_EXCEPTION_ORACLE",
    "AP_EXCLUSION_ORACLE",
    "AP_SCHEMA_VERSION",
    "AP_SCENARIO_LABELS",
    "AP_SEED_PROFILE_VERSION",
    "APSeedProfile",
    "APSeedReport",
    "APSeedValidationError",
    "DEFAULT_AP_RANDOM_SEED",
    "ap_schema_has_no_sensitive_payment_columns",
    "seed_accounts_payable_demo_database",
]
