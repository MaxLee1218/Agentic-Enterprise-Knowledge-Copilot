"""Structured report-to-verification adapters; no natural-language parsing."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import JsonValue, TypeAdapter

from copilot.contracts import (
    APReportClaimV1,
    CandidateResult,
    CitationClaim,
    ClaimType,
    DeliverableRecord,
    EvidenceItem,
    EvidenceType,
    JsonObject,
    NumericClaim,
    TaskContract,
    TaskType,
)
from copilot.contracts.base import JsonMapping
from copilot.tools.analytics.precision import DECIMAL_PLACES

_AP_REPORT_CLAIMS = TypeAdapter(tuple[APReportClaimV1, ...])


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


def candidate_from_ap_report(
    *,
    task_contract: TaskContract,
    report_step_id: str,
    report: JsonMapping,
    evidence: tuple[EvidenceItem, ...],
) -> CandidateResult:
    """Map the frozen AP report claim envelope without parsing narrative text."""
    if task_contract.task_type is not TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1:
        raise ValueError("AP report claims require the Accounts Payable task profile")
    raw_evidence = report.get("evidence")
    if not isinstance(raw_evidence, dict):
        raise ValueError("AP report evidence section is missing")
    raw_claims = raw_evidence.get("claims")
    if not isinstance(raw_claims, list):
        raise ValueError("AP report evidence claims are missing")
    report_claims = _AP_REPORT_CLAIMS.validate_python(raw_claims)
    claim_ids = tuple(claim.claim_id for claim in report_claims)
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("AP report claim identifiers must be unique")

    available_ids = {item.evidence_id for item in evidence}
    cited_ids = tuple(
        dict.fromkeys(
            evidence_id
            for claim in report_claims
            for evidence_id in claim.evidence_ids
            if evidence_id in available_ids
        )
    )
    data_overview = report.get("data_overview")
    overview = data_overview if isinstance(data_overview, dict) else {}
    empty_result = overview.get("empty_result") is True
    deliverables = tuple(
        DeliverableRecord(
            deliverable_id=section,
            producing_step_id=report_step_id,
            content=report[section],
            evidence_ids=cited_ids,
            empty_result=empty_result and section in {"data_overview", "exception_summary"},
        )
        for section in task_contract.expected_output.required_sections
        if section in report
    )
    claims = tuple(
        CitationClaim(
            claim_id=claim.claim_id,
            claim_type=claim.claim_type,
            evidence_ids=claim.evidence_ids,
            step_id=report_step_id,
            policy_governed=claim.policy_governed,
            rule_ids=claim.rule_ids,
        )
        for claim in report_claims
    )
    numeric_claims: list[NumericClaim] = []
    for claim in report_claims:
        if claim.claim_type is not ClaimType.NUMERIC:
            continue
        value = claim.value
        unit = claim.unit or "invalid"
        precision = claim.precision if claim.precision is not None else 0
        if unit == "percent":
            value = (
                None
                if value is None
                else (Decimal(value) / Decimal(100)).quantize(Decimal("0.00000001"))
            )
            unit = "ratio"
            precision = 8
        numeric_claims.append(
            NumericClaim(
                claim_id=claim.claim_id,
                metric_name=claim.metric_name or "invalid",
                value=value,
                unit=unit,
                precision=precision,
                evidence_ids=claim.evidence_ids,
                dimensions=claim.dimensions,
                operation_name=claim.operation_name,
            )
        )
    return CandidateResult(
        task_id=task_contract.task_id,
        deliverables=deliverables,
        claims=claims,
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


__all__ = ["candidate_from_ap_report", "candidate_from_json_report"]
