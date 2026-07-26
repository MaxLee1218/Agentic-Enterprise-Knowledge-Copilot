"""Controlled builders shared by Analytics Tool tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    EvidenceContent,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    ToolCall,
)
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.tools.analytics import AnalyticsTool
from copilot.tools.analytics.validators import canonical_checksum
from copilot.tools.base import EvidenceDraft, ToolExecutionContext

DEFAULT_ROWS: list[JsonMapping] = [
    {
        "supplier_id": "S-100",
        "period": "2026-01",
        "inspected_count": 1000,
        "defect_count": 10,
    },
    {
        "supplier_id": "S-100",
        "period": "2026-02",
        "inspected_count": 1000,
        "defect_count": 15,
    },
]


def analytics_arguments(
    rows: list[JsonMapping] | None = None,
    *,
    evidence_id: str = "E-DB-001",
    checksum: str | None = None,
    metrics: list[str] | None = None,
    group_by: list[str] | None = None,
) -> JsonObject:
    """Return one schema-valid frozen analytics input."""
    selected_rows = DEFAULT_ROWS if rows is None else rows
    dataset_checksum = checksum or canonical_checksum(selected_rows)
    return JsonObject(
        {
            "dataset": cast(JsonValue, selected_rows),
            "dataset_evidence_id": evidence_id,
            "dataset_checksum": dataset_checksum,
            "metrics": cast(
                JsonValue,
                metrics
                or [
                    "defect_count",
                    "inspected_count",
                    "defect_rate",
                    "period_over_period_trend",
                ],
            ),
            "group_by": cast(
                JsonValue,
                group_by if group_by is not None else ["supplier_id", "period"],
            ),
            "engine_version": "quality_metrics.v1",
        }
    )


def analytics_context(
    arguments: JsonObject,
    *,
    task_id: str = "T-AN-001",
    call_id: str = "TC-AN-001",
) -> ToolExecutionContext:
    """Bind analytics arguments to one trusted tool invocation."""
    return ToolExecutionContext(
        call=ToolCall(
            tool_call_id=call_id,
            task_id=task_id,
            step_id="S-AN-001",
            tool_name="analysis_engine",
            tool_version=AnalyticsTool.definition.tool_version,
            input=arguments,
            idempotency_key=f"IDEMPOTENCY-{call_id}",
            approval_id=None,
            deadline_at=datetime.now(UTC) + timedelta(seconds=10),
            tenant_id="TENANT-A",
            user_id="U-QUALITY",
        )
    )


def ledger_with_database_evidence(
    rows: list[JsonMapping] | None = None,
    *,
    evidence_id: str = "E-DB-001",
    task_id: str = "T-AN-001",
) -> InMemoryEvidenceLedger:
    """Create a ledger containing checksum-bound DATABASE evidence."""
    selected_rows = DEFAULT_ROWS if rows is None else rows
    checksum = canonical_checksum(selected_rows)
    ledger = InMemoryEvidenceLedger(
        id_factory=lambda: evidence_id,
        clock=lambda: datetime(2026, 7, 26, tzinfo=UTC),
    )
    database_call = ToolCall(
        tool_call_id="TC-DB-001",
        task_id=task_id,
        step_id="S-DB-001",
        tool_name="database_query",
        tool_version="1.0.0-test",
        input=JsonObject({"dataset": cast(JsonValue, selected_rows)}),
        idempotency_key="IDEMPOTENCY-DB-001",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
        tenant_id="TENANT-A",
        user_id="U-QUALITY",
    )
    ledger.record(
        database_call,
        (
            EvidenceDraft(
                source_type=EvidenceType.DATABASE,
                source_reference=EvidenceSourceReference(
                    reference=JsonObject({"query_fingerprint": "sha256:test"})
                ),
                content=EvidenceContent(
                    data=JsonObject({"row_count": len(selected_rows)}),
                    classification="CONFIDENTIAL",
                    checksum=checksum,
                ),
            ),
        ),
    )
    return ledger
