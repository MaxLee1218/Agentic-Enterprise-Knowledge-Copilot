"""Bounded synthetic performance fixture for Accounts Payable Stage 10."""

from __future__ import annotations

import tracemalloc
from datetime import date
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from copilot.contracts import CurrencyAmountV1
from copilot.tools.analytics.ap_operations import run_detection
from copilot.tools.analytics.ap_schemas import (
    APAnalyticsOperation,
    APDatabaseTemplate,
    APDatasetReferenceV1,
    APDetectionParametersV1,
    APDetectionRequestV1,
    APDuplicateInvoiceRowV1,
    APInvoicePopulationRowV1,
    APPolicyRuleSnapshotV1,
    APRuleEvidenceReferenceV1,
)
from copilot.tools.knowledge import load_ap_policy_bundle
from evaluation.contracts import MetricDirection, MetricResult, MetricStatus
from evaluation.dataset_loader import canonical_hash

AP_PERFORMANCE_ROWS = 50_000
AP_ANALYTICS_LIMIT_MS = Decimal("20000")
AP_EXCEPTION_RECORD_LIMIT = 5_000
AP_PERFORMANCE_SEED = 42
_CHECKSUM = "sha256:" + ("0" * 64)
_POLICY_ROOT = Path(__file__).resolve().parents[1] / "data" / "policies" / "accounts_payable" / "v1"


def run_accounts_payable_performance_fixture(*, samples: int = 3) -> tuple[MetricResult, ...]:
    """Measure three deterministic 50k-row detection runs and enforce frozen limits."""
    if samples < 1:
        raise ValueError("performance samples must be positive")
    tracemalloc.start()
    try:
        population, dedicated = _rows()
        request = _request()
        latencies: list[Decimal] = []
        exception_count = 0
        for _sample in range(samples):
            started = perf_counter()
            result = run_detection(request, population=population, dedicated=dedicated)
            latencies.append(Decimal(str((perf_counter() - started) * 1000)))
            exception_count = len(result.records)
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    p95 = _percentile(latencies, Decimal("0.95"))
    fixture_hash = canonical_hash(
        {
            "profile": "accounts_payable_performance.v1",
            "rows": AP_PERFORMANCE_ROWS,
            "seed": AP_PERFORMANCE_SEED,
            "samples": samples,
        }
    )
    notes = (f"fixture_hash={fixture_hash}", f"samples={samples}")
    return (
        _measurement(
            "ap_performance_input_rows",
            Decimal(AP_PERFORMANCE_ROWS),
            "rows",
            passed=len(population) == AP_PERFORMANCE_ROWS,
            notes=notes,
        ),
        _measurement(
            "ap_analytics_latency_p95_ms",
            p95,
            "milliseconds",
            passed=p95 <= AP_ANALYTICS_LIMIT_MS,
            notes=notes,
        ),
        _measurement(
            "ap_performance_peak_memory_bytes",
            Decimal(peak_bytes),
            "bytes",
            passed=True,
            notes=notes,
            direction=MetricDirection.INFORMATIONAL,
        ),
        _measurement(
            "ap_performance_exception_records",
            Decimal(exception_count),
            "records",
            passed=exception_count <= AP_EXCEPTION_RECORD_LIMIT,
            notes=notes,
        ),
    )


def _rows() -> tuple[dict[str, APInvoicePopulationRowV1], tuple[APDuplicateInvoiceRowV1, ...]]:
    population: dict[str, APInvoicePopulationRowV1] = {}
    dedicated: list[APDuplicateInvoiceRowV1] = []
    for index in range(AP_PERFORMANCE_ROWS):
        key = f"PERF-{index:05d}"
        supplier = f"SUP-{(index % 100) + 1:03d}"
        common = APInvoicePopulationRowV1(
            invoice_record_key=key,
            tenant_id="TENANT-DEMO",
            supplier_id=supplier,
            legal_entity_id="LE-US-01",
            business_unit_id="BU-US-PROC",
            invoice_type="STANDARD",
            invoice_date=date(2026, 4, 1),
            posting_date=date(2026, 4, 2),
            due_date=date(2026, 5, 1),
            net_amount=Decimal("900.0000"),
            tax_amount=Decimal("100.0000"),
            gross_amount=Decimal("1000.0000"),
            currency="USD",
            invoice_status="POSTED",
            po_record_key=f"PO-{index:05d}",
            po_matching_basis="SINGLE_INVOICE",
            po_status="APPROVED",
            payment_count=0,
            settled_payment_count=0,
            eligibility_reason="ELIGIBLE",
        )
        population[key] = common
        dedicated.append(
            APDuplicateInvoiceRowV1(
                invoice_record_key=key,
                tenant_id="TENANT-DEMO",
                supplier_id=supplier,
                legal_entity_id="LE-US-01",
                business_unit_id="BU-US-PROC",
                normalized_invoice_number=f"PERF{index:05d}",
                invoice_date=common.invoice_date,
                gross_amount=common.gross_amount,
                currency=common.currency,
                invoice_type=common.invoice_type,
                invoice_status=common.invoice_status,
            )
        )
    return population, tuple(dedicated)


def _request() -> APDetectionRequestV1:
    bundle = load_ap_policy_bundle(_POLICY_ROOT, expected_tenant_id="TENANT-DEMO")
    snapshot = APPolicyRuleSnapshotV1(
        rule_manifest=bundle.rule_manifest,
        document_evidence=tuple(
            APRuleEvidenceReferenceV1(rule_id=rule.rule_id, evidence_id=f"E-DOC-{index}")
            for index, rule in enumerate(bundle.rule_manifest.rules, start=1)
        ),
    )
    datasets = tuple(
        APDatasetReferenceV1(
            template_id=template,
            template_version=template.value,
            evidence_id=f"E-DB-{index}",
            dataset_checksum=_CHECKSUM,
            rows=(),
        )
        for index, template in enumerate(
            (
                APDatabaseTemplate.INVOICE_POPULATION,
                APDatabaseTemplate.DUPLICATE_CANDIDATES,
            ),
            start=1,
        )
    )
    return APDetectionRequestV1(
        operation_name=APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        operation_version="1.0.0",
        datasets=datasets,
        rule_snapshot=snapshot,
        parameters=APDetectionParametersV1(
            effective_materiality=(CurrencyAmountV1(currency="USD", amount=Decimal("1000.0000")),)
        ),
        engine_version="accounts_payable_analytics.v1",
    )


def _percentile(values: list[Decimal], quantile: Decimal) -> Decimal:
    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            int((Decimal(len(ordered)) * quantile).to_integral_value(rounding="ROUND_CEILING")) - 1,
        ),
    )
    return ordered[index]


def _measurement(
    name: str,
    value: Decimal,
    unit: str,
    *,
    passed: bool,
    notes: tuple[str, ...],
    direction: MetricDirection = MetricDirection.LOWER_IS_BETTER,
) -> MetricResult:
    return MetricResult(
        metric_name=name,
        value=value,
        numerator=value,
        denominator=Decimal(1),
        unit=unit,
        direction=direction,
        coverage=Decimal(1),
        status=MetricStatus.PASS if passed else MetricStatus.FAIL,
        notes=notes,
    )


__all__ = [
    "AP_ANALYTICS_LIMIT_MS",
    "AP_PERFORMANCE_ROWS",
    "run_accounts_payable_performance_fixture",
]
