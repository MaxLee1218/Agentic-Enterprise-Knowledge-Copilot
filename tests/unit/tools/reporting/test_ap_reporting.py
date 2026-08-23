"""Stage 7 Accounts Payable report model, render, safety, and Artifact coverage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from copilot.contracts import (
    ArtifactType,
    EvidenceItem,
    JsonObject,
    RetryPolicy,
    RiskLevel,
    StepType,
    TaskPlan,
    TaskStep,
    VerificationContext,
)
from copilot.evidence.ap_validators import APNumericVerifier
from copilot.evidence.citations import candidate_from_ap_report
from copilot.tools.reporting import (
    AccountsPayableReportComposer,
    AccountsPayableReportV1,
    APDetailAccess,
    APJsonReportRenderer,
    APPdfReportRenderer,
    APReportRequestV1,
)
from copilot.tools.reporting.ap_schemas import (
    AP_JSON_MAX_SIZE_BYTES,
    AP_PDF_MAX_SIZE_BYTES,
)
from copilot.tools.reporting.exceptions import (
    ReportConsistencyError,
    ReportInputDeniedError,
    SensitiveOutputBlockedError,
)
from copilot.tools.reporting.renderer import extract_pdf_report_model
from copilot.tools.reporting.schemas import ReportFormat
from copilot.tools.reporting.validator import report_mapping_from_bytes
from tests.unit.tools.reporting.ap_helpers import (
    AP_REQUIRED_SECTIONS,
    FIXED_TIME,
    TASK_ID,
    TENANT_ID,
    ap_report_context,
    ap_report_fixture,
    ap_report_tool,
    ap_task_contract,
)


def _document(
    *, detail_access: APDetailAccess = APDetailAccess.DETAIL
) -> tuple[AccountsPayableReportV1, APReportRequestV1]:
    ledger, request = ap_report_fixture()
    request = request.model_copy(update={"detail_access": detail_access})
    document = AccountsPayableReportComposer(ledger, clock=lambda: FIXED_TIME).compose(
        request,
        ledger.list(TASK_ID, tenant_id=TENANT_ID),
        tenant_id=TENANT_ID,
    )
    return document, request


def test_ap_request_is_strong_and_rejects_wrong_summary_and_duplicate_evidence() -> None:
    _ledger, request = ap_report_fixture()
    payload = request.model_dump(mode="json")
    payload["evidence_refs"] = [request.evidence_refs[0], request.evidence_refs[0]]
    with pytest.raises(ValidationError):
        APReportRequestV1.model_validate(payload)

    payload = request.model_dump(mode="json")
    payload["exception_summary_result"]["operation_name"] = "ap.supplier_exception_rate.v1"
    with pytest.raises(ValidationError):
        APReportRequestV1.model_validate(payload)


def test_ap_composer_copies_canonical_values_and_aggregate_mode_excludes_details() -> None:
    detail, request = _document()
    aggregate, _aggregate_request = _document(detail_access=APDetailAccess.AGGREGATE)

    assert detail.exception_summary.metrics == request.exception_summary_result.metrics
    assert detail.duplicate_invoice_findings
    assert detail.po_compliance_findings
    assert detail.payment_findings
    assert detail.supplier_summary
    assert detail.evidence.claims
    assert all(claim.evidence_ids for claim in detail.evidence.claims)
    assert aggregate.duplicate_invoice_findings == ()
    assert aggregate.po_compliance_findings == ()
    assert aggregate.payment_findings == ()
    assert aggregate.material_exceptions == ()
    assert "invoice_record_key" not in aggregate.model_dump_json()


def test_ap_json_and_pdf_share_one_canonical_round_trip_and_are_deterministic() -> None:
    document, _request = _document()
    json_rendered = APJsonReportRenderer().render(document)
    pdf_first = APPdfReportRenderer().render(document)
    pdf_second = APPdfReportRenderer().render(document)

    assert AccountsPayableReportV1.model_validate_json(json_rendered.content) == document
    assert (
        AccountsPayableReportV1.model_validate(extract_pdf_report_model(pdf_first.content))
        == document
    )
    assert pdf_first.content == pdf_second.content
    assert pdf_first.content.startswith(b"%PDF-")
    assert len(json_rendered.content) <= AP_JSON_MAX_SIZE_BYTES
    assert len(pdf_first.content) <= AP_PDF_MAX_SIZE_BYTES


def test_generated_ap_report_maps_to_independent_stage6_claim_contract() -> None:
    ledger, request = ap_report_fixture()
    document = AccountsPayableReportComposer(ledger, clock=lambda: FIXED_TIME).compose(
        request,
        ledger.list(TASK_ID, tenant_id=TENANT_ID),
        tenant_id=TENANT_ID,
    )
    contract = ap_task_contract(request)
    candidate = candidate_from_ap_report(
        task_contract=contract,
        report_step_id="S-AP-REPORT",
        report=document.model_dump(mode="json"),
        evidence=ledger.list(TASK_ID, tenant_id=TENANT_ID),
    )

    assert tuple(item.deliverable_id for item in candidate.deliverables) == AP_REQUIRED_SECTIONS
    assert candidate.numeric_claims
    assert all(claim.operation_name for claim in candidate.numeric_claims)

    report_step = TaskStep(
        step_id="S-AP-REPORT",
        task_id=TASK_ID,
        step_type=StepType.REPORT_GENERATION,
        tool_name="report_generator",
        tool_version="2.0.0",
        contract_profile="accounts_payable_report.v1",
        input_schema=JsonObject({}),
        output_schema=JsonObject({}),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    issues = APNumericVerifier().verify(
        task_contract=contract,
        task_plan=TaskPlan(
            task_id=TASK_ID,
            steps=(report_step,),
            planning_version=1,
            created_at=FIXED_TIME,
        ),
        step_results={},
        evidence_ledger=ledger,
        verification_context=VerificationContext(
            registered_tools=(),
            tool_results=(),
        ),
        candidate_result=candidate,
    )
    assert issues == ()


@pytest.mark.parametrize(
    ("report_format", "artifact_type", "suffix"),
    [
        (ReportFormat.JSON, ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON, ".json"),
        (ReportFormat.PDF, ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF, ".pdf"),
    ],
)
def test_ap_report_tool_commits_both_formats_idempotently(
    tmp_path: Path,
    report_format: ReportFormat,
    artifact_type: ArtifactType,
    suffix: str,
) -> None:
    ledger, request = ap_report_fixture()
    request = request.model_copy(update={"format": report_format})
    tool, repository = ap_report_tool(tmp_path, ledger)
    context = ap_report_context(request)
    arguments = JsonObject(request.model_dump(mode="json"))

    first = tool.execute(arguments, context)
    second = tool.execute(arguments, context)

    assert first.output == second.output
    artifact_id = str(first.output.root["artifact_id"])
    artifact = repository.get_by_id(artifact_id, tenant_id=TENANT_ID)
    assert artifact.type is artifact_type
    assert Path(artifact.location).suffix == suffix
    assert artifact.task_id == TASK_ID
    assert set(artifact.evidence_ids) == set(request.evidence_refs)
    content = Path(artifact.location).read_bytes()
    parsed = report_mapping_from_bytes(content, artifact.type)
    restored = AccountsPayableReportV1.model_validate(parsed)
    assert restored.exception_summary.metrics == request.exception_summary_result.metrics
    assert tool.definition.risk_level is RiskLevel.LOW
    assert tool.definition.timeout.attempt_seconds == 45
    assert tool.definition.approval_policy.editable_fields == ()


def test_ap_tool_requires_exact_purpose_and_detail_scope(tmp_path: Path) -> None:
    ledger, request = ap_report_fixture()
    tool, _repository = ap_report_tool(tmp_path, ledger)
    context = ap_report_context(request)
    arguments = JsonObject(request.model_dump(mode="json"))

    with pytest.raises(ReportInputDeniedError, match="purpose"):
        tool.execute(arguments, replace(context, purpose="supplier_quality_analysis.v1"))
    with pytest.raises(ReportInputDeniedError, match="finance:ap.detail"):
        tool.execute(arguments, replace(context, scopes=("artifact.write",)))


def test_ap_report_blocks_restricted_financial_fields_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, request = ap_report_fixture()
    tool, repository = ap_report_tool(tmp_path, ledger)
    original = tool._build_report

    def malicious(
        report_request: APReportRequestV1,
        evidence: tuple[EvidenceItem, ...],
        *,
        tenant_id: str,
    ) -> dict[str, JsonValue]:
        report = original(report_request, evidence, tenant_id=tenant_id)
        report["tax_id"] = "FORBIDDEN-TAX-ID"
        return report

    monkeypatch.setattr(tool, "_build_report", malicious)
    with pytest.raises(SensitiveOutputBlockedError):
        tool.execute(
            JsonObject(request.model_dump(mode="json")),
            ap_report_context(request),
        )
    assert repository.list_by_task(TASK_ID, tenant_id=TENANT_ID) == ()


def test_corrupt_ap_artifact_parser_fails_closed() -> None:
    with pytest.raises((ValueError, UnicodeDecodeError)):
        report_mapping_from_bytes(
            b"{not-json",
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON,
        )
    with pytest.raises(ValueError):
        report_mapping_from_bytes(
            b"%PDF-1.4\nno-model",
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF,
        )


def test_aggregate_model_rejects_injected_detail() -> None:
    detail, _request = _document()
    aggregate, _aggregate_request = _document(detail_access=APDetailAccess.AGGREGATE)
    with pytest.raises(ValidationError, match="Aggregate"):
        AccountsPayableReportV1.model_validate(
            {
                **aggregate.model_dump(mode="json"),
                "payment_findings": [detail.payment_findings[0].model_dump(mode="json")],
            }
        )


def test_report_consistency_error_remains_typed() -> None:
    error = ReportConsistencyError("controlled AP report mismatch")
    assert error.error.error_code == "REPORT_INPUT_INVALID"
