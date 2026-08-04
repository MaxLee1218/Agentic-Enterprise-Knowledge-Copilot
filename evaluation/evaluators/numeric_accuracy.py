"""Decimal-based numeric assertions with explicit missing and non-finite semantics."""

from decimal import Decimal, InvalidOperation

from evaluation.contracts import CapturedExecution, EvaluationCase, MetricResult
from evaluation.evaluators.base import count_metric, ratio_metric


class NumericAccuracyEvaluator:
    name = "numeric_accuracy"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        passed = failed = missing = 0
        notes: list[str] = []
        for assertion in case.expected_numbers:
            found, actual, unit = _resolve(assertion.json_path, execution)
            if not found:
                missing += 1
                notes.append(f"{assertion.assertion_id}: missing")
                continue
            if actual is None:
                if assertion.allow_null and assertion.expected_value is None:
                    passed += 1
                else:
                    failed += 1
                    notes.append(f"{assertion.assertion_id}: unexpected null")
                continue
            try:
                value = Decimal(str(actual).rstrip("%"))
                if isinstance(actual, str) and actual.endswith("%"):
                    value /= Decimal(100)
            except (InvalidOperation, ValueError):
                failed += 1
                notes.append(f"{assertion.assertion_id}: non-numeric")
                continue
            if not value.is_finite() or assertion.expected_value is None:
                failed += 1
                notes.append(f"{assertion.assertion_id}: non-finite or unexpected value")
                continue
            tolerance = max(
                assertion.absolute_tolerance,
                abs(assertion.expected_value) * assertion.relative_tolerance,
            )
            unit_ok = assertion.unit is None or assertion.unit == unit
            if abs(value - assertion.expected_value) <= tolerance and unit_ok:
                passed += 1
            else:
                failed += 1
                notes.append(f"{assertion.assertion_id}: outside tolerance or unit mismatch")
        total = len(case.expected_numbers)
        return (
            ratio_metric("numeric_accuracy", passed, total, notes=tuple(notes)),
            count_metric("numeric_assertion_passed", passed),
            count_metric("numeric_assertion_failed", failed, pass_when=failed == 0),
            count_metric("numeric_assertion_missing", missing, pass_when=missing == 0),
        )


def _resolve(path: str, execution: CapturedExecution) -> tuple[bool, object, str | None]:
    if path.startswith("metric:"):
        parts = path.split(":", 2)
        metric_name = parts[1]
        dimensions = {}
        if len(parts) == 3:
            dimensions = dict(item.split("=", 1) for item in parts[2].split(",") if "=" in item)
        for result in execution.tool_results:
            if result.tool_name != "analysis_engine" or result.output is None:
                continue
            metrics = result.output.root.get("metrics")
            if not isinstance(metrics, list):
                continue
            for metric in metrics:
                raw_dimensions = metric.get("dimensions") if isinstance(metric, dict) else None
                if (
                    isinstance(metric, dict)
                    and metric.get("metric") == metric_name
                    and all(
                        isinstance(raw_dimensions, dict) and raw_dimensions.get(key) == value
                        for key, value in dimensions.items()
                    )
                ):
                    return True, metric.get("value"), str(metric.get("unit"))
        return False, None, None
    return False, None, None


__all__ = ["NumericAccuracyEvaluator"]
