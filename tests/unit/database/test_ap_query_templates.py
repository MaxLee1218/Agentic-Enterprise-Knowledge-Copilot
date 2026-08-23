"""Stage 4 regression coverage for the five frozen AP database reads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue
from sqlalchemy import select, update

from copilot.contracts import EvidenceType, JsonObject, ToolCall
from copilot.tools.base import ToolExecutionContext
from copilot.tools.database import (
    ACCOUNTS_PAYABLE_DATABASE_CONTRACT_PROFILE,
    DatabaseConnection,
    DatabaseTool,
    SchemaRegistry,
    SQLValidator,
)
from copilot.tools.database.ap_seed import seed_accounts_payable_demo_database
from copilot.tools.database.errors import DatabaseQueryValidationError
from copilot.tools.database.models import CorrectiveAction, Invoice, Supplier
from copilot.tools.database.query_templates import QueryTemplate, QueryTemplateRegistry
from copilot.tools.exceptions import ToolPermissionError, ToolValidationError
from copilot.tools.registry import ToolRegistry

_TEMPLATES = (
    "ap_invoice_population_v1",
    "ap_duplicate_invoice_candidates_v1",
    "ap_invoice_po_variance_v1",
    "ap_payment_terms_v1",
    "ap_payment_amount_v1",
)

_COLUMNS = {
    "ap_invoice_population_v1": (
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_date",
        "posting_date",
        "due_date",
        "net_amount",
        "tax_amount",
        "gross_amount",
        "currency",
        "invoice_status",
        "po_record_key",
        "po_matching_basis",
        "po_status",
        "payment_count",
        "settled_payment_count",
        "eligibility_reason",
    ),
    "ap_duplicate_invoice_candidates_v1": (
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "normalized_invoice_number",
        "invoice_date",
        "gross_amount",
        "currency",
        "invoice_type",
        "invoice_status",
    ),
    "ap_invoice_po_variance_v1": (
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "po_record_key",
        "po_tenant_id",
        "invoice_type",
        "invoice_status",
        "invoice_gross_amount",
        "invoice_currency",
        "po_approved_amount",
        "po_currency",
        "po_matching_basis",
        "po_status",
        "po_supplier_id",
        "po_legal_entity_id",
        "po_business_unit_id",
        "no_po_exception_ref",
        "no_po_exception_approved",
    ),
    "ap_payment_terms_v1": (
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_status",
        "invoice_date",
        "due_date",
        "payment_terms_days",
        "invoice_currency",
        "payment_count",
        "settled_payment_count",
        "payment_date",
        "payment_currency",
        "payment_status",
        "payment_tenant_id",
        "payment_invoice_record_key",
        "payment_legal_entity_id",
        "payment_business_unit_id",
    ),
    "ap_payment_amount_v1": (
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_status",
        "invoice_gross_amount",
        "invoice_currency",
        "payment_count",
        "settled_payment_count",
        "payment_date",
        "payment_amount",
        "payment_currency",
        "payment_status",
        "payment_tenant_id",
        "payment_invoice_record_key",
        "payment_legal_entity_id",
        "payment_business_unit_id",
    ),
}


def _tool(tmp_path: Path) -> DatabaseTool:
    database_url = f"sqlite:///{tmp_path / 'ap-queries.db'}"
    seed_accounts_payable_demo_database(database_url)
    return DatabaseTool.accounts_payable(DatabaseConnection(database_url, read_only=True))


def _arguments(
    template_id: str,
    *,
    tenant_id: str = "TENANT-DEMO",
    supplier_ids: list[str] | None = None,
    legal_entity_ids: list[str] | None = None,
    business_unit_ids: list[str] | None = None,
    currency_scope: list[str] | None = None,
    row_limit: int = 50000,
    start_date: str = "2026-04-01",
    end_date: str = "2026-06-30",
    snapshot_at: str = "2026-10-01T00:00:00+00:00",
) -> JsonObject:
    return JsonObject(
        {
            "query_template_id": template_id,
            "parameters": {
                "tenant_id": tenant_id,
                "start_date": start_date,
                "end_date": end_date,
                "supplier_ids": cast(JsonValue, supplier_ids or []),
                "legal_entity_ids": cast(JsonValue, legal_entity_ids or ["LE-CN-01", "LE-US-01"]),
                "business_unit_ids": cast(JsonValue, business_unit_ids or []),
                "currency_scope": cast(JsonValue, currency_scope or []),
            },
            "schema_version": "accounts_payable.v1",
            "snapshot_at": snapshot_at,
            "row_limit": row_limit,
        }
    )


def _context(
    tool: DatabaseTool,
    arguments: JsonObject,
    *,
    tenant_id: str = "TENANT-DEMO",
    scopes: tuple[str, ...] = ("finance:ap.detail",),
) -> ToolExecutionContext:
    call = ToolCall(
        tool_call_id="TC-AP-DB-001",
        task_id="T-AP-DB-001",
        step_id="S-AP-DB-001",
        tool_name="database_query",
        tool_version=tool.definition.tool_version,
        input=arguments,
        idempotency_key="IDEMPOTENCY-AP-DB-001",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=20),
        tenant_id=tenant_id,
        user_id="U-FINANCE-001",
    )
    return ToolExecutionContext(
        call=call,
        roles=("finance_analyst",),
        scopes=scopes,
        purpose="accounts_payable_analysis.v1",
    )


@pytest.mark.parametrize("template_id", _TEMPLATES)
def test_each_frozen_template_returns_exact_scope_and_columns(
    tmp_path: Path,
    template_id: str,
) -> None:
    tool = _tool(tmp_path)
    arguments = _arguments(template_id)
    try:
        result = tool.execute(arguments, _context(tool, arguments))
    finally:
        tool.close()

    assert result.output.root["row_count"] == 23
    assert result.output.root["empty_result"] is False
    assert result.output.root["truncated"] is False
    columns = result.output.root["columns"]
    assert isinstance(columns, list)
    assert (
        tuple(item["name"] for item in columns if isinstance(item, dict)) == _COLUMNS[template_id]
    )
    rows = result.output.root["rows"]
    assert isinstance(rows, list)
    assert all(isinstance(row, dict) and tuple(row) == _COLUMNS[template_id] for row in rows)


def test_ap_values_are_reproducible_decimal_exact_and_opaque(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    arguments = _arguments("ap_invoice_population_v1")
    try:
        result = tool.execute(arguments, _context(tool, arguments))
    finally:
        tool.close()

    rows = result.output.root["rows"]
    assert isinstance(rows, list)
    first = rows[0]
    assert isinstance(first, dict)
    assert first["invoice_record_key"] == "20001"
    assert first["gross_amount"] == "10000.0000"
    assert first["payment_count"] == 1
    assert "invoice_number" not in first
    assert "po_number" not in first


def test_ap_scope_predicates_empty_result_and_sentinel_truncation(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    cny = _arguments(
        "ap_duplicate_invoice_candidates_v1",
        legal_entity_ids=["LE-CN-01"],
        currency_scope=["CNY"],
    )
    empty = _arguments(
        "ap_duplicate_invoice_candidates_v1",
        supplier_ids=["SUP-NOT-FOUND"],
    )
    truncated = _arguments("ap_duplicate_invoice_candidates_v1", row_limit=1)
    try:
        cny_result = tool.execute(cny, _context(tool, cny))
        empty_result = tool.execute(empty, _context(tool, empty))
        truncated_result = tool.execute(truncated, _context(tool, truncated))
    finally:
        tool.close()

    assert cny_result.output.root["row_count"] == 5
    assert empty_result.output.root["rows"] == []
    assert empty_result.output.root["empty_result"] is True
    assert truncated_result.output.root["row_count"] == 1
    assert truncated_result.output.root["truncated"] is True


def test_ap_query_fingerprint_is_scope_canonical_and_evidence_is_minimized(
    tmp_path: Path,
) -> None:
    tool = _tool(tmp_path)
    first = _arguments(
        "ap_payment_amount_v1",
        supplier_ids=["SUP-001", "SUP-002"],
        legal_entity_ids=["LE-CN-01", "LE-US-01"],
    )
    reordered = _arguments(
        "ap_payment_amount_v1",
        supplier_ids=["SUP-002", "SUP-001"],
        legal_entity_ids=["LE-US-01", "LE-CN-01"],
    )
    try:
        first_result = tool.execute(first, _context(tool, first))
        second_result = tool.execute(reordered, _context(tool, reordered))
    finally:
        tool.close()

    assert (
        first_result.output.root["query_fingerprint"]
        == second_result.output.root["query_fingerprint"]
    )
    assert (
        first_result.evidence[0].source_reference.reference.root["parameter_summary"]
        == second_result.evidence[0].source_reference.reference.root["parameter_summary"]
    )
    evidence = first_result.evidence[0]
    reference = evidence.source_reference.reference.root
    assert evidence.source_type is EvidenceType.DATABASE
    assert reference["template_version"] == "ap_payment_amount_v1"
    assert reference["dataset_checksum"] == evidence.content.checksum
    table_names = reference["table_names"]
    assert isinstance(table_names, list)
    assert table_names == sorted(cast(list[str], table_names))
    assert "sql" not in reference
    assert "rows" not in evidence.content.data.root
    parameter_summary = reference["parameter_summary"]
    assert isinstance(parameter_summary, dict)
    assert "supplier_ids" not in parameter_summary


def test_ap_access_denies_missing_detail_scope_and_cross_tenant_call(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    arguments = _arguments("ap_invoice_population_v1")
    wrong_tenant = _arguments("ap_invoice_population_v1", tenant_id="TENANT-A")
    try:
        with pytest.raises(ToolPermissionError) as missing_scope:
            tool.execute(arguments, _context(tool, arguments, scopes=()))
        assert missing_scope.value.error.error_code == "AP_DETAIL_SCOPE_REQUIRED"
        with pytest.raises(ToolPermissionError) as cross_tenant:
            tool.execute(wrong_tenant, _context(tool, wrong_tenant))
        assert cross_tenant.value.error.error_code == "DATABASE_QUERY_DENIED"
    finally:
        tool.close()


def test_ap_absolute_time_and_snapshot_bounds_fail_closed(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    too_long = _arguments(
        "ap_invoice_population_v1",
        start_date="2025-01-01",
        end_date="2026-01-02",
    )
    stale_snapshot = _arguments(
        "ap_invoice_population_v1",
        snapshot_at="2026-06-01T00:00:00+00:00",
    )
    try:
        with pytest.raises(ToolValidationError, match="must not exceed 366 days"):
            tool.execute(too_long, _context(tool, too_long))
        with pytest.raises(ToolValidationError, match="must cover the complete date range"):
            tool.execute(stale_snapshot, _context(tool, stale_snapshot))
    finally:
        tool.close()


def test_ap_validator_denies_raw_sql_writes_unregistered_tables_and_fields() -> None:
    validator = SQLValidator(SchemaRegistry.accounts_payable())

    with pytest.raises(DatabaseQueryValidationError):
        validator.validate("SELECT * FROM invoices")
    with pytest.raises(DatabaseQueryValidationError):
        validator.validate(cast(Any, update(Invoice).values(status="VOID")))
    with pytest.raises(DatabaseQueryValidationError):
        validator.validate(select(CorrectiveAction.id).limit(1))
    with pytest.raises(DatabaseQueryValidationError):
        validator.validate(select(Supplier.name).limit(1))


def test_ap_tool_denies_template_drift_outside_exact_access_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _tool(tmp_path)
    arguments = _arguments("ap_duplicate_invoice_candidates_v1")
    approved = tool._templates.build(  # noqa: SLF001 - controlled adapter-boundary test
        "ap_duplicate_invoice_candidates_v1",
        filter_supplier_ids=False,
        filter_legal_entity_ids=True,
        filter_business_unit_ids=False,
        filter_currency_scope=False,
    )
    drifted = QueryTemplate(
        template_id=approved.template_id,
        statement=approved.statement.add_columns(Invoice.due_date.label("unexpected_due_date")),
        columns=approved.columns,
    )
    monkeypatch.setattr(
        tool._templates,  # noqa: SLF001 - controlled adapter-boundary test
        "build",
        lambda *_args, **_kwargs: drifted,
    )
    try:
        with pytest.raises(ToolPermissionError) as denied:
            tool.execute(arguments, _context(tool, arguments))
    finally:
        tool.close()

    assert denied.value.error.error_code == "DATABASE_QUERY_DENIED"


def test_ap_schema_registry_exposes_only_frozen_read_model_surface() -> None:
    registry = SchemaRegistry.accounts_payable()

    assert registry.schema_version == "accounts_payable.v1"
    assert registry.list_templates() == tuple(sorted(_TEMPLATES))
    assert registry.list_tables() == (
        "business_units",
        "invoices",
        "legal_entities",
        "payments",
        "purchase_orders",
        "suppliers",
    )
    assert registry.is_column_allowed("invoices", "invoice_number") is False
    assert registry.is_column_allowed("purchase_orders", "po_number") is False
    assert registry.is_column_allowed("payments", "source_record_id") is False


@pytest.mark.parametrize("template_id", _TEMPLATES)
def test_ap_access_profile_matches_exact_validated_physical_lineage(template_id: str) -> None:
    registry = SchemaRegistry.accounts_payable()
    template = QueryTemplateRegistry(registry).build(
        template_id,
        filter_supplier_ids=True,
        filter_legal_entity_ids=True,
        filter_business_unit_ids=True,
        filter_currency_scope=True,
    )
    validated = SQLValidator(registry).validate(template.statement)

    assert registry.access_profile_for_template(template_id) == (
        validated.table_names,
        validated.column_names,
    )


def test_ap_database_contract_profile_is_explicitly_resolvable(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    registry = ToolRegistry()
    try:
        registration = registry.register(
            tool,
            contract_profiles=(ACCOUNTS_PAYABLE_DATABASE_CONTRACT_PROFILE,),
        )
        resolved = registry.get_profile(
            "database_query",
            tool.definition.tool_version,
            ACCOUNTS_PAYABLE_DATABASE_CONTRACT_PROFILE,
        )
    finally:
        tool.close()

    assert registration.contract_profiles == ("accounts_payable_database.v1",)
    assert resolved is tool
    properties = tool.definition.input_schema.root["properties"]
    assert isinstance(properties, dict)
    schema_version = properties["schema_version"]
    assert isinstance(schema_version, dict)
    assert schema_version["const"] == "accounts_payable.v1"
