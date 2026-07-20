"""Executor lifecycle, failure normalization, evidence, and audit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import sleep

import pytest

from copilot.contracts import JsonObject, ToolCall, ToolDefinition, ToolResultStatus
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import InMemoryToolAuditRepository
from copilot.tools import ToolExecutionContext, ToolExecutionOutput, ToolExecutor, ToolRegistry
from copilot.tools.exceptions import ToolAuthorizationError, ToolNotFoundError, ToolValidationError
from tests.mocks.mock_tools import (
    MockAnalyticsTool,
    MockDatabaseTool,
    MockKnowledgeTool,
    MockReportTool,
)


class ApprovedAuthorizer:
    """Controlled policy decision for tests."""

    def __init__(self) -> None:
        self.call_count = 0

    def authorize(self, call: ToolCall, definition: ToolDefinition) -> None:
        self.call_count += 1
        assert call.tool_name == definition.tool_name


class DeniedAuthorizer:
    def authorize(self, call: ToolCall, definition: ToolDefinition) -> None:
        del call, definition
        raise ToolAuthorizationError("Approval does not cover this invocation")


class FailingKnowledgeTool(MockKnowledgeTool):
    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        del arguments, context
        raise RuntimeError("sensitive implementation detail")


class SleepingKnowledgeTool(MockKnowledgeTool):
    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        sleep(0.2)
        return super().execute(arguments, context)


def make_call(
    *,
    tool_name: str = "knowledge_search",
    tool_version: str = "1.0.0-test",
    input_payload: JsonMapping | None = None,
    deadline_after: float = 5,
    call_id: str = "TC-KB-001",
) -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        task_id="T-001",
        step_id="S-KB-001",
        tool_name=tool_name,
        tool_version=tool_version,
        input=JsonObject(
            input_payload if input_payload is not None else {"query": "quality policy"}
        ),
        idempotency_key=f"IDEMPOTENCY-{call_id}",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=deadline_after),
        tenant_id="TENANT-A",
        user_id="U-001",
    )


def make_runtime(
    tool: MockKnowledgeTool,
    *,
    authorizer: ApprovedAuthorizer | DeniedAuthorizer | None = None,
) -> tuple[ToolExecutor, InMemoryEvidenceLedger, InMemoryToolAuditRepository]:
    registry = ToolRegistry()
    registry.register(tool)
    ledger = InMemoryEvidenceLedger()
    audit = InMemoryToolAuditRepository()
    executor = ToolExecutor(
        registry=registry,
        authorizer=authorizer or ApprovedAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=audit,
    )
    return executor, ledger, audit


def test_successful_execution_collects_evidence_and_writes_audit() -> None:
    tool = MockKnowledgeTool()
    executor, ledger, audit = make_runtime(tool)

    try:
        result = executor.execute(make_call())
    finally:
        executor.close()

    assert result.status is ToolResultStatus.SUCCESS
    assert result.output is not None
    assert len(result.evidence_ids) == 1
    assert ledger.get(result.evidence_ids[0]).tool_call_id == result.tool_call_id
    assert tool.call_count == 1
    records = audit.list()
    assert len(records) == 1
    assert records[0].status is ToolResultStatus.SUCCESS
    assert records[0].latency_ms >= 0


def test_unknown_tool_is_rejected_before_execution() -> None:
    executor, _, _ = make_runtime(MockKnowledgeTool())
    try:
        with pytest.raises(ToolNotFoundError):
            executor.execute(make_call(tool_name="database_query"))
    finally:
        executor.close()


def test_invalid_input_is_audited_and_never_calls_tool_or_policy() -> None:
    tool = MockKnowledgeTool()
    authorizer = ApprovedAuthorizer()
    executor, _, audit = make_runtime(tool, authorizer=authorizer)

    try:
        with pytest.raises(ToolValidationError):
            executor.execute(make_call(input_payload={}))
    finally:
        executor.close()

    assert tool.call_count == 0
    assert authorizer.call_count == 0
    assert audit.list()[0].error_code == "TOOL_INPUT_INVALID"


def test_tool_exception_becomes_safe_typed_technical_result() -> None:
    executor, _, audit = make_runtime(FailingKnowledgeTool())

    try:
        result = executor.execute(make_call())
    finally:
        executor.close()

    assert result.status is ToolResultStatus.TECHNICAL_FAILURE
    assert result.error is not None
    assert result.error.error_code == "TOOL_EXECUTION_FAILED"
    assert "sensitive" not in result.error.message
    assert audit.list()[0].error_code == "TOOL_EXECUTION_FAILED"


def test_timeout_becomes_typed_result_and_late_output_is_not_recorded() -> None:
    executor, ledger, audit = make_runtime(SleepingKnowledgeTool())

    try:
        result = executor.execute(make_call(deadline_after=0.02))
        sleep(0.25)
    finally:
        executor.close()

    assert result.status is ToolResultStatus.TIMEOUT
    assert result.error is not None
    assert result.error.error_code == "TOOL_TIMEOUT"
    assert result.evidence_ids == ()
    assert ledger.list_for_call(result.tool_call_id) == ()
    assert audit.list()[0].status is ToolResultStatus.TIMEOUT


def test_policy_denial_prevents_execution_and_is_audited() -> None:
    tool = MockKnowledgeTool()
    executor, _, audit = make_runtime(tool, authorizer=DeniedAuthorizer())

    try:
        result = executor.execute(make_call())
    finally:
        executor.close()

    assert result.status is ToolResultStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.error_code == "TOOL_ACCESS_DENIED"
    assert tool.call_count == 0
    assert audit.list()[0].status is ToolResultStatus.PERMISSION_DENIED


def test_all_four_frozen_mock_capabilities_run_without_executor_changes() -> None:
    tools = (
        MockKnowledgeTool(),
        MockDatabaseTool(),
        MockAnalyticsTool(),
        MockReportTool(),
    )
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    ledger = InMemoryEvidenceLedger()
    audit = InMemoryToolAuditRepository()
    executor = ToolExecutor(
        registry=registry,
        authorizer=ApprovedAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=audit,
    )

    try:
        knowledge = executor.execute(make_call(call_id="TC-KB-CHAIN"))
        database = executor.execute(
            make_call(
                tool_name="database_query",
                input_payload={"query_template_id": "supplier_quality_summary_v1"},
                call_id="TC-DB-CHAIN",
            )
        )
        analytics = executor.execute(
            make_call(
                tool_name="analysis_engine",
                input_payload={"dataset_evidence_id": database.evidence_ids[0]},
                call_id="TC-AN-CHAIN",
            )
        )
        report = executor.execute(
            make_call(
                tool_name="report_generator",
                input_payload={
                    "evidence_refs": [
                        knowledge.evidence_ids[0],
                        database.evidence_ids[0],
                        analytics.evidence_ids[0],
                    ]
                },
                call_id="TC-RP-CHAIN",
            )
        )
    finally:
        executor.close()

    assert [result.status for result in (knowledge, database, analytics, report)] == [
        ToolResultStatus.SUCCESS,
        ToolResultStatus.SUCCESS,
        ToolResultStatus.SUCCESS,
        ToolResultStatus.SUCCESS,
    ]
    assert report.evidence_ids == ()
    assert len(audit.list()) == 4
