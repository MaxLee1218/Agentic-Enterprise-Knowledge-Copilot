"""Offline tool plugins exercising the four frozen v1 capability contracts."""

from __future__ import annotations

from copilot.contracts import (
    EvidenceContent,
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
from copilot.tools import EvidenceDraft, ToolExecutionContext, ToolExecutionOutput


def _definition(
    *,
    name: str,
    description: str,
    risk_level: RiskLevel,
    input_schema: JsonMapping,
    output_schema: JsonMapping,
    timeout_seconds: int,
) -> ToolDefinition:
    return ToolDefinition(
        tool_name=name,
        tool_version="1.0.0-test",
        description=description,
        input_schema=JsonObject(input_schema),
        output_schema=JsonObject(output_schema),
        risk_level=risk_level,
        timeout=ToolTimeout(
            attempt_seconds=timeout_seconds,
            overall_seconds=timeout_seconds * 2,
        ),
        approval_policy=ToolApprovalPolicy(
            policy_id=f"{name}-test-policy",
            trigger_conditions=("restricted_scope",),
            approver_role="quality_data_approver",
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=("normalized_input", "tool_version"),
            reuse_window_seconds=60,
            side_effects="None; deterministic test double",
        ),
    )


class MockKnowledgeTool:
    """Return one controlled document match and its minimized evidence."""

    definition = _definition(
        name="knowledge_search",
        description="Test-only enterprise knowledge retrieval double",
        risk_level=RiskLevel.LOW,
        timeout_seconds=5,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["matches", "match_count", "empty_result"],
            "properties": {
                "matches": {"type": "array"},
                "match_count": {"type": "integer", "minimum": 0},
                "empty_result": {"type": "boolean"},
            },
        },
    )

    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        self.call_count += 1
        query = str(arguments.root["query"])
        output = JsonObject(
            {
                "matches": [{"document_id": "DOC-001", "excerpt": "Knowledge result"}],
                "match_count": 1,
                "empty_result": False,
            }
        )
        evidence = EvidenceDraft(
            source_type=EvidenceType.DOCUMENT,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "document_id": "DOC-001",
                        "document_version": "v1",
                        "chunk_id": "CHUNK-001",
                    }
                )
            ),
            content=EvidenceContent(
                data=JsonObject({"excerpt": "Knowledge result", "query": query}),
                classification="INTERNAL",
                checksum="sha256:mock-document",
            ),
        )
        assert context.call.tool_name == self.definition.tool_name
        return ToolExecutionOutput(output=output, evidence=(evidence,))


class MockDatabaseTool:
    """Return one controlled read-only query row and database evidence."""

    definition = _definition(
        name="database_query",
        description="Test-only registered read-only quality query double",
        risk_level=RiskLevel.MEDIUM,
        timeout_seconds=5,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query_template_id"],
            "properties": {"query_template_id": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["rows", "row_count", "empty_result", "query_fingerprint"],
            "properties": {
                "rows": {"type": "array"},
                "row_count": {"type": "integer", "minimum": 0},
                "empty_result": {"type": "boolean"},
                "query_fingerprint": {"type": "string"},
            },
        },
    )

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        del arguments, context
        output = JsonObject(
            {
                "rows": [{"supplier_id": "S-100", "defect_count": 2}],
                "row_count": 1,
                "empty_result": False,
                "query_fingerprint": "sha256:mock-query",
            }
        )
        evidence = EvidenceDraft(
            source_type=EvidenceType.DATABASE,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {"query_template_id": "supplier_quality_summary_v1", "row_count": 1}
                )
            ),
            content=EvidenceContent(
                data=JsonObject({"row_count": 1}),
                classification="CONFIDENTIAL",
                checksum="sha256:mock-dataset",
            ),
        )
        return ToolExecutionOutput(output=output, evidence=(evidence,))


class MockAnalyticsTool:
    """Return one deterministic calculation linked to database evidence."""

    definition = _definition(
        name="analysis_engine",
        description="Test-only deterministic quality calculation double",
        risk_level=RiskLevel.LOW,
        timeout_seconds=5,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["dataset_evidence_id"],
            "properties": {"dataset_evidence_id": {"type": "string", "minLength": 1}},
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["metrics", "empty_result"],
            "properties": {
                "metrics": {"type": "array"},
                "empty_result": {"type": "boolean"},
            },
        },
    )

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        del context
        source_id = str(arguments.root["dataset_evidence_id"])
        output = JsonObject(
            {"metrics": [{"metric": "defect_rate", "value": 0.02}], "empty_result": False}
        )
        evidence = EvidenceDraft(
            source_type=EvidenceType.CALCULATION,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {"formula": "defect_count / inspected_count", "version": "quality_metrics.v1"}
                ),
                input_evidence_ids=(source_id,),
            ),
            content=EvidenceContent(
                data=JsonObject({"numerator": 2, "denominator": 100, "value": 0.02}),
                classification="CONFIDENTIAL",
                checksum="sha256:mock-calculation",
            ),
        )
        return ToolExecutionOutput(output=output, evidence=(evidence,))


class MockReportTool:
    """Return controlled Artifact metadata without inventing a new evidence source type."""

    definition = _definition(
        name="report_generator",
        description="Test-only internal quality report generation double",
        risk_level=RiskLevel.LOW,
        timeout_seconds=5,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["evidence_refs"],
            "properties": {
                "evidence_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}}
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["artifact_id", "location", "checksum"],
            "properties": {
                "artifact_id": {"type": "string"},
                "location": {"type": "string"},
                "checksum": {"type": "string"},
            },
        },
    )

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        del arguments, context
        return ToolExecutionOutput(
            output=JsonObject(
                {
                    "artifact_id": "A-MOCK-001",
                    "location": "artifact://test/T-001/A-MOCK-001",
                    "checksum": "sha256:mock-artifact",
                }
            )
        )
