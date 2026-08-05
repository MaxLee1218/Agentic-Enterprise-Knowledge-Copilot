"""Full offline evaluation through the production Task Service and LangGraph."""

from copilot.contracts import TaskStatus
from evaluation.config import DEFAULT_DATASET, EvaluationConfig
from evaluation.contracts import EvaluationRunResult
from evaluation.dataset_loader import load_dataset
from evaluation.runner import EvaluationRunner


def test_all_required_supplier_quality_cases_pass_their_oracles(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dataset = load_dataset(DEFAULT_DATASET)

    run = EvaluationRunner(EvaluationConfig(output_dir=tmp_path)).run(dataset)

    assert run.total_cases == 30
    assert run.passed_cases == 30
    recovery = next(
        item for item in run.case_results if item.case_id == "report-verification-replan"
    )
    assert recovery.terminal_task_status is TaskStatus.COMPLETED
    replan_metric = next(
        item for item in recovery.metric_results if item.metric_name == "replan_count"
    )
    assert replan_metric.value == 1
    assert run.failed_cases == 0
    assert run.errored_cases == 0
    assert {case.category for case in dataset.cases} == {
        "normal",
        "clarification",
        "empty_data",
        "rag_failure",
        "tool_failure",
        "numeric_edge",
        "approval",
        "security",
        "authorization",
        "validation",
        "plan_repair",
        "registry",
    }
    assert any(
        result.terminal_task_status is TaskStatus.WAITING_APPROVAL for result in run.case_results
    )


def test_deterministic_fields_repeat_across_runs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dataset = load_dataset(DEFAULT_DATASET, tags=("smoke",))
    runner = EvaluationRunner(EvaluationConfig(output_dir=tmp_path))

    first = runner.run(dataset)
    second = runner.run(dataset)

    assert _normalize(first) == _normalize(second)
    assert first.gate_result == second.gate_result


def _normalize(run: EvaluationRunResult) -> object:
    return [
        (
            case.case_id,
            case.status,
            tuple(call.tool_name for call in case.tool_calls),
            case.failure_categories,
            tuple(
                (metric.metric_name, metric.value)
                for metric in case.metric_results
                if metric.metric_name != "latency_ms"
            ),
        )
        for case in run.case_results
    ]
