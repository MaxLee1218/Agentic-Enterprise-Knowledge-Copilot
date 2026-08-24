"""Independent deterministic metrics for the frozen Accounts Payable v1 oracle."""

from __future__ import annotations

from decimal import Decimal

from pydantic import JsonValue

from copilot.contracts import TaskType
from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    ExpectedAPExceptionRecord,
    ExpectedAPSummaryAssertion,
    MetricDirection,
    MetricResult,
    MetricStatus,
)
from evaluation.evaluators.base import ratio_metric

_DETECTION_SUFFIX = "_detection.v1"
_DUPLICATE = "EXACT_DUPLICATE_INVOICE"
_PO_VARIANCE = "PO_AMOUNT_VARIANCE"
_PAYMENT_TERMS = {"LATE_PAYMENT", "MATERIAL_EARLY_PAYMENT"}


class AccountsPayableEvaluator:
    """Score AP Tool outputs only against labels carried by the evaluation dataset."""

    name = "accounts_payable"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        if case.task_input.task_type is not TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1:
            return ()
        expected = case.expected_ap
        if expected is None:
            return tuple(_not_available(name, direction) for name, direction in _METRICS)

        outputs = _analytics_outputs(execution)
        detections = tuple(
            output
            for output in outputs
            if str(output.get("operation_name", "")).endswith(_DETECTION_SUFFIX)
        )
        predicted_records = tuple(
            record for output in detections for record in _objects(output.get("records"))
        )
        predicted = {
            (str(item.get("invoice_record_key")), str(item.get("exception_type")))
            for item in predicted_records
        }
        labeled = {
            (item.invoice_record_key, item.exception_type) for item in expected.exception_records
        }
        predicted_duplicate = {item for item in predicted if item[1] == _DUPLICATE}
        labeled_duplicate = {item for item in labeled if item[1] == _DUPLICATE}
        has_negative_population = bool(expected.normal_eligible_record_keys)

        duplicate_precision = _classification_metric(
            "duplicate_detection_precision",
            len(predicted_duplicate & labeled_duplicate),
            len(predicted_duplicate),
            available=bool(labeled_duplicate) and has_negative_population,
        )
        duplicate_recall = _classification_metric(
            "duplicate_detection_recall",
            len(predicted_duplicate & labeled_duplicate),
            len(labeled_duplicate),
            available=bool(labeled_duplicate) and has_negative_population,
        )
        exception_precision = _classification_metric(
            "exception_detection_precision",
            len(predicted & labeled),
            len(predicted),
            available=bool(labeled) and has_negative_population,
        )
        exception_recall = _classification_metric(
            "exception_detection_recall",
            len(predicted & labeled),
            len(labeled),
            available=bool(labeled) and has_negative_population,
        )
        predicted_keys = {key for key, _exception_type in predicted}
        normal = set(expected.normal_eligible_record_keys)
        false_positives = len(predicted_keys & normal)
        false_negatives = len(labeled - predicted)

        actual_by_label = {
            (str(item.get("invoice_record_key")), str(item.get("exception_type"))): item
            for item in predicted_records
        }
        po_passed, po_total = _record_accuracy(
            expected.exception_records,
            actual_by_label,
            exception_types={_PO_VARIANCE},
        )
        payment_passed, payment_total = _record_accuracy(
            expected.exception_records,
            actual_by_label,
            exception_types=_PAYMENT_TERMS,
        )
        summary = next(
            (
                output
                for output in outputs
                if output.get("operation_name") == "ap.exception_summary.v1"
            ),
            None,
        )
        amount_passed, amount_total = _summary_accuracy(expected.summary_assertions, summary)
        actual_exclusions = {
            (str(item.get("invoice_record_key")), str(item.get("reason_code")))
            for item in _objects(summary.get("exclusions") if summary else None)
        }
        labeled_exclusions = {
            (item.invoice_record_key, item.reason_code) for item in expected.exclusions
        }
        exclusion_correct = len(actual_exclusions & labeled_exclusions)
        exclusion_total = len(actual_exclusions | labeled_exclusions)
        policy_passed, policy_total = _policy_accuracy(
            expected.exception_records,
            actual_by_label,
            detections,
            rule_set_version=expected.rule_set_version,
            manifest_checksum=expected.manifest_checksum,
        )
        return (
            duplicate_precision,
            duplicate_recall,
            exception_precision,
            exception_recall,
            ratio_metric(
                "false_positive_rate",
                false_positives,
                len(normal),
                direction=MetricDirection.LOWER_IS_BETTER,
                pass_when=false_positives == 0,
            ),
            ratio_metric(
                "false_negative_rate",
                false_negatives,
                len(labeled),
                direction=MetricDirection.LOWER_IS_BETTER,
                pass_when=false_negatives == 0,
            ),
            ratio_metric("po_variance_accuracy", po_passed, po_total),
            ratio_metric("payment_term_accuracy", payment_passed, payment_total),
            ratio_metric("exception_amount_accuracy", amount_passed, amount_total),
            ratio_metric("exclusion_accuracy", exclusion_correct, exclusion_total),
            ratio_metric("policy_binding_accuracy", policy_passed, policy_total),
        )


def _analytics_outputs(execution: CapturedExecution) -> tuple[dict[str, JsonValue], ...]:
    return tuple(
        result.output.root
        for result in execution.tool_results
        if result.tool_name == "analysis_engine" and result.output is not None
    )


def _objects(value: object) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _classification_metric(
    name: str,
    numerator: int,
    denominator: int,
    *,
    available: bool,
) -> MetricResult:
    if not available:
        return _not_available(name, MetricDirection.HIGHER_IS_BETTER)
    return ratio_metric(name, numerator, denominator)


def _record_accuracy(
    expected: tuple[ExpectedAPExceptionRecord, ...],
    actual: dict[tuple[str, str], dict[str, JsonValue]],
    *,
    exception_types: set[str],
) -> tuple[int, int]:
    passed = total = 0
    for label in expected:
        if label.exception_type not in exception_types:
            continue
        observed = actual.get((label.invoice_record_key, label.exception_type))
        for collection_name, values in (
            ("observed_values", label.observed_values),
            ("threshold_values", label.threshold_values),
        ):
            actual_values = observed.get(collection_name) if observed else None
            for key, expected_value in values.items():
                total += 1
                if isinstance(actual_values, dict) and actual_values.get(key) == expected_value:
                    passed += 1
        if label.status is not None:
            total += 1
            if observed is not None and observed.get("status") == label.status:
                passed += 1
    return passed, total


def _summary_accuracy(
    assertions: tuple[ExpectedAPSummaryAssertion, ...], summary: dict[str, JsonValue] | None
) -> tuple[int, int]:
    passed = 0
    metrics = summary.get("metrics") if summary else None
    for assertion in assertions:
        metric_name = assertion.metric_name
        expected_value = assertion.expected_value
        currency = assertion.currency
        actual: object = metrics.get(metric_name) if isinstance(metrics, dict) else None
        if currency is not None:
            actual = actual.get(currency) if isinstance(actual, dict) else None
        if actual == expected_value:
            passed += 1
    return passed, len(assertions)


def _policy_accuracy(
    expected: tuple[ExpectedAPExceptionRecord, ...],
    actual: dict[tuple[str, str], dict[str, JsonValue]],
    outputs: tuple[dict[str, JsonValue], ...],
    *,
    rule_set_version: str | None,
    manifest_checksum: str | None,
) -> tuple[int, int]:
    passed = total = 0
    for label in expected:
        record = actual.get((label.invoice_record_key, label.exception_type))
        for field, expected_value in (
            ("rule_id", label.rule_id),
            ("rule_version", label.rule_version),
        ):
            if expected_value is None:
                continue
            total += 1
            if record is not None and record.get(field) == expected_value:
                passed += 1
    for output in outputs:
        for field, expected_value in (
            ("rule_set_version", rule_set_version),
            ("manifest_checksum", manifest_checksum),
        ):
            if expected_value is None:
                continue
            total += 1
            if output.get(field) == expected_value:
                passed += 1
    return passed, total


def _not_available(name: str, direction: MetricDirection) -> MetricResult:
    return MetricResult(
        metric_name=name,
        unit="ratio",
        direction=direction,
        coverage=Decimal(0),
        status=MetricStatus.NOT_AVAILABLE,
        notes=("No complete AP oracle is applicable to this case",),
    )


_METRICS = (
    ("duplicate_detection_precision", MetricDirection.HIGHER_IS_BETTER),
    ("duplicate_detection_recall", MetricDirection.HIGHER_IS_BETTER),
    ("exception_detection_precision", MetricDirection.HIGHER_IS_BETTER),
    ("exception_detection_recall", MetricDirection.HIGHER_IS_BETTER),
    ("false_positive_rate", MetricDirection.LOWER_IS_BETTER),
    ("false_negative_rate", MetricDirection.LOWER_IS_BETTER),
    ("po_variance_accuracy", MetricDirection.HIGHER_IS_BETTER),
    ("payment_term_accuracy", MetricDirection.HIGHER_IS_BETTER),
    ("exception_amount_accuracy", MetricDirection.HIGHER_IS_BETTER),
    ("exclusion_accuracy", MetricDirection.HIGHER_IS_BETTER),
    ("policy_binding_accuracy", MetricDirection.HIGHER_IS_BETTER),
)


__all__ = ["AccountsPayableEvaluator"]
