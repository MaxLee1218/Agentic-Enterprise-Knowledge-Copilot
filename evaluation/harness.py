"""Isolated harness that executes every case through the production Task Service and Graph."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TypeVar, cast

from pydantic import BaseModel, JsonValue

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.contracts import (
    ApprovalResolutionAction,
    Artifact,
    JsonObject,
    TaskPlan,
)
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.approval_service import (
    ApprovalResolutionCommand,
    ApprovalResolutionResult,
    ApprovalServiceError,
)
from copilot.services.artifact_service import ArtifactServiceError
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
    LLMProvider,
    LLMSchemaValidationError,
    LLMUsage,
    StructuredLLMResult,
)
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)
from copilot.services.workflows.models import WorkflowExecution
from copilot.tools.mock_supplier_quality import MockBehavior, MockFailureKind
from evaluation.contracts import (
    CapturedExecution,
    EvaluationCase,
    FaultInjectionSpec,
    LLMUsageRecord,
)

TModel = TypeVar("TModel", bound=BaseModel)
_FIXED_NOW = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)


class RecordingEvaluationLLM(LLMProvider):
    """Offline provider wrapper with deterministic usage and bounded plan fault injection."""

    def __init__(self, *, plan_fault: str | None = None) -> None:
        self._delegate = OfflineMockLLM()
        self._plan_fault = plan_fault
        self.records: list[LLMUsageRecord] = []

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        output_schema: type[TModel],
        context: LLMCallContext,
        options: LLMGenerationOptions,
    ) -> StructuredLLMResult[TModel]:
        result = self._delegate.generate_structured(
            messages=messages,
            output_schema=output_schema,
            context=context,
            options=options,
        )
        if output_schema is TaskPlan and context.node_name == "create_plan":
            if self._plan_fault == "cycle":
                raise LLMSchemaValidationError(
                    "LLM output failed TaskPlan DAG validation: cyclic dependency"
                )
            if self._plan_fault in {"unregistered_tool", "database_write"}:
                plan = cast(TaskPlan, result.parsed_output)
                invalid_step = plan.steps[0].model_copy(update={"tool_name": self._plan_fault})
                invalid_plan = plan.model_copy(update={"steps": (invalid_step, *plan.steps[1:])})
                result = result.model_copy(update={"parsed_output": invalid_plan})
        usage = LLMUsage(input_tokens=120, output_tokens=80, total_tokens=200)
        result = result.model_copy(update={"usage": usage})
        self.records.append(
            LLMUsageRecord(
                node_name=context.node_name,
                provider=result.provider,
                model="offline-supplier-quality-eval-v1",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                latency_ms=result.latency_ms,
            )
        )
        return result


class EvaluationHarness:
    """Create a task-local runtime, execute one case, capture facts, and clean everything up."""

    def __init__(self, *, dataset_directory: Path, seed: int = 42) -> None:
        self._dataset_directory = dataset_directory.resolve()
        self._seed = seed

    def execute(self, case: EvaluationCase, work_directory: Path) -> CapturedExecution:
        """Execute a case in a private SQLite/checkpoint/artifact environment."""
        random.seed(f"{self._seed}:{case.case_id}")
        started_at = datetime.now(UTC)
        timer = perf_counter()
        task_id: str | None = None
        execution: WorkflowExecution | None = None
        interrupted = False
        harness_error: str | None = None
        provider = RecordingEvaluationLLM(plan_fault=self._plan_fault(case))
        fixtures = self._load_fixtures(case)
        settings = self._settings(case, work_directory)
        container: WorkflowContainer | None = None
        try:
            container = build_workflow_container(
                settings,
                ids=SequentialIdentifierFactory(),
                clock=lambda: _FIXED_NOW,
                sleeper=lambda _seconds: None,
                knowledge_behavior=self._knowledge_behavior(case, fixtures),
                database_behavior=self._database_behavior(case, fixtures),
                report_behavior=self._report_behavior(case, fixtures),
                llm_provider=provider,
            )
            caller = self._caller(case)
            command = NaturalLanguageTaskCommand(
                task=case.task_input.raw_input,
                output_format=(
                    TaskOutputFormat(case.task_input.output_format)
                    if case.task_input.output_format is not None
                    else None
                ),
                max_steps=case.task_input.max_steps,
                read_only=case.task_input.read_only,
                require_approval=case.task_input.require_approval,
                metadata=case.task_input.metadata,
                source=RequestSource.INTERNAL,
            )
            try:
                execution = container.task_service.submit(command, caller)
                task_id = execution.task_result.task_id
            except WorkflowInterrupted as exc:
                interrupted = True
                task_id = exc.task_id
                action = case.execution_config.approval_action
                if action and action != "pause" and exc.approval_id:
                    try:
                        resolution = self._resolve_approval(
                            container,
                            case,
                            caller,
                            task_id,
                            exc.approval_id,
                        )
                        execution = resolution.execution
                        interrupted = execution is None
                    except ApprovalServiceError:
                        # Expected authorization denials remain Agent outcomes, not harness errors.
                        execution = None
                        interrupted = True
        except (
            Exception
        ) as exc:  # captured as harness/evaluator data, never treated as Agent success
            harness_error = _safe_exception(exc)
        completed_at = datetime.now(UTC)
        latency_ms = max(0, round((perf_counter() - timer) * 1000))
        if container is None:
            return CapturedExecution(
                task_id=task_id,
                started_at=started_at,
                completed_at=completed_at,
                latency_ms=latency_ms,
                task_request_text=case.task_input.raw_input,
                interrupted=interrupted,
                harness_error=harness_error or "Evaluation harness setup failed",
            )
        try:
            return self._capture(
                container,
                case,
                provider,
                task_id,
                execution,
                started_at,
                completed_at,
                latency_ms,
                interrupted,
                harness_error,
            )
        finally:
            container.close()

    def _capture(
        self,
        container: WorkflowContainer,
        case: EvaluationCase,
        provider: RecordingEvaluationLLM,
        task_id: str | None,
        execution: WorkflowExecution | None,
        started_at: datetime,
        completed_at: datetime,
        latency_ms: int,
        interrupted: bool,
        harness_error: str | None,
    ) -> CapturedExecution:
        state = None
        if task_id:
            try:
                state = container.engine.get_state(task_id, case.actor_context.tenant_id)
            except ValueError:
                state = None
        plan = state.get("plan") if state else None
        terminal = (
            execution.final_state.state
            if execution is not None
            else (state["domain_state"].state if state is not None else None)
        )
        tool_results = container.repository.tool_results_for(task_id) if task_id else ()
        approvals = container.approval_repository.list_by_task(task_id) if task_id else ()
        artifact_probe = self._artifact_authorization_probe(container, case, task_id)
        events = _workflow_events(container, task_id)
        errors = (
            execution.errors
            if execution is not None
            else tuple(state["errors"] if state is not None else ())
        )
        step_results = (
            execution.step_results
            if execution is not None
            else (container.repository.step_results_for(task_id) if task_id else ())
        )
        retries = sum(max(result.attempt - 1, 0) for result in tool_results)
        return CapturedExecution(
            task_id=task_id,
            trace_id=(
                execution.trace_id
                if execution is not None
                else (state["trace_id"] if state else None)
            ),
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
            terminal_task_status=terminal,
            task_request_text=case.task_input.raw_input,
            task_contract=state.get("contract") if state else None,
            plan_snapshot=plan,
            tool_calls=tuple(state["tool_calls"] if state is not None else ()),
            tool_results=tool_results,
            step_results=step_results,
            evidence=container.evidence.list_for_task(task_id) if task_id else (),
            artifacts=container.artifacts.list_by_task(task_id) if task_id else (),
            artifact_texts=tuple(
                _artifact_text(container, artifact)
                for artifact in (container.artifacts.list_by_task(task_id) if task_id else ())
            ),
            verification_result=(
                execution.verification_result
                if execution is not None
                else (state["verification_result"] if state is not None else None)
            ),
            approvals=approvals,
            errors=errors,
            warnings=_warnings(step_results),
            workflow_events=events,
            tool_audit_events=_tool_audit_events(container, task_id),
            artifact_authorization_probe=artifact_probe,
            llm_usage=tuple(provider.records),
            retry_count=retries,
            replan_count=int(state["replan_count"] if state is not None else 0),
            plan_repair_count=int(state["plan_repair_count"] if state is not None else 0),
            interrupted=interrupted,
            harness_error=harness_error,
        )

    @staticmethod
    def _artifact_authorization_probe(
        container: WorkflowContainer,
        case: EvaluationCase,
        task_id: str | None,
    ) -> str | None:
        if "artifact_authorization" not in case.tags:
            return None
        artifacts = container.artifacts.list_by_task(task_id) if task_id else ()
        if task_id is None or not artifacts:
            return "NO_ARTIFACT"
        attacker = TrustedCallerContext(
            user_id="U-EVAL-OTHER",
            tenant_id=case.actor_context.tenant_id,
            data_scope=case.actor_context.data_scope,
            roles=("quality_analyst",),
            authentication_source="evaluation_fixture",
            is_demo_identity=False,
            purpose="supplier_quality_analysis.v1",
        )
        try:
            container.artifact_service.get_task_artifact(
                task_id,
                artifacts[0].artifact_id,
                attacker,
                trace_id="TRACE-ARTIFACT-PROBE",
            )
        except (ArtifactServiceError, RuntimeError):
            return "DENIED"
        return "ALLOWED"

    def _resolve_approval(
        self,
        container: WorkflowContainer,
        case: EvaluationCase,
        caller: TrustedCallerContext,
        task_id: str,
        approval_id: str,
    ) -> ApprovalResolutionResult:
        action_name = case.execution_config.approval_action or "approve"
        action = ApprovalResolutionAction(action_name.upper())
        pending = container.approval_repository.get(approval_id)
        edited: JsonObject | None = None
        if action is ApprovalResolutionAction.EDIT:
            values = dict(pending.proposed_arguments.root)
            values.update(case.execution_config.approval_edit or {"row_limit": 5000})
            edited = JsonObject(values)
        return container.approval_service.resolve(
            ApprovalResolutionCommand(
                task_id=task_id,
                approval_id=approval_id,
                action=action,
                reason=(
                    "Deterministic evaluation approval"
                    if action is not ApprovalResolutionAction.APPROVE
                    else None
                ),
                edited_arguments=edited,
            ),
            caller,
        )

    @staticmethod
    def _caller(case: EvaluationCase) -> TrustedCallerContext:
        actor = case.actor_context
        roles = tuple(
            dict.fromkeys((*actor.approval_permissions, *((actor.role,) if actor.role else ())))
        )
        return TrustedCallerContext(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            data_scope=actor.data_scope,
            supplier_ids=actor.supplier_ids,
            roles=roles,
            authentication_source="evaluation_fixture",
            is_demo_identity=True,
            purpose="supplier_quality_analysis.v1",
            policy_requires_approval=case.task_input.require_approval,
            policy_forces_read_only=True,
        )

    @staticmethod
    def _settings(case: EvaluationCase, work_directory: Path) -> Settings:
        work_directory.mkdir(parents=True, exist_ok=True)
        plan_fault = any(item.target == "planner" for item in case.fault_injection)
        return Settings(
            app_env="test",
            database_url="sqlite:///unused-evaluation.db",
            artifact_dir=work_directory / "artifacts",
            checkpoint_database_path=work_directory / "workflow.db",
            workflow_retry_delay_seconds=0,
            max_plan_repair_attempts=0 if plan_fault else 2,
            max_total_execution_seconds=case.execution_config.timeout_seconds,
        )

    def _load_fixtures(self, case: EvaluationCase) -> tuple[dict[str, object], ...]:
        fixtures: list[dict[str, object]] = []
        root = self._dataset_directory / "fixtures"
        for reference in case.fixture_refs:
            raw = json.loads((root / reference).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"Fixture root must be an object: {reference}")
            fixtures.append(raw)
        return tuple(fixtures)

    @staticmethod
    def _knowledge_behavior(
        case: EvaluationCase, fixtures: tuple[dict[str, object], ...]
    ) -> MockBehavior:
        empty = any(item.get("knowledge_result") == "empty" for item in fixtures)
        fault = next(
            (item for item in case.fault_injection if item.target == "knowledge_search"), None
        )
        return _mock_behavior(fault, empty_result=empty)

    @staticmethod
    def _database_behavior(
        case: EvaluationCase, fixtures: tuple[dict[str, object], ...]
    ) -> MockBehavior:
        empty = any(item.get("database_result") == "empty" for item in fixtures)
        zero = any(item.get("database_result") == "zero_denominator" for item in fixtures)
        fault = next(
            (item for item in case.fault_injection if item.target == "database_query"), None
        )
        return _mock_behavior(fault, empty_result=empty, zero_denominator=zero)

    @staticmethod
    def _plan_fault(case: EvaluationCase) -> str | None:
        fault = next((item for item in case.fault_injection if item.target == "planner"), None)
        return fault.failure_type if fault is not None else None

    @staticmethod
    def _report_behavior(
        case: EvaluationCase, fixtures: tuple[dict[str, object], ...]
    ) -> MockBehavior | None:
        fault = next(
            (item for item in case.fault_injection if item.target == "report_generator"), None
        )
        if fault is not None:
            return _mock_behavior(fault)
        corrupt_once = any(item.get("report_result") == "corrupt_numeric_once" for item in fixtures)
        return MockBehavior(corrupt_report_first_n_attempts=1) if corrupt_once else None


def _mock_behavior(
    fault: FaultInjectionSpec | None,
    *,
    empty_result: bool = False,
    zero_denominator: bool = False,
) -> MockBehavior:
    if fault is None:
        return MockBehavior(empty_result=empty_result, zero_denominator=zero_denominator)
    failure_type = str(fault.failure_type)
    if failure_type in {
        "knowledge_prompt_injection",
        "tool_output_prompt_injection",
        "tool_output_secret",
        "sensitive_field_output",
        "report_secret",
        "report_stack_trace",
        "unsafe_error",
    }:
        return MockBehavior(
            empty_result=empty_result,
            zero_denominator=zero_denominator,
            security_fault=failure_type,
        )
    kind = {
        "temporary_failure": MockFailureKind.TRANSIENT,
        "permanent_failure": MockFailureKind.PERMANENT,
        "permission_denied": MockFailureKind.PERMISSION,
        "timeout": MockFailureKind.TIMEOUT,
    }.get(failure_type, MockFailureKind.PERMANENT)
    attempts = fault.fail_on_attempts
    return MockBehavior(
        failure_kind=kind,
        fail_first_n_attempts=max(attempts, default=0),
        always_fail=not attempts,
        empty_result=empty_result,
        zero_denominator=zero_denominator,
    )


def _warnings(step_results: tuple[object, ...]) -> tuple[str, ...]:
    warnings: list[str] = []
    for result in step_results:
        output = getattr(result, "output", None)
        if output is None:
            continue
        raw = output.root.get("warnings")
        if isinstance(raw, list):
            warnings.extend(str(item) for item in raw)
    return tuple(dict.fromkeys(warnings))


def _safe_exception(error: Exception) -> str:
    text = " ".join(str(error).split())
    lowered = text.casefold()
    if any(token in lowered for token in ("api_key", "authorization:", "bearer ", "password=")):
        return f"{type(error).__name__}: [REDACTED]"
    return f"{type(error).__name__}: {text[:500]}"


def _artifact_text(container: WorkflowContainer, artifact: Artifact) -> str:
    try:
        path = container.artifacts.path_for(artifact)
        return path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except (OSError, UnicodeError, ValueError):
        return ""


def _workflow_events(
    container: WorkflowContainer, task_id: str | None
) -> tuple[dict[str, JsonValue], ...]:
    events: list[dict[str, JsonValue]] = []
    for record in container.workflow_audit.list():
        if task_id is not None and record.task_id != task_id:
            continue
        events.append(
            {
                "event": record.event,
                "status": record.status,
                "step_id": record.step_id,
                "tool_name": record.tool_name,
                "attempt": record.attempt,
                "duration_ms": record.duration_ms,
                "error_type": record.error_type,
                "metadata": record.metadata.root,
            }
        )
    return tuple(events)


def _tool_audit_events(
    container: WorkflowContainer, task_id: str | None
) -> tuple[dict[str, JsonValue], ...]:
    events: list[dict[str, JsonValue]] = []
    for record in container.tool_audit.list():
        if task_id is not None and record.task_id != task_id:
            continue
        events.append(
            {
                "tool_name": record.tool_name,
                "status": record.status.value,
                "principal_id": record.principal_id,
                "policy_decision": record.policy_decision,
                "reason_code": record.reason_code,
                "security_finding_codes": list(record.security_finding_codes),
            }
        )
    return tuple(events)


__all__ = ["EvaluationHarness", "RecordingEvaluationLLM"]
