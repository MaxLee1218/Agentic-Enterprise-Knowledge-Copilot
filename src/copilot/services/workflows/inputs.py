"""Explicit schema-bound input construction and evidence transfer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from copilot.contracts import (
    ArtifactType,
    EvidenceItem,
    EvidenceType,
    JsonObject,
    StepResult,
    StepType,
    TaskContract,
    TaskRequest,
    TaskStep,
)
from copilot.contracts.base import JsonMapping
from copilot.services.workflows.errors import StepInputError


class StepInputBuilder:
    """Build one tool input solely from contract scope and immutable prior results."""

    def build(
        self,
        step: TaskStep,
        request: TaskRequest,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
    ) -> JsonObject:
        """Return the exact frozen input shape for the step type."""
        if step.step_type is StepType.KNOWLEDGE_SEARCH:
            return self._knowledge(request, contract)
        if step.step_type is StepType.DATABASE_QUERY:
            return self._database(request, contract)
        if step.step_type is StepType.ANALYSIS:
            return self._analytics(step, contract, prior_results, evidence)
        if step.step_type is StepType.REPORT_GENERATION:
            return self._report(step, contract, prior_results, evidence)
        raise StepInputError(f"Unsupported step type {step.step_type}")

    @staticmethod
    def _knowledge(request: TaskRequest, contract: TaskContract) -> JsonObject:
        scope = contract.constraints
        return JsonObject(
            {
                "query": f"Supplier quality policy and deviation process: {request.raw_input}",
                "tenant_id": scope.tenant_id,
                "collection_ids": ["supplier-quality-policy-v1"],
                "supplier_ids": list(scope.supplier_ids),
                "date_range": {
                    "start": scope.start_date.isoformat(),
                    "end": scope.end_date.isoformat(),
                },
                "top_k": 10,
                "index_snapshot_id": "supplier-quality-policy-snapshot-v1",
            }
        )

    @staticmethod
    def _database(request: TaskRequest, contract: TaskContract) -> JsonObject:
        scope = contract.constraints
        return JsonObject(
            {
                "query_template_id": "supplier_quality_summary_v1",
                "parameters": {
                    "tenant_id": scope.tenant_id,
                    "start_date": scope.start_date.isoformat(),
                    "end_date": scope.end_date.isoformat(),
                    "supplier_ids": list(scope.supplier_ids),
                },
                "schema_version": "quality.v1",
                "snapshot_at": request.created_at.isoformat(),
                "row_limit": 10000,
            }
        )

    @staticmethod
    def _analytics(
        step: TaskStep,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
    ) -> JsonObject:
        if len(step.dependency) != 1:
            raise StepInputError("Analysis step must have exactly one database dependency")
        result = prior_results.get(step.dependency[0])
        if result is None or result.output is None or not result.evidence:
            raise StepInputError("Analysis dependency has no dataset output or evidence")
        database_item = next(
            (
                evidence[evidence_id]
                for evidence_id in result.evidence
                if evidence_id in evidence
                and evidence[evidence_id].source_type is EvidenceType.DATABASE
            ),
            None,
        )
        if database_item is None:
            raise StepInputError("Analysis dependency has no database evidence")
        rows = result.output.root.get("rows")
        if not isinstance(rows, list):
            raise StepInputError("Database output rows are missing")
        return JsonObject(
            {
                "dataset": rows,
                "dataset_evidence_id": database_item.evidence_id,
                "dataset_checksum": database_item.content.checksum,
                "metrics": list(contract.constraints.metrics),
                "group_by": ["supplier_id", "period"],
                "engine_version": "quality_metrics.v1",
            }
        )

    @staticmethod
    def _report(
        step: TaskStep,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
    ) -> JsonObject:
        analysis = next(
            (
                prior_results[dependency]
                for dependency in step.dependency
                if dependency in prior_results
                and prior_results[dependency].output is not None
                and any(
                    evidence.get(evidence_id) is not None
                    and evidence[evidence_id].source_type is EvidenceType.CALCULATION
                    for evidence_id in prior_results[dependency].evidence
                )
            ),
            None,
        )
        if analysis is None or analysis.output is None:
            raise StepInputError("Report step has no successful analysis result")
        refs = tuple(evidence)
        types = {item.source_type for item in evidence.values()}
        required_types = {EvidenceType.DOCUMENT, EvidenceType.DATABASE, EvidenceType.CALCULATION}
        if not required_types.issubset(types):
            raise StepInputError(
                "Report input lacks required document, database, or calculation evidence"
            )
        artifact_type = contract.expected_output.artifact_type
        report_format = (
            "PDF" if artifact_type is ArtifactType.QUALITY_ANALYSIS_REPORT_PDF else "JSON"
        )
        scope = contract.constraints
        return JsonObject(
            {
                "task_id": contract.task_id,
                "scope": {
                    "year": scope.year,
                    "quarter": scope.quarter,
                    "start_date": scope.start_date.isoformat(),
                    "end_date": scope.end_date.isoformat(),
                    "supplier_ids": list(scope.supplier_ids),
                },
                "analysis_result": analysis.output.root,
                "evidence_refs": list(refs),
                "template_version": "supplier_quality_report.v1",
                "format": report_format,
                "language": contract.expected_output.language.value,
            }
        )


def summarize_payload(payload: JsonObject | None) -> JsonObject:
    """Return a bounded key/count summary suitable for audit and execution metadata."""
    if payload is None:
        return JsonObject({})
    summary: dict[str, object] = {"keys": sorted(payload.root)}
    for key in ("row_count", "match_count", "empty_result", "artifact_id"):
        if key in payload.root:
            summary[key] = payload.root[key]
    return JsonObject(cast(JsonMapping, summary))
