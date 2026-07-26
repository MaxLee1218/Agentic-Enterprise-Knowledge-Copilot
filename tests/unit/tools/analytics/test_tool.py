"""Analytics Tool success, empty-data, integrity, and bounded-input tests."""

import pytest

from copilot.contracts import JsonObject
from copilot.tools.analytics import AnalyticsTool
from copilot.tools.analytics.exceptions import AnalyticsInputDeniedError, AnalyticsInputError
from tests.unit.tools.analytics.helpers import (
    analytics_arguments,
    analytics_context,
    ledger_with_database_evidence,
)


def test_tool_returns_frozen_output_and_tracks_received_evidence() -> None:
    ledger = ledger_with_database_evidence()
    tool = AnalyticsTool(ledger)
    arguments = analytics_arguments()

    result = tool.execute(arguments, analytics_context(arguments))

    assert result.output.root["calculation_version"] == "quality_metrics.v1"
    assert result.output.root["input_row_count"] == 2
    assert result.output.root["empty_result"] is False
    assert tool.received_evidence_ids == ["E-DB-001"]
    assert tool.call_count == 1


def test_empty_dataset_returns_success_payload_and_calculation_evidence() -> None:
    ledger = ledger_with_database_evidence([])
    tool = AnalyticsTool(ledger)
    arguments = analytics_arguments([])

    result = tool.execute(arguments, analytics_context(arguments))

    assert result.output.root["metrics"] == []
    assert result.output.root["empty_result"] is True
    assert result.output.root["warnings"]
    assert len(result.evidence) == 1


def test_checksum_mismatch_and_cross_task_evidence_fail_closed() -> None:
    ledger = ledger_with_database_evidence()
    tool = AnalyticsTool(ledger)
    mismatch = analytics_arguments(checksum="wrong-checksum")
    with pytest.raises(AnalyticsInputDeniedError, match="database evidence"):
        tool.execute(mismatch, analytics_context(mismatch))

    arguments = analytics_arguments()
    with pytest.raises(AnalyticsInputDeniedError, match="different task"):
        tool.execute(arguments, analytics_context(arguments, task_id="T-OTHER"))


def test_direct_adapter_validation_rejects_missing_fields() -> None:
    tool = AnalyticsTool(ledger_with_database_evidence())
    invalid = JsonObject({"dataset_evidence_id": "E-DB-001"})

    with pytest.raises(AnalyticsInputError):
        tool.execute(invalid, analytics_context(invalid))
