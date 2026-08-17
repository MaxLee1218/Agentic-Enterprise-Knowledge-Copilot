"""Deterministic per-tool overall deadline calculation."""

from __future__ import annotations

from datetime import datetime, timedelta

from copilot.contracts import ToolResult


def tool_attempt_deadline(
    *,
    task_deadline: datetime,
    attempt_started_at: datetime,
    overall_seconds: int,
    prior_results: tuple[ToolResult, ...] = (),
) -> datetime:
    """Bound every retry by one overall window starting with the first attempt."""
    first_started_at = min(
        (result.started_at for result in prior_results),
        default=attempt_started_at,
    )
    overall_deadline = first_started_at + timedelta(seconds=overall_seconds)
    return min(task_deadline, overall_deadline)


__all__ = ["tool_attempt_deadline"]
