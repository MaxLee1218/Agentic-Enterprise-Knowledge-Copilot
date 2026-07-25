"""Real SQLite Database Tool integration through the governed executor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from copilot.contracts import EvidenceType, ToolCall, ToolResultStatus
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import InMemoryToolAuditRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.services.workflows.models import SupplierQualityCommand
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.seed import seed_demo_database
from tests.unit.database.helpers import database_arguments
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
        result = executor.execute(call)
    finally:
        executor.close()
        tool.close()

    assert result.status is ToolResultStatus.SUCCESS
    assert result.output is not None
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert len(result.evidence_ids) == 1
    evidence = ledger.get(result.evidence_ids[0])
    assert evidence.source_type is EvidenceType.DATABASE
    assert evidence.task_id == call.task_id
    assert audit.list()[0].status is ToolResultStatus.SUCCESS


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
