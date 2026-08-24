"""Human-readable evaluation report with explicit numerators and denominators."""

from __future__ import annotations

from decimal import Decimal

from evaluation.contracts import EvaluationRunResult, MetricResult


def render_markdown(run: EvaluationRunResult) -> str:
    metrics = "\n".join(f"- {_metric_line(item)}" for item in run.metrics)
    cases = "\n".join(
        f"- `{item.case_id}` ({item.category}): {item.status.value}; "
        f"terminal={item.terminal_task_status.value if item.terminal_task_status else 'n/a'}"
        for item in run.case_results
    )
    failed = (
        "\n".join(
            f"- `{item.case_id}`: {item.primary_failure_category or 'unclassified'}"
            for item in run.case_results
            if item.status.value != "passed"
        )
        or "- None"
    )
    safety = _named_lines(
        run,
        (
            "safety_violation_rate",
            "unauthorized_tool_execution_rate",
            "unauthorized_table_access_rate",
            "unauthorized_field_access_rate",
            "sensitive_data_leakage_rate",
            "secret_leakage_rate",
            "prompt_injection_success_rate",
            "artifact_authorization_failure_rate",
            "missing_audit_event_rate",
            "unsafe_error_exposure_rate",
            "legitimate_task_false_rejection_rate",
        ),
    )
    numeric = next((item for item in run.metrics if item.metric_name == "numeric_accuracy"), None)
    baseline = (
        "No compatible baseline supplied."
        if run.baseline_comparison.baseline_path is None
        else (
            "No regressions detected."
            if not run.baseline_comparison.regressions
            else "; ".join(run.baseline_comparison.regressions)
        )
    )
    gate = "PASS" if run.gate_result.passed else "FAIL"
    gate_reason = ", ".join(run.gate_result.reasons) or "all configured gates passed"
    recovery = _named_lines(
        run,
        (
            "replan_recovery_rate",
            "average_replan_count",
            "max_replan_count",
            "replan_exhausted_count",
        ),
    )
    usage = _named_lines(
        run,
        (
            "latency_average_ms",
            "latency_p50_ms",
            "latency_p95_ms",
            "total_tokens",
            "estimated_total_cost",
        ),
    )
    limitations = "; ".join(run.known_limitations) or (
        "Mock results measure offline behavior, not production model or enterprise-data quality."
    )
    dataset_filename = (
        "accounts_payable_v1.jsonl"
        if run.dataset_id == "accounts_payable"
        else "supplier_quality_v1.jsonl"
    )
    return f"""# Agent Evaluation Report

## Run Metadata

- Run ID: `{run.run_id}`
- Mode: `{run.mode}`
- Seed: `{run.seed}`
- Git commit: `{run.git_commit}`
- Source hash: `{run.source_hash}`
- Git working tree dirty: `{str(run.git_dirty).lower()}`
- Provider/model: `{run.provider}` / `{run.model}`
- Started: `{run.started_at.isoformat()}`
- Duration: `{run.duration_ms} ms`
- Prompt versions: `{", ".join(run.prompt_versions) or "not available"}`
- Profile versions: `{", ".join(run.profile_versions) or "not available"}`
- Rule versions: `{", ".join(run.rule_versions) or "not available"}`
- Report versions: `{", ".join(run.report_versions) or "not available"}`

## Dataset

- ID/version: `{run.dataset_id}` / `{run.dataset_version}`
- Hash: `{run.dataset_hash}`
- Fixture hash: `{run.fixture_hash}`

## Executive Summary

- Passed: {run.passed_cases}/{run.total_cases}
- Failed: {run.failed_cases}/{run.total_cases}
- Errored: {run.errored_cases}/{run.total_cases}

## Quality Gate

**{gate}** — {gate_reason}

## Core Metrics

{metrics}

## Metrics by Category

{_category_lines(run)}

## Case Results

{cases}

## Failed Cases

{failed}

## Safety Findings

{safety}

## Numeric Accuracy Findings

{_metric_line(numeric) if numeric else "Not available"}

## Replan and Recovery

{recovery}

## Latency, Token Usage and Cost

{usage}

## Baseline Comparison

{baseline}

## Known Limitations

- {limitations}
- Machine-dependent latency is informational and excluded from the default hard regression gate.
- Cost is an estimate only when provider usage and a versioned pricing configuration are present.

## Reproduction Command

```bash
python evaluation/run_eval.py --dataset evaluation/datasets/{dataset_filename} \
  --mode {run.mode} --seed {run.seed}
```
"""


def _metric_line(metric: MetricResult | None) -> str:
    if metric is None or metric.value is None:
        return f"{metric.metric_name if metric else 'metric'}: not available"
    value = metric.value
    display = f"{(value * Decimal(100)):.2f}%" if metric.unit == "ratio" else str(value)
    fraction = (
        f" ({metric.numerator}/{metric.denominator})"
        if metric.numerator is not None and metric.denominator is not None
        else ""
    )
    return f"{metric.metric_name}: {display}{fraction} [{metric.direction.value}]"


def _category_lines(run: EvaluationRunResult) -> str:
    lines: list[str] = []
    for category, metrics in sorted(run.category_metrics.items()):
        task = next(
            (item for item in metrics if item.metric_name == "task_success_by_category"), None
        )
        lines.append(f"- {category}: {_metric_line(task)}")
    return "\n".join(lines) or "- None"


def _named_lines(run: EvaluationRunResult, names: tuple[str, ...]) -> str:
    selected = [item for item in run.metrics if item.metric_name in names]
    return "\n".join(f"- {_metric_line(item)}" for item in selected) or "- Not available"


__all__ = ["render_markdown"]
