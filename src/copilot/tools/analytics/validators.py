"""Integrity and numeric validators for deterministic analytics."""

from __future__ import annotations

import hashlib
import json
import math

from copilot.tools.analytics.exceptions import AnalyticsResultError
from copilot.tools.analytics.precision import normalize_decimal
from copilot.tools.analytics.schemas import AnalyticsMetric, AnalyticsResult


def canonical_checksum(value: object) -> str:
    """Hash JSON using the canonical representation shared with Database Tool evidence."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def checksum_matches(value: object, expected: str) -> bool:
    """Accept the repository's supported bare or ``sha256:``-prefixed digest forms."""
    digest = canonical_checksum(value)
    return expected in {digest, f"sha256:{digest}"}


def validate_result(result: AnalyticsResult) -> None:
    """Reject non-finite, out-of-range, or arithmetically inconsistent metrics."""
    for metric in result.metrics:
        for value in (metric.value, metric.numerator, metric.denominator):
            if isinstance(value, float) and not math.isfinite(value):
                raise AnalyticsResultError("Analytics result contains a non-finite number")
        if metric.metric is AnalyticsMetric.DEFECT_RATE:
            if metric.value is not None and not 0 <= metric.value <= 1:
                raise AnalyticsResultError("Defect rate must be between zero and one")
            if (
                metric.value is not None
                and isinstance(metric.numerator, (int, float))
                and isinstance(metric.denominator, (int, float))
                and metric.denominator != 0
                and metric.value
                != normalize_decimal(float(metric.numerator) / float(metric.denominator))
            ):
                raise AnalyticsResultError("Defect rate operands do not match its value")
        if metric.metric is AnalyticsMetric.PERIOD_OVER_PERIOD_TREND:
            if metric.value is not None and not -1 <= metric.value <= 1:
                raise AnalyticsResultError("Period trend must be between minus one and one")
            if (
                metric.value is not None
                and isinstance(metric.numerator, (int, float))
                and isinstance(metric.denominator, (int, float))
                and metric.value != normalize_decimal(metric.numerator - metric.denominator)
            ):
                raise AnalyticsResultError("Trend operands do not match its value")


__all__ = ["canonical_checksum", "checksum_matches", "validate_result"]
