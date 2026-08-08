"""Repository and checkpoint tenant-isolation regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.agent.graph import WorkflowInterrupted
from copilot.contracts import TaskStatus
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)
from copilot.services.task_service import TaskNotFoundError
from copilot.services.workflows.models import TaskStateEvent
from tests.workflow_helpers import build_test_container, fixed_clock


def _caller(tenant_id: str) -> TrustedCallerContext:
    return TrustedCallerContext(
        user_id=f"U-{tenant_id}",
        tenant_id=tenant_id,
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
        roles=("quality_data_approver",),
        scopes=("task:execute", "task:read", "evidence:read", "artifact:read"),
        authentication_source="tenant-isolation-test",
        authenticated=True,
        is_demo_identity=False,
    )


def test_cross_tenant_task_evidence_artifact_audit_checkpoint_and_update_are_denied(
    tmp_path: Path,
) -> None:
    tenant_a = _caller("TENANT-A")
    tenant_b = _caller("TENANT-B")
    with build_test_container(tmp_path / "artifacts", llm_provider=OfflineMockLLM()) as container:
        execution = container.task_service.submit(
            NaturalLanguageTaskCommand(
                task="Analyze Q2 2026 supplier quality and generate a JSON report.",
                source=RequestSource.API,
            ),
            tenant_a,
        )
        task_id = execution.task_result.task_id
        artifact_id = execution.artifacts[0].artifact_id

        with pytest.raises(TaskNotFoundError):
            container.task_service.get_task(task_id, tenant_b)
        with pytest.raises(KeyError):
            container.repository.state_for(task_id, tenant_id=tenant_b.tenant_id)
        assert container.evidence.list_for_task(task_id, tenant_id=tenant_b.tenant_id) == ()
        assert container.artifacts.list_by_task(task_id, tenant_id=tenant_b.tenant_id) == ()
        with pytest.raises(KeyError):
            container.artifacts.get_by_id(artifact_id, tenant_id=tenant_b.tenant_id)
        assert container.workflow_audit.list(tenant_id=tenant_b.tenant_id) == ()
        assert container.tool_audit.list(tenant_id=tenant_b.tenant_id) == ()
        with pytest.raises(ValueError, match="checkpoint was not found"):
            container.engine.get_state(task_id, tenant_b.tenant_id)

        authoritative = container.repository.state_for(task_id, tenant_id=tenant_a.tenant_id)
        assert authoritative.state is TaskStatus.COMPLETED
        event_id = "EVT-CROSS-TENANT"
        changed = authoritative.model_copy(
            update={
                "version": authoritative.version + 1,
                "updated_at": fixed_clock(),
                "last_event_id": event_id,
            }
        )
        event = TaskStateEvent(
            event_id=event_id,
            task_id=task_id,
            from_state=authoritative.state.value,
            event="CROSS_TENANT_UPDATE",
            to_state=authoritative.state.value,
            timestamp=fixed_clock(),
            reason="cross-tenant mutation attempt",
        )
        with pytest.raises(ValueError, match="compare-and-swap"):
            container.repository.commit_transition(
                authoritative,
                changed,
                event,
                tenant_id=tenant_b.tenant_id,
            )


def test_cross_tenant_approval_is_indistinguishable_from_missing(tmp_path: Path) -> None:
    tenant_a = _caller("TENANT-A")
    tenant_b = _caller("TENANT-B")
    with build_test_container(
        tmp_path / "approval-artifacts", llm_provider=OfflineMockLLM()
    ) as container:
        with pytest.raises(WorkflowInterrupted) as interrupted:
            container.task_service.submit(
                NaturalLanguageTaskCommand(
                    task="Analyze Q2 2026 supplier quality and generate a JSON report.",
                    require_approval=True,
                    source=RequestSource.API,
                ),
                tenant_a,
            )
        approval_id = interrupted.value.approval_id
        assert approval_id is not None
        assert (
            container.approval_repository.get(approval_id, tenant_id=tenant_a.tenant_id).tenant_id
            == tenant_a.tenant_id
        )
        with pytest.raises(KeyError, match="not found"):
            container.approval_repository.get(approval_id, tenant_id=tenant_b.tenant_id)
