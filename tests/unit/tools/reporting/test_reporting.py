"""Report model, composer, renderer, validator, and tool tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from reportlab.platypus import PageBreak, Paragraph

from copilot.contracts import ArtifactType, JsonObject, RiskLevel
from copilot.tools.analytics.schemas import AnalyticsResult
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
    ReportInputError,
    ReportSizeLimitError,
)
from copilot.tools.reporting.presentation import (
    format_metric_value,
    observed_supplier_ids,
    supplier_overview_rows,
)
from copilot.tools.reporting.renderer import (
    _appendices,
    _build_styles,
    _management_pages,
    extract_pdf_report_model,
)
from tests.unit.tools.reporting.helpers import (
    TASK_ID,
    TENANT_ID,
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


def test_pdf_renderer_is_byte_deterministic_for_the_same_report_model() -> None:
    document = _document(ReportFormat.PDF)

    assert (
        PdfReportRenderer().render(document).content == PdfReportRenderer().render(document).content
    )


def test_management_formatting_preserves_units_and_raw_metric_values() -> None:
    assert format_metric_value(18_994, "count") == "18,994"
    assert format_metric_value(0.0587, "ratio") == "5.87%"
    assert format_metric_value(0.0018, "ratio_delta") == "+0.18 pp"
    assert format_metric_value(-0.0025, "ratio_delta") == "-0.25 pp"
    assert format_metric_value(None, "ratio") == "N/A"

    document = _document()
    assert document.key_metrics[2].value == 0.0125
    assert supplier_overview_rows(document)[0].defect_rates == ()


def test_empty_authorized_scope_uses_calculation_dimensions_not_literal_zero() -> None:
    items = evidence_items()
    request = report_request().model_copy(
        update={"scope": report_request().scope.model_copy(update={"supplier_ids": ()})}
    )
    document = ReportComposer(DictEvidenceReader(items), clock=lambda: items[0].timestamp).compose(
        request, items
    )

    assert observed_supplier_ids(document) == ("S-100",)
    assert "1 supplier(s) represented" in document.executive_summary
    assert "0 authorized supplier" not in document.executive_summary


def test_first_period_warnings_are_consolidated_as_methodology_not_business_risk() -> None:
    items = evidence_items()
    request = report_request()
    warnings = tuple(
        f"Trend is undefined for the first period (period=2026-04, supplier_id=S-{index:03d})"
        for index in range(1, 16)
    )
    request = request.model_copy(
        update={
            "analysis_result": request.analysis_result.model_copy(update={"warnings": warnings})
        }
    )
    document = ReportComposer(DictEvidenceReader(items), clock=lambda: items[0].timestamp).compose(
        request, items
    )

    assert len(document.risk_analysis) == 1
    assert document.risk_analysis[0].level == "INFORMATIONAL"
    assert "REVIEW_REQUIRED" not in document.risk_analysis[0].statement
    assert sum(item.code == "FIRST_PERIOD_TREND_BASELINE" for item in document.limitations) == 1


def test_management_pdf_has_five_page_layer_and_structured_appendices() -> None:
    document = _document(ReportFormat.PDF)
    styles = _build_styles("Helvetica")
    management = _management_pages(document, styles, 178 * 2.834645669)
    appendices = _appendices(document, styles, 178 * 2.834645669)
    management_headings = {
        item.getPlainText() for item in management if isinstance(item, Paragraph)
    }
    appendix_headings = {item.getPlainText() for item in appendices if isinstance(item, Paragraph)}

    assert sum(isinstance(item, PageBreak) for item in management) == 4
    assert {
        "Executive Summary",
        "Supplier Quality Overview",
        "Applicable Quality Policies",
        "Findings and Recommended Actions",
        "Methodology and Limitations",
    }.issubset(management_headings)
    assert {
        "Appendix A - Detailed Calculation Metrics",
        "Appendix B - Evidence and Lineage",
        "Appendix C - Execution Trace",
    }.issubset(appendix_headings)
    assert "Key Metrics" not in management_headings


def test_pdf_renderer_handles_many_suppliers_long_ids_and_policy_names() -> None:
    document = _document(ReportFormat.PDF)
    metrics = []
    metric_names = (
        "defect_count",
        "inspected_count",
        "defect_rate",
        "period_over_period_trend",
    )
    for metric_name in metric_names:
        for supplier_index in range(1, 16):
            for month_index, month in enumerate(("2026-04", "2026-05", "2026-06"), start=1):
                if metric_name == "defect_count":
                    value: int | float | None = 10 + supplier_index + month_index
                    unit = "count"
                    numerator: int | float | None = value
                    denominator: int | float | None = None
                elif metric_name == "inspected_count":
                    value = 1_000 + supplier_index * 10
                    unit = "count"
                    numerator = value
                    denominator = None
                elif metric_name == "defect_rate":
                    value = round(0.005 + supplier_index * 0.002 + month_index * 0.0001, 4)
                    unit = "ratio"
                    numerator = 10 + supplier_index + month_index
                    denominator = 1_000 + supplier_index * 10
                else:
                    value = None if month_index == 1 else 0.0001
                    unit = "ratio_delta"
                    numerator = 0.01 + month_index * 0.0001
                    denominator = None if month_index == 1 else 0.01
                metrics.append(
                    {
                        "metric": metric_name,
                        "dimensions": {
                            "supplier_id": f"SUP-{supplier_index:03d}",
                            "period": month,
                        },
                        "value": value,
                        "unit": unit,
                        "numerator": numerator,
                        "denominator": denominator,
                    }
                )
    analytics = AnalyticsResult.model_validate(
        {
            **document.analysis_results.model_dump(mode="json"),
            "metrics": metrics,
            "input_row_count": 45,
            "warnings": [
                "Trend is undefined for the first period (period=2026-04, supplier_id=SUP-001)"
            ],
        }
    )
    long_policy = document.applicable_policies[0].model_copy(
        update={
            "document_id": "Global Supplier Quality and Deviation Management Policy " * 3,
            "evidence_id": "E-" + "a" * 80,
        }
    )
    document = document.model_copy(
        update={
            "key_metrics": analytics.metrics,
            "analysis_results": analytics,
            "applicable_policies": (long_policy,),
            "quality_policy_findings": (long_policy,),
        }
    )

    rendered = PdfReportRenderer().render(document)
    restored = ReportDocument.model_validate(extract_pdf_report_model(rendered.content))
    assert restored.key_metrics == analytics.metrics
    assert len(restored.key_metrics) == 180
    assert rendered.content.startswith(b"%PDF-")


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
    artifact = repository.get_by_id(artifact_id, tenant_id=TENANT_ID)
    assert artifact.type is artifact_type
    assert Path(artifact.location).suffix == suffix
    assert artifact.checksum.startswith("sha256:")
    assert repository.exists(artifact_id, tenant_id=TENANT_ID)
    assert repository.list_by_task(TASK_ID, tenant_id=TENANT_ID) == (artifact,)
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
    with pytest.raises(ReportInputError, match="does not exist"):
        cross_tool.execute(
            JsonObject(request.model_dump(mode="json")),
            report_context(request),
        )
    assert repository.list_by_task(TASK_ID, tenant_id=TENANT_ID) == ()


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
    assert repository.list_by_task(TASK_ID, tenant_id=TENANT_ID) == ()
    assert list(tmp_path.iterdir()) == []


def test_report_tool_maps_artifact_size_limit_to_typed_error(tmp_path: Path) -> None:
    tool, repository = report_tool(tmp_path, max_size_bytes=16)
    request = report_request()

    with pytest.raises(ReportSizeLimitError) as exceeded:
        tool.execute(JsonObject(request.model_dump(mode="json")), report_context(request))

    assert exceeded.value.error.error_code == "ARTIFACT_SIZE_LIMIT_EXCEEDED"
    assert repository.list_by_task(TASK_ID, tenant_id=TENANT_ID) == ()
