"""Raw governed tool-attempt success metrics."""

from copilot.contracts import ToolResultStatus
from evaluation.contracts import CapturedExecution, EvaluationCase, MetricResult
from evaluation.evaluators.base import ratio_metric


class ToolExecutionEvaluator:
    name = "tool_execution"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        del case
        successful = sum(
            result.status is ToolResultStatus.SUCCESS for result in execution.tool_results
        )
        return (
            ratio_metric(
                "tool_execution_success_rate",
                successful,
                len(execution.tool_results),
                pass_when=True,
            ),
        )


__all__ = ["ToolExecutionEvaluator"]
