"""Continue only unrun first attempts after the preserved typed-intake harness stop."""

import json
from pathlib import Path

from copilot.config import Settings
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.budget import (
    BudgetLedger,
    BudgetPolicy,
    BudgetStopped,
    PricingCard,
)
from evaluation.supplier_quality_pilot.live import DEFAULT_LEDGER, live_settings
from evaluation.supplier_quality_validation.bundle import verify_bundle
from evaluation.supplier_quality_validation.live import (
    verify_execution_baseline,
    verify_original_ledger,
)
from evaluation.supplier_quality_validation.suite import (
    ExecutionSegment,
    Progress,
    ValidationReport,
    execution_settings,
    markdown,
    read_report,
    run_population,
    save_record,
    verify_cached_case,
)


def unrun_tail(parent: ValidationReport) -> tuple[str, ...]:
    """Accept only the observed no-Task/no-attempt typed intake stop, never a retry."""
    if parent.stop_reason != "HARNESS_TaskIntakeValidationError":
        raise ValueError("continuation requires the preserved typed intake stop")
    first = next(
        (i for i, r in enumerate(parent.runs) if r.case_status == "unrun"), len(parent.runs)
    )
    if first == 0 or first == len(parent.runs):
        raise ValueError("continuation requires a nonempty contiguous unrun tail")
    stopped = parent.runs[first - 1]
    if (
        stopped.case_status != "interrupted"
        or stopped.error_code != "TaskIntakeValidationError"
        or stopped.task_id is not None
        or stopped.task_status is not None
        or stopped.attempts
        or stopped.executed_tools
        or stopped.artifact_paths
        or stopped.qualified_delivery
        or any(r.case_status != "executed" for r in parent.runs[: first - 1])
        or any(
            r.case_status != "unrun"
            or r.run_id
            or r.attempts
            or r.task_id is not None
            or r.task_status is not None
            or r.started_at is not None
            or r.latency_ms is not None
            or r.executed_tools
            or r.artifact_paths
            or r.artifact_checksums
            or r.interaction is not None
            or r.errors
            or any(a.passed for a in r.assertions)
            for r in parent.runs[first:]
        )
    ):
        raise ValueError("started, paid, pending or otherwise interrupted cases cannot be repeated")
    return tuple(r.case_id for r in parent.runs[first:])


def validate_continuation_configuration(
    parent: ValidationReport, pricing: PricingCard, policy: BudgetPolicy, settings: Settings
) -> None:
    """Reject condition changes before binding a segment or sending its first request."""
    if (
        parent.config.model != settings.llm_model
        or parent.config.settings != execution_settings(settings)
        or parent.config.pricing_hash != canonical_hash(pricing.model_dump(mode="json"))
        or parent.config.budget_hash != canonical_hash(policy.model_dump(mode="json"))
    ):
        raise BudgetStopped("VALIDATION_CONTINUATION_CONFIG_CHANGED")


def combine_first_attempts(
    parent: ValidationReport, child: ValidationReport, *, parent_hash: str, child_hash: str
) -> ValidationReport:
    """Retain the prefix and failed intake; disclose both harness segments and all costs."""
    tail = unrun_tail(parent)
    if (
        tuple(r.case_id for r in child.runs) != tail
        or child.config.parent_report_hash != parent_hash
        or child.config.freeze_hash != parent.config.freeze_hash
        or child.config.source_hash != parent.config.source_hash
        or child.config.policy_hash != parent.config.policy_hash
        or child.config.pricing_hash != parent.config.pricing_hash
        or child.config.budget_hash != parent.config.budget_hash
        or child.config.model != parent.config.model
        or child.config.settings != parent.config.settings
        or child.config.cases != tuple(c for c in parent.config.cases if c.case_id in tail)
        or child.spending.attempts[: len(parent.spending.attempts)] != parent.spending.attempts
        or child.prior_attempt_ids != tuple(a.attempt_id for a in parent.spending.attempts)
    ):
        raise ValueError("continuation changed identity, prefix outcomes or prior charges")
    prefix = tuple(
        r.model_copy(
            update={
                "code_revision": parent.config.code_revision,
                "code_hash": parent.config.code_hash,
                **({"case_status": "intake_rejected"} if r.case_status == "interrupted" else {}),
            }
        )
        for r in parent.runs[: len(parent.runs) - len(tail)]
    )
    return ValidationReport(
        config=parent.config,
        started_at=parent.started_at,
        stopped_at=child.stopped_at,
        stop_reason=child.stop_reason,
        runs=prefix + child.runs,
        spending=child.spending,
        prior_attempt_ids=parent.prior_attempt_ids,
        segments=(
            ExecutionSegment(
                config=parent.config,
                case_ids=tuple(r.case_id for r in prefix),
                report_hash=parent_hash,
            ),
            ExecutionSegment(config=child.config, case_ids=tail, report_hash=child_hash),
        ),
        limitations=parent.limitations
        + (
            "Two harness segments: the original typed intake stop report remains immutable; "
            "only previously unrun cases continued. No product/prompt/input changes or "
            "case reruns.",
            "The original stopped intake case remains failed; only its harness-error "
            "classification "
            "is corrected in this combined view. Its original exception class is preserved.",
        ),
    )


def continue_validation_live(
    parent_directory: Path,
    output_directory: Path,
    *,
    pricing: PricingCard,
    previous_pricing: PricingCard,
    policy: BudgetPolicy,
    persistence_url: str,
) -> ValidationReport:
    """Hash-bound unrun-only segment in the original budget, without altering the stop."""
    bundle = verify_bundle()
    verify_execution_baseline()
    verify_original_ledger()
    if not persistence_url.startswith("postgresql"):
        raise BudgetStopped("VALIDATION_POSTGRES_RUNTIME_REQUIRED")
    parent_directory = parent_directory.resolve()
    raw = json.loads((parent_directory / "report.json").read_bytes())
    parent = read_report(parent_directory / "report.json")
    tail = unrun_tail(parent)
    if parent.config.freeze_hash != bundle.freeze_hash or tuple(
        c.case_id for c in bundle.cases
    ) != tuple(r.case_id for r in parent.runs):
        raise ValueError("parent is not the original frozen population")
    parent_hash = canonical_hash(raw)
    binding = canonical_hash(
        {"output": str(parent_directory), "config": canonical_hash(raw["config"])}
    )
    progress = Progress.model_validate_json((parent_directory / "progress.json").read_bytes())
    prefix = parent.runs[: len(parent.runs) - len(tail)]
    if progress.config_hash != canonical_hash(raw["config"]) or tuple(
        c.case_id for c in progress.cases
    ) != tuple(r.case_id for r in prefix):
        raise ValueError("parent progress/config identity changed")
    for completed, run in zip(progress.cases, prefix, strict=True):
        if (
            not completed.report_hash
            or verify_cached_case(parent_directory / run.case_id, completed) != run
        ):
            raise ValueError("parent first outcomes changed")
    configured = live_settings(pricing)
    validate_continuation_configuration(parent, pricing, policy, configured)
    with BudgetLedger(DEFAULT_LEDGER, pricing, policy, previous_pricing=previous_pricing) as ledger:
        if ledger.population_identity("supplier-quality-validation.v1") != binding:
            raise BudgetStopped("VALIDATION_PARENT_BINDING_MISMATCH")
        current = ledger.snapshot().attempts
        if current[: len(parent.spending.attempts)] != parent.spending.attempts:
            raise BudgetStopped("VALIDATION_PARENT_CHARGES_CHANGED")
        if (
            len(current) != len(parent.spending.attempts)
            and not (output_directory / "progress.json").is_file()
        ):
            raise BudgetStopped("VALIDATION_UNEXPECTED_PRIOR_CHARGE")
        child = run_population(
            output_directory,
            source=bundle.source,
            cases=tuple(c for c in bundle.cases if c.case_id in tail),
            oracles=tuple(o for o in bundle.oracles if o.case_id in tail),
            policy=bundle.policy,
            freeze_hash=bundle.freeze_hash,
            frozen_revision=parent.config.frozen_revision,
            ledger=ledger,
            settings=configured,
            persistence_url=persistence_url,
            population_name="supplier-quality-validation.v1-continuation",
            parent_report_hash=parent_hash,
        )
    combined = combine_first_attempts(
        parent,
        child,
        parent_hash=parent_hash,
        child_hash=canonical_hash(child.model_dump(mode="json")),
    )
    save_record(output_directory / "combined-report.json", combined)
    (output_directory / "combined-report.md").write_text(markdown(combined))
    return combined
