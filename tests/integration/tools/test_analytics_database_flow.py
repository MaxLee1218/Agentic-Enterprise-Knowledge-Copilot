"""Real Database Tool to Analytics Tool calculation-evidence integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from copilot.contracts import EvidenceType, JsonObject, ToolCall, ToolResultStatus
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import InMemoryToolAuditRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.analytics import AnalyticsTool
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.seed import seed_demo_database
from tests.execution_helpers import execution_context
from tests.unit.database.helpers import database_arguments


def _call(
    *,
    call_id: str,
    step_id: str,
    tool_name: str,
    tool_version: str,
    arguments: JsonObject,
) -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        task_id="T-ANALYTICS-FLOW",
        step_id=step_id,
        tool_name=tool_name,
        tool_version=tool_version,
        input=arguments,
        idempotency_key=f"IDEMPOTENCY-{call_id}",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=15),
        tenant_id="TENANT-DEMO",
        user_id="U-QUALITY",
    )


def test_database_output_becomes_traceable_calculation_evidence(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'analytics-flow.db'}"
    seed_demo_database(database_url)
    database = DatabaseTool(DatabaseConnection(database_url, read_only=True))
    ledger = InMemoryEvidenceLedger()
    analytics = AnalyticsTool(ledger)
    registry = ToolRegistry()
    registry.register(database)
    registry.register(analytics)
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=InMemoryToolAuditRepository(),
    )
    try:
        database_call = _call(
            call_id="TC-DB-FLOW",
            step_id="S-DB-FLOW",
            tool_name=database.definition.tool_name,
            tool_version=database.definition.tool_version,
            arguments=database_arguments(),
        )
        database_result = executor.execute(database_call, execution_context(database_call))
        assert database_result.status is ToolResultStatus.SUCCESS
        assert database_result.output is not None
        database_evidence_id = database_result.evidence_ids[0]
        database_evidence = ledger.get(
            database_evidence_id,
            task_id=database_call.task_id,
            tenant_id=database_call.tenant_id,
        )
        rows = cast(list[JsonMapping], database_result.output.root["rows"])
        analytics_arguments = JsonObject(
            {
                "dataset": cast(JsonValue, rows),
                "dataset_evidence_id": database_evidence_id,
                "dataset_checksum": database_evidence.content.checksum,
                "metrics": [
                    "defect_count",
                    "inspected_count",
                    "defect_rate",
                    "period_over_period_trend",
                ],
                "group_by": ["supplier_id", "period"],
                "engine_version": "quality_metrics.v1",
            }
        )

        analytics_call = _call(
            call_id="TC-AN-FLOW",
            step_id="S-AN-FLOW",
            tool_name=analytics.definition.tool_name,
            tool_version=analytics.definition.tool_version,
            arguments=analytics_arguments,
        )
        analytics_result = executor.execute(analytics_call, execution_context(analytics_call))
    finally:
        executor.close()
        database.close()

    assert analytics_result.status is ToolResultStatus.SUCCESS
    assert analytics_result.output is not None
    assert analytics_result.output.root["input_row_count"] == 3
    calculation = ledger.get(
        analytics_result.evidence_ids[0],
        task_id=analytics_call.task_id,
        tenant_id=analytics_call.tenant_id,
    )
    assert calculation.source_type is EvidenceType.CALCULATION
    assert calculation.source_reference.input_evidence_ids == (database_evidence_id,)
    assert calculation.task_id == database_evidence.task_id
    assert calculation.source_reference.reference.root["dataset_checksum"] == (
        database_evidence.content.checksum
    )
    assert calculation.source_reference.reference.root["formulas"]


def test_quarterly_pattern_oracle_flows_through_real_database_and_analytics(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'quarterly-patterns.db'}"
    seed_report = seed_demo_database(database_url)
    database = DatabaseTool(DatabaseConnection(database_url, read_only=True))
    ledger = InMemoryEvidenceLedger()
    analytics = AnalyticsTool(ledger)
    registry = ToolRegistry()
    registry.register(database)
    registry.register(analytics)
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=InMemoryToolAuditRepository(),
    )
    expected = {
        (item.supplier_code, item.quarter): round(item.defect_rate, 4)
        for item in seed_report.profile.quarterly_quality
        if item.supplier_code in {"SUP-005", "SUP-006"}
    }
    observed: dict[tuple[str, str], float] = {}
    boundaries = {
        1: ("2026-01-01", "2026-03-31"),
        2: ("2026-04-01", "2026-06-30"),
        3: ("2026-07-01", "2026-09-30"),
        4: ("2026-10-01", "2026-12-31"),
    }
    try:
        for supplier_code in ("SUP-005", "SUP-006"):
            for quarter, (start_date, end_date) in boundaries.items():
                suffix = f"{supplier_code}-{quarter}"
                database_call = _call(
                    call_id=f"TC-DB-{suffix}",
                    step_id=f"S-DB-{suffix}",
                    tool_name=database.definition.tool_name,
                    tool_version=database.definition.tool_version,
                    arguments=database_arguments(
                        supplier_ids=[supplier_code],
                        start_date=start_date,
                        end_date=end_date,
                    ),
                )
                database_result = executor.execute(
                    database_call,
                    execution_context(database_call),
                )
                assert database_result.status is ToolResultStatus.SUCCESS
                assert database_result.output is not None
                database_evidence = ledger.get(
                    database_result.evidence_ids[0],
                    task_id=database_call.task_id,
                    tenant_id=database_call.tenant_id,
                )
                rows = cast(list[JsonMapping], database_result.output.root["rows"])
                analytics_arguments = JsonObject(
                    {
                        "dataset": cast(JsonValue, rows),
                        "dataset_evidence_id": database_evidence.evidence_id,
                        "dataset_checksum": database_evidence.content.checksum,
                        "metrics": ["defect_rate"],
                        "group_by": ["supplier_id"],
                        "engine_version": "quality_metrics.v1",
                    }
                )
                analytics_call = _call(
                    call_id=f"TC-AN-{suffix}",
                    step_id=f"S-AN-{suffix}",
                    tool_name=analytics.definition.tool_name,
                    tool_version=analytics.definition.tool_version,
                    arguments=analytics_arguments,
                )
                analytics_result = executor.execute(
                    analytics_call,
                    execution_context(analytics_call),
                )
                assert analytics_result.status is ToolResultStatus.SUCCESS
                assert analytics_result.output is not None
                metrics = analytics_result.output.root["metrics"]
                assert isinstance(metrics, list) and len(metrics) == 1
                metric = metrics[0]
                assert isinstance(metric, dict)
                value = metric["value"]
                assert isinstance(value, float)
                observed[(supplier_code, f"2026-Q{quarter}")] = value
    finally:
        executor.close()
        database.close()

    assert observed == expected
    assert observed[("SUP-005", "2026-Q2")] < observed[("SUP-005", "2026-Q3")]
    assert (
        observed[("SUP-006", "2026-Q1")]
        > observed[("SUP-006", "2026-Q2")]
        > observed[("SUP-006", "2026-Q3")]
        > observed[("SUP-006", "2026-Q4")]
    )
