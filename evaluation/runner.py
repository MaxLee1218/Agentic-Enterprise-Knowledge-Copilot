"""Sequential deterministic evaluation orchestration and metric aggregation."""

from __future__ import annotations

import platform
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from copilot import __version__
from evaluation.baseline import compare_baseline, load_baseline
from evaluation.config import EvaluationConfig
from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationRunResult,
    FailureCategory,
    GateResult,
    MetricDirection,
    MetricResult,
    MetricStatus,
)
from evaluation.dataset_loader import LoadedDataset, canonical_hash
from evaluation.evaluators import (
    EfficiencyEvaluator,
    GroundingEvaluator,
    NumericAccuracyEvaluator,
    PlanQualityEvaluator,
    ReplanRecoveryEvaluator,
    SafetyEvaluator,
    TaskSuccessEvaluator,
    ToolExecutionEvaluator,
    ToolSelectionEvaluator,
    UsageCostEvaluator,
)
from evaluation.evaluators.base import Evaluator
from evaluation.failure_classifier import classify_failures
from evaluation.harness import EvaluationHarness


class EvaluationRunner:
    """Execute isolated cases through production composition, then evaluate captured facts."""

    def __init__(self, config: EvaluationConfig) -> None:
        if config.max_workers != 1:
            raise ValueError("Concurrent evaluation is not implemented; use max_workers=1")
        self._config = config
        self._evaluators: tuple[Evaluator, ...] = (
            TaskSuccessEvaluator(),
            PlanQualityEvaluator(),
            ToolSelectionEvaluator(),
            ToolExecutionEvaluator(),
            GroundingEvaluator(),
            NumericAccuracyEvaluator(),
            SafetyEvaluator(),
            ReplanRecoveryEvaluator(),
            EfficiencyEvaluator(),
            UsageCostEvaluator(config.pricing),
        )

    def run(
        self,
        dataset: LoadedDataset,
        *,
        baseline_path: Path | None = None,
    ) -> EvaluationRunResult:
        started_at = datetime.now(UTC)
        config_hash = canonical_hash(self._config.model_dump(mode="json"))
        run_id = started_at.strftime("%Y%m%dT%H%M%S.%fZ") + f"-{config_hash[-8:]}"
        harness = EvaluationHarness(dataset_directory=dataset.path.parent, seed=self._config.seed)
        case_results: list[EvaluationCaseResult] = []
        captures: list[CapturedExecution] = []
        with tempfile.TemporaryDirectory(prefix="copilot-evaluation-") as temporary:
            temporary_root = Path(temporary)
            for case in dataset.cases:
                capture = harness.execute(case, temporary_root / case.case_id)
                captures.append(capture)
                case_results.append(self._evaluate_case(case, capture))
        metrics = _aggregate_metrics(tuple(case_results), tuple(captures))
        category_metrics = _category_metrics(tuple(case_results))
        completed_at = datetime.now(UTC)
        failures = Counter(
            category.value for result in case_results for category in result.failure_categories
        )
        passed = sum(item.status is EvaluationCaseStatus.PASSED for item in case_results)
        failed = sum(item.status is EvaluationCaseStatus.FAILED for item in case_results)
        errored = sum(item.status is EvaluationCaseStatus.ERRORED for item in case_results)
        skipped = sum(item.status is EvaluationCaseStatus.SKIPPED for item in case_results)
        reasons = tuple(
            reason
            for reason, present in (
                ("one or more evaluation cases failed", failed > 0),
                ("one or more evaluation cases errored", errored > 0),
            )
            if present
        )
        run = EvaluationRunResult(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            dataset_hash=dataset.dataset_hash,
            config_hash=config_hash,
            fixture_hash=dataset.fixture_hash,
            seed=self._config.seed,
            mode=self._config.mode,  # type: ignore[arg-type]
            git_commit=_git_commit(),
            python_version=platform.python_version(),
            platform=platform.platform(),
            agent_version=__version__,
            provider="mock" if self._config.mode == "mock" else "configured-live-provider",
            model=(
                self._config.pricing.model if self._config.pricing is not None else "not_available"
            ),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, round((completed_at - started_at).total_seconds() * 1000)),
            total_cases=len(case_results),
            passed_cases=passed,
            failed_cases=failed,
            errored_cases=errored,
            skipped_cases=skipped,
            metrics=metrics,
            category_metrics=category_metrics,
            case_results=tuple(case_results),
            failure_summary=dict(sorted(failures.items())),
            gate_result=GateResult(passed=not reasons, reasons=reasons),
        )
        if baseline_path is not None:
            comparison = compare_baseline(
                run,
                load_baseline(baseline_path),
                self._config.regression,
                baseline_path=baseline_path,
            )
            gate_reasons = (*run.gate_result.reasons, *comparison.regressions)
            run = run.model_copy(
                update={
                    "baseline_comparison": comparison,
                    "gate_result": GateResult(
                        passed=run.gate_result.passed and not comparison.regressions,
                        reasons=gate_reasons,
                    ),
                }
            )
        return run

    def _evaluate_case(
        self,
        case: EvaluationCase,
        execution: CapturedExecution,
    ) -> EvaluationCaseResult:
        metrics: list[MetricResult] = []
        diagnostics: list[str] = []
        evaluator_error = False
        for evaluator in self._evaluators:
            try:
                metrics.extend(evaluator.evaluate(case, execution))
            except Exception as exc:  # evaluator failures remain distinct from Agent outcomes
                evaluator_error = True
                diagnostics.append(f"{evaluator.name}: {type(exc).__name__}: {str(exc)[:300]}")
                metrics.append(
                    MetricResult(
                        metric_name=f"{evaluator.name}_internal_error",
                        unit="count",
                        direction=MetricDirection.LOWER_IS_BETTER,
                        value=Decimal(1),
                        numerator=Decimal(1),
                        denominator=Decimal(1),
                        coverage=Decimal(0),
                        status=MetricStatus.ERROR,
                    )
                )
        task_metric = next(item for item in metrics if item.metric_name == "task_success")
        assertion_failure = any(
            item.status in {MetricStatus.FAIL, MetricStatus.ERROR}
            for item in metrics
            if item.metric_name not in {"tool_execution_success_rate"}
        )
        if execution.harness_error or evaluator_error:
            status = EvaluationCaseStatus.ERRORED
        elif task_metric.status is MetricStatus.PASS and not assertion_failure:
            status = EvaluationCaseStatus.PASSED
        else:
            status = EvaluationCaseStatus.FAILED
        if status is EvaluationCaseStatus.PASSED:
            primary: FailureCategory | None = None
            categories: tuple[FailureCategory, ...] = ()
        else:
            primary, categories = classify_failures(execution, tuple(metrics))
        if execution.harness_error:
            diagnostics.append(execution.harness_error)
        evidence_counts = Counter(item.source_type.value for item in execution.evidence)
        return EvaluationCaseResult(
            case_id=case.case_id,
            category=case.category,
            tags=case.tags,
            status=status,
            task_id=execution.task_id,
            trace_id=execution.trace_id,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            latency_ms=execution.latency_ms,
            terminal_task_status=execution.terminal_task_status,
            task_request_text=execution.task_request_text,
            task_contract=execution.task_contract,
            plan_snapshot=execution.plan_snapshot,
            tool_calls=execution.tool_calls,
            tool_results=execution.tool_results,
            step_results=execution.step_results,
            evidence_summary={"total": len(execution.evidence), "by_type": dict(evidence_counts)},
            artifact_summary={"total": len(execution.artifacts)},
            verification_result=execution.verification_result,
            approvals=execution.approvals,
            approval_summary={
                "total": len(execution.approvals),
                "statuses": [item.status.value for item in execution.approvals],
            },
            errors=execution.errors,
            warnings=execution.warnings,
            workflow_events=execution.workflow_events,
            tool_audit_events=execution.tool_audit_events,
            artifact_authorization_probe=execution.artifact_authorization_probe,
            metric_results=tuple(metrics),
            primary_failure_category=primary,
            failure_categories=categories,
            diagnostics=tuple(diagnostics),
        )


def _aggregate_metrics(
    cases: tuple[EvaluationCaseResult, ...],
    captures: tuple[CapturedExecution, ...],
) -> tuple[MetricResult, ...]:
    results: list[MetricResult] = []
    results.append(_aggregate_ratio("overall_task_success_rate", cases, "task_success"))
    for output_name, source_name, direction in (
        ("initial_plan_validity", "initial_plan_validity", MetricDirection.HIGHER_IS_BETTER),
        ("final_plan_validity", "final_plan_validity", MetricDirection.HIGHER_IS_BETTER),
        ("plan_repair_success_rate", "plan_repair_success", MetricDirection.HIGHER_IS_BETTER),
        ("tool_selection_accuracy", "tool_selection_accuracy", MetricDirection.HIGHER_IS_BETTER),
        (
            "tool_execution_success_rate",
            "tool_execution_success_rate",
            MetricDirection.HIGHER_IS_BETTER,
        ),
        ("evidence_coverage", "evidence_coverage", MetricDirection.HIGHER_IS_BETTER),
        ("citation_correctness", "citation_correctness", MetricDirection.HIGHER_IS_BETTER),
        ("numeric_accuracy", "numeric_accuracy", MetricDirection.HIGHER_IS_BETTER),
        ("safety_violation_rate", "safety_violation_rate", MetricDirection.LOWER_IS_BETTER),
        ("attack_block_rate", "attack_block_rate", MetricDirection.HIGHER_IS_BETTER),
        ("authorization_block_rate", "authorization_block_rate", MetricDirection.HIGHER_IS_BETTER),
        (
            "unauthorized_tool_execution_rate",
            "unauthorized_tool_execution_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "unauthorized_table_access_rate",
            "unauthorized_table_access_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "unauthorized_field_access_rate",
            "unauthorized_field_access_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "sensitive_data_leakage_rate",
            "sensitive_data_leakage_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "secret_leakage_rate",
            "secret_leakage_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "prompt_injection_success_rate",
            "prompt_injection_success_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "artifact_authorization_failure_rate",
            "artifact_authorization_failure_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "missing_audit_event_rate",
            "missing_audit_event_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "unsafe_error_exposure_rate",
            "unsafe_error_exposure_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        (
            "legitimate_task_false_rejection_rate",
            "legitimate_task_false_rejection_rate",
            MetricDirection.LOWER_IS_BETTER,
        ),
        ("replan_recovery_rate", "replan_recovery", MetricDirection.HIGHER_IS_BETTER),
    ):
        results.append(_aggregate_ratio(output_name, cases, source_name, direction=direction))
    steps = [Decimal(len(capture.step_results)) for capture in captures]
    replans = [Decimal(capture.replan_count) for capture in captures]
    latencies = [Decimal(capture.latency_ms) for capture in captures]
    results.extend(
        (
            _observation(
                "average_steps_per_task", _mean(steps), "steps", MetricDirection.LOWER_IS_BETTER
            ),
            _observation(
                "median_steps_per_task",
                Decimal(str(median(steps))),
                "steps",
                MetricDirection.INFORMATIONAL,
            ),
            _observation(
                "min_steps", min(steps, default=Decimal(0)), "steps", MetricDirection.INFORMATIONAL
            ),
            _observation(
                "max_steps", max(steps, default=Decimal(0)), "steps", MetricDirection.INFORMATIONAL
            ),
            _observation(
                "average_replan_count", _mean(replans), "count", MetricDirection.LOWER_IS_BETTER
            ),
            _observation(
                "max_replan_count",
                max(replans, default=Decimal(0)),
                "count",
                MetricDirection.INFORMATIONAL,
            ),
            _observation(
                "replan_exhausted_count",
                _sum_named(cases, "replan_exhausted_count"),
                "count",
                MetricDirection.LOWER_IS_BETTER,
            ),
            _observation(
                "latency_average_ms",
                _mean(latencies),
                "milliseconds",
                MetricDirection.INFORMATIONAL,
            ),
            _observation(
                "latency_p50_ms",
                _percentile(latencies, Decimal("0.50")),
                "milliseconds",
                MetricDirection.INFORMATIONAL,
            ),
            _observation(
                "latency_p95_ms",
                _percentile(latencies, Decimal("0.95")),
                "milliseconds",
                MetricDirection.INFORMATIONAL,
            ),
            _observation(
                "latency_min_ms",
                min(latencies, default=Decimal(0)),
                "milliseconds",
                MetricDirection.INFORMATIONAL,
            ),
            _observation(
                "latency_max_ms",
                max(latencies, default=Decimal(0)),
                "milliseconds",
                MetricDirection.INFORMATIONAL,
            ),
        )
    )
    usage_cases = [capture for capture in captures if capture.llm_usage]
    input_tokens = sum(item.input_tokens for capture in usage_cases for item in capture.llm_usage)
    output_tokens = sum(item.output_tokens for capture in usage_cases for item in capture.llm_usage)
    total_tokens = sum(item.total_tokens for capture in usage_cases for item in capture.llm_usage)
    usage_coverage = Decimal(len(usage_cases)) / Decimal(len(captures)) if captures else Decimal(0)
    results.extend(
        (
            _observation(
                "total_input_tokens",
                Decimal(input_tokens) if usage_cases else None,
                "tokens",
                MetricDirection.INFORMATIONAL,
                usage_coverage,
            ),
            _observation(
                "total_output_tokens",
                Decimal(output_tokens) if usage_cases else None,
                "tokens",
                MetricDirection.INFORMATIONAL,
                usage_coverage,
            ),
            _observation(
                "total_tokens",
                Decimal(total_tokens) if usage_cases else None,
                "tokens",
                MetricDirection.INFORMATIONAL,
                usage_coverage,
            ),
            _observation(
                "average_tokens_per_task",
                Decimal(total_tokens) / Decimal(len(usage_cases)) if usage_cases else None,
                "tokens",
                MetricDirection.INFORMATIONAL,
                usage_coverage,
            ),
            _observation(
                "token_usage_coverage",
                usage_coverage,
                "ratio",
                MetricDirection.INFORMATIONAL,
                usage_coverage,
            ),
        )
    )
    cost_values = [
        metric.value
        for case in cases
        for metric in case.metric_results
        if metric.metric_name == "estimated_cost" and metric.value is not None
    ]
    cost_coverage = Decimal(len(cost_values)) / Decimal(len(cases)) if cases else Decimal(0)
    total_cost = sum(cost_values, Decimal(0))
    results.extend(
        (
            _observation(
                "estimated_total_cost",
                total_cost if cost_values else None,
                "USD",
                MetricDirection.INFORMATIONAL,
                cost_coverage,
            ),
            _observation(
                "estimated_average_cost_per_task",
                total_cost / Decimal(len(cost_values)) if cost_values else None,
                "USD",
                MetricDirection.INFORMATIONAL,
                cost_coverage,
            ),
            _observation(
                "cost_coverage",
                cost_coverage,
                "ratio",
                MetricDirection.INFORMATIONAL,
                cost_coverage,
            ),
        )
    )
    return tuple(results)


def _category_metrics(
    cases: tuple[EvaluationCaseResult, ...],
) -> dict[str, tuple[MetricResult, ...]]:
    grouped: defaultdict[str, list[EvaluationCaseResult]] = defaultdict(list)
    for case in cases:
        grouped[case.category].append(case)
    return {
        category: (
            _aggregate_ratio(
                "task_success_by_category",
                tuple(items),
                "task_success",
            ),
        )
        for category, items in sorted(grouped.items())
    }


def _aggregate_ratio(
    name: str,
    cases: tuple[EvaluationCaseResult, ...],
    source_name: str,
    *,
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER,
) -> MetricResult:
    items = [
        metric
        for case in cases
        for metric in case.metric_results
        if metric.metric_name == source_name and metric.denominator is not None
    ]
    numerator = sum((item.numerator or Decimal(0) for item in items), Decimal(0))
    denominator = sum((item.denominator or Decimal(0) for item in items), Decimal(0))
    if denominator == 0:
        return MetricResult(
            metric_name=name,
            unit="ratio",
            direction=direction,
            coverage=Decimal(0),
            status=MetricStatus.NOT_AVAILABLE,
        )
    value = numerator / denominator
    return MetricResult(
        metric_name=name,
        value=value,
        numerator=numerator,
        denominator=denominator,
        unit="ratio",
        direction=direction,
        coverage=Decimal(len(items)) / Decimal(len(cases)) if cases else Decimal(0),
        status=MetricStatus.PASS,
    )


def _observation(
    name: str,
    value: Decimal | None,
    unit: str,
    direction: MetricDirection,
    coverage: Decimal = Decimal(1),
) -> MetricResult:
    return MetricResult(
        metric_name=name,
        value=value,
        numerator=value,
        denominator=Decimal(1) if value is not None else None,
        unit=unit,
        direction=direction,
        coverage=coverage,
        status=(
            MetricStatus.PASS if coverage > 0 and value is not None else MetricStatus.NOT_AVAILABLE
        ),
    )


def _sum_named(cases: tuple[EvaluationCaseResult, ...], name: str) -> Decimal:
    return sum(
        (
            metric.value or Decimal(0)
            for case in cases
            for metric in case.metric_results
            if metric.metric_name == name
        ),
        Decimal(0),
    )


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else Decimal(0)


def _percentile(values: list[Decimal], quantile: Decimal) -> Decimal:
    if not values:
        return Decimal(0)
    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            int((Decimal(len(ordered)) * quantile).to_integral_value(rounding="ROUND_CEILING")) - 1,
        ),
    )
    return ordered[index]


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


__all__ = ["EvaluationRunner"]
