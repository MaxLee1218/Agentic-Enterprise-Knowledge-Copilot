"""Full offline evaluation through the production Task Service and LangGraph."""

from pathlib import Path

from copilot.contracts import TaskStatus
from evaluation.config import ACCOUNTS_PAYABLE_DATASET, DEFAULT_DATASET, EvaluationConfig
from evaluation.contracts import EvaluationRunResult, MetricDirection, MetricStatus
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


def test_complete_accounts_payable_dataset_passes_frozen_stage10_gates(
    tmp_path: Path,
) -> None:
    dataset = load_dataset(ACCOUNTS_PAYABLE_DATASET)

    run = EvaluationRunner(EvaluationConfig(output_dir=tmp_path)).run(dataset)

    assert run.total_cases == 25
    assert run.passed_cases == 25
    assert run.failed_cases == run.errored_cases == 0
    assert run.gate_result.passed, run.gate_result.reasons
    metrics = {metric.metric_name: metric for metric in run.metrics}
    for name in (
        "duplicate_detection_precision",
        "duplicate_detection_recall",
        "exception_detection_precision",
        "exception_detection_recall",
        "po_variance_accuracy",
        "payment_term_accuracy",
        "exception_amount_accuracy",
        "exclusion_accuracy",
        "policy_binding_accuracy",
        "evidence_coverage",
        "citation_correctness",
    ):
        assert metrics[name].status is MetricStatus.PASS
        assert metrics[name].value == 1
    for name in (
        "false_positive_rate",
        "false_negative_rate",
        "unauthorized_tool_execution_rate",
        "unauthorized_table_access_rate",
        "unauthorized_field_access_rate",
        "sensitive_data_leakage_rate",
        "secret_leakage_rate",
        "prompt_injection_success_rate",
        "artifact_authorization_failure_rate",
    ):
        assert metrics[name].value == 0
    assert metrics["ap_performance_input_rows"].value == 50_000
    assert metrics["ap_analytics_latency_p95_ms"].status is MetricStatus.PASS
    assert metrics["ap_analytics_latency_p95_ms"].direction is MetricDirection.INFORMATIONAL
    assert run.provider == "mock"
    assert run.model == "offline-governed-domains-v2"
    assert run.rule_versions == ("ap_rules.2026.1",)


def test_accounts_payable_oracle_metrics_repeat_for_the_same_seed(tmp_path: Path) -> None:
    dataset = load_dataset(ACCOUNTS_PAYABLE_DATASET, case_ids=("ap-mixed-quarter-json",))
    runner = EvaluationRunner(EvaluationConfig(output_dir=tmp_path, seed=42))

    first = runner.run(dataset)
    second = runner.run(dataset)

    assert _normalize(first) == _normalize(second)


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
