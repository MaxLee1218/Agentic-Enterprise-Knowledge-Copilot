"""Tests for immutable task request validation."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from copilot.contracts import TaskRequest


def test_task_request_creates_with_utc_default_and_empty_metadata() -> None:
    """A valid request should receive UTC time and isolated metadata defaults."""
    first = TaskRequest(id="R-001", user_id="U-001", raw_input="Analyze Q1 2026 quality")
    second = TaskRequest(id="R-002", user_id="U-001", raw_input="Analyze Q2 2026 quality")

    offset = first.created_at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0
    assert first.metadata.root == {}
    assert first.metadata is not second.metadata


@pytest.mark.parametrize("field", ["id", "user_id", "raw_input"])
def test_task_request_rejects_blank_required_text(field: str) -> None:
    """Request identity and original input must not be blank."""
    values = {"id": "R-001", "user_id": "U-001", "raw_input": "Analyze quality"}
    values[field] = "   "

    with pytest.raises(ValidationError):
        TaskRequest.model_validate(values)


def test_task_request_rejects_naive_datetime_and_unknown_fields() -> None:
    """Audit timestamps require UTC and undeclared fields cannot pollute a request."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        TaskRequest(
            id="R-001",
            user_id="U-001",
            raw_input="Analyze quality",
            created_at=datetime(2026, 7, 19, 8, 0),
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskRequest.model_validate(
            {
                "id": "R-001",
                "user_id": "U-001",
                "raw_input": "Analyze quality",
                "unexpected": True,
            }
        )
