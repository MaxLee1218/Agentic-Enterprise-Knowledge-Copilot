"""Pure deterministic operations for Accounts Payable analytics v1."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Final, Literal, cast

from pydantic import JsonValue

from copilot.contracts import (
    APExceptionType,
    APPolicyRuleKind,
    APPolicyRuleV1,
    JsonObject,
    MaterialEarlyDaysRuleV1,
    MaterialityAmountRuleV1,
    OverpaymentToleranceRuleV1,
    PORequiredAmountRuleV1,
    POVarianceToleranceRuleV1,
)
from copilot.contracts.base import JsonMapping
from copilot.tools.analytics.ap_schemas import (
    AP_ANALYTICS_ENGINE_VERSION,
    AP_ANALYTICS_OPERATION_VERSION,
    INVOICE_NUMBER_NORMALIZATION_VERSION,
    APAnalyticsOperation,
    APAnalyticsResultV1,
    APDetectionRequestV1,
    APDuplicateGroupV1,
    APDuplicateInvoiceRowV1,
    APExceptionRecordV1,
    APExceptionStatus,
    APExclusionRecordV1,
    APInvoicePopulationRowV1,
    APInvoicePOVarianceRowV1,
    APPaymentAmountRowV1,
    APPaymentTermsRowV1,
    APSupplierExceptionRateRecordV1,
)
from copilot.tools.analytics.ap_validators import (
    amount_for_currency,
    average,
    money,
    ratio,
    rule_for,
    validate_materiality,
    validate_rule_applicability,
)
from copilot.tools.analytics.exceptions import (
    APAnalyticsDataConsistencyError,
    APAnalyticsDataIncompleteError,
    APAnalyticsScopeTooLargeError,
    APPolicyRuleUnavailableError,
)

AP_ANALYTICS_NORMALIZATION_VERSION: Final[Literal["accounts_payable_normalization.v1"]] = (
    "accounts_payable_normalization.v1"
)
_ZERO_CHECKSUM = "sha256:" + ("0" * 64)

AP_FORMULA_CATALOGUE: Mapping[APAnalyticsOperation, tuple[str, ...]] = {
    APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION: (
        "key=(tenant_id,supplier_id,normalized_invoice_number,gross_amount,currency,invoice_date)",
        "duplicate=group_size>=2; exposure=sum(noncanonical gross_amount)",
    ),
    APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION: (
        "variance_amount=gross_amount-approved_amount",
        "variance_rate=variance_amount/approved_amount",
        "exception=abs(rate)>allowed_rate OR abs(amount)>allowed_amount",
    ),
    APAnalyticsOperation.MISSING_PO_DETECTION: (
        "po_required=gross_amount>=po_required_min_amount",
        "exception=po_required AND NOT valid_approved_no_po_exception",
    ),
    APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION: (
        "days_late=max((payment_date-due_date).days,0)",
        "days_early=max((due_date-payment_date).days,0)",
        "late=days_late>0; material_early=days_early>=material_early_days",
    ),
    APAnalyticsOperation.OVERPAYMENT_DETECTION: (
        "overpayment_amount=payment_amount-gross_amount",
        "exception=overpayment_amount>overpayment_tolerance",
    ),
    APAnalyticsOperation.EXCEPTION_SUMMARY: (
        "exception_rate=unique_exception_invoice_count/eligible_invoice_count",
        "currency amounts=sum each unique invoice gross_amount once per currency",
    ),
    APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE: (
        "supplier_exception_rate=unique_supplier_exception_invoices/eligible_supplier_invoices",
    ),
}


def run_detection(
    request: APDetectionRequestV1,
    *,
    population: dict[str, APInvoicePopulationRowV1],
    dedicated: tuple[
        APDuplicateInvoiceRowV1
        | APInvoicePOVarianceRowV1
        | APPaymentTermsRowV1
        | APPaymentAmountRowV1,
        ...,
    ],
) -> APAnalyticsResultV1:
    """Dispatch one of the five strict AP detection operations."""
    operation = APAnalyticsOperation(request.operation_name)
    if operation is APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION:
        return _detect_exact_duplicates(
            request,
            population,
            cast(tuple[APDuplicateInvoiceRowV1, ...], dedicated),
        )
    if operation is APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION:
        return _detect_po_variance(
            request,
            population,
            cast(tuple[APInvoicePOVarianceRowV1, ...], dedicated),
        )
    if operation is APAnalyticsOperation.MISSING_PO_DETECTION:
        return _detect_missing_po(
            request,
            population,
            cast(tuple[APInvoicePOVarianceRowV1, ...], dedicated),
        )
    if operation is APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION:
        return _detect_payment_terms(
            request,
            population,
            cast(tuple[APPaymentTermsRowV1, ...], dedicated),
        )
    return _detect_overpayment(
        request,
        population,
        cast(tuple[APPaymentAmountRowV1, ...], dedicated),
    )


def run_exception_summary(
    *,
    request_operation: APAnalyticsOperation,
    population: dict[str, APInvoicePopulationRowV1],
    calculation_results: tuple[APAnalyticsResultV1, ...],
    manifest_checksum: str,
    rule_set_version: str,
    population_checksum: str,
) -> APAnalyticsResultV1:
    """Merge requested detection batches into unique-invoice KPIs."""
    records, exclusions = _validated_calculation_records(population, calculation_results)
    eligible = {key: row for key, row in population.items() if row.eligibility_reason == "ELIGIBLE"}
    _require_coverage(len(population), len(eligible))
    exception_keys = {record.invoice_record_key for record in records}
    invoice_amounts = _amounts_by_currency(eligible.values())
    exception_amounts = _amounts_by_currency(eligible[key] for key in sorted(exception_keys))
    count_by_type = Counter(record.exception_type.value for record in records)
    exclusion_count_by_reason = dict(
        sorted(Counter(item.reason_code for item in exclusions).items())
    )
    exception_rate = ratio(len(exception_keys), len(eligible))
    metrics = JsonObject(
        {
            "invoice_count": len(eligible),
            "invoice_amount_by_currency": cast(JsonValue, invoice_amounts),
            "exception_invoice_count": len(exception_keys),
            "exception_rate": _decimal_json(exception_rate),
            "exception_invoice_amount_by_currency": cast(JsonValue, exception_amounts),
            "exception_count_by_type": {
                item.value: count_by_type.get(item.value, 0) for item in APExceptionType
            },
            "finding_count": sum(record.status is APExceptionStatus.FINDING for record in records),
            "warning_count": sum(record.status is APExceptionStatus.WARNING for record in records),
            "exclusion_count_by_reason": cast(JsonValue, exclusion_count_by_reason),
        }
    )
    warnings = _unique(warning for result in calculation_results for warning in result.warnings)
    return _result(
        operation=request_operation,
        records=records,
        exclusions=exclusions,
        metrics=metrics,
        warnings=warnings,
        input_row_count=len(population),
        eligibility_count=len(eligible),
        rule_ids=_calculation_rule_ids(calculation_results),
        rule_set_version=rule_set_version,
        manifest_checksum=manifest_checksum,
        input_checksums=(
            population_checksum,
            *[item.output_checksum for item in calculation_results],
        ),
    )


def run_supplier_exception_rate(
    *,
    population: dict[str, APInvoicePopulationRowV1],
    calculation_results: tuple[APAnalyticsResultV1, ...],
    manifest_checksum: str,
    rule_set_version: str,
    population_checksum: str,
) -> APAnalyticsResultV1:
    """Calculate per-supplier exception review rates without cross-currency totals."""
    records, exclusions = _validated_calculation_records(population, calculation_results)
    all_suppliers = sorted({row.supplier_id for row in population.values()})
    if len(all_suppliers) > 100:
        raise APAnalyticsScopeTooLargeError("AP supplier count exceeds 100")
    eligible_by_supplier: dict[str, dict[str, APInvoicePopulationRowV1]] = defaultdict(dict)
    for key, row in population.items():
        if row.eligibility_reason == "ELIGIBLE":
            eligible_by_supplier[row.supplier_id][key] = row
    if population and not any(eligible_by_supplier.values()):
        raise APAnalyticsDataIncompleteError()
    exception_keys_by_supplier: dict[str, set[str]] = defaultdict(set)
    for record in records:
        exception_keys_by_supplier[record.supplier_id].add(record.invoice_record_key)
    exclusions_by_supplier = Counter(item.supplier_id for item in exclusions)
    supplier_rates: list[APSupplierExceptionRateRecordV1] = []
    warnings: list[str] = []
    for supplier_id in all_suppliers:
        eligible = eligible_by_supplier[supplier_id]
        exception_keys = exception_keys_by_supplier[supplier_id]
        supplier_rate = ratio(len(exception_keys), len(eligible))
        if supplier_rate is None:
            warnings.append(
                f"Supplier exception rate is undefined for zero eligible invoices: {supplier_id}"
            )
        supplier_rates.append(
            APSupplierExceptionRateRecordV1(
                supplier_id=supplier_id,
                exception_invoice_count=len(exception_keys),
                eligible_invoice_count=len(eligible),
                supplier_exception_rate=supplier_rate,
                invoice_amount_by_currency=JsonObject(_amounts_by_currency(eligible.values())),
                exception_amount_by_currency=JsonObject(
                    _amounts_by_currency(eligible[key] for key in sorted(exception_keys))
                ),
                exclusion_count=exclusions_by_supplier[supplier_id],
            )
        )
    supplier_rates.sort(
        key=lambda item: (
            item.supplier_exception_rate is None,
            -(item.supplier_exception_rate or Decimal("0")),
            -item.exception_invoice_count,
            item.supplier_id,
        )
    )
    return _result(
        operation=APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
        supplier_rates=tuple(supplier_rates),
        exclusions=exclusions,
        metrics=JsonObject({"supplier_count": len(supplier_rates)}),
        warnings=_unique(warnings),
        input_row_count=len(population),
        eligibility_count=sum(len(rows) for rows in eligible_by_supplier.values()),
        rule_ids=_calculation_rule_ids(calculation_results),
        rule_set_version=rule_set_version,
        manifest_checksum=manifest_checksum,
        input_checksums=(
            population_checksum,
            *[item.output_checksum for item in calculation_results],
        ),
    )


def _detect_exact_duplicates(
    request: APDetectionRequestV1,
    population: dict[str, APInvoicePopulationRowV1],
    rows: tuple[APDuplicateInvoiceRowV1, ...],
) -> APAnalyticsResultV1:
    manifest = request.rule_snapshot.rule_manifest
    materiality_rule, organization, requested, effective = validate_materiality(
        manifest,
        request.parameters,
        required_currencies={
            row.currency
            for row in rows
            if row.currency is not None
            and population[row.invoice_record_key].eligibility_reason == "ELIGIBLE"
        },
    )
    _validate_rule_for_population(materiality_rule, population.values())
    groups: dict[tuple[object, ...], list[APDuplicateInvoiceRowV1]] = defaultdict(list)
    exclusions: list[APExclusionRecordV1] = []
    eligibility_count = 0
    for row in rows:
        common = population[row.invoice_record_key]
        if common.eligibility_reason != "ELIGIBLE":
            exclusions.append(_exclusion(common, common.eligibility_reason))
            continue
        if (
            row.normalized_invoice_number is None
            or row.invoice_date is None
            or row.gross_amount is None
            or row.gross_amount <= 0
            or row.currency is None
        ):
            exclusions.append(_exclusion(common, "DUPLICATE_KEY_INCOMPLETE"))
            continue
        eligibility_count += 1
        groups[
            (
                row.tenant_id,
                row.supplier_id,
                row.normalized_invoice_number,
                money(row.gross_amount),
                row.currency,
                row.invoice_date,
            )
        ].append(row)
    _require_coverage(len(rows), eligibility_count)
    evidence_ids = _database_evidence_ids(request)
    duplicate_groups: list[APDuplicateGroupV1] = []
    records: list[APExceptionRecordV1] = []
    exposure_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    for key in sorted(groups, key=_sortable_group_key):
        members = sorted(groups[key], key=lambda item: item.invoice_record_key)
        if len(members) < 2:
            continue
        canonical = members[0]
        if (
            canonical.gross_amount is None
            or canonical.currency is None
            or canonical.invoice_date is None
            or canonical.normalized_invoice_number is None
        ):
            raise APAnalyticsDataConsistencyError(
                "Eligible AP duplicate group contains an incomplete canonical member"
            )
        member_keys = tuple(item.invoice_record_key for item in members)
        duplicate_groups.append(
            APDuplicateGroupV1(
                canonical_invoice_record_key=canonical.invoice_record_key,
                member_invoice_record_keys=member_keys,
                supplier_id=canonical.supplier_id,
                normalized_invoice_number=canonical.normalized_invoice_number,
                invoice_date=canonical.invoice_date,
                gross_amount=money(canonical.gross_amount),
                currency=canonical.currency,
            )
        )
        for row in members[1:]:
            common = population[row.invoice_record_key]
            exposure = money(common.gross_amount)
            exposure_by_currency[common.currency] += exposure
            records.append(
                _exception(
                    exception_type=APExceptionType.EXACT_DUPLICATE_INVOICE,
                    common=common,
                    exposure=exposure,
                    organization=organization,
                    requested=requested,
                    effective=effective,
                    primary_rule=materiality_rule,
                    materiality_rule=materiality_rule,
                    database_evidence_ids=evidence_ids,
                    observed={
                        "canonical_invoice_record_key": canonical.invoice_record_key,
                        "member_invoice_record_keys": list(member_keys),
                        "gross_amount": _decimal_json(exposure),
                    },
                    thresholds={},
                    reason_code="EXACT_DUPLICATE_KEY_MATCH",
                )
            )
    records.sort(key=lambda item: (item.supplier_id, item.currency, item.invoice_record_key))
    return _result(
        operation=APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        records=tuple(records),
        duplicate_groups=tuple(duplicate_groups),
        exclusions=tuple(exclusions),
        metrics=JsonObject(
            {
                "duplicate_group_count": len(duplicate_groups),
                "duplicate_invoice_count": len(records),
                "duplicate_exposure_amount_by_currency": _decimal_map(exposure_by_currency),
            }
        ),
        warnings=(),
        input_row_count=len(rows),
        eligibility_count=eligibility_count,
        rule_ids=(materiality_rule.rule_id,),
        rule_set_version=manifest.rule_set_version,
        manifest_checksum=manifest.manifest_checksum,
        input_checksums=_dataset_checksums(request),
        normalization_version=INVOICE_NUMBER_NORMALIZATION_VERSION,
    )


def _detect_po_variance(
    request: APDetectionRequestV1,
    population: dict[str, APInvoicePopulationRowV1],
    rows: tuple[APInvoicePOVarianceRowV1, ...],
) -> APAnalyticsResultV1:
    manifest = request.rule_snapshot.rule_manifest
    materiality_rule, organization, requested, effective = validate_materiality(
        manifest,
        request.parameters,
        required_currencies={
            row.invoice_currency
            for row in rows
            if row.po_record_key is not None
            and population[row.invoice_record_key].eligibility_reason == "ELIGIBLE"
        },
    )
    _validate_rule_for_population(materiality_rule, population.values())
    variance_rule = rule_for(manifest, APPolicyRuleKind.PO_VARIANCE_TOLERANCE)
    if not isinstance(variance_rule, POVarianceToleranceRuleV1):
        raise APPolicyRuleUnavailableError("AP PO variance rule has the wrong schema")
    evidence_ids = _database_evidence_ids(request)
    records: list[APExceptionRecordV1] = []
    exclusions: list[APExclusionRecordV1] = []
    exposure_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    eligibility_count = 0
    for row in rows:
        common = population[row.invoice_record_key]
        if common.eligibility_reason != "ELIGIBLE":
            exclusions.append(_exclusion(common, common.eligibility_reason))
            continue
        if row.po_record_key is None:
            continue
        rule_for(
            manifest,
            APPolicyRuleKind.PO_VARIANCE_TOLERANCE,
            legal_entity_id=row.legal_entity_id,
            invoice_type=row.invoice_type,
            effective_on=common.invoice_date,
        )
        if row.po_matching_basis != "SINGLE_INVOICE":
            exclusions.append(_exclusion(common, "MULTI_INVOICE_MATCHING_UNSUPPORTED"))
            continue
        if row.po_status not in {"APPROVED", "CLOSED"}:
            exclusions.append(_exclusion(common, "PO_STATUS_INELIGIBLE"))
            continue
        if row.po_approved_amount is None or row.po_approved_amount == 0:
            exclusions.append(_exclusion(common, "PO_AMOUNT_ZERO"))
            continue
        if row.po_approved_amount < 0:
            exclusions.append(_exclusion(common, "PO_AMOUNT_INVALID"))
            continue
        if row.po_currency != row.invoice_currency:
            exclusions.append(_exclusion(common, "AP_CURRENCY_MISMATCH_EXCLUDED"))
            continue
        eligibility_count += 1
        variance_amount = money(row.invoice_gross_amount - row.po_approved_amount)
        variance_rate = cast(Decimal, ratio(variance_amount, row.po_approved_amount))
        absolute_amount = abs(variance_amount)
        absolute_rate = abs(variance_rate)
        allowed_amount = amount_for_currency(
            variance_rule.allowed_variance_amounts,
            row.invoice_currency,
            "amount",
        )
        if not (
            absolute_rate > variance_rule.allowed_variance_rate or absolute_amount > allowed_amount
        ):
            continue
        exposure_by_currency[row.invoice_currency] += absolute_amount
        records.append(
            _exception(
                exception_type=APExceptionType.PO_AMOUNT_VARIANCE,
                common=common,
                exposure=absolute_amount,
                organization=organization,
                requested=requested,
                effective=effective,
                primary_rule=variance_rule,
                materiality_rule=materiality_rule,
                database_evidence_ids=evidence_ids,
                observed={
                    "gross_amount": _decimal_json(row.invoice_gross_amount),
                    "approved_amount": _decimal_json(row.po_approved_amount),
                    "variance_amount": _decimal_json(variance_amount),
                    "variance_rate": _decimal_json(variance_rate),
                    "absolute_variance_amount": _decimal_json(absolute_amount),
                    "absolute_variance_rate": _decimal_json(absolute_rate),
                },
                thresholds={
                    "allowed_variance_rate": _decimal_json(variance_rule.allowed_variance_rate),
                    "allowed_variance_amount": _decimal_json(allowed_amount),
                },
                reason_code="PO_VARIANCE_TOLERANCE_EXCEEDED",
            )
        )
    _require_coverage(len(rows), eligibility_count)
    records.sort(
        key=lambda item: (
            item.supplier_id,
            item.currency,
            -Decimal(cast(str, item.observed_values.root["absolute_variance_amount"])),
            item.invoice_record_key,
        )
    )
    return _result(
        operation=APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION,
        records=tuple(records),
        exclusions=tuple(exclusions),
        metrics=JsonObject(
            {
                "po_variance_exception_count": len(records),
                "absolute_variance_amount_by_currency": _decimal_map(exposure_by_currency),
            }
        ),
        warnings=(),
        input_row_count=len(rows),
        eligibility_count=eligibility_count,
        rule_ids=(variance_rule.rule_id, materiality_rule.rule_id),
        rule_set_version=manifest.rule_set_version,
        manifest_checksum=manifest.manifest_checksum,
        input_checksums=_dataset_checksums(request),
    )


def _detect_missing_po(
    request: APDetectionRequestV1,
    population: dict[str, APInvoicePopulationRowV1],
    rows: tuple[APInvoicePOVarianceRowV1, ...],
) -> APAnalyticsResultV1:
    manifest = request.rule_snapshot.rule_manifest
    materiality_rule, organization, requested, effective = validate_materiality(
        manifest,
        request.parameters,
        required_currencies={
            row.invoice_currency
            for row in rows
            if population[row.invoice_record_key].eligibility_reason == "ELIGIBLE"
        },
    )
    _validate_rule_for_population(materiality_rule, population.values())
    po_required_rule = rule_for(manifest, APPolicyRuleKind.PO_REQUIRED_AMOUNT)
    if not isinstance(po_required_rule, PORequiredAmountRuleV1):
        raise APPolicyRuleUnavailableError("AP required-PO rule has the wrong schema")
    evidence_ids = _database_evidence_ids(request)
    records: list[APExceptionRecordV1] = []
    exclusions: list[APExclusionRecordV1] = []
    exposure_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    eligibility_count = 0
    for row in rows:
        common = population[row.invoice_record_key]
        if common.eligibility_reason != "ELIGIBLE":
            exclusions.append(_exclusion(common, common.eligibility_reason))
            continue
        rule_for(
            manifest,
            APPolicyRuleKind.PO_REQUIRED_AMOUNT,
            legal_entity_id=row.legal_entity_id,
            invoice_type=row.invoice_type,
            effective_on=common.invoice_date,
        )
        if row.no_po_exception_approved and not row.no_po_exception_ref:
            exclusions.append(_exclusion(common, "INVALID_NO_PO_EXCEPTION"))
            continue
        eligibility_count += 1
        if row.po_record_key is not None:
            continue
        minimum = amount_for_currency(
            po_required_rule.minimum_amounts,
            row.invoice_currency,
            "amount",
        )
        valid_exception = bool(row.no_po_exception_approved and row.no_po_exception_ref)
        if row.invoice_gross_amount < minimum or valid_exception:
            continue
        exposure = money(row.invoice_gross_amount)
        exposure_by_currency[row.invoice_currency] += exposure
        records.append(
            _exception(
                exception_type=APExceptionType.MISSING_REQUIRED_PO,
                common=common,
                exposure=exposure,
                organization=organization,
                requested=requested,
                effective=effective,
                primary_rule=po_required_rule,
                materiality_rule=materiality_rule,
                database_evidence_ids=evidence_ids,
                observed={
                    "gross_amount": _decimal_json(row.invoice_gross_amount),
                    "purchase_order_present": False,
                    "approved_no_po_exception": valid_exception,
                },
                thresholds={"po_required_min_amount": _decimal_json(minimum)},
                reason_code="REQUIRED_PO_MISSING",
            )
        )
    _require_coverage(len(rows), eligibility_count)
    records.sort(key=lambda item: (item.supplier_id, item.currency, item.invoice_record_key))
    return _result(
        operation=APAnalyticsOperation.MISSING_PO_DETECTION,
        records=tuple(records),
        exclusions=tuple(exclusions),
        metrics=JsonObject(
            {
                "missing_required_po_count": len(records),
                "missing_po_exposure_amount_by_currency": _decimal_map(exposure_by_currency),
            }
        ),
        warnings=(),
        input_row_count=len(rows),
        eligibility_count=eligibility_count,
        rule_ids=(po_required_rule.rule_id, materiality_rule.rule_id),
        rule_set_version=manifest.rule_set_version,
        manifest_checksum=manifest.manifest_checksum,
        input_checksums=_dataset_checksums(request),
    )


def _detect_payment_terms(
    request: APDetectionRequestV1,
    population: dict[str, APInvoicePopulationRowV1],
    rows: tuple[APPaymentTermsRowV1, ...],
) -> APAnalyticsResultV1:
    manifest = request.rule_snapshot.rule_manifest
    early_rule = rule_for(manifest, APPolicyRuleKind.MATERIAL_EARLY_DAYS)
    if not isinstance(early_rule, MaterialEarlyDaysRuleV1):
        raise APPolicyRuleUnavailableError("AP material-early rule has the wrong schema")
    evidence_ids = _database_evidence_ids(request)
    records: list[APExceptionRecordV1] = []
    exclusions: list[APExclusionRecordV1] = []
    warnings: list[str] = []
    late_days: list[int] = []
    eligibility_count = 0
    for row in rows:
        common = population[row.invoice_record_key]
        if common.eligibility_reason != "ELIGIBLE":
            exclusions.append(_exclusion(common, common.eligibility_reason))
            continue
        settlement_reason = _settlement_exclusion(row)
        if settlement_reason is not None:
            exclusions.append(_exclusion(common, settlement_reason))
            continue
        if row.payment_date is None or row.payment_currency is None:
            raise APAnalyticsDataConsistencyError("Eligible AP payment timing row is incomplete")
        if row.payment_currency != row.invoice_currency:
            exclusions.append(_exclusion(common, "AP_CURRENCY_MISMATCH_EXCLUDED"))
            continue
        if row.due_date < row.invoice_date:
            exclusions.append(_exclusion(common, "AP_PAYMENT_TERM_INVALID"))
            continue
        if row.due_date != row.invoice_date + timedelta(days=row.payment_terms_days):
            warnings.append(f"PAYMENT_TERM_DAYS_INCONSISTENT:{row.invoice_record_key}")
        rule_for(
            manifest,
            APPolicyRuleKind.MATERIAL_EARLY_DAYS,
            legal_entity_id=row.legal_entity_id,
            invoice_type=row.invoice_type,
            effective_on=row.invoice_date,
        )
        eligibility_count += 1
        delta_days = (row.payment_date - row.due_date).days
        days_late = max(delta_days, 0)
        days_early = max(-delta_days, 0)
        if days_late > 0:
            late_days.append(days_late)
            records.append(
                _timing_exception(
                    exception_type=APExceptionType.LATE_PAYMENT,
                    common=common,
                    rule=early_rule,
                    database_evidence_ids=evidence_ids,
                    days_late=days_late,
                    days_early=0,
                    threshold_days=0,
                    reason_code="PAYMENT_AFTER_DUE_DATE",
                )
            )
        if days_early >= early_rule.days:
            records.append(
                _timing_exception(
                    exception_type=APExceptionType.MATERIAL_EARLY_PAYMENT,
                    common=common,
                    rule=early_rule,
                    database_evidence_ids=evidence_ids,
                    days_late=0,
                    days_early=days_early,
                    threshold_days=early_rule.days,
                    reason_code="PAYMENT_EARLY_DAYS_THRESHOLD_MET",
                )
            )
    _require_coverage(len(rows), eligibility_count)
    records.sort(
        key=lambda item: (item.exception_type.value, item.supplier_id, item.invoice_record_key)
    )
    average_days_late = average(late_days)
    return _result(
        operation=APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION,
        records=tuple(records),
        exclusions=tuple(exclusions),
        metrics=JsonObject(
            {
                "late_payment_count": sum(
                    item.exception_type is APExceptionType.LATE_PAYMENT for item in records
                ),
                "material_early_payment_count": sum(
                    item.exception_type is APExceptionType.MATERIAL_EARLY_PAYMENT
                    for item in records
                ),
                "average_days_late": _decimal_json(average_days_late),
            }
        ),
        warnings=_unique(warnings),
        input_row_count=len(rows),
        eligibility_count=eligibility_count,
        rule_ids=(early_rule.rule_id,),
        rule_set_version=manifest.rule_set_version,
        manifest_checksum=manifest.manifest_checksum,
        input_checksums=_dataset_checksums(request),
    )


def _detect_overpayment(
    request: APDetectionRequestV1,
    population: dict[str, APInvoicePopulationRowV1],
    rows: tuple[APPaymentAmountRowV1, ...],
) -> APAnalyticsResultV1:
    manifest = request.rule_snapshot.rule_manifest
    materiality_rule, organization, requested, effective = validate_materiality(
        manifest,
        request.parameters,
        required_currencies={
            row.invoice_currency
            for row in rows
            if population[row.invoice_record_key].eligibility_reason == "ELIGIBLE"
        },
    )
    _validate_rule_for_population(materiality_rule, population.values())
    overpayment_rule = rule_for(manifest, APPolicyRuleKind.OVERPAYMENT_TOLERANCE)
    if not isinstance(overpayment_rule, OverpaymentToleranceRuleV1):
        raise APPolicyRuleUnavailableError("AP overpayment rule has the wrong schema")
    evidence_ids = _database_evidence_ids(request)
    records: list[APExceptionRecordV1] = []
    exclusions: list[APExclusionRecordV1] = []
    exposure_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    eligibility_count = 0
    for row in rows:
        common = population[row.invoice_record_key]
        if common.eligibility_reason != "ELIGIBLE":
            exclusions.append(_exclusion(common, common.eligibility_reason))
            continue
        settlement_reason = _settlement_exclusion(row)
        if settlement_reason is not None:
            exclusions.append(_exclusion(common, settlement_reason))
            continue
        if row.payment_amount is None or row.payment_currency is None:
            raise APAnalyticsDataConsistencyError("Eligible AP payment amount row is incomplete")
        if row.payment_currency != row.invoice_currency:
            exclusions.append(_exclusion(common, "AP_CURRENCY_MISMATCH_EXCLUDED"))
            continue
        rule_for(
            manifest,
            APPolicyRuleKind.OVERPAYMENT_TOLERANCE,
            legal_entity_id=row.legal_entity_id,
            invoice_type=row.invoice_type,
            effective_on=common.invoice_date,
        )
        eligibility_count += 1
        tolerance = amount_for_currency(
            overpayment_rule.tolerances,
            row.invoice_currency,
            "amount",
        )
        overpayment_amount = money(row.payment_amount - row.invoice_gross_amount)
        if overpayment_amount <= tolerance:
            continue
        exposure_by_currency[row.invoice_currency] += overpayment_amount
        records.append(
            _exception(
                exception_type=APExceptionType.OVERPAYMENT,
                common=common,
                exposure=overpayment_amount,
                organization=organization,
                requested=requested,
                effective=effective,
                primary_rule=overpayment_rule,
                materiality_rule=materiality_rule,
                database_evidence_ids=evidence_ids,
                observed={
                    "gross_amount": _decimal_json(row.invoice_gross_amount),
                    "payment_amount": _decimal_json(row.payment_amount),
                    "overpayment_amount": _decimal_json(overpayment_amount),
                },
                thresholds={"overpayment_tolerance": _decimal_json(tolerance)},
                reason_code="OVERPAYMENT_TOLERANCE_EXCEEDED",
            )
        )
    _require_coverage(len(rows), eligibility_count)
    records.sort(key=lambda item: (item.supplier_id, item.currency, item.invoice_record_key))
    return _result(
        operation=APAnalyticsOperation.OVERPAYMENT_DETECTION,
        records=tuple(records),
        exclusions=tuple(exclusions),
        metrics=JsonObject(
            {
                "overpayment_count": len(records),
                "overpayment_amount_by_currency": _decimal_map(exposure_by_currency),
            }
        ),
        warnings=(),
        input_row_count=len(rows),
        eligibility_count=eligibility_count,
        rule_ids=(overpayment_rule.rule_id, materiality_rule.rule_id),
        rule_set_version=manifest.rule_set_version,
        manifest_checksum=manifest.manifest_checksum,
        input_checksums=_dataset_checksums(request),
    )


def _exception(
    *,
    exception_type: APExceptionType,
    common: APInvoicePopulationRowV1,
    exposure: Decimal,
    organization: dict[str, Decimal],
    requested: dict[str, Decimal],
    effective: dict[str, Decimal],
    primary_rule: APPolicyRuleV1,
    materiality_rule: MaterialityAmountRuleV1,
    database_evidence_ids: tuple[str, ...],
    observed: JsonMapping,
    thresholds: JsonMapping,
    reason_code: str,
) -> APExceptionRecordV1:
    currency = common.currency
    validate_rule_applicability(
        primary_rule,
        legal_entity_id=common.legal_entity_id,
        invoice_type=common.invoice_type,
        effective_on=common.invoice_date,
    )
    validate_rule_applicability(
        materiality_rule,
        legal_entity_id=common.legal_entity_id,
        invoice_type=common.invoice_type,
        effective_on=common.invoice_date,
    )
    effective_amount = effective[currency]
    threshold_values: JsonMapping = {
        **thresholds,
        "organization_materiality": _decimal_json(organization[currency]),
        "requested_materiality": _decimal_json(requested.get(currency)),
        "effective_materiality": _decimal_json(effective_amount),
    }
    return APExceptionRecordV1(
        exception_id=_exception_id(exception_type, common.invoice_record_key),
        exception_type=exception_type,
        invoice_record_key=common.invoice_record_key,
        supplier_id=common.supplier_id,
        legal_entity_id=common.legal_entity_id,
        business_unit_id=common.business_unit_id,
        currency=currency,
        gross_amount=money(common.gross_amount),
        observed_values=JsonObject(observed),
        threshold_values=JsonObject(threshold_values),
        status=(
            APExceptionStatus.FINDING if exposure >= effective_amount else APExceptionStatus.WARNING
        ),
        rule_id=primary_rule.rule_id,
        rule_version=primary_rule.rule_version,
        rule_set_version="ap_rules.2026.1",
        database_evidence_ids=database_evidence_ids,
        reason_codes=(reason_code,),
    )


def _timing_exception(
    *,
    exception_type: APExceptionType,
    common: APInvoicePopulationRowV1,
    rule: MaterialEarlyDaysRuleV1,
    database_evidence_ids: tuple[str, ...],
    days_late: int,
    days_early: int,
    threshold_days: int,
    reason_code: str,
) -> APExceptionRecordV1:
    return APExceptionRecordV1(
        exception_id=_exception_id(exception_type, common.invoice_record_key),
        exception_type=exception_type,
        invoice_record_key=common.invoice_record_key,
        supplier_id=common.supplier_id,
        legal_entity_id=common.legal_entity_id,
        business_unit_id=common.business_unit_id,
        currency=common.currency,
        gross_amount=money(common.gross_amount),
        observed_values=JsonObject({"days_late": days_late, "days_early": days_early}),
        threshold_values=JsonObject({"material_early_days": threshold_days}),
        status=APExceptionStatus.FINDING,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        rule_set_version="ap_rules.2026.1",
        database_evidence_ids=database_evidence_ids,
        reason_codes=(reason_code,),
    )


def _settlement_exclusion(row: APPaymentTermsRowV1 | APPaymentAmountRowV1) -> str | None:
    if row.payment_count == 0 or row.settled_payment_count == 0:
        return "UNPAID_INVOICE"
    if row.payment_count != 1 or row.settled_payment_count != 1:
        return "MULTIPLE_PAYMENT_EXCLUSION"
    if row.payment_status != "SETTLED":
        return "UNSUPPORTED_SETTLEMENT_STATUS"
    return None


def _exclusion(common: APInvoicePopulationRowV1, reason_code: str) -> APExclusionRecordV1:
    return APExclusionRecordV1(
        invoice_record_key=common.invoice_record_key,
        supplier_id=common.supplier_id,
        legal_entity_id=common.legal_entity_id,
        business_unit_id=common.business_unit_id,
        currency=common.currency,
        reason_code=reason_code,
    )


def _validated_calculation_records(
    population: dict[str, APInvoicePopulationRowV1],
    calculation_results: tuple[APAnalyticsResultV1, ...],
) -> tuple[tuple[APExceptionRecordV1, ...], tuple[APExclusionRecordV1, ...]]:
    records_by_id: dict[str, APExceptionRecordV1] = {}
    exclusions_by_identity: dict[tuple[str, str], APExclusionRecordV1] = {}
    for result in calculation_results:
        for record in result.records:
            common = population.get(record.invoice_record_key)
            if common is None or common.eligibility_reason != "ELIGIBLE":
                raise APAnalyticsDataConsistencyError(
                    "AP exception record is outside the eligible common population"
                )
            if (
                record.supplier_id != common.supplier_id
                or record.legal_entity_id != common.legal_entity_id
                or record.business_unit_id != common.business_unit_id
                or record.currency != common.currency
                or money(record.gross_amount) != money(common.gross_amount)
            ):
                raise APAnalyticsDataConsistencyError(
                    "AP exception record dimensions or amount differ from population"
                )
            if record.exception_id in records_by_id:
                raise APAnalyticsDataConsistencyError("Duplicate AP exception result was supplied")
            records_by_id[record.exception_id] = record
        for exclusion in result.exclusions:
            common = population.get(exclusion.invoice_record_key)
            if common is None or exclusion.supplier_id != common.supplier_id:
                raise APAnalyticsDataConsistencyError(
                    "AP exclusion record is outside the common population"
                )
            exclusions_by_identity[(exclusion.reason_code, exclusion.invoice_record_key)] = (
                exclusion
            )
    records = tuple(
        sorted(
            records_by_id.values(),
            key=lambda item: (
                item.exception_type.value,
                item.supplier_id,
                item.invoice_record_key,
            ),
        )
    )
    if len(records) > 5_000:
        raise APAnalyticsScopeTooLargeError("AP exception records exceed 5,000")
    exclusions = tuple(exclusions_by_identity[key] for key in sorted(exclusions_by_identity))
    return records, exclusions


def _result(
    *,
    operation: APAnalyticsOperation,
    metrics: JsonObject,
    warnings: tuple[str, ...],
    input_row_count: int,
    eligibility_count: int,
    rule_ids: tuple[str, ...],
    rule_set_version: str,
    manifest_checksum: str,
    input_checksums: tuple[str, ...],
    records: tuple[APExceptionRecordV1, ...] = (),
    duplicate_groups: tuple[APDuplicateGroupV1, ...] = (),
    supplier_rates: tuple[APSupplierExceptionRateRecordV1, ...] = (),
    exclusions: tuple[APExclusionRecordV1, ...] = (),
    normalization_version: Literal[
        "accounts_payable_normalization.v1", "invoice_number_normalization.v1"
    ] = AP_ANALYTICS_NORMALIZATION_VERSION,
) -> APAnalyticsResultV1:
    if len(records) > 5_000:
        raise APAnalyticsScopeTooLargeError("AP exception records exceed 5,000")
    exclusion_counts = dict(sorted(Counter(item.reason_code for item in exclusions).items()))
    effective_warnings = (
        _unique(("EMPTY_SOURCE_POPULATION", *warnings)) if input_row_count == 0 else warnings
    )
    if rule_set_version != "ap_rules.2026.1":
        raise APAnalyticsDataConsistencyError("AP calculation rule-set version drifted")
    initial = APAnalyticsResultV1(
        operation_name=operation,
        operation_version=AP_ANALYTICS_OPERATION_VERSION,
        engine_version=AP_ANALYTICS_ENGINE_VERSION,
        records=records,
        duplicate_groups=duplicate_groups,
        supplier_rates=supplier_rates,
        exclusions=exclusions,
        metrics=metrics,
        warnings=effective_warnings,
        input_row_count=input_row_count,
        eligibility_count=eligibility_count,
        exclusion_count=len(exclusions),
        exclusion_count_by_reason=exclusion_counts,
        rule_ids=tuple(dict.fromkeys(rule_ids)),
        rule_set_version="ap_rules.2026.1",
        manifest_checksum=manifest_checksum,
        input_checksums=tuple(dict.fromkeys(input_checksums)),
        normalization_version=normalization_version,
        precision="decimal(20,4);ratio(12,8)",
        rounding_mode="ROUND_HALF_EVEN",
        empty_result=input_row_count == 0,
        output_checksum=_ZERO_CHECKSUM,
    )
    payload = initial.model_dump(mode="json", exclude={"output_checksum"})
    return initial.model_copy(update={"output_checksum": _checksum(payload)})


def _amounts_by_currency(
    rows: Iterable[APInvoicePopulationRowV1],
) -> dict[str, JsonValue]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        totals[row.currency] += money(row.gross_amount)
    return _decimal_map(totals)


def _decimal_map(values: Mapping[str, Decimal]) -> dict[str, JsonValue]:
    return {key: format(money(values[key]), "f") for key in sorted(values)}


def _decimal_json(value: Decimal | None) -> JsonValue:
    return None if value is None else format(value, "f")


def _exception_id(exception_type: APExceptionType, invoice_record_key: str) -> str:
    digest = hashlib.sha256(f"{exception_type.value}:{invoice_record_key}".encode()).hexdigest()[
        :24
    ]
    return f"APX-{digest}"


def _checksum(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _dataset_checksums(request: APDetectionRequestV1) -> tuple[str, ...]:
    return tuple(
        item.dataset_checksum
        for item in sorted(request.datasets, key=lambda item: item.template_id)
    )


def _database_evidence_ids(request: APDetectionRequestV1) -> tuple[str, ...]:
    return tuple(
        item.evidence_id for item in sorted(request.datasets, key=lambda item: item.template_id)
    )


def _calculation_rule_ids(results: tuple[APAnalyticsResultV1, ...]) -> tuple[str, ...]:
    return tuple(sorted({rule_id for result in results for rule_id in result.rule_ids}))


def _validate_rule_for_population(
    rule: APPolicyRuleV1,
    rows: Iterable[APInvoicePopulationRowV1],
) -> None:
    for row in rows:
        if row.eligibility_reason != "ELIGIBLE":
            continue
        validate_rule_applicability(
            rule,
            legal_entity_id=row.legal_entity_id,
            invoice_type=row.invoice_type,
            effective_on=row.invoice_date,
        )


def _require_coverage(input_row_count: int, eligibility_count: int) -> None:
    if input_row_count > 0 and eligibility_count == 0:
        raise APAnalyticsDataIncompleteError()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _sortable_group_key(value: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(str(item) for item in value)


__all__ = [
    "AP_ANALYTICS_NORMALIZATION_VERSION",
    "AP_FORMULA_CATALOGUE",
    "run_detection",
    "run_exception_summary",
    "run_supplier_exception_rate",
]
