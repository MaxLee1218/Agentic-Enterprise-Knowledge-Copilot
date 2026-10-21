"""Hermetic P01 orchestration through the production application boundary."""

import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, TypeAdapter
from pydantic_settings import PydanticBaseSettingsSource

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import build_workflow_container
from copilot.config import Settings
from copilot.contracts import TaskStatus
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory, UuidIdentifierFactory
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
    LLMProvider,
    StructuredLLMResult,
)
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    TaskIntakeValidationError,
    TrustedCallerContext,
)
from copilot.services.workflows.models import WorkflowExecution
from evaluation.supplier_quality_pilot.contracts import (
    ClarificationObservation,
)
from evaluation.supplier_quality_pilot.interfaces import SourceFacts
from evaluation.supplier_quality_pilot.policy import PolicySnapshot
from evaluation.supplier_quality_pilot.source import (
    P01_SUPPLIERS,
    seed_source,
)

if TYPE_CHECKING:
    from evaluation.supplier_quality_pilot.live import LiveModelSession

TModel = TypeVar("TModel", bound=BaseModel)


class IsolatedEvaluationSettings(Settings):
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


class RecordingModel:
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


@dataclass(frozen=True)
class CapturedTask:
    """Observed business execution, not a Task authority or evaluator answer."""

    execution: WorkflowExecution | None
    task_id: str | None
    task_status: TaskStatus | None
    error_code: str | None
    artifact_paths: dict[str, Path]
    interaction: ClarificationObservation | None
    harness_error: bool = False
    intake_rejected: bool = False


def capture_task(
    output_directory: Path,
    *,
    snapshot: SourceFacts,
    command: NaturalLanguageTaskCommand,
    scripted_answer: str | None = None,
    policy: PolicySnapshot | None = None,
    persistence_url: str | None = None,
    live: "LiveModelSession | None" = None,
) -> CapturedTask:
    """Run the unchanged governed application with source facts and raw request only.

    Oracle, expected scope and expected outcomes cannot cross this seam. Trusted caller
    authorization is always S-100/S-200 regardless of the requested or expected scope.
    """
    output_directory = output_directory.resolve()
    if output_directory.exists() and any(output_directory.iterdir()):
        raise ValueError("Pilot output directory must be empty")
    output_directory.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    model = RecordingModel(live.provider if live else OfflineMockLLM())
    execution: WorkflowExecution | None = None
    task_id = None
    task_status = None
    error_code = None
    artifact_paths: dict[str, Path] = {}
    interaction: ClarificationObservation | None = None
    harness_error = False
    intake_rejected = False
    with tempfile.TemporaryDirectory(prefix="supplier-quality-p01-") as temporary:
        root = Path(temporary)
        database_url = f"sqlite:///{root / 'source.db'}"
        seed_source(snapshot, database_url)
        settings = IsolatedEvaluationSettings(
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
            persistence_database_url=persistence_url,
            persistence_auto_create_schema=not bool(persistence_url),
            max_task_steps=14,
            workflow_retry_delay_seconds=0,
            observability_enabled=False,
            log_level="ERROR",
        )
        with build_workflow_container(
            settings,
            ids=UuidIdentifierFactory() if persistence_url else SequentialIdentifierFactory(),
            llm_provider=model,
        ) as container:
            if policy is not None:
                from copilot.tools.knowledge.tool import KnowledgeTool
                from evaluation.supplier_quality_pilot.policy import (
                    SnapshotKnowledgeClient,
                )

                container.registry.unregister("knowledge_search")
                container.registry.register(KnowledgeTool(SnapshotKnowledgeClient(policy)))
            try:
                caller = TrustedCallerContext(
                    user_id="U-PILOT",
                    tenant_id=snapshot.tenant_id,
                    data_scope=("quality.v1",),
                    supplier_ids=P01_SUPPLIERS,
                )
                if scripted_answer and persistence_url:
                    from evaluation.supplier_quality_pilot.interaction import scripted_clarification

                    execution, interaction = scripted_clarification(
                        container, command, caller, scripted_answer
                    )
                    if execution is None:
                        assert interaction is not None
                        task_id = interaction.initial_task_id
                        task_status = container.repository.state_for(
                            task_id, tenant_id=snapshot.tenant_id
                        ).state
                        error_code = "SCRIPTED_TASK_PENDING"
                else:
                    execution = container.task_service.submit(command, caller)
                if execution is None:
                    raise WorkflowInterrupted(
                        "Scripted task remains pending",
                        task_id=str(task_id),
                        trace_id=str(task_id),
                        status=str(task_status),
                        created_at=started_at,
                    )
                task_id = execution.task_result.task_id
                task_status = execution.task_result.final_status
                if execution.errors:
                    error_code = execution.errors[-1].error_code
                artifact_paths = {
                    a.artifact_id: container.artifacts.path_for(a) for a in execution.artifacts
                }
                (output_directory / "execution.json").write_bytes(
                    TypeAdapter(WorkflowExecution).dump_json(execution, indent=2)
                )
                (output_directory / "evidence.json").write_text(
                    json.dumps([e.model_dump(mode="json") for e in execution.evidence], indent=2)
                )
            except TaskIntakeValidationError as exc:
                # Deterministic product refusal before persistence/model, not a harness IO fault.
                error_code = exc.code
                intake_rejected = True
            except WorkflowInterrupted as exc:
                task_id = exc.task_id
                task_status = container.repository.state_for(
                    task_id,
                    tenant_id=snapshot.tenant_id,
                ).state
                error_code = "WORKFLOW_INTERRUPTED"
                if scripted_answer and interaction is None:
                    state = container.engine.get_state(task_id, snapshot.tenant_id)
                    interaction = ClarificationObservation(
                        initial_task_id=task_id,
                        initial_status=task_status,
                        tools_before_answer=sum(s.executed for s in state["step_executions"]),
                        answer=scripted_answer,
                    )
                    error_code = "SCRIPTED_POSTGRES_RUNTIME_REQUIRED"
            except Exception as exc:
                # Safe harness failure, recorded separately from a failed business Task.
                error_code = type(exc).__name__
                harness_error = True
    (output_directory / "model-inputs.json").write_text(
        json.dumps(
            [[m.model_dump(mode="json") for m in call] for call in model.messages],
            indent=2,
        )
    )
    return CapturedTask(
        execution,
        task_id,
        task_status,
        error_code,
        artifact_paths,
        interaction,
        harness_error,
        intake_rejected,
    )
