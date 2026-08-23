"""Governed ``analysis_engine`` adapter for Accounts Payable analytics v1."""

from __future__ import annotations

import hashlib
import json
import logging
from math import ceil
from typing import cast

from pydantic import JsonValue, ValidationError

from copilot.contracts import (
    EvidenceContent,
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
from copilot.tools.analytics.ap_lineage import APAnalyticsLineageValidator
from copilot.tools.analytics.ap_operations import (
    AP_FORMULA_CATALOGUE,
    run_detection,
    run_exception_summary,
    run_supplier_exception_rate,
)
from copilot.tools.analytics.ap_schemas import (
    AP_ANALYTICS_ENGINE_VERSION,
    AP_ANALYTICS_INPUT_SCHEMA,
    AP_ANALYTICS_OUTPUT_SCHEMA,
    AP_ANALYTICS_REQUEST_ADAPTER,
    AP_ANALYTICS_TOOL_VERSION,
    AP_CALCULATION_BATCH_SIZE,
    APAnalyticsOperation,
    APAnalyticsRequestV1,
    APAnalyticsResultV1,
    APDetectionRequestV1,
    APExceptionSummaryRequestV1,
)
from copilot.tools.analytics.exceptions import (
    APAnalyticsInputError,
    APAnalyticsOperationUnsupportedError,
)
from copilot.tools.base import EvidenceDraft, ToolExecutionContext, ToolExecutionOutput

LOGGER = logging.getLogger(__name__)


class AccountsPayableAnalyticsTool:
    """Execute only the seven frozen AP operations with exact Evidence lineage."""

    definition = ToolDefinition(
        tool_name="analysis_engine",
        tool_version=AP_ANALYTICS_TOOL_VERSION,
        description=(
            "Deterministically execute the seven Accounts Payable v1 analytics operations "
            "over checksum-bound governed Evidence; no LLM calculation, external access, "
            "currency conversion, arbitrary code, or business mutation"
        ),
        input_schema=JsonObject(AP_ANALYTICS_INPUT_SCHEMA),
        output_schema=JsonObject(AP_ANALYTICS_OUTPUT_SCHEMA),
        risk_level=RiskLevel.LOW,
        timeout=ToolTimeout(attempt_seconds=20, overall_seconds=40),
        approval_policy=ToolApprovalPolicy(
            policy_id="accounts-payable-analytics-v1-policy",
            trigger_conditions=(),
            approver_role=None,
            editable_fields=(),
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=(
                "operation_name",
                "operation_version",
                "dataset_checksums",
                "rule_manifest_checksum",
                "canonical_parameters",
                "engine_version",
            ),
            reuse_window_seconds=300,
            side_effects="None; deterministic in-memory Accounts Payable calculation",
        ),
    )

    def __init__(self, evidence_reader: EvidenceReader) -> None:
        self._lineage = APAnalyticsLineageValidator(evidence_reader)
        self.call_count = 0

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Validate scope/lineage, execute one operation, and emit batched Evidence drafts."""
        context.cancellation.raise_if_requested()
        self.call_count += 1
        request = self._parse_request(arguments)
        validated = self._lineage.validate(request, context)
        context.cancellation.raise_if_requested()
        if isinstance(request, APDetectionRequestV1):
            result = run_detection(
                request,
                population=validated.population,
                dedicated=validated.dedicated,
            )
        elif isinstance(request, APExceptionSummaryRequestV1):
            population_dataset = request.datasets[0]
            result = run_exception_summary(
                request_operation=APAnalyticsOperation.EXCEPTION_SUMMARY,
                population=validated.population,
                calculation_results=validated.calculation_results,
                manifest_checksum=request.rule_snapshot.rule_manifest.manifest_checksum,
                rule_set_version=request.rule_snapshot.rule_manifest.rule_set_version,
                population_checksum=population_dataset.dataset_checksum,
            )
        else:
            population_dataset = request.datasets[0]
            result = run_supplier_exception_rate(
                population=validated.population,
                calculation_results=validated.calculation_results,
                manifest_checksum=request.rule_snapshot.rule_manifest.manifest_checksum,
                rule_set_version=request.rule_snapshot.rule_manifest.rule_set_version,
                population_checksum=population_dataset.dataset_checksum,
            )
        context.cancellation.raise_if_requested()
        drafts = _calculation_evidence(
            result,
            parent_evidence_ids=validated.parent_evidence_ids,
            task_id=context.call.task_id,
        )
        output = JsonObject(cast(JsonMapping, result.model_dump(mode="json")))
        LOGGER.info(
            "Deterministic Accounts Payable analytics evidence prepared",
            extra={
                "event": "ap_analytics_evidence_prepared",
                "task_id": context.call.task_id,
                "tool_call_id": context.call.tool_call_id,
                "tool_name": self.definition.tool_name,
                "operation_name": result.operation_name.value,
                "input_row_count": result.input_row_count,
                "eligibility_count": result.eligibility_count,
                "exception_count": len(result.records),
                "exclusion_count": result.exclusion_count,
                "evidence_batch_count": len(drafts),
                "rule_set_version": result.rule_set_version,
            },
        )
        context.cancellation.raise_if_requested()
        return ToolExecutionOutput(output=output, evidence=drafts)

    @staticmethod
    def _parse_request(arguments: JsonObject) -> APAnalyticsRequestV1:
        raw_operation = arguments.root.get("operation_name")
        if isinstance(raw_operation, str):
            try:
                APAnalyticsOperation(raw_operation)
            except ValueError as exc:
                raise APAnalyticsOperationUnsupportedError() from exc
        try:
            return AP_ANALYTICS_REQUEST_ADAPTER.validate_python(arguments.root)
        except ValidationError as exc:
            raise APAnalyticsInputError() from exc


def _calculation_evidence(
    result: APAnalyticsResultV1,
    *,
    parent_evidence_ids: tuple[str, ...],
    task_id: str,
) -> tuple[EvidenceDraft, ...]:
    items: list[JsonMapping] = []
    items.extend(
        {"kind": "exception", "value": cast(JsonValue, item.model_dump(mode="json"))}
        for item in result.records
    )
    items.extend(
        {"kind": "duplicate_group", "value": cast(JsonValue, item.model_dump(mode="json"))}
        for item in result.duplicate_groups
    )
    items.extend(
        {"kind": "supplier_rate", "value": cast(JsonValue, item.model_dump(mode="json"))}
        for item in result.supplier_rates
    )
    items.extend(
        {"kind": "exclusion", "value": cast(JsonValue, item.model_dump(mode="json"))}
        for item in result.exclusions
    )
    batch_count = max(1, ceil(len(items) / AP_CALCULATION_BATCH_SIZE))
    run_id = _calculation_run_id(result, parent_evidence_ids, task_id)
    result_metadata = cast(
        JsonMapping,
        result.model_dump(
            mode="json",
            exclude={"records", "duplicate_groups", "supplier_rates", "exclusions"},
        ),
    )
    formulas = list(AP_FORMULA_CATALOGUE[result.operation_name])
    drafts: list[EvidenceDraft] = []
    for batch_index in range(batch_count):
        start = batch_index * AP_CALCULATION_BATCH_SIZE
        selected = items[start : start + AP_CALCULATION_BATCH_SIZE]
        content_data: JsonMapping = {
            "result_metadata": cast(JsonValue, result_metadata),
            "batch_items": cast(JsonValue, selected),
        }
        reference: JsonMapping = {
            "operation_name": result.operation_name.value,
            "operation_version": result.operation_version,
            "engine_version": AP_ANALYTICS_ENGINE_VERSION,
            "formulas": cast(JsonValue, formulas),
            "normalization_version": result.normalization_version,
            "precision": result.precision,
            "rounding_mode": result.rounding_mode,
            "rule_ids": list(result.rule_ids),
            "rule_set_version": result.rule_set_version,
            "manifest_checksum": result.manifest_checksum,
            "input_checksums": list(result.input_checksums),
            "calculation_run_id": run_id,
            "batch_index": batch_index,
            "batch_count": batch_count,
            "output_checksum": result.output_checksum,
        }
        drafts.append(
            EvidenceDraft(
                source_type=EvidenceType.CALCULATION,
                source_reference=EvidenceSourceReference(
                    reference=JsonObject(reference),
                    input_evidence_ids=parent_evidence_ids,
                ),
                content=EvidenceContent(
                    data=JsonObject(content_data),
                    classification="CONFIDENTIAL",
                    checksum=_checksum(content_data),
                ),
            )
        )
    return tuple(drafts)


def _calculation_run_id(
    result: APAnalyticsResultV1,
    parent_evidence_ids: tuple[str, ...],
    task_id: str,
) -> str:
    identity = {
        "task_id": task_id,
        "operation_name": result.operation_name.value,
        "output_checksum": result.output_checksum,
        "parent_evidence_ids": list(parent_evidence_ids),
    }
    return f"APCALC-{_checksum(identity).removeprefix('sha256:')[:24]}"


def _checksum(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = ["AccountsPayableAnalyticsTool"]
