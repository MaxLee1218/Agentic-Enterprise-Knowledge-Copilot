"""Hermetic P01 orchestration through the production application boundary."""

import hashlib
import json
import logging
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from copilot.config import PROJECT_ROOT
from copilot.contracts import TaskStatus
from copilot.llm.prompts import (
    PLANNER_PROMPT_VERSION,
    TASK_UNDERSTANDING_PROMPT_VERSION,
)
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
)
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.cases import PilotCase
from evaluation.supplier_quality_pilot.checker import evaluate_case
from evaluation.supplier_quality_pilot.contracts import (
    OFFLINE_LIMITATIONS,
    PilotAssertion,
    PilotRun,
    PilotSnapshot,
)
from evaluation.supplier_quality_pilot.source import (
    DEFAULT_SOURCE,
    P01_REQUEST,
)

if TYPE_CHECKING:
    from evaluation.supplier_quality_pilot.live import LiveModelSession

LOGGER = logging.getLogger(__name__)
TModel = TypeVar("TModel", bound=BaseModel)


def run_case(
    output_directory: Path,
    *,
    source_path: Path = DEFAULT_SOURCE,
    case: PilotCase,
    persistence_url: str | None = None,
    live: "LiveModelSession | None" = None,
) -> PilotRun:
    """Run a governed task into an empty directory; live execution requires a session."""
    from evaluation.supplier_quality_pilot.execution import capture_task
    from evaluation.supplier_quality_pilot.policy import POLICY_PATH, PolicySnapshot

    started_at, timer = datetime.now(UTC), perf_counter()
    snapshot = PilotSnapshot.model_validate_json(source_path.read_bytes())
    captured = capture_task(
        output_directory,
        snapshot=snapshot,
        command=NaturalLanguageTaskCommand(
            task=case.request,
            output_format=TaskOutputFormat(case.output_format),
            source=RequestSource.INTERNAL,
        ),
        scripted_answer=case.scripted_answer,
        policy=PolicySnapshot.model_validate_json(POLICY_PATH.read_bytes())
        if case.policy
        else None,
        persistence_url=persistence_url,
        live=live,
    )
    execution, task_id, task_status, error_code, artifact_paths, interaction = (
        captured.execution,
        captured.task_id,
        captured.task_status,
        captured.error_code,
        captured.artifact_paths,
        captured.interaction,
    )
    checks = evaluate_case(snapshot, execution, artifact_paths, case)
    if case.scripted_answer:
        checks += tuple(
            PilotAssertion(assertion_id=n, category="lifecycle", passed=p)
            for n, p in (
                (
                    "clarification_before_tools",
                    interaction is not None
                    and interaction.initial_status is TaskStatus.WAITING_CLARIFICATION
                    and interaction.tools_before_answer == 0,
                ),
                (
                    "same_task_scripted_resume",
                    interaction is not None
                    and interaction.answer_applied is True
                    and interaction.initial_task_id == interaction.final_task_id,
                ),
            )
        )
    spending = live.ledger.snapshot() if live else None
    attempts = (
        tuple(a for a in spending.attempts if a.run_id == live.run_id)
        if (live and spending)
        else ()
    )
    if spending and spending.stopped_reason:
        error_code = spending.stopped_reason
    model_cost = (
        sum((a.usd_cost for a in attempts if a.usd_cost is not None), Decimal(0))
        if live and all(a.status == "reconciled" for a in attempts)
        else None
    )
    model_name = live.settings.llm_model if live else "offline-governed-domains-v2"
    config = {
        "case_id": case.case_id,
        "mode": "live" if live else "offline",
        "database": "sqlite-read-only",
        "request": case.request,
        "supplier_ids": case.suppliers,
        "tenant_id": snapshot.tenant_id,
        "case_scope": case.model_dump(mode="json"),
        "model": model_name,
        **(
            {
                "pricing": live.ledger.pricing.model_dump(mode="json"),
                "budget": live.ledger.policy.model_dump(mode="json"),
                "max_output_tokens": live.settings.llm_max_output_tokens,
                "temperature": live.settings.llm_temperature,
                "provider_retries": live.settings.llm_max_retries,
                "transport_retries": 0,
                "ledger_path": str(live.ledger.path.resolve()),
            }
            if live
            else {}
        ),
    }
    run = PilotRun(
        case_id=case.case_id,
        expected_outcome=case.expected_outcome,
        interaction=interaction,
        started_at=started_at,
        latency_ms=max(0, round((perf_counter() - timer) * 1000)),
        task_id=task_id,
        task_status=task_status,
        error_code=error_code,
        model=model_name,
        mode="live" if live else "offline",
        provider="deepseek" if live else "mock",
        model_cost=model_cost,
        model_cost_currency="USD" if model_cost is not None else None,
        run_id=live.run_id if live else None,
        spending=spending,
        limitations=(
            "Controlled synthetic case; not a held-out benchmark or production success rate.",
            "USD cost calculated from real provider usage at pinned ceiling rates; not an invoice.",
            "EUR budget uses explicit FX and tax contingencies; human time is unmeasured.",
            "Independent support covers the enumerated structured numeric facts only.",
        )
        if live
        else OFFLINE_LIMITATIONS,
        code_revision=_git("rev-parse", "HEAD"),
        code_hash=_code_hash(),
        git_dirty=bool(_git("status", "--porcelain")),
        source_hash=canonical_hash(snapshot.model_dump(mode="json")),
        config_hash=canonical_hash(config),
        source_version=snapshot.version,
        prompt_versions=(TASK_UNDERSTANDING_PROMPT_VERSION, PLANNER_PROMPT_VERSION),
        executed_tools=tuple(s.tool_name for s in execution.step_executions) if execution else (),
        artifact_paths=tuple(str(p) for p in artifact_paths.values()),
        artifact_ids=tuple(a.artifact_id for a in execution.artifacts) if execution else (),
        artifact_checksums={a.artifact_id: a.checksum for a in execution.artifacts}
        if execution
        else {},
        assertions=checks,
    )
    (output_directory / "source.json").write_text(snapshot.model_dump_json(indent=2))
    (output_directory / "config.json").write_text(json.dumps(config, indent=2))
    if spending:
        (output_directory / "spending.json").write_text(spending.model_dump_json(indent=2))
    (output_directory / "report.json").write_text(run.model_dump_json(indent=2))
    (output_directory / "report.md").write_text(_markdown(run))
    LOGGER.info(
        "Supplier Quality pilot finished",
        extra={
            "event": "supplier_quality_pilot_finished",
            "task_id": task_id,
            "qualified_delivery": run.qualified_delivery,
            "error_code": error_code,
        },
    )
    return run


def _git(*arguments: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *arguments],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _code_hash() -> str:
    digest = hashlib.sha256()
    for directory in ("src", "evaluation"):
        paths = (p for p in (PROJECT_ROOT / directory).rglob("*") if p.suffix in {".py", ".sql"})
        for path in sorted(paths):
            digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _markdown(run: PilotRun) -> str:
    lines = [
        f"# {run.mode.title()} Supplier Quality {run.case_id} evaluation",
        "",
        f"Qualified delivery: **{run.qualified_delivery}**",
        f"Numeric assertions: **{run.numeric_passed}/{run.numeric_total}**",
        "",
        f"Task: `{run.task_id}`; status: `{run.task_status}`; error: `{run.error_code}`.",
        f"Provider/model: `{run.provider}` / `{run.model}`; "
        "usage-based USD cost ceiling: "
        f"{run.model_cost if run.model_cost is not None else 'unavailable'}.",
        f"Started UTC: {run.started_at.isoformat()}; observed latency: {run.latency_ms} ms.",
        f"Code: `{run.code_revision}`; dirty: `{run.git_dirty}`.",
        f"Code hash: `{run.code_hash}`; source: `{run.source_hash}`.",
        f"Configuration: `{run.config_hash}`; source version: `{run.source_version}`.",
        f"Prompt versions: {', '.join(run.prompt_versions)}.",
        "",
        "## Assertions",
        "",
        "| Assertion | Category | Pass | Expected | Actual |",
        "|---|---|---|---|---|",
        *[
            f"| {a.assertion_id} | {a.category} | {a.passed} | {a.expected} | {a.actual} |"
            for a in run.assertions
        ],
        "",
        "## Artifacts",
        "",
        *[
            f"- [JSON report]({Path(p).relative_to(Path(p).parents[1]).as_posix()})"
            for p in run.artifact_paths
        ],
        "",
        "## Limitations",
        "",
        *[f"- {limitation}" for limitation in run.limitations],
        "",
    ]
    return "\n".join(lines)


def run_p01(
    output_directory: Path,
    *,
    source_path: Path = DEFAULT_SOURCE,
    live: "LiveModelSession | None" = None,
) -> PilotRun:
    """Retain backward-compatible calibration without changing its defaults."""
    return run_case(
        output_directory,
        source_path=source_path,
        case=PilotCase(case_id="P01", request=P01_REQUEST),
        live=live,
    )
