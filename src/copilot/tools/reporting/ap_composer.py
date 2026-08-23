"""Deterministic Accounts Payable report composition from verified structured inputs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from copilot.contracts import APReportClaimV1, ClaimType, EvidenceItem, EvidenceType, JsonObject
from copilot.contracts.base import JsonMapping
from copilot.evidence.ap_validators import APCalculationRun, parse_ap_calculation_runs
from copilot.services.workflows.ports import EvidenceReader
from copilot.tools.analytics.ap_schemas import (
    APAnalyticsOperation,
    APAnalyticsResultV1,
    APExceptionRecordV1,
    APExceptionStatus,
)
from copilot.tools.reporting.ap_schemas import (
    AP_MATERIAL_DETAIL_LIMIT,
    AP_REPORT_GENERATOR_VERSION,
    AP_REPORT_SCHEMA_VERSION,
    AccountsPayableReportV1,
    APDataOverviewV1,
    APDataSourceV1,
    APDetailAccess,
    APEvidenceReferenceV1,
    APEvidenceSectionV1,
    APExceptionSummarySectionV1,
    APExecutionMetadataV1,
    APExecutionStepV1,
    APFindingV1,
    APLimitationV1,
    APPolicyReferenceV1,
    APRecommendationV1,
    APReportRequestV1,
    APReportTaskSummaryV1,
    APRiskObservationV1,
    APSupplierSummaryV1,
)
from copilot.tools.reporting.exceptions import ReportInputDeniedError, ReportInputError

_DETAIL_OBSERVED_FIELDS = frozenset(
    {
        "absolute_variance_amount",
        "absolute_variance_rate",
        "approved_amount",
        "approved_no_po_exception",
        "canonical_invoice_record_key",
        "days_early",
        "days_late",
        "gross_amount",
        "member_invoice_record_keys",
        "overpayment_amount",
        "payment_amount",
        "purchase_order_present",
        "variance_amount",
        "variance_rate",
    }
)
_DETAIL_THRESHOLD_FIELDS = frozenset(
    {
        "allowed_variance_amount",
        "allowed_variance_rate",
        "material_early_days",
        "overpayment_tolerance",
        "po_required_min_amount",
    }
)
_COUNT_METRICS = frozenset(
    {
        "duplicate_group_count",
        "duplicate_invoice_count",
        "eligible_invoice_count",
        "exception_invoice_count",
        "invoice_count",
        "late_payment_count",
        "material_early_payment_count",
        "missing_required_po_count",
        "overpayment_count",
        "po_variance_exception_count",
        "supplier_count",
    }
)
_MONEY_METRICS = frozenset(
    {
        "absolute_variance_amount",
        "absolute_variance_amount_by_currency",
        "duplicate_exposure_amount_by_currency",
        "exception_amount_by_currency",
        "exception_invoice_amount_by_currency",
        "invoice_amount_by_currency",
        "missing_po_exposure_amount_by_currency",
        "overpayment_amount",
        "overpayment_amount_by_currency",
        "variance_amount",
    }
)
_RATIO_METRICS = frozenset(
    {
        "absolute_variance_rate",
        "exception_rate",
        "supplier_exception_rate",
        "variance_rate",
    }
)
_DAY_METRICS = frozenset({"days_early", "days_late"})
_DAYS_METRICS = frozenset({"average_days_late"})


class AccountsPayableReportComposer:
    """Build one AP report without querying, recalculating, or model-authored prose."""

    def __init__(
        self,
        evidence_reader: EvidenceReader,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._evidence_reader = evidence_reader
        self._clock = clock

    def load_evidence(
        self,
        request: APReportRequestV1,
        *,
        task_id: str,
        tenant_id: str,
    ) -> tuple[EvidenceItem, ...]:
        """Resolve every referenced item with current-task and tenant ownership checks."""
        loaded: list[EvidenceItem] = []
        for evidence_id in request.evidence_refs:
            try:
                item = self._evidence_reader.get(
                    evidence_id,
                    task_id=task_id,
                    tenant_id=tenant_id,
                )
            except (KeyError, LookupError) as exc:
                raise ReportInputError("AP report references Evidence that does not exist") from exc
            if item.task_id != task_id:
                raise ReportInputDeniedError("AP report Evidence belongs to another task")
            loaded.append(item)
        return tuple(loaded)

    def compose(
        self,
        request: APReportRequestV1,
        evidence: tuple[EvidenceItem, ...],
        *,
        tenant_id: str,
    ) -> AccountsPayableReportV1:
        """Compose the canonical model from exact policy, database, and calculation data."""
        runs, issues = parse_ap_calculation_runs(
            task_id=request.task_id,
            tenant_id=tenant_id,
            evidence=evidence,
        )
        if issues:
            codes = ",".join(sorted({issue.code for issue in issues}))
            raise ReportInputError(f"AP Calculation Evidence is invalid: {codes}")
        summary_run = _one_run(runs, APAnalyticsOperation.EXCEPTION_SUMMARY)
        if summary_run.result != request.exception_summary_result:
            raise ReportInputError("AP report summary differs from Calculation Evidence")
        supplier_run = _one_run(runs, APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE)
        policies = _policy_references(request, evidence)
        data_overview = _data_overview(evidence)
        all_records = tuple(
            record
            for run in runs
            if run.operation
            not in {
                APAnalyticsOperation.EXCEPTION_SUMMARY,
                APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
            }
            for record in run.result.records
        )
        record_run = {
            record.exception_id: run
            for run in runs
            for record in run.result.records
            if run.operation
            not in {
                APAnalyticsOperation.EXCEPTION_SUMMARY,
                APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
            }
        }
        details = _details(request.detail_access, all_records, record_run)
        claims = _claims(
            runs,
            evidence,
            include_record_claims=request.detail_access is APDetailAccess.DETAIL,
        )
        references = tuple(_evidence_reference(item) for item in evidence)
        trace = tuple(
            APExecutionStepV1(
                step_id=item.step_id,
                tool_call_id=item.tool_call_id,
                evidence_id=item.evidence_id,
                source_type=item.source_type,
            )
            for item in evidence
        )
        limitations = _limitations(
            runs,
            source_record_count=len(all_records),
            published_detail_count=len(details),
            empty_result=summary_run.result.empty_result,
        )
        summary = _summary_section(summary_run)
        return AccountsPayableReportV1(
            title="Accounts Payable Invoice Compliance & Exception Investigation",
            executive_summary=_executive_summary(summary_run.result),
            task_summary=APReportTaskSummaryV1(
                task_id=request.task_id,
                task_status="EXECUTING",
            ),
            scope=request.scope,
            data_overview=data_overview,
            applicable_policies=policies,
            exception_summary=summary,
            duplicate_invoice_findings=tuple(
                item for item in details if item.exception_type == "EXACT_DUPLICATE_INVOICE"
            ),
            po_compliance_findings=tuple(
                item
                for item in details
                if item.exception_type in {"PO_AMOUNT_VARIANCE", "MISSING_REQUIRED_PO"}
            ),
            payment_findings=tuple(
                item
                for item in details
                if item.exception_type in {"LATE_PAYMENT", "MATERIAL_EARLY_PAYMENT", "OVERPAYMENT"}
            ),
            material_exceptions=tuple(
                item for item in details if item.status is APExceptionStatus.FINDING
            )[:AP_MATERIAL_DETAIL_LIMIT],
            supplier_summary=tuple(
                APSupplierSummaryV1(
                    **rate.model_dump(mode="json"),
                    evidence_ids=supplier_run.evidence_ids,
                )
                for rate in supplier_run.result.supplier_rates
            ),
            risk_observations=_risk_observations(summary_run),
            recommended_actions=_recommendations(summary_run),
            limitations=limitations,
            evidence=APEvidenceSectionV1(claims=claims, references=references),
            execution_trace=trace,
            execution_metadata=APExecutionMetadataV1(
                generated_at=self._clock(),
                schema_version=AP_REPORT_SCHEMA_VERSION,
                template_version=request.template_version,
                generator_version=AP_REPORT_GENERATOR_VERSION,
                rule_set_version=request.policy_rule_snapshot.rule_manifest.rule_set_version,
                policy_manifest_checksum=(
                    request.policy_rule_snapshot.rule_manifest.manifest_checksum
                ),
                language=request.language,
                detail_access=request.detail_access,
                verification_status="PENDING",
            ),
        )


def _one_run(
    runs: tuple[APCalculationRun, ...], operation: APAnalyticsOperation
) -> APCalculationRun:
    selected = tuple(run for run in runs if run.operation is operation)
    if len(selected) != 1:
        raise ReportInputError(f"AP report requires exactly one {operation.value} run")
    return selected[0]


def _policy_references(
    request: APReportRequestV1,
    evidence: tuple[EvidenceItem, ...],
) -> tuple[APPolicyReferenceV1, ...]:
    documents = {
        item.evidence_id: item for item in evidence if item.source_type is EvidenceType.DOCUMENT
    }
    expected = {
        item.evidence_id: item.rule_id for item in request.policy_rule_snapshot.document_evidence
    }
    if set(documents) != set(expected):
        raise ReportInputError("AP report policy Evidence differs from its rule snapshot")
    references: list[APPolicyReferenceV1] = []
    for evidence_id in request.evidence_refs:
        item = documents.get(evidence_id)
        if item is None:
            continue
        source = item.source_reference.reference.root
        content = item.content.data.root
        document_id = source.get("document_id")
        version = source.get("document_version")
        location = source.get("chunk_id") or source.get("page")
        excerpt = content.get("excerpt")
        classification = source.get("classification")
        bound_rule_ids = source.get("bound_rule_ids")
        if not all(isinstance(value, str) and value for value in (document_id, version, excerpt)):
            raise ReportInputError("AP Document Evidence lacks required report metadata")
        if not isinstance(location, str | int) or not isinstance(classification, str):
            raise ReportInputError("AP Document Evidence location/classification is invalid")
        if not isinstance(bound_rule_ids, list) or expected[evidence_id] not in bound_rule_ids:
            raise ReportInputError("AP Document Evidence rule binding is invalid")
        references.append(
            APPolicyReferenceV1(
                evidence_id=evidence_id,
                document_id=cast(str, document_id),
                document_version=cast(str, version),
                location=str(location),
                excerpt=cast(str, excerpt),
                classification=classification,
                rule_ids=tuple(
                    sorted(item for item in bound_rule_ids if isinstance(item, str) and item)
                ),
            )
        )
    return tuple(references)


def _data_overview(evidence: tuple[EvidenceItem, ...]) -> APDataOverviewV1:
    sources: list[APDataSourceV1] = []
    for item in evidence:
        if item.source_type is not EvidenceType.DATABASE:
            continue
        source = item.source_reference.reference.root
        content = item.content.data.root
        try:
            sources.append(
                APDataSourceV1(
                    evidence_id=item.evidence_id,
                    query_template_id=cast(str, source["query_template_id"]),
                    query_fingerprint=cast(str, source["query_fingerprint"]),
                    row_count=cast(int, source["row_count"]),
                    empty_result=cast(bool, content["empty_result"]),
                    truncated=cast(Literal[False], content["truncated"]),
                    snapshot_at=datetime.fromisoformat(cast(str, source["snapshot_at"])),
                    dataset_checksum=cast(str, source["dataset_checksum"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReportInputError("AP Database Evidence lacks report coverage metadata") from exc
    if not sources:
        raise ReportInputError("AP report requires Database Evidence")
    return APDataOverviewV1(
        sources=tuple(sources),
        empty_result=all(source.empty_result for source in sources),
    )


def _summary_section(run: APCalculationRun) -> APExceptionSummarySectionV1:
    metrics = run.result.metrics.root
    finding_count = metrics.get("finding_count")
    warning_count = metrics.get("warning_count")
    if not isinstance(finding_count, int) or not isinstance(warning_count, int):
        raise ReportInputError("AP exception summary lacks severity counts")
    return APExceptionSummarySectionV1(
        operation_name=APAnalyticsOperation.EXCEPTION_SUMMARY,
        metrics=run.result.metrics,
        eligibility_count=run.result.eligibility_count,
        exclusion_count=run.result.exclusion_count,
        exclusion_count_by_reason=run.result.exclusion_count_by_reason,
        finding_count=finding_count,
        warning_count=warning_count,
        evidence_ids=run.evidence_ids,
        empty_result=run.result.empty_result,
    )


def _details(
    detail_access: APDetailAccess,
    records: tuple[APExceptionRecordV1, ...],
    record_run: dict[str, APCalculationRun],
) -> tuple[APFindingV1, ...]:
    if detail_access is APDetailAccess.AGGREGATE:
        return ()
    selected = sorted(
        records,
        key=lambda item: (
            item.status is not APExceptionStatus.FINDING,
            item.exception_type.value,
            item.supplier_id,
            item.currency,
            item.invoice_record_key,
            item.exception_id,
        ),
    )[:AP_MATERIAL_DETAIL_LIMIT]
    details: list[APFindingV1] = []
    for record in selected:
        run = record_run[record.exception_id]
        details.append(
            APFindingV1(
                exception_id=record.exception_id,
                exception_type=record.exception_type.value,
                supplier_id=record.supplier_id,
                currency=record.currency,
                status=record.status,
                invoice_record_key=record.invoice_record_key,
                observed_values=JsonObject(
                    {
                        key: value
                        for key, value in record.observed_values.root.items()
                        if key in _DETAIL_OBSERVED_FIELDS
                    }
                ),
                threshold_values=JsonObject(
                    {
                        key: value
                        for key, value in record.threshold_values.root.items()
                        if key in _DETAIL_THRESHOLD_FIELDS
                    }
                ),
                rule_id=record.rule_id,
                rule_version=record.rule_version,
                reason_codes=record.reason_codes,
                evidence_ids=run.evidence_ids,
            )
        )
    return tuple(details)


def _claims(
    runs: tuple[APCalculationRun, ...],
    evidence: tuple[EvidenceItem, ...],
    *,
    include_record_claims: bool,
) -> tuple[APReportClaimV1, ...]:
    claims: list[APReportClaimV1] = []
    document_ids = tuple(
        item.evidence_id for item in evidence if item.source_type is EvidenceType.DOCUMENT
    )
    database_ids = tuple(
        item.evidence_id for item in evidence if item.source_type is EvidenceType.DATABASE
    )
    if document_ids:
        rule_ids = tuple(
            sorted(
                {
                    rule_id
                    for item in evidence
                    if item.source_type is EvidenceType.DOCUMENT
                    for rule_id in _strings(
                        item.source_reference.reference.root.get("bound_rule_ids")
                    )
                }
            )
        )
        claims.append(
            APReportClaimV1(
                claim_id="ap:policy-snapshot",
                claim_type=ClaimType.POLICY,
                evidence_ids=document_ids,
                policy_governed=True,
                rule_ids=rule_ids,
            )
        )
    if database_ids:
        claims.append(
            APReportClaimV1(
                claim_id="ap:data-coverage",
                claim_type=ClaimType.DATA,
                evidence_ids=database_ids,
            )
        )
    for run in runs:
        claims.extend(_run_metric_claims(run))
        if include_record_claims:
            claims.extend(_record_metric_claims(run))
        if run.result.records:
            claims.append(
                APReportClaimV1(
                    claim_id=f"ap:{run.operation.value}:findings",
                    claim_type=ClaimType.DATA,
                    evidence_ids=run.evidence_ids,
                    policy_governed=True,
                    rule_ids=run.result.rule_ids,
                )
            )
    return tuple(claims)


def _run_metric_claims(run: APCalculationRun) -> tuple[APReportClaimV1, ...]:
    claims: list[APReportClaimV1] = []
    for metric_name, raw in sorted(run.result.metrics.root.items()):
        policy = _metric_policy(metric_name)
        if policy is None:
            continue
        unit, precision = policy
        values = raw if isinstance(raw, dict) else {None: raw}
        for dimension, value in sorted(values.items(), key=lambda item: str(item[0])):
            dimensions: JsonMapping = {}
            if dimension is not None:
                dimensions["currency"] = str(dimension)
            claims.append(
                _numeric_claim(
                    claim_id=(
                        f"ap:{run.operation.value}:{metric_name}"
                        + (f":{dimension}" if dimension is not None else "")
                    ),
                    run=run,
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                    precision=precision,
                    dimensions=dimensions,
                )
            )
    for rate in run.result.supplier_rates:
        fields: tuple[tuple[str, object], ...] = (
            ("eligible_invoice_count", rate.eligible_invoice_count),
            ("supplier_exception_rate", rate.supplier_exception_rate),
            ("exception_invoice_count", rate.exception_invoice_count),
            ("invoice_amount_by_currency", rate.invoice_amount_by_currency.root),
            ("exception_amount_by_currency", rate.exception_amount_by_currency.root),
        )
        for metric_name, field_value in fields:
            policy = _metric_policy(metric_name)
            if policy is None:
                continue
            unit, precision = policy
            supplier_values = field_value if isinstance(field_value, dict) else {None: field_value}
            for currency, supplier_value in sorted(
                supplier_values.items(), key=lambda item: str(item[0])
            ):
                dimensions = {"supplier_id": rate.supplier_id}
                if currency is not None:
                    dimensions["currency"] = str(currency)
                claims.append(
                    _numeric_claim(
                        claim_id=(
                            f"ap:{run.operation.value}:{rate.supplier_id}:{metric_name}"
                            + (f":{currency}" if currency is not None else "")
                        ),
                        run=run,
                        metric_name=metric_name,
                        value=supplier_value,
                        unit=unit,
                        precision=precision,
                        dimensions=dimensions,
                    )
                )
    return tuple(claims)


def _record_metric_claims(run: APCalculationRun) -> tuple[APReportClaimV1, ...]:
    claims: list[APReportClaimV1] = []
    for record in run.result.records[:AP_MATERIAL_DETAIL_LIMIT]:
        for metric_name, raw in sorted(record.observed_values.root.items()):
            policy = _metric_policy(metric_name)
            if policy is None:
                continue
            unit, precision = policy
            claims.append(
                _numeric_claim(
                    claim_id=f"ap:{run.operation.value}:{record.exception_id}:{metric_name}",
                    run=run,
                    metric_name=metric_name,
                    value=raw,
                    unit=unit,
                    precision=precision,
                    dimensions={
                        "invoice_record_key": record.invoice_record_key,
                        "exception_type": record.exception_type.value,
                        **({"currency": record.currency} if unit == "money" else {}),
                    },
                )
            )
    return tuple(claims)


def _numeric_claim(
    *,
    claim_id: str,
    run: APCalculationRun,
    metric_name: str,
    value: object,
    unit: str,
    precision: int,
    dimensions: JsonMapping,
) -> APReportClaimV1:
    return APReportClaimV1(
        claim_id=claim_id,
        claim_type=ClaimType.NUMERIC,
        evidence_ids=run.evidence_ids,
        policy_governed=True,
        rule_ids=run.result.rule_ids,
        metric_name=metric_name,
        value=_numeric_value(value, unit),
        unit=unit,
        precision=precision,
        operation_name=run.operation.value,
        dimensions=JsonObject(dimensions),
    )


def _numeric_value(value: object, unit: str) -> Decimal | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float | str):
        raise ReportInputError("AP report metric has a non-numeric value")
    if unit in {"count", "day_count"}:
        try:
            decimal = Decimal(str(value))
        except InvalidOperation as exc:
            raise ReportInputError("AP report count is invalid") from exc
        if decimal != decimal.to_integral_value():
            raise ReportInputError("AP report count is not an integer")
        return int(decimal)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ReportInputError("AP report metric is invalid") from exc


def _metric_policy(metric_name: str) -> tuple[str, int] | None:
    if metric_name in _COUNT_METRICS:
        return "count", 0
    if metric_name in _MONEY_METRICS:
        return "money", 4
    if metric_name in _RATIO_METRICS:
        return "ratio", 8
    if metric_name in _DAY_METRICS:
        return "day_count", 0
    if metric_name in _DAYS_METRICS:
        return "days", 2
    return None


def _evidence_reference(item: EvidenceItem) -> APEvidenceReferenceV1:
    return APEvidenceReferenceV1(
        evidence_id=item.evidence_id,
        source_type=item.source_type,
        source_step_id=item.step_id,
        source_tool_call_id=item.tool_call_id,
        checksum=item.content.checksum,
        input_evidence_ids=item.source_reference.input_evidence_ids,
    )


def _executive_summary(result: APAnalyticsResultV1) -> str:
    if result.empty_result:
        return (
            "No invoice records were available for the authorized scope; the report does not "
            "assert that no compliance issues exist."
        )
    metrics = result.metrics.root
    return (
        "The deterministic review covers "
        f"{metrics.get('invoice_count', 0)} eligible invoice(s) and identifies "
        f"{metrics.get('exception_invoice_count', 0)} unique invoice(s) with at least one "
        "frozen v1 exception. Amounts remain partitioned by currency and all findings require "
        "manual business review."
    )


def _risk_observations(run: APCalculationRun) -> tuple[APRiskObservationV1, ...]:
    metrics = run.result.metrics.root
    evidence_ids = run.evidence_ids
    count = metrics.get("exception_invoice_count")
    if count == 0:
        statement = (
            "No deterministic v1 exception record was produced; this does not establish "
            "broader AP compliance beyond the evidenced scope and exclusions."
        )
        level: Literal["INFORMATIONAL", "REVIEW_REQUIRED"] = "INFORMATIONAL"
    else:
        statement = (
            "One or more deterministic AP exception records require manual review; this report "
            "does not approve, edit, reject, or pay any invoice."
        )
        level = "REVIEW_REQUIRED"
    return (
        APRiskObservationV1(
            observation_id="ap-exception-review-boundary",
            statement=statement,
            level=level,
            evidence_ids=evidence_ids,
        ),
    )


def _recommendations(run: APCalculationRun) -> tuple[APRecommendationV1, ...]:
    return (
        APRecommendationV1(
            action_id="ap-manual-review",
            action=(
                "Review evidenced exceptions with the responsible AP or procurement owner and "
                "record any source-data correction through the authorized business process."
            ),
            evidence_ids=run.evidence_ids,
        ),
        APRecommendationV1(
            action_id="ap-policy-owner-confirmation",
            action=(
                "Confirm policy interpretation with the controlled policy owner when an "
                "exception requires a business decision."
            ),
            evidence_ids=run.evidence_ids,
        ),
    )


def _limitations(
    runs: tuple[APCalculationRun, ...],
    *,
    source_record_count: int,
    published_detail_count: int,
    empty_result: bool,
) -> tuple[APLimitationV1, ...]:
    values = [
        APLimitationV1(
            code="READ_ONLY_ANALYSIS",
            statement=(
                "The report is read-only and cannot approve, edit, cancel, pay, or publish a "
                "business transaction."
            ),
        ),
        APLimitationV1(
            code="V1_SCOPE_BOUNDARY",
            statement=(
                "The analysis excludes fuzzy duplicates, partial or multiple payments, credit "
                "notes, line matching, tax matching, bank data, and currency conversion."
            ),
        ),
    ]
    exclusion_count = sum(run.result.exclusion_count for run in runs)
    if exclusion_count:
        values.append(
            APLimitationV1(
                code="REASON_CODED_EXCLUSIONS",
                statement=(
                    f"The Calculation Evidence records {exclusion_count} reason-coded exclusion "
                    "occurrence(s); exclusions are not compliance conclusions."
                ),
            )
        )
    if source_record_count > published_detail_count:
        values.append(
            APLimitationV1(
                code="MANAGEMENT_DETAIL_LIMIT",
                statement=(
                    f"The management detail includes {published_detail_count} of "
                    f"{source_record_count} exception record(s); complete counts and lineage "
                    "remain in Calculation Evidence."
                ),
            )
        )
    if empty_result:
        values.append(
            APLimitationV1(
                code="NO_INVOICE_RECORDS",
                statement=(
                    "No invoice records were available; the empty result must not be interpreted "
                    "as evidence that no AP compliance issue exists."
                ),
            )
        )
    return tuple(values)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


__all__ = ["AccountsPayableReportComposer"]
