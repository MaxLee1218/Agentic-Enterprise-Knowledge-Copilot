"""Deterministic dependency checks for serial fixed-plan execution."""

from collections.abc import Mapping
from dataclasses import dataclass

from copilot.contracts import StepResult, StepResultStatus, TaskStep


@dataclass(frozen=True, slots=True)
class DependencyCheckResult:
    """Explicit decision and failed dependency identifiers."""

    satisfied: bool
    failed_dependencies: tuple[str, ...]
    reason: str | None = None


class DependencyChecker:
    """Permit execution only after every declared predecessor succeeded with output."""

    def check(
        self,
        step: TaskStep,
        completed_results: Mapping[str, StepResult],
    ) -> DependencyCheckResult:
        """Return a deterministic dependency decision without side effects."""
        failed: list[str] = []
        for dependency_id in step.dependency:
            result = completed_results.get(dependency_id)
            if (
                result is None
                or result.status is not StepResultStatus.SUCCESS
                or result.output is None
            ):
                failed.append(dependency_id)
        if not failed:
            return DependencyCheckResult(satisfied=True, failed_dependencies=())
        dependencies = tuple(failed)
        return DependencyCheckResult(
            satisfied=False,
            failed_dependencies=dependencies,
            reason=(
                f"Step {step.step_id} was not executed because dependencies "
                f"{', '.join(dependencies)} did not finish successfully."
            ),
        )
