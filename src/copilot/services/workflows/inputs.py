"""Explicit schema-bound input construction and evidence transfer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    APAnalyticsOperation,
    APDatabaseTemplate,
    APPolicyRuleManifestV1,
    ArtifactType,
    EvidenceItem,
    EvidenceType,
    JsonObject,
    StepResult,
    StepType,
    SupplierQualityConstraintsV1,
    TaskContract,
    TaskRequest,
    TaskStep,
)
from copilot.contracts.base import JsonMapping
from copilot.services.domains import (
    DomainCapabilityManifestRegistry,
    DomainManifestError,
    builtin_domain_manifest_registry,
)
from copilot.services.domains.accounts_payable_inputs import build_accounts_payable_database_input
from copilot.services.task_intake import TrustedTaskContext
from copilot.services.workflows.accounts_payable_plan import (
    ap_analytics_operation_for_step,
    ap_database_template_for_step,
)
from copilot.services.workflows.errors import StepInputError


class StepInputBuilder:
    """Build one tool input solely from contract scope and immutable prior results."""

    def __init__(
        self,
        domain_manifests: DomainCapabilityManifestRegistry | None = None,
        *,
        ap_policy_collection_id: str | None = None,
        ap_policy_rule_manifest: APPolicyRuleManifestV1 | None = None,
        ap_policy_snapshot_id: str | None = None,
        ap_database_row_limit: int = 50_000,
    ) -> None:
        self._domain_manifests = domain_manifests or builtin_domain_manifest_registry()
        self._ap_policy_collection_id = ap_policy_collection_id
        self._ap_policy_rule_manifest = ap_policy_rule_manifest
        self._ap_policy_snapshot_id = ap_policy_snapshot_id
        if not 1 <= ap_database_row_limit <= 50_000:
            raise ValueError("AP database row limit must be between 1 and 50000")
        self._ap_database_row_limit = ap_database_row_limit

    def build(
        self,
        step: TaskStep,
        request: TaskRequest,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
        trusted_context: TrustedTaskContext | None = None,
    ) -> JsonObject:
        """Return the exact frozen input shape for the step type."""
        try:
            manifest = self._domain_manifests.require_execution(contract)
        except DomainManifestError as exc:
            raise StepInputError(f"{exc.code}: {exc}") from exc
        if manifest.input_profile == "accounts_payable_inputs.v1":
            return self._accounts_payable(
                step,
                request,
                contract,
                prior_results,
                evidence,
                trusted_context,
            )
        if manifest.input_profile != "supplier_quality_inputs.v1":
            raise StepInputError(f"DOMAIN_INPUT_PROFILE_NOT_IMPLEMENTED: {manifest.input_profile}")
        if not isinstance(contract.constraints, SupplierQualityConstraintsV1):
            raise StepInputError("Supplier Quality input profile received incompatible constraints")
        if step.step_type is StepType.KNOWLEDGE_SEARCH:
            return self._knowledge(request, contract)
        if step.step_type is StepType.DATABASE_QUERY:
            return self._database(request, contract)
        if step.step_type is StepType.ANALYSIS:
            return self._analytics(step, contract, prior_results, evidence)
        if step.step_type is StepType.REPORT_GENERATION:
            return self._report(step, contract, prior_results, evidence)
        raise StepInputError(f"Unsupported step type {step.step_type}")

    def _accounts_payable(
        self,
        step: TaskStep,
        request: TaskRequest,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
        trusted_context: TrustedTaskContext | None,
    ) -> JsonObject:
        del request
        scope = contract.constraints
        if not isinstance(scope, AccountsPayableConstraintsV1):
            raise StepInputError("Accounts Payable input profile received incompatible constraints")
        if (
            self._ap_policy_collection_id is None
            or self._ap_policy_rule_manifest is None
            or self._ap_policy_snapshot_id is None
        ):
            raise StepInputError("Accounts Payable controlled policy bundle is unavailable")
        if step.step_type is StepType.KNOWLEDGE_SEARCH:
            return JsonObject(
                {
                    "query": (
                        "Accounts Payable duplicate, purchase-order, payment timing, "
                        "overpayment, and materiality policies"
                    ),
                    "tenant_id": scope.tenant_id,
                    "collection_ids": [self._ap_policy_collection_id],
                    "supplier_ids": list(scope.supplier_ids),
                    "date_range": {
                        "start": scope.time_range.start_date.isoformat(),
                        "end": scope.time_range.end_date.isoformat(),
                    },
                    "top_k": 12,
                    "index_snapshot_id": self._ap_policy_snapshot_id,
                }
            )
        if step.step_type is StepType.DATABASE_QUERY:
            try:
                template = ap_database_template_for_step(step.step_id)
            except ValueError as exc:
                raise StepInputError(str(exc)) from exc
            return build_accounts_payable_database_input(
                scope,
                template.value,
                row_limit=self._ap_database_row_limit,
            )
        rule_snapshot = self._ap_rule_snapshot(evidence)
        if step.step_type is StepType.ANALYSIS:
            return self._ap_analytics_input(
                step,
                scope,
                prior_results,
                evidence,
                rule_snapshot,
            )
        if step.step_type is StepType.REPORT_GENERATION:
            return self._ap_report_input(
                step,
                contract,
                prior_results,
                evidence,
                rule_snapshot,
                trusted_context,
            )
        raise StepInputError(f"Unsupported Accounts Payable step type {step.step_type}")

    def _ap_rule_snapshot(self, evidence: Mapping[str, EvidenceItem]) -> JsonMapping:
        assert self._ap_policy_rule_manifest is not None
        references: list[JsonMapping] = []
        for rule in self._ap_policy_rule_manifest.rules:
            match = next(
                (
                    item
                    for item in evidence.values()
                    if item.source_type is EvidenceType.DOCUMENT
                    and item.source_reference.reference.root.get("document_id")
                    == rule.binding.document_id
                    and item.source_reference.reference.root.get("document_version")
                    == rule.binding.document_version
                    and item.source_reference.reference.root.get("chunk_id")
                    == rule.binding.chunk_id
                    and item.source_reference.reference.root.get("excerpt_checksum")
                    == rule.binding.excerpt_checksum
                ),
                None,
            )
            if match is None:
                raise StepInputError(
                    f"Controlled policy Evidence is missing for AP rule {rule.rule_id}"
                )
            references.append({"rule_id": rule.rule_id, "evidence_id": match.evidence_id})
        return {
            "rule_manifest": cast(
                JsonMapping,
                self._ap_policy_rule_manifest.model_dump(mode="json"),
            ),
            "document_evidence": cast(JsonValue, references),
        }

    @staticmethod
    def _ap_analytics_input(
        step: TaskStep,
        scope: AccountsPayableConstraintsV1,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
        rule_snapshot: JsonMapping,
    ) -> JsonObject:
        try:
            operation = ap_analytics_operation_for_step(step.step_id)
        except ValueError as exc:
            raise StepInputError(str(exc)) from exc
        datasets: list[JsonMapping] = []
        calculation_ids: list[str] = []
        for dependency in step.dependency:
            result = prior_results.get(dependency)
            if result is None or result.output is None:
                raise StepInputError("AP analytics dependency has no successful output")
            try:
                template = ap_database_template_for_step(dependency)
            except ValueError:
                calculation_ids.extend(
                    evidence_id
                    for evidence_id in result.evidence
                    if evidence_id in evidence
                    and evidence[evidence_id].source_type is EvidenceType.CALCULATION
                )
                continue
            database_item = _evidence_of_type(result, evidence, EvidenceType.DATABASE)
            rows = result.output.root.get("rows")
            if database_item is None or not isinstance(rows, list):
                raise StepInputError("AP database dependency lacks rows or Database Evidence")
            if (
                database_item.source_reference.reference.root.get("query_template_id")
                != template.value
            ):
                raise StepInputError("AP database Evidence template differs from the Plan binding")
            datasets.append(
                {
                    "template_id": template.value,
                    "template_version": template.value,
                    "evidence_id": database_item.evidence_id,
                    "dataset_checksum": database_item.content.checksum,
                    "rows": rows,
                }
            )
        if operation in {
            APAnalyticsOperation.EXCEPTION_SUMMARY,
            APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
        }:
            datasets = [
                item
                for item in datasets
                if item["template_id"] == APDatabaseTemplate.INVOICE_POPULATION.value
            ]
            if not calculation_ids:
                raise StepInputError("AP aggregation lacks Calculation Evidence")
            parameters: JsonMapping = {
                "calculation_evidence_ids": list(dict.fromkeys(calculation_ids))
            }
        else:
            parameters = {
                "requested_materiality": [
                    item.model_dump(mode="json") for item in scope.requested_materiality
                ],
                "effective_materiality": [
                    item.model_dump(mode="json") for item in scope.effective_materiality
                ],
            }
        return JsonObject(
            {
                "operation_name": operation.value,
                "operation_version": "1.0.0",
                "datasets": cast(JsonValue, datasets),
                "rule_snapshot": rule_snapshot,
                "parameters": parameters,
                "engine_version": "accounts_payable_analytics.v1",
            }
        )

    @staticmethod
    def _ap_report_input(
        step: TaskStep,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
        rule_snapshot: JsonMapping,
        trusted_context: TrustedTaskContext | None,
    ) -> JsonObject:
        scope = contract.constraints
        assert isinstance(scope, AccountsPayableConstraintsV1)
        summary = next(
            (
                result.output.root
                for dependency in step.dependency
                if (result := prior_results.get(dependency)) is not None
                and result.output is not None
                and result.output.root.get("operation_name")
                == APAnalyticsOperation.EXCEPTION_SUMMARY.value
            ),
            None,
        )
        if summary is None:
            raise StepInputError("AP report lacks the exception-summary result")
        detail_access = (
            "DETAIL"
            if trusted_context is not None and "finance:ap.detail" in trusted_context.scopes
            else "AGGREGATE"
        )
        artifact_type = contract.expected_output.artifact_type
        report_format = (
            "PDF" if artifact_type is ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF else "JSON"
        )
        return JsonObject(
            {
                "task_id": contract.task_id,
                "scope": {
                    "start_date": scope.time_range.start_date.isoformat(),
                    "end_date": scope.time_range.end_date.isoformat(),
                    "supplier_ids": list(scope.supplier_ids),
                    "legal_entity_ids": list(scope.legal_entity_ids),
                    "business_unit_ids": list(scope.business_unit_ids),
                    "currency_scope": list(scope.currency_scope),
                },
                "exception_summary_result": summary,
                "evidence_refs": cast(JsonValue, sorted(evidence)),
                "policy_rule_snapshot": rule_snapshot,
                "template_version": "accounts_payable_report.v1",
                "format": report_format,
                "language": contract.expected_output.language.value,
                "detail_access": detail_access,
            }
        )

    @staticmethod
    def _knowledge(request: TaskRequest, contract: TaskContract) -> JsonObject:
        scope = _quality_scope(contract)
        return JsonObject(
            {
                "query": f"Supplier quality policy and deviation process: {request.raw_input}",
                "tenant_id": scope.tenant_id,
                "collection_ids": ["supplier-quality-policy-v1"],
                "supplier_ids": list(scope.supplier_ids),
                "date_range": {
                    "start": scope.start_date.isoformat(),
                    "end": scope.end_date.isoformat(),
                },
                "top_k": 10,
                "index_snapshot_id": "supplier-quality-policy-snapshot-v1",
            }
        )

    @staticmethod
    def _database(request: TaskRequest, contract: TaskContract) -> JsonObject:
        scope = _quality_scope(contract)
        return JsonObject(
            {
                "query_template_id": "supplier_quality_summary_v1",
                "parameters": {
                    "tenant_id": scope.tenant_id,
                    "start_date": scope.start_date.isoformat(),
                    "end_date": scope.end_date.isoformat(),
                    "supplier_ids": list(scope.supplier_ids),
                },
                "schema_version": "quality.v1",
                "snapshot_at": request.created_at.isoformat(),
                "row_limit": 10000,
            }
        )

    @staticmethod
    def _analytics(
        step: TaskStep,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
    ) -> JsonObject:
        if len(step.dependency) != 1:
            raise StepInputError("Analysis step must have exactly one database dependency")
        result = prior_results.get(step.dependency[0])
        if result is None or result.output is None or not result.evidence:
            raise StepInputError("Analysis dependency has no dataset output or evidence")
        database_item = next(
            (
                evidence[evidence_id]
                for evidence_id in result.evidence
                if evidence_id in evidence
                and evidence[evidence_id].source_type is EvidenceType.DATABASE
            ),
            None,
        )
        if database_item is None:
            raise StepInputError("Analysis dependency has no database evidence")
        rows = result.output.root.get("rows")
        if not isinstance(rows, list):
            raise StepInputError("Database output rows are missing")
        return JsonObject(
            {
                "dataset": rows,
                "dataset_evidence_id": database_item.evidence_id,
                "dataset_checksum": database_item.content.checksum,
                "metrics": list(_quality_scope(contract).metrics),
                "group_by": ["supplier_id", "period"],
                "engine_version": "quality_metrics.v1",
            }
        )

    @staticmethod
    def _report(
        step: TaskStep,
        contract: TaskContract,
        prior_results: Mapping[str, StepResult],
        evidence: Mapping[str, EvidenceItem],
    ) -> JsonObject:
        analysis = next(
            (
                prior_results[dependency]
                for dependency in step.dependency
                if dependency in prior_results
                and prior_results[dependency].output is not None
                and any(
                    evidence.get(evidence_id) is not None
                    and evidence[evidence_id].source_type is EvidenceType.CALCULATION
                    for evidence_id in prior_results[dependency].evidence
                )
            ),
            None,
        )
        if analysis is None or analysis.output is None:
            raise StepInputError("Report step has no successful analysis result")
        refs = tuple(evidence)
        types = {item.source_type for item in evidence.values()}
        required_types = {EvidenceType.DOCUMENT, EvidenceType.DATABASE, EvidenceType.CALCULATION}
        if not required_types.issubset(types):
            raise StepInputError(
                "Report input lacks required document, database, or calculation evidence"
            )
        artifact_type = contract.expected_output.artifact_type
        report_format = (
            "PDF" if artifact_type is ArtifactType.QUALITY_ANALYSIS_REPORT_PDF else "JSON"
        )
        scope = _quality_scope(contract)
        return JsonObject(
            {
                "task_id": contract.task_id,
                "scope": {
                    "year": scope.year,
                    "quarter": scope.quarter,
                    "start_date": scope.start_date.isoformat(),
                    "end_date": scope.end_date.isoformat(),
                    "supplier_ids": list(scope.supplier_ids),
                },
                "analysis_result": analysis.output.root,
                "evidence_refs": list(refs),
                "template_version": "supplier_quality_report.v1",
                "format": report_format,
                "language": contract.expected_output.language.value,
            }
        )


def summarize_payload(payload: JsonObject | None) -> JsonObject:
    """Return a bounded key/count summary suitable for audit and execution metadata."""
    if payload is None:
        return JsonObject({})
    summary: dict[str, object] = {"keys": sorted(payload.root)}
    for key in ("row_count", "match_count", "empty_result", "artifact_id"):
        if key in payload.root:
            summary[key] = payload.root[key]
    return JsonObject(cast(JsonMapping, summary))


def _quality_scope(contract: TaskContract) -> SupplierQualityConstraintsV1:
    scope = contract.constraints
    if not isinstance(scope, SupplierQualityConstraintsV1):
        raise StepInputError("Supplier Quality input profile received incompatible constraints")
    return scope


def _evidence_of_type(
    result: StepResult,
    evidence: Mapping[str, EvidenceItem],
    evidence_type: EvidenceType,
) -> EvidenceItem | None:
    return next(
        (
            evidence[evidence_id]
            for evidence_id in result.evidence
            if evidence_id in evidence and evidence[evidence_id].source_type is evidence_type
        ),
        None,
    )
