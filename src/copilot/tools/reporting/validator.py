"""Pre-render, post-render, and Artifact consistency checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from copilot.contracts import Artifact, ArtifactType, EvidenceItem, EvidenceType
from copilot.contracts.base import JsonMapping
from copilot.tools.reporting.composer import calculation_metrics
from copilot.tools.reporting.exceptions import ReportConsistencyError
from copilot.tools.reporting.renderer import extract_pdf_report_model
from copilot.tools.reporting.schemas import ReportDocument, ReportFormat, ReportRequest


class ReportValidator:
    """Validate structured inputs and rendered bytes without parsing prose for facts."""

    def validate_pre_render(
        self,
        request: ReportRequest,
        document: ReportDocument,
        evidence: tuple[EvidenceItem, ...],
    ) -> None:
        """Check Task ownership, Evidence lineage, and Analytics value identity."""
        if document.task_summary.task_id != request.task_id:
            raise ReportConsistencyError("Report task identity differs from the request")
        if document.scope != request.scope:
            raise ReportConsistencyError("Report scope differs from the authorized input")
        if document.key_metrics != request.analysis_result.metrics:
            raise ReportConsistencyError("Canonical report metrics differ from Analytics output")
        if tuple(item.evidence_id for item in document.evidence) != request.evidence_refs:
            raise ReportConsistencyError("Canonical report Evidence index differs from input")
        evidence_by_id = {item.evidence_id: item for item in evidence}
        if tuple(evidence_by_id) != request.evidence_refs:
            raise ReportConsistencyError("Resolved Evidence does not match report references")
        types = {item.source_type for item in evidence}
        required = {EvidenceType.DOCUMENT, EvidenceType.DATABASE, EvidenceType.CALCULATION}
        if not required.issubset(types):
            raise ReportConsistencyError(
                "Report requires document, database, and calculation Evidence"
            )
        database_ids = {
            item.evidence_id for item in evidence if item.source_type is EvidenceType.DATABASE
        }
        calculation_items = [
            item for item in evidence if item.source_type is EvidenceType.CALCULATION
        ]
        for item in calculation_items:
            if not set(item.source_reference.input_evidence_ids).intersection(database_ids):
                raise ReportConsistencyError(
                    "Calculation Evidence does not trace to referenced Database Evidence"
                )
            reference = item.source_reference.reference.root
            if not reference.get("formula") and not reference.get("formulas"):
                raise ReportConsistencyError("Calculation Evidence lacks formula metadata")
        expected_metrics = request.analysis_result.model_dump(mode="json")["metrics"]
        evidence_metrics = [
            metric for item in calculation_items for metric in calculation_metrics(item)
        ]
        if evidence_metrics != expected_metrics:
            raise ReportConsistencyError("Analytics output differs from Calculation Evidence")
        for item in evidence:
            if item.source_type is EvidenceType.DATABASE:
                reference = item.source_reference.reference.root
                if not reference.get("query_fingerprint") and not reference.get("query_id"):
                    raise ReportConsistencyError("Database Evidence lacks query lineage")

    def validate_rendered(
        self,
        document: ReportDocument,
        report_format: ReportFormat,
        content: bytes,
    ) -> None:
        """Check format structure and exact round-trip model identity."""
        if not content:
            raise ReportConsistencyError("Rendered report is empty")
        if report_format is ReportFormat.JSON:
            try:
                raw = json.loads(content.decode("utf-8"))
                restored = ReportDocument.model_validate(raw)
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
                raise ReportConsistencyError("Rendered JSON report is invalid") from exc
        else:
            if not content.startswith(b"%PDF-"):
                raise ReportConsistencyError("Rendered PDF header is invalid")
            try:
                restored = ReportDocument.model_validate(extract_pdf_report_model(content))
            except (ValueError, ValidationError) as exc:
                raise ReportConsistencyError(
                    "Rendered PDF lacks a valid embedded report model"
                ) from exc
        if restored != document:
            raise ReportConsistencyError("Rendered report model differs from its source")

    def validate_artifact(
        self,
        artifact: Artifact,
        *,
        report_format: ReportFormat,
        content: bytes,
        root: Path | None = None,
    ) -> None:
        """Verify immutable metadata against final on-disk bytes."""
        expected_type = (
            ArtifactType.QUALITY_ANALYSIS_REPORT_PDF
            if report_format is ReportFormat.PDF
            else ArtifactType.QUALITY_ANALYSIS_REPORT_JSON
        )
        if artifact.type is not expected_type:
            raise ReportConsistencyError("Artifact type differs from rendered format")
        expected_checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if artifact.checksum != expected_checksum or artifact.size_bytes != len(content):
            raise ReportConsistencyError("Artifact checksum or size metadata is invalid")
        path = Path(artifact.location).resolve()
        if root is not None and path.parent != root.resolve():
            raise ReportConsistencyError("Artifact path is outside governed storage")
        extension = ".pdf" if report_format is ReportFormat.PDF else ".json"
        if path.suffix.lower() != extension:
            raise ReportConsistencyError("Artifact extension differs from its format")
        try:
            disk_content = path.read_bytes()
        except OSError as exc:
            raise ReportConsistencyError("Artifact is not readable after commit") from exc
        if disk_content != content:
            raise ReportConsistencyError("Artifact bytes differ from rendered content")


def report_mapping_from_bytes(content: bytes, artifact_type: ArtifactType) -> JsonMapping:
    """Return the structured report model carried by either frozen Artifact format."""
    if artifact_type is ArtifactType.QUALITY_ANALYSIS_REPORT_JSON:
        raw = json.loads(content.decode("utf-8"))
    else:
        raw = extract_pdf_report_model(content)
    if not isinstance(raw, dict):
        raise ValueError("report model root must be an object")
    return cast(JsonMapping, raw)


__all__ = ["ReportValidator", "report_mapping_from_bytes"]
