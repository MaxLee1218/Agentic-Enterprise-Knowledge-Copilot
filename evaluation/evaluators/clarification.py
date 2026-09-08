"""Deterministic clarification detection, field coverage, and scope-safety metrics."""

from collections.abc import Mapping

from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    MetricDirection,
    MetricResult,
)
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
        unresolved_expected = bool({"clarification_limit", "unauthorized_entity"} & set(case.tags))
        expected_nonresolution_observed = (
            unresolved_expected and resolved_events == submitted_events - 1
        )
        resume_success = ratio_metric(
            "clarification_resume_success_rate",
            resolved_events + int(expected_nonresolution_observed),
            submitted_events,
            pass_when=(
                resolved_events == submitted_events - 1
                if unresolved_expected
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
        natural_metrics = _natural_resolution_metrics(case, execution)
        return (
            detection,
            required_coverage,
            unauthorized,
            resume_success,
            exhaustion,
            *natural_metrics,
        )


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


def _natural_resolution_metrics(
    case: EvaluationCase,
    execution: CapturedExecution,
) -> tuple[MetricResult, ...]:
    events = execution.workflow_events
    normalized_fields = {
        str(metadata.get("field"))
        for event in events
        if event.get("event") == "TASK_FIELD_NORMALIZED"
        and isinstance((metadata := event.get("metadata")), dict)
    }
    for event in events:
        if event.get("event") != "TASK_CLARIFICATION_REQUIRED":
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        resolutions = metadata.get("field_resolutions")
        if not isinstance(resolutions, list):
            continue
        normalized_fields.update(
            str(item.get("field"))
            for item in resolutions
            if isinstance(item, dict)
            and item.get("status") in {"EXACT", "NORMALIZED", "CONFIRMATION_REQUIRED"}
        )
    expected_fields: set[str] = set()
    if "normalize_time" in case.tags:
        expected_fields.add("time_range")
    if "normalize_entity" in case.tags:
        expected_fields.add("legal_entity_ids")
    normalization = ratio_metric(
        "normalization_accuracy",
        len(normalized_fields & expected_fields),
        len(expected_fields),
        pass_when=expected_fields <= normalized_fields,
    )
    field_resolution = ratio_metric(
        "field_resolution_accuracy",
        len(normalized_fields & expected_fields),
        len(expected_fields),
        pass_when=expected_fields <= normalized_fields,
    )
    required_events = [
        event for event in events if event.get("event") == "TASK_CLARIFICATION_REQUIRED"
    ]
    ambiguous_observed = any(
        _metadata_value(event, "clarification_kind") == "AMBIGUITY_RESOLUTION"
        for event in required_events
    )
    ambiguity_expected = "ambiguity" in case.tags
    ambiguity = ratio_metric(
        "ambiguity_detection_accuracy",
        int(ambiguous_observed == ambiguity_expected),
        1,
        pass_when=ambiguous_observed == ambiguity_expected,
    )
    unauthorized_case = "unauthorized_entity" in case.tags
    unauthorized_accepted = bool(
        unauthorized_case and (execution.task_contract is not None or execution.tool_calls)
    )
    unauthorized_resolution = ratio_metric(
        "unauthorized_resolution_rate",
        int(unauthorized_accepted),
        int(unauthorized_case),
        direction=MetricDirection.LOWER_IS_BETTER,
        pass_when=not unauthorized_accepted,
    )
    carry_time = "carry_forward_time" in case.tags
    carry_entity = "carry_forward_entity" in case.tags
    carry_forward_expected = carry_time or carry_entity
    last_fields = _question_fields(required_events[-1]) if required_events else set()
    carry_forward_ok = not carry_forward_expected or (
        (not carry_time or ("time_range" not in last_fields and "legal_entity_ids" in last_fields))
        and (
            not carry_entity
            or ("legal_entity_ids" not in last_fields and "time_range" in last_fields)
        )
    )
    carry_forward = ratio_metric(
        "clarification_field_carry_forward_accuracy",
        int(carry_forward_ok),
        int(carry_forward_expected),
        pass_when=carry_forward_ok,
    )
    regression = ratio_metric(
        "resolved_field_regression_rate",
        int(carry_forward_expected and not carry_forward_ok),
        int(carry_forward_expected),
        direction=MetricDirection.LOWER_IS_BETTER,
        pass_when=carry_forward_ok,
    )
    expected_confirmation_event = (
        "TASK_INTERPRETATION_CORRECTED"
        if "confirmation_correction" in case.tags
        else (
            "TASK_INTERPRETATION_REJECTED"
            if "confirmation_rejection" in case.tags
            else "TASK_INTERPRETATION_CONFIRMED"
        )
    )
    confirmation_expected = bool(
        {"confirmation", "confirmation_correction", "confirmation_rejection"} & set(case.tags)
    )
    confirmation_observed = any(
        event.get("event") == expected_confirmation_event for event in events
    )
    confirmation = ratio_metric(
        "confirmation_resolution_accuracy",
        int(confirmation_observed == confirmation_expected),
        1,
        pass_when=confirmation_observed == confirmation_expected,
    )
    return (
        field_resolution,
        normalization,
        ambiguity,
        unauthorized_resolution,
        carry_forward,
        regression,
        confirmation,
    )


def _question_fields(event: Mapping[str, object]) -> set[str]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return set()
    fields = metadata.get("question_fields")
    if not isinstance(fields, list):
        return set()
    return {str(item) for item in fields}


def _metadata_value(event: Mapping[str, object], key: str) -> object:
    metadata = event.get("metadata")
    return metadata.get(key) if isinstance(metadata, dict) else None


__all__ = ["ClarificationEvaluator"]
