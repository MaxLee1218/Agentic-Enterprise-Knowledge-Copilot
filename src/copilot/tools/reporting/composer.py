"""Deterministic mapping from frozen tool inputs and Evidence to a report model."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from copilot.contracts import EvidenceItem, EvidenceType, JsonObject
from copilot.contracts.base import JsonMapping
from copilot.services.workflows.ports import EvidenceReader
from copilot.tools.reporting.exceptions import ReportInputDeniedError, ReportInputError
from copilot.tools.reporting.schemas import (
    REPORT_GENERATOR_VERSION,
    REPORT_SCHEMA_VERSION,
    ReportDataSource,
    ReportDocument,
    ReportEvidenceReference,
    ReportExecutionMetadata,
    ReportExecutionStep,
    ReportFinding,
    ReportLimitation,
    ReportPolicyReference,
    ReportRecommendation,
    ReportRequest,
    ReportRisk,
    ReportTaskSummary,
)


class ReportComposer:
    """Build one report without querying, recalculating, or inventing missing facts."""

    def __init__(
        self,
        evidence_reader: EvidenceReader,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._evidence_reader = evidence_reader
        self._clock = clock

    def load_evidence(
        self,
        request: ReportRequest,
        *,
        task_id: str,
        tenant_id: str,
    ) -> tuple[EvidenceItem, ...]:
        """Resolve and scope every referenced Evidence item."""
        loaded: list[EvidenceItem] = []
        for evidence_id in request.evidence_refs:
            try:
                item = self._evidence_reader.get(
                    evidence_id,
                    task_id=task_id,
                    tenant_id=tenant_id,
                )
            except (KeyError, LookupError) as exc:
                raise ReportInputError("Report references Evidence that does not exist") from exc
            if item.task_id != task_id:
                raise ReportInputDeniedError("Report Evidence belongs to a different task")
            loaded.append(item)
        return tuple(loaded)

    def compose(
        self,
        request: ReportRequest,
        evidence: tuple[EvidenceItem, ...],
    ) -> ReportDocument:
        """Map only trusted structured values into the common ReportDocument."""
        policies = tuple(
            self._policy_reference(item)
            for item in evidence
            if item.source_type is EvidenceType.DOCUMENT
        )
        data_sources = tuple(
            self._data_source(item)
            for item in evidence
            if item.source_type is EvidenceType.DATABASE
        )
        calculation_ids = tuple(
            item.evidence_id for item in evidence if item.source_type is EvidenceType.CALCULATION
        )
        findings = self._findings(request, calculation_ids)
        risks = self._risks(request, calculation_ids)
        recommendations = self._recommendations(policies)
        limitations = self._limitations(request, policies)
        references = tuple(self._evidence_reference(item) for item in evidence)
        execution_trace = tuple(
            ReportExecutionStep(
                step_id=item.step_id,
                tool_call_id=item.tool_call_id,
                evidence_id=item.evidence_id,
                source_type=item.source_type,
            )
            for item in evidence
        )
        return ReportDocument(
            title="Supplier Quality Analysis Report",
            executive_summary=self._executive_summary(request),
            task_summary=ReportTaskSummary(
                task_id=request.task_id,
                trace_id=request.task_id,
                task_status="EXECUTING",
            ),
            scope=request.scope,
            applicable_policies=policies,
            quality_policy_findings=policies,
            data_overview=data_sources,
            supplier_quality_data=data_sources,
            key_metrics=request.analysis_result.metrics,
            analysis_results=request.analysis_result,
            supplier_ranking=(),
            major_findings=findings,
            key_risks=risks,
            risk_analysis=risks,
            recommended_actions=recommendations,
            recommendations=recommendations,
            limitations=limitations,
            evidence=references,
            evidence_references=references,
            execution_trace=execution_trace,
            execution_metadata=ReportExecutionMetadata(
                generated_at=self._clock(),
                schema_version=REPORT_SCHEMA_VERSION,
                template_version=request.template_version,
                generator_version=REPORT_GENERATOR_VERSION,
                language=request.language,
                verification_status="PENDING",
            ),
        )

    @staticmethod
    def _policy_reference(item: EvidenceItem) -> ReportPolicyReference:
        reference = item.source_reference.reference.root
        content = item.content.data.root
        document_id = reference.get("document_id") or reference.get("source")
        location = reference.get("chunk_id") or reference.get("page")
        excerpt = content.get("excerpt")
        if not isinstance(document_id, str) or not isinstance(location, str | int):
            raise ReportInputError("Document Evidence lacks stable source metadata")
        if not isinstance(excerpt, str) or not excerpt.strip():
            raise ReportInputError("Document Evidence lacks a usable excerpt")
        version = reference.get("document_version")
        return ReportPolicyReference(
            evidence_id=item.evidence_id,
            document_id=document_id,
            document_version=version if isinstance(version, str) else None,
            location=str(location),
            excerpt=excerpt,
        )

    @staticmethod
    def _data_source(item: EvidenceItem) -> ReportDataSource:
        reference = item.source_reference.reference.root
        query_id = reference.get("query_fingerprint") or reference.get("query_id")
        if not isinstance(query_id, str) or not query_id.strip():
            raise ReportInputError("Database Evidence lacks a query identifier")
        row_count = reference.get("row_count", item.content.data.root.get("row_count"))
        if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
            raise ReportInputError("Database Evidence lacks a valid row count")
        snapshot = reference.get("snapshot_at")
        return ReportDataSource(
            evidence_id=item.evidence_id,
            query_id=query_id,
            row_count=row_count,
            snapshot_at=snapshot if isinstance(snapshot, str) else None,
            checksum=item.content.checksum,
        )

    @staticmethod
    def _evidence_reference(item: EvidenceItem) -> ReportEvidenceReference:
        reference = item.source_reference.reference.root
        query_id_value = reference.get("query_fingerprint") or reference.get("query_id")
        query_id = query_id_value if isinstance(query_id_value, str) else None
        formula_value = reference.get("formulas", reference.get("formula"))
        formulas: dict[str, str] = {}
        if isinstance(formula_value, str):
            formulas["default"] = formula_value
        elif isinstance(formula_value, dict):
            formulas = {
                str(key): str(value)
                for key, value in sorted(formula_value.items())
                if isinstance(value, str)
            }
        return ReportEvidenceReference(
            evidence_id=item.evidence_id,
            source_type=item.source_type,
            source_step_id=item.step_id,
            source_tool_call_id=item.tool_call_id,
            source=JsonObject(item.source_reference.reference.root.copy()),
            checksum=item.content.checksum,
            query_id=query_id,
            formulas=formulas,
            input_evidence_ids=item.source_reference.input_evidence_ids,
        )

    @staticmethod
    def _executive_summary(request: ReportRequest) -> str:
        if request.analysis_result.empty_result:
            return (
                "No supplier quality records were returned for the authorized scope; "
                "quality metrics could not be calculated."
            )
        return (
            f"The deterministic quality analysis produced "
            f"{len(request.analysis_result.metrics)} metric observations for "
            f"{len(request.scope.supplier_ids)} authorized supplier(s)."
        )

    @staticmethod
    def _findings(
        request: ReportRequest,
        calculation_ids: tuple[str, ...],
    ) -> tuple[ReportFinding, ...]:
        if request.analysis_result.empty_result:
            return (
                ReportFinding(
                    finding_id="finding-no-records",
                    title="No records in scope",
                    statement=(
                        "The approved database query returned no records; no zero-defect "
                        "conclusion is made."
                    ),
                    severity="WARNING",
                    evidence_ids=calculation_ids,
                ),
            )
        findings: list[ReportFinding] = []
        for index, metric in enumerate(request.analysis_result.metrics, start=1):
            if metric.metric.value != "defect_rate":
                continue
            dimensions = ", ".join(
                f"{key}={value}" for key, value in sorted(metric.dimensions.items())
            )
            findings.append(
                ReportFinding(
                    finding_id=f"finding-defect-rate-{index}",
                    title="Reported defect rate",
                    statement=(
                        f"Analytics reported defect_rate={metric.value!s} {metric.unit}"
                        + (f" for {dimensions}." if dimensions else ".")
                    ),
                    severity="INFO" if metric.value is not None else "WARNING",
                    evidence_ids=calculation_ids,
                )
            )
        return tuple(findings)

    @staticmethod
    def _risks(
        request: ReportRequest,
        calculation_ids: tuple[str, ...],
    ) -> tuple[ReportRisk, ...]:
        warnings = request.analysis_result.warnings
        if warnings:
            return tuple(
                ReportRisk(
                    risk_id=f"risk-analytics-warning-{index}",
                    statement=warning,
                    level="REVIEW_REQUIRED",
                    evidence_ids=calculation_ids,
                )
                for index, warning in enumerate(warnings, start=1)
            )
        return (
            ReportRisk(
                risk_id="risk-no-analytics-warning",
                statement=(
                    "No analytics warning was reported. Policy threshold assessment remains "
                    "subject to the cited controlled documents."
                ),
                level="INFORMATIONAL",
                evidence_ids=calculation_ids,
            ),
        )

    @staticmethod
    def _recommendations(
        policies: tuple[ReportPolicyReference, ...],
    ) -> tuple[ReportRecommendation, ...]:
        policy_ids = tuple(item.evidence_id for item in policies)
        return (
            ReportRecommendation(
                action_id="action-review-deviations",
                action=(
                    "Review recorded deviations and document containment in accordance with "
                    "the cited controlled quality policy."
                ),
                basis="POLICY_EVIDENCE",
                evidence_ids=policy_ids,
            ),
        )

    @staticmethod
    def _limitations(
        request: ReportRequest,
        policies: tuple[ReportPolicyReference, ...],
    ) -> tuple[ReportLimitation, ...]:
        limitations = [
            ReportLimitation(
                code="RANKING_NOT_AVAILABLE",
                statement=(
                    "Supplier ranking is not produced because quality_metrics.v1 does not "
                    "define a ranking operation."
                ),
            ),
            ReportLimitation(
                code="DETERMINISTIC_SCOPE",
                statement=(
                    "The report reproduces approved Evidence and Analytics outputs and does "
                    "not infer root causes."
                ),
            ),
        ]
        if not policies:
            limitations.append(
                ReportLimitation(
                    code="POLICY_EVIDENCE_MISSING",
                    statement="No controlled policy Evidence was available.",
                )
            )
        if request.analysis_result.empty_result:
            limitations.append(
                ReportLimitation(
                    code="NO_DATA_RECORDS",
                    statement=(
                        "No records were available; null or absent metrics must not be "
                        "interpreted as zero defects."
                    ),
                )
            )
        return tuple(limitations)


def calculation_metrics(item: EvidenceItem) -> tuple[JsonMapping, ...]:
    """Extract calculation metrics for direct consistency comparison."""
    raw = item.content.data.root.get("metrics")
    if not isinstance(raw, list):
        raise ReportInputError("Calculation Evidence lacks structured metrics")
    return tuple(metric for metric in raw if isinstance(metric, dict))


__all__ = ["ReportComposer", "calculation_metrics"]
