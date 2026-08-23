"""Deterministic validation for AP rows, relationships, rules, and materiality."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TypeVar, cast

from pydantic import ValidationError

from copilot.contracts import (
    APPolicyRuleKind,
    APPolicyRuleManifestV1,
    APPolicyRuleV1,
    MaterialityAmountRuleV1,
)
from copilot.tools.analytics.ap_schemas import (
    APDatabaseTemplate,
    APDatasetReferenceV1,
    APDetectionParametersV1,
    APDuplicateInvoiceRowV1,
    APInvoicePopulationRowV1,
    APInvoicePOVarianceRowV1,
    APPaymentAmountRowV1,
    APPaymentTermsRowV1,
    APSourceRowV1,
)
from copilot.tools.analytics.exceptions import (
    APAnalyticsDataConsistencyError,
    APAnalyticsInputDeniedError,
    APAnalyticsInputError,
    APPolicyRuleUnavailableError,
    APPolicyThresholdRelaxationError,
)

MONEY_QUANTUM = Decimal("0.0001")
RATIO_QUANTUM = Decimal("0.00000001")
AVERAGE_QUANTUM = Decimal("0.01")

_RowT = TypeVar("_RowT", bound=APSourceRowV1)


def money(value: Decimal) -> Decimal:
    """Return one finite four-place monetary value using the frozen rounding mode."""
    normalized = value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    if not normalized.is_finite():
        raise APAnalyticsInputError("AP monetary input must be finite")
    return normalized


def ratio(numerator: int | Decimal, denominator: int | Decimal) -> Decimal | None:
    """Return one eight-place Decimal ratio or null for a zero denominator."""
    denominator_value = Decimal(denominator)
    if denominator_value == 0:
        return None
    value = (Decimal(numerator) / denominator_value).quantize(
        RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
    )
    if not value.is_finite():
        raise APAnalyticsInputError("AP ratio input must be finite")
    return value


def average(values: Iterable[int]) -> Decimal | None:
    """Return a two-place arithmetic mean or null for an empty collection."""
    selected = tuple(values)
    if not selected:
        return None
    return (Decimal(sum(selected)) / Decimal(len(selected))).quantize(
        AVERAGE_QUANTUM, rounding=ROUND_HALF_EVEN
    )


def parse_dataset_rows(dataset: APDatasetReferenceV1) -> tuple[APSourceRowV1, ...]:
    """Parse one exact template row shape without coercing it into another profile."""
    try:
        if dataset.template_id is APDatabaseTemplate.INVOICE_POPULATION:
            rows: tuple[APSourceRowV1, ...] = tuple(
                APInvoicePopulationRowV1.model_validate(item.root) for item in dataset.rows
            )
        elif dataset.template_id is APDatabaseTemplate.DUPLICATE_CANDIDATES:
            rows = tuple(APDuplicateInvoiceRowV1.model_validate(item.root) for item in dataset.rows)
        elif dataset.template_id is APDatabaseTemplate.INVOICE_PO_VARIANCE:
            rows = tuple(
                APInvoicePOVarianceRowV1.model_validate(item.root) for item in dataset.rows
            )
        elif dataset.template_id is APDatabaseTemplate.PAYMENT_TERMS:
            rows = tuple(APPaymentTermsRowV1.model_validate(item.root) for item in dataset.rows)
        else:
            rows = tuple(APPaymentAmountRowV1.model_validate(item.root) for item in dataset.rows)
    except ValidationError as exc:
        raise APAnalyticsInputError(f"Rows do not match {dataset.template_id.value}") from exc
    return rows


def validate_population(
    rows: tuple[APInvoicePopulationRowV1, ...],
    *,
    tenant_id: str,
) -> dict[str, APInvoicePopulationRowV1]:
    """Validate common cohort identity, stored arithmetic, and declared eligibility."""
    by_key: dict[str, APInvoicePopulationRowV1] = {}
    for row in rows:
        if row.invoice_record_key in by_key:
            raise APAnalyticsDataConsistencyError("AP population record keys must be unique")
        if row.tenant_id != tenant_id:
            raise APAnalyticsInputDeniedError("AP population contains a different tenant")
        if money(row.net_amount + row.tax_amount) != money(row.gross_amount):
            raise APAnalyticsDataConsistencyError(
                "AP invoice gross amount does not equal net plus tax"
            )
        expected_eligible = (
            row.invoice_type == "STANDARD"
            and row.invoice_status in {"POSTED", "PAID"}
            and row.gross_amount > 0
        )
        if (row.eligibility_reason == "ELIGIBLE") != expected_eligible:
            raise APAnalyticsDataConsistencyError(
                "AP population eligibility reason does not match its source facts"
            )
        if row.settled_payment_count > row.payment_count:
            raise APAnalyticsDataConsistencyError(
                "AP settled payment count exceeds non-void payment count"
            )
        by_key[row.invoice_record_key] = row
    return by_key


def validate_dedicated_rows(
    rows: tuple[_RowT, ...],
    population: dict[str, APInvoicePopulationRowV1],
    *,
    tenant_id: str,
) -> dict[str, _RowT]:
    """Prove dedicated rows are the same scoped cohort and parent relationships are sound."""
    by_key: dict[str, _RowT] = {}
    for row in rows:
        key = row.invoice_record_key
        if key in by_key:
            raise APAnalyticsDataConsistencyError("AP source record keys must be unique")
        if row.tenant_id != tenant_id:
            raise APAnalyticsInputDeniedError("AP source rows contain a different tenant")
        common = population.get(key)
        if common is None:
            raise APAnalyticsDataConsistencyError(
                "AP dedicated dataset contains a record outside the common population"
            )
        if (
            row.supplier_id != common.supplier_id
            or row.legal_entity_id != common.legal_entity_id
            or row.business_unit_id != common.business_unit_id
        ):
            raise APAnalyticsDataConsistencyError(
                "AP dedicated and common population dimensions do not match"
            )
        _validate_dedicated_invoice_values(row, common)
        if isinstance(row, APInvoicePOVarianceRowV1):
            _validate_po_relationship(row)
        elif isinstance(row, (APPaymentTermsRowV1, APPaymentAmountRowV1)):
            _validate_payment_relationship(row)
        by_key[key] = row
    if set(by_key) != set(population):
        raise APAnalyticsDataConsistencyError(
            "AP dedicated dataset does not cover the complete common population"
        )
    return by_key


def _validate_dedicated_invoice_values(
    row: APSourceRowV1,
    common: APInvoicePopulationRowV1,
) -> None:
    if row.invoice_type != common.invoice_type or row.invoice_status != common.invoice_status:
        raise APAnalyticsDataConsistencyError(
            "AP dedicated invoice classification differs from the common population"
        )
    if isinstance(row, APDuplicateInvoiceRowV1):
        if row.gross_amount is not None and money(row.gross_amount) != money(common.gross_amount):
            raise APAnalyticsDataConsistencyError("AP duplicate amount differs from population")
        if row.currency is not None and row.currency != common.currency:
            raise APAnalyticsDataConsistencyError("AP duplicate currency differs from population")
        if row.invoice_date is not None and row.invoice_date != common.invoice_date:
            raise APAnalyticsDataConsistencyError("AP duplicate date differs from population")
    elif isinstance(row, (APInvoicePOVarianceRowV1, APPaymentAmountRowV1)):
        if money(row.invoice_gross_amount) != money(common.gross_amount):
            raise APAnalyticsDataConsistencyError("AP invoice amount differs from population")
        if row.invoice_currency != common.currency:
            raise APAnalyticsDataConsistencyError("AP invoice currency differs from population")
    elif isinstance(row, APPaymentTermsRowV1):
        if (
            row.invoice_currency != common.currency
            or row.invoice_date != common.invoice_date
            or row.due_date != common.due_date
        ):
            raise APAnalyticsDataConsistencyError(
                "AP payment-term invoice facts differ from population"
            )


def _validate_po_relationship(row: APInvoicePOVarianceRowV1) -> None:
    if row.po_record_key is None:
        nullable_parent = (
            row.po_tenant_id,
            row.po_approved_amount,
            row.po_currency,
            row.po_matching_basis,
            row.po_status,
            row.po_supplier_id,
            row.po_legal_entity_id,
            row.po_business_unit_id,
        )
        if any(value is not None for value in nullable_parent):
            raise APAnalyticsDataConsistencyError("Missing PO row contains partial parent facts")
        return
    required_parent = (
        row.po_tenant_id,
        row.po_approved_amount,
        row.po_currency,
        row.po_matching_basis,
        row.po_status,
        row.po_supplier_id,
        row.po_legal_entity_id,
        row.po_business_unit_id,
    )
    if any(value is None for value in required_parent):
        raise APAnalyticsDataConsistencyError("Referenced PO row is incomplete")
    if (
        row.po_tenant_id != row.tenant_id
        or row.po_supplier_id != row.supplier_id
        or row.po_legal_entity_id != row.legal_entity_id
        or row.po_business_unit_id != row.business_unit_id
    ):
        raise APAnalyticsDataConsistencyError(
            "Invoice and referenced PO tenant or dimensions do not match"
        )


def _validate_payment_relationship(row: APPaymentTermsRowV1 | APPaymentAmountRowV1) -> None:
    if row.settled_payment_count > row.payment_count:
        raise APAnalyticsDataConsistencyError(
            "AP settled payment count exceeds non-void payment count"
        )
    if row.payment_count == 0:
        return
    if row.payment_count != 1 or row.settled_payment_count != 1:
        return
    required = (
        row.payment_date,
        row.payment_currency,
        row.payment_status,
        row.payment_tenant_id,
        row.payment_invoice_record_key,
        row.payment_legal_entity_id,
        row.payment_business_unit_id,
    )
    amount_missing = isinstance(row, APPaymentAmountRowV1) and row.payment_amount is None
    if any(value is None for value in required) or amount_missing:
        raise APAnalyticsDataConsistencyError("Eligible AP payment relationship is incomplete")
    if (
        row.payment_tenant_id != row.tenant_id
        or row.payment_invoice_record_key != row.invoice_record_key
        or row.payment_legal_entity_id != row.legal_entity_id
        or row.payment_business_unit_id != row.business_unit_id
    ):
        raise APAnalyticsDataConsistencyError(
            "Invoice and payment tenant or dimensions do not match"
        )


def rule_for(
    manifest: APPolicyRuleManifestV1,
    kind: APPolicyRuleKind,
    *,
    legal_entity_id: str | None = None,
    invoice_type: str | None = None,
    effective_on: date | None = None,
) -> APPolicyRuleV1:
    """Resolve the single exact v1 rule and verify row-level applicability when available."""
    candidates = tuple(rule for rule in manifest.rules if rule.kind is kind)
    if len(candidates) != 1:
        raise APPolicyRuleUnavailableError()
    rule = candidates[0]
    validate_rule_applicability(
        rule,
        legal_entity_id=legal_entity_id,
        invoice_type=invoice_type,
        effective_on=effective_on,
    )
    return rule


def validate_rule_applicability(
    rule: APPolicyRuleV1,
    *,
    legal_entity_id: str | None,
    invoice_type: str | None,
    effective_on: date | None,
) -> None:
    """Fail closed when one controlled rule does not cover the exact invoice facts."""
    if rule.legal_entity_ids and (
        legal_entity_id is None or legal_entity_id not in rule.legal_entity_ids
    ):
        raise APPolicyRuleUnavailableError("AP rule does not cover the legal entity")
    if invoice_type is not None and invoice_type not in rule.invoice_types:
        raise APPolicyRuleUnavailableError("AP rule does not cover the invoice type")
    if effective_on is not None and not rule.effective_from <= effective_on <= rule.effective_to:
        raise APPolicyRuleUnavailableError("AP rule does not cover the invoice date")


def validate_materiality(
    manifest: APPolicyRuleManifestV1,
    parameters: APDetectionParametersV1,
    *,
    required_currencies: set[str],
) -> tuple[
    MaterialityAmountRuleV1,
    dict[str, Decimal],
    dict[str, Decimal],
    dict[str, Decimal],
]:
    """Validate tightening-only materiality and return policy/request/effective maps."""
    rule = rule_for(manifest, APPolicyRuleKind.MATERIALITY_AMOUNT)
    if not isinstance(rule, MaterialityAmountRuleV1):
        raise APPolicyRuleUnavailableError("AP materiality rule has the wrong schema")
    organization = {item.currency: money(item.amount) for item in rule.thresholds}
    requested = {item.currency: money(item.amount) for item in parameters.requested_materiality}
    effective = {item.currency: money(item.amount) for item in parameters.effective_materiality}
    if not set(requested).issubset(organization) or not set(effective).issubset(organization):
        raise APPolicyRuleUnavailableError("Materiality currency has no controlled rule")
    if not set(requested).issubset(effective):
        raise APAnalyticsInputError(
            "Effective materiality must retain every requested materiality currency"
        )
    if not required_currencies.issubset(effective):
        raise APPolicyRuleUnavailableError(
            "Effective materiality is missing a source-data currency"
        )
    for currency, amount in requested.items():
        if amount > organization[currency]:
            raise APPolicyThresholdRelaxationError()
    for currency, amount in effective.items():
        expected = min(organization[currency], requested.get(currency, organization[currency]))
        if amount != expected:
            if amount > expected:
                raise APPolicyThresholdRelaxationError()
            raise APAnalyticsInputError(
                "Effective materiality does not equal the frozen policy/request merge"
            )
    return rule, organization, requested, effective


def amount_for_currency(values: Iterable[object], currency: str, attribute: str) -> Decimal:
    """Resolve one exact governed currency amount or fail closed."""
    matches = tuple(item for item in values if getattr(item, "currency", None) == currency)
    if len(matches) != 1:
        raise APPolicyRuleUnavailableError(f"Controlled AP rule has no exact {currency} amount")
    return money(cast(Decimal, getattr(matches[0], attribute)))


__all__ = [
    "AVERAGE_QUANTUM",
    "MONEY_QUANTUM",
    "RATIO_QUANTUM",
    "amount_for_currency",
    "average",
    "money",
    "parse_dataset_rows",
    "ratio",
    "rule_for",
    "validate_dedicated_rows",
    "validate_materiality",
    "validate_population",
    "validate_rule_applicability",
]
