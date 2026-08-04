"""Strict and diagnostic tool-selection metrics."""

from evaluation.contracts import CapturedExecution, EvaluationCase, MetricResult
from evaluation.evaluators.base import count_metric, ratio_metric


class ToolSelectionEvaluator:
    name = "tool_selection"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        expected = case.expected_tools
        called = [call.tool_name for call in execution.tool_calls]
        called_set = set(called)
        required = set(expected.required_tools)
        allowed = required | set(expected.optional_tools)
        forbidden = set(expected.forbidden_tools)
        missing = required - called_set
        unexpected = (called_set - allowed if allowed else set()) | (called_set & forbidden)
        order_ok = all(
            before in called_set
            and after in called_set
            and _first_index(called, before) < _first_index(called, after)
            for before, after in expected.required_order_constraints
        )
        counts_ok = all(
            called.count(tool) <= maximum for tool, maximum in expected.max_call_counts.items()
        )
        strict = not missing and not unexpected and order_ok and counts_ok
        tp = len(required & called_set)
        precision_denominator = len(called_set)
        recall_denominator = len(required)
        precision = (
            tp / precision_denominator if precision_denominator else 1.0 if not required else 0.0
        )
        recall = tp / recall_denominator if recall_denominator else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        applicable = bool(required or expected.optional_tools or forbidden)
        return (
            ratio_metric("tool_selection_accuracy", int(strict), int(applicable)),
            ratio_metric("tool_selection_precision", round(precision * 1_000_000), 1_000_000),
            ratio_metric("tool_selection_recall", round(recall * 1_000_000), 1_000_000),
            ratio_metric("tool_selection_f1", round(f1 * 1_000_000), 1_000_000),
            count_metric("unexpected_tool_call_count", len(unexpected), pass_when=not unexpected),
            count_metric("missing_required_tool_count", len(missing), pass_when=not missing),
        )


def _first_index(values: list[str], target: str) -> int:
    return values.index(target) if target in values else len(values) + 1


__all__ = ["ToolSelectionEvaluator"]
