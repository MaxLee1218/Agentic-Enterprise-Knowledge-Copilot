"""End-to-end v1.1 approve/edit/reject, API, audit, and restart recovery coverage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from copilot.agent.graph import WorkflowInterrupted
from copilot.api.app import create_app
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.contracts import ApprovalResolutionAction, ApprovalStatus, JsonObject, TaskStatus
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.security.identity import DemoIdentityProvider
from copilot.services.approval_service import (
    ApprovalAlreadyResolvedError,
    ApprovalArgumentsInvalidError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalPermissionDeniedError,
    ApprovalResolutionCommand,
)
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)
from tests.workflow_helpers import build_test_container, fixed_clock

TASK_TEXT = "Analyze supplier quality in Q2 2026 and generate a JSON report."
TENANT_ID = "TENANT-DEMO"


def caller(*, authorized: bool = True) -> TrustedCallerContext:
    """Return the trusted demo identity with an optional approval role."""
    return TrustedCallerContext(
        user_id="U-DEMO",
        tenant_id=TENANT_ID,
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
        roles=(("quality_data_approver",) if authorized else ()),
    )


def submit_for_approval(container: WorkflowContainer) -> tuple[str, str]:
    """Submit through natural-language intake and return the task and approval IDs."""
    service = container.task_service
    with pytest.raises(WorkflowInterrupted) as captured:
        service.submit(
            NaturalLanguageTaskCommand(
                task=TASK_TEXT,
                require_approval=True,
                source=RequestSource.API,
            ),
            caller(),
        )
    interrupted = captured.value
    assert interrupted.approval_id is not None
    return interrupted.task_id, interrupted.approval_id


def edited_command(
    container: WorkflowContainer,
    task_id: str,
    approval_id: str,
    limit: int,
) -> ApprovalResolutionCommand:
    """Build a complete replacement payload from the persisted proposal."""
    approval = container.approval_repository.get(approval_id, tenant_id=TENANT_ID)
    arguments = dict(approval.proposed_arguments.root)
    arguments["row_limit"] = limit
    return ApprovalResolutionCommand(
        task_id=task_id,
        approval_id=approval_id,
        action=ApprovalResolutionAction.EDIT,
        reason="Reduce the bounded result size for this review",
        edited_arguments=JsonObject(arguments),
    )


def test_edit_resumes_with_final_arguments_without_replaying_knowledge(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts", llm_provider=OfflineMockLLM()) as container:
        task_id, approval_id = submit_for_approval(container)
        pending = container.approval_repository.get(approval_id, tenant_id=TENANT_ID)

        assert pending.status is ApprovalStatus.PENDING
        assert pending.tool_name == "database_query"
        assert pending.editable_fields == ("row_limit",)
        assert pending.proposed_arguments.root["row_limit"] == 10000
        assert container.knowledge_tool.call_count == 1
        assert container.database_tool.call_count == 0

        with pytest.raises(ApprovalArgumentsInvalidError):
            container.approval_service.resolve(
                ApprovalResolutionCommand(
                    task_id=task_id,
                    approval_id=approval_id,
                    action=ApprovalResolutionAction.EDIT,
                    reason="Partial payload is forbidden",
                    edited_arguments=JsonObject({"row_limit": 5000}),
                ),
                caller(),
            )
        assert (
            container.approval_repository.get(approval_id, tenant_id=TENANT_ID).status
            is ApprovalStatus.PENDING
        )
        assert (
            container.repository.state_for(task_id, tenant_id=TENANT_ID).state
            is TaskStatus.WAITING_APPROVAL
        )
        assert container.database_tool.call_count == 0

        result = container.approval_service.resolve(
            edited_command(container, task_id, approval_id, 5000), caller()
        )

        assert result.task_status is TaskStatus.COMPLETED
        assert container.knowledge_tool.call_count == 1
        assert container.database_tool.call_count == 1
        state = container.engine.get_state(task_id, "TENANT-DEMO")
        database_calls = [
            call for call in state["tool_calls"] if call.tool_name == "database_query"
        ]
        assert len(database_calls) == 1
        assert database_calls[0].input.root["row_limit"] == 5000
        assert database_calls[0].approval_id == approval_id
        resolved = container.approval_repository.get(approval_id, tenant_id=TENANT_ID)
        assert resolved.status is ApprovalStatus.APPROVED
        assert resolved.resolution_action is ApprovalResolutionAction.EDIT
        audit_records = container.workflow_audit.list(tenant_id=TENANT_ID)
        events = {record.event for record in audit_records}
        assert {
            "APPROVAL_REQUESTED",
            "APPROVAL_EDITED",
            "APPROVAL_RESUME_STARTED",
            "APPROVAL_RESUME_SUCCEEDED",
        }.issubset(events)
        edited_audit = next(record for record in audit_records if record.event == "APPROVAL_EDITED")
        metadata = edited_audit.metadata.root
        assert metadata["tenant_id"] == "TENANT-DEMO"
        assert metadata["trace_id"]
        assert metadata["decision"] == "EDIT"
        assert metadata["reason"] == "Reduce the bounded result size for this review"
        assert len(str(metadata["original_arguments_hash"])) == 64
        assert len(str(metadata["resolved_arguments_hash"])) == 64
        assert "proposed_arguments" not in metadata


def test_approve_uses_proposed_arguments_and_duplicate_resolution_is_rejected(
    tmp_path: Path,
) -> None:
    with build_test_container(tmp_path / "artifacts", llm_provider=OfflineMockLLM()) as container:
        task_id, approval_id = submit_for_approval(container)
        command = ApprovalResolutionCommand(
            task_id=task_id,
            approval_id=approval_id,
            action=ApprovalResolutionAction.APPROVE,
            reason="Reviewed and approved",
        )
        result = container.approval_service.resolve(command, caller())

        assert result.task_status is TaskStatus.COMPLETED
        database_call = next(
            call
            for call in container.engine.get_state(task_id, "TENANT-DEMO")["tool_calls"]
            if call.tool_name == "database_query"
        )
        assert database_call.input.root["row_limit"] == 10000
        with pytest.raises(ApprovalAlreadyResolvedError):
            container.approval_service.resolve(command, caller())


def test_reject_cancels_without_calling_the_target_or_downstream_tools(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts", llm_provider=OfflineMockLLM()) as container:
        task_id, approval_id = submit_for_approval(container)
        result = container.approval_service.resolve(
            ApprovalResolutionCommand(
                task_id=task_id,
                approval_id=approval_id,
                action=ApprovalResolutionAction.REJECT,
                reason="Insufficient evidence",
            ),
            caller(),
        )

        assert result.task_status is TaskStatus.CANCELLED
        assert container.knowledge_tool.call_count == 1
        assert container.database_tool.call_count == 0
        assert container.analytics_tool.call_count == 0
        assert container.report_tool.call_count == 0
        assert (
            container.approval_repository.get(approval_id, tenant_id=TENANT_ID).status
            is ApprovalStatus.REJECTED
        )


def test_unauthorized_role_cannot_resolve_an_approval(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts", llm_provider=OfflineMockLLM()) as container:
        task_id, approval_id = submit_for_approval(container)
        with pytest.raises(ApprovalPermissionDeniedError):
            container.approval_service.resolve(
                ApprovalResolutionCommand(
                    task_id=task_id,
                    approval_id=approval_id,
                    action=ApprovalResolutionAction.APPROVE,
                ),
                caller(authorized=False),
            )
        assert (
            container.approval_repository.get(approval_id, tenant_id=TENANT_ID).status
            is ApprovalStatus.PENDING
        )

        cross_tenant = caller().model_copy(update={"tenant_id": "TENANT-OTHER"})
        with pytest.raises(ApprovalNotFoundError):
            container.approval_service.resolve(
                ApprovalResolutionCommand(
                    task_id=task_id,
                    approval_id=approval_id,
                    action=ApprovalResolutionAction.APPROVE,
                ),
                cross_tenant,
            )
        assert (
            container.approval_repository.get(approval_id, tenant_id=TENANT_ID).status
            is ApprovalStatus.PENDING
        )
        denied = {
            record.event
            for record in container.workflow_audit.list(tenant_id=TENANT_ID)
            if record.task_id == task_id
        }
        assert "APPROVAL_PERMISSION_DENIED" in denied


def test_restart_recovers_persisted_approval_and_checkpoint(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "restart" / "artifacts"
    ids = SequentialIdentifierFactory()
    with build_test_container(
        artifact_dir,
        ids=ids,
        llm_provider=OfflineMockLLM(),
    ) as first:
        task_id, approval_id = submit_for_approval(first)
        assert first.knowledge_tool.call_count == 1

    with build_test_container(
        artifact_dir,
        ids=ids,
        llm_provider=OfflineMockLLM(),
    ) as restarted:
        assert (
            restarted.approval_repository.get(approval_id, tenant_id=TENANT_ID).status
            is ApprovalStatus.PENDING
        )
        result = restarted.approval_service.resolve(
            edited_command(restarted, task_id, approval_id, 5000), caller()
        )
        assert result.task_status is TaskStatus.COMPLETED
        assert restarted.knowledge_tool.call_count == 0
        assert restarted.database_tool.call_count == 1


def test_expired_approval_cancels_without_executing_target(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 2, 8, 0, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    settings = Settings(
        database_url="sqlite:///unused-expired-approval.db",
        artifact_dir=tmp_path / "expired" / "artifacts",
        checkpoint_database_path=tmp_path / "expired" / "checkpoints.db",
        approval_ttl_seconds=60,
    )
    with build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=clock,
        sleeper=lambda _seconds: None,
        llm_provider=OfflineMockLLM(),
    ) as container:
        task_id, approval_id = submit_for_approval(container)
        current[0] += timedelta(seconds=61)

        with pytest.raises(ApprovalExpiredError):
            container.approval_service.resolve(
                ApprovalResolutionCommand(
                    task_id=task_id,
                    approval_id=approval_id,
                    action=ApprovalResolutionAction.APPROVE,
                ),
                caller(),
            )

        assert (
            container.approval_repository.get(approval_id, tenant_id=TENANT_ID).status
            is ApprovalStatus.EXPIRED
        )
        assert (
            container.repository.state_for(task_id, tenant_id=TENANT_ID).state
            is TaskStatus.CANCELLED
        )
        assert container.database_tool.call_count == 0


def test_approval_api_validates_actions_and_resumes_the_graph(tmp_path: Path) -> None:
    settings = Settings(
        database_url="sqlite:///unused-approval-api.db",
        artifact_dir=tmp_path / "api" / "artifacts",
        checkpoint_database_path=tmp_path / "api" / "checkpoints.db",
        demo_approval_roles=("quality_data_approver",),
    )
    container = build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        llm_provider=OfflineMockLLM(),
    )
    client = TestClient(
        create_app(
            task_service=container.task_service,
            approval_service=container.approval_service,
            settings=settings,
            identity_provider=DemoIdentityProvider(settings),
        )
    )
    try:
        with client:
            submitted = client.post(
                "/v1/tasks",
                json={"task": TASK_TEXT, "require_approval": True},
            )
            assert submitted.status_code == 202
            task_id = submitted.json()["task_id"]
            approval_id = submitted.json()["pending_approval_id"]
            approval_detail = client.get(f"/v1/tasks/{task_id}/approvals/{approval_id}")
            assert approval_detail.status_code == 200
            assert approval_detail.json()["editable_fields"] == ["row_limit"]
            proposed_arguments = approval_detail.json()["proposed_arguments"]

            missing = client.get(f"/v1/tasks/{task_id}/approvals/AP-MISSING")
            assert missing.status_code == 404
            assert missing.json()["error_code"] == "APPROVAL_NOT_FOUND"

            wrong_task = client.get(f"/v1/tasks/T-OTHER/approvals/{approval_id}")
            assert wrong_task.status_code == 404

            invalid_action = client.post(
                f"/v1/tasks/{task_id}/approvals/{approval_id}",
                json={"action": "override"},
            )
            assert invalid_action.status_code == 400
            assert invalid_action.json()["error_code"] == "INVALID_APPROVAL_ACTION"

            missing_arguments = client.post(
                f"/v1/tasks/{task_id}/approvals/{approval_id}",
                json={"action": "edit", "reason": "missing complete arguments"},
            )
            assert missing_arguments.status_code == 422

            invalid_arguments = dict(proposed_arguments)
            invalid_arguments["row_limit"] = 20000
            invalid = client.post(
                f"/v1/tasks/{task_id}/approvals/{approval_id}",
                json={
                    "action": "edit",
                    "edited_arguments": invalid_arguments,
                    "reason": "invalid increase",
                },
            )
            assert invalid.status_code == 422
            assert invalid.json()["error_code"] == "APPROVAL_ARGUMENTS_INVALID"

            edited = dict(proposed_arguments)
            edited["row_limit"] = 5000
            resolved = client.post(
                f"/v1/tasks/{task_id}/approvals/{approval_id}",
                json={
                    "action": "edit",
                    "edited_arguments": edited,
                    "reason": "Reduce bounded result size",
                },
            )
            assert resolved.status_code == 200
            assert resolved.json()["approval_status"] == "APPROVED"
            assert resolved.json()["resolution_action"] == "EDIT"
            assert resolved.json()["task_status"] == "COMPLETED"

            duplicate = client.post(
                f"/v1/tasks/{task_id}/approvals/{approval_id}",
                json={"action": "approve"},
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["error_code"] == "APPROVAL_ALREADY_RESOLVED"

            resolved_detail = client.get(f"/v1/tasks/{task_id}/approvals/{approval_id}")
            assert resolved_detail.status_code == 200
            assert resolved_detail.json()["resolved_arguments"]["row_limit"] == 5000

            concurrent_task = client.post(
                "/v1/tasks",
                json={"task": TASK_TEXT, "require_approval": True},
            )
            assert concurrent_task.status_code == 202
            concurrent_payload = concurrent_task.json()
            concurrent_url = (
                f"/v1/tasks/{concurrent_payload['task_id']}/approvals/"
                f"{concurrent_payload['pending_approval_id']}"
            )
            barrier = Barrier(2)

            def approve_concurrently() -> int:
                barrier.wait()
                return int(client.post(concurrent_url, json={"action": "approve"}).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = tuple(pool.map(lambda _index: approve_concurrently(), range(2)))

            assert sorted(statuses) == [200, 409]

            rejected_task = client.post(
                "/v1/tasks",
                json={"task": TASK_TEXT, "require_approval": True},
            )
            assert rejected_task.status_code == 202
            rejected_payload = rejected_task.json()
            rejected = client.post(
                (
                    f"/v1/tasks/{rejected_payload['task_id']}/approvals/"
                    f"{rejected_payload['pending_approval_id']}"
                ),
                json={"action": "reject", "reason": "Insufficient evidence"},
            )
            assert rejected.status_code == 200
            assert rejected.json()["approval_status"] == "REJECTED"
            assert rejected.json()["task_status"] == "CANCELLED"
    finally:
        container.close()
