"""Stage 8 Accounts Payable execution through the one shared governed Graph."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.contracts import (
    ApprovalResolutionAction,
    EvidenceType,
    JsonObject,
    MoneyThreshold,
    StepResultStatus,
    TaskStatus,
    TaskType,
    VerificationStatus,
)
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.approval_service import (
    ApprovalResolutionCommand,
)
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)
from copilot.tools.base import ToolExecutionContext, ToolExecutionOutput
from copilot.tools.database.ap_seed import seed_accounts_payable_demo_database
from copilot.tools.database.migrations import upgrade_business_schema
from copilot.tools.exceptions import ToolExecutionError
from tests.workflow_helpers import fixed_clock

pytestmark = pytest.mark.integration

TENANT_ID = "TENANT-DEMO"
TASK_TEXT = (
    "Analyze all Accounts Payable exceptions from 2026-04-01 to 2026-06-30 "
    "for LE-CN-01 and LE-US-01"
)
_MANIFEST_CHECKSUM = "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"


def _caller(*, roles: tuple[str, ...] = ("finance_analyst",)) -> TrustedCallerContext:
    return TrustedCallerContext(
        user_id="U-FINANCE-001",
        tenant_id=TENANT_ID,
        data_scope=("accounts_payable.v1", "accounts-payable-policy-v1"),
        legal_entity_ids=("LE-CN-01", "LE-US-01"),
        currency_scope=("CNY", "USD"),
        allowed_task_types=(TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,),
        roles=roles,
        scopes=(
            "task:execute",
            "approvals:read",
            "approvals:resolve",
            "finance:ap.detail",
            "finance:ap.artifact:download",
            "artifact.write",
        ),
        purpose=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
        policy_rule_set_id="accounts-payable-v1",
        policy_rule_set_version="ap_rules.2026.1",
        policy_manifest_checksum=_MANIFEST_CHECKSUM,
        policy_materiality=(
            MoneyThreshold(currency="CNY", amount=Decimal("5000")),
            MoneyThreshold(currency="USD", amount=Decimal("1000")),
        ),
        policy_snapshot_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def _container(tmp_path: Path, *, empty_database: bool = False) -> WorkflowContainer:
    database_url = f"sqlite:///{tmp_path / 'ap-stage8-business.db'}"
    if empty_database:
        upgrade_business_schema(database_url)
    else:
        seed_accounts_payable_demo_database(database_url)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        database_provider="mock",
        persistence_database_url=f"sqlite:///{tmp_path / 'ap-stage8-runtime.db'}",
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=False,
        llm_provider="mock",
        max_task_steps=14,
        max_database_rows=50_000,
        report_max_size_bytes=25 * 1024 * 1024,
        workflow_max_retries=2,
        workflow_retry_delay_seconds=0,
        log_level="ERROR",
        observability_enabled=False,
        metrics_enabled=False,
        trace_enabled=False,
    )
    return build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        llm_provider=OfflineMockLLM(),
    )


def _command(*, require_approval: bool = False) -> NaturalLanguageTaskCommand:
    return NaturalLanguageTaskCommand(
        task=TASK_TEXT,
        output_format=TaskOutputFormat.JSON,
        require_approval=require_approval,
        source=RequestSource.INTERNAL,
    )


def test_ap_full_workflow_retries_once_and_verifies_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _container(tmp_path) as container:
        original_execute = container.ap_database_tool.execute
        attempts = 0

        def fail_first_database_attempt(
            arguments: JsonObject,
            context: ToolExecutionContext,
        ) -> ToolExecutionOutput:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ToolExecutionError(
                    error_code="DATABASE_UNAVAILABLE",
                    message="Synthetic transient database outage",
                )
            return original_execute(arguments, context)

        monkeypatch.setattr(
            container.ap_database_tool,
            "execute",
            fail_first_database_attempt,
        )
        execution = container.task_service.submit(_command(), _caller())

        assert execution.task_result.final_status is TaskStatus.COMPLETED
        assert len(execution.step_results) == 14
        assert all(result.status is StepResultStatus.SUCCESS for result in execution.step_results)
        assert execution.verification_result is not None
        assert execution.verification_result.status is VerificationStatus.PASSED
        assert {item.source_type for item in execution.evidence} == {
            EvidenceType.DOCUMENT,
            EvidenceType.DATABASE,
            EvidenceType.CALCULATION,
        }
        assert len(execution.artifacts) == 1
        assert container.ap_knowledge_tool.call_count == 1
        assert container.ap_database_tool.call_count == 5
        assert container.ap_analytics_tool.call_count == 7
        assert container.ap_report_tool.call_count == 1
        database_steps = tuple(
            item for item in execution.step_executions if item.tool_name == "database_query"
        )
        assert len(database_steps) == 5
        assert sum(item.attempt_count for item in database_steps) == 6
        assert any(item.attempt_count == 2 for item in database_steps)


def test_ap_missing_date_waits_before_planning_or_tools(tmp_path: Path) -> None:
    with _container(tmp_path) as container:
        with pytest.raises(WorkflowInterrupted) as captured:
            container.task_service.submit(
                NaturalLanguageTaskCommand(
                    task="Analyze Accounts Payable exceptions for LE-CN-01",
                    output_format=TaskOutputFormat.JSON,
                    source=RequestSource.INTERNAL,
                ),
                _caller(),
            )

        assert captured.value.status == TaskStatus.WAITING_CLARIFICATION.value
        assert container.repository.plan_for(captured.value.task_id, tenant_id=TENANT_ID) is None
        assert container.ap_knowledge_tool.call_count == 0
        assert container.ap_database_tool.call_count == 0
        assert container.ap_analytics_tool.call_count == 0
        assert container.ap_report_tool.call_count == 0


def test_ap_approval_edit_resumes_without_replaying_completed_work(tmp_path: Path) -> None:
    with _container(tmp_path) as container:
        with pytest.raises(WorkflowInterrupted) as captured:
            container.task_service.submit(_command(require_approval=True), _caller())
        interrupted = captured.value
        assert interrupted.approval_id is not None
        pending = container.approval_repository.get(
            interrupted.approval_id,
            tenant_id=TENANT_ID,
        )
        assert pending.required_role == "finance_approver"
        assert pending.editable_fields == ("row_limit",)
        assert pending.proposed_arguments.root["row_limit"] == 50_000
        assert container.ap_knowledge_tool.call_count == 1
        assert container.ap_database_tool.call_count == 0

        edited = dict(pending.proposed_arguments.root)
        edited["row_limit"] = 10_000
        resolved = container.approval_service.resolve(
            ApprovalResolutionCommand(
                task_id=interrupted.task_id,
                approval_id=interrupted.approval_id,
                action=ApprovalResolutionAction.EDIT,
                reason="Reduce the bounded AP review set",
                edited_arguments=JsonObject(edited),
            ),
            _caller(roles=("finance_approver",)),
        )

        assert resolved.task_status is TaskStatus.COMPLETED
        assert resolved.execution is not None
        assert container.ap_knowledge_tool.call_count == 1
        assert container.ap_database_tool.call_count == 5
        state = container.engine.get_state(interrupted.task_id, TENANT_ID)
        database_calls = tuple(
            call for call in state["tool_calls"] if call.tool_name == "database_query"
        )
        assert len(database_calls) == 5
        assert database_calls[0].input.root["row_limit"] == 10_000
        assert database_calls[0].approval_id == interrupted.approval_id
        events = {record.event for record in container.workflow_audit.list(tenant_id=TENANT_ID)}
        assert {
            "APPROVAL_REQUESTED",
            "APPROVAL_EDITED",
            "APPROVAL_RESUME_STARTED",
            "APPROVAL_RESUME_SUCCEEDED",
        }.issubset(events)


def test_ap_empty_dataset_completes_with_zero_row_evidence(tmp_path: Path) -> None:
    with _container(tmp_path, empty_database=True) as container:
        execution = container.task_service.submit(_command(), _caller())

        assert execution.task_result.final_status is TaskStatus.COMPLETED
        assert execution.verification_result is not None
        assert execution.verification_result.status is not VerificationStatus.FAILED
        database_outputs = tuple(
            result.output
            for result in container.repository.tool_results_for(
                execution.task_result.task_id,
                tenant_id=TENANT_ID,
            )
            if result.tool_name == "database_query"
            and result.output is not None
            and "empty_result" in result.output.root
        )
        assert len(database_outputs) == 5
        assert all(output.root["empty_result"] is True for output in database_outputs)
