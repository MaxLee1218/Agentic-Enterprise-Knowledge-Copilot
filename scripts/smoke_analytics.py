"""Run a local Database -> Analytics -> Calculation Evidence smoke flow."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from pydantic import JsonValue

from copilot.contracts import JsonObject, ToolCall, ToolResultStatus
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import InMemoryToolAuditRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.services.execution import ExecutionContext
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.analytics import AnalyticsTool
from copilot.tools.cancellation import CancellationToken
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.seed import seed_demo_database


def main() -> int:
    """Execute the governed local smoke flow and print its safe result summary."""
    with TemporaryDirectory(prefix="copilot-analytics-smoke-") as temporary_directory:
        database_path = Path(temporary_directory) / "quality.db"
        database_url = f"sqlite:///{database_path}"
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
                call_id="TC-SMOKE-DB",
                step_id="S-SMOKE-DB",
                tool_name=database.definition.tool_name,
                tool_version=database.definition.tool_version,
                arguments=_database_arguments(),
            )
            database_result = executor.execute(
                database_call,
                _context(database_call),
            )
            if database_result.status is not ToolResultStatus.SUCCESS:
                print("Analytics smoke failed during database_query")
                return 1
            assert database_result.output is not None
            database_evidence = ledger.get(
                database_result.evidence_ids[0],
                task_id=database_call.task_id,
                tenant_id=database_call.tenant_id,
            )
            rows = cast(list[JsonMapping], database_result.output.root["rows"])
            analytics_call = _call(
                call_id="TC-SMOKE-AN",
                step_id="S-SMOKE-AN",
                tool_name=analytics.definition.tool_name,
                tool_version=analytics.definition.tool_version,
                arguments=JsonObject(
                    {
                        "dataset": cast(JsonValue, rows),
                        "dataset_evidence_id": database_evidence.evidence_id,
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
                ),
            )
            analytics_result = executor.execute(
                analytics_call,
                _context(analytics_call),
            )
            if analytics_result.status is not ToolResultStatus.SUCCESS:
                error_code = (
                    analytics_result.error.error_code
                    if analytics_result.error is not None
                    else "UNKNOWN"
                )
                print(f"Analytics smoke failed: error_code={error_code}")
                return 1
            assert analytics_result.output is not None
            calculation = ledger.get(
                analytics_result.evidence_ids[0],
                task_id=analytics_call.task_id,
                tenant_id=analytics_call.tenant_id,
            )
            formulas = calculation.source_reference.reference.root["formulas"]
            print(f"operation={analytics_result.tool_name}")
            print(f"formula={json.dumps(formulas, sort_keys=True)}")
            print(f"result={json.dumps(analytics_result.output.root['metrics'], sort_keys=True)}")
            print(f"evidence_id={calculation.evidence_id}")
            print(f"latency_ms={analytics_result.latency_ms}")
            return 0
        finally:
            executor.close()
            database.close()


def _database_arguments() -> JsonObject:
    return JsonObject(
        {
            "query_template_id": "supplier_quality_summary_v1",
            "parameters": {
                "tenant_id": "TENANT-DEMO",
                "start_date": "2026-01-01",
                "end_date": "2026-03-31",
                "supplier_ids": ["SUP-001"],
            },
            "schema_version": "quality.v1",
            "snapshot_at": "2026-04-01T00:00:00+00:00",
            "row_limit": 10_000,
        }
    )


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
        task_id="T-SMOKE-ANALYTICS",
        step_id=step_id,
        tool_name=tool_name,
        tool_version=tool_version,
        input=arguments,
        idempotency_key=f"IDEMPOTENCY-{call_id}",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=15),
        tenant_id="TENANT-DEMO",
        user_id="U-SMOKE",
    )


def _context(call: ToolCall) -> ExecutionContext:
    return ExecutionContext(
        task_id=call.task_id,
        trace_id="TRACE-ANALYTICS-SMOKE",
        step_id=call.step_id,
        user_id=call.user_id,
        tenant_id=call.tenant_id,
        roles=("quality_analyst",),
        scopes=("task:execute", "data:quality.v1"),
        data_scope=("quality.v1",),
        purpose="supplier_quality_analysis.v1",
        authentication_source="explicit_smoke_identity",
        is_demo_identity=True,
        authenticated=True,
        deadline_at=call.deadline_at,
        approval_required=False,
        approval_id=call.approval_id,
        cancellation=CancellationToken(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
