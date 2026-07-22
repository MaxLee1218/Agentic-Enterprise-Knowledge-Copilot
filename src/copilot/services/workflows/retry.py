"""Bounded retry eligibility for frozen plan steps."""

from dataclasses import dataclass

from copilot.contracts import TaskStep, ToolDefinition, ToolResult, ToolResultStatus


@dataclass(frozen=True, slots=True)
class WorkflowRetryPolicy:
    """Global retry cap layered under each frozen step policy."""

    max_retries: int
    retry_delay_seconds: float = 0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")

    def should_retry(
        self,
        step: TaskStep,
        definition: ToolDefinition,
        result: ToolResult,
        attempt_number: int,
    ) -> bool:
        """Retry only safe idempotent transient technical or timeout failures."""
        if not definition.idempotency.idempotent or result.error is None:
            return False
        if result.status not in {ToolResultStatus.TECHNICAL_FAILURE, ToolResultStatus.TIMEOUT}:
            return False
        if not result.error.recoverable:
            return False
        if result.error.error_code not in step.retry_policy.retryable_error_codes:
            return False
        allowed_attempts = min(step.retry_policy.max_attempts, self.max_retries + 1)
        return attempt_number < allowed_attempts

    def delay_for(self, step: TaskStep, completed_attempt: int) -> float:
        """Use the frozen deterministic backoff, never a smaller configured delay."""
        index = completed_attempt - 1
        planned = (
            float(step.retry_policy.backoff_seconds[index])
            if index < len(step.retry_policy.backoff_seconds)
            else 0.0
        )
        return max(planned, self.retry_delay_seconds)
