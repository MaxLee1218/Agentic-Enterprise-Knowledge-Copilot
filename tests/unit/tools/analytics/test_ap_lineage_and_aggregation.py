"""AP Calculation Evidence batching, lineage, summary, and supplier-rate tests."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest

from copilot.contracts import EvidenceItem, JsonObject
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.tools.analytics import AccountsPayableAnalyticsTool
from copilot.tools.analytics.ap_schemas import APAnalyticsOperation, APDatabaseTemplate
from copilot.tools.analytics.exceptions import (
    APAnalyticsInputDeniedError,
    APAnalyticsScopeTooLargeError,
    APPolicyRuleBindingMismatchError,
)
from copilot.tools.base import ToolExecutionOutput
from tests.unit.tools.analytics.ap_helpers import (
    add_database_dataset,
    add_policy_evidence,
    aggregation_arguments,
    analytics_context,
    detection_arguments,
    duplicate_row,
    evidence_ledger,
    po_row,
    population_row,
)


def _record_calculation(
    ledger: InMemoryEvidenceLedger,
    tool: AccountsPayableAnalyticsTool,
    arguments: JsonObject,
    *,
    call_id: str,
) -> tuple[ToolExecutionOutput, tuple[EvidenceItem, ...]]:
    context = analytics_context(arguments, call_id=call_id)
    execution = tool.execute(arguments, context)
    evidence = ledger.record(context.call, execution.evidence)
    return execution, evidence


def test_summary_counts_unique_invoices_and_supplier_rate_is_stably_ranked() -> None:
    ledger = evidence_ledger()
    rule_snapshot, _ = add_policy_evidence(ledger)
    population = [
        population_row(
            "I-001",
            supplier_id="SUP-001",
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
        population_row(
            "I-002",
            supplier_id="SUP-001",
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
        population_row("I-003", supplier_id="SUP-002"),
    ]
    population_dataset = add_database_dataset(
        ledger, APDatabaseTemplate.INVOICE_POPULATION, population
    )
    duplicate_dataset = add_database_dataset(
        ledger,
        APDatabaseTemplate.DUPLICATE_CANDIDATES,
        [duplicate_row(population[0]), duplicate_row(population[1]), duplicate_row(population[2])],
    )
    po_dataset = add_database_dataset(
        ledger,
        APDatabaseTemplate.INVOICE_PO_VARIANCE,
        [
            po_row(
                population[0],
                po_record_key=None,
                no_po_exception_ref="APPROVAL-001",
                no_po_exception_approved=True,
            ),
            po_row(population[1], po_record_key=None),
            po_row(population[2]),
        ],
    )
    tool = AccountsPayableAnalyticsTool(ledger)
    duplicate_arguments = detection_arguments(
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        population_dataset,
        duplicate_dataset,
        rule_snapshot,
    )
    missing_arguments = detection_arguments(
        APAnalyticsOperation.MISSING_PO_DETECTION,
        population_dataset,
        po_dataset,
        rule_snapshot,
    )
    _duplicate, duplicate_evidence = _record_calculation(
        ledger, tool, duplicate_arguments, call_id="TC-AP-DUP"
    )
    _missing, missing_evidence = _record_calculation(
        ledger, tool, missing_arguments, call_id="TC-AP-MISSING"
    )
    calculation_ids = tuple(item.evidence_id for item in (*duplicate_evidence, *missing_evidence))

    summary_arguments = aggregation_arguments(
        APAnalyticsOperation.EXCEPTION_SUMMARY,
        population_dataset,
        rule_snapshot,
        calculation_ids,
    )
    summary_context = analytics_context(summary_arguments, call_id="TC-AP-SUMMARY")
    summary = tool.execute(summary_arguments, summary_context)

    metrics = cast(JsonMapping, summary.output.root["metrics"])
    assert metrics["invoice_count"] == 3
    assert metrics["exception_invoice_count"] == 1
    assert metrics["exception_rate"] == "0.33333333"
    assert metrics["exception_count_by_type"] == {
        "EXACT_DUPLICATE_INVOICE": 1,
        "PO_AMOUNT_VARIANCE": 0,
        "MISSING_REQUIRED_PO": 1,
        "LATE_PAYMENT": 0,
        "MATERIAL_EARLY_PAYMENT": 0,
        "OVERPAYMENT": 0,
    }
    assert metrics["exception_invoice_amount_by_currency"] == {"USD": "1000.0000"}
    assert metrics["exclusion_count_by_reason"] == {}
    records = cast(list[JsonMapping], summary.output.root["records"])
    assert {item["invoice_record_key"] for item in records} == {"I-002"}
    assert all(item["calculation_evidence_id"] for item in records)
    assert all(
        calculation_id in summary.evidence[0].source_reference.input_evidence_ids
        for calculation_id in calculation_ids
    )

    supplier_arguments = aggregation_arguments(
        APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
        population_dataset,
        rule_snapshot,
        calculation_ids,
    )
    supplier_result = tool.execute(
        supplier_arguments,
        analytics_context(supplier_arguments, call_id="TC-AP-SUPPLIER"),
    )
    rates = cast(list[JsonMapping], supplier_result.output.root["supplier_rates"])
    assert [item["supplier_id"] for item in rates] == ["SUP-001", "SUP-002"]
    assert rates[0]["supplier_exception_rate"] == "0.50000000"
    assert rates[1]["supplier_exception_rate"] == "0E-8"


def test_large_detection_batches_deterministically_and_missing_batch_fails_closed() -> None:
    ledger = evidence_ledger()
    rule_snapshot, _ = add_policy_evidence(ledger)
    population = [population_row(f"I-{index:04d}") for index in range(1002)]
    dedicated = [duplicate_row(row) for row in population]
    population_dataset = add_database_dataset(
        ledger, APDatabaseTemplate.INVOICE_POPULATION, population
    )
    duplicate_dataset = add_database_dataset(
        ledger, APDatabaseTemplate.DUPLICATE_CANDIDATES, dedicated
    )
    arguments = detection_arguments(
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        population_dataset,
        duplicate_dataset,
        rule_snapshot,
    )
    tool = AccountsPayableAnalyticsTool(ledger)
    context = analytics_context(arguments, call_id="TC-AP-BATCH")

    result = tool.execute(arguments, context)
    repeated = tool.execute(
        arguments,
        analytics_context(arguments, call_id="TC-AP-BATCH-REPEATED"),
    )

    assert len(result.evidence) == 2
    assert result.output == repeated.output
    assert tuple(item.content.checksum for item in result.evidence) == tuple(
        item.content.checksum for item in repeated.evidence
    )
    assert tuple(item.source_reference.reference for item in result.evidence) == tuple(
        item.source_reference.reference for item in repeated.evidence
    )
    assert [item.source_reference.reference.root["batch_index"] for item in result.evidence] == [
        0,
        1,
    ]
    assert all(item.source_reference.reference.root["batch_count"] == 2 for item in result.evidence)
    stored = ledger.record(context.call, result.evidence)
    incomplete_ids = (stored[0].evidence_id,)
    summary_arguments = aggregation_arguments(
        APAnalyticsOperation.EXCEPTION_SUMMARY,
        population_dataset,
        rule_snapshot,
        incomplete_ids,
    )
    with pytest.raises(APAnalyticsInputDeniedError, match="batches are incomplete"):
        tool.execute(
            summary_arguments,
            analytics_context(summary_arguments, call_id="TC-AP-INCOMPLETE"),
        )

    tampered_reference = dict(stored[0].source_reference.reference.root)
    tampered_reference["formulas"] = ["tampered-formula"]
    tampered_evidence = stored[0].model_copy(
        update={
            "evidence_id": "E-AP-CALC-TAMPERED",
            "source_reference": stored[0].source_reference.model_copy(
                update={"reference": JsonObject(tampered_reference)}
            ),
        }
    )
    tampered_stored = ledger.add(tampered_evidence, tenant_id="TENANT-DEMO").evidence
    tampered_arguments = aggregation_arguments(
        APAnalyticsOperation.EXCEPTION_SUMMARY,
        population_dataset,
        rule_snapshot,
        (tampered_stored.evidence_id, stored[1].evidence_id),
    )
    with pytest.raises(APAnalyticsInputDeniedError, match="reference metadata drifted"):
        tool.execute(
            tampered_arguments,
            analytics_context(tampered_arguments, call_id="TC-AP-REFERENCE-DRIFT"),
        )


def test_dataset_checksum_truncation_tenant_and_manifest_drift_fail_closed() -> None:
    ledger = evidence_ledger()
    rule_snapshot, _ = add_policy_evidence(ledger)
    population = [population_row("I-001"), population_row("I-002")]
    population_dataset = add_database_dataset(
        ledger, APDatabaseTemplate.INVOICE_POPULATION, population
    )
    duplicate_dataset = add_database_dataset(
        ledger,
        APDatabaseTemplate.DUPLICATE_CANDIDATES,
        [duplicate_row(item) for item in population],
    )
    arguments = detection_arguments(
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        population_dataset,
        duplicate_dataset,
        rule_snapshot,
    )
    tool = AccountsPayableAnalyticsTool(ledger)

    tampered = deepcopy(arguments.root)
    datasets = cast(list[JsonMapping], tampered["datasets"])
    rows = cast(list[JsonMapping], datasets[1]["rows"])
    rows[0]["normalized_invoice_number"] = "TAMPERED"
    tampered_arguments = type(arguments)(tampered)
    with pytest.raises(APAnalyticsInputDeniedError, match="checksum"):
        tool.execute(tampered_arguments, analytics_context(tampered_arguments))

    with pytest.raises(APAnalyticsInputDeniedError, match="different tenant"):
        tool.execute(arguments, analytics_context(arguments, tenant_id="TENANT-A"))

    drifted = deepcopy(arguments.root)
    snapshot = cast(JsonMapping, drifted["rule_snapshot"])
    manifest = cast(JsonMapping, snapshot["rule_manifest"])
    manifest["manifest_checksum"] = "sha256:" + ("0" * 64)
    drifted_arguments = type(arguments)(drifted)
    with pytest.raises(APPolicyRuleBindingMismatchError, match="checksum drifted"):
        tool.execute(drifted_arguments, analytics_context(drifted_arguments))

    truncated_ledger = evidence_ledger()
    truncated_snapshot, _ = add_policy_evidence(truncated_ledger)
    truncated_population = add_database_dataset(
        truncated_ledger,
        APDatabaseTemplate.INVOICE_POPULATION,
        population,
        truncated=True,
    )
    truncated_dedicated = add_database_dataset(
        truncated_ledger,
        APDatabaseTemplate.DUPLICATE_CANDIDATES,
        [duplicate_row(item) for item in population],
    )
    truncated_arguments = detection_arguments(
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        truncated_population,
        truncated_dedicated,
        truncated_snapshot,
    )
    with pytest.raises(APAnalyticsScopeTooLargeError):
        AccountsPayableAnalyticsTool(truncated_ledger).execute(
            truncated_arguments,
            analytics_context(truncated_arguments),
        )
