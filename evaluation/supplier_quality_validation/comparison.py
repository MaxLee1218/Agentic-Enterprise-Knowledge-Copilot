"""Recomputable same-input comparison; never pool development and first populations."""

from decimal import Decimal
from statistics import median
from typing import Literal

from evaluation.contracts import EvaluationModel
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_validation.audit import PDF_AUDIT_VERSION
from evaluation.supplier_quality_validation.suite import PopulationConfig, ValidationReport


class Scorecard(EvaluationModel):
    """Fixed denominators and inclusive attempt costs, with qualified-only latency."""

    qualified_reports: int
    report_total: int
    safety_passed: int
    safety_total: int
    numeric_passed: int
    numeric_total: int
    fact_passed: int
    fact_total: int
    partial: bool
    incremental_usd: Decimal | None
    report_usd: Decimal | None
    safety_usd: Decimal | None
    cost_per_qualified_usd: Decimal | None
    aggregate_eur_upper: Decimal
    unresolved_eur: Decimal
    aggregate_attempts: int
    qualified_latency_median_ms: float | None
    qualified_latency_range_ms: tuple[int, int] | None

    @classmethod
    def from_report(cls, report: ValidationReport) -> "Scorecard":
        """Use all failed/repaired attempt charges; no success-only cost filtering."""
        times = [
            r.latency_ms for r in report.runs if r.qualified_delivery and r.latency_ms is not None
        ]
        return cls(
            qualified_reports=report.qualified_deliveries,
            report_total=report.report_total,
            safety_passed=report.safety_passed,
            safety_total=report.safety_total,
            numeric_passed=report.numeric_passed,
            numeric_total=report.numeric_total,
            fact_passed=report.fact_passed,
            fact_total=report.fact_total,
            partial=report.partial,
            incremental_usd=report.incremental_usd,
            report_usd=report.report_usd,
            safety_usd=report.safety_usd,
            cost_per_qualified_usd=report.cost_per_qualified_usd,
            aggregate_eur_upper=report.spending.incurred_eur_upper,
            unresolved_eur=report.spending.unresolved_eur,
            aggregate_attempts=len(report.spending.attempts),
            qualified_latency_median_ms=median(times) if times else None,
            qualified_latency_range_ms=(min(times), max(times)) if times else None,
        )


class CaseComparison(EvaluationModel):
    """First and regression outcomes, including unchanged and newly failing cases."""

    case_id: str
    before_status: str | None
    after_status: str | None
    before_qualified: bool
    after_qualified: bool
    before_error: str | None
    after_error: str | None
    before_latency_ms: int | None
    after_latency_ms: int | None
    before_failed_assertions: tuple[str, ...]
    after_failed_assertions: tuple[str, ...]


class RegressionComparison(EvaluationModel):
    """Bound report hashes and complete provenance for the separate populations."""

    version: Literal["supplier-quality-validation-comparison.v1"] = (
        "supplier-quality-validation-comparison.v1"
    )
    audit_version: str = PDF_AUDIT_VERSION
    baseline_hash: str
    regression_hash: str
    before_config: PopulationConfig
    after_config: PopulationConfig
    before: Scorecard
    after: Scorecard
    cases: tuple[CaseComparison, ...]
    limitations: tuple[str, ...]


def compare_regression(before: ValidationReport, after: ValidationReport) -> RegressionComparison:
    """Reject input, audit, fee or denominator differences instead of claiming improvement."""
    fields = (
        "freeze_hash",
        "frozen_revision",
        "source_hash",
        "policy_hash",
        "model",
        "pricing_hash",
        "budget_hash",
        "settings",
        "cases",
    )
    baseline_hash = canonical_hash(before.model_dump(mode="json"))
    if (
        after.config.parent_report_hash != baseline_hash
        or any(getattr(before.config, key) != getattr(after.config, key) for key in fields)
        or after.spending.attempts[: len(before.spending.attempts)] != before.spending.attempts
        or after.prior_attempt_ids != tuple(a.attempt_id for a in before.spending.attempts)
        or not all(
            any(PDF_AUDIT_VERSION in line for line in r.limitations) for r in (before, after)
        )
    ):
        raise ValueError("comparison identity changed")
    return RegressionComparison(
        baseline_hash=baseline_hash,
        regression_hash=canonical_hash(after.model_dump(mode="json")),
        before_config=before.config,
        after_config=after.config,
        before=Scorecard.from_report(before),
        after=Scorecard.from_report(after),
        cases=tuple(
            CaseComparison(
                case_id=old.case_id,
                before_status=old.task_status or old.case_status,
                after_status=new.task_status or new.case_status,
                before_qualified=old.qualified_delivery,
                after_qualified=new.qualified_delivery,
                before_error=old.error_code,
                after_error=new.error_code,
                before_latency_ms=old.latency_ms,
                after_latency_ms=new.latency_ms,
                before_failed_assertions=tuple(
                    a.assertion_id for a in old.assertions if not a.passed
                ),
                after_failed_assertions=tuple(
                    a.assertion_id for a in new.assertions if not a.passed
                ),
            )
            for old, new in zip(before.runs, after.runs, strict=True)
        ),
        limitations=after.limitations
        + (
            "Baseline is the preserved first audited population, including its partial/pending "
            "outcomes and two harness segments. Regression uses fresh Tasks and one code revision.",
        ),
    )


def comparison_markdown(result: RegressionComparison) -> str:
    """Present measured scores with distinct costs and no causal/speedup claim."""
    a, b = result.before, result.after
    rows = (
        (
            "Qualified reports (actual PDF audited)",
            f"{a.qualified_reports}/{a.report_total}",
            f"{b.qualified_reports}/{b.report_total}",
        ),
        (
            "Numeric assertions",
            f"{a.numeric_passed}/{a.numeric_total}",
            f"{b.numeric_passed}/{b.numeric_total}",
        ),
        ("Enumerated facts", f"{a.fact_passed}/{a.fact_total}", f"{b.fact_passed}/{b.fact_total}"),
        (
            "Safe denials",
            f"{a.safety_passed}/{a.safety_total}",
            f"{b.safety_passed}/{b.safety_total}",
        ),
        ("Partial", str(a.partial), str(b.partial)),
        ("Incremental model USD ceiling", str(a.incremental_usd), str(b.incremental_usd)),
        ("Report-task USD (all failures/retries)", str(a.report_usd), str(b.report_usd)),
        ("Safety-task USD", str(a.safety_usd), str(b.safety_usd)),
        ("USD per qualified report", str(a.cost_per_qualified_usd), str(b.cost_per_qualified_usd)),
        ("Aggregate EUR upper", str(a.aggregate_eur_upper), str(b.aggregate_eur_upper)),
        ("Unresolved EUR", str(a.unresolved_eur), str(b.unresolved_eur)),
        ("Aggregate HTTP attempts", str(a.aggregate_attempts), str(b.aggregate_attempts)),
        (
            "Qualified latency median ms",
            str(a.qualified_latency_median_ms),
            str(b.qualified_latency_median_ms),
        ),
        (
            "Qualified latency range ms",
            str(a.qualified_latency_range_ms),
            str(b.qualified_latency_range_ms),
        ),
    )
    return "\n".join(
        [
            "# Supplier Quality — development regression v1 comparison",
            "",
            "Same V01–V20 inputs, /16 /4 /317 /256, model/configuration "
            "and visible-quarter.v1 audit.",
            "",
            "| Metric | First audited baseline | Development regression v1 |",
            "|---|---|---|",
            *[f"| {label} | {old} | {new} |" for label, old, new in rows],
            "",
            f"Baseline hash: `{result.baseline_hash}`.",
            f"Regression hash: `{result.regression_hash}`.",
            f"Regression code: `{result.after_config.code_revision}`; "
            f"dirty: {result.after_config.git_dirty}.",
            "",
            "| Case | Before | After | Qualified before → after | "
            "Latency ms before → after | Error after |",
            "|---|---|---|---|---|---|",
            *[
                f"| {r.case_id} | {r.before_status} | {r.after_status} | "
                f"{r.before_qualified} → {r.after_qualified} | "
                f"{r.before_latency_ms} → {r.after_latency_ms} | {r.after_error} |"
                for r in result.cases
            ],
            "",
            "## Limitations",
            "",
            *[f"- {line}" for line in result.limitations],
            "",
        ]
    )
