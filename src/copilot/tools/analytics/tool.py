"""Governed deterministic implementation of ``analysis_engine`` v1.0."""

from __future__ import annotations

import logging
from typing import cast

from pydantic import JsonValue, ValidationError

from copilot.contracts import (
    EvidenceContent,
    EvidenceItem,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    RiskLevel,
    ToolApprovalPolicy,
    ToolDefinition,
    ToolIdempotency,
    ToolTimeout,
)
from copilot.contracts.base import JsonMapping
from copilot.services.workflows.ports import EvidenceReader
from copilot.tools.analytics.exceptions import AnalyticsInputDeniedError, AnalyticsInputError
from copilot.tools.analytics.metrics import FORMULAS, calculate_metrics
from copilot.tools.analytics.schemas import (
    ANALYTICS_ENGINE_VERSION,
    ANALYTICS_INPUT_SCHEMA,
    ANALYTICS_OUTPUT_SCHEMA,
    AnalyticsRequest,
    AnalyticsResult,
)
from copilot.tools.analytics.validators import canonical_checksum, checksum_matches, validate_result
from copilot.tools.base import EvidenceDraft, ToolExecutionContext, ToolExecutionOutput

LOGGER = logging.getLogger(__name__)


class AnalyticsTool:
    """Calculate the four frozen quality metrics without external access or arbitrary code."""

    definition = ToolDefinition(
        tool_name="analysis_engine",
        tool_version="1.0.0",
        description=(
            "Deterministically calculate the four Supplier Quality v1.0 metrics from one "
            "checksum-bound Database Tool dataset; no external access or arbitrary code execution"
        ),
        input_schema=JsonObject(ANALYTICS_INPUT_SCHEMA),
        output_schema=JsonObject(ANALYTICS_OUTPUT_SCHEMA),
        risk_level=RiskLevel.LOW,
        timeout=ToolTimeout(attempt_seconds=15, overall_seconds=25),
        approval_policy=ToolApprovalPolicy(
            policy_id="analysis-engine-v1-policy",
            trigger_conditions=(),
            approver_role=None,
            editable_fields=(),
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=(
                "dataset_checksum",
                "metrics",
                "group_by",
                "engine_version",
            ),
            reuse_window_seconds=300,
            side_effects="None; deterministic in-memory calculation",
        ),
    )

    def __init__(self, evidence_reader: EvidenceReader) -> None:
        self._evidence_reader = evidence_reader
        self.call_count = 0
        self.received_evidence_ids: list[str] = []

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Validate lineage, calculate metrics, and return one calculation evidence draft."""
        context.cancellation.raise_if_requested()
        self.call_count += 1
        request = self._parse_request(arguments)
        source_evidence = self._validate_lineage(request, context)
        self.received_evidence_ids.append(request.dataset_evidence_id)

        raw_dataset = arguments.root["dataset"]
        if not checksum_matches(raw_dataset, request.dataset_checksum):
            raise AnalyticsInputDeniedError("Analytics dataset checksum does not match its input")

        context.cancellation.raise_if_requested()
        metrics, warnings = calculate_metrics(request)
        context.cancellation.raise_if_requested()
        result = AnalyticsResult(
            metrics=metrics,
            warnings=warnings,
            input_row_count=len(request.dataset),
            dataset_checksum=request.dataset_checksum,
            calculation_version="quality_metrics.v1",
            empty_result=not request.dataset,
        )
        validate_result(result)
        output_mapping = cast(JsonMapping, result.model_dump(mode="json"))
        evidence_data = JsonObject(
            cast(
                JsonMapping,
                {
                    "metrics": output_mapping["metrics"],
                    "warnings": output_mapping["warnings"],
                    "input_row_count": output_mapping["input_row_count"],
                    "empty_result": output_mapping["empty_result"],
                },
            )
        )
        evidence = EvidenceDraft(
            source_type=EvidenceType.CALCULATION,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "operation": "deterministic_quality_metrics",
                        "formulas": {metric.value: FORMULAS[metric] for metric in request.metrics},
                        "engine_version": ANALYTICS_ENGINE_VERSION,
                        "dataset_checksum": request.dataset_checksum,
                        "group_by": [dimension.value for dimension in request.group_by],
                    }
                ),
                input_evidence_ids=(request.dataset_evidence_id,),
            ),
            content=EvidenceContent(
                data=evidence_data,
                classification=source_evidence.content.classification,
                checksum=canonical_checksum(cast(JsonValue, evidence_data.root)),
            ),
        )
        LOGGER.info(
            "Deterministic analytics evidence prepared",
            extra={
                "event": "analytics_evidence_prepared",
                "task_id": context.call.task_id,
                "tool_call_id": context.call.tool_call_id,
                "tool_name": self.definition.tool_name,
                "input_row_count": len(request.dataset),
                "metric_count": len(metrics),
                "warning_count": len(warnings),
                "dataset_evidence_id": request.dataset_evidence_id,
            },
        )
        context.cancellation.raise_if_requested()
        return ToolExecutionOutput(output=JsonObject(output_mapping), evidence=(evidence,))

    @staticmethod
    def _parse_request(arguments: JsonObject) -> AnalyticsRequest:
        try:
            return AnalyticsRequest.model_validate(arguments.root)
        except ValidationError as exc:
            raise AnalyticsInputError() from exc

    def _validate_lineage(
        self,
        request: AnalyticsRequest,
        context: ToolExecutionContext,
    ) -> EvidenceItem:
        try:
            evidence = self._evidence_reader.get(
                request.dataset_evidence_id,
                task_id=context.call.task_id,
                tenant_id=context.tenant_id,
            )
        except (KeyError, LookupError) as exc:
            raise AnalyticsInputDeniedError("Analytics input evidence does not exist") from exc
        if evidence.task_id != context.call.task_id:
            raise AnalyticsInputDeniedError("Analytics input evidence belongs to a different task")
        if evidence.source_type is not EvidenceType.DATABASE:
            raise AnalyticsInputDeniedError("Analytics input evidence is not database evidence")
        if evidence.content.checksum != request.dataset_checksum:
            raise AnalyticsInputDeniedError(
                "Analytics input checksum does not match database evidence"
            )
        return evidence


__all__ = ["AnalyticsTool"]
