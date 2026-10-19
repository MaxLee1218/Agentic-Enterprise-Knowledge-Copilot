"""Hermetic P01 orchestration through the production application boundary."""

import hashlib
import json
import logging
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, TypeAdapter
from pydantic_settings import PydanticBaseSettingsSource

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import build_workflow_container
from copilot.config import PROJECT_ROOT, Settings
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.llm.prompts import (
    PLANNER_PROMPT_VERSION,
    TASK_UNDERSTANDING_PROMPT_VERSION,
)
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
    LLMProvider,
    StructuredLLMResult,
)
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)
from copilot.services.workflows.models import WorkflowExecution
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.checker import evaluate_p01
from evaluation.supplier_quality_pilot.contracts import OFFLINE_LIMITATIONS, PilotRun, PilotSnapshot
from evaluation.supplier_quality_pilot.source import (
    DEFAULT_SOURCE,
    P01_REQUEST,
    P01_SUPPLIERS,
    seed_source,
)

if TYPE_CHECKING:
    from evaluation.supplier_quality_pilot.live import LiveModelSession

LOGGER = logging.getLogger(__name__)
TModel = TypeVar("TModel", bound=BaseModel)


class _OfflineSettings(Settings):
    """Exclude ambient environment and dotenv settings from an offline experiment."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseModel],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


class _RecordingModel:
    """Capture synthetic inputs to make oracle isolation independently inspectable."""

    def __init__(self, delegate: LLMProvider) -> None:
        self._delegate = delegate
        self.messages: list[tuple[LLMMessage, ...]] = []

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        output_schema: type[TModel],
        context: LLMCallContext,
        options: LLMGenerationOptions,
    ) -> StructuredLLMResult[TModel]:
        self.messages.append(tuple(messages))
        return self._delegate.generate_structured(
            messages=messages,
            output_schema=output_schema,
            context=context,
            options=options,
        )


def run_p01(
    output_directory: Path,
    *,
    source_path: Path = DEFAULT_SOURCE,
    live: "LiveModelSession | None" = None,
) -> PilotRun:
    """Run a governed task into an empty directory; live execution requires a session."""
    output_directory = output_directory.resolve()
    if output_directory.exists() and any(output_directory.iterdir()):
        raise ValueError("Pilot output directory must be empty")
    snapshot = PilotSnapshot.model_validate_json(source_path.read_bytes())
    output_directory.mkdir(parents=True, exist_ok=True)
    started_at, timer = datetime.now(UTC), perf_counter()
    model = _RecordingModel(live.provider if live else OfflineMockLLM())
    execution: WorkflowExecution | None = None
    task_id = None
    task_status = None
    error_code = None
    artifact_paths: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="supplier-quality-p01-") as temporary:
        root = Path(temporary)
        database_url = f"sqlite:///{root / 'source.db'}"
        seed_source(snapshot, database_url)
        settings = _OfflineSettings(
            app_env="test",
            database_url=database_url,
            database_provider="sqlalchemy",
            llm_provider="mock",  # The explicit injected provider owns live transport credentials.
            llm_max_output_tokens=live.settings.llm_max_output_tokens if live else 4096,
            llm_temperature=live.settings.llm_temperature if live else 0,
            knowledge_provider="mock",
            artifact_dir=output_directory / "artifacts",
            checkpoint_database_path=root / "checkpoints.db",
            checkpoint_enabled=True,
            max_task_steps=14,
            workflow_retry_delay_seconds=0,
            observability_enabled=False,
            log_level="ERROR",
        )
        with build_workflow_container(
            settings,
            ids=SequentialIdentifierFactory(),
            llm_provider=model,
        ) as container:
            try:
                execution = container.task_service.submit(
                    NaturalLanguageTaskCommand(
                        task=P01_REQUEST,
                        output_format=TaskOutputFormat.JSON,
                        source=RequestSource.INTERNAL,
                    ),
                    TrustedCallerContext(
                        user_id="U-PILOT",
                        tenant_id=snapshot.tenant_id,
                        data_scope=("quality.v1",),
                        supplier_ids=P01_SUPPLIERS,
                    ),
                )
                task_id = execution.task_result.task_id
                task_status = execution.task_result.final_status
                artifact_paths = {
                    a.artifact_id: container.artifacts.path_for(a) for a in execution.artifacts
                }
                (output_directory / "execution.json").write_bytes(
                    TypeAdapter(WorkflowExecution).dump_json(execution, indent=2)
                )
                (output_directory / "evidence.json").write_text(
                    json.dumps([e.model_dump(mode="json") for e in execution.evidence], indent=2)
                )
            except WorkflowInterrupted as exc:
                task_id = exc.task_id
                task_status = container.repository.state_for(
                    task_id,
                    tenant_id=snapshot.tenant_id,
                ).state
                error_code = "WORKFLOW_INTERRUPTED"
            except Exception as exc:
                # Safe harness failure, recorded separately from a failed business Task.
                error_code = type(exc).__name__
    checks = evaluate_p01(snapshot, execution, artifact_paths)
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
        if (attempts and all(a.status == "reconciled" for a in attempts))
        else None
    )
    model_name = live.settings.llm_model if live else "offline-governed-domains-v2"
    config = {
        "case_id": "P01",
        "mode": "live" if live else "offline",
        "database": "sqlite-read-only",
        "request": P01_REQUEST,
        "supplier_ids": P01_SUPPLIERS,
        "tenant_id": snapshot.tenant_id,
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
            "One synthetic P01 calibration; not a held-out or ten-case benchmark.",
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
        assertions=checks,
    )
    (output_directory / "source.json").write_text(snapshot.model_dump_json(indent=2))
    (output_directory / "config.json").write_text(json.dumps(config, indent=2))
    (output_directory / "model-inputs.json").write_text(
        json.dumps(
            [[m.model_dump(mode="json") for m in call] for call in model.messages],
            indent=2,
        )
    )
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
        for path in sorted((PROJECT_ROOT / directory).rglob("*.py")):
            digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _markdown(run: PilotRun) -> str:
    lines = [
        f"# {run.mode.title()} Supplier Quality P01 evaluation",
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
