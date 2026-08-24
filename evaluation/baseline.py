"""Explicit baseline persistence and direction-aware regression comparison."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from evaluation.config import RegressionConfig
from evaluation.contracts import (
    BaselineComparison,
    BaselineMetric,
    EvaluationBaseline,
    EvaluationRunResult,
    MetricDirection,
)


def load_baseline(path: Path) -> EvaluationBaseline:
    return EvaluationBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def compare_baseline(
    run: EvaluationRunResult,
    baseline: EvaluationBaseline,
    config: RegressionConfig,
    *,
    baseline_path: Path,
) -> BaselineComparison:
    """Compare only compatible datasets using centralized direction and tolerances."""
    if (
        run.dataset_id != baseline.dataset_id
        or run.dataset_version != baseline.dataset_version
        or run.dataset_hash != baseline.dataset_hash
        or run.seed != baseline.seed
        or {item.case_id for item in run.case_results} != set(baseline.case_outcomes)
    ):
        return BaselineComparison(
            baseline_path=str(baseline_path),
            compatible=False,
            regressions=("Baseline dataset/version/hash/seed/case selection is incompatible",),
        )
    current = {item.metric_name: item for item in run.metrics}
    regressions: list[str] = []
    missing: list[str] = []
    for name, reference in baseline.metrics.items():
        actual = current.get(name)
        if actual is None:
            missing.append(name)
            if name in config.hard_gate_metrics:
                regressions.append(f"Required metric is unavailable: {name}")
            continue
        if actual.value is None:
            missing.append(name)
            if reference.value is not None and name in config.hard_gate_metrics:
                regressions.append(f"Required metric is unavailable: {name}")
            continue
        if reference.value is None or reference.direction is MetricDirection.INFORMATIONAL:
            continue
        if (
            actual.coverage is not None
            and actual.coverage < config.minimum_coverage
            and reference.coverage is not None
            and reference.coverage >= config.minimum_coverage
        ):
            if name in config.hard_gate_metrics:
                regressions.append(f"Metric coverage is below the gate: {name}")
            continue
        tolerance = config.tolerances.get(name, reference.tolerance)
        if (
            reference.direction is MetricDirection.HIGHER_IS_BETTER
            and actual.value < reference.value - tolerance
        ) or (
            reference.direction is MetricDirection.LOWER_IS_BETTER
            and actual.value > reference.value + tolerance
        ):
            regressions.append(f"{name} regressed from {reference.value} to {actual.value}")
    return BaselineComparison(
        baseline_path=str(baseline_path),
        compatible=True,
        regressions=tuple(regressions),
        missing_metrics=tuple(missing),
    )


def baseline_from_run(run: EvaluationRunResult) -> EvaluationBaseline:
    return EvaluationBaseline(
        dataset_id=run.dataset_id,
        dataset_version=run.dataset_version,
        dataset_hash=run.dataset_hash,
        seed=run.seed,
        agent_version=run.agent_version,
        git_commit=run.git_commit,
        source_hash=run.source_hash,
        git_dirty=run.git_dirty,
        metrics={
            metric.metric_name: BaselineMetric(
                value=metric.value,
                direction=metric.direction,
                coverage=metric.coverage,
            )
            for metric in run.metrics
        },
        case_outcomes={item.case_id: item.status.value for item in run.case_results},
        known_failures=tuple(
            item.case_id for item in run.case_results if item.status.value != "passed"
        ),
        created_at=datetime.now(UTC),
    )


def write_baseline(baseline: EvaluationBaseline, path: Path) -> None:
    """Write a baseline only when explicitly called by the CLI."""
    _atomic_json(path, baseline.model_dump(mode="json"))


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "baseline_from_run",
    "compare_baseline",
    "load_baseline",
    "write_baseline",
]
