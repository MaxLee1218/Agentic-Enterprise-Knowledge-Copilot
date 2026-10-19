"""First-run denominators, bounded repetitions and durable partial experiment reports."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import Field, TypeAdapter, computed_field

from copilot.config import Settings
from copilot.contracts import TaskStatus
from copilot.services.workflows.models import WorkflowExecution
from evaluation.contracts import EvaluationModel
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.budget import (
    BudgetLedger,
    BudgetPolicy,
    BudgetStopped,
    PricingCard,
    SpendingSnapshot,
)
from evaluation.supplier_quality_pilot.cases import CASE_PATH, PilotCase, load_cases
from evaluation.supplier_quality_pilot.checker import evaluate_case
from evaluation.supplier_quality_pilot.contracts import (
    PilotAssertion,
    PilotRun,
    PilotSnapshot,
    read_pilot_run,
)
from evaluation.supplier_quality_pilot.live import DEFAULT_LEDGER, live_model, live_settings
from evaluation.supplier_quality_pilot.policy import POLICY_PATH, PolicySnapshot
from evaluation.supplier_quality_pilot.runner import _code_hash, _git, run_case
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE


class CaseDenominator(EvaluationModel):
    """Applicable checks counted before any execution."""

    numeric: int
    facts: int


class SuiteManifest(EvaluationModel):
    """Immutable configuration pinned before first dispatch and on every resume."""

    version: Literal["pilot-suite-manifest.v1"] = "pilot-suite-manifest.v1"
    dataset_hash: str
    source_hash: str
    policy_hash: str
    code_hash: str
    code_revision: str | None
    dirty: bool
    mode: Literal["offline", "live"]
    pricing: PricingCard | None
    budget: BudgetPolicy | None
    runtime: str
    model: str
    max_output_tokens: int
    temperature: float
    provider_retries: int
    denominators: dict[str, CaseDenominator]


class CaseProgress(EvaluationModel):
    """Started is durable before billing; an unfinished case cannot be silently retried."""

    status: Literal["started", "finished"]
    run_id: str | None
    report_hash: str | None = None


class SuiteProgress(EvaluationModel):
    """Crash-safe experiment journal, distinct from Task runtime authority."""

    version: Literal["pilot-suite-progress.v1"] = "pilot-suite-progress.v1"
    config_hash: str
    started_at: datetime
    cases: dict[str, CaseProgress] = Field(default_factory=dict)


class SuitePointer(EvaluationModel):
    """One first-population destination for the authorized spending experiment."""

    version: Literal["pilot-suite-pointer.v1"] = "pilot-suite-pointer.v1"
    output: str
    config_hash: str


class SuiteReport(EvaluationModel):
    """Versioned measurements; security, repeats, modeled effort and unrun cases stay separate."""

    version: Literal["supplier-quality-pilot-suite.v1"] = "supplier-quality-pilot-suite.v1"
    started_at: datetime
    finished_at: datetime
    mode: Literal["offline", "live"]
    code_revision: str | None
    code_hash: str
    git_dirty: bool
    dataset_hash: str
    source_hash: str
    policy_hash: str
    config_hash: str
    runs: tuple[PilotRun, ...]
    partial: bool
    stop_reason: str | None
    spending: SpendingSnapshot | None
    numeric_total: int
    fact_total: int
    cost_per_qualified_usd: Decimal | None
    safety_cost_usd: Decimal | None
    first_delivery_cost_usd: Decimal | None
    repeat_cost_usd: Decimal | None
    total_experiment_cost_usd: Decimal | None
    qualified_latency_median_ms: float | None
    qualified_latency_range_ms: tuple[int, int] | None
    delivery_total: Literal[9] = 9
    manual_effort_minutes: dict[str, tuple[int, int, int]] = Field(
        default_factory=lambda: {
            "ordinary": (26, 41, 64),
            "policy": (31, 49, 76),
            "empty": (11, 18, 29),
        }
    )
    limitations: tuple[str, ...] = (
        "Controlled synthetic pilot; not a held-out benchmark or production success rate.",
        "Fact support covers enumerated numeric, null, no-record and policy-method facts only.",
        "P09 is scripted; no human interaction or observed human-time savings were measured.",
        "USD uses pinned ceilings; EUR uses budget contingencies, not invoice FX/tax.",
        "P01 retains its earlier code revision; it is re-scored and never replaced by a repeat.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrun_ids(self) -> tuple[str, ...]:
        """Always enumerate all absent or unstarted first cases in a partial report."""
        executed = {
            r.case_id for r in self.runs if r.repeat_index == 0 and r.case_status == "executed"
        }
        return tuple(f"P{i:02}" for i in range(1, 11) if f"P{i:02}" not in executed)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pending_ids(self) -> tuple[str, ...]:
        """Waiting Tasks remain distinct from independent acceptance failures."""
        return tuple(
            r.case_id
            for r in self.runs
            if r.repeat_index == 0
            and r.case_status == "executed"
            and r.task_status in {TaskStatus.WAITING_APPROVAL, TaskStatus.WAITING_CLARIFICATION}
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def samples(self) -> tuple[str, ...]:
        """At most two actual qualified artifacts; prefer the PDF and original JSON."""
        candidates = sorted(
            (r for r in self.runs if r.qualified_delivery and r.repeat_index == 0),
            key=lambda r: (r.case_id != "P02", r.case_id),
        )
        return tuple(p for r in candidates[:2] for p in r.artifact_paths[:1])

    @computed_field  # type: ignore[prop-decorator]
    @property
    def qualified_deliveries(self) -> int:
        return sum(
            r.qualified_delivery for r in self.runs if r.repeat_index == 0 and r.case_id != "P10"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def numeric_passed(self) -> int:
        return sum(
            r.numeric_passed for r in self.runs if r.repeat_index == 0 and r.case_id != "P10"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def facts_passed(self) -> int:
        return sum(
            a.passed
            for r in self.runs
            if r.repeat_index == 0 and r.case_id != "P10"
            for a in r.assertions
            if a.assertion_id.startswith("support/")
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safety_passed(self) -> bool:
        return any(
            r.case_id == "P10"
            and r.case_status == "executed"
            and all(a.passed for a in r.assertions)
            for r in self.runs
        )


def _save(path: Path, data: EvaluationModel) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        stream.write(data.model_dump_json(indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_suite(
    output_directory: Path,
    *,
    mode: Literal["offline", "live"] = "offline",
    source_path: Path = DEFAULT_SOURCE,
    cases_path: Path = CASE_PATH,
    pricing: PricingCard | None = None,
    policy: BudgetPolicy | None = None,
    calibration_directory: Path | None = None,
    settings: Settings | None = None,
    ledger_path: Path = DEFAULT_LEDGER,
    persistence_url: str | None = None,
    resume: bool = False,
    transport_factory: Callable[[], httpx.BaseTransport] | None = None,
) -> SuiteReport:
    """Run the frozen first population, retaining calibration and every failed or pending result.

    Paid runs use a fixed experiment pointer alongside the ledger. Restart cannot silently create
    another first population or retry an interrupted case with reconciled-but-unsaved fees.
    """
    cases = load_cases(cases_path)
    snapshot = PilotSnapshot.model_validate_json(source_path.read_bytes())
    policy_snapshot = PolicySnapshot.model_validate_json(POLICY_PATH.read_bytes())
    configured = live_settings(pricing, settings) if mode == "live" and pricing else None
    if mode == "live" and (pricing is None or policy is None or calibration_directory is None):
        raise BudgetStopped("LIVE_SUITE_REQUIRES_PRICING_BUDGET_CALIBRATION")
    output_directory = output_directory.resolve()
    if output_directory.exists() and any(output_directory.iterdir()) and not resume:
        raise ValueError("Suite output must be empty unless explicitly resumed")
    output_directory.mkdir(parents=True, exist_ok=True)
    declared = {c.case_id: evaluate_case(snapshot, None, {}, c) for c in cases}
    frozen = SuiteManifest(
        dataset_hash=canonical_hash([c.model_dump(mode="json") for c in cases]),
        source_hash=canonical_hash(snapshot.model_dump(mode="json")),
        policy_hash=canonical_hash(policy_snapshot.model_dump(mode="json")),
        code_hash=_code_hash(),
        code_revision=_git("rev-parse", "HEAD"),
        dirty=bool(_git("status", "--porcelain")),
        mode=mode,
        pricing=pricing,
        budget=policy,
        runtime="postgresql-worker" if persistence_url else "sqlite-sync-pending-clarification",
        model=configured.llm_model if configured else "offline-governed-domains-v2",
        max_output_tokens=configured.llm_max_output_tokens if configured else 4096,
        temperature=configured.llm_temperature if configured else 0,
        provider_retries=configured.llm_max_retries if configured else 0,
        denominators={
            c.case_id: CaseDenominator(
                numeric=sum(a.category == "numeric" for a in declared[c.case_id]),
                facts=sum(a.assertion_id.startswith("support/") for a in declared[c.case_id]),
            )
            for c in cases
        },
    )
    fingerprint = canonical_hash(frozen.model_dump(mode="json"))
    progress_path = output_directory / "progress.json"
    if resume:
        progress = SuiteProgress.model_validate_json(progress_path.read_bytes())
        if progress.config_hash != fingerprint:
            raise BudgetStopped("SUITE_RESUME_CONFIG_MISMATCH")
    else:
        progress = SuiteProgress(config_hash=fingerprint, started_at=datetime.now(UTC))
        _save(output_directory / "manifest.json", frozen)
        _save(progress_path, progress)
    ledger_context = (
        BudgetLedger(ledger_path, pricing, policy)
        if pricing and policy and mode == "live"
        else nullcontext(None)
    )
    with ledger_context as ledger:
        if ledger:
            pointer = ledger.path.with_suffix(".suite.json")
            if pointer.exists() and SuitePointer.model_validate_json(
                pointer.read_bytes()
            ).output != str(output_directory):
                raise BudgetStopped("EXPERIMENT_SUITE_ALREADY_EXISTS")
            if not pointer.exists():
                _save(pointer, SuitePointer(output=str(output_directory), config_hash=fingerprint))
        runs: list[PilotRun] = []
        stopped = ledger.snapshot().stopped_reason if ledger else None
        for case, repeat in [(c, 0) for c in cases] + [(cases[0], 1), (cases[7], 1)]:
            key = f"{case.case_id}-r{repeat}"
            destination = output_directory / key
            prior = progress.cases.get(key)
            if prior and prior.status == "finished":
                if (
                    prior.report_hash
                    != hashlib.sha256((destination / "report.json").read_bytes()).hexdigest()
                ):
                    raise BudgetStopped("SUITE_CACHE_HASH_MISMATCH")
                cached = read_pilot_run(destination / "report.json")
                for artifact_id, path in zip(
                    cached.artifact_ids, cached.artifact_paths, strict=True
                ):
                    expected = cached.artifact_checksums[artifact_id].removeprefix("sha256:")
                    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                        raise BudgetStopped("SUITE_CACHE_ARTIFACT_MISMATCH")
                runs.append(cached)
                continue
            if prior:
                stopped = stopped or "UNFINISHED_CASE_REQUIRES_RECONCILIATION"
            first_complete = len(runs) >= 10 and all(
                r.case_status == "executed"
                and r.task_status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
                for r in runs[:10]
            )
            if stopped or (repeat and not first_complete):
                if repeat:
                    continue
                runs.append(
                    _missing(case, snapshot, frozen, stopped or "FIRST_ROUND_INCOMPLETE", mode)
                )
                continue
            run_id = uuid4().hex
            progress.cases[key] = CaseProgress(status="started", run_id=run_id)
            _save(progress_path, progress)
            if case.case_id == "P01" and repeat == 0 and mode == "live":
                assert calibration_directory is not None and ledger is not None
                run = _calibration(calibration_directory, snapshot, case, ledger)
                destination.mkdir(exist_ok=True)
                (destination / "report.json").write_text(run.model_dump_json(indent=2))
            else:
                try:
                    if ledger and configured:
                        with live_model(
                            ledger,
                            configured,
                            stage="suite",
                            run_id=run_id,
                            transport=transport_factory() if transport_factory else None,
                        ) as session:
                            run = run_case(
                                destination,
                                case=case,
                                source_path=source_path,
                                live=session,
                                persistence_url=persistence_url if case.scripted_answer else None,
                            )
                    else:
                        run = run_case(
                            destination,
                            case=case,
                            source_path=source_path,
                            persistence_url=persistence_url if case.scripted_answer else None,
                        )
                except Exception as exc:
                    run = _missing(case, snapshot, frozen, "SETUP_" + type(exc).__name__, mode)
            run.repeat_index = repeat
            run.policy_hash = frozen.policy_hash if case.policy else None
            destination.mkdir(exist_ok=True)
            (destination / "report.json").write_text(run.model_dump_json(indent=2))
            runs.append(run)
            progress.cases[key] = CaseProgress(
                status="finished",
                run_id=run.run_id,
                report_hash=hashlib.sha256((destination / "report.json").read_bytes()).hexdigest(),
            )
            _save(progress_path, progress)
            if ledger:
                stopped = ledger.snapshot().stopped_reason
            report = _summary(
                runs, cases, declared, frozen, fingerprint, progress, ledger, stopped, mode
            )
            _publish(output_directory, report)
        report = _summary(
            runs, cases, declared, frozen, fingerprint, progress, ledger, stopped, mode
        )
        _publish(output_directory, report)
        return report


def _calibration(
    directory: Path, snapshot: PilotSnapshot, case: PilotCase, ledger: BudgetLedger
) -> PilotRun:
    run = read_pilot_run(directory / "report.json")
    if (
        run.mode != "live"
        or run.case_id != "P01"
        or not run.qualified_delivery
        or run.source_hash != canonical_hash(snapshot.model_dump(mode="json"))
    ):
        raise BudgetStopped("CALIBRATION_NOT_QUALIFIED_OR_SOURCE_MISMATCH")
    attempts = [a for a in ledger.snapshot().attempts if a.run_id == run.run_id]
    if not attempts or any(a.status != "reconciled" or a.stage != "calibration" for a in attempts):
        raise BudgetStopped("CALIBRATION_NOT_IN_EXPERIMENT_LEDGER")
    if run.model_cost != sum((a.usd_cost for a in attempts if a.usd_cost is not None), Decimal(0)):
        raise BudgetStopped("CALIBRATION_USAGE_COST_MISMATCH")
    if run.provider != "deepseek" or run.model != ledger.pricing.model:
        raise BudgetStopped("CALIBRATION_MODEL_MISMATCH")
    execution = TypeAdapter(WorkflowExecution).validate_json(
        (directory / "execution.json").read_bytes()
    )
    run.schema_version = "supplier-quality-pilot-run.v2"
    run.artifact_ids = tuple(a.artifact_id for a in execution.artifacts)
    run.artifact_checksums = {a.artifact_id: a.checksum for a in execution.artifacts}
    run.assertions = evaluate_case(
        snapshot,
        execution,
        {
            a.artifact_id: Path(p)
            for a, p in zip(execution.artifacts, run.artifact_paths, strict=True)
        },
        case,
    )
    if not run.qualified_delivery:
        raise BudgetStopped("CALIBRATION_RESCORING_FAILED")
    return run


def _missing(
    case: PilotCase,
    snapshot: PilotSnapshot,
    frozen: SuiteManifest,
    reason: str,
    mode: Literal["offline", "live"],
) -> PilotRun:
    return PilotRun(
        case_id=case.case_id,
        expected_outcome=case.expected_outcome,
        case_status="unrun",
        mode=mode,
        provider="deepseek" if mode == "live" else "mock",
        model=frozen.model,
        started_at=datetime.now(UTC),
        latency_ms=0,
        task_id=None,
        task_status=None,
        code_revision=frozen.code_revision,
        code_hash=frozen.code_hash,
        git_dirty=frozen.dirty,
        source_hash=frozen.source_hash,
        config_hash=canonical_hash(frozen.model_dump(mode="json")),
        source_version=snapshot.version,
        prompt_versions=(),
        executed_tools=(),
        artifact_paths=(),
        assertions=evaluate_case(snapshot, None, {}, case),
        error_code=reason,
    )


def _summary(
    runs: list[PilotRun],
    cases: tuple[PilotCase, ...],
    declared: dict[str, tuple[PilotAssertion, ...]],
    frozen: SuiteManifest,
    fingerprint: str,
    progress: SuiteProgress,
    ledger: BudgetLedger | None,
    reason: str | None,
    mode: Literal["offline", "live"],
) -> SuiteReport:
    first = [r for r in runs if r.repeat_index == 0]
    reports = [r for r in first if r.case_id != "P10"]
    qualified = [r for r in reports if r.qualified_delivery]
    spending = ledger.snapshot() if ledger else None
    known = (
        mode == "live"
        and spending is not None
        and spending.unresolved_eur == 0
        and all(r.model_cost is not None for r in reports if r.case_status == "executed")
    )
    cost = sum((r.model_cost for r in reports if r.model_cost is not None), Decimal(0))
    latency = [r.latency_ms for r in qualified]
    return SuiteReport(
        started_at=progress.started_at,
        finished_at=datetime.now(UTC),
        mode=mode,
        code_revision=frozen.code_revision,
        code_hash=frozen.code_hash,
        git_dirty=frozen.dirty,
        dataset_hash=frozen.dataset_hash,
        source_hash=frozen.source_hash,
        policy_hash=frozen.policy_hash,
        config_hash=fingerprint,
        runs=tuple(runs),
        partial=len(first) < 10
        or any(
            r.case_status == "unrun"
            or r.task_status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            for r in first
        ),
        stop_reason=reason,
        spending=spending,
        numeric_total=sum(
            sum(a.category == "numeric" for a in declared[c.case_id])
            for c in cases
            if c.case_id != "P10"
        ),
        fact_total=sum(
            sum(a.assertion_id.startswith("support/") for a in declared[c.case_id])
            for c in cases
            if c.case_id != "P10"
        ),
        cost_per_qualified_usd=cost / len(qualified) if qualified and known else None,
        safety_cost_usd=next((r.model_cost for r in first if r.case_id == "P10"), None),
        first_delivery_cost_usd=cost if known else None,
        repeat_cost_usd=sum(
            (r.model_cost for r in runs if r.repeat_index and r.model_cost is not None), Decimal(0)
        )
        if known and all(r.model_cost is not None for r in runs if r.repeat_index)
        else None,
        total_experiment_cost_usd=sum(
            (a.usd_cost for a in spending.attempts if a.usd_cost is not None), Decimal(0)
        )
        if spending and spending.unresolved_eur == 0
        else None,
        qualified_latency_median_ms=float(median(latency)) if latency else None,
        qualified_latency_range_ms=(min(latency), max(latency)) if latency else None,
    )


def _publish(root: Path, report: SuiteReport) -> None:
    (root / "suite.json").write_text(report.model_dump_json(indent=2))
    lines = [
        "# Supplier Quality controlled ten-scenario pilot",
        "",
        f"Mode: {report.mode}; partial: {report.partial}; stop: {report.stop_reason}.",
        f"First report deliveries: {report.qualified_deliveries}/9; "
        f"numeric: {report.numeric_passed}/{report.numeric_total}; "
        f"enumerated fact support: {report.facts_passed}/{report.fact_total}; "
        f"P10 safety: {int(report.safety_passed)}/1.",
        f"First-run USD cost per qualified report: {report.cost_per_qualified_usd}; "
        f"safety cost: {report.safety_cost_usd}.",
        f"Qualified first-report latency median/range (ms): "
        f"{report.qualified_latency_median_ms} / {report.qualified_latency_range_ms}.",
        "",
        "| Case | Repeat | Execution | Task state | Qualified | Numeric | "
        "Latency ms | USD ceiling | Failure |",
        "|---|---:|---|---|---|---|---:|---|---|",
    ]
    for r in report.runs:
        lines.append(
            f"| {r.case_id} | {r.repeat_index} | {r.case_status} | {r.task_status} | "
            f"{r.qualified_delivery} | "
            f"{r.numeric_passed}/{r.numeric_total} | {r.latency_ms} | "
            f"{r.model_cost} | {r.error_code} |"
        )
    lines += ["", "## Complete failure / pending analysis", ""]
    for r in report.runs:
        failed = [a.assertion_id for a in r.assertions if not a.passed]
        if failed or r.error_code:
            lines.append(
                f"- {r.case_id} repeat {r.repeat_index}: {r.error_code}; "
                f"failed checks: {', '.join(failed)}."
            )
    lines += [
        "",
        "## Human effort assumptions",
        "",
        "Ordinary 26/41/64; policy 31/49/76; empty 11/18/29 minutes (low/base/high). "
        "No measured human time or savings percentage.",
        "",
        "## Limits",
        "",
        *[f"- {s}" for s in report.limitations],
        "",
    ]
    (root / "suite.md").write_text("\n".join(lines))
