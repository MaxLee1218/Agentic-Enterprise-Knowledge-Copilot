"""Provider-reported token usage and versioned configurable cost estimation."""

from decimal import Decimal

from evaluation.config import PricingConfig
from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    MetricDirection,
    MetricResult,
    MetricStatus,
)


class UsageCostEvaluator:
    name = "usage_cost"

    def __init__(self, pricing: PricingConfig | None) -> None:
        self._pricing = pricing

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        del case
        usage = execution.llm_usage
        if not usage:
            unavailable = MetricResult(
                metric_name="token_usage",
                unit="tokens",
                direction=MetricDirection.INFORMATIONAL,
                coverage=Decimal(0),
                status=MetricStatus.NOT_AVAILABLE,
                notes=("Provider usage was not available",),
            )
            unavailable_cost = unavailable.model_copy(
                update={"metric_name": "estimated_cost", "unit": "currency"}
            )
            return (unavailable, unavailable_cost)
        input_tokens = sum(item.input_tokens for item in usage)
        output_tokens = sum(item.output_tokens for item in usage)
        total_tokens = sum(item.total_tokens for item in usage)
        token_metric = MetricResult(
            metric_name="token_usage",
            value=Decimal(total_tokens),
            numerator=Decimal(total_tokens),
            denominator=Decimal(1),
            unit="tokens",
            direction=MetricDirection.INFORMATIONAL,
            coverage=Decimal(1),
            status=MetricStatus.PASS,
            notes=(f"input={input_tokens}", f"output={output_tokens}"),
        )
        if self._pricing is None:
            cost_metric = MetricResult(
                metric_name="estimated_cost",
                unit="currency",
                direction=MetricDirection.INFORMATIONAL,
                coverage=Decimal(0),
                status=MetricStatus.NOT_AVAILABLE,
                notes=("Pricing was not configured",),
            )
        else:
            estimated_cost = (
                Decimal(input_tokens) * self._pricing.input_cost_per_million_tokens
                + Decimal(output_tokens) * self._pricing.output_cost_per_million_tokens
            ) / Decimal(1_000_000)
            cost_metric = MetricResult(
                metric_name="estimated_cost",
                value=estimated_cost,
                numerator=estimated_cost,
                denominator=Decimal(1),
                unit=self._pricing.currency,
                direction=MetricDirection.INFORMATIONAL,
                coverage=Decimal(1),
                status=MetricStatus.PASS,
                notes=(f"pricing_version={self._pricing.version}",),
            )
        return token_metric, cost_metric


__all__ = ["UsageCostEvaluator"]
