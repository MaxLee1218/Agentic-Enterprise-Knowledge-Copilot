"""Report model, composer, renderer, validator, and tool tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from copilot.contracts import ArtifactType, JsonObject, RiskLevel
from copilot.tools.reporting import (
    JsonReportRenderer,
    PdfReportRenderer,
    ReportComposer,
    ReportDocument,
    ReportFormat,
    ReportRequest,
    ReportValidator,
)
from copilot.tools.reporting.exceptions import (
    ReportConsistencyError,
    ReportInputDeniedError,
    ReportInputError,
)
from copilot.tools.reporting.renderer import extract_pdf_report_model
from tests.unit.tools.reporting.helpers import (
    TASK_ID,
    DictEvidenceReader,
    evidence_items,
    report_context,
    report_request,
    report_tool,
)


def _document(report_format: ReportFormat = ReportFormat.JSON) -> ReportDocument:
    items = evidence_items()
    request = report_request(report_format)
    return ReportComposer(DictEvidenceReader(items), clock=lambda: items[0].timestamp).compose(
        request, items
    )


def test_report_request_rejects_unknown_format_duplicate_evidence_and_invalid_scope() -> None:
    raw = report_request().model_dump(mode="json")
    raw["format"] = "HTML"
    with pytest.raises(ValidationError):
        ReportRequest.model_validate(raw)

    raw = report_request().model_dump(mode="json")
    raw["evidence_refs"] = ["E-DB-001", "E-DB-001"]
    with pytest.raises(ValidationError):
        ReportRequest.model_validate(raw)

    raw = report_request().model_dump(mode="json")
    raw["scope"]["end_date"] = "2026-07-01"
    with pytest.raises(ValidationError):
        ReportRequest.model_validate(raw)


def test_json_renderer_round_trips_one_strong_model_without_nan() -> None:
    document = _document()
    rendered = JsonReportRenderer().render(document)
    restored = ReportDocument.model_validate_json(rendered.content)
    assert restored == document
    assert b"NaN" not in rendered.content
    assert json.loads(rendered.content)["task_summary"]["trace_id"] == TASK_ID


def test_pdf_renderer_carries_same_model_and_required_visible_sections() -> None:
    document = _document(ReportFormat.PDF)
    rendered = PdfReportRenderer().render(document)
    assert rendered.content.startswith(b"%PDF-")
    assert ReportDocument.model_validate(extract_pdf_report_model(rendered.content)) == document
    assert rendered.media_type == "application/pdf"


def test_validator_rejects_numeric_drift_and_missing_query_lineage() -> None:
    request = report_request()
    items = evidence_items()
    document = _document()
    validator = ReportValidator()

    altered = document.model_copy(update={"key_metrics": document.key_metrics[:-1]})
    with pytest.raises(ReportConsistencyError, match="metrics"):
        validator.validate_pre_render(request, altered, items)

    bad_database = items[1].model_copy(
        update={
            "source_reference": items[1].source_reference.model_copy(
                update={"reference": JsonObject({"row_count": 3})}
            )
        }
    )
    with pytest.raises(ReportConsistencyError, match="query"):
        validator.validate_pre_render(request, document, (items[0], bad_database, items[2]))


@pytest.mark.parametrize(
    ("report_format", "artifact_type", "suffix"),
    [
        (ReportFormat.JSON, ArtifactType.QUALITY_ANALYSIS_REPORT_JSON, ".json"),
        (ReportFormat.PDF, ArtifactType.QUALITY_ANALYSIS_REPORT_PDF, ".pdf"),
    ],
)
def test_report_tool_commits_both_frozen_formats_idempotently(
    tmp_path: Path,
    report_format: ReportFormat,
    artifact_type: ArtifactType,
    suffix: str,
) -> None:
    tool, repository = report_tool(tmp_path)
    request = report_request(report_format)
    context = report_context(request)
    first = tool.execute(JsonObject(request.model_dump(mode="json")), context)
    second = tool.execute(JsonObject(request.model_dump(mode="json")), context)

    assert first.output == second.output
    artifact_id = str(first.output.root["artifact_id"])
    artifact = repository.get_by_id(artifact_id)
    assert artifact.type is artifact_type
    assert Path(artifact.location).suffix == suffix
    assert artifact.checksum.startswith("sha256:")
    assert repository.exists(artifact_id)
    assert repository.list_by_task(TASK_ID) == (artifact,)
    assert tool.definition.risk_level is RiskLevel.LOW
    assert tool.definition.timeout.attempt_seconds == 30
    assert tool.definition.idempotency.idempotent
    assert first.evidence == ()


def test_report_tool_rejects_missing_and_cross_task_evidence(tmp_path: Path) -> None:
    request = report_request()
    missing_reader = DictEvidenceReader(evidence_items()[:-1])
    missing_tool, _repository = report_tool(tmp_path)
    missing_tool._composer = ReportComposer(  # noqa: SLF001 - controlled boundary fixture
        missing_reader, clock=lambda: evidence_items()[0].timestamp
    )
    with pytest.raises(ReportInputError, match="does not exist"):
        missing_tool.execute(
            JsonObject(request.model_dump(mode="json")),
            report_context(request),
        )

    cross_items = evidence_items(task_id="T-OTHER")
    cross_reader = DictEvidenceReader(cross_items)
    cross_tool, repository = report_tool(tmp_path / "cross")
    cross_tool._composer = ReportComposer(  # noqa: SLF001 - controlled boundary fixture
        cross_reader, clock=lambda: cross_items[0].timestamp
    )
    with pytest.raises(ReportInputDeniedError, match="different task"):
        cross_tool.execute(
            JsonObject(request.model_dump(mode="json")),
            report_context(request),
        )
    assert repository.list_by_task(TASK_ID) == ()


def test_report_tool_compensates_post_commit_consistency_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, repository = report_tool(tmp_path)
    request = report_request()

    def fail_artifact_validation(*_args: object, **_kwargs: object) -> None:
        raise ReportConsistencyError("controlled post-commit failure")

    monkeypatch.setattr(tool._validator, "validate_artifact", fail_artifact_validation)
    with pytest.raises(ReportConsistencyError, match="post-commit"):
        tool.execute(
            JsonObject(request.model_dump(mode="json")),
            report_context(request),
        )
    assert repository.list_by_task(TASK_ID) == ()
    assert list(tmp_path.iterdir()) == []
