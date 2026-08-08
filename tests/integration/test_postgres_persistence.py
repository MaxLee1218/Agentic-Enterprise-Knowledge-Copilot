"""Real PostgreSQL migration, persistence, checkpoint, and restart integration test."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import build_application
from copilot.config import PROJECT_ROOT, Settings, get_settings
from copilot.contracts import ApprovalResolutionAction, ApprovalStatus, TaskStatus
from copilot.persistence.checkpoint import migrate_postgres_checkpoints
from copilot.services.approval_service import ApprovalResolutionCommand
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured"),
]


def test_postgres_migration_round_trip_and_restart_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", POSTGRES_URL)
    get_settings.cache_clear()
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    migrate_postgres_checkpoints(POSTGRES_URL.replace("+psycopg", ""))

    settings = Settings(
        app_env="test",
        database_url="sqlite:///:memory:",
        persistence_database_url=POSTGRES_URL,
        persistence_auto_create_schema=False,
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=True,
    )
    caller = TrustedCallerContext(
        user_id="U-POSTGRES",
        tenant_id="TENANT-POSTGRES",
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
        roles=("quality_data_approver",),
        authentication_source="postgres_integration_test",
        is_demo_identity=True,
    )
    command_input = NaturalLanguageTaskCommand(
        task="Analyze Q2 2026 supplier quality and generate a JSON report.",
        require_approval=True,
        source=RequestSource.INTERNAL,
    )

    with build_application(settings) as first:
        with pytest.raises(WorkflowInterrupted) as captured:
            first.task_service.submit(command_input, caller)
        task_id = captured.value.task_id
        approval_id = captured.value.approval_id
        assert approval_id is not None
        assert first.approval_repository.get(approval_id).status is ApprovalStatus.PENDING
        assert first.evidence.list(task_id)
        assert first.workflow_audit.list()

    with build_application(settings) as restarted:
        assert restarted.approval_repository.get(approval_id).status is ApprovalStatus.PENDING
        result = restarted.approval_service.resolve(
            ApprovalResolutionCommand(
                task_id=task_id,
                approval_id=approval_id,
                action=ApprovalResolutionAction.APPROVE,
                reason="PostgreSQL restart recovery verification",
            ),
            caller,
        )
        assert result.task_status is TaskStatus.COMPLETED
        assert restarted.artifacts.list_by_task(task_id)
        assert restarted.engine.get_state(task_id, caller.tenant_id)["task_id"] == task_id

    with build_application(settings) as recovered:
        task = recovered.task_service.get_task(task_id, caller)
        assert task.status == TaskStatus.COMPLETED.value
        assert recovered.repository.task_result_for(task_id) is not None
        assert recovered.approval_repository.get(approval_id).status is ApprovalStatus.APPROVED
        assert recovered.engine.get_state(task_id, caller.tenant_id)["task_id"] == task_id

    get_settings.cache_clear()
