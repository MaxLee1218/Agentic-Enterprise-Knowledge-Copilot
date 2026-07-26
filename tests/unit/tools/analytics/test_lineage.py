"""Calculation evidence formula, checksum, and input-lineage tests."""

from copilot.contracts import EvidenceType
from copilot.tools.analytics import AnalyticsTool
from tests.unit.tools.analytics.helpers import (
    analytics_arguments,
    analytics_context,
    ledger_with_database_evidence,
)


def test_tool_prepares_calculation_evidence_linked_to_database_evidence() -> None:
    ledger = ledger_with_database_evidence()
    tool = AnalyticsTool(ledger)
    arguments = analytics_arguments()

    result = tool.execute(arguments, analytics_context(arguments))

    assert len(result.evidence) == 1
    draft = result.evidence[0]
    assert draft.source_type is EvidenceType.CALCULATION
    assert draft.source_reference.input_evidence_ids == ("E-DB-001",)
    reference = draft.source_reference.reference.root
    assert reference["engine_version"] == "quality_metrics.v1"
    assert reference["dataset_checksum"] == arguments.root["dataset_checksum"]
    formulas = reference["formulas"]
    assert isinstance(formulas, dict)
    assert formulas["defect_rate"] == "sum(defect_count) / sum(inspected_count)"
    assert draft.content.checksum
