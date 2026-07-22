"""Offline deterministic implementations of the four frozen v1 tool contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    ArtifactType,
    EvidenceContent,
    EvidenceItem,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    RiskLevel,
    ToolApprovalPolicy,
    ToolDefinition,
    ToolIdempotency,
    ToolTimeout,
)
from copilot.contracts.base import JsonMapping
from copilot.services.workflows.ports import ArtifactStore, EvidenceReader, IdentifierFactory
from copilot.tools.base import EvidenceDraft, ToolExecutionContext, ToolExecutionOutput
from copilot.tools.exceptions import (
    ToolBusinessError,
    ToolExecutionError,
    ToolPermissionError,
    ToolTimeoutError,
)


class MockFailureKind(StrEnum):
    """Deterministic failure categories available to offline and test composition."""

    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    BUSINESS = "BUSINESS"
    PERMISSION = "PERMISSION"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True, slots=True)
class MockBehavior:
    """Call-count based failure injection with no randomness."""

    failure_kind: MockFailureKind | None = None
    fail_first_n_attempts: int = 0
    always_fail: bool = False

    def __post_init__(self) -> None:
        if self.fail_first_n_attempts < 0:
            raise ValueError("fail_first_n_attempts must be non-negative")
        if (self.fail_first_n_attempts or self.always_fail) and self.failure_kind is None:
            raise ValueError("failure_kind is required when failures are configured")

    def should_fail(self, call_count: int) -> bool:
        """Return whether this deterministic invocation should fail."""
        return self.failure_kind is not None and (
            self.always_fail or call_count <= self.fail_first_n_attempts
        )


class _MockToolBase:
    behavior: MockBehavior
    call_count: int
    transient_error_code: str
    timeout_error_code: str

    def _before_execute(self) -> None:
        self.call_count += 1
        if not self.behavior.should_fail(self.call_count):
            return
        kind = self.behavior.failure_kind
        if kind is MockFailureKind.TRANSIENT:
            raise ToolExecutionError(
                error_code=self.transient_error_code,
                message="Mock dependency is temporarily unavailable",
                recoverable=True,
            )
        if kind is MockFailureKind.PERMANENT:
            raise ToolExecutionError(
                error_code="MOCK_PERMANENT_FAILURE",
                message="Mock dependency failed permanently",
                recoverable=False,
            )
        if kind is MockFailureKind.BUSINESS:
            raise ToolBusinessError(
                error_code="MOCK_BUSINESS_FAILURE",
                message="Mock business rule could not satisfy the request",
            )
        if kind is MockFailureKind.PERMISSION:
            raise ToolPermissionError(
                error_code="MOCK_PERMISSION_DENIED",
                message="Mock scope is not authorized",
            )
        if kind is MockFailureKind.TIMEOUT:
            raise ToolTimeoutError(
                error_code=self.timeout_error_code,
                message="Mock dependency timed out",
            )


def _definition(
    *,
    name: str,
    description: str,
    risk: RiskLevel,
    attempt_seconds: int,
    overall_seconds: int,
    input_schema: JsonMapping,
    output_schema: JsonMapping,
) -> ToolDefinition:
    return ToolDefinition(
        tool_name=name,
        tool_version="1.0.0-mock",
        description=description,
        input_schema=JsonObject(input_schema),
        output_schema=JsonObject(output_schema),
        risk_level=risk,
        timeout=ToolTimeout(
            attempt_seconds=attempt_seconds,
            overall_seconds=overall_seconds,
        ),
        approval_policy=ToolApprovalPolicy(
            policy_id=f"{name}-v1-policy",
            trigger_conditions=("restricted_scope",),
            approver_role="quality_data_approver",
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=("normalized_input", "tool_version"),
            reuse_window_seconds=300,
            side_effects="Read-only or atomic internal artifact creation",
        ),
    )


class MockKnowledgeTool(_MockToolBase):
    """Return fixed policy excerpts with one DOCUMENT evidence draft per match."""

    transient_error_code = "KNOWLEDGE_UNAVAILABLE"
    timeout_error_code = "KNOWLEDGE_TIMEOUT"
    definition = _definition(
        name="knowledge_search",
        description="Offline supplier quality policy search; no network access",
        risk=RiskLevel.LOW,
        attempt_seconds=10,
        overall_seconds=25,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "query",
                "tenant_id",
                "collection_ids",
                "supplier_ids",
                "date_range",
                "top_k",
                "index_snapshot_id",
            ],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "tenant_id": {"type": "string", "minLength": 1},
                "collection_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "supplier_ids": {
                    "type": "array",
                    "maxItems": 100,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "date_range": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start", "end"],
                    "properties": {
                        "start": {"type": "string", "format": "date"},
                        "end": {"type": "string", "format": "date"},
                    },
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "index_snapshot_id": {"type": "string", "minLength": 1},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["matches", "match_count", "index_snapshot_id", "empty_result"],
            "properties": {
                "matches": {"type": "array", "maxItems": 20},
                "match_count": {"type": "integer", "minimum": 0},
                "index_snapshot_id": {"type": "string"},
                "empty_result": {"type": "boolean"},
            },
        },
    )

    def __init__(self, behavior: MockBehavior | None = None) -> None:
        self.behavior = behavior or MockBehavior()
        self.call_count = 0

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        self._before_execute()
        snapshot = str(arguments.root["index_snapshot_id"])
        excerpts = (
            (
                "quality-policy",
                "Defect rate is defect count divided by inspected count; "
                "zero denominators are null.",
            ),
            (
                "deviation-process",
                "Quality deviations above the reviewed threshold require documented containment.",
            ),
        )
        matches: list[dict[str, object]] = []
        drafts: list[EvidenceDraft] = []
        for index, (document_id, excerpt) in enumerate(excerpts, start=1):
            checksum = _checksum(excerpt.encode())
            matches.append(
                {
                    "document_id": document_id,
                    "document_version": "v1",
                    "chunk_id": f"chunk-{index}",
                    "excerpt": excerpt,
                    "score": 1.0 - index / 10,
                    "classification": "INTERNAL",
                    "checksum": checksum,
                }
            )
            drafts.append(
                EvidenceDraft(
                    source_type=EvidenceType.DOCUMENT,
                    source_reference=EvidenceSourceReference(
                        reference=JsonObject(
                            {
                                "document_id": document_id,
                                "document_version": "v1",
                                "chunk_id": f"chunk-{index}",
                                "index_snapshot_id": snapshot,
                            }
                        )
                    ),
                    content=EvidenceContent(
                        data=JsonObject({"excerpt": excerpt}),
                        classification="INTERNAL",
                        checksum=checksum,
                    ),
                )
            )
        assert context.call.tool_name == self.definition.tool_name
        return ToolExecutionOutput(
            output=_json_object(
                {
                    "matches": matches,
                    "match_count": len(matches),
                    "index_snapshot_id": snapshot,
                    "empty_result": False,
                }
            ),
            evidence=tuple(drafts),
        )


class MockDatabaseTool(_MockToolBase):
    """Return a fixed supplier-period dataset and one DATABASE evidence draft."""

    transient_error_code = "DATABASE_UNAVAILABLE"
    timeout_error_code = "DATABASE_TIMEOUT"
    definition = _definition(
        name="database_query",
        description="Offline registered supplier quality summary query; no database access",
        risk=RiskLevel.MEDIUM,
        attempt_seconds=10,
        overall_seconds=25,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "query_template_id",
                "parameters",
                "schema_version",
                "snapshot_at",
                "row_limit",
            ],
            "properties": {
                "query_template_id": {
                    "type": "string",
                    "enum": ["supplier_quality_summary_v1", "supplier_quality_trend_v1"],
                },
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tenant_id", "start_date", "end_date", "supplier_ids"],
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "start_date": {"type": "string", "format": "date"},
                        "end_date": {"type": "string", "format": "date"},
                        "supplier_ids": {
                            "type": "array",
                            "maxItems": 100,
                            "uniqueItems": True,
                            "items": {"type": "string"},
                        },
                    },
                },
                "schema_version": {"type": "string", "const": "quality.v1"},
                "snapshot_at": {"type": "string", "format": "date-time"},
                "row_limit": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "columns",
                "rows",
                "row_count",
                "empty_result",
                "truncated",
                "query_fingerprint",
                "snapshot_at",
            ],
            "properties": {
                "columns": {"type": "array"},
                "rows": {"type": "array", "maxItems": 10000},
                "row_count": {"type": "integer", "minimum": 0},
                "empty_result": {"type": "boolean"},
                "truncated": {"type": "boolean"},
                "query_fingerprint": {"type": "string"},
                "snapshot_at": {"type": "string", "format": "date-time"},
            },
        },
    )

    def __init__(self, behavior: MockBehavior | None = None) -> None:
        self.behavior = behavior or MockBehavior()
        self.call_count = 0

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        self._before_execute()
        parameters = cast(dict[str, JsonValue], arguments.root["parameters"])
        raw_supplier_ids = parameters["supplier_ids"] or ["SUP-001"]
        supplier_ids = [str(item) for item in cast(list[JsonValue], raw_supplier_ids)]
        rows: list[JsonMapping] = [
            {
                "supplier_id": supplier_id,
                "period": period,
                "inspected_count": 1000,
                "defect_count": defect_count,
            }
            for supplier_id in supplier_ids
            for period, defect_count in (("P1", 10), ("P2", 15), ("P3", 20))
        ]
        dataset_bytes = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        dataset_checksum = _checksum(dataset_bytes)
        fingerprint = _checksum(
            json.dumps(arguments.root, sort_keys=True, separators=(",", ":")).encode()
        )
        snapshot_at = str(arguments.root["snapshot_at"])
        output = _json_object(
            {
                "columns": [
                    {"name": "supplier_id", "data_type": "string"},
                    {"name": "period", "data_type": "string"},
                    {"name": "inspected_count", "data_type": "integer"},
                    {"name": "defect_count", "data_type": "integer"},
                ],
                "rows": rows,
                "row_count": len(rows),
                "empty_result": not rows,
                "truncated": False,
                "query_fingerprint": fingerprint,
                "snapshot_at": snapshot_at,
            }
        )
        evidence = EvidenceDraft(
            source_type=EvidenceType.DATABASE,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "query_template_id": arguments.root["query_template_id"],
                        "query_fingerprint": fingerprint,
                        "schema_version": arguments.root["schema_version"],
                        "snapshot_at": snapshot_at,
                        "row_count": len(rows),
                    }
                )
            ),
            content=EvidenceContent(
                data=JsonObject(
                    {
                        "row_count": len(rows),
                        "inspected_count": sum(cast(int, row["inspected_count"]) for row in rows),
                        "defect_count": sum(cast(int, row["defect_count"]) for row in rows),
                    }
                ),
                classification="CONFIDENTIAL",
                checksum=dataset_checksum,
            ),
        )
        assert context.call.tool_name == self.definition.tool_name
        return ToolExecutionOutput(output=output, evidence=(evidence,))


class MockAnalyticsTool(_MockToolBase):
    """Compute frozen defect metrics and period trend deterministically."""

    transient_error_code = "ANALYSIS_ENGINE_FAILURE"
    timeout_error_code = "ANALYSIS_TIMEOUT"
    definition = _definition(
        name="analysis_engine",
        description="Offline deterministic quality_metrics.v1 implementation",
        risk=RiskLevel.LOW,
        attempt_seconds=15,
        overall_seconds=25,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "dataset",
                "dataset_evidence_id",
                "dataset_checksum",
                "metrics",
                "group_by",
                "engine_version",
            ],
            "properties": {
                "dataset": {"type": "array", "maxItems": 10000},
                "dataset_evidence_id": {"type": "string"},
                "dataset_checksum": {"type": "string"},
                "metrics": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "enum": [
                            "defect_count",
                            "inspected_count",
                            "defect_rate",
                            "period_over_period_trend",
                        ],
                    },
                },
                "group_by": {
                    "type": "array",
                    "maxItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": ["supplier_id", "period"]},
                },
                "engine_version": {"type": "string", "const": "quality_metrics.v1"},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "metrics",
                "warnings",
                "input_row_count",
                "dataset_checksum",
                "calculation_version",
                "empty_result",
            ],
            "properties": {
                "metrics": {"type": "array"},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "input_row_count": {"type": "integer", "minimum": 0},
                "dataset_checksum": {"type": "string"},
                "calculation_version": {"type": "string"},
                "empty_result": {"type": "boolean"},
            },
        },
    )

    def __init__(self, behavior: MockBehavior | None = None) -> None:
        self.behavior = behavior or MockBehavior()
        self.call_count = 0
        self.received_evidence_ids: list[str] = []

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        self._before_execute()
        rows = cast(list[JsonMapping], arguments.root["dataset"])
        source_id = str(arguments.root["dataset_evidence_id"])
        self.received_evidence_ids.append(source_id)
        inspected = sum(cast(int, row["inspected_count"]) for row in rows)
        defects = sum(cast(int, row["defect_count"]) for row in rows)
        defect_rate = defects / inspected if inspected else None
        first_rate = (
            cast(int, rows[0]["defect_count"]) / cast(int, rows[0]["inspected_count"])
            if rows
            else None
        )
        last_rate = (
            cast(int, rows[-1]["defect_count"]) / cast(int, rows[-1]["inspected_count"])
            if rows
            else None
        )
        trend = last_rate - first_rate if last_rate is not None and first_rate is not None else None
        requested = [str(item) for item in cast(list[JsonValue], arguments.root["metrics"])]
        values: dict[str, tuple[float | None, float | None, float | None, str]] = {
            "defect_count": (float(defects), float(defects), None, "count"),
            "inspected_count": (float(inspected), float(inspected), None, "count"),
            "defect_rate": (defect_rate, float(defects), float(inspected), "ratio"),
            "period_over_period_trend": (trend, last_rate, first_rate, "ratio_delta"),
        }
        metrics: list[JsonMapping] = [
            {
                "metric": name,
                "dimensions": {"scope": "all_suppliers"},
                "value": values[name][0],
                "unit": values[name][3],
                "numerator": values[name][1],
                "denominator": values[name][2],
            }
            for name in requested
        ]
        warnings = ["No rows were available for calculation"] if not rows else []
        output = _json_object(
            {
                "metrics": metrics,
                "warnings": warnings,
                "input_row_count": len(rows),
                "dataset_checksum": arguments.root["dataset_checksum"],
                "calculation_version": "quality_metrics.v1",
                "empty_result": not rows,
            }
        )
        evidence = EvidenceDraft(
            source_type=EvidenceType.CALCULATION,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "formula": "defect_count / inspected_count",
                        "engine_version": "quality_metrics.v1",
                        "dataset_checksum": arguments.root["dataset_checksum"],
                    }
                ),
                input_evidence_ids=(source_id,),
            ),
            content=EvidenceContent(
                data=_json_object({"metrics": metrics, "warnings": warnings}),
                classification="CONFIDENTIAL",
                checksum=_checksum(
                    json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode()
                ),
            ),
        )
        assert context.call.tool_name == self.definition.tool_name
        return ToolExecutionOutput(output=output, evidence=(evidence,))


class MockReportTool(_MockToolBase):
    """Render evidence-backed JSON report content into the governed ArtifactStore."""

    transient_error_code = "REPORT_GENERATION_FAILURE"
    timeout_error_code = "REPORT_TIMEOUT"
    definition = _definition(
        name="report_generator",
        description="Offline internal Supplier Quality report generator",
        risk=RiskLevel.LOW,
        attempt_seconds=30,
        overall_seconds=55,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "task_id",
                "scope",
                "analysis_result",
                "evidence_refs",
                "template_version",
                "format",
                "language",
            ],
            "properties": {
                "task_id": {"type": "string"},
                "scope": {
                    "type": "object",
                    "required": ["year", "quarter", "start_date", "end_date", "supplier_ids"],
                },
                "analysis_result": {"type": "object"},
                "evidence_refs": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "template_version": {
                    "type": "string",
                    "const": "supplier_quality_report.v1",
                },
                "format": {"type": "string", "enum": ["PDF", "JSON"]},
                "language": {"type": "string", "enum": ["zh-CN", "en-US"]},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "artifact_id",
                "type",
                "location",
                "created_at",
                "checksum",
                "size_bytes",
                "citation_map",
                "generator_version",
            ],
            "properties": {
                "artifact_id": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["QUALITY_ANALYSIS_REPORT_PDF", "QUALITY_ANALYSIS_REPORT_JSON"],
                },
                "location": {"type": "string"},
                "created_at": {"type": "string", "format": "date-time"},
                "checksum": {"type": "string"},
                "size_bytes": {"type": "integer", "minimum": 1},
                "citation_map": {"type": "object"},
                "generator_version": {"type": "string"},
            },
        },
    )

    def __init__(
        self,
        *,
        evidence_reader: EvidenceReader,
        artifact_store: ArtifactStore,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        behavior: MockBehavior | None = None,
    ) -> None:
        self._evidence_reader = evidence_reader
        self._artifact_store = artifact_store
        self._ids = ids
        self._clock = clock
        self.behavior = behavior or MockBehavior()
        self.call_count = 0
        self.received_evidence_ids: list[tuple[str, ...]] = []

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        self._before_execute()
        evidence_refs = tuple(
            str(item) for item in cast(list[JsonValue], arguments.root["evidence_refs"])
        )
        self.received_evidence_ids.append(evidence_refs)
        evidence = tuple(self._evidence_reader.get(item) for item in evidence_refs)
        task_id = str(arguments.root["task_id"])
        if task_id != context.call.task_id or any(item.task_id != task_id for item in evidence):
            raise ToolPermissionError(
                error_code="REPORT_INPUT_DENIED",
                message="Report evidence does not belong to the current task",
            )
        report = self._build_report(arguments, evidence)
        content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        artifact_id = self._ids.new_id("A")
        safe_task = _safe_filename_component(task_id)
        artifact = self._artifact_store.write(
            artifact_id=artifact_id,
            task_id=task_id,
            artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
            filename=f"supplier-quality-analysis-{safe_task}.json",
            media_type="application/json",
            content=content,
            generator_version="supplier_quality_report.v1-mock",
            evidence_ids=evidence_refs,
        )
        citation_map = {item.evidence_id: item.source_type.value for item in evidence}
        return ToolExecutionOutput(
            output=_json_object(
                {
                    "artifact_id": artifact.artifact_id,
                    "type": artifact.type.value,
                    "location": artifact.location,
                    "created_at": artifact.created_at.isoformat(),
                    "checksum": artifact.checksum,
                    "size_bytes": artifact.size_bytes,
                    "citation_map": citation_map,
                    "generator_version": artifact.generator_version,
                }
            )
        )

    def _build_report(
        self,
        arguments: JsonObject,
        evidence: tuple[EvidenceItem, ...],
    ) -> dict[str, object]:
        policy = [
            item.content.data.root for item in evidence if item.source_type is EvidenceType.DOCUMENT
        ]
        database = [
            item.content.data.root for item in evidence if item.source_type is EvidenceType.DATABASE
        ]
        metrics = cast(dict[str, JsonValue], arguments.root["analysis_result"])
        metric_items = cast(list[JsonValue], metrics.get("metrics", []))
        metric_objects = [cast(dict[str, JsonValue], item) for item in metric_items]
        defect_rate = next(
            (item.get("value") for item in metric_objects if item.get("metric") == "defect_rate"),
            None,
        )
        risk = (
            "Defect rate exceeds the 1% review threshold."
            if isinstance(defect_rate, int | float) and defect_rate > 0.01
            else "No threshold exception was identified from the deterministic metrics."
        )
        return {
            "title": "Supplier Quality Analysis Report",
            "task_summary": {"task_id": arguments.root["task_id"]},
            "scope": arguments.root["scope"],
            "quality_policy_findings": policy,
            "supplier_quality_data": database,
            "analysis_results": metrics,
            "key_risks": [risk],
            "recommendations": [
                "Review the recorded deviation trend and document containment when required."
            ],
            "evidence_references": [
                {
                    "evidence_id": item.evidence_id,
                    "source_type": item.source_type.value,
                    "source_step_id": item.step_id,
                    "source_tool_call_id": item.tool_call_id,
                }
                for item in evidence
            ],
            "execution_metadata": {
                "generated_at": self._clock().isoformat(),
                "template_version": arguments.root["template_version"],
                "language": arguments.root["language"],
            },
        }


def _checksum(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _json_object(value: object) -> JsonObject:
    return JsonObject(cast(JsonMapping, value))


def _safe_filename_component(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in value
    )
    return normalized[:100] or "task"
