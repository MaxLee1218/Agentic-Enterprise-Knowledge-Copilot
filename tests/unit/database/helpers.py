"""Controlled database test builders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue

from copilot.contracts import JsonObject, ToolCall
from copilot.tools.base import ToolExecutionContext
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.seed import seed_demo_database


def seeded_tool(tmp_path: Path) -> DatabaseTool:
    """Create a tool over one isolated deterministically seeded SQLite file."""
    database_path = tmp_path / "demo.db"
    database_url = f"sqlite:///{database_path}"
    seed_demo_database(database_url)
    return DatabaseTool(DatabaseConnection(database_url, read_only=True))


def database_arguments(
    *,
    tenant_id: str = "TENANT-DEMO",
    supplier_ids: list[str] | None = None,
    start_date: str = "2026-01-01",
    end_date: str = "2026-03-31",
    row_limit: int = 10000,
    template_id: str = "supplier_quality_summary_v1",
    schema_version: str = "quality.v1",
) -> JsonObject:
    """Return one schema-valid frozen Database Tool input."""
    selected_supplier_ids: list[JsonValue] = list(
        supplier_ids if supplier_ids is not None else ["SUP-001"]
    )
    return JsonObject(
        {
            "query_template_id": template_id,
            "parameters": {
                "tenant_id": tenant_id,
                "start_date": start_date,
                "end_date": end_date,
                "supplier_ids": selected_supplier_ids,
            },
            "schema_version": schema_version,
            "snapshot_at": "2026-04-01T00:00:00+00:00",
            "row_limit": row_limit,
        }
    )


def database_context(*, tenant_id: str = "TENANT-DEMO") -> ToolExecutionContext:
    """Return trusted execution context matching the input tenant."""
    call = ToolCall(
        tool_call_id="TC-DB-001",
        task_id="T-DB-001",
        step_id="S-DB-001",
        tool_name="database_query",
        tool_version=DatabaseTool.definition.tool_version,
        input=database_arguments(tenant_id=tenant_id),
        idempotency_key="IDEMPOTENCY-DB-001",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
        tenant_id=tenant_id,
        user_id="U-QUALITY-001",
    )
    return ToolExecutionContext(call=call)
