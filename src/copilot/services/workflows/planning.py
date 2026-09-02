"""Application port for candidate LLM understanding and planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from copilot.contracts import (
    ClarificationContext,
    ClarificationQuestion,
    ClarificationResponse,
    StepResult,
    TaskContract,
    TaskPlan,
    TaskRequest,
)
from copilot.services.task_intake import TrustedTaskContext
from copilot.services.workflows.plan_compiler import PlanCompilationDiagnostic
from copilot.services.workflows.validation import PlanValidationIssue, PlanValidationResult


@dataclass(frozen=True, slots=True)
class TaskUnderstandingOutcome:
    """Either a frozen contract or explicit missing-information requirements."""

    contract: TaskContract | None
    missing_information: tuple[str, ...] = ()
    questions: tuple[ClarificationQuestion, ...] = ()
    clarification_context: ClarificationContext = ClarificationContext()


@dataclass(frozen=True, slots=True)
class PlanGenerationOutcome:
    """Validated plan plus an independent bounded repair count."""

    plan: TaskPlan
    validation: PlanValidationResult
    repair_attempts: int = 0
    structured_output_retries: int = 0
    compilation_diagnostics: tuple[PlanCompilationDiagnostic, ...] = ()
    model_calls: tuple[PlannerModelCall, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerModelCall:
    """Safe per-call planning telemetry returned to opt-in stability harnesses."""

    node_name: str
    attempt: int
    provider_attempts: int
    prompt_chars: int
    provider: str | None
    model: str | None
    latency_ms: int | None
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    raw_output_chars: int
    raw_output_hash: str | None
    parse_status: str
    schema_status: str
    error_code: str | None = None
    repair_type: str | None = None


class PlanningService(Protocol):
    """Injected application boundary implemented by deterministic or LLM planning."""

    def understand(
        self,
        *,
        request: TaskRequest,
        trusted_context: TrustedTaskContext,
        trace_id: str,
        max_steps: int,
        clarification_context: ClarificationContext | None = None,
        clarification_response: ClarificationResponse | None = None,
    ) -> TaskUnderstandingOutcome:
        """Interpret untrusted text without changing trusted authorization scope."""
        ...

    def create_validated_plan(
        self,
        *,
        request: TaskRequest,
        contract: TaskContract,
        trace_id: str,
        max_steps: int,
    ) -> PlanGenerationOutcome:
        """Generate, deterministically validate, and if eligible repair a candidate plan."""
        ...

    def create_plan(
        self,
        *,
        contract: TaskContract,
        trace_id: str,
        max_steps: int,
    ) -> PlanGenerationOutcome:
        """Generate one candidate without performing plan repair."""
        ...

    def repair_plan(
        self,
        *,
        contract: TaskContract,
        invalid_plan: TaskPlan,
        errors: tuple[PlanValidationIssue, ...],
        trace_id: str,
        max_steps: int,
        attempt: int,
    ) -> PlanGenerationOutcome:
        """Perform one repair attempt using deterministic validator feedback."""
        ...

    def replan(
        self,
        *,
        contract: TaskContract,
        current_plan: TaskPlan,
        step_results: tuple[StepResult, ...],
        evidence_ids: tuple[str, ...],
        reason: str,
        trace_id: str,
        remaining_steps: int,
    ) -> PlanGenerationOutcome:
        """Generate one constrained higher-version plan after a recoverable runtime gap."""
        ...


__all__ = [
    "PlanGenerationOutcome",
    "PlannerModelCall",
    "PlanningService",
    "TaskUnderstandingOutcome",
]
