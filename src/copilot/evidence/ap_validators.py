"""Deterministic Accounts Payable Evidence and claim verification profile rules."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast

from pydantic import JsonValue, ValidationError

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    APExceptionType,
    CandidateResult,
    ClaimType,
    EvidenceItem,
    EvidenceType,
    JsonObject,
    NumericClaim,
    StepResult,
    StepResultStatus,
    TaskContract,
    TaskPlan,
    TaskType,
    VerificationContext,
    VerificationIssue,
    VerificationSeverity,
)
from copilot.contracts.base import JsonMapping
from copilot.evidence.lineage import contains_source_type
from copilot.evidence.validators import EvidenceLedgerView
from copilot.security import SensitiveDataRegistry
from copilot.tools.analytics.ap_operations import AP_FORMULA_CATALOGUE
from copilot.tools.analytics.ap_schemas import (
    AP_ANALYTICS_ENGINE_VERSION,
    AP_ANALYTICS_OPERATION_VERSION,
    AP_CALCULATION_BATCH_SIZE,
    APAnalyticsOperation,
    APAnalyticsResultV1,
)


@dataclass(frozen=True, slots=True)
class APCalculationRun:
    """One complete, checksum-validated AP Calculation Evidence run."""

    operation: APAnalyticsOperation
    run_id: str
    evidence_ids: tuple[str, ...]
    items: tuple[EvidenceItem, ...]
    result: APAnalyticsResultV1


class APEvidenceMetadataVerifier:
    """Validate the frozen AP Document, Database, and Calculation metadata profiles."""

    name = "APEvidenceMetadataVerifier"

    def verify(
        self,
        *,
        task_contract: TaskContract,
        task_plan: TaskPlan,
        step_results: Mapping[str, StepResult],
        evidence_ledger: EvidenceLedgerView,
        verification_context: VerificationContext,
        candidate_result: CandidateResult,
    ) -> tuple[VerificationIssue, ...]:
        del task_plan, step_results, verification_context, candidate_result
        constraints = _ap_constraints(task_contract)
        if constraints is None:
            return (_profile_issue(task_contract.task_id, self.name),)
        task_id = task_contract.task_id
        tenant_id = constraints.tenant_id
        issues: list[VerificationIssue] = []
        evidence = evidence_ledger.list(task_id, tenant_id=tenant_id)
        for item in evidence:
            if item.source_type is EvidenceType.DOCUMENT:
                issues.extend(_document_metadata_issues(item, constraints, self.name))
            elif item.source_type is EvidenceType.DATABASE:
                issues.extend(_database_metadata_issues(item, constraints, self.name))
            elif item.source_type is EvidenceType.CALCULATION:
                issues.extend(_calculation_metadata_issues(item, constraints, self.name))
            restricted = _restricted_paths(item.content.data.root)
            for path in restricted:
                issues.append(
                    _issue(
                        "AP_RESTRICTED_FIELD_EXPOSURE",
                        "AP Evidence exposes a restricted field",
                        self.name,
                        task_id,
                        item=item,
                        details={"field_path": path},
                    )
                )
        _, run_issues = parse_ap_calculation_runs(
            task_id=task_id,
            tenant_id=tenant_id,
            evidence=evidence,
        )
        issues.extend(run_issues)
        return tuple(issues)


class APPolicyBindingVerifier:
    """Prove exact Task rule snapshot and dual policy/data lineage for AP claims."""

    name = "APPolicyBindingVerifier"

    def verify(
        self,
        *,
        task_contract: TaskContract,
        task_plan: TaskPlan,
        step_results: Mapping[str, StepResult],
        evidence_ledger: EvidenceLedgerView,
        verification_context: VerificationContext,
        candidate_result: CandidateResult,
    ) -> tuple[VerificationIssue, ...]:
        del task_plan, step_results, verification_context
        constraints = _ap_constraints(task_contract)
        if constraints is None:
            return (_profile_issue(task_contract.task_id, self.name),)
        task_id = task_contract.task_id
        tenant_id = constraints.tenant_id
        issues: list[VerificationIssue] = []
        evidence = evidence_ledger.list(task_id, tenant_id=tenant_id)
        for item in evidence:
            if item.source_type is not EvidenceType.CALCULATION:
                continue
            reference = item.source_reference.reference.root
            if (
                reference.get("rule_set_version") != constraints.policy_rule_set_version
                or reference.get("manifest_checksum") != constraints.policy_manifest_checksum
            ):
                issues.append(
                    _issue(
                        "AP_POLICY_MANIFEST_MISMATCH",
                        "Calculation Evidence does not match the Task policy snapshot",
                        self.name,
                        task_id,
                        item=item,
                    )
                )
            rule_ids = _strings(reference.get("rule_ids"))
            raw_versions = reference.get("rule_versions")
            rule_versions = raw_versions if isinstance(raw_versions, dict) else {}
            if (
                not rule_ids
                or set(rule_versions) != set(rule_ids)
                or any(not isinstance(value, str) or not value for value in rule_versions.values())
            ):
                issues.append(
                    _issue(
                        "AP_RULE_VERSION_BINDING_INVALID",
                        "Calculation Evidence lacks exact rule ID/version bindings",
                        self.name,
                        task_id,
                        item=item,
                    )
                )
            trace = evidence_ledger.trace_lineage(task_id, item.evidence_id, tenant_id=tenant_id)
            documents = tuple(
                node for node in trace.nodes if node.source_type is EvidenceType.DOCUMENT
            )
            databases = tuple(
                node for node in trace.nodes if node.source_type is EvidenceType.DATABASE
            )
            covered_rules = {
                rule_id
                for document in documents
                for rule_id in _strings(
                    document.source_reference.reference.root.get("bound_rule_ids")
                )
            }
            if not databases or not documents or not set(rule_ids).issubset(covered_rules):
                issues.append(
                    _issue(
                        "AP_POLICY_DUAL_LINEAGE_MISSING",
                        "Policy-governed calculation lacks exact Database and Document lineage",
                        self.name,
                        task_id,
                        item=item,
                    )
                )

        claims_by_id = {claim.claim_id: claim for claim in candidate_result.claims}
        if len(claims_by_id) != len(candidate_result.claims) or not claims_by_id:
            issues.append(
                _issue(
                    "AP_MATERIAL_CLAIMS_MISSING",
                    "AP report must expose unique structured material claims",
                    self.name,
                    task_id,
                )
            )
        for claim in candidate_result.claims:
            if claim.claim_type is ClaimType.NUMERIC and not claim.policy_governed:
                issues.append(
                    _claim_issue(
                        "AP_NUMERIC_RULE_BINDING_MISSING",
                        "AP numeric claim is not bound to controlled rule IDs",
                        self.name,
                        task_id,
                        claim.claim_id,
                        claim.evidence_ids,
                    )
                )
                continue
            if not claim.policy_governed:
                continue
            traces = tuple(
                evidence_ledger.trace_lineage(task_id, evidence_id, tenant_id=tenant_id)
                for evidence_id in claim.evidence_ids
                if evidence_ledger.validate_reference(task_id, evidence_id, tenant_id=tenant_id)
            )
            documents = tuple(
                node
                for trace in traces
                for node in trace.nodes
                if node.source_type is EvidenceType.DOCUMENT
            )
            covered = {
                rule_id
                for document in documents
                for rule_id in _strings(
                    document.source_reference.reference.root.get("bound_rule_ids")
                )
            }
            has_calc = any(
                contains_source_type(trace, EvidenceType.CALCULATION) for trace in traces
            )
            has_db = any(contains_source_type(trace, EvidenceType.DATABASE) for trace in traces)
            if (
                not has_calc
                or not has_db
                or not documents
                or not set(claim.rule_ids).issubset(covered)
            ):
                issues.append(
                    _claim_issue(
                        "AP_CLAIM_POLICY_BINDING_INVALID",
                        "Policy-governed claim lacks Calculation/Database/Document rule lineage",
                        self.name,
                        task_id,
                        claim.claim_id,
                        claim.evidence_ids,
                    )
                )
        for numeric in candidate_result.numeric_claims:
            citation = claims_by_id.get(numeric.claim_id)
            if citation is None or citation.claim_type is not ClaimType.NUMERIC:
                issues.append(
                    _claim_issue(
                        "AP_NUMERIC_CITATION_MISSING",
                        "AP numeric claim lacks its matching citation claim",
                        self.name,
                        task_id,
                        numeric.claim_id,
                        numeric.evidence_ids,
                    )
                )
        if constraints.include_policy_comparison and not any(
            claim.claim_type is ClaimType.POLICY for claim in candidate_result.claims
        ):
            issues.append(
                _issue(
                    "AP_POLICY_CLAIM_MISSING",
                    "Requested policy comparison has no structured policy claim",
                    self.name,
                    task_id,
                )
            )
        return tuple(issues)


class APConsistencyVerifier:
    """Validate requested-operation coverage, scope, batching, and record relationships."""

    name = "APConsistencyVerifier"

    def verify(
        self,
        *,
        task_contract: TaskContract,
        task_plan: TaskPlan,
        step_results: Mapping[str, StepResult],
        evidence_ledger: EvidenceLedgerView,
        verification_context: VerificationContext,
        candidate_result: CandidateResult,
    ) -> tuple[VerificationIssue, ...]:
        del verification_context, candidate_result
        constraints = _ap_constraints(task_contract)
        if constraints is None:
            return (_profile_issue(task_contract.task_id, self.name),)
        task_id = task_contract.task_id
        tenant_id = constraints.tenant_id
        evidence = evidence_ledger.list(task_id, tenant_id=tenant_id)
        runs, issues_tuple = parse_ap_calculation_runs(
            task_id=task_id,
            tenant_id=tenant_id,
            evidence=evidence,
        )
        issues = list(issues_tuple)
        operation_counts = Counter(run.operation for run in runs)
        required = _required_operations(constraints)
        for operation in required:
            if operation_counts[operation] != 1:
                issues.append(
                    _issue(
                        "AP_OPERATION_COVERAGE_INVALID",
                        "A requested AP operation does not have exactly one complete result",
                        self.name,
                        task_id,
                        details={"operation_name": operation.value},
                    )
                )
        planned_steps = {step.step_id for step in task_plan.steps}
        for run in runs:
            if any(item.step_id not in planned_steps for item in run.items) or any(
                step_results.get(item.step_id) is None
                or step_results[item.step_id].status is not StepResultStatus.SUCCESS
                or item.evidence_id not in step_results[item.step_id].evidence
                for item in run.items
            ):
                issues.append(
                    _issue(
                        "AP_OPERATION_STEP_RESULT_INVALID",
                        "AP Calculation Evidence is not retained by a successful planned step",
                        self.name,
                        task_id,
                        evidence_ids=run.evidence_ids,
                    )
                )
            result = run.result
            run_trace = evidence_ledger.trace_lineage(
                task_id, run.evidence_ids[0], tenant_id=tenant_id
            )
            traced_database_ids = {
                item.evidence_id
                for item in run_trace.nodes
                if item.source_type is EvidenceType.DATABASE
            }
            traced_calculation_ids = {
                item.evidence_id
                for item in run_trace.nodes
                if item.source_type is EvidenceType.CALCULATION
            }
            if any(
                not set(record.database_evidence_ids).issubset(traced_database_ids)
                or (
                    record.calculation_evidence_id is not None
                    and record.calculation_evidence_id not in traced_calculation_ids
                )
                for record in result.records
            ):
                issues.append(
                    _issue(
                        "AP_RECORD_LINEAGE_INVALID",
                        "AP exception record identifiers do not resolve within its Calculation run",
                        self.name,
                        task_id,
                        evidence_ids=run.evidence_ids,
                    )
                )
            if result.input_row_count > 0 and result.eligibility_count == 0:
                issues.append(
                    _issue(
                        "AP_ELIGIBLE_COVERAGE_MISSING",
                        "A non-empty AP source population has zero eligible coverage",
                        self.name,
                        task_id,
                        evidence_ids=run.evidence_ids,
                    )
                )
            for record in result.records:
                issues.extend(
                    _record_dimension_scope_issues(
                        task_id=task_id,
                        run=run,
                        constraints=constraints,
                        supplier_id=record.supplier_id,
                        legal_entity_id=record.legal_entity_id,
                        business_unit_id=record.business_unit_id,
                        currency=record.currency,
                    )
                )
            for exclusion in result.exclusions:
                issues.extend(
                    _record_dimension_scope_issues(
                        task_id=task_id,
                        run=run,
                        constraints=constraints,
                        supplier_id=exclusion.supplier_id,
                        legal_entity_id=exclusion.legal_entity_id,
                        business_unit_id=exclusion.business_unit_id,
                        currency=exclusion.currency,
                    )
                )
        return tuple(issues)


class APNumericVerifier:
    """Resolve AP numeric claims to one exact batched Calculation Evidence baseline."""

    name = "APNumericVerifier"

    def verify(
        self,
        *,
        task_contract: TaskContract,
        task_plan: TaskPlan,
        step_results: Mapping[str, StepResult],
        evidence_ledger: EvidenceLedgerView,
        verification_context: VerificationContext,
        candidate_result: CandidateResult,
    ) -> tuple[VerificationIssue, ...]:
        del task_plan, step_results, verification_context
        constraints = _ap_constraints(task_contract)
        if constraints is None:
            return (_profile_issue(task_contract.task_id, self.name),)
        task_id = task_contract.task_id
        tenant_id = constraints.tenant_id
        runs, _ = parse_ap_calculation_runs(
            task_id=task_id,
            tenant_id=tenant_id,
            evidence=evidence_ledger.list(task_id, tenant_id=tenant_id),
        )
        issues: list[VerificationIssue] = []
        for claim in candidate_result.numeric_claims:
            issues.extend(_verify_numeric_claim(claim, runs, constraints, task_id))
        return tuple(issues)


def parse_ap_calculation_runs(
    *, task_id: str, tenant_id: str, evidence: tuple[EvidenceItem, ...]
) -> tuple[tuple[APCalculationRun, ...], tuple[VerificationIssue, ...]]:
    """Reconstruct every complete AP run and return safe structural issues."""
    del tenant_id
    verifier = APEvidenceMetadataVerifier.name
    evidence_by_id = {item.evidence_id: item for item in evidence}
    grouped: dict[tuple[APAnalyticsOperation, str], list[EvidenceItem]] = {}
    issues: list[VerificationIssue] = []
    for item in evidence:
        if item.source_type is not EvidenceType.CALCULATION:
            continue
        reference = item.source_reference.reference.root
        try:
            operation = APAnalyticsOperation(cast(str, reference.get("operation_name")))
        except (TypeError, ValueError):
            issues.append(
                _issue(
                    "AP_CALCULATION_OPERATION_INVALID",
                    "Calculation Evidence uses an unsupported AP operation",
                    verifier,
                    task_id,
                    item=item,
                )
            )
            continue
        run_id = reference.get("calculation_run_id")
        if not isinstance(run_id, str) or not run_id:
            issues.append(
                _issue(
                    "AP_CALCULATION_RUN_ID_MISSING",
                    "Calculation Evidence lacks a calculation run identifier",
                    verifier,
                    task_id,
                    item=item,
                )
            )
            continue
        grouped.setdefault((operation, run_id), []).append(item)

    runs: list[APCalculationRun] = []
    for (operation, run_id), raw_items in sorted(
        grouped.items(), key=lambda pair: (pair[0][0].value, pair[0][1])
    ):
        ordered = sorted(raw_items, key=_batch_index)
        first_reference = ordered[0].source_reference.reference.root
        batch_count = first_reference.get("batch_count")
        indices = tuple(_batch_index(item) for item in ordered)
        if (
            not isinstance(batch_count, int)
            or isinstance(batch_count, bool)
            or batch_count < 1
            or len(ordered) != batch_count
            or indices != tuple(range(batch_count))
        ):
            issues.append(
                _issue(
                    "AP_CALCULATION_BATCH_INCOMPLETE",
                    "AP Calculation Evidence batches are missing, duplicated, or out of range",
                    verifier,
                    task_id,
                    evidence_ids=tuple(item.evidence_id for item in ordered),
                )
            )
            continue
        metadata = ordered[0].content.data.root.get("result_metadata")
        if not isinstance(metadata, dict):
            issues.append(
                _issue(
                    "AP_CALCULATION_RESULT_METADATA_MISSING",
                    "AP Calculation Evidence has no typed result metadata",
                    verifier,
                    task_id,
                    evidence_ids=tuple(item.evidence_id for item in ordered),
                )
            )
            continue
        records: list[JsonMapping] = []
        groups: list[JsonMapping] = []
        rates: list[JsonMapping] = []
        exclusions: list[JsonMapping] = []
        valid = True
        for item in ordered:
            reference = item.source_reference.reference.root
            data = item.content.data.root
            if (
                reference.get("batch_count") != batch_count
                or reference.get("calculation_run_id") != run_id
                or reference.get("operation_name") != operation.value
                or reference.get("output_checksum") != first_reference.get("output_checksum")
                or data.get("result_metadata") != metadata
                or _checksum(data) != item.content.checksum
            ):
                valid = False
                break
            batch_items = data.get("batch_items")
            if not isinstance(batch_items, list) or len(batch_items) > AP_CALCULATION_BATCH_SIZE:
                valid = False
                break
            for raw in batch_items:
                if not isinstance(raw, dict) or not isinstance(raw.get("value"), dict):
                    valid = False
                    break
                kind = raw.get("kind")
                if not isinstance(kind, str):
                    valid = False
                    break
                target = {
                    "exception": records,
                    "duplicate_group": groups,
                    "supplier_rate": rates,
                    "exclusion": exclusions,
                }.get(kind)
                if target is None:
                    valid = False
                    break
                target.append(cast(JsonMapping, raw["value"]))
        if not valid:
            issues.append(
                _issue(
                    "AP_CALCULATION_BATCH_TAMPERED",
                    "AP Calculation Evidence batch metadata or content checksum drifted",
                    verifier,
                    task_id,
                    evidence_ids=tuple(item.evidence_id for item in ordered),
                )
            )
            continue
        payload: JsonMapping = {
            **metadata,
            "records": cast(JsonValue, records),
            "duplicate_groups": cast(JsonValue, groups),
            "supplier_rates": cast(JsonValue, rates),
            "exclusions": cast(JsonValue, exclusions),
        }
        try:
            result = APAnalyticsResultV1.model_validate(payload)
        except ValidationError:
            issues.append(
                _issue(
                    "AP_CALCULATION_RESULT_INVALID",
                    "AP Calculation batches do not reconstruct a valid typed result",
                    verifier,
                    task_id,
                    evidence_ids=tuple(item.evidence_id for item in ordered),
                )
            )
            continue
        expected_output_checksum = _checksum(
            result.model_dump(mode="json", exclude={"output_checksum"})
        )
        if result.output_checksum != expected_output_checksum:
            issues.append(
                _issue(
                    "AP_CALCULATION_OUTPUT_CHECKSUM_MISMATCH",
                    "AP Calculation result checksum does not match reconstructed batches",
                    verifier,
                    task_id,
                    evidence_ids=tuple(item.evidence_id for item in ordered),
                )
            )
            continue
        expected_reference: JsonMapping = {
            "operation_name": result.operation_name.value,
            "operation_version": result.operation_version,
            "engine_version": result.engine_version,
            "formulas": cast(JsonValue, list(AP_FORMULA_CATALOGUE[result.operation_name])),
            "normalization_version": result.normalization_version,
            "precision": result.precision,
            "rounding_mode": result.rounding_mode,
            "rule_ids": cast(JsonValue, list(result.rule_ids)),
            "rule_set_version": result.rule_set_version,
            "manifest_checksum": result.manifest_checksum,
            "input_checksums": cast(JsonValue, list(result.input_checksums)),
            "output_checksum": result.output_checksum,
        }
        first_parents = ordered[0].source_reference.input_evidence_ids
        reference_drift = any(
            item.source_reference.input_evidence_ids != first_parents
            or item.source_reference.reference.root.get("rule_versions")
            != first_reference.get("rule_versions")
            or any(
                item.source_reference.reference.root.get(key) != value
                for key, value in expected_reference.items()
            )
            for item in ordered
        )
        if operation is not result.operation_name or reference_drift:
            issues.append(
                _issue(
                    "AP_CALCULATION_REFERENCE_MISMATCH",
                    "AP Calculation reference metadata differs from its typed result",
                    verifier,
                    task_id,
                    evidence_ids=tuple(item.evidence_id for item in ordered),
                )
            )
            continue
        parent_checksums: set[str] = set()
        parents_valid = True
        for parent_id in first_parents:
            parent = evidence_by_id.get(parent_id)
            if parent is None:
                parents_valid = False
                break
            if parent.source_type is EvidenceType.DATABASE:
                parent_checksums.add(parent.content.checksum)
            elif parent.source_type is EvidenceType.CALCULATION:
                output_checksum = parent.source_reference.reference.root.get("output_checksum")
                if not isinstance(output_checksum, str):
                    parents_valid = False
                    break
                parent_checksums.add(output_checksum)
            elif parent.source_type is not EvidenceType.DOCUMENT:
                parents_valid = False
                break
        if not parents_valid or parent_checksums != set(result.input_checksums):
            issues.append(
                _issue(
                    "AP_CALCULATION_INPUT_CHECKSUM_MISMATCH",
                    "AP Calculation inputs do not match its Database/Calculation lineage checksums",
                    verifier,
                    task_id,
                    evidence_ids=tuple(item.evidence_id for item in ordered),
                )
            )
            continue
        runs.append(
            APCalculationRun(
                operation=operation,
                run_id=run_id,
                evidence_ids=tuple(item.evidence_id for item in ordered),
                items=tuple(ordered),
                result=result,
            )
        )
    return tuple(runs), tuple(issues)


def _document_metadata_issues(
    item: EvidenceItem, constraints: AccountsPayableConstraintsV1, verifier: str
) -> tuple[VerificationIssue, ...]:
    reference = item.source_reference.reference.root
    required_text = (
        "document_id",
        "document_version",
        "collection_id",
        "index_snapshot_id",
        "effective_from",
        "effective_to",
        "classification",
        "excerpt_checksum",
        "retrieval_trace_id",
        "policy_rule_set_version",
    )
    score = reference.get("retrieval_score")
    score_valid = isinstance(score, int | float) and not isinstance(score, bool) and score >= 0
    effective_from = _date(reference.get("effective_from"))
    effective_to = _date(reference.get("effective_to"))
    invalid = (
        any(
            not isinstance(reference.get(key), str) or not reference.get(key)
            for key in required_text
        )
        or not (
            isinstance(reference.get("chunk_id"), str) or isinstance(reference.get("page"), int)
        )
        or not score_valid
        or effective_from is None
        or effective_to is None
        or effective_from > constraints.time_range.start_date
        or effective_to < constraints.time_range.end_date
        or not _strings(reference.get("bound_rule_ids"))
        or reference.get("collection_id") != "accounts-payable-policy-v1"
        or reference.get("policy_rule_set_version") != constraints.policy_rule_set_version
        or reference.get("excerpt_checksum") != item.content.checksum
        or reference.get("classification") != item.content.classification
    )
    return (
        (
            _issue(
                "AP_DOCUMENT_METADATA_INVALID",
                "AP Document Evidence lacks exact policy provenance metadata",
                verifier,
                item.task_id,
                item=item,
            ),
        )
        if invalid
        else ()
    )


def _database_metadata_issues(
    item: EvidenceItem, constraints: AccountsPayableConstraintsV1, verifier: str
) -> tuple[VerificationIssue, ...]:
    reference = item.source_reference.reference.root
    content = item.content.data.root
    summary = reference.get("parameter_summary")
    schema = reference.get("schema_snapshot")
    tables = _strings(reference.get("table_names"))
    columns = _strings(reference.get("column_names"))
    expected_scope = _expected_scope_hashes(constraints)
    invalid = (
        not isinstance(reference.get("query_id"), str)
        or not isinstance(reference.get("query_fingerprint"), str)
        or not isinstance(reference.get("query_template_id"), str)
        or reference.get("template_version") != reference.get("query_template_id")
        or reference.get("schema_version") != "accounts_payable.v1"
        or not isinstance(schema, dict)
        or schema.get("version") != "accounts_payable.v1"
        or schema.get("snapshot_at") != reference.get("snapshot_at")
        or reference.get("snapshot_at") != constraints.snapshot_at.isoformat()
        or reference.get("statement_type") != "SELECT"
        or reference.get("read_only") is not True
        or not tables
        or not columns
        or tuple(sorted(set(tables))) != tables
        or tuple(sorted(set(columns))) != columns
        or not isinstance(summary, dict)
        or any(summary.get(key) != value for key, value in expected_scope.items())
        or reference.get("row_count") != content.get("row_count")
        or content.get("empty_result") is not (content.get("row_count") == 0)
        or content.get("truncated") is not False
        or reference.get("dataset_checksum") != item.content.checksum
    )
    code = (
        "AP_DATABASE_TRUNCATED"
        if content.get("truncated") is True
        else "AP_DATABASE_METADATA_INVALID"
    )
    return (
        (
            _issue(
                code,
                "AP Database Evidence lacks exact scope, snapshot, or completeness metadata",
                verifier,
                item.task_id,
                item=item,
            ),
        )
        if invalid
        else ()
    )


def _calculation_metadata_issues(
    item: EvidenceItem, constraints: AccountsPayableConstraintsV1, verifier: str
) -> tuple[VerificationIssue, ...]:
    reference = item.source_reference.reference.root
    versions = reference.get("rule_versions")
    required_text = (
        "operation_name",
        "operation_version",
        "engine_version",
        "normalization_version",
        "precision",
        "rounding_mode",
        "rule_set_version",
        "manifest_checksum",
        "calculation_run_id",
        "output_checksum",
    )
    try:
        operation = APAnalyticsOperation(cast(str, reference.get("operation_name")))
    except (TypeError, ValueError):
        operation = None
    invalid = (
        any(
            not isinstance(reference.get(key), str) or not reference.get(key)
            for key in required_text
        )
        or reference.get("operation_version") != AP_ANALYTICS_OPERATION_VERSION
        or reference.get("engine_version") != AP_ANALYTICS_ENGINE_VERSION
        or reference.get("rule_set_version") != constraints.policy_rule_set_version
        or reference.get("manifest_checksum") != constraints.policy_manifest_checksum
        or operation is None
        or reference.get("formulas")
        != (list(AP_FORMULA_CATALOGUE[operation]) if operation is not None else None)
        or not _strings(reference.get("rule_ids"))
        or not isinstance(versions, dict)
        or (
            isinstance(versions, dict) and set(versions) != set(_strings(reference.get("rule_ids")))
        )
        or not _strings(reference.get("input_checksums"))
        or not item.source_reference.input_evidence_ids
        or not isinstance(reference.get("batch_index"), int)
        or not isinstance(reference.get("batch_count"), int)
    )
    return (
        (
            _issue(
                "AP_CALCULATION_METADATA_INVALID",
                "AP Calculation Evidence lacks exact operation, rule, or batch metadata",
                verifier,
                item.task_id,
                item=item,
            ),
        )
        if invalid
        else ()
    )


def _verify_numeric_claim(
    claim: NumericClaim,
    runs: tuple[APCalculationRun, ...],
    constraints: AccountsPayableConstraintsV1,
    task_id: str,
) -> tuple[VerificationIssue, ...]:
    expected = _METRIC_POLICY.get(claim.metric_name)
    if expected is None:
        return (
            _numeric_issue(
                "AP_NUMERIC_METRIC_UNSUPPORTED",
                "AP numeric metric is not allowlisted",
                task_id,
                claim,
            ),
        )
    expected_unit, expected_precision = expected
    if claim.unit != expected_unit or claim.precision != expected_precision:
        return (
            _numeric_issue(
                "AP_NUMERIC_UNIT_PRECISION_MISMATCH",
                "AP numeric unit or precision differs from its frozen metric",
                task_id,
                claim,
            ),
        )
    if claim.operation_name is None:
        return (
            _numeric_issue(
                "AP_NUMERIC_OPERATION_MISSING",
                "AP numeric claim lacks its baseline operation",
                task_id,
                claim,
            ),
        )
    try:
        operation = APAnalyticsOperation(claim.operation_name)
    except ValueError:
        return (
            _numeric_issue(
                "AP_NUMERIC_OPERATION_INVALID",
                "AP numeric claim uses an unsupported operation",
                task_id,
                claim,
            ),
        )
    cited = set(claim.evidence_ids)
    matching_runs = tuple(
        run for run in runs if run.operation is operation and set(run.evidence_ids).issubset(cited)
    )
    baselines = [
        value
        for run in matching_runs
        for value in _baseline_values(run.result, claim.metric_name, claim.dimensions.root)
    ]
    if len(matching_runs) != 1 or len(baselines) != 1:
        return (
            _numeric_issue(
                "AP_NUMERIC_BASELINE_NOT_UNIQUE",
                "AP numeric claim does not resolve to exactly one complete Calculation baseline",
                task_id,
                claim,
            ),
        )
    if expected_unit == "money":
        currency = claim.dimensions.root.get("currency")
        if not isinstance(currency, str) or (
            constraints.currency_scope and currency not in constraints.currency_scope
        ):
            return (
                _numeric_issue(
                    "AP_NUMERIC_CURRENCY_MISMATCH",
                    "AP money claim lacks the exact allowed currency dimension",
                    task_id,
                    claim,
                ),
            )
    actual = _decimal(claim.value)
    baseline = _decimal(baselines[0])
    if actual is None or baseline is None or not actual.is_finite() or not baseline.is_finite():
        matches = actual is None and baseline is None
    elif expected_unit in {"count", "day_count"}:
        matches = (
            actual == actual.to_integral_value()
            and baseline == baseline.to_integral_value()
            and actual == baseline
        )
    else:
        quantum = Decimal(1).scaleb(-expected_precision)
        actual_exponent = actual.as_tuple().exponent
        baseline_exponent = baseline.as_tuple().exponent
        matches = (
            actual == baseline
            and isinstance(actual_exponent, int)
            and isinstance(baseline_exponent, int)
            and actual_exponent == -expected_precision
            and baseline_exponent == -expected_precision
            and baseline.quantize(quantum) == baseline
        )
    return (
        ()
        if matches
        else (
            _numeric_issue(
                "AP_NUMERIC_CLAIM_MISMATCH",
                "AP numeric claim differs from deterministic Calculation Evidence",
                task_id,
                claim,
            ),
        )
    )


def _baseline_values(
    result: APAnalyticsResultV1, metric_name: str, dimensions: JsonMapping
) -> tuple[JsonValue | Decimal, ...]:
    supplier_id = dimensions.get("supplier_id")
    if isinstance(supplier_id, str):
        values: list[JsonValue | Decimal] = []
        for supplier_record in result.supplier_rates:
            if supplier_record.supplier_id != supplier_id or not hasattr(
                supplier_record, metric_name
            ):
                continue
            raw = getattr(supplier_record, metric_name)
            values.extend(
                _dimension_value(raw.root if isinstance(raw, JsonObject) else raw, dimensions)
            )
        return tuple(values)
    invoice_key = dimensions.get("invoice_record_key")
    if isinstance(invoice_key, str):
        values = []
        for exception_record in result.records:
            if exception_record.invoice_record_key != invoice_key:
                continue
            exception_type = dimensions.get("exception_type")
            if (
                isinstance(exception_type, str)
                and exception_record.exception_type.value != exception_type
            ):
                continue
            raw = exception_record.observed_values.root.get(metric_name)
            if raw is not None:
                values.append(raw)
        return tuple(values)
    raw_metric = result.metrics.root.get(metric_name)
    return _dimension_value(raw_metric, dimensions)


def _dimension_value(value: object, dimensions: JsonMapping) -> tuple[JsonValue | Decimal, ...]:
    if isinstance(value, dict):
        currency = dimensions.get("currency")
        if not isinstance(currency, str) or currency not in value:
            return ()
        return (cast(JsonValue, value[currency]),)
    if value is None:
        return (None,)
    return (cast(JsonValue | Decimal, value),)


_METRIC_POLICY: dict[str, tuple[str, int]] = {
    "duplicate_group_count": ("count", 0),
    "duplicate_invoice_count": ("count", 0),
    "duplicate_exposure_amount_by_currency": ("money", 4),
    "invoice_count": ("count", 0),
    "exception_invoice_count": ("count", 0),
    "exception_rate": ("ratio", 8),
    "invoice_amount_by_currency": ("money", 4),
    "exception_invoice_amount_by_currency": ("money", 4),
    "po_variance_exception_count": ("count", 0),
    "absolute_variance_amount_by_currency": ("money", 4),
    "variance_amount": ("money", 4),
    "absolute_variance_amount": ("money", 4),
    "variance_rate": ("ratio", 8),
    "absolute_variance_rate": ("ratio", 8),
    "missing_required_po_count": ("count", 0),
    "missing_po_exposure_amount_by_currency": ("money", 4),
    "late_payment_count": ("count", 0),
    "material_early_payment_count": ("count", 0),
    "days_late": ("day_count", 0),
    "days_early": ("day_count", 0),
    "average_days_late": ("days", 2),
    "overpayment_count": ("count", 0),
    "overpayment_amount": ("money", 4),
    "overpayment_amount_by_currency": ("money", 4),
    "eligible_invoice_count": ("count", 0),
    "supplier_exception_rate": ("ratio", 8),
    "exception_amount_by_currency": ("money", 4),
    "supplier_count": ("count", 0),
}


def _required_operations(
    constraints: AccountsPayableConstraintsV1,
) -> tuple[APAnalyticsOperation, ...]:
    mapping = {
        APExceptionType.EXACT_DUPLICATE_INVOICE: (
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION
        ),
        APExceptionType.PO_AMOUNT_VARIANCE: APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION,
        APExceptionType.MISSING_REQUIRED_PO: APAnalyticsOperation.MISSING_PO_DETECTION,
        APExceptionType.LATE_PAYMENT: APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION,
        APExceptionType.MATERIAL_EARLY_PAYMENT: (
            APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION
        ),
        APExceptionType.OVERPAYMENT: APAnalyticsOperation.OVERPAYMENT_DETECTION,
    }
    return tuple(
        dict.fromkeys(
            (
                *(mapping[item] for item in constraints.exception_types),
                APAnalyticsOperation.EXCEPTION_SUMMARY,
                APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
            )
        )
    )


def _expected_scope_hashes(constraints: AccountsPayableConstraintsV1) -> JsonMapping:
    return {
        "tenant_scope_hash": _checksum(constraints.tenant_id),
        "time_scope_hash": _checksum(
            {
                "start_date": constraints.time_range.start_date.isoformat(),
                "end_date": constraints.time_range.end_date.isoformat(),
            }
        ),
        "supplier_count": len(constraints.supplier_ids),
        "supplier_scope_hash": _checksum(sorted(constraints.supplier_ids)),
        "legal_entity_count": len(constraints.legal_entity_ids),
        "legal_entity_scope_hash": _checksum(sorted(constraints.legal_entity_ids)),
        "business_unit_count": len(constraints.business_unit_ids),
        "business_unit_scope_hash": _checksum(sorted(constraints.business_unit_ids)),
        "currency_count": len(constraints.currency_scope),
        "currency_scope_hash": _checksum(sorted(constraints.currency_scope)),
    }


def _ap_constraints(task_contract: TaskContract) -> AccountsPayableConstraintsV1 | None:
    if task_contract.task_type is TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1 and isinstance(
        task_contract.constraints, AccountsPayableConstraintsV1
    ):
        return task_contract.constraints
    return None


def _restricted_paths(value: JsonValue, prefix: str = "") -> tuple[str, ...]:
    registry = SensitiveDataRegistry()
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if registry.policy_for(key) is not None:
                paths.append(path)
            paths.extend(_restricted_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_restricted_paths(child, f"{prefix}[{index}]"))
    return tuple(paths)


def _record_dimension_scope_issues(
    *,
    task_id: str,
    run: APCalculationRun,
    constraints: AccountsPayableConstraintsV1,
    supplier_id: str,
    legal_entity_id: str,
    business_unit_id: str,
    currency: str | None,
) -> tuple[VerificationIssue, ...]:
    dimensions: list[str] = []
    if constraints.supplier_ids and supplier_id not in constraints.supplier_ids:
        dimensions.append("supplier_id")
    if legal_entity_id not in constraints.legal_entity_ids:
        dimensions.append("legal_entity_id")
    if constraints.business_unit_ids and business_unit_id not in constraints.business_unit_ids:
        dimensions.append("business_unit_id")
    if (
        currency is not None
        and constraints.currency_scope
        and currency not in constraints.currency_scope
    ):
        dimensions.append("currency")
    return tuple(_record_scope_issue(task_id, run, dimension) for dimension in dimensions)


def _record_scope_issue(task_id: str, run: APCalculationRun, dimension: str) -> VerificationIssue:
    return _issue(
        "AP_RECORD_SCOPE_MISMATCH",
        "AP calculation record is outside the frozen Task scope",
        APConsistencyVerifier.name,
        task_id,
        evidence_ids=run.evidence_ids,
        details={"dimension": dimension, "operation_name": run.operation.value},
    )


def _batch_index(item: EvidenceItem) -> int:
    value = item.source_reference.reference.root.get("batch_index")
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float | str):
        return Decimal("NaN")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("NaN")


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _checksum(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _profile_issue(task_id: str, verifier: str) -> VerificationIssue:
    return _issue(
        "AP_VERIFIER_PROFILE_MISMATCH",
        "AP verifier was invoked for a non-AP Task Contract",
        verifier,
        task_id,
    )


def _claim_issue(
    code: str,
    message: str,
    verifier: str,
    task_id: str,
    claim_id: str,
    evidence_ids: tuple[str, ...],
) -> VerificationIssue:
    return _issue(
        code,
        message,
        verifier,
        task_id,
        claim_id=claim_id,
        evidence_ids=evidence_ids,
    )


def _numeric_issue(code: str, message: str, task_id: str, claim: NumericClaim) -> VerificationIssue:
    return _issue(
        code,
        message,
        APNumericVerifier.name,
        task_id,
        claim_id=claim.claim_id,
        evidence_ids=claim.evidence_ids,
    )


def _issue(
    code: str,
    message: str,
    verifier: str,
    task_id: str,
    *,
    item: EvidenceItem | None = None,
    claim_id: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    details: JsonMapping | None = None,
) -> VerificationIssue:
    return VerificationIssue(
        code=code,
        message=message,
        severity=VerificationSeverity.ERROR,
        verifier=verifier,
        task_id=task_id,
        step_id=item.step_id if item is not None else None,
        claim_id=claim_id,
        evidence_ids=(item.evidence_id,) if item is not None else evidence_ids,
        details=JsonObject(details or {}),
    )


__all__ = [
    "APCalculationRun",
    "APConsistencyVerifier",
    "APEvidenceMetadataVerifier",
    "APNumericVerifier",
    "APPolicyBindingVerifier",
    "parse_ap_calculation_runs",
]
