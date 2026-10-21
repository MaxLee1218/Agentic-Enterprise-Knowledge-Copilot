"""A separately bound development population, preserving first outcomes and all fees."""

import hashlib
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import httpx

from copilot.config import PROJECT_ROOT, Settings
from evaluation.contracts import EvaluationModel
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.budget import (
    BudgetLedger,
    BudgetPolicy,
    BudgetStopped,
    PricingCard,
)
from evaluation.supplier_quality_pilot.interfaces import SourceFacts
from evaluation.supplier_quality_pilot.live import DEFAULT_LEDGER, live_settings
from evaluation.supplier_quality_pilot.policy import PolicySnapshot
from evaluation.supplier_quality_pilot.runner import _git
from evaluation.supplier_quality_validation.audit import PDF_AUDIT_VERSION, audit_pdf_reports
from evaluation.supplier_quality_validation.bundle import (
    DEFAULT_BUNDLE,
    FILES,
    CaseOracle,
    verify_bundle,
)
from evaluation.supplier_quality_validation.comparison import (
    compare_regression,
    comparison_markdown,
)
from evaluation.supplier_quality_validation.continuation import validate_continuation_configuration
from evaluation.supplier_quality_validation.live import FROZEN_REVISION, verify_original_ledger
from evaluation.supplier_quality_validation.runner import CaseInput
from evaluation.supplier_quality_validation.suite import (
    ValidationReport,
    case_definitions,
    markdown,
    read_report,
    run_population,
    save_record,
)

PROTOCOL_PATH = PROJECT_ROOT / "evaluation/configs/supplier-quality-validation/regression-v1.json"


class RegressionProtocol(EvaluationModel):
    """Precommitted identity of the comparison and original aggregate attempt prefix."""

    version: Literal["supplier-quality-validation-regression.v1"] = (
        "supplier-quality-validation-regression.v1"
    )
    baseline_revision: str
    baseline_report: str
    baseline_sha256: str
    baseline_canonical_hash: str
    baseline_attempt_count: int
    baseline_attempt_records_sha256: str
    freeze_hash: str
    case_ids: tuple[str, ...]
    audit_version: Literal["visible-quarter.v1"] = "visible-quarter.v1"
    population_name: Literal["supplier-quality-validation.regression.v1"] = (
        "supplier-quality-validation.regression.v1"
    )


def run_regression(
    output_directory: Path,
    *,
    protocol: RegressionProtocol,
    source: SourceFacts,
    cases: tuple[CaseInput, ...],
    oracles: tuple[CaseOracle, ...],
    policy: PolicySnapshot,
    ledger: BudgetLedger,
    settings: Settings,
    persistence_url: str | None = None,
    transport_factory: Callable[[], httpx.BaseTransport] | None = None,
) -> ValidationReport:
    """Validate all comparison identities before any HTTP call; keep the raw new report."""
    baseline_path = Path(protocol.baseline_report)
    if (
        "sha256:" + hashlib.sha256(baseline_path.read_bytes()).hexdigest()
        != protocol.baseline_sha256
    ):
        raise BudgetStopped("REGRESSION_BASELINE_BYTES_CHANGED")
    baseline = read_report(baseline_path)
    if canonical_hash(baseline.model_dump(mode="json")) != protocol.baseline_canonical_hash:
        raise BudgetStopped("REGRESSION_BASELINE_CHANGED")
    validate_continuation_configuration(baseline, ledger.pricing, ledger.policy, settings)
    if (
        baseline.config.freeze_hash != protocol.freeze_hash
        or tuple(c.case_id for c in cases) != protocol.case_ids
        or case_definitions(cases, oracles) != baseline.config.cases
        or baseline.config.policy_hash != canonical_hash(policy.model_dump(mode="json"))
        or baseline.config.source_hash
        != canonical_hash(
            {
                "tenant_id": source.tenant_id,
                "rows": [r.model_dump(mode="json") for r in source.rows],
            }
        )
    ):
        raise BudgetStopped("REGRESSION_INPUTS_CHANGED")
    count = protocol.baseline_attempt_count
    if (
        len(baseline.spending.attempts) != count
        or ledger.snapshot().attempts[:count] != baseline.spending.attempts
    ):
        raise BudgetStopped("REGRESSION_PRIOR_FEES_CHANGED")
    if (
        len(ledger.snapshot().attempts) > count
        and ledger.population_identity(protocol.population_name) is None
    ):
        raise BudgetStopped("REGRESSION_UNEXPECTED_PRIOR_ATTEMPTS")
    ledger.bind_population(
        "supplier-quality-validation.regression.v1-protocol",
        canonical_hash(protocol.model_dump(mode="json")),
    )
    return run_population(
        output_directory,
        source=source,
        cases=cases,
        oracles=oracles,
        policy=policy,
        freeze_hash=protocol.freeze_hash,
        frozen_revision=baseline.config.frozen_revision,
        ledger=ledger,
        settings=settings,
        persistence_url=persistence_url,
        transport_factory=transport_factory,
        population_name=protocol.population_name,
        parent_report_hash=protocol.baseline_canonical_hash,
    )


def run_regression_live(
    output_directory: Path,
    *,
    pricing: PricingCard,
    previous_pricing: PricingCard,
    policy: BudgetPolicy,
    persistence_url: str,
) -> ValidationReport:
    """Explicit paid entry: committed protocol, original frozen bytes and original ledger."""
    if _git("status", "--porcelain"):
        raise BudgetStopped("REGRESSION_REQUIRES_CLEAN_COMMIT")
    protocol = RegressionProtocol.model_validate_json(PROTOCOL_PATH.read_bytes())
    for name in FILES | {"freeze.json"}:
        path = DEFAULT_BUNDLE / name
        relative = path.resolve().relative_to(PROJECT_ROOT).as_posix()
        if path.read_bytes() != subprocess.check_output(
            ["git", "show", f"{FROZEN_REVISION}:{relative}"], cwd=PROJECT_ROOT
        ):
            raise BudgetStopped("REGRESSION_FROZEN_BYTES_CHANGED")
    baseline_path = Path(protocol.baseline_report)
    if baseline_path.read_bytes() != subprocess.check_output(
        ["git", "show", f"{protocol.baseline_revision}:{baseline_path.as_posix()}"],
        cwd=PROJECT_ROOT,
    ):
        raise BudgetStopped("REGRESSION_BASELINE_REVISION_CHANGED")
    verify_original_ledger()
    with sqlite3.connect(f"file:{DEFAULT_LEDGER.resolve()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT record FROM attempts ORDER BY rowid LIMIT ?", (protocol.baseline_attempt_count,)
        ).fetchall()
    if hashlib.sha256("".join(row[0] for row in rows).encode()).hexdigest() != (
        protocol.baseline_attempt_records_sha256
    ):
        raise BudgetStopped("REGRESSION_ORIGINAL_RECORDS_CHANGED")
    if not persistence_url.startswith("postgresql"):
        raise BudgetStopped("REGRESSION_POSTGRES_REQUIRED")
    if protocol.audit_version != PDF_AUDIT_VERSION:
        raise BudgetStopped("REGRESSION_AUDIT_CHANGED")
    bundle = verify_bundle()
    configured = live_settings(pricing)
    with BudgetLedger(DEFAULT_LEDGER, pricing, policy, previous_pricing=previous_pricing) as ledger:
        raw = run_regression(
            output_directory,
            protocol=protocol,
            source=bundle.source,
            cases=bundle.cases,
            oracles=bundle.oracles,
            policy=bundle.policy,
            ledger=ledger,
            settings=configured,
            persistence_url=persistence_url,
        )
    audited = audit_pdf_reports(
        raw, bundle.cases, audit_revision=_git("rev-parse", "HEAD") or "unknown"
    )
    save_record(output_directory / "audited-report.json", audited)
    (output_directory / "audited-report.md").write_text(markdown(audited))
    comparison = compare_regression(read_report(baseline_path), audited)
    save_record(output_directory / "comparison.json", comparison)
    (output_directory / "comparison.md").write_text(comparison_markdown(comparison))
    return audited
