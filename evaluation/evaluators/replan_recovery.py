"""Bounded replan recovery metrics; retry counts remain separate diagnostics."""

from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    MetricResult,
)
from evaluation.evaluators.base import count_metric, ratio_metric


class ReplanRecoveryEvaluator:
    name = "replan_recovery"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        expected = case.expected_recovery
        applicable = expected.required or execution.replan_count > 0
        task_ok = execution.terminal_task_status in case.expected_outcome.allowed_terminal_statuses
        recovered = applicable and execution.replan_count > 0 and task_ok
        retry_ok = (
            expected.expected_retry_count is None
            or execution.retry_count == expected.expected_retry_count
        )
        replan_ok = (
            expected.expected_replan_count is None
            or execution.replan_count == expected.expected_replan_count
        ) and (
            expected.max_replan_count is None or execution.replan_count <= expected.max_replan_count
        )
        recovered = recovered and replan_ok and retry_ok
        metrics = [
            ratio_metric("replan_recovery", int(recovered), int(applicable)),
            count_metric("replan_count", execution.replan_count, pass_when=replan_ok),
            count_metric("retry_count", execution.retry_count, pass_when=retry_ok),
            count_metric(
                "replan_exhausted_count",
                int(applicable and not recovered),
                pass_when=not applicable or recovered,
            ),
        ]
        return tuple(metrics)


__all__ = ["ReplanRecoveryEvaluator"]
