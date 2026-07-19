"""Tests for governed tool definitions and normalized invocation results."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from copilot.contracts import (
    ErrorType,
    JsonObject,
    RiskLevel,
    TaskError,
    ToolApprovalPolicy,
    ToolDefinition,
    ToolIdempotency,
    ToolResult,
    ToolResultStatus,
    ToolTimeout,
)
from tests.unit.domain.helpers import COMPLETED_AT, STARTED_AT, TASK_ID


def test_tool_definition_contains_governance_contracts() -> None:
    """A tool definition must expose schema, risk, approval, timeout, and idempotency."""
    definition = ToolDefinition(
        tool_name="database_query",
        tool_version="1.0.0",
        description="Execute approved read-only quality query templates",
        input_schema=JsonObject({"type": "object", "additionalProperties": False}),
        output_schema=JsonObject({"type": "object", "additionalProperties": False}),
        risk_level=RiskLevel.MEDIUM,
        timeout=ToolTimeout(attempt_seconds=10, overall_seconds=25),
        approval_policy=ToolApprovalPolicy(
            policy_id="quality-data-v1",
            trigger_conditions=("restricted_scope",),
            approver_role="quality_data_approver",
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=("template", "parameters", "snapshot"),
            reuse_window_seconds=300,
            side_effects="None; read-only query",
        ),
    )

    assert definition.risk_level is RiskLevel.MEDIUM
    assert definition.idempotency.idempotent is True


def test_tool_result_calculates_latency_and_round_trips() -> None:
    """Latency should be derived from UTC timestamps and persist safely in JSON."""
    result = ToolResult(
        tool_call_id="TC-001",
        task_id=TASK_ID,
        step_id="S-DB-01",
        tool_name="database_query",
        tool_version="1.0.0",
        status=ToolResultStatus.SUCCESS,
        output=JsonObject({"rows": [], "row_count": 0, "empty_result": True}),
        error=None,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        attempt=1,
        evidence_ids=("E-DB-EMPTY-01",),
    )

    assert result.latency_ms == 1250
    assert ToolResult.model_validate_json(result.model_dump_json()) == result


def test_tool_result_requires_error_for_failure() -> None:
    """A failed tool attempt cannot omit its typed error."""
    with pytest.raises(ValidationError, match="must contain an error"):
        ToolResult(
            tool_call_id="TC-001",
            task_id=TASK_ID,
            step_id="S-DB-01",
            tool_name="database_query",
            tool_version="1.0.0",
            status=ToolResultStatus.TECHNICAL_FAILURE,
            output=None,
            error=None,
            started_at=STARTED_AT,
            completed_at=COMPLETED_AT,
            attempt=1,
        )


def test_tool_result_rejects_inconsistent_latency_and_timing() -> None:
    """Persisted latency and timestamp ordering must be internally consistent."""
    error = TaskError(
        error_code="DATABASE_TIMEOUT",
        error_type=ErrorType.TIMEOUT,
        message="The read-only query timed out",
        recoverable=True,
    )
    with pytest.raises(ValidationError, match="latency_ms must match"):
        ToolResult(
            tool_call_id="TC-002",
            task_id=TASK_ID,
            step_id="S-DB-01",
            tool_name="database_query",
            tool_version="1.0.0",
            status=ToolResultStatus.TIMEOUT,
            output=None,
            error=error,
            started_at=STARTED_AT,
            completed_at=STARTED_AT + timedelta(seconds=1),
            attempt=1,
            latency_ms=999,
        )
