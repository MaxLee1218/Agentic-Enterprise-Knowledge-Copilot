"""Auditable before/after observations from two retained controlled populations."""

from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from evaluation.contracts import EvaluationModel
from evaluation.supplier_quality_pilot.suite import SuiteReport, read_suite_report


class PopulationMeasurement(EvaluationModel):
    """Measured outcomes and their configuration, rather than an inferred business saving."""

    snapshot: str
    snapshot_hash: str
    mode: str
    code_revision: str | None
    code_hash: str
    config_hash: str
    started_at: datetime
    finished_at: datetime
    partial: bool
    stop_reason: str | None
    models: tuple[str, ...]
    prompt_versions: tuple[str, ...]
    qualified: int
    delivery_total: int
    numeric_passed: int
    numeric_total: int
    facts_passed: int
    fact_total: int
    safety_passed: bool
    first_report_cost_usd: Decimal | None
    cost_per_qualified_usd: Decimal | None
    safety_cost_usd: Decimal | None
    qualified_latency_median_ms: float | None
    qualified_latency_range_ms: tuple[int, int] | None


class CaseComparison(EvaluationModel):
    """Both original and repaired outcomes, including every failed assertion ID."""

    case_id: str
    before_qualified: bool
    after_qualified: bool
    before_state: str | None
    after_state: str | None
    before_failed_assertions: tuple[str, ...]
    after_failed_assertions: tuple[str, ...]


class PilotComparison(EvaluationModel):
    """Fixed-input development regression; neither a held-out sample nor a causal trial."""

    version: str = "supplier-quality-pilot-comparison.v1"
    dataset_hash: str
    source_hash: str
    policy_hash: str
    before: PopulationMeasurement
    after: PopulationMeasurement
    qualified_delta: int
    numeric_delta: int
    facts_delta: int
    cases: tuple[CaseComparison, ...]
    incremental_regression_cost_usd: Decimal | None
    aggregate_experiment_cost_usd: Decimal | None
    aggregate_budget_eur_upper: Decimal | None
    limitations: tuple[str, ...] = (
        "Same fixed synthetic scenarios used during debugging; not held-out generalization.",
        "All first results retained; no repeat replaces an original outcome.",
        "Latency medians cover different qualified populations; no speedup percentage is inferred.",
        "USD uses pinned usage ceilings; EUR includes contingencies, not invoice FX or tax.",
        "Human effort remains assumed; no observed human-time saving is measured.",
        "Original P01 was calibrated on an earlier revision; code revisions are disclosed per run.",
    )


def _measurement(report: SuiteReport, path: Path) -> PopulationMeasurement:
    return PopulationMeasurement(
        snapshot=path.name,
        snapshot_hash="sha256:" + sha256(path.read_bytes()).hexdigest(),
        mode=report.mode,
        code_revision=report.code_revision,
        code_hash=report.code_hash,
        config_hash=report.config_hash,
        started_at=report.started_at,
        finished_at=report.finished_at,
        partial=report.partial,
        stop_reason=report.stop_reason,
        models=tuple(sorted({r.model for r in report.runs})),
        prompt_versions=tuple(sorted({p for r in report.runs for p in r.prompt_versions})),
        qualified=report.qualified_deliveries,
        delivery_total=report.delivery_total,
        numeric_passed=report.numeric_passed,
        numeric_total=report.numeric_total,
        facts_passed=report.facts_passed,
        fact_total=report.fact_total,
        safety_passed=report.safety_passed,
        first_report_cost_usd=report.first_delivery_cost_usd,
        cost_per_qualified_usd=report.cost_per_qualified_usd,
        safety_cost_usd=report.safety_cost_usd,
        qualified_latency_median_ms=report.qualified_latency_median_ms,
        qualified_latency_range_ms=report.qualified_latency_range_ms,
    )


def publish_comparison(baseline: Path, regression: Path, destination: Path) -> PilotComparison:
    """Validate the retained inputs and write a comparison without mutating either snapshot."""
    before, after = read_suite_report(baseline), read_suite_report(regression)
    bound = "sha256:" + sha256(baseline.read_bytes()).hexdigest()
    if after.population != "post-fix" or after.baseline_hash != bound:
        raise ValueError("Regression is not bound to this original baseline")
    if (
        before.dataset_hash,
        before.source_hash,
        before.policy_hash,
        before.numeric_total,
        before.fact_total,
    ) != (
        after.dataset_hash,
        after.source_hash,
        after.policy_hash,
        after.numeric_total,
        after.fact_total,
    ):
        raise ValueError("Regression inputs or denominators changed")
    old = {r.case_id: r for r in before.runs if r.repeat_index == 0}
    new = {r.case_id: r for r in after.runs if r.repeat_index == 0}
    spending, prior_spending = after.spending, before.spending
    if spending and prior_spending:
        attempts = {a.attempt_id: a for a in spending.attempts}
        if (
            spending.pricing != prior_spending.pricing
            or spending.policy != prior_spending.policy
            or any(attempts.get(a.attempt_id) != a for a in prior_spending.attempts)
        ):
            raise ValueError("Comparison does not preserve the original spending ledger")
    incremental = None
    if spending and prior_spending and spending.unresolved_eur == 0:
        prior_ids = {a.attempt_id for a in prior_spending.attempts}
        incremental = sum(
            (
                a.usd_cost
                for a in spending.attempts
                if a.attempt_id not in prior_ids and a.usd_cost is not None
            ),
            Decimal(0),
        )
    result = PilotComparison(
        dataset_hash=after.dataset_hash,
        source_hash=after.source_hash,
        policy_hash=after.policy_hash,
        before=_measurement(before, baseline),
        after=_measurement(after, regression),
        qualified_delta=after.qualified_deliveries - before.qualified_deliveries,
        numeric_delta=after.numeric_passed - before.numeric_passed,
        facts_delta=after.facts_passed - before.facts_passed,
        cases=tuple(
            CaseComparison(
                case_id=case_id,
                before_qualified=old[case_id].qualified_delivery,
                after_qualified=new[case_id].qualified_delivery,
                before_state=old[case_id].task_status,
                after_state=new[case_id].task_status,
                before_failed_assertions=tuple(
                    a.assertion_id for a in old[case_id].assertions if not a.passed
                ),
                after_failed_assertions=tuple(
                    a.assertion_id for a in new[case_id].assertions if not a.passed
                ),
            )
            for case_id in sorted(old)
        ),
        incremental_regression_cost_usd=incremental,
        aggregate_experiment_cost_usd=after.total_experiment_cost_usd,
        aggregate_budget_eur_upper=spending.incurred_eur_upper if spending else None,
    )
    if before.mode != after.mode:
        result.limitations += (
            "Modes differ: this is an offline preflight, not a live performance comparison.",
        )
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "comparison.json").write_text(result.model_dump_json(indent=2))
    lines = [
        "# Preserved Supplier Quality baseline and post-fix regression",
        "",
        "| Observation | Original first population | Post-fix population |",
        "|---|---:|---:|",
        f"| Qualified reports | {before.qualified_deliveries}/9 | {after.qualified_deliveries}/9 |",
        f"| Numeric | {before.numeric_passed}/{before.numeric_total} | "
        f"{after.numeric_passed}/{after.numeric_total} |",
        f"| Enumerated fact support | {before.facts_passed}/{before.fact_total} | "
        f"{after.facts_passed}/{after.fact_total} |",
        f"| P10 rejection | {int(before.safety_passed)}/1 | {int(after.safety_passed)}/1 |",
        f"| First report USD cost / qualified | {before.cost_per_qualified_usd} | "
        f"{after.cost_per_qualified_usd} |",
        f"| Qualified latency median ms | {before.qualified_latency_median_ms} | "
        f"{after.qualified_latency_median_ms} |",
        "",
        f"Regression USD ceiling: {incremental}; aggregate USD: {after.total_experiment_cost_usd}; "
        f"aggregate EUR upper: {result.aggregate_budget_eur_upper}.",
        f"Partial: {after.partial}; unrun: {after.unrun_ids}; pending: {after.pending_ids}.",
        f"Code revisions: {before.code_revision} -> {after.code_revision}.",
        "",
        "## First-case outcomes",
        "",
        "| Case | Before qualified | After qualified | Remaining failed checks |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r.case_id} | {r.before_qualified} | {r.after_qualified} | "
        f"{', '.join(r.after_failed_assertions)} |"
        for r in result.cases
    ]
    lines += ["", "## Limitations", "", *[f"- {s}" for s in result.limitations], ""]
    (destination / "comparison.md").write_text("\n".join(lines))
    return result
