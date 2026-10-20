"""Explicit first live execution of the committed validation freeze and original budget."""

import hashlib
import sqlite3
import subprocess
from pathlib import Path

from copilot.config import PROJECT_ROOT
from evaluation.supplier_quality_pilot.budget import (
    BudgetLedger,
    BudgetPolicy,
    BudgetStopped,
    PricingCard,
)
from evaluation.supplier_quality_pilot.live import DEFAULT_LEDGER, live_settings
from evaluation.supplier_quality_pilot.runner import _git
from evaluation.supplier_quality_validation.bundle import DEFAULT_BUNDLE, FILES, verify_bundle
from evaluation.supplier_quality_validation.suite import ValidationReport, run_population

FROZEN_REVISION = "9febd06336dd6955b8dd63ca983fa9ad59df6e7b"
LEGACY_ATTEMPTS_SHA256 = "74138481764f06253eab4859873d9bc9c93fa814835deecc5f63c569275ed08c"


def verify_execution_baseline(directory: Path = DEFAULT_BUNDLE) -> None:
    """Require committed freeze bytes and unchanged production/prompts before first use."""
    if _git("status", "--porcelain"):
        raise BudgetStopped("VALIDATION_REQUIRES_CLEAN_COMMIT")
    for name in FILES | {"freeze.json"}:
        relative = directory.resolve().relative_to(PROJECT_ROOT).joinpath(name)
        original = subprocess.check_output(
            ["git", "show", f"{FROZEN_REVISION}:{relative.as_posix()}"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
        )
        if original != (directory / name).read_bytes():
            raise BudgetStopped("VALIDATION_FREEZE_REVISION_MISMATCH")
    if _git("diff", FROZEN_REVISION, "--", "src"):
        raise BudgetStopped("VALIDATION_PRODUCTION_CHANGED_SINCE_FREEZE")


def verify_original_ledger(path: Path = DEFAULT_LEDGER) -> None:
    """Fail closed on absent/replaced/truncated original 46 attempt records."""
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT record FROM attempts ORDER BY rowid LIMIT 46").fetchall()
    digest = hashlib.sha256("".join(row[0] for row in rows).encode()).hexdigest()
    if len(rows) != 46 or digest != LEGACY_ATTEMPTS_SHA256:
        raise BudgetStopped("ORIGINAL_EXPERIMENT_LEDGER_MISMATCH")


def run_validation_live(
    output_directory: Path,
    *,
    pricing: PricingCard,
    previous_pricing: PricingCard,
    policy: BudgetPolicy,
    persistence_url: str,
) -> ValidationReport:
    """The only frozen-population entry point: explicit live, original durable ledger."""
    bundle = verify_bundle()
    verify_execution_baseline()
    verify_original_ledger()
    if not persistence_url.startswith("postgresql"):
        raise BudgetStopped("VALIDATION_POSTGRES_RUNTIME_REQUIRED")
    configured = live_settings(pricing)
    with BudgetLedger(DEFAULT_LEDGER, pricing, policy, previous_pricing=previous_pricing) as ledger:
        return run_population(
            output_directory,
            source=bundle.source,
            cases=bundle.cases,
            oracles=bundle.oracles,
            policy=bundle.policy,
            freeze_hash=bundle.freeze_hash,
            frozen_revision=FROZEN_REVISION,
            ledger=ledger,
            settings=configured,
            persistence_url=persistence_url,
        )
