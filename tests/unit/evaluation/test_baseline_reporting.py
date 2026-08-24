"""Direction-aware baseline and safe report tests."""

import json
from decimal import Decimal
from pathlib import Path

from evaluation.baseline import baseline_from_run, compare_baseline
from evaluation.config import DEFAULT_DATASET, EvaluationConfig, RegressionConfig
from evaluation.contracts import EvaluationCaseStatus, FailureCategory
from evaluation.dataset_loader import load_dataset
from evaluation.reporting import write_reports
from evaluation.runner import EvaluationRunner


def test_baseline_compares_higher_and_lower_metrics(tmp_path: Path) -> None:
    dataset = load_dataset(DEFAULT_DATASET, tags=("smoke",))
    config = EvaluationConfig(output_dir=tmp_path)
    run = EvaluationRunner(config).run(dataset)
    baseline = baseline_from_run(run)

    degraded_metrics = tuple(
        metric.model_copy(
            update={
                "value": (
                    Decimal("0.9")
                    if metric.metric_name == "overall_task_success_rate"
                    else Decimal("0.1")
                ),
                "numerator": (
                    Decimal("0.9")
                    if metric.metric_name == "overall_task_success_rate"
                    else Decimal("0.1")
                ),
            }
        )
        if metric.metric_name in {"overall_task_success_rate", "safety_violation_rate"}
        else metric
        for metric in run.metrics
    )
    degraded = run.model_copy(update={"metrics": degraded_metrics})

    comparison = compare_baseline(
        degraded,
        baseline,
        RegressionConfig(),
        baseline_path=tmp_path / "baseline.json",
    )

    assert comparison.compatible
    assert any("overall_task_success_rate regressed" in item for item in comparison.regressions)
    assert any("safety_violation_rate regressed" in item for item in comparison.regressions)

    subset = run.model_copy(update={"case_results": run.case_results[:-1]})
    incompatible = compare_baseline(
        subset,
        baseline,
        RegressionConfig(),
        baseline_path=tmp_path / "baseline.json",
    )
    assert not incompatible.compatible


def test_baseline_does_not_regress_when_metric_is_unavailable_on_both_runs(
    tmp_path: Path,
) -> None:
    dataset = load_dataset(DEFAULT_DATASET, case_ids=("prompt-injection-attempt",))
    run = EvaluationRunner(EvaluationConfig(output_dir=tmp_path)).run(dataset)

    comparison = compare_baseline(
        run,
        baseline_from_run(run),
        RegressionConfig(),
        baseline_path=tmp_path / "baseline.json",
    )

    assert comparison.compatible
    assert comparison.regressions == ()


def test_reporting_writes_required_markdown_sections_and_redacts_secrets(
    tmp_path: Path,
) -> None:
    dataset = load_dataset(DEFAULT_DATASET, case_ids=("prompt-injection-attempt",))
    run = EvaluationRunner(EvaluationConfig(output_dir=tmp_path)).run(dataset)

    write_reports(
        run,
        {case.case_id: case for case in dataset.cases},
        tmp_path,
        update_latest=True,
    )

    markdown = (tmp_path / "latest.md").read_text(encoding="utf-8")
    json_report = (tmp_path / "latest.json").read_text(encoding="utf-8")
    parsed_report = type(run).model_validate_json(json_report)
    run_directories = tuple((tmp_path / "runs").iterdir())
    assert len(run_directories) == 1
    manifest = json.loads((run_directories[0] / "manifest.json").read_text(encoding="utf-8"))
    assert "## Quality Gate" in markdown
    assert "## Baseline Comparison" in markdown
    assert "## Reproduction Command" in markdown
    assert "api-key-secret" not in json_report
    assert parsed_report.category_metrics == run.category_metrics
    assert manifest["dataset_hash"] == run.dataset_hash
    assert manifest["fixture_hash"] == run.fixture_hash
    assert manifest["config_hash"] == run.config_hash
    assert manifest["source_hash"] == run.source_hash
    assert manifest["git_dirty"] == run.git_dirty
    assert manifest["configuration"] == run.configuration
    assert manifest["provider"] == run.provider
    assert manifest["model"] == run.model
    assert manifest["prompt_versions"]
    assert manifest["profile_versions"]
    assert manifest["report_versions"]
    assert manifest["metric_definitions"]
    assert manifest["known_limitations"]


def test_failure_report_contains_execution_histories(tmp_path: Path) -> None:
    dataset = load_dataset(DEFAULT_DATASET, case_ids=("normal-q2-analysis",))
    run = EvaluationRunner(EvaluationConfig(output_dir=tmp_path)).run(dataset)
    original = run.case_results[0]
    failed = original.model_copy(
        update={
            "status": EvaluationCaseStatus.FAILED,
            "primary_failure_category": FailureCategory.NUMERIC,
            "failure_categories": (FailureCategory.NUMERIC,),
        }
    )
    failed_run = run.model_copy(update={"case_results": (failed,)})

    run_directory = write_reports(
        failed_run,
        {case.case_id: case for case in dataset.cases},
        tmp_path,
        update_latest=False,
    )

    payload = json.loads(
        (run_directory / "failures" / "normal-q2-analysis.json").read_text(encoding="utf-8")
    )
    assert payload["task_contract"] is not None
    assert payload["plan_validation"]
    assert payload["tool_calls"]
    assert payload["tool_results"]
    assert "retry_history" in payload
    assert "replan_history" in payload
    assert "approval_history" in payload
