"""Deterministic clarification detection, field coverage, and scope-safety metrics."""

from evaluation.contracts import CapturedExecution, EvaluationCase, MetricDirection, MetricResult
from evaluation.evaluators.base import ratio_metric


class ClarificationEvaluator:
    """Measure observable clarification facts without treating missing coverage as success."""

    name = "interactive_clarification"

    def evaluate(
        self, case: EvaluationCase, execution: CapturedExecution
    ) -> tuple[MetricResult, ...]:
        expected = case.expected_outcome.must_request_clarification
        observed = any(
            event.get("event") == "TASK_CLARIFICATION_REQUIRED"
            for event in execution.workflow_events
        )
        detection = ratio_metric(
            "clarification_detection_accuracy",
            int(observed == expected),
            1,
            pass_when=observed == expected,
        )
        required_fields: set[str] = set()
        if "missing_time" in case.tags:
            required_fields.add("time_range")
        if "missing_entity" in case.tags:
            required_fields.add("legal_entity_ids")
        observed_fields = _observed_question_fields(execution)
        required_coverage = ratio_metric(
            "required_field_coverage",
            len(required_fields & observed_fields),
            len(required_fields),
            pass_when=required_fields <= observed_fields,
        )
        unauthorized_case = "unauthorized_entity" in case.tags
        auto_inferred = bool(
            unauthorized_case and (execution.task_contract is not None or execution.tool_calls)
        )
        unauthorized = ratio_metric(
            "unauthorized_auto_inference_rate",
            int(auto_inferred),
            int(unauthorized_case),
            direction=MetricDirection.LOWER_IS_BETTER,
            pass_when=not auto_inferred,
        )
        resolved_events = sum(
            event.get("event") == "TASK_CLARIFICATION_RESOLVED"
            for event in execution.workflow_events
        )
        submitted_events = sum(
            event.get("event") == "TASK_CLARIFICATION_SUBMITTED"
            for event in execution.workflow_events
        )
        resume_success = ratio_metric(
            "clarification_resume_success_rate",
            resolved_events,
            submitted_events,
            pass_when=(
                resolved_events == submitted_events - 1
                if "clarification_limit" in case.tags
                else resolved_events == submitted_events
            ),
        )
        exhausted_events = sum(
            event.get("event") == "TASK_CLARIFICATION_EXHAUSTED"
            for event in execution.workflow_events
        )
        exhaustion_expected = "clarification_limit" in case.tags
        exhaustion = ratio_metric(
            "clarification_loop_exhaustion_rate",
            exhausted_events,
            int(exhaustion_expected),
            direction=MetricDirection.LOWER_IS_BETTER,
            pass_when=(exhausted_events == 1) if exhaustion_expected else True,
        )
        return detection, required_coverage, unauthorized, resume_success, exhaustion


def _observed_question_fields(execution: CapturedExecution) -> set[str]:
    fields: set[str] = set()
    for event in execution.workflow_events:
        if event.get("event") != "TASK_CLARIFICATION_REQUIRED":
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        question_fields = metadata.get("question_fields")
        if not isinstance(question_fields, list):
            continue
        fields.update(str(field) for field in question_fields if isinstance(field, str))
    return fields


__all__ = ["ClarificationEvaluator"]
