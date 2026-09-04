"""Deterministic evaluation of chat-first natural-language domain resolution."""

from copilot.services.task_intake import (
    TaskDomainResolutionStatus,
    resolve_task_domain,
)
from evaluation.contracts import CapturedExecution, EvaluationCase, MetricResult
from evaluation.evaluators.base import ratio_metric


class DomainResolutionEvaluator:
    """Check that raw browser-equivalent text resolves without caller-purpose fallback."""

    name = "domain_resolution"

    def evaluate(
        self,
        case: EvaluationCase,
        execution: CapturedExecution,
    ) -> tuple[MetricResult, ...]:
        del execution
        observed = resolve_task_domain(case.task_input.raw_input)
        if "expect_ambiguous_domain" in case.tags:
            correct = observed.status is TaskDomainResolutionStatus.AMBIGUOUS
        elif "expect_unsupported_domain" in case.tags:
            correct = observed.status is TaskDomainResolutionStatus.UNSUPPORTED
        else:
            correct = (
                observed.status is TaskDomainResolutionStatus.RESOLVED
                and observed.task_type is case.task_input.task_type
            )
        return (
            ratio_metric(
                "domain_resolution_accuracy",
                int(correct),
                1,
                pass_when=correct,
            ),
        )


__all__ = ["DomainResolutionEvaluator"]
