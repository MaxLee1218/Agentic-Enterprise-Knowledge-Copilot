"""Real PostgreSQL migration, persistence, checkpoint, and restart integration test."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import build_application
from copilot.config import PROJECT_ROOT, Settings, get_settings
from copilot.contracts import ApprovalResolutionAction, ApprovalStatus, TaskStatus
from copilot.persistence.checkpoint import migrate_postgres_checkpoints
from copilot.persistence.mcp_connection_repository import MCPConnectionRepository
from copilot.services.approval_service import ApprovalResolutionCommand
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)
from copilot.services.task_service import TaskNotFoundError
from tests.mcp_helpers import stdio_connection

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured"),
]


def test_postgres_step_result_identity_is_tenant_task_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", POSTGRES_URL)
    get_settings.cache_clear()
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    engine = create_engine(POSTGRES_URL)
    suffix = uuid4().hex
    first_task = f"T-STEP-A-{suffix}"
    second_task = f"T-STEP-B-{suffix}"
    try:
        unique_column_sets = {
            tuple(item["column_names"])
            for item in inspect(engine).get_unique_constraints("workflow_step_results")
        }
        assert ("tenant_id", "task_id", "step_id") in unique_column_sets
        assert ("step_id",) not in unique_column_sets

        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    "INSERT INTO workflow_tasks "
                    "(task_id, tenant_id, request_json, contract_json, plan_json, state_json) "
                    "VALUES (:first_task, 'TENANT-A', '{}', NULL, NULL, '{}'), "
                    "(:second_task, 'TENANT-A', '{}', NULL, NULL, '{}')"
                ),
                {"first_task": first_task, "second_task": second_task},
            )
            for task_id, payload in ((first_task, "A"), (second_task, "B")):
                connection.execute(
                    text(
                        "INSERT INTO workflow_step_results "
                        "(tenant_id, task_id, step_id, result_json, execution_json) "
                        "VALUES ('TENANT-A', :task_id, 'step-1-knowledge-search', :payload, '{}')"
                    ),
                    {"task_id": task_id, "payload": payload},
                )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM workflow_step_results "
                        "WHERE task_id IN (:first_task, :second_task) "
                        "AND step_id = 'step-1-knowledge-search'"
                    ),
                    {"first_task": first_task, "second_task": second_task},
                ).scalar_one()
                == 2
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO workflow_step_results "
                        "(tenant_id, task_id, step_id, result_json, execution_json) "
                        "VALUES ('TENANT-A', :task_id, 'step-1-knowledge-search', "
                        "'duplicate', '{}')"
                    ),
                    {"task_id": first_task},
                )
            transaction.rollback()
    finally:
        engine.dispose()
        get_settings.cache_clear()


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
        mcp_connections = MCPConnectionRepository(
            first.persistence_database, initialize_schema=False
        )
        mcp_connection = stdio_connection()
        mcp_connections.save(mcp_connection, tenant_id=caller.tenant_id)
        assert (
            mcp_connections.get(mcp_connection.connection_id, tenant_id=caller.tenant_id)
            == mcp_connection
        )
        with pytest.raises(KeyError):
            mcp_connections.get(mcp_connection.connection_id, tenant_id="TENANT-POSTGRES-OTHER")
        with pytest.raises(WorkflowInterrupted) as captured:
            first.task_service.submit(command_input, caller)
        task_id = captured.value.task_id
        approval_id = captured.value.approval_id
        assert approval_id is not None
        assert (
            first.approval_repository.get(approval_id, tenant_id=caller.tenant_id).status
            is ApprovalStatus.PENDING
        )
        assert first.evidence.list(task_id, tenant_id=caller.tenant_id)
        assert first.workflow_audit.list(tenant_id=caller.tenant_id)

    with build_application(settings) as restarted:
        assert (
            restarted.approval_repository.get(approval_id, tenant_id=caller.tenant_id).status
            is ApprovalStatus.PENDING
        )
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
        assert restarted.artifacts.list_by_task(task_id, tenant_id=caller.tenant_id)
        assert restarted.engine.get_state(task_id, caller.tenant_id)["task_id"] == task_id

    with build_application(settings) as recovered:
        task = recovered.task_service.get_task(task_id, caller)
        assert task.status == TaskStatus.COMPLETED.value
        task_page = recovered.task_service.list_tasks(
            caller,
            status=TaskStatus.COMPLETED,
            limit=20,
            offset=0,
        )
        assert task_page.total == 1
        assert tuple(item.task_id for item in task_page.items) == (task_id,)
        assert recovered.repository.task_result_for(task_id, tenant_id=caller.tenant_id) is not None
        assert (
            recovered.approval_repository.get(approval_id, tenant_id=caller.tenant_id).status
            is ApprovalStatus.APPROVED
        )
        assert recovered.engine.get_state(task_id, caller.tenant_id)["task_id"] == task_id

        intruder = TrustedCallerContext(
            user_id="U-POSTGRES-INTRUDER",
            tenant_id="TENANT-POSTGRES-OTHER",
            data_scope=caller.data_scope,
            roles=caller.roles,
            scopes=caller.scopes,
            authentication_source="postgres_integration_test",
            authenticated=True,
            is_demo_identity=False,
        )
        with pytest.raises(TaskNotFoundError):
            recovered.task_service.get_task(task_id, intruder)
        with pytest.raises(KeyError):
            recovered.repository.state_for(task_id, tenant_id=intruder.tenant_id)
        assert recovered.evidence.list(task_id, tenant_id=intruder.tenant_id) == ()
        assert recovered.artifacts.list_by_task(task_id, tenant_id=intruder.tenant_id) == ()
        with pytest.raises(KeyError):
            recovered.approval_repository.get(approval_id, tenant_id=intruder.tenant_id)
        assert recovered.tool_audit.list(tenant_id=intruder.tenant_id) == ()
        assert recovered.workflow_audit.list(tenant_id=intruder.tenant_id) == ()
        with pytest.raises(ValueError, match="checkpoint was not found"):
            recovered.engine.get_state(task_id, intruder.tenant_id)

    get_settings.cache_clear()
