"""Independent consistency and Artifact checks for the AP report profile."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from copilot.contracts import Artifact, ArtifactType, EvidenceItem, EvidenceType
from copilot.evidence.ap_validators import parse_ap_calculation_runs
from copilot.security import SensitiveDataRegistry
from copilot.tools.analytics.ap_schemas import APAnalyticsOperation
from copilot.tools.reporting.ap_schemas import (
    AccountsPayableReportV1,
    APDetailAccess,
    APReportRequestV1,
)
from copilot.tools.reporting.exceptions import ReportConsistencyError
from copilot.tools.reporting.renderer import extract_pdf_report_model
from copilot.tools.reporting.schemas import ReportFormat


class AccountsPayableReportValidator:
    """Validate AP inputs, canonical model identity, render round trips, and bytes."""

    def validate_pre_render(
        self,
        request: APReportRequestV1,
        document: AccountsPayableReportV1,
        evidence: tuple[EvidenceItem, ...],
        *,
        tenant_id: str,
    ) -> None:
        """Fail closed on scope, lineage, numeric-source, or disclosure drift."""
        if document.task_summary.task_id != request.task_id:
            raise ReportConsistencyError("AP report task identity differs from its request")
        if document.scope != request.scope:
            raise ReportConsistencyError("AP report scope differs from its authorized input")
        if document.execution_metadata.detail_access is not request.detail_access:
            raise ReportConsistencyError("AP report detail mode differs from trusted policy")
        report_evidence_ids = tuple(item.evidence_id for item in document.evidence.references)
        if report_evidence_ids != request.evidence_refs:
            raise ReportConsistencyError("AP report Evidence index differs from its input")
        if tuple(item.evidence_id for item in evidence) != request.evidence_refs:
            raise ReportConsistencyError("Resolved AP Evidence differs from report references")
        required_types = {EvidenceType.DOCUMENT, EvidenceType.DATABASE, EvidenceType.CALCULATION}
        if not required_types.issubset({item.source_type for item in evidence}):
            raise ReportConsistencyError(
                "AP report requires Document, Database, and Calculation Evidence"
            )
        runs, issues = parse_ap_calculation_runs(
            task_id=request.task_id,
            tenant_id=tenant_id,
            evidence=evidence,
        )
        if issues:
            raise ReportConsistencyError("AP Calculation Evidence did not pass reconstruction")
        summaries = tuple(
            run for run in runs if run.operation is APAnalyticsOperation.EXCEPTION_SUMMARY
        )
        supplier_rates = tuple(
            run for run in runs if run.operation is APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE
        )
        if len(summaries) != 1 or len(supplier_rates) != 1:
            raise ReportConsistencyError(
                "AP report requires one exception summary and one supplier-rate run"
            )
        if summaries[0].result != request.exception_summary_result:
            raise ReportConsistencyError("AP report summary differs from Calculation Evidence")
        if document.exception_summary.metrics != request.exception_summary_result.metrics:
            raise ReportConsistencyError("AP report canonical metrics differ from Analytics")
        if request.detail_access is APDetailAccess.AGGREGATE and _has_detail(document):
            raise ReportConsistencyError("Aggregate AP report contains record-level details")
        reject_ap_restricted_fields(document.model_dump(mode="json"))

    def validate_rendered(
        self,
        document: AccountsPayableReportV1,
        report_format: ReportFormat,
        content: bytes,
    ) -> None:
        """Require a valid format and exact canonical-model round trip."""
        if not content:
            raise ReportConsistencyError("Rendered AP report is empty")
        try:
            if report_format is ReportFormat.JSON:
                restored = AccountsPayableReportV1.model_validate_json(content)
            else:
                if not content.startswith(b"%PDF-"):
                    raise ReportConsistencyError("Rendered AP PDF header is invalid")
                restored = AccountsPayableReportV1.model_validate(extract_pdf_report_model(content))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            if isinstance(exc, ReportConsistencyError):
                raise
            raise ReportConsistencyError("Rendered AP report model is invalid") from exc
        if restored != document:
            raise ReportConsistencyError("Rendered AP report model differs from its source")

    def validate_artifact(
        self,
        artifact: Artifact,
        *,
        report_format: ReportFormat,
        content: bytes,
        root: Path | None = None,
    ) -> None:
        """Verify current-task AP Artifact type, location, checksum, size, and readability."""
        expected_type = (
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF
            if report_format is ReportFormat.PDF
            else ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON
        )
        if artifact.type is not expected_type:
            raise ReportConsistencyError("AP Artifact type differs from rendered format")
        expected_checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if artifact.checksum != expected_checksum or artifact.size_bytes != len(content):
            raise ReportConsistencyError("AP Artifact checksum or size is invalid")
        path = Path(artifact.location).resolve()
        if root is not None and path.parent != root.resolve():
            raise ReportConsistencyError("AP Artifact path is outside governed storage")
        expected_suffix = ".pdf" if report_format is ReportFormat.PDF else ".json"
        if path.suffix.lower() != expected_suffix:
            raise ReportConsistencyError("AP Artifact extension differs from its format")
        try:
            disk_content = path.read_bytes()
        except OSError as exc:
            raise ReportConsistencyError("AP Artifact is unreadable after commit") from exc
        if disk_content != content:
            raise ReportConsistencyError("AP Artifact bytes differ from rendered content")


def reject_ap_restricted_fields(value: object, prefix: str = "") -> None:
    """Apply the stricter AP report field policy before shared redaction can repair it."""
    registry = SensitiveDataRegistry()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if registry.policy_for(key) is not None:
                raise ReportConsistencyError(
                    f"AP report contains a forbidden restricted field at {path}"
                )
            reject_ap_restricted_fields(child, path)
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            reject_ap_restricted_fields(child, f"{prefix}[{index}]")


def _has_detail(document: AccountsPayableReportV1) -> bool:
    return any(
        (
            document.duplicate_invoice_findings,
            document.po_compliance_findings,
            document.payment_findings,
            document.material_exceptions,
        )
    )


__all__ = ["AccountsPayableReportValidator", "reject_ap_restricted_fields"]
