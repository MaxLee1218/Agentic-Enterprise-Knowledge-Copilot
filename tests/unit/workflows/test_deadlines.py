"""Overall tool deadline regression coverage."""

from datetime import UTC, datetime, timedelta

from copilot.contracts import ErrorType, TaskError, ToolResult, ToolResultStatus
from copilot.services.workflows.deadlines import tool_attempt_deadline

STARTED = datetime(2026, 8, 17, 5, 16, tzinfo=UTC)


def _timeout_result(started_at: datetime) -> ToolResult:
    return ToolResult(
        tool_call_id="TC-001",
        task_id="T-001",
        step_id="S-001",
        tool_name="knowledge_search",
        tool_version="1.0.0-http",
        status=ToolResultStatus.TIMEOUT,
        output=None,
        error=TaskError(
            error_code="KNOWLEDGE_TIMEOUT",
            error_type=ErrorType.TIMEOUT,
            message="Enterprise knowledge retrieval timed out",
            recoverable=True,
            task_id="T-001",
            step_id="S-001",
            tool_call_id="TC-001",
            timestamp=started_at + timedelta(seconds=9),
        ),
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=9),
        attempt=1,
    )


def test_retry_keeps_the_first_attempt_overall_deadline() -> None:
    first = _timeout_result(STARTED)

    deadline = tool_attempt_deadline(
        task_deadline=STARTED + timedelta(minutes=5),
        attempt_started_at=STARTED + timedelta(seconds=10),
        overall_seconds=25,
        prior_results=(first,),
    )

    assert deadline == STARTED + timedelta(seconds=25)


def test_task_deadline_can_be_stricter_than_tool_overall_deadline() -> None:
    deadline = tool_attempt_deadline(
        task_deadline=STARTED + timedelta(seconds=20),
        attempt_started_at=STARTED,
        overall_seconds=25,
    )

    assert deadline == STARTED + timedelta(seconds=20)
