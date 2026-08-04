"""Common deterministic evaluator protocol and result constructors."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    MetricDirection,
    MetricResult,
    MetricStatus,
)


class Evaluator(Protocol):
    name: str

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]: ...


def ratio_metric(
    name: str,
    numerator: int,
    denominator: int,
    *,
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER,
    pass_when: bool | None = None,
    notes: tuple[str, ...] = (),
) -> MetricResult:
    """Build a ratio and preserve zero-denominator missingness."""
    if denominator == 0:
        return MetricResult(
            metric_name=name,
            unit="ratio",
            direction=direction,
            status=MetricStatus.NOT_AVAILABLE,
            coverage=Decimal("0"),
            notes=notes or ("No applicable denominator",),
        )
    value = Decimal(numerator) / Decimal(denominator)
    return MetricResult(
        metric_name=name,
        value=value,
        numerator=Decimal(numerator),
        denominator=Decimal(denominator),
        unit="ratio",
        direction=direction,
        coverage=Decimal("1"),
        status=MetricStatus.PASS
        if (pass_when if pass_when is not None else value == 1)
        else MetricStatus.FAIL,
        notes=notes,
    )


def count_metric(name: str, value: int, *, pass_when: bool = True) -> MetricResult:
    return MetricResult(
        metric_name=name,
        value=Decimal(value),
        numerator=Decimal(value),
        denominator=Decimal(1),
        unit="count",
        direction=MetricDirection.INFORMATIONAL,
        coverage=Decimal("1"),
        status=MetricStatus.PASS if pass_when else MetricStatus.FAIL,
    )


__all__ = ["Evaluator", "count_metric", "ratio_metric"]
