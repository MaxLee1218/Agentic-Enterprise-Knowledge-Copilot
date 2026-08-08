"""Offline smoke coverage for checkpointed v1.1 approval edit and resume."""

from pathlib import Path

import pytest

from copilot.agent.graph import WorkflowInterrupted
from copilot.contracts import ApprovalResolutionAction, JsonObject, TaskStatus
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.services.approval_service import ApprovalResolutionCommand
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)
from tests.workflow_helpers import build_test_container


def test_offline_human_approval_edit_smoke(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts", llm_provider=OfflineMockLLM()) as container:
        caller = TrustedCallerContext(
            user_id="U-DEMO",
            tenant_id="TENANT-DEMO",
            data_scope=("quality.v1", "supplier-quality-policy-v1"),
            roles=("quality_data_approver",),
        )
        with pytest.raises(WorkflowInterrupted) as captured:
            container.task_service.submit(
                NaturalLanguageTaskCommand(
                    task="Analyze supplier quality in Q2 2026 and generate a JSON report.",
                    require_approval=True,
                    source=RequestSource.INTERNAL,
                ),
                caller,
            )
        task_id = captured.value.task_id
        approval_id = captured.value.approval_id
        assert approval_id is not None
        pending = container.approval_repository.get(approval_id, tenant_id=caller.tenant_id)
        edited = dict(pending.proposed_arguments.root)
        edited["row_limit"] = 5000

        result = container.approval_service.resolve(
            ApprovalResolutionCommand(
                task_id=task_id,
                approval_id=approval_id,
                action=ApprovalResolutionAction.EDIT,
                reason="Use a smaller bounded result set",
                edited_arguments=JsonObject(edited),
            ),
            caller,
        )

        assert result.task_status is TaskStatus.COMPLETED
        assert container.knowledge_tool.call_count == 1
        assert container.database_tool.call_count == 1
