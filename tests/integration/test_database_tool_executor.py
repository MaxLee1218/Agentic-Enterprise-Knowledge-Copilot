"""Real SQLite Database Tool integration through the governed executor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from copilot.contracts import EvidenceType, ToolCall, ToolResultStatus
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.audit_repository import InMemoryToolAuditRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)
from copilot.services.workflows.models import SupplierQualityCommand
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.seed import seed_demo_database
from tests.execution_helpers import execution_context
from tests.unit.database.helpers import database_arguments, database_context
from tests.workflow_helpers import build_test_container

pytestmark = pytest.mark.integration


def test_executor_runs_real_database_tool_and_records_evidence(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'executor.db'}"
    seed_demo_database(database_url)
    tool = DatabaseTool(DatabaseConnection(database_url, read_only=True))
    registry = ToolRegistry()
    registry.register(tool)
    ledger = InMemoryEvidenceLedger()
    audit = InMemoryToolAuditRepository()
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=audit,
    )
    arguments = database_arguments()
    call = ToolCall(
        tool_call_id="TC-DB-INTEGRATION",
        task_id="T-DB-INTEGRATION",
        step_id="S-DB-INTEGRATION",
        tool_name=tool.definition.tool_name,
        tool_version=tool.definition.tool_version,
        input=arguments,
        idempotency_key="IDEMPOTENCY-DB-INTEGRATION",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
        tenant_id="TENANT-DEMO",
        user_id="U-QUALITY",
    )
    try:
        result = executor.execute(call, execution_context(call))
    finally:
        executor.close()
        tool.close()

    assert result.status is ToolResultStatus.SUCCESS
    assert result.output is not None
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert len(result.evidence_ids) == 1
    evidence = ledger.get(result.evidence_ids[0], task_id=call.task_id, tenant_id=call.tenant_id)
    assert evidence.source_type is EvidenceType.DATABASE
    assert evidence.task_id == call.task_id
    assert audit.list(tenant_id=call.tenant_id)[0].status is ToolResultStatus.SUCCESS


def test_full_workflow_can_use_real_database_tool(tmp_path: Path) -> None:
    database_path = tmp_path / "workflow.db"
    database_url = f"sqlite:///{database_path}"
    seed_demo_database(database_url)
    command = SupplierQualityCommand(
        supplier_id="SUP-001",
        material_id="MAT-001",
        time_range="2026-Q1",
    )

    with build_test_container(
        tmp_path / "artifacts",
        database_url=database_url,
        use_real_database=True,
    ) as container:
        execution = container.service.execute(command)

    assert execution.task_result.final_status.value == "COMPLETED"
    database_result = execution.step_results[1]
    assert database_result.status.value == "SUCCESS"
    assert database_result.output is not None
    assert database_result.output.root["row_count"] == 3
    assert any(item.source_type is EvidenceType.DATABASE for item in execution.evidence)


def test_real_templates_enforce_tenant_supplier_quarter_and_trend_scope(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'scope.db'}"
    seed_demo_database(database_url)
    tool = DatabaseTool(DatabaseConnection(database_url, read_only=True))
    try:
        primary = tool.execute(
            database_arguments(
                supplier_ids=["SUP-005"],
                start_date="2026-07-01",
                end_date="2026-09-30",
            ),
            database_context(),
        )
        trend = tool.execute(
            database_arguments(
                supplier_ids=["SUP-005"],
                start_date="2026-07-01",
                end_date="2026-09-30",
                template_id="supplier_quality_trend_v1",
            ),
            database_context(),
        )
        isolation = tool.execute(
            database_arguments(
                tenant_id="TENANT-A",
                supplier_ids=["SUP-005"],
            ),
            database_context(tenant_id="TENANT-A"),
        )
        walkthrough = tool.execute(
            database_arguments(
                tenant_id="TENANT-A",
                supplier_ids=["S-100"],
            ),
            database_context(tenant_id="TENANT-A"),
        )
    finally:
        tool.close()

    primary_rows = cast(list[JsonMapping], primary.output.root["rows"])
    primary_row_count = cast(int, primary.output.root["row_count"])
    trend_row_count = cast(int, trend.output.root["row_count"])
    assert primary_row_count == 3
    assert {row["period"] for row in primary_rows} == {
        "2026-07",
        "2026-08",
        "2026-09",
    }
    assert trend_row_count > primary_row_count
    assert isolation.output.root["empty_result"] is True
    assert walkthrough.output.root["row_count"] == 3


def test_natural_language_workflow_uses_real_business_database_end_to_end(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'natural-real.db'}"
    seed_demo_database(database_url)
    caller = TrustedCallerContext(
        user_id="U-QUALITY",
        tenant_id="TENANT-DEMO",
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
    )

    with build_test_container(
        tmp_path / "natural-real-artifacts",
        database_url=database_url,
        use_real_database=True,
        llm_provider=OfflineMockLLM(),
    ) as container:
        execution = container.task_service.submit(
            NaturalLanguageTaskCommand(
                task=(
                    "Analyze SUP-005 supplier quality for Q2 2026, compare the quality "
                    "policy, and generate a JSON management report."
                ),
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.API,
                trace_id="TRACE-REAL-BUSINESS-DATABASE",
            ),
            caller,
        )

    assert execution.task_result.final_status.value == "COMPLETED"
    database_result = execution.step_results[1]
    analytics_result = execution.step_results[2]
    assert database_result.output is not None
    assert database_result.output.root["row_count"] == 3
    rows = database_result.output.root["rows"]
    assert isinstance(rows, list)
    assert {row["supplier_id"] for row in rows if isinstance(row, dict)} == {"SUP-005"}
    assert analytics_result.output is not None
    assert analytics_result.output.root["empty_result"] is False
    assert execution.verification_result is not None
    assert execution.verification_result.status.value == "PASSED"
    assert {item.source_type for item in execution.evidence} == {
        EvidenceType.DOCUMENT,
        EvidenceType.DATABASE,
        EvidenceType.CALCULATION,
    }
