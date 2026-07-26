"""Structured report-to-verification adapters; no natural-language parsing."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import JsonValue

from copilot.contracts import (
    CandidateResult,
    CitationClaim,
    ClaimType,
    DeliverableRecord,
    EvidenceItem,
    EvidenceType,
    JsonObject,
    NumericClaim,
    TaskContract,
)
from copilot.contracts.base import JsonMapping
from copilot.tools.analytics.precision import DECIMAL_PLACES


def candidate_from_json_report(
    *,
    task_contract: TaskContract,
    report_step_id: str,
    report: JsonMapping,
    evidence: tuple[EvidenceItem, ...],
) -> CandidateResult:
    """Map the current JSON report model to stable deliverable and claim contracts."""
    task_id = task_contract.task_id
    evidence_by_id = {item.evidence_id: item for item in evidence}
    cited_ids = _cited_ids(report)
    cited_by_type = {
        source_type: tuple(
            evidence_id
            for evidence_id in cited_ids
            if evidence_id in evidence_by_id
            and evidence_by_id[evidence_id].source_type is source_type
        )
        for source_type in EvidenceType
    }
    analysis = report.get("analysis_results")
    analysis_mapping = analysis if isinstance(analysis, dict) else {}
    empty_result = bool(analysis_mapping.get("empty_result", False))
    deliverables = tuple(
        DeliverableRecord(
            deliverable_id=section,
            producing_step_id=report_step_id,
            content=report[section],
            evidence_ids=cited_ids,
            empty_result=empty_result and section in {"supplier_quality_data", "analysis_results"},
        )
        for section in task_contract.expected_output.required_sections
        if section in report
    )

    claims: list[CitationClaim] = []
    if "quality_policy_findings" in report:
        claims.append(
            CitationClaim(
                claim_id="report:quality-policy",
                claim_type=ClaimType.POLICY,
                evidence_ids=cited_by_type[EvidenceType.DOCUMENT],
                step_id=report_step_id,
            )
        )
    if "supplier_quality_data" in report:
        claims.append(
            CitationClaim(
                claim_id="report:supplier-quality-data",
                claim_type=ClaimType.DATA,
                evidence_ids=cited_by_type[EvidenceType.DATABASE],
                step_id=report_step_id,
            )
        )

    numeric_claims: list[NumericClaim] = []
    metrics = analysis_mapping.get("metrics")
    if isinstance(metrics, list):
        for index, raw_metric in enumerate(metrics):
            if not isinstance(raw_metric, dict):
                continue
            metric_name = raw_metric.get("metric")
            unit = raw_metric.get("unit")
            if not isinstance(metric_name, str) or not isinstance(unit, str):
                continue
            raw_value = raw_metric.get("value")
            value = _decimal_or_none(raw_value)
            raw_dimensions = raw_metric.get("dimensions")
            dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
            numeric_claims.append(
                NumericClaim(
                    claim_id=f"report:metric:{index}:{metric_name}",
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                    precision=0 if unit == "count" else DECIMAL_PLACES,
                    evidence_ids=cited_by_type[EvidenceType.CALCULATION],
                    dimensions=JsonObject(dimensions),
                )
            )
            claims.append(
                CitationClaim(
                    claim_id=f"report:metric:{index}:{metric_name}",
                    claim_type=ClaimType.NUMERIC,
                    evidence_ids=cited_by_type[EvidenceType.CALCULATION],
                    step_id=report_step_id,
                )
            )

    return CandidateResult(
        task_id=task_id,
        deliverables=deliverables,
        claims=tuple(claims),
        numeric_claims=tuple(numeric_claims),
        output_fields=tuple(sorted(_field_names(report))),
    )


def _cited_ids(report: JsonMapping) -> tuple[str, ...]:
    references = report.get("evidence_references")
    if not isinstance(references, list):
        return ()
    identifiers: list[str] = []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        evidence_id = reference.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id not in identifiers:
            identifiers.append(evidence_id)
    return tuple(identifiers)


def _decimal_or_none(value: JsonValue | None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, int | float | str):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("NaN")


def _field_names(value: JsonValue, prefix: str = "") -> set[str]:
    fields: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            fields.add(path)
            fields.add(key)
            fields.update(_field_names(child, path))
    elif isinstance(value, list):
        for child in value:
            fields.update(_field_names(child, prefix))
    return fields


__all__ = ["candidate_from_json_report"]
