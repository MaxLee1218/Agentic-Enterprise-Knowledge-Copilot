"""Governed Stage 7 adapter for the frozen Accounts Payable report profile."""

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
from copilot.security import (
    ContentSourceType,
    OutputDisposition,
    OutputGuard,
    OutputGuardBlockedError,
)
from copilot.services.workflows.ports import (
    ArtifactSizeLimitError,
    ArtifactStore,
    EvidenceReader,
    IdentifierFactory,
)
from copilot.tools.base import ToolExecutionContext, ToolExecutionOutput
from copilot.tools.reporting.ap_composer import AccountsPayableReportComposer
from copilot.tools.reporting.ap_renderer import APRendererRegistry
from copilot.tools.reporting.ap_schemas import (
    AP_JSON_MAX_SIZE_BYTES,
    AP_PDF_MAX_SIZE_BYTES,
    AP_REPORT_GENERATOR_VERSION,
    AP_REPORT_INPUT_SCHEMA,
    AP_REPORT_OUTPUT_SCHEMA,
    AP_REPORT_TOOL_VERSION,
    AccountsPayableReportV1,
    APDetailAccess,
    APReportRequestV1,
)
from copilot.tools.reporting.ap_validator import (
    AccountsPayableReportValidator,
    reject_ap_restricted_fields,
)
from copilot.tools.reporting.exceptions import (
    ReportConsistencyError,
    ReportInputDeniedError,
    ReportInputError,
    ReportPersistenceError,
    ReportSizeLimitError,
    SensitiveOutputBlockedError,
)
from copilot.tools.reporting.schemas import ReportFormat

LOGGER = logging.getLogger(__name__)


class AccountsPayableReportTool:
    """Compose, validate, render, and atomically persist one internal AP report."""

    definition = ToolDefinition(
        tool_name="report_generator",
        tool_version=AP_REPORT_TOOL_VERSION,
        description=(
            "Render a structured Accounts Payable exception report from current-task policy, "
            "Database, and Calculation Evidence; no recalculation, write, payment, or publication"
        ),
        input_schema=JsonObject(AP_REPORT_INPUT_SCHEMA),
        output_schema=JsonObject(AP_REPORT_OUTPUT_SCHEMA),
        risk_level=RiskLevel.LOW,
        timeout=ToolTimeout(attempt_seconds=45, overall_seconds=90),
        approval_policy=ToolApprovalPolicy(
            policy_id="accounts-payable-report-v1-policy",
            trigger_conditions=("task_contract_requires_approval",),
            approver_role="finance_approver",
            editable_fields=(),
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=(
                "report_schema",
                "template_version",
                "normalized_summary",
                "evidence_checksums",
                "format",
                "detail_access",
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
        composer: AccountsPayableReportComposer | None = None,
        validator: AccountsPayableReportValidator | None = None,
        renderers: APRendererRegistry | None = None,
        output_guard: OutputGuard | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._clock = clock
        self._ids = ids or UuidIdentifierFactory()
        self._composer = composer or AccountsPayableReportComposer(
            evidence_reader,
            clock=clock,
        )
        self._validator = validator or AccountsPayableReportValidator()
        self._renderers = renderers or APRendererRegistry()
        self._output_guard = output_guard or OutputGuard()
        self._idempotent_artifacts: dict[str, str] = {}
        self._lock = RLock()
        self.call_count = 0
        self.received_evidence_ids: list[tuple[str, ...]] = []

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Execute one AP report attempt through profile, disclosure, and integrity gates."""
        self.call_count += 1
        request = self._parse_request(arguments)
        if request.task_id != context.call.task_id:
            raise ReportInputDeniedError("AP report task_id differs from the trusted invocation")
        if context.purpose != "accounts_payable_analysis.v1":
            raise ReportInputDeniedError("AP report requires its exact policy purpose")
        if request.detail_access is APDetailAccess.DETAIL and "finance:ap.detail" not in set(
            context.scopes
        ):
            raise ReportInputDeniedError("AP detail report requires finance:ap.detail scope")
        self.received_evidence_ids.append(request.evidence_refs)

        with self._lock:
            cached_id = self._idempotent_artifacts.get(context.call.idempotency_key)
        if cached_id is not None:
            try:
                cached = self._artifact_store.get(cached_id, tenant_id=context.tenant_id)
            except (KeyError, LookupError):
                cached = None
            if cached is not None:
                evidence = self._composer.load_evidence(
                    request,
                    task_id=context.call.task_id,
                    tenant_id=context.tenant_id,
                )
                return ToolExecutionOutput(output=self._tool_output(cached, evidence))

        evidence = self._composer.load_evidence(
            request,
            task_id=context.call.task_id,
            tenant_id=context.tenant_id,
        )
        raw_report = self._build_report(request, evidence, tenant_id=context.tenant_id)
        try:
            reject_ap_restricted_fields(raw_report)
        except ReportConsistencyError as exc:
            raise SensitiveOutputBlockedError() from exc
        guarded_report = self._output_guard.guard(
            cast(JsonValue, raw_report),
            source_type=ContentSourceType.TOOL_OUTPUT,
            source_id=context.call.tool_call_id,
            target="report",
        )
        if (
            guarded_report.disposition is OutputDisposition.BLOCKED
            or guarded_report.content is None
            or not isinstance(guarded_report.content, dict)
        ):
            raise SensitiveOutputBlockedError()
        try:
            document = AccountsPayableReportV1.model_validate(guarded_report.content)
        except ValidationError as exc:
            raise ReportInputError(
                "AP report model could not be built from structured inputs"
            ) from exc
        self._validator.validate_pre_render(
            request,
            document,
            evidence,
            tenant_id=context.tenant_id,
        )
        rendered = self._renderers.get(request.format).render(document)
        size_limit = (
            AP_PDF_MAX_SIZE_BYTES if request.format is ReportFormat.PDF else AP_JSON_MAX_SIZE_BYTES
        )
        if len(rendered.content) > size_limit:
            raise ReportSizeLimitError()
        self._validator.validate_rendered(document, request.format, rendered.content)
        guarded_bytes = self._output_guard.guard_bytes(
            rendered.content,
            source_type=ContentSourceType.TOOL_OUTPUT,
            source_id=context.call.tool_call_id,
            media_type=rendered.media_type,
        )
        if guarded_bytes.disposition is OutputDisposition.BLOCKED:
            raise SensitiveOutputBlockedError()

        artifact_id = self._ids.new_id("A")
        filename = self._filename(request, artifact_id, rendered.extension)
        artifact_type = (
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF
            if request.format is ReportFormat.PDF
            else ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON
        )
        try:
            artifact = self._artifact_store.write(
                artifact_id=artifact_id,
                task_id=request.task_id,
                tenant_id=context.tenant_id,
                artifact_type=artifact_type,
                filename=filename,
                media_type=rendered.media_type,
                content=rendered.content,
                generator_version=AP_REPORT_GENERATOR_VERSION,
                evidence_ids=request.evidence_refs,
            )
        except OutputGuardBlockedError as exc:
            raise SensitiveOutputBlockedError() from exc
        except ArtifactSizeLimitError as exc:
            raise ReportSizeLimitError() from exc
        except (OSError, ValueError) as exc:
            raise ReportPersistenceError() from exc
        try:
            path = self._artifact_store.path_for(artifact)
            self._validator.validate_artifact(
                artifact,
                report_format=request.format,
                content=rendered.content,
                root=path.parent,
            )
        except (ReportConsistencyError, OSError, ValueError) as validation_error:
            try:
                self._artifact_store.delete(
                    artifact.artifact_id,
                    tenant_id=context.tenant_id,
                )
            except (KeyError, OSError, ValueError) as exc:
                raise ReportPersistenceError() from exc
            if isinstance(validation_error, ReportConsistencyError):
                raise
            raise ReportPersistenceError() from validation_error
        with self._lock:
            self._idempotent_artifacts[context.call.idempotency_key] = artifact.artifact_id
        LOGGER.info(
            "Accounts Payable report Artifact committed",
            extra={
                "event": "ap_report_artifact_committed",
                "task_id": request.task_id,
                "tool_call_id": context.call.tool_call_id,
                "artifact_id": artifact.artifact_id,
                "format": request.format.value,
                "detail_access": request.detail_access.value,
                "size_bytes": artifact.size_bytes,
                "evidence_count": len(request.evidence_refs),
            },
        )
        return ToolExecutionOutput(output=self._tool_output(artifact, evidence))

    def _build_report(
        self,
        request: APReportRequestV1,
        evidence: tuple[EvidenceItem, ...],
        *,
        tenant_id: str,
    ) -> dict[str, JsonValue]:
        document = self._composer.compose(request, evidence, tenant_id=tenant_id)
        return cast(dict[str, JsonValue], document.model_dump(mode="json"))

    @staticmethod
    def _parse_request(arguments: JsonObject) -> APReportRequestV1:
        try:
            return APReportRequestV1.model_validate(arguments.root)
        except ValidationError as exc:
            raise ReportInputError() from exc

    @staticmethod
    def _filename(request: APReportRequestV1, artifact_id: str, extension: str) -> str:
        safe_task = _safe_component(request.task_id, "task")
        safe_artifact = _safe_component(artifact_id, "artifact")
        return f"accounts-payable-analysis-{safe_task}-{safe_artifact}{extension}"

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


def _safe_component(value: str, fallback: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in value
    )
    return normalized[:80] or fallback


__all__ = ["AccountsPayableReportTool"]
