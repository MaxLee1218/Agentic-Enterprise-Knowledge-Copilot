"""Evidence-ledger coverage and Artifact citation correctness metrics."""

from decimal import Decimal

from copilot.contracts import EvidenceItem
from evaluation.contracts import CapturedExecution, EvaluationCase, ExpectedClaim, MetricResult
from evaluation.evaluators.base import count_metric, ratio_metric


class GroundingEvaluator:
    name = "grounding"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        evidence = {item.evidence_id: item for item in execution.evidence}
        expected = case.expected_evidence
        items = tuple(evidence.values())
        checks = [
            *(_claim_supported(claim, items) for claim in expected.claims),
            *(
                any(item.source_type is source_type for item in items)
                for source_type in expected.required_evidence_types
            ),
            *(_source_present(source_id, items) for source_id in expected.required_source_ids),
            *(_query_present(query_id, items) for query_id in expected.required_query_ids),
            *(
                _lineage_present(parent, child, items)
                for parent, child in expected.required_lineage_edges
            ),
        ]
        supported = sum(checks)
        coverage_denominator = len(checks)
        cited = [
            evidence_id for artifact in execution.artifacts for evidence_id in artifact.evidence_ids
        ]
        valid = sum(
            evidence_id in evidence and evidence[evidence_id].task_id == execution.task_id
            for evidence_id in cited
        )
        invalid = len(cited) - valid
        missing = max(case.expected_citations.minimum_count - len(cited), 0)
        if case.expected_citations.required and not cited:
            missing = max(missing, 1)
        orphan = len(set(evidence) - set(cited)) if execution.artifacts else 0
        citation_denominator = len(cited)
        if citation_denominator == 0 and case.expected_citations.required:
            citation_denominator = 1
        minimum_met = (
            coverage_denominator > 0
            and Decimal(supported) / Decimal(coverage_denominator) >= expected.minimum_coverage
        )
        return (
            ratio_metric(
                "evidence_coverage",
                supported,
                coverage_denominator,
                pass_when=minimum_met,
            ),
            ratio_metric("citation_correctness", valid, citation_denominator),
            count_metric("invalid_citation_count", invalid, pass_when=invalid == 0),
            count_metric("missing_citation_count", missing, pass_when=missing == 0),
            count_metric("orphan_citation_count", orphan),
        )


def _claim_supported(claim: ExpectedClaim, evidence: tuple[EvidenceItem, ...]) -> bool:
    for item in evidence:
        if item.source_type is not claim.evidence_type:
            continue
        reference = item.source_reference.reference.root
        if claim.source_id is not None and claim.source_id not in reference.values():
            continue
        if claim.query_required and not any(
            isinstance(reference.get(key), str) and reference.get(key)
            for key in ("query_id", "query_fingerprint", "query_template_id")
        ):
            continue
        if claim.lineage_required and not item.source_reference.input_evidence_ids:
            continue
        return True
    return False


def _source_present(source_id: str, evidence: tuple[EvidenceItem, ...]) -> bool:
    return any(source_id in item.source_reference.reference.root.values() for item in evidence)


def _query_present(query_id: str, evidence: tuple[EvidenceItem, ...]) -> bool:
    return any(
        query_id
        in {
            str(reference.get(key))
            for key in ("query_id", "query_fingerprint", "query_template_id")
            if reference.get(key) is not None
        }
        for item in evidence
        for reference in (item.source_reference.reference.root,)
    )


def _lineage_present(
    parent_id: str,
    child_id: str,
    evidence: tuple[EvidenceItem, ...],
) -> bool:
    return any(
        item.evidence_id == child_id and parent_id in item.source_reference.input_evidence_ids
        for item in evidence
    )


__all__ = ["GroundingEvaluator"]
