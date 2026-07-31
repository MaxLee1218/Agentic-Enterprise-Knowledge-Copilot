"""Application port for candidate LLM understanding and planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from copilot.contracts import StepResult, TaskContract, TaskPlan, TaskRequest
from copilot.services.workflows.validation import PlanValidationIssue, PlanValidationResult


@dataclass(frozen=True, slots=True)
class TaskUnderstandingOutcome:
    """Either a frozen contract or explicit missing-information requirements."""

    contract: TaskContract | None
    missing_information: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanGenerationOutcome:
    """Validated plan plus an independent bounded repair count."""

    plan: TaskPlan
    validation: PlanValidationResult
    repair_attempts: int = 0


class PlanningService(Protocol):
    """Injected application boundary implemented by deterministic or LLM planning."""

    def understand(
        self,
        *,
        request: TaskRequest,
        trusted_contract: TaskContract,
        trace_id: str,
        max_steps: int,
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
    "PlanningService",
    "TaskUnderstandingOutcome",
]
