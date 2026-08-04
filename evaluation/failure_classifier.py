"""Stable multi-label evaluation failure classification."""

from __future__ import annotations

from evaluation.contracts import (
    CapturedExecution,
    FailureCategory,
    MetricResult,
    MetricStatus,
)

_METRIC_CATEGORIES = {
    "task_success": FailureCategory.TASK_UNDERSTANDING,
    "initial_plan_validity": FailureCategory.PLAN_INVALID,
    "final_plan_validity": FailureCategory.PLAN_INVALID,
    "plan_repair_success": FailureCategory.PLAN_REPAIR,
    "tool_selection_accuracy": FailureCategory.TOOL_SELECTION,
    "evidence_coverage": FailureCategory.EVIDENCE,
    "citation_correctness": FailureCategory.CITATION,
    "numeric_accuracy": FailureCategory.NUMERIC,
    "safety_violation_rate": FailureCategory.SAFETY,
    "replan_recovery": FailureCategory.REPLAN_FAILED,
}

_ERROR_CATEGORIES = {
    "TASK_INFORMATION_MISSING": FailureCategory.CLARIFICATION,
    "PLAN_INVALID": FailureCategory.PLAN_INVALID,
    "TOOL_NOT_REGISTERED": FailureCategory.PLAN_INVALID,
    "DATABASE_UNAVAILABLE": FailureCategory.TOOL_EXECUTION,
    "KNOWLEDGE_UNAVAILABLE": FailureCategory.TOOL_EXECUTION,
    "APPROVAL_REJECTED": FailureCategory.APPROVAL,
    "TASK_DEADLINE_EXCEEDED": FailureCategory.TIMEOUT,
}

_PRECEDENCE = tuple(FailureCategory)


def classify_failures(
    execution: CapturedExecution,
    metrics: tuple[MetricResult, ...],
) -> tuple[FailureCategory | None, tuple[FailureCategory, ...]]:
    """Return deterministic primary and ordered secondary failure categories."""
    categories: set[FailureCategory] = set()
    if execution.harness_error:
        categories.add(FailureCategory.HARNESS_SETUP)
    for error in execution.errors:
        categories.add(_ERROR_CATEGORIES.get(error.error_code, FailureCategory.UNEXPECTED_INTERNAL))
    for metric in metrics:
        if metric.status in {MetricStatus.FAIL, MetricStatus.ERROR}:
            categories.add(
                _METRIC_CATEGORIES.get(metric.metric_name, FailureCategory.EVALUATOR_INTERNAL)
            )
    ordered = tuple(item for item in _PRECEDENCE if item in categories)
    return (ordered[0] if ordered else None), ordered


__all__ = ["classify_failures"]
