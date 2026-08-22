"""Accounts Payable v1 schema and deterministic seed regression coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from copilot.tools.database import DatabaseConnection
from copilot.tools.database.ap_seed import (
    AP_DATASET_NAME,
    AP_EXCEPTION_ORACLE,
    AP_EXCLUSION_ORACLE,
    AP_SCHEMA_VERSION,
    AP_SEED_PROFILE_VERSION,
    DEFAULT_AP_RANDOM_SEED,
    APSeedValidationError,
    ap_schema_has_no_sensitive_payment_columns,
    seed_accounts_payable_demo_database,
)
from copilot.tools.database.normalization import normalize_invoice_number
from copilot.tools.database.seed import seed_demo_database


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'ap-seed.db'}"


def _table_snapshot(
    database_url: str,
    table_names: tuple[str, ...],
) -> tuple[tuple[tuple[object, ...], ...], ...]:
    connection = DatabaseConnection(database_url, read_only=True)
    try:
        with connection.engine.connect() as database:
            return tuple(
                tuple(
                    tuple(row)
                    for row in database.execute(
                        text(f"SELECT * FROM {table_name} ORDER BY id")  # noqa: S608
                    )
                )
                for table_name in table_names
            )
    finally:
        connection.dispose()


def test_invoice_number_normalization_is_frozen_and_bounded() -> None:
    assert normalize_invoice_number("  ａｂＣ / 12-3 ") == "ABC123"
    with pytest.raises(ValueError, match="empty"):
        normalize_invoice_number(" / - ")
    with pytest.raises(ValueError, match="128"):
        normalize_invoice_number("A" * 129)


def test_ap_seed_is_repeatable_and_preserves_supplier_quality_rows(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)
    quality_before = seed_demo_database(database_url)
    quality_tables = (
        "suppliers",
        "supplier_deviations",
        "incoming_inspections",
        "corrective_actions",
    )
    ap_tables = (
        "legal_entities",
        "business_units",
        "purchase_orders",
        "invoices",
        "payments",
    )
    quality_rows_before = _table_snapshot(database_url, quality_tables)

    first = seed_accounts_payable_demo_database(database_url)
    second = seed_accounts_payable_demo_database(database_url)
    ap_rows_before_quality_reset = _table_snapshot(database_url, ap_tables)
    quality_after = seed_demo_database(database_url)
    quality_rows_after = _table_snapshot(database_url, quality_tables)
    ap_rows_after_quality_reset = _table_snapshot(database_url, ap_tables)

    assert first == second
    assert first.dataset_checksum == (
        "e920b4b13403831b0c4e7150edea452736f5c278cb2ed272b98c25da66b02f91"
    )
    assert first.profile.dataset_name == AP_DATASET_NAME
    assert first.profile.profile_version == AP_SEED_PROFILE_VERSION
    assert first.profile.schema_version == AP_SCHEMA_VERSION
    assert first.profile.random_seed == DEFAULT_AP_RANDOM_SEED
    assert dict(first.profile.row_counts) == {
        "legal_entities": 3,
        "business_units": 4,
        "purchase_orders": 24,
        "invoices": 27,
        "payments": 11,
    }
    assert first.profile.tenant_count == 2
    assert first.profile.referenced_supplier_count == 6
    assert dict(first.profile.expected_exception_records) == dict(AP_EXCEPTION_ORACLE)
    assert dict(first.profile.expected_exclusion_records) == dict(AP_EXCLUSION_ORACLE)
    assert quality_before.supplier_count == 17
    assert quality_before.inspection_count == 5000
    assert quality_after.dataset_checksum == quality_before.dataset_checksum
    assert quality_rows_after == quality_rows_before
    assert ap_rows_after_quality_reset == ap_rows_before_quality_reset


def test_ap_seed_rejects_an_unreviewed_seed(tmp_path: Path) -> None:
    with pytest.raises(APSeedValidationError, match="frozen synthetic seed"):
        seed_accounts_payable_demo_database(
            _database_url(tmp_path),
            random_seed=DEFAULT_AP_RANDOM_SEED + 1,
        )


def test_ap_schema_has_scoped_keys_checks_indexes_and_minimized_payment_data(
    tmp_path: Path,
) -> None:
    database_url = _database_url(tmp_path)
    seed_accounts_payable_demo_database(database_url)
    connection = DatabaseConnection(database_url, read_only=True)
    try:
        inspector = inspect(connection.engine)
        assert {
            "legal_entities",
            "business_units",
            "purchase_orders",
            "invoices",
            "payments",
        }.issubset(inspector.get_table_names())
        assert {item["name"] for item in inspector.get_columns("payments")} == {
            "id",
            "tenant_id",
            "source_system",
            "source_record_id",
            "invoice_id",
            "legal_entity_id",
            "business_unit_id",
            "payment_date",
            "payment_amount",
            "currency",
            "status",
            "created_at",
        }
        invoice_uniques = {
            tuple(item["column_names"]) for item in inspector.get_unique_constraints("invoices")
        }
        assert ("tenant_id", "supplier_id", "normalized_invoice_number") not in invoice_uniques
        assert ("tenant_id", "source_system", "source_record_id") in invoice_uniques
        assert {
            "ck_invoices_amount_balance",
            "ck_invoices_no_po_exception",
            "ck_invoices_normalized_number",
        }.issubset({item["name"] for item in inspector.get_check_constraints("invoices")})
        payment_foreign_keys = {
            tuple(item["constrained_columns"]) for item in inspector.get_foreign_keys("payments")
        }
        assert ("tenant_id", "invoice_id") in payment_foreign_keys
        assert (
            "tenant_id",
            "legal_entity_id",
            "business_unit_id",
        ) in payment_foreign_keys
    finally:
        connection.dispose()
    assert ap_schema_has_no_sensitive_payment_columns(database_url)


def test_composite_supplier_fk_rejects_cross_tenant_purchase_order(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)
    seed_accounts_payable_demo_database(database_url)
    connection = DatabaseConnection(database_url, read_only=False)
    try:
        with pytest.raises(IntegrityError), connection.engine.begin() as transaction:
            transaction.execute(
                text(
                    """
                    INSERT INTO purchase_orders (
                        id, tenant_id, source_system, source_record_id, po_number,
                        supplier_id, legal_entity_id, business_unit_id, order_date,
                        approved_amount, currency, matching_basis, status, approved_at, created_at
                    ) VALUES (
                        99999, 'TENANT-A', 'TEST', 'CROSS-TENANT', 'PO-CROSS-TENANT',
                        1, 2001, 2101, '2026-06-01', 10.0000, 'EUR',
                        'SINGLE_INVOICE', 'APPROVED',
                        '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
                    )
                    """
                )
            )
    finally:
        connection.dispose()
