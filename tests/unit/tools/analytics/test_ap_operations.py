"""Formula, equality-boundary, exclusion, and materiality tests for AP Stage 5."""

from __future__ import annotations

from typing import cast

import pytest

from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.tools.analytics import AccountsPayableAnalyticsTool
from copilot.tools.analytics.ap_schemas import APAnalyticsOperation, APDatabaseTemplate
from copilot.tools.analytics.exceptions import (
    APAnalyticsDataConsistencyError,
    APAnalyticsDataIncompleteError,
    APAnalyticsInputError,
    APAnalyticsScopeTooLargeError,
    APPolicyThresholdRelaxationError,
)
from copilot.tools.base import ToolExecutionOutput
from tests.unit.tools.analytics.ap_helpers import (
    add_database_dataset,
    add_policy_evidence,
    analytics_context,
    detection_arguments,
    duplicate_row,
    evidence_ledger,
    payment_amount_row,
    payment_terms_row,
    po_row,
    population_row,
)


def _execute_detection(
    operation: APAnalyticsOperation,
    population: list[JsonMapping],
    dedicated: list[JsonMapping],
    template: APDatabaseTemplate,
    *,
    requested_materiality: list[dict[str, str]] | None = None,
    effective_materiality: list[dict[str, str]] | None = None,
) -> tuple[ToolExecutionOutput, InMemoryEvidenceLedger]:
    ledger = evidence_ledger()
    rule_snapshot, _ = add_policy_evidence(ledger)
    population_dataset = add_database_dataset(
        ledger, APDatabaseTemplate.INVOICE_POPULATION, population
    )
    dedicated_dataset = add_database_dataset(ledger, template, dedicated)
    arguments = detection_arguments(
        operation,
        population_dataset,
        dedicated_dataset,
        rule_snapshot,
        requested_materiality=requested_materiality,
        effective_materiality=effective_materiality,
    )
    result = AccountsPayableAnalyticsTool(ledger).execute(arguments, analytics_context(arguments))
    return result, ledger


def test_exact_duplicate_uses_exact_key_canonical_member_and_materiality_equality() -> None:
    population = [
        population_row("I-001", gross_amount="1000.0000"),
        population_row("I-002", gross_amount="1000.0000"),
        population_row("I-003", invoice_date="2026-05-02", gross_amount="1000.0000"),
    ]
    dedicated = [duplicate_row(row) for row in population]

    result, _ledger = _execute_detection(
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        population,
        dedicated,
        APDatabaseTemplate.DUPLICATE_CANDIDATES,
    )

    records = cast(list[JsonMapping], result.output.root["records"])
    groups = cast(list[JsonMapping], result.output.root["duplicate_groups"])
    assert [item["invoice_record_key"] for item in records] == ["I-002"]
    assert records[0]["status"] == "FINDING"
    assert groups[0]["canonical_invoice_record_key"] == "I-001"
    assert groups[0]["member_invoice_record_keys"] == ["I-001", "I-002"]
    assert result.output.root["normalization_version"] == "invoice_number_normalization.v1"
    assert result.output.root["metrics"] == {
        "duplicate_group_count": 1,
        "duplicate_invoice_count": 1,
        "duplicate_exposure_amount_by_currency": {"USD": "1000.0000"},
    }


def test_po_variance_equality_is_normal_above_is_exception_and_edges_are_excluded() -> None:
    population = [
        population_row("I-EQUAL", gross_amount="1050.0000"),
        population_row("I-ABOVE", gross_amount="1060.0000"),
        population_row("I-ZERO", gross_amount="100.0000"),
        population_row("I-FX", gross_amount="1060.0000"),
    ]
    dedicated = [
        po_row(population[0], approved_amount="1000.0000"),
        po_row(population[1], approved_amount="1000.0000"),
        po_row(population[2], approved_amount="0.0000"),
        po_row(population[3], approved_amount="1000.0000", po_currency="CNY"),
    ]

    result, _ledger = _execute_detection(
        APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION,
        population,
        dedicated,
        APDatabaseTemplate.INVOICE_PO_VARIANCE,
    )

    records = cast(list[JsonMapping], result.output.root["records"])
    assert [item["invoice_record_key"] for item in records] == ["I-ABOVE"]
    assert records[0]["observed_values"] == {
        "gross_amount": "1060.0000",
        "approved_amount": "1000.0000",
        "variance_amount": "60.0000",
        "variance_rate": "0.06000000",
        "absolute_variance_amount": "60.0000",
        "absolute_variance_rate": "0.06000000",
    }
    assert records[0]["status"] == "WARNING"
    assert result.output.root["exclusion_count_by_reason"] == {
        "AP_CURRENCY_MISMATCH_EXCLUDED": 1,
        "PO_AMOUNT_ZERO": 1,
    }


def test_missing_po_threshold_equality_detects_and_valid_approval_exempts() -> None:
    population = [
        population_row(
            "I-EQUAL",
            gross_amount="1000.0000",
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
        population_row(
            "I-BELOW",
            gross_amount="999.9999",
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
        population_row(
            "I-APPROVED",
            gross_amount="1500.0000",
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
        population_row(
            "I-INVALID",
            gross_amount="1500.0000",
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
    ]
    dedicated = [
        po_row(population[0], po_record_key=None),
        po_row(population[1], po_record_key=None),
        po_row(
            population[2],
            po_record_key=None,
            no_po_exception_ref="APPROVAL-001",
            no_po_exception_approved=True,
        ),
        po_row(population[3], po_record_key=None, no_po_exception_approved=True),
    ]

    result, _ledger = _execute_detection(
        APAnalyticsOperation.MISSING_PO_DETECTION,
        population,
        dedicated,
        APDatabaseTemplate.INVOICE_PO_VARIANCE,
    )

    records = cast(list[JsonMapping], result.output.root["records"])
    assert [item["invoice_record_key"] for item in records] == ["I-EQUAL"]
    assert records[0]["status"] == "FINDING"
    assert result.output.root["exclusion_count_by_reason"] == {"INVALID_NO_PO_EXCEPTION": 1}


def test_payment_terms_use_calendar_days_boundary_and_settlement_exclusions() -> None:
    population = [
        population_row("I-ONTIME", invoice_status="PAID"),
        population_row("I-LATE", invoice_status="PAID"),
        population_row("I-EARLY", invoice_status="PAID"),
        population_row("I-UNPAID"),
        population_row("I-MULTI", invoice_status="PAID"),
    ]
    dedicated = [
        payment_terms_row(population[0]),
        payment_terms_row(population[1], payment_date="2026-06-05"),
        payment_terms_row(population[2], payment_date="2026-05-21"),
        payment_terms_row(
            population[3], payment_date=None, payment_count=0, settled_payment_count=0
        ),
        payment_terms_row(population[4], payment_count=2, settled_payment_count=2),
    ]

    result, _ledger = _execute_detection(
        APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION,
        population,
        dedicated,
        APDatabaseTemplate.PAYMENT_TERMS,
    )

    records = cast(list[JsonMapping], result.output.root["records"])
    assert {(item["exception_type"], item["invoice_record_key"]) for item in records} == {
        ("LATE_PAYMENT", "I-LATE"),
        ("MATERIAL_EARLY_PAYMENT", "I-EARLY"),
    }
    assert result.output.root["metrics"] == {
        "late_payment_count": 1,
        "material_early_payment_count": 1,
        "average_days_late": "5.00",
    }
    assert result.output.root["exclusion_count_by_reason"] == {
        "MULTIPLE_PAYMENT_EXCLUSION": 1,
        "UNPAID_INVOICE": 1,
    }


def test_overpayment_tolerance_equality_is_normal_and_above_is_warning() -> None:
    population = [
        population_row("I-EQUAL", gross_amount="600.0000", invoice_status="PAID"),
        population_row("I-ABOVE", gross_amount="600.0000", invoice_status="PAID"),
        population_row("I-UNDER", gross_amount="600.0000", invoice_status="PAID"),
    ]
    dedicated = [
        payment_amount_row(population[0], payment_amount="605.0000"),
        payment_amount_row(population[1], payment_amount="605.0001"),
        payment_amount_row(population[2], payment_amount="500.0000"),
    ]

    result, _ledger = _execute_detection(
        APAnalyticsOperation.OVERPAYMENT_DETECTION,
        population,
        dedicated,
        APDatabaseTemplate.PAYMENT_AMOUNT,
    )

    records = cast(list[JsonMapping], result.output.root["records"])
    assert [item["invoice_record_key"] for item in records] == ["I-ABOVE"]
    observed = cast(JsonMapping, records[0]["observed_values"])
    assert observed["overpayment_amount"] == "5.0001"
    assert records[0]["status"] == "WARNING"


def test_empty_dataset_succeeds_but_no_eligible_coverage_and_relaxation_fail_closed() -> None:
    empty, _ledger = _execute_detection(
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        [],
        [],
        APDatabaseTemplate.DUPLICATE_CANDIDATES,
    )
    assert empty.output.root["empty_result"] is True
    assert empty.output.root["records"] == []
    assert empty.output.root["warnings"] == ["EMPTY_SOURCE_POPULATION"]

    ineligible = population_row(
        "I-VOID",
        invoice_status="VOID",
        eligibility_reason="INVOICE_STATUS_INELIGIBLE",
    )
    with pytest.raises(APAnalyticsDataIncompleteError):
        _execute_detection(
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
            [ineligible],
            [duplicate_row(ineligible)],
            APDatabaseTemplate.DUPLICATE_CANDIDATES,
        )

    population = [population_row("I-001")]
    with pytest.raises(APPolicyThresholdRelaxationError):
        _execute_detection(
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
            population,
            [duplicate_row(population[0])],
            APDatabaseTemplate.DUPLICATE_CANDIDATES,
            requested_materiality=[{"currency": "USD", "amount": "2000.0000"}],
            effective_materiality=[{"currency": "USD", "amount": "2000.0000"}],
        )

    with pytest.raises(APAnalyticsInputError, match="retain every requested"):
        _execute_detection(
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
            population,
            [duplicate_row(population[0])],
            APDatabaseTemplate.DUPLICATE_CANDIDATES,
            requested_materiality=[{"currency": "CNY", "amount": "4000.0000"}],
            effective_materiality=[{"currency": "USD", "amount": "1000.0000"}],
        )


def test_cross_record_dimension_mismatch_fails_before_calculation() -> None:
    population = [population_row("I-001")]
    mismatched = duplicate_row(population[0])
    mismatched["supplier_id"] = "SUP-DIFFERENT"

    with pytest.raises(APAnalyticsDataConsistencyError, match="dimensions do not match"):
        _execute_detection(
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
            population,
            [mismatched],
            APDatabaseTemplate.DUPLICATE_CANDIDATES,
        )


def test_exception_record_limit_fails_instead_of_returning_a_partial_summary() -> None:
    population = [population_row(f"I-{index:05d}") for index in range(5002)]

    with pytest.raises(APAnalyticsScopeTooLargeError, match="exceed 5,000"):
        _execute_detection(
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
            population,
            [duplicate_row(row) for row in population],
            APDatabaseTemplate.DUPLICATE_CANDIDATES,
        )
