"""Hermetic P01 orchestration through the production application boundary."""

import hashlib
import json
import logging
import subprocess
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TypeVar

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
from evaluation.supplier_quality_pilot.contracts import PilotRun, PilotSnapshot
from evaluation.supplier_quality_pilot.source import (
    DEFAULT_SOURCE,
    P01_REQUEST,
    P01_SUPPLIERS,
    seed_source,
)

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


class _RecordingOfflineModel:
    """Capture synthetic inputs to make oracle isolation independently inspectable."""

    def __init__(self) -> None:
        self._delegate = OfflineMockLLM()
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


def run_p01(output_directory: Path, *, source_path: Path = DEFAULT_SOURCE) -> PilotRun:
    """Run one offline task into an empty output directory; never overwrite an earlier run."""
    output_directory = output_directory.resolve()
    if output_directory.exists() and any(output_directory.iterdir()):
        raise ValueError("Pilot output directory must be empty")
    snapshot = PilotSnapshot.model_validate_json(source_path.read_bytes())
    output_directory.mkdir(parents=True, exist_ok=True)
    started_at, timer = datetime.now(UTC), perf_counter()
    model = _RecordingOfflineModel()
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
            llm_provider="mock",
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
    config = {
        "case_id": "P01",
        "mode": "offline",
        "database": "sqlite-read-only",
        "request": P01_REQUEST,
        "supplier_ids": P01_SUPPLIERS,
        "tenant_id": snapshot.tenant_id,
        "model": "offline-governed-domains-v2",
    }
    run = PilotRun(
        started_at=started_at,
        latency_ms=max(0, round((perf_counter() - timer) * 1000)),
        task_id=task_id,
        task_status=task_status,
        error_code=error_code,
        model="offline-governed-domains-v2",
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
        "# Offline Supplier Quality P01 evaluation",
        "",
        f"Qualified delivery: **{run.qualified_delivery}**",
        f"Numeric assertions: **{run.numeric_passed}/{run.numeric_total}**",
        "",
        f"Task: `{run.task_id}`; status: `{run.task_status}`; error: `{run.error_code}`.",
        f"Provider/model: `{run.provider}` / `{run.model}`; model cost: unavailable.",
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
