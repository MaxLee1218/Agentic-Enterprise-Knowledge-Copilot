"""Governed production adapter for the frozen ``report_generator`` capability."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import cast

from pydantic import JsonValue, ValidationError

from copilot.contracts import (
    Artifact,
    ArtifactType,
    EvidenceItem,
    JsonObject,
    RiskLevel,
    ToolApprovalPolicy,
    ToolDefinition,
    ToolIdempotency,
    ToolTimeout,
)
from copilot.persistence.identifiers import UuidIdentifierFactory
from copilot.services.workflows.ports import ArtifactStore, EvidenceReader, IdentifierFactory
from copilot.tools.base import ToolExecutionContext, ToolExecutionOutput
from copilot.tools.reporting.composer import ReportComposer
from copilot.tools.reporting.exceptions import (
    ReportConsistencyError,
    ReportInputDeniedError,
    ReportInputError,
    ReportPersistenceError,
)
from copilot.tools.reporting.renderer import RendererRegistry
from copilot.tools.reporting.schemas import (
    REPORT_GENERATOR_VERSION,
    REPORT_INPUT_SCHEMA,
    REPORT_OUTPUT_SCHEMA,
    ReportDocument,
    ReportFormat,
    ReportRequest,
)
from copilot.tools.reporting.validator import ReportValidator

LOGGER = logging.getLogger(__name__)


class ReportTool:
    """Compose, validate, render, and atomically persist one internal report Artifact."""

    definition = ToolDefinition(
        tool_name="report_generator",
        tool_version="1.0.0",
        description=(
            "Render a structured Supplier Quality report from current-Task Analytics output "
            "and Evidence; no querying, recalculation, external publication, or model generation"
        ),
        input_schema=JsonObject(REPORT_INPUT_SCHEMA),
        output_schema=JsonObject(REPORT_OUTPUT_SCHEMA),
        risk_level=RiskLevel.LOW,
        timeout=ToolTimeout(attempt_seconds=30, overall_seconds=55),
        approval_policy=ToolApprovalPolicy(
            policy_id="report-generator-v1-policy",
            trigger_conditions=("task_contract_requires_approval",),
            approver_role="quality_data_approver",
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=(
                "template_version",
                "normalized_input",
                "evidence_checksums",
                "format",
            ),
            reuse_window_seconds=300,
            side_effects="Atomic internal Artifact creation only; no business-data mutation",
        ),
    )

    def __init__(
        self,
        *,
        evidence_reader: EvidenceReader,
        artifact_store: ArtifactStore,
        clock: Callable[[], datetime],
        ids: IdentifierFactory | None = None,
        composer: ReportComposer | None = None,
        validator: ReportValidator | None = None,
        renderers: RendererRegistry | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._clock = clock
        self._ids = ids or UuidIdentifierFactory()
        self._composer = composer or ReportComposer(
            evidence_reader,
            clock=clock,
        )
        self._validator = validator or ReportValidator()
        self._renderers = renderers or RendererRegistry()
        self._idempotent_artifacts: dict[str, str] = {}
        self._lock = RLock()
        self.call_count = 0
        self.received_evidence_ids: list[tuple[str, ...]] = []

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Execute one authorized report attempt through all consistency gates."""
        self.call_count += 1
        request = self._parse_request(arguments)
        if request.task_id != context.call.task_id:
            raise ReportInputDeniedError("Report task_id differs from the trusted invocation")
        self.received_evidence_ids.append(request.evidence_refs)

        with self._lock:
            cached_id = self._idempotent_artifacts.get(context.call.idempotency_key)
        if cached_id is not None:
            try:
                cached = self._artifact_store.get(cached_id)
            except (KeyError, LookupError):
                cached = None
            if cached is not None:
                evidence = self._composer.load_evidence(request, task_id=context.call.task_id)
                return ToolExecutionOutput(output=self._tool_output(cached, evidence))

        evidence = self._composer.load_evidence(request, task_id=context.call.task_id)
        raw_report = self._build_report(arguments, evidence)
        try:
            document = ReportDocument.model_validate(raw_report)
        except ValidationError as exc:
            raise ReportInputError(
                "Report model could not be built from structured inputs"
            ) from exc
        self._validator.validate_pre_render(request, document, evidence)
        renderer = self._renderers.get(request.format)
        rendered = renderer.render(document)
        self._validator.validate_rendered(document, request.format, rendered.content)

        artifact_id = self._ids.new_id("A")
        filename = self._filename(request, artifact_id, rendered.extension)
        artifact_type = (
            ArtifactType.QUALITY_ANALYSIS_REPORT_PDF
            if request.format is ReportFormat.PDF
            else ArtifactType.QUALITY_ANALYSIS_REPORT_JSON
        )
        try:
            artifact = self._artifact_store.write(
                artifact_id=artifact_id,
                task_id=request.task_id,
                artifact_type=artifact_type,
                filename=filename,
                media_type=rendered.media_type,
                content=rendered.content,
                generator_version=REPORT_GENERATOR_VERSION,
                evidence_ids=request.evidence_refs,
            )
        except (OSError, ValueError) as exc:
            raise ReportPersistenceError() from exc
        try:
            artifact_path = self._artifact_store.path_for(artifact)
            self._validator.validate_artifact(
                artifact,
                report_format=request.format,
                content=rendered.content,
                root=artifact_path.parent,
            )
        except (ReportConsistencyError, OSError, ValueError) as validation_error:
            try:
                self._artifact_store.delete(artifact.artifact_id)
            except (KeyError, OSError, ValueError) as exc:
                raise ReportPersistenceError() from exc
            if isinstance(validation_error, ReportConsistencyError):
                raise
            raise ReportPersistenceError() from validation_error
        with self._lock:
            self._idempotent_artifacts[context.call.idempotency_key] = artifact.artifact_id
        LOGGER.info(
            "Supplier Quality report Artifact committed",
            extra={
                "event": "report_artifact_committed",
                "task_id": request.task_id,
                "tool_call_id": context.call.tool_call_id,
                "tool_name": self.definition.tool_name,
                "artifact_id": artifact.artifact_id,
                "format": request.format.value,
                "size_bytes": artifact.size_bytes,
                "evidence_count": len(request.evidence_refs),
            },
        )
        return ToolExecutionOutput(output=self._tool_output(artifact, evidence))

    def _build_report(
        self,
        arguments: JsonObject,
        evidence: tuple[EvidenceItem, ...],
    ) -> dict[str, JsonValue]:
        """Compatibility seam retained for deterministic verifier regression tests."""
        request = self._parse_request(arguments)
        document = self._composer.compose(
            request,
            evidence,
        )
        return cast(dict[str, JsonValue], document.model_dump(mode="json"))

    @staticmethod
    def _parse_request(arguments: JsonObject) -> ReportRequest:
        try:
            return ReportRequest.model_validate(arguments.root)
        except ValidationError as exc:
            raise ReportInputError() from exc

    @staticmethod
    def _filename(request: ReportRequest, artifact_id: str, extension: str) -> str:
        safe_task = (
            "".join(
                character if character.isalnum() or character in "-_" else "-"
                for character in request.task_id
            )[:80]
            or "task"
        )
        safe_artifact = (
            "".join(
                character if character.isalnum() or character in "-_" else "-"
                for character in artifact_id
            )[:80]
            or "artifact"
        )
        return f"supplier-quality-analysis-{safe_task}-{safe_artifact}{extension}"

    @staticmethod
    def _tool_output(artifact: Artifact, evidence: tuple[EvidenceItem, ...]) -> JsonObject:
        citation_map: dict[str, JsonValue] = {
            item.evidence_id: item.source_type.value for item in evidence
        }
        return JsonObject(
            {
                "artifact_id": artifact.artifact_id,
                "type": artifact.type.value,
                "location": artifact.location,
                "created_at": artifact.created_at.isoformat(),
                "checksum": artifact.checksum,
                "size_bytes": artifact.size_bytes,
                "citation_map": citation_map,
                "generator_version": artifact.generator_version,
            }
        )


__all__ = ["ReportTool"]
