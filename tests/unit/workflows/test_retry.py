"""Retry-policy eligibility tests independent of the runner loop."""

from datetime import UTC, datetime

from copilot.contracts import (
    ErrorType,
    TaskError,
    ToolIdempotency,
    ToolResult,
    ToolResultStatus,
)
from copilot.services.workflows.retry import WorkflowRetryPolicy
from copilot.tools.mock_supplier_quality import MockDatabaseTool
from tests.unit.domain.helpers import make_plan


def _failure(status: ToolResultStatus, recoverable: bool, code: str) -> ToolResult:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    return ToolResult(
        tool_call_id="TC-001",
        task_id="T-001",
        step_id="S-DB-01",
        tool_name="database_query",
        tool_version="1.0.0-mock",
        status=status,
        output=None,
        error=TaskError(
            error_code=code,
            error_type=ErrorType.TECHNICAL,
            message="Controlled failure",
            recoverable=recoverable,
        ),
        started_at=now,
        completed_at=now,
        attempt=1,
    )


def test_retry_requires_idempotent_recoverable_allowlisted_failure() -> None:
    step = (
        make_plan()
        .steps[0]
        .model_copy(
            update={
                "retry_policy": make_plan()
                .steps[0]
                .retry_policy.model_copy(
                    update={"retryable_error_codes": ("DATABASE_UNAVAILABLE",)}
                )
            }
        )
    )
    definition = MockDatabaseTool.definition
    policy = WorkflowRetryPolicy(max_retries=2)
    failure = _failure(ToolResultStatus.TECHNICAL_FAILURE, True, "DATABASE_UNAVAILABLE")
    assert policy.should_retry(step, definition, failure, 1) is True
    non_idempotent = definition.model_copy(
        update={
            "idempotency": ToolIdempotency(
                idempotent=False,
                key_components=("input",),
                reuse_window_seconds=0,
                side_effects="Controlled test side effect",
            )
        }
    )
    assert policy.should_retry(step, non_idempotent, failure, 1) is False
    assert policy.should_retry(step, definition, failure, 3) is False
