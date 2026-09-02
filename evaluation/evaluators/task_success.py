"""Expected-outcome-aware task success evaluation."""

from evaluation.contracts import CapturedExecution, EvaluationCase, MetricResult
from evaluation.evaluators.base import ratio_metric


class TaskSuccessEvaluator:
    name = "task_success"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        expected = case.expected_outcome
        status_ok = execution.terminal_task_status in expected.allowed_terminal_statuses
        if expected.required_terminal_status is not None:
            status_ok = execution.terminal_task_status is expected.required_terminal_status
        if (
            expected.must_request_clarification
            and execution.terminal_task_status is not None
            and execution.terminal_task_status.value == "WAITING_CLARIFICATION"
        ):
            # Compatibility for frozen pre-clarification datasets whose success oracle encoded
            # the old FAILED transport outcome. The interaction event below remains mandatory.
            status_ok = True
        artifact_ok = not expected.must_generate_artifact or bool(execution.artifacts)
        clarification_ok = not expected.must_request_clarification or any(
            event.get("event") == "TASK_CLARIFICATION_REQUIRED"
            for event in execution.workflow_events
        )
        approval_ok = not expected.must_require_approval or bool(execution.approvals)
        tools_ok = not expected.must_not_execute_tools or not execution.tool_calls
        error_ok = not expected.allowed_error_codes or all(
            error.error_code in expected.allowed_error_codes for error in execution.errors
        )
        warning_ok = all(
            any(required in actual for actual in execution.warnings)
            for required in expected.required_warnings
        )
        passed = (
            execution.harness_error is None
            and status_ok
            and artifact_ok
            and clarification_ok
            and approval_ok
            and tools_ok
            and error_ok
            and warning_ok
        )
        notes = tuple(
            name
            for name, ok in (
                ("terminal status mismatch", status_ok),
                ("artifact expectation mismatch", artifact_ok),
                ("clarification expectation mismatch", clarification_ok),
                ("approval expectation mismatch", approval_ok),
                ("unexpected tool execution", tools_ok),
                ("unexpected error code", error_ok),
                ("required warning missing", warning_ok),
            )
            if not ok
        )
        return (ratio_metric("task_success", int(passed), 1, pass_when=passed, notes=notes),)


__all__ = ["TaskSuccessEvaluator"]
