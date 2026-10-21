"""Durable first-population runner; experiment progress never authorizes business work."""

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import computed_field, model_validator

from copilot.config import Settings
from copilot.contracts import TaskStatus
from evaluation.contracts import EvaluationModel
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.budget import BudgetLedger, BudgetStopped, SpendingSnapshot
from evaluation.supplier_quality_pilot.contracts import PilotAssertion
from evaluation.supplier_quality_pilot.interfaces import SourceFacts
from evaluation.supplier_quality_pilot.live import live_model
from evaluation.supplier_quality_pilot.policy import PolicySnapshot
from evaluation.supplier_quality_pilot.runner import _code_hash, _git
from evaluation.supplier_quality_validation.bundle import CaseOracle
from evaluation.supplier_quality_validation.runner import (
    CaseInput,
    ValidationRun,
    run_validation_case,
)


class CaseDefinition(EvaluationModel):
    """Frozen score denominators and input identity for one case."""

    case_id: str
    expected_outcome: Literal["report", "denial"]
    input_hash: str
    numeric_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]


def execution_settings(settings: Settings) -> dict[str, int | float | str]:
    """Recorded model/transport conditions checked before paid continuation dispatch."""
    return {
        "max_output_tokens": settings.llm_max_output_tokens,
        "temperature": settings.llm_temperature,
        "provider_retries": settings.llm_max_retries,
        "transport_retries": 0,
        "clarification_runtime": "postgresql-worker",
        "read_timeout_seconds": settings.llm_read_timeout_seconds,
    }


class PopulationConfig(EvaluationModel):
    """Safe execution identity; contains no credential, provider body or database URL."""

    version: Literal["supplier-quality-validation-config.v1"] = (
        "supplier-quality-validation-config.v1"
    )
    freeze_hash: str
    frozen_revision: str
    code_revision: str | None
    code_hash: str
    git_dirty: bool
    source_hash: str
    policy_hash: str
    model: str
    pricing_hash: str
    parent_report_hash: str | None = None
    budget_hash: str
    settings: dict[str, int | float | str]
    cases: tuple[CaseDefinition, ...]


class ExecutionSegment(EvaluationModel):
    """First case attempts bound to each harness version and immutable stop report."""

    config: PopulationConfig
    case_ids: tuple[str, ...]
    report_hash: str


class ValidationReport(EvaluationModel):
    """Recomputable first outcomes and incremental versus aggregate spending."""

    version: Literal["supplier-quality-validation-report.v1"] = (
        "supplier-quality-validation-report.v1"
    )
    config: PopulationConfig
    started_at: datetime
    stopped_at: datetime
    stop_reason: str | None
    runs: tuple[ValidationRun, ...]
    spending: SpendingSnapshot
    prior_attempt_ids: tuple[str, ...]
    segments: tuple[ExecutionSegment, ...] = ()
    limitations: tuple[str, ...] = (
        "Synthetic validation authored by the same coding agent after development defects; "
        "not independently blinded.",
        "Different data/denominators from development; no pooled scores or direct "
        "success-rate improvement.",
        "Enumerated structured facts only; not general claim correctness or production "
        "reliability.",
        "USD peak-rate usage ceilings and conservative EUR/tax bounds, not provider invoices.",
        "Scripted clarification, not measured human interaction; human time savings unmeasured.",
        "Observed submit-to-stop latency includes scripted resume/overhead; not production "
        "p50/p95.",
    )

    @model_validator(mode="after")
    def validate_population(self) -> "ValidationReport":
        if tuple(r.case_id for r in self.runs) != tuple(c.case_id for c in self.config.cases):
            raise ValueError("population changed")
        if self.segments:
            if tuple(case_id for s in self.segments for case_id in s.case_ids) != tuple(
                r.case_id for r in self.runs
            ):
                raise ValueError("execution segment population changed")
            for segment in self.segments:
                for run in self.runs:
                    if (
                        run.case_id in segment.case_ids
                        and run.case_status != "unrun"
                        and (
                            run.code_revision != segment.config.code_revision
                            or run.code_hash != segment.config.code_hash
                        )
                    ):
                        raise ValueError("case harness provenance disagrees with its segment")
        for run, case in zip(self.runs, self.config.cases, strict=True):
            if (run.numeric_ids, run.fact_ids, run.expected_outcome) != (
                case.numeric_ids,
                case.fact_ids,
                case.expected_outcome,
            ):
                raise ValueError("case denominators changed")
            paid = tuple(a for a in self.spending.attempts if run.run_id and a.run_id == run.run_id)
            if run.attempts != paid:
                raise ValueError("case fees disagree with the experiment ledger")
        known = {a.attempt_id for a in self.spending.attempts}
        if (
            len(set(self.prior_attempt_ids)) != len(self.prior_attempt_ids)
            or not set(self.prior_attempt_ids) <= known
        ):
            raise ValueError("prior experiment attempts changed")
        run_ids = {r.run_id for r in self.runs if r.run_id}
        if any(
            a.run_id not in run_ids and a.attempt_id not in self.prior_attempt_ids
            for a in self.spending.attempts
        ):
            raise ValueError("unallocated incremental charge")
        if self.config.pricing_hash != canonical_hash(
            self.spending.pricing.model_dump(mode="json")
        ) or self.config.budget_hash != canonical_hash(
            self.spending.policy.model_dump(mode="json")
        ):
            raise ValueError("budget/price identity mismatch")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def qualified_deliveries(self) -> int:
        return sum(r.qualified_delivery for r in self.runs)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def report_total(self) -> int:
        return sum(c.expected_outcome == "report" for c in self.config.cases)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safety_passed(self) -> int:
        return sum(r.safety_passed for r in self.runs)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safety_total(self) -> int:
        return sum(c.expected_outcome == "denial" for c in self.config.cases)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def numeric_total(self) -> int:
        return sum(r.numeric_total for r in self.runs)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def numeric_passed(self) -> int:
        return sum(r.numeric_passed for r in self.runs)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fact_total(self) -> int:
        return sum(r.fact_total for r in self.runs)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fact_passed(self) -> int:
        return sum(r.fact_passed for r in self.runs)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def partial(self) -> bool:
        return (
            bool(self.stop_reason)
            or any(r.case_status in {"unrun", "interrupted"} for r in self.runs)
            or bool(self.spending.unresolved_eur)
            or any(
                r.case_status == "executed"
                and r.task_status
                not in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }
                for r in self.runs
            )
        )

    def _cost(self, outcome: str | None = None) -> Decimal | None:
        attempts = [a for a in self.spending.attempts if a.attempt_id not in self.prior_attempt_ids]
        if outcome:
            run_ids = {r.run_id for r in self.runs if r.expected_outcome == outcome}
            attempts = [a for a in attempts if a.run_id in run_ids]
        if any(a.status != "reconciled" or a.usd_cost is None for a in attempts):
            return None
        return sum((a.usd_cost for a in attempts if a.usd_cost is not None), Decimal(0))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def incremental_usd(self) -> Decimal | None:
        return self._cost()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def report_usd(self) -> Decimal | None:
        return self._cost("report")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safety_usd(self) -> Decimal | None:
        return self._cost("denial")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_per_qualified_usd(self) -> Decimal | None:
        cost = self.report_usd
        return (
            cost / self.qualified_deliveries
            if cost is not None and self.qualified_deliveries
            else None
        )


class CaseProgress(EvaluationModel):
    """Started is durable before dispatch; unfinished work cannot be silently retried."""

    case_id: str
    run_id: str
    started_at: datetime
    report_hash: str | None = None


class Progress(EvaluationModel):
    """One immutable population identity and append-only case starts/completions."""

    config_hash: str
    started_at: datetime
    prior_attempt_ids: tuple[str, ...]
    cases: tuple[CaseProgress, ...] = ()
    stopped_reason: str | None = None


def save_record(path: Path, value: EvaluationModel) -> None:
    """Atomically persist progress/results and fsync before subsequent paid dispatch."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        stream.write(value.model_dump_json(indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_run(data: dict[str, object]) -> ValidationRun:
    """Verify serialized computed case scores rather than trusting them."""
    raw = dict(data)
    computed = {key: raw.pop(key) for key in ValidationRun.model_computed_fields if key in raw}
    run = ValidationRun.model_validate(raw)
    if any(getattr(run, key) != value for key, value in computed.items()):
        # JSON decimal strings need JSON-mode comparison.
        actual = run.model_dump(mode="json")
        if any(actual[key] != value for key, value in computed.items()):
            raise ValueError("case scores/cost disagree with records")
    return run


def read_report(path: Path) -> ValidationReport:
    """Check published case and aggregate computed values."""
    raw = json.loads(path.read_bytes())
    computed = {key: raw.pop(key) for key in ValidationReport.model_computed_fields if key in raw}
    raw["runs"] = tuple(parse_run(r) for r in raw["runs"])
    report = ValidationReport.model_validate(raw)
    actual = report.model_dump(mode="json")
    if any(actual[key] != value for key, value in computed.items()):
        raise ValueError("aggregate scores/cost disagree with records")
    return report


def verify_cached_case(directory: Path, progress: CaseProgress) -> ValidationRun:
    """A retained score cannot outlive changed/missing case records or artifacts."""
    raw = json.loads((directory / "report.json").read_bytes())
    if canonical_hash(raw) != progress.report_hash:
        raise BudgetStopped("VALIDATION_RESULT_CHANGED")
    run = parse_run(raw)
    if len(run.artifact_paths) != len(run.artifact_checksums):
        raise BudgetStopped("VALIDATION_ARTIFACT_CHANGED")
    for path, checksum in zip(run.artifact_paths, run.artifact_checksums.values(), strict=True):
        artifact = Path(path).resolve()
        if (
            not artifact.is_relative_to(directory.resolve())
            or not artifact.is_file()
            or "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest() != checksum
        ):
            raise BudgetStopped("VALIDATION_ARTIFACT_CHANGED")
    return run


def unavailable_run(
    case: CaseInput,
    oracle: CaseOracle,
    config: PopulationConfig,
    reason: str,
    *,
    started: CaseProgress | None = None,
    ledger: BudgetLedger,
) -> ValidationRun:
    """Missing artifacts/facts stay false; possibly paid interrupted attempts stay attached."""
    return ValidationRun(
        case_id=case.case_id,
        expected_outcome=case.expected_outcome,
        mode="live",
        case_status="interrupted" if started else "unrun",
        code_revision=config.code_revision,
        code_hash=config.code_hash,
        run_id=started.run_id if started else None,
        started_at=started.started_at if started else None,
        latency_ms=max(0, round((datetime.now(UTC) - started.started_at).total_seconds() * 1000))
        if started
        else None,
        error_code=reason,
        numeric_ids=oracle.numeric_ids,
        fact_ids=oracle.fact_ids,
        source_hash=config.source_hash,
        policy_hash=config.policy_hash,
        attempts=tuple(
            a for a in ledger.snapshot().attempts if started and a.run_id == started.run_id
        ),
        assertions=(
            PilotAssertion(assertion_id="delivery_unavailable", category="lifecycle", passed=False),
            *(
                PilotAssertion(assertion_id=key, category="numeric", passed=False)
                for key in oracle.numeric_ids
            ),
            *(
                PilotAssertion(assertion_id=key, category="evidence", passed=False)
                for key in oracle.fact_ids
            ),
        ),
    )


def case_definitions(
    cases: tuple[CaseInput, ...], oracles: tuple[CaseOracle, ...]
) -> tuple[CaseDefinition, ...]:
    """Bind raw requests, scripted answers, evaluator scope and literal assertions."""
    return tuple(
        CaseDefinition(
            case_id=c.case_id,
            expected_outcome=c.expected_outcome,
            numeric_ids=o.numeric_ids,
            fact_ids=o.fact_ids,
            input_hash=canonical_hash(
                {
                    "request": c.request,
                    "output_format": c.output_format,
                    "scripted_answer": c.scripted_answer,
                    "expected_scope": {
                        "supplier_ids": c.suppliers,
                        "year": c.year,
                        "quarter": c.quarter,
                        "policy": c.policy,
                    },
                    "oracle": o.model_dump(mode="json"),
                }
            ),
        )
        for c, o in zip(cases, oracles, strict=True)
    )


def run_population(
    output_directory: Path,
    *,
    source: SourceFacts,
    cases: tuple[CaseInput, ...],
    oracles: tuple[CaseOracle, ...],
    policy: PolicySnapshot,
    freeze_hash: str,
    frozen_revision: str,
    ledger: BudgetLedger,
    settings: Settings,
    persistence_url: str | None = None,
    transport_factory: Callable[[], httpx.BaseTransport] | None = None,
    population_name: Literal[
        "supplier-quality-validation.v1",
        "supplier-quality-validation.v1-continuation",
        "supplier-quality-validation.regression.v1",
    ] = "supplier-quality-validation.v1",
    parent_report_hash: str | None = None,
) -> ValidationReport:
    """Run one ordered population, preserving every first outcome and original ledger.

    The CLI supplies the verified frozen bundle. Tests use different control facts/cases.
    No entry point executes the frozen population offline or tunes production prompts.
    """
    if not cases or tuple(c.case_id for c in cases) != tuple(o.case_id for o in oracles):
        raise ValueError("population/oracle mismatch")
    config = PopulationConfig(
        freeze_hash=freeze_hash,
        frozen_revision=frozen_revision,
        code_revision=_git("rev-parse", "HEAD"),
        code_hash=_code_hash(),
        git_dirty=bool(_git("status", "--porcelain")),
        source_hash=canonical_hash(
            {
                "tenant_id": source.tenant_id,
                "rows": [r.model_dump(mode="json") for r in source.rows],
            }
        ),
        policy_hash=canonical_hash(policy.model_dump(mode="json")),
        model=settings.llm_model,
        pricing_hash=canonical_hash(ledger.pricing.model_dump(mode="json")),
        budget_hash=canonical_hash(ledger.policy.model_dump(mode="json")),
        parent_report_hash=parent_report_hash,
        settings=execution_settings(settings),
        cases=case_definitions(cases, oracles),
    )
    output_directory = output_directory.resolve()
    config_hash = canonical_hash(config.model_dump(mode="json"))
    ledger.bind_population(
        population_name,
        canonical_hash({"output": str(output_directory), "config": config_hash}),
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    progress_path = output_directory / "progress.json"
    if progress_path.exists():
        progress = Progress.model_validate_json(progress_path.read_bytes())
        if progress.config_hash != config_hash:
            raise BudgetStopped("VALIDATION_CONFIG_CHANGED")
        if (
            all(c.report_hash for c in progress.cases)
            and (output_directory / "report.json").exists()
            and (len(progress.cases) == len(cases) or progress.stopped_reason)
        ):
            cached = read_report(output_directory / "report.json")
            for completed in progress.cases:
                retained = verify_cached_case(output_directory / completed.case_id, completed)
                if retained != next(r for r in cached.runs if r.case_id == completed.case_id):
                    raise BudgetStopped("VALIDATION_RESULT_CHANGED")
            if cached.spending.attempts != ledger.snapshot().attempts:
                raise BudgetStopped("VALIDATION_LEDGER_CHANGED")
            return cached
    else:
        if any(output_directory.iterdir()):
            raise BudgetStopped("VALIDATION_OUTPUT_NOT_EMPTY")
        progress = Progress(
            config_hash=config_hash,
            started_at=datetime.now(UTC),
            prior_attempt_ids=tuple(a.attempt_id for a in ledger.snapshot().attempts),
        )
        save_record(output_directory / "config.json", config)
        save_record(progress_path, progress)
    runs = []
    stop = progress.stopped_reason or ledger.snapshot().stopped_reason
    for index, (case, oracle) in enumerate(zip(cases, oracles, strict=True)):
        case_directory = output_directory / case.case_id
        started = progress.cases[index] if index < len(progress.cases) else None
        if started and started.case_id != case.case_id:
            raise BudgetStopped("VALIDATION_PROGRESS_CHANGED")
        if started and started.report_hash:
            run = verify_cached_case(case_directory, started)
        elif started:
            stop = "STARTED_CASE_REQUIRES_RECONCILIATION"
            run = unavailable_run(case, oracle, config, stop, started=started, ledger=ledger)
        elif stop:
            run = unavailable_run(case, oracle, config, stop, ledger=ledger)
        else:
            started = CaseProgress(
                case_id=case.case_id, run_id=uuid4().hex, started_at=datetime.now(UTC)
            )
            progress.cases += (started,)
            save_record(progress_path, progress)
            timer = perf_counter()
            try:
                with live_model(
                    ledger,
                    settings,
                    stage="suite",
                    run_id=started.run_id,
                    transport=transport_factory() if transport_factory else None,
                ) as live:
                    run = run_validation_case(
                        case_directory,
                        source=source,
                        case=case,
                        oracle=oracle,
                        policy=policy,
                        persistence_url=persistence_url,
                        live=live,
                    )
                if run.case_status == "interrupted":
                    stop = f"HARNESS_{run.error_code}"
                run.code_revision, run.code_hash = config.code_revision, config.code_hash
            except Exception as exc:
                stop = f"HARNESS_{type(exc).__name__}"
                run = unavailable_run(case, oracle, config, stop, started=started, ledger=ledger)
                run.latency_ms = max(0, round((perf_counter() - timer) * 1000))
            case_directory.mkdir(parents=True, exist_ok=True)
            save_record(case_directory / "report.json", run)
            started.report_hash = canonical_hash(run.model_dump(mode="json"))
            save_record(progress_path, progress)
        runs.append(run)
        stop = stop or ledger.snapshot().stopped_reason
        # A complete business failure is an outcome, not a reason to tune/retry the case.
        progress.stopped_reason = stop
        save_record(progress_path, progress)
        report = ValidationReport(
            config=config,
            started_at=progress.started_at,
            stopped_at=datetime.now(UTC),
            stop_reason=stop,
            runs=tuple(runs)
            + tuple(
                unavailable_run(c, o, config, stop or "NOT_YET_RUN", ledger=ledger)
                if future >= len(progress.cases)
                else unavailable_run(
                    c,
                    o,
                    config,
                    stop or "RESUME_RECONCILING",
                    ledger=ledger,
                    started=progress.cases[future],
                )
                for future, (c, o) in enumerate(
                    zip(cases[index + 1 :], oracles[index + 1 :], strict=True), start=index + 1
                )
            ),
            spending=ledger.snapshot(),
            prior_attempt_ids=progress.prior_attempt_ids,
        )
        if population_name == "supplier-quality-validation.regression.v1":
            report = report.model_copy(update={"limitations": REGRESSION_LIMITATIONS})
        save_record(output_directory / "report.json", report)
        (output_directory / "report.md").write_text(markdown(report))
    return report


REGRESSION_LIMITATIONS = (
    "Same synthetic V01–V20 inputs and checker as the audited baseline; now development "
    "regression data, not independent blinded validation or production reliability.",
    "All first outcomes remain separate; no adaptive case repeats or additional answers.",
    "Enumerated structured facts only; not general claim correctness.",
    "USD peak-rate usage ceilings and conservative EUR/tax bounds, not provider invoices.",
    "Scripted clarification; human time savings unmeasured.",
    "Qualified latency populations may differ; median/range do not measure speedup or p50/p95.",
)


def markdown(report: ValidationReport) -> str:
    """Render separate delivery/safety/number/fact metrics with every first outcome."""
    qualified_times = [
        r.latency_ms for r in report.runs if r.qualified_delivery and r.latency_ms is not None
    ]
    return "\n".join(
        [
            (
                "# Supplier Quality validation — development regression v1"
                if report.limitations[:1] == REGRESSION_LIMITATIONS[:1]
                else "# Supplier Quality validation v1 — first live population"
            ),
            "",
            f"Qualified reports: **{report.qualified_deliveries}/{report.report_total}**; "
            f"safety: **{report.safety_passed}/{report.safety_total}**; "
            f"numeric: **{report.numeric_passed}/{report.numeric_total}**; "
            f"enumerated facts: **{report.fact_passed}/{report.fact_total}**.",
            "",
            f"Partial: {report.partial}; stop: {report.stop_reason}.",
            f"UTC: {report.started_at.isoformat()} to {report.stopped_at.isoformat()}.",
            f"Incremental USD ceiling: {report.incremental_usd}; report USD: {report.report_usd}; "
            f"safety USD: {report.safety_usd}; per qualified: {report.cost_per_qualified_usd}.",
            f"Aggregate EUR upper: {report.spending.incurred_eur_upper}; unresolved EUR: "
            f"{report.spending.unresolved_eur}; attempts: {len(report.spending.attempts)}.",
            "Qualified submit-to-stop ms median/range: "
            f"{median(qualified_times) if qualified_times else None} / "
            f"{(min(qualified_times), max(qualified_times)) if qualified_times else None}.",
            f"Freeze: {report.config.freeze_hash}; "
            f"frozen revision: {report.config.frozen_revision}.",
            f"Execution code: {report.config.code_revision}; dirty: {report.config.git_dirty}; "
            f"hash: {report.config.code_hash}.",
            *[
                f"Segment {index}: code {segment.config.code_revision}; "
                f"cases {', '.join(segment.case_ids)}; result hash {segment.report_hash}."
                for index, segment in enumerate(report.segments, start=1)
            ],
            "",
            "| Case | State | Task status | Qualified | Safety | Numeric | Facts | "
            "Latency ms | USD | Error |",
            "|---|---|---|---|---|---|---|---|---|---|",
            *[
                f"| {r.case_id} | {r.case_status} | {r.task_status} | {r.qualified_delivery} | "
                f"{r.safety_passed} | {r.numeric_passed}/{r.numeric_total} | "
                f"{r.fact_passed}/{r.fact_total} | "
                f"{r.latency_ms} | {r.usd_cost} | {r.error_code} |"
                for r in report.runs
            ],
            "",
            "## Limitations",
            "",
            *[f"- {s}" for s in report.limitations],
            "",
        ]
    )
