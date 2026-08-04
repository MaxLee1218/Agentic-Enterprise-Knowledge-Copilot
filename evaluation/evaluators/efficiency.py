"""Per-case executed-step and end-to-end latency observations."""

from decimal import Decimal

from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    MetricDirection,
    MetricResult,
    MetricStatus,
)


class EfficiencyEvaluator:
    name = "efficiency"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        del case
        executed_steps = sum(record.status.value == "SUCCESS" for record in execution.step_results)
        return (
            MetricResult(
                metric_name="steps_per_task",
                value=Decimal(executed_steps),
                numerator=Decimal(executed_steps),
                denominator=Decimal(1),
                unit="steps",
                direction=MetricDirection.LOWER_IS_BETTER,
                coverage=Decimal(1),
                status=MetricStatus.PASS,
            ),
            MetricResult(
                metric_name="latency_ms",
                value=Decimal(execution.latency_ms),
                numerator=Decimal(execution.latency_ms),
                denominator=Decimal(1),
                unit="milliseconds",
                direction=MetricDirection.INFORMATIONAL,
                coverage=Decimal(1),
                status=MetricStatus.PASS,
            ),
        )


__all__ = ["EfficiencyEvaluator"]
