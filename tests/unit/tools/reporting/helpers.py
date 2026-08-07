"""Deterministic Report Tool fixtures."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from copilot.contracts import (
    EvidenceContent,
    EvidenceItem,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    ReportLanguage,
    ToolCall,
)
from copilot.contracts.base import JsonMapping
from copilot.persistence.artifact_repository import LocalArtifactRepository
from copilot.tools.analytics.schemas import AnalyticsResult
from copilot.tools.base import ToolExecutionContext
from copilot.tools.reporting import ReportComposer, ReportRequest, ReportTool, ReportValidator
from copilot.tools.reporting.schemas import ReportFormat, ReportScope

FIXED_NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
TASK_ID = "T-REPORT-001"

METRICS: tuple[JsonMapping, ...] = (
    {
        "metric": "defect_count",
        "dimensions": {"supplier_id": "S-100"},
        "value": 25,
        "unit": "count",
        "numerator": 25,
        "denominator": None,
    },
    {
        "metric": "inspected_count",
        "dimensions": {"supplier_id": "S-100"},
        "value": 2000,
        "unit": "count",
        "numerator": 2000,
        "denominator": None,
    },
    {
        "metric": "defect_rate",
        "dimensions": {"supplier_id": "S-100"},
        "value": 0.0125,
        "unit": "ratio",
        "numerator": 25,
        "denominator": 2000,
    },
)


class DictEvidenceReader:
    """Minimal task-local Evidence reader."""

    def __init__(self, items: tuple[EvidenceItem, ...]) -> None:
        self._items = {item.evidence_id: item for item in items}

    def get(self, evidence_id: str) -> EvidenceItem:
        """Return a fixture Evidence item."""
        return self._items[evidence_id]


class SequentialIds:
    """Predictable Artifact identifier factory."""

    def __init__(self) -> None:
        self._value = 0

    def new_id(self, prefix: str) -> str:
        """Return a deterministic identifier."""
        self._value += 1
        return f"{prefix}-{self._value:04d}"


def evidence_items(*, task_id: str = TASK_ID) -> tuple[EvidenceItem, ...]:
    """Return document, database, and calculation Evidence with complete lineage."""
    return (
        EvidenceItem(
            evidence_id="E-DOC-001",
            task_id=task_id,
            step_id="S-KB-001",
            tool_call_id="TC-KB-001",
            source_type=EvidenceType.DOCUMENT,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "document_id": "quality-policy",
                        "document_version": "v4",
                        "chunk_id": "chunk-7",
                        "index_snapshot_id": "IDX-001",
                    }
                )
            ),
            content=EvidenceContent(
                data=JsonObject(
                    {"excerpt": ("Defect rate is defect count divided by inspected count.")}
                ),
                classification="INTERNAL",
                checksum="sha256:doc",
            ),
            timestamp=FIXED_NOW,
        ),
        EvidenceItem(
            evidence_id="E-DB-001",
            task_id=task_id,
            step_id="S-DB-001",
            tool_call_id="TC-DB-001",
            source_type=EvidenceType.DATABASE,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "query_fingerprint": "sha256:query",
                        "snapshot_at": FIXED_NOW.isoformat(),
                        "row_count": 3,
                    }
                )
            ),
            content=EvidenceContent(
                data=JsonObject({"row_count": 3}),
                classification="CONFIDENTIAL",
                checksum="sha256:dataset",
            ),
            timestamp=FIXED_NOW,
        ),
        EvidenceItem(
            evidence_id="E-CALC-001",
            task_id=task_id,
            step_id="S-AN-001",
            tool_call_id="TC-AN-001",
            source_type=EvidenceType.CALCULATION,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "formulas": {
                            "defect_count": "sum(defect_count)",
                            "inspected_count": "sum(inspected_count)",
                            "defect_rate": "sum(defect_count) / sum(inspected_count)",
                        },
                        "engine_version": "quality_metrics.v1",
                    }
                ),
                input_evidence_ids=("E-DB-001",),
            ),
            content=EvidenceContent(
                data=JsonObject({"metrics": list(METRICS), "warnings": []}),
                classification="CONFIDENTIAL",
                checksum="sha256:calculation",
            ),
            timestamp=FIXED_NOW,
        ),
    )


def report_request(report_format: ReportFormat = ReportFormat.JSON) -> ReportRequest:
    """Return a complete frozen report request."""
    result = AnalyticsResult.model_validate(
        {
            "metrics": list(METRICS),
            "warnings": [],
            "input_row_count": 3,
            "dataset_checksum": "sha256:dataset",
            "calculation_version": "quality_metrics.v1",
            "empty_result": False,
        }
    )
    return ReportRequest(
        task_id=TASK_ID,
        scope=ReportScope(
            year=2026,
            quarter=2,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            supplier_ids=("S-100",),
        ),
        analysis_result=result,
        evidence_refs=("E-DOC-001", "E-DB-001", "E-CALC-001"),
        template_version="supplier_quality_report.v1",
        format=report_format,
        language=ReportLanguage.EN_US,
    )


def report_context(request: ReportRequest, *, task_id: str = TASK_ID) -> ToolExecutionContext:
    """Bind one request to a trusted report invocation."""
    arguments = JsonObject(request.model_dump(mode="json"))
    return ToolExecutionContext(
        call=ToolCall(
            tool_call_id="TC-RP-001",
            task_id=task_id,
            step_id="S-RP-001",
            tool_name="report_generator",
            tool_version=ReportTool.definition.tool_version,
            input=arguments,
            idempotency_key=f"IDEMPOTENCY-{request.format.value}",
            approval_id=None,
            deadline_at=FIXED_NOW + timedelta(minutes=1),
            tenant_id="TENANT-A",
            user_id="U-QUALITY",
        )
    )


def report_tool(
    tmp_path: Path, *, max_size_bytes: int = 10 * 1024 * 1024
) -> tuple[ReportTool, LocalArtifactRepository]:
    """Create a Report Tool with deterministic local dependencies."""
    items = evidence_items()
    reader = DictEvidenceReader(items)
    repository = LocalArtifactRepository(
        tmp_path,
        clock=lambda: FIXED_NOW,
        max_size_bytes=max_size_bytes,
    )
    tool = ReportTool(
        evidence_reader=reader,
        artifact_store=repository,
        ids=SequentialIds(),
        clock=lambda: FIXED_NOW,
        composer=ReportComposer(reader, clock=lambda: FIXED_NOW),
        validator=ReportValidator(),
    )
    return tool, repository


__all__ = [
    "DictEvidenceReader",
    "FIXED_NOW",
    "TASK_ID",
    "evidence_items",
    "report_context",
    "report_request",
    "report_tool",
]
