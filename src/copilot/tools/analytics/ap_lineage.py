"""Evidence ownership, checksum, batching, and policy-binding checks for AP analytics."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue, ValidationError

from copilot.contracts import EvidenceItem, EvidenceType
from copilot.contracts.base import JsonMapping
from copilot.services.workflows.ports import EvidenceReader
from copilot.tools.analytics.ap_operations import AP_FORMULA_CATALOGUE
from copilot.tools.analytics.ap_schemas import (
    AP_ANALYTICS_ENGINE_VERSION,
    AP_ANALYTICS_OPERATION_VERSION,
    AP_CALCULATION_BATCH_SIZE,
    APAnalyticsOperation,
    APAnalyticsRequestV1,
    APAnalyticsResultV1,
    APDatabaseTemplate,
    APDetectionRequestV1,
    APDuplicateInvoiceRowV1,
    APInvoicePopulationRowV1,
    APInvoicePOVarianceRowV1,
    APPaymentAmountRowV1,
    APPaymentTermsRowV1,
)
from copilot.tools.analytics.ap_validators import (
    parse_dataset_rows,
    validate_dedicated_rows,
    validate_population,
)
from copilot.tools.analytics.exceptions import (
    APAnalyticsDataConsistencyError,
    APAnalyticsInputDeniedError,
    APAnalyticsInputError,
    APAnalyticsScopeTooLargeError,
    APPolicyRuleBindingMismatchError,
)
from copilot.tools.base import ToolExecutionContext


@dataclass(frozen=True, slots=True)
class APValidatedInputs:
    """Fully lineage-checked inputs ready for deterministic pure operations."""

    population: dict[str, APInvoicePopulationRowV1]
    dedicated: tuple[
        APDuplicateInvoiceRowV1
        | APInvoicePOVarianceRowV1
        | APPaymentTermsRowV1
        | APPaymentAmountRowV1,
        ...,
    ]
    calculation_results: tuple[APAnalyticsResultV1, ...]
    parent_evidence_ids: tuple[str, ...]


class APAnalyticsLineageValidator:
    """Fail closed unless every dataset, rule, and batch belongs to the current task."""

    def __init__(self, evidence_reader: EvidenceReader) -> None:
        self._evidence_reader = evidence_reader

    def validate(
        self,
        request: APAnalyticsRequestV1,
        context: ToolExecutionContext,
    ) -> APValidatedInputs:
        """Validate the complete AP request lineage and return parsed rows/results."""
        if context.purpose != "accounts_payable_analysis.v1":
            raise APAnalyticsInputDeniedError("AP analytics requires its exact policy purpose")
        manifest = request.rule_snapshot.rule_manifest
        if manifest.tenant_id != context.tenant_id:
            raise APAnalyticsInputDeniedError("AP rule manifest belongs to a different tenant")
        if _manifest_checksum(manifest.model_dump(mode="json")) != manifest.manifest_checksum:
            raise APPolicyRuleBindingMismatchError("AP rule manifest checksum drifted")
        document_ids = self._validate_document_evidence(request, context)
        database_evidence = tuple(
            self._validate_database_dataset(dataset, context) for dataset in request.datasets
        )
        self._validate_dataset_scope_consistency(database_evidence)

        parsed = {dataset.template_id: parse_dataset_rows(dataset) for dataset in request.datasets}
        raw_population = parsed[APDatabaseTemplate.INVOICE_POPULATION]
        population_rows = cast(tuple[APInvoicePopulationRowV1, ...], raw_population)
        population = validate_population(population_rows, tenant_id=context.tenant_id)
        dedicated: tuple[
            APDuplicateInvoiceRowV1
            | APInvoicePOVarianceRowV1
            | APPaymentTermsRowV1
            | APPaymentAmountRowV1,
            ...,
        ] = ()
        if isinstance(request, APDetectionRequestV1):
            dedicated_template = next(
                template
                for template in parsed
                if template is not APDatabaseTemplate.INVOICE_POPULATION
            )
            dedicated = cast(
                tuple[
                    APDuplicateInvoiceRowV1
                    | APInvoicePOVarianceRowV1
                    | APPaymentTermsRowV1
                    | APPaymentAmountRowV1,
                    ...,
                ],
                parsed[dedicated_template],
            )
            validate_dedicated_rows(
                dedicated,
                population,
                tenant_id=context.tenant_id,
            )

        calculation_results: tuple[APAnalyticsResultV1, ...] = ()
        calculation_ids: tuple[str, ...] = ()
        parameters = request.parameters
        if hasattr(parameters, "calculation_evidence_ids"):
            calculation_ids = cast(tuple[str, ...], parameters.calculation_evidence_ids)
            calculation_results = self._load_calculation_results(
                calculation_ids,
                context,
                manifest_checksum=manifest.manifest_checksum,
                document_evidence_ids=document_ids,
                rule_versions={rule.rule_id: rule.rule_version for rule in manifest.rules},
            )
        parent_ids = tuple(
            dict.fromkeys(
                (
                    *(item.evidence_id for item in request.datasets),
                    *calculation_ids,
                    *document_ids,
                )
            )
        )
        return APValidatedInputs(
            population=population,
            dedicated=dedicated,
            calculation_results=calculation_results,
            parent_evidence_ids=parent_ids,
        )

    def _validate_database_dataset(
        self,
        dataset: object,
        context: ToolExecutionContext,
    ) -> EvidenceItem:
        from copilot.tools.analytics.ap_schemas import APDatasetReferenceV1

        typed = cast(APDatasetReferenceV1, dataset)
        evidence = self._get(typed.evidence_id, context)
        if evidence.source_type is not EvidenceType.DATABASE:
            raise APAnalyticsInputDeniedError("AP dataset input is not DATABASE Evidence")
        reference = evidence.source_reference.reference.root
        content = evidence.content.data.root
        schema_snapshot = reference.get("schema_snapshot")
        parameter_summary = reference.get("parameter_summary")
        exact_metadata = (
            reference.get("query_template_id") == typed.template_id.value
            and reference.get("template_version") == typed.template_version
            and reference.get("schema_version") == "accounts_payable.v1"
            and isinstance(schema_snapshot, dict)
            and schema_snapshot.get("version") == "accounts_payable.v1"
            and schema_snapshot.get("snapshot_at") == reference.get("snapshot_at")
            and isinstance(reference.get("snapshot_at"), str)
            and isinstance(reference.get("query_fingerprint"), str)
            and bool(reference.get("query_fingerprint"))
            and isinstance(parameter_summary, dict)
            and isinstance(reference.get("table_names"), list)
            and isinstance(reference.get("column_names"), list)
            and reference.get("statement_type") == "SELECT"
            and reference.get("read_only") is True
            and reference.get("dataset_checksum") == typed.dataset_checksum
            and evidence.content.checksum == typed.dataset_checksum
            and reference.get("row_count") == len(typed.rows)
            and content.get("row_count") == len(typed.rows)
            and content.get("empty_result") is (len(typed.rows) == 0)
        )
        if not exact_metadata:
            raise APAnalyticsInputDeniedError("AP DATABASE Evidence metadata does not match rows")
        if content.get("truncated") is True:
            raise APAnalyticsScopeTooLargeError("Truncated AP data cannot be analyzed")
        if content.get("truncated") is not False:
            raise APAnalyticsInputDeniedError("AP DATABASE Evidence truncation state is missing")
        raw_rows = [item.root for item in typed.rows]
        if not _checksum_matches(raw_rows, typed.dataset_checksum):
            raise APAnalyticsInputDeniedError("AP dataset checksum does not match its rows")
        return evidence

    @staticmethod
    def _validate_dataset_scope_consistency(evidence: tuple[EvidenceItem, ...]) -> None:
        if len(evidence) < 2:
            return
        first = evidence[0].source_reference.reference.root
        first_scope = first.get("parameter_summary")
        first_snapshot = first.get("snapshot_at")
        for item in evidence[1:]:
            reference = item.source_reference.reference.root
            if (
                reference.get("parameter_summary") != first_scope
                or reference.get("snapshot_at") != first_snapshot
            ):
                raise APAnalyticsDataConsistencyError(
                    "AP analytics datasets were read from different scopes or snapshots"
                )

    def _validate_document_evidence(
        self,
        request: APAnalyticsRequestV1,
        context: ToolExecutionContext,
    ) -> tuple[str, ...]:
        manifest = request.rule_snapshot.rule_manifest
        bindings = {
            item.rule_id: item.evidence_id for item in request.rule_snapshot.document_evidence
        }
        validated: list[str] = []
        for rule in manifest.rules:
            evidence_id = bindings[rule.rule_id]
            evidence = self._get(evidence_id, context)
            if evidence.source_type is not EvidenceType.DOCUMENT:
                raise APPolicyRuleBindingMismatchError(
                    "AP rule binding does not reference DOCUMENT Evidence"
                )
            reference = evidence.source_reference.reference.root
            binding = rule.binding
            bound_rule_ids = reference.get("bound_rule_ids")
            exact = (
                reference.get("document_id") == binding.document_id
                and reference.get("document_version") == binding.document_version
                and reference.get("chunk_id") == binding.chunk_id
                and reference.get("page") == binding.page
                and reference.get("document_checksum") == binding.document_checksum
                and reference.get("excerpt_checksum") == binding.excerpt_checksum
                and reference.get("policy_rule_set_version") == manifest.rule_set_version
                and isinstance(bound_rule_ids, list)
                and rule.rule_id in bound_rule_ids
                and evidence.content.checksum == binding.excerpt_checksum
                and reference.get("quarantined") is not True
            )
            if not exact:
                raise APPolicyRuleBindingMismatchError()
            validated.append(evidence_id)
        return tuple(validated)

    def _load_calculation_results(
        self,
        evidence_ids: tuple[str, ...],
        context: ToolExecutionContext,
        *,
        manifest_checksum: str,
        document_evidence_ids: tuple[str, ...],
        rule_versions: dict[str, str],
    ) -> tuple[APAnalyticsResultV1, ...]:
        grouped: dict[tuple[str, str], list[EvidenceItem]] = {}
        for evidence_id in evidence_ids:
            evidence = self._get(evidence_id, context)
            if evidence.source_type is not EvidenceType.CALCULATION:
                raise APAnalyticsInputDeniedError(
                    "AP aggregation input is not CALCULATION Evidence"
                )
            if _prefixed_checksum(evidence.content.data.root) != evidence.content.checksum:
                raise APAnalyticsInputDeniedError("AP calculation Evidence checksum drifted")
            reference = evidence.source_reference.reference.root
            operation_name = reference.get("operation_name")
            run_id = reference.get("calculation_run_id")
            if not isinstance(operation_name, str) or not isinstance(run_id, str):
                raise APAnalyticsInputDeniedError("AP calculation Evidence identity is incomplete")
            try:
                operation = APAnalyticsOperation(operation_name)
            except ValueError as exc:
                raise APAnalyticsInputDeniedError(
                    "AP calculation Evidence operation is not supported"
                ) from exc
            if operation in {
                APAnalyticsOperation.EXCEPTION_SUMMARY,
                APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
            }:
                raise APAnalyticsInputDeniedError(
                    "AP aggregation requires detection Calculation Evidence"
                )
            if reference.get("manifest_checksum") != manifest_checksum:
                raise APPolicyRuleBindingMismatchError(
                    "AP calculation and aggregation manifests differ"
                )
            self._validate_calculation_parents(
                evidence,
                context,
                document_evidence_ids=document_evidence_ids,
            )
            grouped.setdefault((operation.value, run_id), []).append(evidence)
        operation_runs = Counter(operation for operation, _run in grouped)
        if any(count != 1 for count in operation_runs.values()):
            raise APAnalyticsDataConsistencyError(
                "AP aggregation cannot combine multiple runs of one operation"
            )
        results = tuple(
            self._assemble_calculation_run(items, rule_versions=rule_versions)
            for _identity, items in sorted(grouped.items())
        )
        if not results:
            raise APAnalyticsInputError("AP aggregation requires calculation results")
        return results

    def _validate_calculation_parents(
        self,
        evidence: EvidenceItem,
        context: ToolExecutionContext,
        *,
        document_evidence_ids: tuple[str, ...],
    ) -> None:
        parent_ids = evidence.source_reference.input_evidence_ids
        if not parent_ids or len(set(parent_ids)) != len(parent_ids):
            raise APAnalyticsInputDeniedError("AP calculation parent lineage is invalid")
        parents = tuple(self._get(evidence_id, context) for evidence_id in parent_ids)
        document_ids = {
            item.evidence_id for item in parents if item.source_type is EvidenceType.DOCUMENT
        }
        databases = tuple(item for item in parents if item.source_type is EvidenceType.DATABASE)
        if document_ids != set(document_evidence_ids) or len(databases) != 2:
            raise APAnalyticsInputDeniedError(
                "AP calculation does not trace to its exact policy and database inputs"
            )
        if any(
            item.source_type not in {EvidenceType.DOCUMENT, EvidenceType.DATABASE}
            for item in parents
        ):
            raise APAnalyticsInputDeniedError("AP calculation has an unsupported parent type")
        input_checksums = evidence.source_reference.reference.root.get("input_checksums")
        if not isinstance(input_checksums, list) or set(input_checksums) != {
            item.content.checksum for item in databases
        }:
            raise APAnalyticsInputDeniedError(
                "AP calculation database checksums do not match parent Evidence"
            )

    @staticmethod
    def _assemble_calculation_run(
        items: list[EvidenceItem], *, rule_versions: dict[str, str]
    ) -> APAnalyticsResultV1:
        ordered = sorted(
            items,
            key=lambda item: cast(int, item.source_reference.reference.root.get("batch_index", -1)),
        )
        first_reference = ordered[0].source_reference.reference.root
        expected_count = first_reference.get("batch_count")
        if not isinstance(expected_count, int) or not 1 <= expected_count <= 250:
            raise APAnalyticsInputDeniedError("AP calculation batch count is invalid")
        indices = tuple(item.source_reference.reference.root.get("batch_index") for item in ordered)
        if len(ordered) != expected_count or indices != tuple(range(expected_count)):
            raise APAnalyticsInputDeniedError("AP calculation Evidence batches are incomplete")
        first_data = ordered[0].content.data.root
        result_metadata = first_data.get("result_metadata")
        if not isinstance(result_metadata, dict):
            raise APAnalyticsInputDeniedError("AP calculation result metadata is missing")
        records: list[JsonMapping] = []
        groups: list[JsonMapping] = []
        supplier_rates: list[JsonMapping] = []
        exclusions: list[JsonMapping] = []
        record_evidence_ids: list[str] = []
        for item in ordered:
            reference = item.source_reference.reference.root
            data = item.content.data.root
            if (
                item.source_reference.input_evidence_ids
                != ordered[0].source_reference.input_evidence_ids
                or reference.get("batch_count") != expected_count
                or reference.get("output_checksum") != first_reference.get("output_checksum")
                or data.get("result_metadata") != result_metadata
            ):
                raise APAnalyticsInputDeniedError("AP calculation batch metadata drifted")
            raw_items = data.get("batch_items")
            if not isinstance(raw_items, list) or len(raw_items) > AP_CALCULATION_BATCH_SIZE:
                raise APAnalyticsInputDeniedError("AP calculation batch items are invalid")
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    raise APAnalyticsInputDeniedError("AP calculation batch item is invalid")
                kind = raw_item.get("kind")
                value = raw_item.get("value")
                if not isinstance(value, dict):
                    raise APAnalyticsInputDeniedError("AP calculation batch value is invalid")
                if kind == "exception":
                    records.append(value)
                    record_evidence_ids.append(item.evidence_id)
                elif kind == "duplicate_group":
                    groups.append(value)
                elif kind == "supplier_rate":
                    supplier_rates.append(value)
                elif kind == "exclusion":
                    exclusions.append(value)
                else:
                    raise APAnalyticsInputDeniedError("AP calculation batch kind is invalid")
        payload = {
            **result_metadata,
            "records": cast(JsonValue, records),
            "duplicate_groups": cast(JsonValue, groups),
            "supplier_rates": cast(JsonValue, supplier_rates),
            "exclusions": cast(JsonValue, exclusions),
        }
        try:
            result = APAnalyticsResultV1.model_validate(payload)
        except ValidationError as exc:
            raise APAnalyticsInputDeniedError(
                "AP calculation batches do not reconstruct a valid result"
            ) from exc
        expected_checksum = _prefixed_checksum(
            result.model_dump(mode="json", exclude={"output_checksum"})
        )
        if expected_checksum != result.output_checksum:
            raise APAnalyticsInputDeniedError("AP calculation output checksum drifted")
        expected_reference = {
            "operation_name": result.operation_name.value,
            "operation_version": AP_ANALYTICS_OPERATION_VERSION,
            "engine_version": AP_ANALYTICS_ENGINE_VERSION,
            "formulas": list(AP_FORMULA_CATALOGUE[result.operation_name]),
            "normalization_version": result.normalization_version,
            "precision": result.precision,
            "rounding_mode": result.rounding_mode,
            "rule_ids": list(result.rule_ids),
            "rule_versions": {
                rule_id: rule_versions[rule_id]
                for rule_id in sorted(result.rule_ids)
                if rule_id in rule_versions
            },
            "rule_set_version": result.rule_set_version,
            "manifest_checksum": result.manifest_checksum,
            "input_checksums": list(result.input_checksums),
            "output_checksum": result.output_checksum,
        }
        for item in ordered:
            reference = item.source_reference.reference.root
            if any(reference.get(key) != value for key, value in expected_reference.items()):
                raise APAnalyticsInputDeniedError("AP calculation reference metadata drifted")
        enriched_records = tuple(
            record.model_copy(update={"calculation_evidence_id": record_evidence_ids[index]})
            for index, record in enumerate(result.records)
        )
        return result.model_copy(update={"records": enriched_records})

    def _get(self, evidence_id: str, context: ToolExecutionContext) -> EvidenceItem:
        try:
            return self._evidence_reader.get(
                evidence_id,
                task_id=context.call.task_id,
                tenant_id=context.tenant_id,
            )
        except (KeyError, LookupError) as exc:
            raise APAnalyticsInputDeniedError("AP input Evidence does not exist") from exc


def _manifest_checksum(payload: JsonMapping) -> str:
    canonical = dict(payload)
    canonical.pop("manifest_checksum", None)
    return _prefixed_checksum(canonical)


def _prefixed_checksum(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _checksum_matches(value: object, expected: str) -> bool:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return expected in {digest, f"sha256:{digest}"}


__all__ = ["APAnalyticsLineageValidator", "APValidatedInputs"]
