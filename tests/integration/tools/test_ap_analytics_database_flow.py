"""Real AP Database Tool to deterministic Analytics and Calculation Evidence flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from copilot.contracts import JsonObject, ToolCall
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.tools.analytics import AccountsPayableAnalyticsTool
from copilot.tools.analytics.ap_schemas import APAnalyticsOperation, APDatabaseTemplate
from copilot.tools.base import ToolExecutionContext
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.ap_seed import AP_EXCEPTION_ORACLE, seed_accounts_payable_demo_database
from copilot.tools.database.models import Invoice
from tests.unit.tools.analytics.ap_helpers import (
    add_policy_evidence,
    aggregation_arguments,
    analytics_context,
    detection_arguments,
)

pytestmark = pytest.mark.integration


def _database_arguments(template: APDatabaseTemplate) -> JsonObject:
    return JsonObject(
        {
            "query_template_id": template.value,
            "parameters": {
                "tenant_id": "TENANT-DEMO",
                "start_date": "2026-04-01",
                "end_date": "2026-06-30",
                "supplier_ids": [],
                "legal_entity_ids": ["LE-CN-01", "LE-US-01"],
                "business_unit_ids": [],
                "currency_scope": [],
            },
            "schema_version": "accounts_payable.v1",
            "snapshot_at": "2026-10-01T00:00:00+00:00",
            "row_limit": 50000,
        }
    )


def _database_context(
    tool: DatabaseTool,
    arguments: JsonObject,
    template: APDatabaseTemplate,
) -> ToolExecutionContext:
    call = ToolCall(
        tool_call_id=f"TC-{template.value}",
        task_id="T-AP-INTEGRATION",
        step_id=f"S-{template.value}",
        tool_name="database_query",
        tool_version=tool.definition.tool_version,
        input=arguments,
        idempotency_key=f"IDEMPOTENCY-{template.value}",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        tenant_id="TENANT-DEMO",
        user_id="U-FINANCE-001",
    )
    return ToolExecutionContext(
        call=call,
        tenant_id="TENANT-DEMO",
        user_id="U-FINANCE-001",
        roles=("finance_analyst",),
        scopes=("finance:ap.detail",),
        purpose="accounts_payable_analysis.v1",
    )


def test_real_q2_fixture_matches_frozen_exception_oracle_and_summary(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'ap-stage5.db'}"
    seed_accounts_payable_demo_database(database_url)
    database = DatabaseTool.accounts_payable(DatabaseConnection(database_url, read_only=True))
    ledger = InMemoryEvidenceLedger(max_items_per_task=500)
    rule_snapshot, _ = add_policy_evidence(ledger, task_id="T-AP-INTEGRATION")
    datasets: dict[APDatabaseTemplate, dict[str, JsonValue]] = {}
    try:
        for template in APDatabaseTemplate:
            arguments = _database_arguments(template)
            context = _database_context(database, arguments, template)
            execution = database.execute(arguments, context)
            evidence = ledger.record(context.call, execution.evidence)[0]
            rows = cast(list[JsonMapping], execution.output.root["rows"])
            assert len(rows) == 23
            datasets[template] = {
                "template_id": template.value,
                "template_version": template.value,
                "evidence_id": evidence.evidence_id,
                "dataset_checksum": evidence.content.checksum,
                "rows": cast(JsonValue, rows),
            }
    finally:
        database.close()

    operation_templates = {
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION: (
            APDatabaseTemplate.DUPLICATE_CANDIDATES
        ),
        APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION: (
            APDatabaseTemplate.INVOICE_PO_VARIANCE
        ),
        APAnalyticsOperation.MISSING_PO_DETECTION: APDatabaseTemplate.INVOICE_PO_VARIANCE,
        APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION: APDatabaseTemplate.PAYMENT_TERMS,
        APAnalyticsOperation.OVERPAYMENT_DETECTION: APDatabaseTemplate.PAYMENT_AMOUNT,
    }
    analytics = AccountsPayableAnalyticsTool(ledger)
    observed: dict[str, list[str]] = {}
    calculation_ids: list[str] = []
    for index, (operation, template) in enumerate(operation_templates.items(), start=1):
        arguments = detection_arguments(
            operation,
            datasets[APDatabaseTemplate.INVOICE_POPULATION],
            datasets[template],
            rule_snapshot,
        )
        context = analytics_context(
            arguments,
            task_id="T-AP-INTEGRATION",
            call_id=f"TC-AP-DETECTION-{index}",
        )
        result = analytics.execute(arguments, context)
        stored = ledger.record(context.call, result.evidence)
        calculation_ids.extend(item.evidence_id for item in stored)
        records = cast(list[JsonMapping], result.output.root["records"])
        for record in records:
            observed.setdefault(cast(str, record["exception_type"]), []).append(
                cast(str, record["invoice_record_key"])
            )

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            source_by_key = {
                str(invoice_id): source_record_id
                for invoice_id, source_record_id in session.execute(
                    select(Invoice.id, Invoice.source_record_id).where(
                        Invoice.tenant_id == "TENANT-DEMO"
                    )
                )
            }
    finally:
        engine.dispose()
    observed_sources = {
        exception_type: tuple(sorted(source_by_key[key] for key in record_keys))
        for exception_type, record_keys in observed.items()
    }
    assert observed_sources == {
        exception_type: tuple(sorted(record_ids))
        for exception_type, record_ids in AP_EXCEPTION_ORACLE.items()
    }

    summary_arguments = aggregation_arguments(
        APAnalyticsOperation.EXCEPTION_SUMMARY,
        datasets[APDatabaseTemplate.INVOICE_POPULATION],
        rule_snapshot,
        tuple(calculation_ids),
    )
    summary = analytics.execute(
        summary_arguments,
        analytics_context(
            summary_arguments,
            task_id="T-AP-INTEGRATION",
            call_id="TC-AP-SUMMARY",
        ),
    )
    metrics = cast(JsonMapping, summary.output.root["metrics"])
    assert metrics["invoice_count"] == 23
    assert metrics["exception_invoice_count"] == 7
    assert metrics["exception_rate"] == "0.30434783"
    assert metrics["exception_invoice_amount_by_currency"] == {
        "CNY": "5000.0000",
        "USD": "5460.0000",
    }
    assert metrics["finding_count"] == 5
    assert metrics["warning_count"] == 2
    output_checksum = cast(str, summary.output.root["output_checksum"])
    assert output_checksum.startswith("sha256:")
    assert all(draft.source_reference.input_evidence_ids for draft in summary.evidence)
