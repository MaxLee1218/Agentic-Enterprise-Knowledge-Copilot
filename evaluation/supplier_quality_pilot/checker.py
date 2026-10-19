"""Artifact-level P01 verification against source-derived answers, not tool outputs."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from copilot.contracts import (
    ArtifactType,
    EvidenceItem,
    EvidenceType,
    TaskStatus,
    VerificationStatus,
)
from copilot.services.workflows.models import WorkflowExecution
from copilot.tools.analytics.schemas import AnalyticsMetricResult
from copilot.tools.reporting.schemas import ReportDocument, ReportFinding, ReportRisk
from evaluation.supplier_quality_pilot.contracts import (
    OracleMetric,
    PilotAssertion,
    PilotSnapshot,
)
from evaluation.supplier_quality_pilot.source import (
    P01_SUPPLIERS,
    oracle_metrics,
    ratio,
    source_counts,
)


def evaluate_p01(
    snapshot: PilotSnapshot,
    execution: WorkflowExecution | None,
    artifact_paths: Mapping[str, Path],
) -> tuple[PilotAssertion, ...]:
    """Score every required fact even when execution or a report is missing or corrupt."""
    checks: list[PilotAssertion] = []

    def check(name: str, category: str, passed: bool) -> None:
        checks.append(
            PilotAssertion.model_validate(
                {
                    "assertion_id": name,
                    "category": category,
                    "passed": passed,
                }
            )
        )

    check(
        "task_completed",
        "lifecycle",
        execution is not None
        and execution.task_result.final_status is TaskStatus.COMPLETED
        and execution.final_state.state is TaskStatus.COMPLETED
        and execution.final_state.task_id == execution.task_result.task_id,
    )
    check(
        "product_verified",
        "lifecycle",
        execution is not None
        and execution.verification_result is not None
        and execution.verification_result.status
        in {
            VerificationStatus.PASSED,
            VerificationStatus.PASSED_WITH_WARNINGS,
        },
    )
    document: ReportDocument | None = None
    artifacts = execution.artifacts if execution else ()
    check(
        "one_json_artifact",
        "artifact",
        len(artifacts) == 1
        and execution is not None
        and tuple(a.artifact_id for a in artifacts) == execution.task_result.artifacts
        and artifacts[0].task_id == execution.task_result.task_id
        and artifacts[0].type is ArtifactType.QUALITY_ANALYSIS_REPORT_JSON
        and artifacts[0].media_type == "application/json",
    )
    for artifact in artifacts:
        path = artifact_paths.get(artifact.artifact_id)
        try:
            data = path.read_bytes() if path else b""
            valid_file = (
                bool(data)
                and len(data) == artifact.size_bytes
                and (hashlib.sha256(data).hexdigest() == artifact.checksum.removeprefix("sha256:"))
            )
            document = ReportDocument.model_validate_json(data)
            check("artifact_schema", "artifact", True)
        except (OSError, ValueError, ValidationError):
            valid_file = False
            check("artifact_schema", "artifact", False)
        check("artifact_integrity", "artifact", valid_file)

    expected = oracle_metrics(snapshot)
    metrics = document.key_metrics if document else ()
    copies = document.analysis_results.metrics if document else ()
    for target in expected:
        actual = _find(metrics, target)
        checks.append(
            PilotAssertion(
                assertion_id=f"{target.supplier_id}/{target.period}/{target.metric}",
                category="numeric",
                passed=_matches(actual, target) and _matches(_find(copies, target), target),
                expected=str(target.value),
                actual=str(actual.value) if actual else "missing",
            )
        )
    # Quarterly checks aggregate the reported monthly counts, without requiring new product output.
    for supplier in P01_SUPPLIERS:
        source = [r for r in source_counts(snapshot) if r[0] == supplier]
        defects, inspected = sum(r[2] for r in source), sum(r[3] for r in source)
        actual_counts: list[int | None] = []
        for name in ("defect_count", "inspected_count"):
            selected = [
                m
                for m in metrics
                if m.metric.value == name and m.dimensions.get("supplier_id") == supplier
            ]
            values = [m.value for m in selected]
            actual_counts.append(
                sum(int(v) for v in values if v is not None)
                if len(values) == len(source) and all(isinstance(v, int) for v in values)
                else None
            )
        for name, value, quarter_actual in (
            ("defect_count", Decimal(defects), actual_counts[0]),
            ("inspected_count", Decimal(inspected), actual_counts[1]),
            (
                "defect_rate",
                ratio(defects, inspected),
                ratio(actual_counts[0], actual_counts[1])
                if actual_counts[0] is not None and actual_counts[1] is not None
                else None,
            ),
        ):
            checks.append(
                PilotAssertion(
                    assertion_id=f"{supplier}/quarter_from_report_counts/{name}",
                    category="numeric",
                    passed=all(count is not None for count in actual_counts)
                    and quarter_actual == value,
                    expected=str(value),
                    actual=str(quarter_actual),
                )
            )
    check(
        "exact_metric_set",
        "scope",
        metrics == copies
        and len(metrics) == len(expected)
        and {_key(m) for m in metrics} == {(m.metric, m.supplier_id, m.period) for m in expected},
    )
    check(
        "report_scope",
        "scope",
        document is not None
        and document.scope.year == 2026
        and document.scope.quarter == 2
        and document.scope.start_date.isoformat() == "2026-04-01"
        and document.scope.end_date.isoformat() == "2026-06-30"
        and document.scope.supplier_ids == P01_SUPPLIERS,
    )
    check(
        "report_task",
        "scope",
        document is not None
        and execution is not None
        and document.task_summary.task_id == execution.task_result.task_id,
    )
    check(
        "no_policy_claims",
        "scope",
        document is not None
        and not document.applicable_policies
        and not document.quality_policy_findings,
    )
    check(
        "null_reasons",
        "scope",
        document is not None and _null_reasons_present(snapshot, document),
    )
    checks.extend(_evidence_checks(snapshot, execution, document, expected))
    return tuple(checks)


def _key(metric: AnalyticsMetricResult) -> tuple[str, str | None, str | None]:
    return (
        metric.metric.value,
        metric.dimensions.get("supplier_id"),
        metric.dimensions.get("period"),
    )


def _find(
    metrics: Sequence[AnalyticsMetricResult],
    target: OracleMetric,
) -> AnalyticsMetricResult | None:
    found = [m for m in metrics if _key(m) == (target.metric, target.supplier_id, target.period)]
    return found[0] if len(found) == 1 else None


def _matches(metric: AnalyticsMetricResult | None, target: OracleMetric) -> bool:
    if (
        metric is None
        or metric.unit != target.unit
        or metric.dimensions
        != {
            "supplier_id": target.supplier_id,
            "period": target.period,
        }
    ):
        return False
    for value, expected in (
        (metric.value, target.value),
        (metric.numerator, target.numerator),
        (metric.denominator, target.denominator),
    ):
        if value is None or expected is None:
            if value is not None or expected is not None:
                return False
        elif Decimal(str(value)) != expected:
            return False
    return True


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def _digest_matches(actual: object, expected: str) -> bool:
    return isinstance(actual, str) and actual.removeprefix("sha256:") == expected


def _null_reasons_present(snapshot: PilotSnapshot, document: ReportDocument) -> bool:
    """Check v1's explicit reasons for each applicable null, derived from the source."""
    reasons: set[str] = set()
    previous: dict[str, int] = {}
    for supplier, period, _defects, inspected in source_counts(snapshot):
        dimensions = f"(period={period}, supplier_id={supplier})"
        if inspected == 0:
            reasons.add(f"Defect rate is undefined because inspected_count is zero {dimensions}")
        if supplier not in previous:
            reasons.add(f"Trend is undefined for the first period {dimensions}")
        elif inspected == 0 or previous[supplier] == 0:
            reasons.add(
                "Trend is undefined because a compared period has zero inspected_count "
                f"{dimensions}"
            )
        previous[supplier] = inspected
    limitation_codes = {item.code for item in document.limitations}
    required_codes = {"FIRST_PERIOD_TREND_BASELINE"}
    if any(row[3] == 0 for row in source_counts(snapshot)):
        required_codes.add("ZERO_DENOMINATOR_ANALYTICS_NOTE")
    return reasons <= set(document.analysis_results.warnings) and required_codes <= limitation_codes


def _fact_references_resolve(document: ReportDocument, ledger: Mapping[str, EvidenceItem]) -> bool:
    """Validate structured fact/reference locations, without judging arbitrary prose."""
    # Evidence IDs alone in an index do not establish that the actual facts cite that index.
    if (
        not document.major_findings
        or document.supplier_ranking
        or document.key_risks != document.risk_analysis
        or document.recommended_actions != document.recommendations
        or document.data_overview != document.supplier_quality_data
    ):
        return False
    database_ids = {
        e.evidence_id for e in ledger.values() if e.source_type is EvidenceType.DATABASE
    }
    if (
        len(document.data_overview) != len(database_ids)
        or {item.evidence_id for item in document.data_overview} != database_ids
    ):
        return False
    facts: tuple[ReportFinding | ReportRisk, ...] = (*document.major_findings, *document.key_risks)
    for fact in facts:
        if not fact.evidence_ids or not set(fact.evidence_ids) <= ledger.keys():
            return False
        if not any(ledger[e].source_type is EvidenceType.CALCULATION for e in fact.evidence_ids):
            return False
    for recommendation in document.recommended_actions:
        if not set(recommendation.evidence_ids) <= ledger.keys():
            return False
        if recommendation.basis == "POLICY_EVIDENCE":
            # This non-policy P01 has no authorized policy conclusion.
            return False
    for item in document.data_overview:
        evidence = ledger.get(item.evidence_id)
        if evidence is None or evidence.source_type is not EvidenceType.DATABASE:
            return False
        if (
            item.row_count != evidence.content.data.root.get("row_count")
            or item.checksum != evidence.content.checksum
            or item.query_id != evidence.source_reference.reference.root.get("query_fingerprint")
        ):
            return False
    return True


def _evidence_checks(
    snapshot: PilotSnapshot,
    execution: WorkflowExecution | None,
    document: ReportDocument | None,
    expected: tuple[OracleMetric, ...],
) -> list[PilotAssertion]:
    ledger = {e.evidence_id: e for e in execution.evidence} if execution else {}
    database = [e for e in ledger.values() if e.source_type is EvidenceType.DATABASE]
    calculations = [e for e in ledger.values() if e.source_type is EvidenceType.CALCULATION]
    refs = document.evidence if document else ()
    reference_ids = {r.evidence_id for r in refs}
    rows = [
        dict(supplier_id=s, period=p, defect_count=d, inspected_count=n)
        for s, p, d, n in source_counts(snapshot)
    ]
    database_ids = {e.evidence_id for e in database}
    database_ok = bool(database) and all(
        e.content.checksum.removeprefix("sha256:") == _hash(rows)
        and e.source_reference.reference.root.get("read_only") is True
        and e.source_reference.reference.root.get("row_count") == len(rows)
        and e.source_reference.reference.root.get("parameter_summary")
        == {
            "tenant_scope_hash": _hash(snapshot.tenant_id),
            "start_date": "2026-04-01",
            "end_date": "2026-06-30",
            "supplier_count": 2,
            "supplier_scope_hash": _hash(list(P01_SUPPLIERS)),
        }
        for e in database
    )
    lineage_ok = (
        bool(calculations)
        and database_ok
        and all(
            set(e.source_reference.input_evidence_ids) <= database_ids
            and bool(e.source_reference.input_evidence_ids)
            and _digest_matches(
                e.source_reference.reference.root.get("dataset_checksum"), _hash(rows)
            )
            and e.content.checksum.removeprefix("sha256:") == _hash(e.content.data.root)
            for e in calculations
        )
    )
    refs_ok = (
        (
            bool(refs)
            and document is not None
            and _fact_references_resolve(document, ledger)
            and document.evidence == document.evidence_references
            and reference_ids == set(ledger)
            and all(
                r.evidence_id in ledger
                and r.checksum == ledger[r.evidence_id].content.checksum
                and r.input_evidence_ids
                == ledger[r.evidence_id].source_reference.input_evidence_ids
                and r.source == ledger[r.evidence_id].source_reference.reference
                and r.source_type is ledger[r.evidence_id].source_type
                and r.source_step_id == ledger[r.evidence_id].step_id
                and r.source_tool_call_id == ledger[r.evidence_id].tool_call_id
                and ledger[r.evidence_id].task_id == execution.task_result.task_id
                for r in refs
            )
        )
        if execution
        else False
    )
    checks = [
        PilotAssertion(assertion_id=n, category="evidence", passed=p)
        for n, p in (
            ("database_matches_source", database_ok),
            ("calculation_lineage", lineage_ok),
            ("report_references_ledger", refs_ok),
        )
    ]
    calc_metrics: list[AnalyticsMetricResult] = []
    for item in calculations:
        raw = item.content.data.root.get("metrics", [])
        if isinstance(raw, list):
            try:
                calc_metrics.extend(AnalyticsMetricResult.model_validate(m) for m in raw)
            except ValidationError:
                calc_metrics = []
                break
    for target in expected:
        checks.append(
            PilotAssertion(
                assertion_id=f"support/{target.supplier_id}/{target.period}/{target.metric}",
                category="evidence",
                passed=refs_ok
                and lineage_ok
                and _matches(_find(calc_metrics, target), target)
                and document is not None
                and _matches(_find(document.key_metrics, target), target),
            )
        )
    return checks
