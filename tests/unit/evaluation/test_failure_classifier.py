"""Stable multi-label failure classification tests."""

from datetime import UTC, datetime
from decimal import Decimal

from evaluation.contracts import (
    CapturedExecution,
    FailureCategory,
    MetricDirection,
    MetricResult,
    MetricStatus,
)
from evaluation.failure_classifier import classify_failures


def test_classifier_keeps_harness_and_evaluator_failures_distinct() -> None:
    capture = CapturedExecution(
        started_at=datetime(2026, 8, 3, tzinfo=UTC),
        completed_at=datetime(2026, 8, 3, tzinfo=UTC),
        latency_ms=0,
        task_request_text="synthetic failure",
        harness_error="setup failed",
    )
    metric = MetricResult(
        metric_name="numeric_accuracy",
        value=Decimal(0),
        numerator=Decimal(0),
        denominator=Decimal(1),
        unit="ratio",
        direction=MetricDirection.HIGHER_IS_BETTER,
        coverage=Decimal(1),
        status=MetricStatus.FAIL,
    )

    primary, categories = classify_failures(capture, (metric,))

    assert primary is FailureCategory.HARNESS_SETUP
    assert categories == (FailureCategory.HARNESS_SETUP, FailureCategory.NUMERIC)
